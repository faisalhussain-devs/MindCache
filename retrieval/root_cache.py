import json
import pickle
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field

# Cache file lives next to the database in the project root
CACHE_FILE = Path(__file__).resolve().parent.parent / "cache" / "root_leaf_cache.pkl"


@dataclass
class RootCacheEntry:
    """All cached data for a single root's subtree."""
    leaf_metas: list[dict] = field(default_factory=list)
    embedding_matrix: np.ndarray | None = None   # (N, dim) or None if no embeddings


class RootLeafCache:
    """
    Module-level singleton. One instance per process.
    Persists to disk so the cache survives server restarts.
    Cleared + rebuilt by the background scheduler after each pipeline run.
    """

    def __init__(self):
        self._cache: dict[int, RootCacheEntry] = {}

    # ── Public API ──────────────────────────────────────────────────────────

    def get(self, root_id: int) -> RootCacheEntry | None:
        return self._cache.get(root_id)

    def build(self, root_id: int, tree) -> RootCacheEntry:
        """
        Traverse the subtree under root_id and build a complete cache entry.
        Stores it in the in-memory cache dict and returns it.
        Does NOT save to disk — call build_all() or save() explicitly.
        """
        root_node = tree.topic_by_id.get(root_id)
        if root_node is None:
            empty = RootCacheEntry()
            self._cache[root_id] = empty
            return empty

        entry = RootCacheEntry()
        embedding_rows: list = []

        def _build_root_path(node):
            path = []
            current = node
            while current is not None:
                path.append(f"[id:{current.id}] {current.name}")
                current = tree.parent_map.get(current.id)
            return list(reversed(path))

        def recurse(node, current_path: list[str]):
            children = tree.children_map.get(node.id)
            if not children:
                path_str = " > ".join(current_path)
                # mem_end = real latest memory time in this node's subtree (set by pipeline)
                if node.mem_end:
                    ts_end = node.mem_end.strftime("%Y-%m-%d %H:%M")
                if node.mem_start:
                    ts_start = node.mem_start.strftime("%Y-%m-%d %H:%M")

                rich_text = node.description or node.summary or ""
                searchable_text = f"{path_str} {rich_text}".strip()

                entry.leaf_metas.append({
                    "name": node.name,
                    "path": path_str,
                    "topic_id": node.id,
                    "searchable_text": searchable_text,
                    "timestamp_start": ts_start,
                    "timestamp_end": ts_end,
                    "is_leaf": True,
                })
                embedding_rows.append(tree.embedding_cache.get(node.id))
                return

            for child in children:
                recurse(child, current_path + [f"[id:{child.id}] {child.name}"])

        root_path = _build_root_path(root_node)
        recurse(root_node, root_path)

        if embedding_rows:
            dim = None
            for vec in embedding_rows:
                if vec is not None:
                    dim = len(vec)
                    break
            if dim is not None:
                rows = [vec if vec is not None else np.zeros(dim, dtype=np.float32)
                        for vec in embedding_rows]
                entry.embedding_matrix = np.vstack(rows)  # (N, dim)

        self._cache[root_id] = entry
        return entry

    def build_all(self, tree):
        """Pre-warm cache for every root in the tree, then persist to disk."""
        self._cache.clear()
        roots = tree.children_map.get(None, [])
        total_leaves = 0
        for root in roots:
            entry = self.build(root.id, tree)
            total_leaves += len(entry.leaf_metas)
        print(f"[RootLeafCache] Built {len(roots)} roots, {total_leaves} leaves.")
        self.save()

    def save(self):
        """Persist the in-memory cache to disk as a pickle file."""
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, "wb") as f:
                pickle.dump(self._cache, f, protocol=pickle.HIGHEST_PROTOCOL)
            size_kb = CACHE_FILE.stat().st_size / 1024
            print(f"[RootLeafCache] Saved to disk: {CACHE_FILE} ({size_kb:.1f} KB)")
        except Exception as e:
            print(f"[RootLeafCache] Save failed: {e}")

    def load(self) -> bool:
        """
        Load cache from disk into memory.
        Returns True if loaded successfully, False if file missing or corrupt.
        """
        if not CACHE_FILE.exists():
            print("[RootLeafCache] No cache file found. Will build on first query.")
            return False
        try:
            with open(CACHE_FILE, "rb") as f:
                data = pickle.load(f)
            if not isinstance(data, dict):
                raise ValueError("Cache file is corrupt (not a dict).")
            self._cache = data
            n_roots = len(self._cache)
            n_leaves = sum(len(e.leaf_metas) for e in self._cache.values())
            size_kb = CACHE_FILE.stat().st_size / 1024
            print(f"[RootLeafCache] Loaded from disk: {n_roots} roots, {n_leaves} leaves ({size_kb:.1f} KB)")
            return True
        except Exception as e:
            print(f"[RootLeafCache] Load failed ({e}). Cache will be rebuilt on first query.")
            self._cache = {}
            return False

    def clear(self):
        """Evict everything from memory AND delete the disk file."""
        self._cache.clear()
        if CACHE_FILE.exists():
            try:
                CACHE_FILE.unlink()
                print(f"[RootLeafCache] Cache file deleted: {CACHE_FILE}")
            except Exception as e:
                print(f"[RootLeafCache] Could not delete cache file: {e}")


# ── Module-level singleton ──
root_leaf_cache = RootLeafCache()
