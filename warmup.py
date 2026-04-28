"""warmup.py — pre-download the embedding model used by send_nl.py.

If a bundled `hf_cache/` directory ships next to this script (44 MB,
included in the workshop zip), the model is loaded straight from there
— no internet required. Otherwise, the model is downloaded once from
HuggingFace and stored in `~/.cache/huggingface/hub/`.

Install dependencies first (pick one):
    pip install -r requirements.txt          # classic
    pip install .                            # via pyproject.toml
    uv pip install -r pyproject.toml         # if you have uv

Then run:
    python warmup.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import time

# Prefer the bundled cache when present so Codespaces or locked-down
# networks can still run send_nl.py with zero outbound HTTP. Must run
# BEFORE `import sentence_transformers` because HF env vars are read
# at SentenceTransformer construction time.
_LOCAL_HF = pathlib.Path(__file__).parent.resolve() / "hf_cache"
if _LOCAL_HF.is_dir() and not os.environ.get("HF_HOME"):
    os.environ["HF_HOME"] = str(_LOCAL_HF)

MODEL = "redis/langcache-embed-v3-small"
DIM = 300


def main() -> None:
    t0 = time.perf_counter()
    cache_loc = os.environ.get("HF_HOME", "~/.cache/huggingface")
    print(f"warmup: loading {MODEL} (truncated to {DIM} dims)")
    print(f"  cache: {cache_loc}")
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        print(f"warmup: sentence-transformers not installed — {e}", file=sys.stderr)
        print("  fix:  pip install -r requirements.txt", file=sys.stderr)
        sys.exit(1)

    model = SentenceTransformer(MODEL, truncate_dim=DIM)
    # Force a sample encode so we know the model actually works.
    out = model.encode(["hello workshop"], normalize_embeddings=True)
    elapsed = time.perf_counter() - t0
    print(f"warmup: ok in {elapsed:.1f}s")
    print(f"  embedding shape: {out.shape}  (expected (1, {DIM}))")
    print()
    print("You're set. Try:")
    print("  python participant.py pico-unit-1")
    print("  python send_nl.py")


if __name__ == "__main__":
    main()
