from mindcache.retrieval.structs import RetrievalContext
from mindcache.Memory_extract.input_denoiser import InputDenoiser
from mindcache.Database.embedder import EmbeddingManager

class ContextBridge:
    def __init__(self, embedder: EmbeddingManager):
        self.denoiser = InputDenoiser()
        self.embedder = embedder

    def process(self, current_prompt: str) -> RetrievalContext:
        """
        Phase 1: Build query vector with denoising
        """
        # Denoise all inputs
        current_clean = self.denoiser.compress(current_prompt)

        ctx = RetrievalContext(
            current_prompt=current_prompt,
        )
        ctx.query_text = f"{current_clean}"
        ctx.query_vector = self._embed(ctx.query_text)
        return ctx

    def _embed(self, text):
        if isinstance(text, str):
            text = [text]
        if hasattr(self.embedder, 'encode'):
            blobs = self.embedder.encode(text, is_query=True)
            if blobs is not None and len(blobs) > 0:
                if len(blobs) > 1:
                    return [b for b in blobs]
                return blobs[0]
        return None

