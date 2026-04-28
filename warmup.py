"""warmup.py — fetch + cache the embedding model used by send_nl.py.

On first run this downloads `redis/langcache-embed-v3-small` (~44 MB)
from HuggingFace into `./hf_cache/` next to this script. On subsequent
runs the model loads from that cache offline — useful on Codespaces,
locked-down Wi-Fi, or planes.

The cache directory is gitignored (some hosts reject files >25 MB), so
each environment downloads it once.

Install dependencies first (pick one):
    pip install -r requirements.txt          # classic
    pip install .                            # via pyproject.toml
    uv pip install -r pyproject.toml         # if you have uv

Then:
    python warmup.py
"""
from __future__ import annotations

import os
import pathlib
import sys
import time

# Pin the cache to a directory next to this script so subsequent runs
# (and other scripts) reuse it without further downloads. Must run
# BEFORE importing sentence_transformers / huggingface_hub since those
# read HF_HOME at import time.
_HERE = pathlib.Path(__file__).parent.resolve()
_LOCAL_HF = _HERE / "hf_cache"
_LOCAL_HF.mkdir(exist_ok=True)
os.environ.setdefault("HF_HOME", str(_LOCAL_HF))

MODEL = "redis/langcache-embed-v3-small"
DIM = 300


def _model_already_cached() -> bool:
    """Return True iff the model snapshot is already on disk so we
    can tell the user 'using cache' vs 'downloading' upfront."""
    snap = _LOCAL_HF / "hub" / f"models--{MODEL.replace('/', '--')}" / "snapshots"
    if not snap.is_dir():
        return False
    for child in snap.iterdir():
        if any(child.iterdir()):
            return True
    return False


def main() -> None:
    cached = _model_already_cached()
    print(f"warmup: model     = {MODEL}")
    print(f"warmup: cache dir = {_LOCAL_HF}")
    if cached:
        print("warmup: model already cached — verifying it loads")
    else:
        print("warmup: model NOT cached — downloading (~44 MB, one time)...")

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        print(f"\nwarmup: sentence-transformers not installed — {e}", file=sys.stderr)
        print("  fix:  pip install -r requirements.txt", file=sys.stderr)
        sys.exit(1)

    t0 = time.perf_counter()
    try:
        model = SentenceTransformer(MODEL, truncate_dim=DIM)
    except Exception as e:
        print(f"\nwarmup: model load failed — {e}", file=sys.stderr)
        print("  Common causes:", file=sys.stderr)
        print("   • No internet on first run (this script needs it once).", file=sys.stderr)
        print("   • Corporate proxy blocks huggingface.co — try a hotspot.", file=sys.stderr)
        sys.exit(1)

    # Force a sample encode so we know the cache is fully primed and
    # the model produces sane output on this machine.
    out = model.encode(["hello workshop"], normalize_embeddings=True)
    elapsed = time.perf_counter() - t0

    if cached:
        print(f"warmup: loaded from cache in {elapsed:.1f}s")
    else:
        print(f"warmup: downloaded + cached in {elapsed:.1f}s")
    print(f"  embedding shape: {out.shape}  (expected (1, {DIM}))")
    print()
    print("You're set. Try:")
    print("  python participant.py pico-unit-1")
    print("  python send_nl.py")


if __name__ == "__main__":
    main()
