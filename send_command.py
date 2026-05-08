"""send_command.py — XADD a function-call command to stream:commands.

The Pico fleet tails this stream via per-unit consumer groups
(`cg:<unit_id>`). Each Pico reacts to entries where `target` is either
its own unit_id or "all". See pico/functions.py for the registry.

Examples:
    uv run send_command.py --target pico-unit-1 --fn led_on
    uv run send_command.py --target pico-unit-1 --fn led_off
    uv run send_command.py --target pico-unit-1 --fn blink \\
        --arg times=5 --arg ms=100
    uv run send_command.py --target all --fn say \\
        --arg message="hello fleet" --arg secs=8

Credentials resolve the same way as create_index.py and participant.py:
    CLI flag > env var > .env (next to this script) > localhost default.

Install:
    uv sync
"""
from __future__ import annotations

import argparse
import json
import os
import socket
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


def _parse_kv(pairs):
    """`--arg k=v --arg k2=v2` -> `{'k': 'v', 'k2': 'v2'}`.

    Values stay as strings; handlers cast on the Pico side. Integer-y
    values like `times=5` and `secs=8` cast cleanly with `int(args[k])`
    over there, and strings like `message="hello"` pass through."""
    out = {}
    for raw in pairs or []:
        if "=" not in raw:
            print(f"bad --arg (expected k=v): {raw!r}", file=sys.stderr)
            sys.exit(2)
        k, v = raw.split("=", 1)
        out[k.strip()] = v
    return out


def main() -> None:
    p = argparse.ArgumentParser(
        description="Send a function-call command to a Pico via stream:commands."
    )
    p.add_argument("--target", required=True,
                   help='Unit id to target, or "all" for a broadcast.')
    p.add_argument("--fn", required=True,
                   help="Function name from pico/functions.py::REGISTRY "
                        "(led_on, led_off, blink, say, ...).")
    p.add_argument("--arg", action="append", metavar="K=V",
                   help="Extra handler arg, repeatable. Values are strings.")
    p.add_argument("--stream", default="stream:commands",
                   help="Override the command stream key (default: stream:commands).")
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
    args = p.parse_args()

    if _ENV_PATH:
        print(f"env: loaded from {_ENV_PATH}")

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

    payload = {
        "target": args.target,
        "fn":     args.fn,
        # args is a single JSON string field. The Pico json.loads() it
        # back into a dict. Encoding everything in one field keeps the
        # stream schema flat regardless of what the handler needs.
        "args":   json.dumps(_parse_kv(args.arg)),
        "sender": socket.gethostname(),
    }
    stream_id = r.xadd(args.stream, payload)
    print(f"sent {stream_id}: fn={args.fn} target={args.target} "
          f"args={payload['args']}")


if __name__ == "__main__":
    main()
