"""create_index.py — build the RediSearch indexes for the workshop.

Creates two indexes:

1. idx:state — over state:<unit_id> JSON docs (nested schema from participant.py).
   Powers fleet aggregates and unit queries.

2. idx:functions — over fn:<name> JSON docs (from register_functions.py).
   Powers KNN vector search for natural-language command dispatch.

Install:
    pip install redis

Run once:
    python client/create_index.py

Re-run safely with --recreate to DROPINDEX + CREATE (docs untouched).

Credentials: CLI flag > env var > .env (next to this script) > localhost default.
"""
from __future__ import annotations

import argparse
import os
import sys

import redis


def _load_env() -> str | None:
    """Load .env (next to this script) into os.environ. Existing env
    vars take precedence — same semantics as python-dotenv defaults."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, ".env")
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return path


_ENV_PATH = _load_env()


# ---- idx:state — nested schema over state:<unit_id> docs ----
STATE_INDEX = "idx:state"
STATE_PREFIX = "state:"
STATE_SCHEMA = [
    ("$.unit_id",          "unit_id",        "TAG"),
    ("$.current.temp",     "temp",           "NUMERIC", "SORTABLE"),
    ("$.current.humidity", "humidity",       "NUMERIC", "SORTABLE"),
    ("$.last_update_ms",   "last_update_ms", "NUMERIC", "SORTABLE"),
    ("$.streaming",        "streaming",      "TAG"),
    ("$.pending_code",     "pending_code",   "TAG"),
    ("$.event_pending",    "event_pending",  "TAG"),
]

# ---- idx:functions — vector search over fn:<name> docs ----
FUNC_INDEX = "idx:functions"
FUNC_PREFIX = "fn:"
FUNC_VECTOR_DIM = 300
FUNC_SCHEMA = [
    ("$.name",        "name",        "TAG"),
    ("$.description", "description", "TEXT"),
    ("$.embedding",   "embedding",   "VECTOR", "FLAT", "6",
     "TYPE", "FLOAT32", "DIM", str(FUNC_VECTOR_DIM), "DISTANCE_METRIC", "COSINE"),
]


def _build_ft_create(index_name: str, prefix: str, schema: list) -> list[str]:
    args: list[str] = [
        "FT.CREATE", index_name,
        "ON", "JSON",
        "PREFIX", "1", prefix,
        "SCHEMA",
    ]
    for field in schema:
        path, alias, ftype, *modifiers = field
        args += [path, "AS", alias, ftype, *modifiers]
    return args


def connect(args: argparse.Namespace) -> redis.Redis:
    r = redis.Redis(
        host=args.host, port=args.port,
        username=args.username, password=args.password,
        decode_responses=True,
    )
    try:
        r.ping()
    except redis.RedisError as e:
        print(f"redis: cannot connect — {e}", file=sys.stderr)
        if _ENV_PATH is None and args.host == "localhost":
            print(
                "\nHint: no .env was found next to this script and no "
                "--host was passed, so the script defaulted to "
                "localhost:6379.\nCopy .env.example to .env and fill it in, "
                "or pass --host/--port/--username/--password explicitly.",
                file=sys.stderr,
            )
        sys.exit(1)
    return r


def _index_exists(r: redis.Redis, name: str) -> bool:
    try:
        r.execute_command("FT.INFO", name)
        return True
    except redis.ResponseError as e:
        if "unknown index" in str(e).lower() or "no such index" in str(e).lower():
            return False
        raise


def _drop_index(r: redis.Redis, name: str) -> None:
    try:
        r.execute_command("FT.DROPINDEX", name)
        print(f"  dropped {name}")
    except redis.ResponseError as e:
        if "unknown index" in str(e).lower() or "no such index" in str(e).lower():
            return
        raise


def _create_index(r: redis.Redis, name: str, prefix: str, schema: list) -> None:
    cmd = _build_ft_create(name, prefix, schema)
    try:
        r.execute_command(*cmd)
    except redis.ResponseError as e:
        if "already exists" in str(e).lower():
            print(f"  {name} already exists — use --recreate to rebuild")
            return
        print(f"  FT.CREATE {name} failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"  created {name}")


def _show_info(r: redis.Redis, name: str) -> None:
    info = r.execute_command("FT.INFO", name)
    kv = dict(zip(info[0::2], info[1::2]))
    print(f"  index:    {kv.get('index_name', name)}")
    print(f"  num_docs: {kv.get('num_docs', '?')}")
    attrs = kv.get("attributes") or []
    print(f"  fields ({len(attrs)}):")
    for a in attrs:
        ad = dict(zip(a[0::2], a[1::2]))
        print(f"    - {ad.get('attribute', '?'):<16} "
              f"{ad.get('type', '?'):<8} "
              f"path={ad.get('identifier', '?')}")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Create idx:state and idx:functions RediSearch indexes."
    )
    p.add_argument(
        "--host",
        default=os.environ.get("PICO_REDIS_HOST", "localhost"),
    )
    p.add_argument(
        "--port", type=int,
        default=int(os.environ.get("PICO_REDIS_PORT", 6379)),
    )
    p.add_argument(
        "--username",
        default=os.environ.get("PICO_REDIS_USER", "default"),
    )
    p.add_argument(
        "--password",
        default=os.environ.get("PICO_REDIS_PASSWORD"),
    )
    p.add_argument("--recreate", action="store_true",
                   help="DROPINDEX first, then CREATE. JSON docs NOT deleted.")
    args = p.parse_args()

    if _ENV_PATH:
        print(f"env: loaded from {_ENV_PATH}")
    print(f"connecting redis://{args.username}@{args.host}:{args.port}")

    r = connect(args)

    # Assert RediSearch is available
    try:
        r.execute_command("FT._LIST")
    except redis.ResponseError as e:
        if "unknown command" in str(e).lower():
            print("redis: RediSearch module not loaded.", file=sys.stderr)
            sys.exit(1)
        raise

    for name, prefix, schema in [
        (STATE_INDEX, STATE_PREFIX, STATE_SCHEMA),
        (FUNC_INDEX, FUNC_PREFIX, FUNC_SCHEMA),
    ]:
        print(f"\n--- {name} ---")
        if args.recreate and _index_exists(r, name):
            _drop_index(r, name)
        _create_index(r, name, prefix, schema)
        if _index_exists(r, name):
            _show_info(r, name)


if __name__ == "__main__":
    main()
