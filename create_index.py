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

Credentials: CLI flag > env var > secrets.py > localhost default.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys

import redis


def _load_secrets() -> tuple[dict, str | None]:
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "secrets.py"),
        os.path.join(here, "..", "pico-current", "secrets.py"),
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
    p.add_argument("--recreate", action="store_true",
                   help="DROPINDEX first, then CREATE. JSON docs NOT deleted.")
    args = p.parse_args()

    if _SECRETS_PATH:
        print(f"secrets: loaded from {_SECRETS_PATH}")
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
