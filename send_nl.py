"""send_nl.py — interactive natural-language Pico command client.

Each participant runs this on their laptop. It loops for input,
embeds each query, searches Redis for the best function match,
resolves the target unit, dispatches the command, and waits for
the Pico to report back — showing timing at every step.

Stack:
  - Embedding:  redis/langcache-embed-v3-small (HuggingFace, 300 dims)
  - Search:     redisvl VectorQuery over idx:functions (JSON-stored vectors)
  - Dispatch:   XADD stream:commands
  - Response:   XREAD stream:events (waits for fn_end from Pico)

Install:
    pip install redis sentence-transformers redisvl

Run:
    python client/send_nl.py
    python client/send_nl.py "flash the led on unit 1"   # one-shot mode
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import re
import socket
import sys
import time as _time

# Prefer a bundled HuggingFace model cache (`hf_cache/` next to this
# file) so the script can run without internet — useful on Codespaces
# or any restricted network. Must run BEFORE the sentence_transformers
# import because env vars are read at construction time.
_LOCAL_HF = pathlib.Path(__file__).parent.resolve() / "hf_cache"
if _LOCAL_HF.is_dir() and not os.environ.get("HF_HOME"):
    os.environ["HF_HOME"] = str(_LOCAL_HF)

import numpy as np
import redis
from sentence_transformers import SentenceTransformer
from redisvl.index import SearchIndex
from redisvl.query import VectorQuery


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


def parse_target(query: str, default: str = "all") -> str:
    """Extract target from natural language.
    "unit 1" -> pico-unit-1, "all"/"every"/"fleet" -> all."""
    q = query.lower()
    if re.search(r'\ball\b|\bevery\b|\bfleet\b|\beveryone\b', q):
        return "all"
    m = re.search(r'pico-unit-(\d+)', q)
    if m:
        return f"pico-unit-{m.group(1)}"
    m = re.search(r'unit[- ]?(\d+)', q)
    if m:
        return f"pico-unit-{m.group(1)}"
    return default


def extract_args(query: str, fn_name: str) -> dict:
    """Extract function-specific args from query text."""
    args = {}
    if fn_name == "blink":
        m = re.search(r'(\d+)\s*times?', query.lower())
        if m:
            args["times"] = m.group(1)
        m = re.search(r'(\d+)\s*ms', query.lower())
        if m:
            args["ms"] = m.group(1)
    elif fn_name == "say":
        m = re.search(r'["\'](.+?)["\']', query)
        if m:
            args["message"] = m.group(1)
        else:
            m = re.search(r'(?:say|show|display|write)\s+(.+?)(?:\s+on\s+|\s+to\s+|$)',
                          query, re.IGNORECASE)
            if m:
                msg = re.sub(r'\s*(unit[- ]?\d+|pico-unit-\d+|all units?|the fleet)\s*',
                             '', m.group(1)).strip()
                if msg:
                    args["message"] = msg
    return args


def wait_for_execution(r_txt: redis.Redis, stream_id: str,
                       timeout_s: float = 15.0) -> dict | None:
    """Poll stream:events for fn_end matching our command's stream_id."""
    last_id = "$"
    deadline = _time.time() + timeout_s
    while _time.time() < deadline:
        remaining_ms = int((deadline - _time.time()) * 1000)
        if remaining_ms <= 0:
            break
        msgs = r_txt.xread({"stream:events": last_id},
                           block=min(remaining_ms, 2000))
        if not msgs:
            continue
        for _stream, entries in msgs:
            for eid, fields in entries:
                last_id = eid
                if (fields.get("stream_id") == stream_id and
                        fields.get("kind") == "fn_end"):
                    return fields
    return None


