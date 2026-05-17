"""Analyze why specific BEAM queries failed to retrieve their correct evidence."""
import sys, os, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import sessionmaker
from Database.db_setup import engine, Topic, KnowledgeMemory, EpisodicMemory, UserMemory
from eval.eval_manual_check import get_evidence_ids, normalize_session_ids
from retrieval.active_path import ActivePathRetrieval

Session = sessionmaker(bind=engine)
s = Session()
r = ActivePathRetrieval()

d = json.load(open('beam/eval_data_beam.json', 'r', encoding='utf-8'))

# Pick 3 interesting logical questions
target_q_types = ['temporal_reasoning', 'contradiction_resolution', 'multi_session_reasoning']

print("=== DEEP DIVE: WHY RETRIEVAL FAILS ON BEAM ===\n")

for tc in d['test_cases']:
    q_type = tc.get('question_type')
    if q_type not in target_q_types:
        continue
        
    q_id = tc['question_id']
    query = tc['question']
    ev_ids = get_evidence_ids(tc)
    if not ev_ids:
        continue
        
    target_q_types.remove(q_type) # Only process one of each
    
    print(f"{'='*80}")
    print(f"QUESTION [{q_type}]: {query}")
    print(f"EVIDENCE TURN IDs: {list(ev_ids)}")
    
    # 1. Are the evidence memories actually in the DB?
    found_evidence_topics = set()
    found_memories = []
    
    for MemClass in [KnowledgeMemory, EpisodicMemory, UserMemory]:
        mems = s.query(MemClass).all()
        for m in mems:
            if m.message and m.message.source_session_id:
                try:
                    src_ids = normalize_session_ids(m.message.source_session_id)
                    overlap = src_ids & ev_ids
                    if overlap:
                        found_evidence_topics.add(m.topic_id)
                        found_memories.append((list(overlap), m.topic_id, m.content))
                except Exception:
                    pass
                    
    print(f"\n1. DB EVIDENCE CHECK:")
    if not found_memories:
        print("  ❌ EVIDENCE NOT FOUND IN DB!")
    else:
        print(f"  ✅ Found {len(found_memories)} memory chunks containing evidence.")
        print(f"  Target Topics containing this evidence: {found_evidence_topics}")
        for overlap, tid, content in found_memories[:3]:
            print(f"    - Turn(s) {overlap} -> Topic {tid}: {content[:120]}...")
            
    # 2. Why didn't retrieval find them?
    print(f"\n2. RETRIEVAL TRACE:")
    result = r.retrieve(query)
    retrieved_tids = result.trace.get("selected_topic_ids", [])
    print(f"  Retrieved Topics: {retrieved_tids}")
    
    overlap = set(retrieved_tids) & found_evidence_topics
    if overlap:
        print(f"  ✅ SUCCESS: Retrieval found evidence topics {overlap}")
    else:
        print(f"  ❌ FAILURE: Retrieval completely missed the target topics.")
        
        # Let's see what the query text is and what the evidence text is
        # to understand why BM25/Vector missed it.
        if found_memories:
            target_tid = list(found_evidence_topics)[0]
            target_topic = s.get(Topic, target_tid)
            print(f"\n  WHY DID IT MISS TOPIC {target_tid}? Semantic Mismatch:")
            print(f"    Query text:     '{query}'")
            print(f"    Topic name:     '{target_topic.name}'")
            print(f"    Topic summary:  '{target_topic.summary[:150] if target_topic.summary else None}'")
            print(f"    Memory content: '{found_memories[0][2][:150]}'")
            
            # Show what was retrieved instead
            if retrieved_tids:
                wrong_topic = s.get(Topic, retrieved_tids[0])
                print(f"\n  WHAT DID IT RETRIEVE INSTEAD? (Topic {retrieved_tids[0]}):")
                print(f"    Topic name:     '{wrong_topic.name}'")
                print(f"    Topic summary:  '{wrong_topic.summary[:150] if wrong_topic.summary else None}'")
    
    print("\n")
    if not target_q_types:
        break

s.close()
