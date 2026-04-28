"""register_functions.py — seed fn:<name> JSON docs with vector embeddings.

Each Pico function gets a JSON doc at fn:<name> containing:
    {
      name, description, code,
      phrases: ["phrase1", "phrase2", ...],
      embeddings: [[300 floats], [300 floats], ...],   // per-phrase
      embedding:  [300 floats]                          // average of all phrases
    }

The idx:functions index uses the averaged `embedding` for KNN search.
The per-phrase `embeddings` array is stored for transparency and debugging.

Embedding: redis/langcache-embed-v3-small from HuggingFace, truncated to 300 dims.

Install:
    pip install redis sentence-transformers redisvl

Run:
    python client/register_functions.py
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import sys
import time

# Pin the HuggingFace cache to `hf_cache/` next to this file so the
# model only downloads once. Run `python warmup.py` first to populate.
_LOCAL_HF = pathlib.Path(__file__).parent.resolve() / "hf_cache"
_LOCAL_HF.mkdir(exist_ok=True)
os.environ.setdefault("HF_HOME", str(_LOCAL_HF))

import numpy as np
import redis
from sentence_transformers import SentenceTransformer


def _load_secrets() -> tuple[dict, str | None]:
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "redis_creds.py"),
        os.path.join(here, "..", "pico-current", "redis_creds.py"),
    ]
    for raw in candidates:
        path = os.path.abspath(raw)
        if not os.path.isfile(path):
            continue
        spec = importlib.util.spec_from_file_location("_pico_secrets", path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            print(f"secrets: failed to load {path} — {e}", file=sys.stderr)
            continue
        return {
            "host":     getattr(mod, "HOST", None),
            "port":     getattr(mod, "PORT", None),
            "user":     getattr(mod, "USER", None),
            "password": getattr(mod, "PASS", None),
        }, path
    return {}, None


_SECRETS, _SECRETS_PATH = _load_secrets()

MODEL_NAME = "redis/langcache-embed-v3-small"
VECTOR_DIM = 300

# ── Function registry ──
# Each function has 5 phrases — different ways a user might ask for it.
# All 5 get embedded; the average becomes the searchable vector.
FUNCTIONS = [
    {
        "name": "led_on",
        "description": "Turn on the LED light on the Pico board.",
        "phrases": [
            "turn on the LED",
            "switch the light on",
            "light up the board on unit 3",
            "turn on the LED on all units",
            "enable the LED on every Pico",
        ],
        "code": """async def led_on(args):
    _led.on()
    return "led on" """,
    },
    {
        "name": "led_off",
        "description": "Turn off the LED light on the Pico board.",
        "phrases": [
            "turn off the LED",
            "switch the light off on unit 2",
            "kill the light on all units",
            "disable the LED on every Pico",
            "turn off the LED on unit 5",
        ],
        "code": """async def led_off(args):
    _led.off()
    return "led off" """,
    },
    {
        "name": "blink",
        "description": "Blink the LED on the Pico board repeatedly.",
        "phrases": [
            "blink the LED on unit 1",
            "flash the light on and off on all units",
            "pulse the LED 5 times on unit 3",
            "make the light blink on every Pico",
            "strobe the onboard LED on unit 7",
        ],
        "code": """async def blink(args):
    times = int(args.get("times", 3))
    period_ms = int(args.get("ms", 200))
    for _ in range(times):
        _led.on(); await asyncio.sleep_ms(period_ms)
        _led.off(); await asyncio.sleep_ms(period_ms)
    return "blinked %d" % times""",
    },
    {
        "name": "say",
        "description": "Display a text message on the OLED screen.",
        "phrases": [
            "send a message to unit 5",
            "show hello message on unit 1",
            "say hello everyone on all units",
            "display a message on the screen of unit 3",
            "write a greeting on every Pico display",
        ],
        "code": """async def say(args):
    msg = str(args.get("message", ""))
    secs = int(args.get("secs", 5))
    display.show_say(msg, secs)
    return "said: " + msg[:20]""",
    },
    {
        "name": "create_function",
        "description": "Register a new custom function on the Pico at runtime.",
        "phrases": [
            "create a new function on unit 1",
            "register a custom handler on all units",
            "add a new command to the Pico",
            "define a runtime function on unit 4",
            "build a dynamic handler with code on every Pico",
        ],
        "code": """async def create_function(args):
    name = args.get("name")
    code = args.get("code")
    ns = _exec_namespace()
    exec(code, ns)
    handler = ns.get("handler")
    _DYNAMIC[name] = handler
    return "registered: " + name""",
    },
]


def embed_functions(model: SentenceTransformer, functions: list[dict]) -> list[dict]:
    """Embed all phrases for each function. Store per-phrase embeddings
    and their average as the searchable vector."""
    all_phrases = []
    phrase_counts = []
    for func in functions:
        all_phrases.extend(func["phrases"])
        phrase_counts.append(len(func["phrases"]))

    # Batch embed all phrases at once
    all_embeddings = model.encode(all_phrases, normalize_embeddings=True)

    idx = 0
    for func, count in zip(functions, phrase_counts):
        phrase_embs = all_embeddings[idx:idx + count]
        idx += count

        # Store per-phrase embeddings
        func["embeddings"] = [emb.tolist() for emb in phrase_embs]

        # Average and normalize for the searchable vector
        avg = np.mean(phrase_embs, axis=0)
        avg = avg / np.linalg.norm(avg)  # re-normalize
        func["embedding"] = avg.tolist()

    return functions


def main() -> None:
    p = argparse.ArgumentParser(
        description="Seed fn:<name> JSON docs with multi-phrase vector embeddings."
    )
    p.add_argument(
        "--host",
        default=(os.environ.get("PICO_REDIS_HOST")
                 or _SECRETS.get("host")
                 or "localhost"),
    )
    p.add_argument(
        "--port", type=int,
        default=int(os.environ.get("PICO_REDIS_PORT")
                    or _SECRETS.get("port")
                    or 6379),
    )
    p.add_argument(
        "--username",
        default=(os.environ.get("PICO_REDIS_USER")
                 or _SECRETS.get("user")
                 or "default"),
    )
    p.add_argument(
        "--password",
        default=(os.environ.get("PICO_REDIS_PASSWORD")
                 or _SECRETS.get("password")),
    )
    args = p.parse_args()

    if _SECRETS_PATH:
        print(f"secrets: loaded from {_SECRETS_PATH}")

    r = redis.Redis(
        host=args.host, port=args.port,
        username=args.username, password=args.password,
        decode_responses=True,
    )
    try:
        r.ping()
    except redis.RedisError as e:
        print(f"redis: cannot connect — {e}", file=sys.stderr)
        if _SECRETS_PATH is None and args.host == "localhost":
            print(
                "\nHint: no redis_creds.py was found in this directory and no "
                "--host was passed, so the script defaulted to "
                "localhost:6379.\nRun from the workshop-client/ directory, "
                "copy redis_creds.py here, or pass --host/--port/--username/"
                "--password explicitly.",
                file=sys.stderr,
            )
        sys.exit(1)

    # ── Load model ──
    print(f"\n{'='*62}")
    print(f"  REGISTER FUNCTIONS — Multi-Phrase Vector Embeddings")
    print(f"  Model: {MODEL_NAME} | Dim: {VECTOR_DIM}")
    print(f"{'='*62}\n")

    t0 = time.perf_counter()
    model = SentenceTransformer(MODEL_NAME, truncate_dim=VECTOR_DIM)
    t_load = (time.perf_counter() - t0) * 1000
    print(f"  model loaded in {t_load:.0f} ms\n")

    # ── Embed all phrases ──
    total_phrases = sum(len(f["phrases"]) for f in FUNCTIONS)
    t0 = time.perf_counter()
    functions = embed_functions(model, FUNCTIONS)
    t_embed = (time.perf_counter() - t0) * 1000
    print(f"  embedded {total_phrases} phrases across {len(FUNCTIONS)} functions in {t_embed:.0f} ms\n")

    # ── Display each function ──
    print(f"{'─'*62}")
    for func in functions:
        print(f"\n  ┌─ fn:{func['name']}")
        print(f"  │  description: {func['description']}")
        print(f"  │")
        print(f"  │  phrases (each vectorized):")
        for i, phrase in enumerate(func["phrases"]):
            emb_i = func["embeddings"][i]
            print(f"  │    {i+1}. \"{phrase}\"")
            print(f"  │       [{emb_i[0]:.4f}, {emb_i[1]:.4f}, ... {emb_i[-1]:.4f}]")
        print(f"  │")
        avg = func["embedding"]
        print(f"  │  avg embedding (searchable):")
        print(f"  │    [{avg[0]:.4f}, {avg[1]:.4f}, ... {avg[-1]:.4f}]")
        print(f"  │")
        print(f"  │  code:")
        for line in func["code"].strip().split("\n"):
            print(f"  │    {line}")
        print(f"  └─")
    print(f"\n{'─'*62}")

    # ── Write to Redis ──
    print(f"\n  WRITING TO REDIS\n")
    t0 = time.perf_counter()
    for func in functions:
        key = f"fn:{func['name']}"
        payload = json.dumps(func)
        r.execute_command("JSON.SET", key, "$", payload)
        n_bytes = len(payload)
        print(f"    JSON.SET {key} — {n_bytes:,} bytes "
              f"({len(func['phrases'])} phrases, "
              f"{len(func['embeddings'])} phrase vectors + 1 avg)")
    t_write = (time.perf_counter() - t0) * 1000

    # ── Verify ──
    try:
        info = r.execute_command("FT.INFO", "idx:functions")
        kv = dict(zip(info[0::2], info[1::2]))
        n_docs = kv.get("num_docs", "?")
    except Exception:
        n_docs = "?"

    print(f"\n{'─'*62}")
    print(f"  SUMMARY")
    print(f"{'─'*62}")
    print(f"  functions:     {len(FUNCTIONS)}")
    print(f"  phrases/fn:    {total_phrases // len(FUNCTIONS)}")
    print(f"  total phrases: {total_phrases}")
    print(f"  idx:functions: {n_docs} docs")
    print(f"  embed time:    {t_embed:.0f} ms ({t_embed/total_phrases:.1f} ms/phrase)")
    print(f"  write time:    {t_write:.0f} ms")
    print(f"  vector dim:    {VECTOR_DIM}")
    print(f"  model:         {MODEL_NAME}")
    print(f"{'─'*62}\n")

    print("  Searchable vector = normalized average of all phrase embeddings.")
    print("  Query vector gets compared against this average via cosine KNN.")
    print()


if __name__ == "__main__":
    main()
