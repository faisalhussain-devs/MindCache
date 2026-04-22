import numpy as np
from Database.db_setup import Topic, engine
from api_server import get_tree_cache
from retrieval.context_bridge import ContextBridge
from retrieval.structs import CandidateTopic, RetrievalConfig, RetrievalContext
from retrieval.hybrid_search import BM25Scorer, calculate_rrf, CrossEncoderManager
from retrieval.root_cache import root_leaf_cache
from sqlalchemy.orm import sessionmaker


class RootDescent:
    """Phase 3: Leaf Search with cached metadata, matrix-multiply vector sim,
    pre-tokenized BM25, RRF fusion, and Cross-Encoder reranking."""

    def __init__(self, config: RetrievalConfig, context_bridge: ContextBridge):
        self.config = config
        self.bridge = context_bridge
        self.Session = sessionmaker(bind=engine)
        self.reranker = CrossEncoderManager()

    def descend(self, ctx: RetrievalContext) -> list[CandidateTopic]:
        if not ctx.sub_queries:
            return []

        session = self.Session()
        try:
            # 1. Map roots → sub-queries
            root_map = {}            # root_id → Topic
            root_to_queries = {}     # root_id → list of sq_texts
            unique_sq_texts = set()

            for sq in ctx.sub_queries:
                sq_text = sq.get("text", ctx.query_text)
                roots = sq.get("roots", [])

                if not roots:
                    continue
                unique_sq_texts.add(sq_text)
                for root_node in roots:
                    if root_node.id not in root_map:
                        node = session.get(Topic, root_node.id)
                        if node:
                            root_map[root_node.id] = node
                            root_to_queries[root_node.id] = []

                    if root_node.id in root_map and sq_text not in root_to_queries[root_node.id]:
                        root_to_queries[root_node.id].append(sq_text)

            if not root_map:
                return []

            # 2. Batch-embed all sub-queries
            sq_texts_list = list(unique_sq_texts)
            sq_vectors = {}
            if hasattr(self.bridge, '_embed') and sq_texts_list:
                result = self.bridge._embed(sq_texts_list)
                if result is not None:
                    if isinstance(result, list) and len(result) == len(sq_texts_list):
                        for t, e in zip(sq_texts_list, result):
                            sq_vectors[t] = e
                    elif len(sq_texts_list) == 1:
                        sq_vectors[sq_texts_list[0]] = result

            tree = get_tree_cache()

            # 3. Merge cache entries for selected roots
            all_metas = []
            embedding_blocks = []

            for root_id in root_map:
                entry = root_leaf_cache.get(root_id)
                if entry is None:
                    entry = root_leaf_cache.build(root_id, tree)

                all_metas.extend(entry.leaf_metas)
                if entry.embedding_matrix is not None:
                    embedding_blocks.append(entry.embedding_matrix)

            if not all_metas:
                return []

            # Stack all embeddings into one matrix: (total_leaves, dim)
            merged_matrix = np.vstack(embedding_blocks) if embedding_blocks else None

            # 4. Vector similarity via matrix multiply
            sq_vecs_ordered = [sq_vectors[t] for t in sq_texts_list if t in sq_vectors]
            if merged_matrix is not None and sq_vecs_ordered:
                sq_matrix = np.array(sq_vecs_ordered)           # (Q, dim)
                sim_all = merged_matrix @ sq_matrix.T            # (N, Q)
                best_sq_idx = np.argmax(sim_all, axis=1)         # (N,)
                best_scores = sim_all[np.arange(len(sim_all)), best_sq_idx]  # (N,)
                sq_keys = [t for t in sq_texts_list if t in sq_vectors]
            else:
                best_sq_idx = np.zeros(len(all_metas), dtype=int)
                best_scores = np.zeros(len(all_metas))
                sq_keys = sq_texts_list

            # 5. Build scored leaf list
            leaves = []
            for i, meta in enumerate(all_metas):
                score = float(best_scores[i])
                if score <= 0:
                    continue
                leaves.append({
                    **meta,
                    "best_sub_query": sq_keys[int(best_sq_idx[i])] if sq_keys else "",
                    "vector_score": score,
                })

            if not leaves:
                return []

            # 6. Vector rank
            leaves.sort(key=lambda x: x["vector_score"], reverse=True)
            for rank, leaf in enumerate(leaves, start=1):
                leaf["vector_rank"] = rank
            leaves = leaves[:self.config.top_k_vector]

            # 7. BM25 from pre-tokenized corpus
            bm25 = BM25Scorer()
            docs = [leaf["searchable_text"] for leaf in leaves]
            bm25.fit(docs)

            for idx, leaf in enumerate(leaves):
                leaf["bm25_score"] = bm25.score(leaf["best_sub_query"], idx)

            leaves.sort(key=lambda x: x["bm25_score"], reverse=True)
            for rank, leaf in enumerate(leaves, start=1):
                leaf["bm25_rank"] = rank

            # 8. RRF fusion
            for leaf in leaves:
                leaf["rrf_score"] = calculate_rrf(leaf["vector_rank"], leaf["bm25_rank"])

            leaves.sort(key=lambda x: x["rrf_score"], reverse=True)
            top = leaves[:self.config.top_k_rrf]

            # 9. Cross-Encoder reranking
            final_top_k = self.reranker.rerank(top, top_k=self.config.top_k_cross)

            # 10. Convert to CandidateTopic structs
            results = []
            for c in final_top_k:
                results.append(
                    CandidateTopic(
                        name=c["name"],
                        path=c["path"],
                        topic_id=c["topic_id"],
                        vector_rank=c["vector_rank"],
                        rrf_score=c["rrf_score"],
                        bm25_rank=c["bm25_rank"],
                        cross_encoder_score=round(
                            c.get("cross_encoder_score", c.get("rrf_score", 0.0)), 3
                        ),
                        timestamp_start=c.get("timestamp_start", "?"),
                        timestamp_end=c.get("timestamp_end", "?"),
                        is_leaf=c["is_leaf"],
                    )
                )
            return results

        finally:
            session.close()
