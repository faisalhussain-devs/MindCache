from typing import Optional
import numpy as np
from retrieval.structs import RetrievalContext, RetrievalConfig
from Database.embedder import EmbeddingManager
from Memory_extract.input_denoiser import InputDenoiser

class ContextBridge:
    def __init__(self, embedder: EmbeddingManager, config: RetrievalConfig):
        self.embedder = embedder
        self.config = config
        self.denoiser = InputDenoiser()

    def process(self, current_prompt: str, last_msg: Optional[str] = None, prev_msg: Optional[str] = None) -> RetrievalContext:
        """
        Phase 1: Build query vector with denoising and dual-threshold context merging.
        
        1. Denoise all inputs
        2. Always use current prompt
        3. Include n-1 only if current is SHORT (< threshold_1) AND similar
        4. Include n-2 only if (current + n-1) is SHORT (< threshold_2) AND similar to n-2
        """
        # Denoise all inputs
        current_clean = self.denoiser.compress(current_prompt)
        last_clean = self.denoiser.compress(last_msg) if last_msg else None
        prev_clean = self.denoiser.compress(prev_msg) if prev_msg else None

        ctx = RetrievalContext(
            current_prompt=current_prompt,
            last_user_msg=last_msg,
            prev_user_msg=prev_msg
        )
        
        # Start with current only
        ctx.query_text = f"[CURRENT] {current_clean}"
        ctx.history_used = ["current"]
        
        # Gate 1: Is current prompt short enough to benefit from context?
        if len(current_clean) > self.config.short_threshold_1 or not last_clean:
            ctx.query_vector = self._embed(ctx.query_text)
            return ctx

        # Gate 2: Is n-1 similar enough? (drift check)
        vecs = self._embed([current_clean, last_clean])
        if vecs is None or not isinstance(vecs, list) or len(vecs) < 2:
            ctx.query_vector = self._embed(ctx.query_text)
            return ctx
            
        curr_vec, last_vec = vecs[0], vecs[1]
        sim_n1 = self._cosine_similarity(curr_vec, last_vec)
        ctx.drift_score = sim_n1
        
        if sim_n1 < self.config.drift_threshold:
            # Drifted — stick with current only
            ctx.query_vector = curr_vec
            return ctx
        
        # n-1 passes — include it
        combined = f"[CURRENT] {current_clean} [PREV] {last_clean}"
        ctx.query_text = combined
        ctx.history_used = ["current", "last"]
        
        # Gate 3: Is combined short enough for n-2?
        if not prev_clean or len(combined) > self.config.short_threshold_2:
            ctx.query_vector = self._embed(combined)
            return ctx
        
        # Gate 4: Is n-2 similar to the combined (current + n-1)?
        combined_vec = self._embed(combined)
        prev_vec = self._embed(prev_clean)
        
        if combined_vec is not None and prev_vec is not None:
            sim_n2 = self._cosine_similarity(combined_vec, prev_vec)
            if sim_n2 >= self.config.drift_threshold:
                full_text = f"[CURRENT] {current_clean} [PREV] {last_clean} [PREV2] {prev_clean}"
                ctx.query_text = full_text
                ctx.history_used = ["current", "last", "prev"]
                ctx.query_vector = self._embed(full_text)
                return ctx
        
        # Fallback: current + n-1 only
        ctx.query_vector = combined_vec
        return ctx

    def _embed(self, text):
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
        return float(np.dot(vec_a, vec_b))
