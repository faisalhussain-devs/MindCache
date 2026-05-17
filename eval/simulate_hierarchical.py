"""Dry-run simulation of hierarchical_pass to verify the algorithm."""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import defaultdict
from sqlalchemy.orm import sessionmaker
from Database.db_setup import engine, Topic
from Database.reorganize_tree import (
    _bfs_levels, _subtree_text_to_depth, _find_cut_depth,
    _get_all_descendants, estimate_tokens, min_groups, TOKEN_LIMIT
)

s = sessionmaker(bind=engine)()
topics = s.query(Topic).all()
children_map = defaultdict(list)
for t in topics:
    children_map[t.parent_id].append(t)

roots = s.query(Topic).filter(Topic.parent_id.is_(None)).all()

print(f"TOKEN_LIMIT = {TOKEN_LIMIT}")
print(f"Total nodes: {len(topics)}, Roots: {len(roots)}\n")
print("=" * 80)
print("SIMULATING hierarchical_pass(depth_offset=0)")
print("=" * 80)

def simulate_pass(roots_to_process, children_map, depth_offset=0, indent=""):
    direct = []
    oversized = []
    
    for root in roots_to_process:
        full_text = _subtree_text_to_depth(root, children_map, 999)
        tokens = estimate_tokens(full_text)
        all_desc = _get_all_descendants(root, children_map=children_map)
        if tokens <= TOKEN_LIMIT:
            direct.append((tokens, root.name, root.id, len(all_desc)))
        else:
            oversized.append((root, tokens, len(all_desc)))
    
    # Direct-fit
    if direct:
        # Simulate bin-packing
        pack_input = [(t, [f"text"], [None]*n) for t, name, rid, n in direct]
        batches = min_groups(pack_input)
        print(f"\n{indent}[depth={depth_offset}] DIRECT-FIT: {len(direct)} subtrees -> {len(batches)} LLM batches")
        for t, name, rid, n in direct:
            print(f"{indent}  Root '{name}' (id={rid}): ~{t} tokens, {n} nodes -> fits in budget")
    
    # Oversized
    if oversized:
        print(f"\n{indent}[depth={depth_offset}] OVERSIZED: {len(oversized)} roots need level-slicing")
        
        for root, total_tokens, total_nodes in oversized:
            cut_depth, levels = _find_cut_depth(root, children_map)
            upper_text = _subtree_text_to_depth(root, children_map, cut_depth)
            upper_tokens = estimate_tokens(upper_text)
            upper_nodes = sum(len(lvl) for lvl in levels[:cut_depth+1])
            
            print(f"\n{indent}  Root '{root.name}' (id={root.id})")
            print(f"{indent}    Total: ~{total_tokens} tokens, {total_nodes} nodes, {len(levels)} BFS levels")
            print(f"{indent}    Cut depth: {cut_depth} (upper slice: ~{upper_tokens} tokens, {upper_nodes} nodes)")
            
            # Show level counts
            for i, lvl in enumerate(levels):
                marker = " <-- CUT" if i == cut_depth else ""
                cumul = estimate_tokens(_subtree_text_to_depth(root, children_map, i))
                print(f"{indent}      Level {i}: {len(lvl)} nodes (cumulative: ~{cumul} tokens){marker}")
            
            print(f"\n{indent}    PHASE A: Send upper skeleton (levels 0..{cut_depth}) to LLM for dedup")
            
            # Phase B: show what mini-roots would be processed
            if len(levels) > cut_depth + 1:
                mini_roots = levels[cut_depth + 1]
                print(f"{indent}    PHASE B: {len(mini_roots)} mini-roots at level {cut_depth+1}")
                
                # Simulate recursive call
                simulate_pass(mini_roots, children_map, depth_offset + 1, indent + "    ")
            else:
                print(f"{indent}    PHASE B: No subtrees below cut depth (tree fully covered)")

simulate_pass(roots, children_map, depth_offset=0)
s.close()
