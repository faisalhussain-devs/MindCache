from typing import Optional
import numpy as np
from retrieval.structs import RetrievalContext, RetrievalConfig
from Database.embedder import EmbeddingManager

class ContextBridge:
    def __init__(self, embedder: EmbeddingManager, config: RetrievalConfig):
        self.embedder = embedder
        self.config = config

    def process(self, current_prompt: str, last_msg: Optional[str] = None, prev_msg: Optional[str] = None) -> RetrievalContext:
        """
        Phase 1: Determine the "Active Path" query vector by bridging context.
        """
        ctx = RetrievalContext(
            current_prompt=current_prompt,
            last_user_msg=last_msg,
            prev_user_msg=prev_msg
        )
        
        # 1. Base Case: Always start with Current
        ctx.query_text = f"CURRENT MESSAGE: {current_prompt}"
        ctx.history_used = ["current"]
        
        if len(current_prompt) > self.config.max_msg_length or not last_msg:
             ctx.query_vector = self._embed(ctx.query_text)
             return ctx

        # 2. Check Drift for Last Message (n vs n-1)
        vecs = self._embed([current_prompt, last_msg])
        curr_vec, last_vec = vecs[0], vecs[1]
        
        sim_score_n_minus_1 = self._cosine_similarity(curr_vec, last_vec)
        ctx.drift_score = sim_score_n_minus_1 # Store primary drift
        
        if sim_score_n_minus_1 < self.config.drift_threshold:
            # Drifted. Stick to current.
            ctx.query_vector = curr_vec
            return ctx
        
        # 3. Check Length of Last Message
        if len(last_msg) > self.config.max_msg_length:
            # Last message is too big to append 'prev' significantly, or we just stop here.
            combined_text = f"CURRENT MESSAGE: {current_prompt}, LAST MESSAGE: {last_msg}"
            ctx.query_text = combined_text
            ctx.history_used = ["last", "current"]
            ctx.query_vector = self._embed(combined_text)
            return ctx
            
        # 4. If Last is Small -> Check Prev Message (n-1 vs n-2)
        if prev_msg:
            prev_vec = self._embed(prev_msg)
            sim_score_n_minus_2 = self._cosine_similarity(last_vec, prev_vec)
            if sim_score_n_minus_2 >= self.config.drift_threshold:
                 combined_text = f"CURRENT MESSAGE: {current_prompt}, LAST MESSAGE: {last_msg}, SECOND LAST MESSAGE: {prev_msg}"
                 ctx.history_used = ["prev", "last", "current"]
                 ctx.query_text = combined_text
                 ctx.query_vector = self._embed(combined_text)
                 return ctx
        
        # Fallback: Last passed, but Prev didn't (or didn't exist).
        combined_text = f"CURRENT MESSAGE: {current_prompt}, LAST MESSAGE: {last_msg}"
        ctx.history_used = ["last", "current"]
        ctx.query_text = combined_text
        ctx.query_vector = self._embed(combined_text)
        return ctx

    def _embed(self, text: list[str] | str):
        if isinstance(text, str):
            text = [text]
        if hasattr(self.embedder, 'get_batch_embeddings'):
            blobs = self.embedder.get_batch_embeddings(text)
            if blobs is not None and len(blobs) > 0:
                if len(blobs) > 1:
                    return blobs
                return blobs[0]
        return None

    def _cosine_similarity(self, vec_a, vec_b):
        if vec_a is None or vec_b is None:
            return 0.0
        # Vectors from Embedder are normalized
        return np.dot(vec_a, vec_b)
