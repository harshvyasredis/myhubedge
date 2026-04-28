"""send_command.py — XADD a function-call command to stream:commands.

The Pico fleet tails this stream via per-unit consumer groups
(`cg:<unit_id>`). Each Pico reacts to entries where `target` is either
its own unit_id or "all". See pico/functions.py for the registry.

Examples:
    python client/send_command.py --target pico-unit-1 --fn led_on
    python client/send_command.py --target pico-unit-1 --fn led_off
    python client/send_command.py --target pico-unit-1 --fn blink \\
        --arg times=5 --arg ms=100
    python client/send_command.py --target all --fn say \\
        --arg message="hello fleet" --arg secs=8

Credentials resolve the same way as create_index.py and participant.py:
    CLI flag > env var > redis_creds.py (HOST/PORT/USER/PASS) > localhost.

Install:
    pip install redis
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import sys

import redis


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
