import logging
logger = logging.getLogger(__name__)
def calculate_rrf(vector_rank: int, bm25_rank: int, k: int = 60) -> float:
    """Reciprocal Rank Fusion (RRF)"""
    v_score = 1.0 / (k + vector_rank) if vector_rank > 0 else 0.0
    b_score = 1.0 / (k + bm25_rank) if bm25_rank > 0 else 0.0
    return 0.8*v_score + 0.2*b_score

RERANKER_NAME = "jinaai/jina-reranker-v2-base-multilingual"

class CrossEncoderManager:
    def __init__(self):
        logger.info(f"Loading FastEmbed ONNX Reranker: {RERANKER_NAME}...")
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            try:
                self.model = TextCrossEncoder(model_name=RERANKER_NAME, providers=["DmlExecutionProvider"])
                logger.info(" Reranker Loaded (ONNX with DirectML GPU)")
            except Exception as dml_err:
                logger.info(f" DirectML initialization failed ({dml_err}). Falling back to CPU...")
                self.model = TextCrossEncoder(model_name=RERANKER_NAME)
                logger.info(" Reranker Loaded (ONNX Native CPU)")
            self.enabled = True
        except ImportError:
            logger.info("[CrossEncoder] fastembed not installed. Reranking disabled.")
            self.enabled = False
        except Exception as e:
            logger.info(f"[CrossEncoder] FastEmbed load failed: {e}")
            self.enabled = False

    def rerank(self, candidates: list[dict], top_k: int) -> list[dict]:
        """
        Reranks a list of candidate dictionaries. 
        Requires each candidate to have 'searchable_text' and 'best_sub_query'.
        Returns the top_k sorted candidates.
        """
        if not self.enabled or not candidates:
            logger.info("[Hybrid Search] Reranker not available, falling back to base order...")
            return candidates[:top_k]

        query_groups: dict[str, list[int]] = {}
        for idx, c in enumerate(candidates):
            q = c.get("best_sub_query", "")
            if q not in query_groups:
                query_groups[q] = []
            query_groups[q].append(idx)

        for query, indices in query_groups.items():
            docs = [candidates[i].get("searchable_text", "") for i in indices]
            # FastEmbed rerank() returns floats in the SAME ORDER as input docs
            scores = list(self.model.rerank(query, documents=docs, batch_size=32))
            for rank_idx, global_idx in enumerate(indices):
                candidates[global_idx]["cross_encoder_score"] = float(scores[rank_idx])

        # Fallback: any candidates that didn't get scored (shouldn't happen)
        for c in candidates:
            if "cross_encoder_score" not in c:
                c["cross_encoder_score"] = float(c.get("rrf_score", 0.0))

        candidates.sort(key=lambda x: x["cross_encoder_score"], reverse=True)
        return candidates[:top_k]