def dispatch_and_wait(query: str, model: SentenceTransformer,
                      fn_index: SearchIndex, r_txt: redis.Redis,
                      default_target: str, stream: str) -> None:
    """Process one NL query: embed -> search -> dispatch -> wait."""

    # ── Step 1: Embed ──
    t0 = _time.perf_counter()
    query_emb = model.encode([query], normalize_embeddings=True)[0]
    t_embed = (_time.perf_counter() - t0) * 1000

    # ── Step 2: KNN search via redisvl ──
    t0 = _time.perf_counter()
    vq = VectorQuery(
        vector=query_emb.tolist(),
        vector_field_name="embedding",
        return_fields=["name", "description", "vector_distance"],
        num_results=3,
    )
    results = fn_index.query(vq)
    t_search = (_time.perf_counter() - t0) * 1000

    if not results:
        print("  [!] no matching function found.\n")
        return

    # Show top 3 matches
    top = results[0]
    fn_name = top["name"]
    fn_desc = top["description"]
    fn_dist = float(top["vector_distance"])

    print(f"\n  ┌─ SEARCH RESULTS (top 3)")
    for i, r in enumerate(results):
        marker = " ◀ best" if i == 0 else ""
        print(f"  │  {i+1}. {r['name']:<18} dist={float(r['vector_distance']):.4f}{marker}")
        print(f"  │     {r['description'][:65]}")
    print(f"  └─")

    # ── Step 3: Resolve target ──
    target = parse_target(query, default_target)
    fn_args = extract_args(query, fn_name)

    print(f"\n  function:  {fn_name}")
    print(f"  target:    {target}")
    print(f"  args:      {fn_args}")

    # ── Step 4: Dispatch ──
    t0 = _time.perf_counter()
    payload = {
        "target": target,
        "fn":     fn_name,
        "args":   json.dumps(fn_args),
        "sender": socket.gethostname(),
        "nl_query": query,
        "nl_score": f"{fn_dist:.4f}",
    }
    stream_id = r_txt.xadd(stream, payload)
    t_dispatch = (_time.perf_counter() - t0) * 1000

    # ── Step 5: Wait for Pico ──
    print(f"  waiting for Pico response...", end="", flush=True)
    t0 = _time.perf_counter()
    event = wait_for_execution(r_txt, stream_id)
    t_response = (_time.perf_counter() - t0) * 1000

    if event:
        status = event.get("status", "?")
        result = event.get("result", "")
        error = event.get("error", "")
        started = int(event.get("started_ms", 0))
        finished = int(event.get("finished_ms", 0))
        exec_ms = finished - started if started and finished else 0
        print(f" done!")
        print(f"  pico says: {result or error}")
        print(f"  status:    {status}")
        if exec_ms:
            print(f"  exec time: {exec_ms} ms (on-device)")
    else:
        print(f" timeout!")
        print(f"  [!] no response — is the Pico online?")

    # ── Timing summary ──
    print(f"\n  {'─'*40}")
    print(f"  TIMING")
    print(f"  {'─'*40}")
    print(f"  embed query:     {t_embed:7.1f} ms")
    print(f"  redis KNN search:{t_search:7.1f} ms")
    print(f"  dispatch XADD:   {t_dispatch:7.1f} ms")
    print(f"  pico round-trip: {t_response:7.0f} ms")
    total = t_embed + t_search + t_dispatch + t_response
    print(f"  total:           {total:7.0f} ms")
    print(f"  {'─'*40}\n")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Interactive NL Pico command client — embed, search, dispatch, wait."
    )
    p.add_argument("query", nargs="?", default=None,
                   help="One-shot query (omit for interactive loop)")
    p.add_argument("--target", default="all",
                   help="Default target if not in query (default: all)")
    p.add_argument("--stream", default="stream:commands")
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

    redis_url = f"redis://{args.username}:{args.password}@{args.host}:{args.port}"

    # Text connection for XADD/XREAD
    r_txt = redis.Redis(
        host=args.host, port=args.port,
        username=args.username, password=args.password,
        decode_responses=True,
    )
    try:
        r_txt.ping()
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

    # redisvl index for vector search (loads schema from existing index)
    fn_index = SearchIndex.from_existing("idx:functions", redis_url=redis_url)

    print(f"loading {MODEL_NAME} (dim={VECTOR_DIM})...")
    model = SentenceTransformer(MODEL_NAME, truncate_dim=VECTOR_DIM)

    if args.query:
        # One-shot mode
        dispatch_and_wait(args.query, model, fn_index, r_txt,
                          args.target, args.stream)
        return

    # Interactive loop
    print("\n" + "=" * 60)
    print("  Pico NL Command Client")
    print("  Type a command in plain English. Ctrl-C to quit.")
    print()
    print("  Examples:")
    print("    flash the led on unit 1")
    print("    blink 5 times on all units")
    print('    say "hello world" on unit 1')
    print("    turn off the light")
    print("=" * 60)

    while True:
        try:
            query = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            return
        if not query:
            continue
        if query.lower() in ("quit", "exit", "q"):
            print("bye")
            return
        dispatch_and_wait(query, model, fn_index, r_txt,
                          args.target, args.stream)


if __name__ == "__main__":
    main()
