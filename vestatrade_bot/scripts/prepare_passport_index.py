"""Validate or build the existing passport index for the configured embeddings.

This is deliberately a small operational wrapper around ``load_or_build``.
It neither creates a second retrieval store nor changes document bindings. A
model or document-digest mismatch makes the existing loader rebuild the one
``passport_index.json`` cache; a failed embedding call leaves the previous
vector cache intact.
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings
from app.openrouter_client import OpenRouterClient
from app.passport_retrieval import load_or_build


def main() -> int:
    settings = get_settings()
    if not settings.embeddings_enabled:
        print("Passport index: FAILED (embeddings are not configured)")
        return 2

    cache_path = settings.products_cache_path.with_name("passport_index.json")
    client = OpenRouterClient(settings=settings)
    with client.request_budget():
        index = load_or_build(
            cache_path,
            [settings.product_docs_dir, PROJECT_ROOT / "data"],
            client.embed,
            settings.embedding_model,
        )

    if not index.chunks:
        print("Passport index: FAILED (no readable passport chunks)")
        return 2
    if not index.has_vectors or index.model != settings.embedding_model:
        print(
            "Passport index: FAILED "
            f"(expected vectors for {settings.embedding_model}, got {index.model or 'none'})"
        )
        return 2

    print("Passport index: OK")
    print(f"Embedding model: {index.model}")
    print(f"Chunks: {len(index.chunks)}")
    print(f"Source digest: {index.source_digest}")
    print(f"Cache: {cache_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
