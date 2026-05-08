"""participant.py — stream projector: tails stream:readings + stream:events,
writes nested JSON docs to state:<unit_id>.

Schema written to Redis (state:<unit_id>):
    {
      "unit_id":            "pico-unit-1",
      "streaming":          true,
      "stream_interval_ms": 10000,
      "current":  { "temp": 71.2, "humidity": 43.1, "ts_ms": 1761003010123 },
      "prior":    { "temp": 71.0, "humidity": 43.0, "ts_ms": 1761003000098 },
      "event_pending":   false,
      "pending_code":    null,
      "last_event_id":   "1761003008912-0",
      "last_update_ms":  1761003010123,
      "history": [
        { "started_ms": ..., "finished_ms": ..., "name": "blink", "status": "ok", "error": null }
      ]
    }

The Pico is a pure stream producer — this script is the ONLY JSON writer.

Credentials resolve: CLI flag > env var > .env (next to this script) > localhost default.

Install:
    uv sync

Run:
    uv run participant.py --unit pico-unit-1
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import redis


HISTORY_CAP = 20


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

STREAM_READINGS = "stream:readings"
STREAM_EVENTS = "stream:events"


def state_key(unit_id: str) -> str:
    return f"state:{unit_id}"


def _stream_id_to_ms(stream_id: str) -> int:
    """Extract ms timestamp from a Redis stream ID like '1777045757796-0'."""
    return int(stream_id.split("-")[0])


def _seed_doc(unit_id: str, interval_ms: int = 10000) -> dict:
    """Empty state doc for a unit we haven't seen before."""
    return {
        "unit_id": unit_id,
        "streaming": True,
        "stream_interval_ms": interval_ms,
        "current": {"temp": None, "humidity": None, "ts_ms": None},
        "prior": None,
        "event_pending": False,
        "pending_code": None,
        "last_event_id": None,
        "last_update_ms": None,
        "history": [],
        # Network identity — populated when the Pico emits kind=boot to
        # stream:events. Lets attendees find their Pico without USB.
        "network": {"ip": "", "ssid": "", "hostname": ""},
    }


def _apply_reading(doc: dict, fields: dict, stream_id: str) -> tuple[dict, bool]:
    """Apply a stream:readings entry. Returns (doc, changed).

    Only counts as changed if temp or humidity differ from current,
    or if this is the first reading (doc didn't exist yet).
    Identical readings are dismissed — no write, no timestamp bump.
    """
    ts_ms = _stream_id_to_ms(stream_id)
    new_temp = float(fields["temperature"])
    new_hum = float(fields["humidity"])

    old_temp = doc["current"].get("temp")
    old_hum = doc["current"].get("humidity")
    was_offline = doc.get("streaming") is not True

    changed = (new_temp != old_temp
               or new_hum != old_hum
               or old_temp is None
               or was_offline)

    if not changed:
        return doc, False

    new_current = {
        "temp": new_temp,
        "humidity": new_hum,
        "ts_ms": ts_ms,
    }
    if doc["current"]["ts_ms"] is not None:
        doc["prior"] = dict(doc["current"])
    doc["current"] = new_current
    doc["last_update_ms"] = ts_ms
    doc["streaming"] = True
    return doc, True


def _apply_event(doc: dict, fields: dict, stream_id: str) -> tuple[dict, bool]:
    """Apply a stream:events entry. Returns (doc, changed).

    Events always count as a change — they represent new state
    (pending command, completed command, history entry).
    """
    kind = fields.get("kind", "")
    fn = fields.get("fn", "")

    if kind == "fn_start":
        doc["event_pending"] = True
        doc["pending_code"] = fn

    elif kind == "fn_end":
        doc["event_pending"] = False
        doc["pending_code"] = None
        doc["last_event_id"] = stream_id
        entry = {
            "started_ms": int(fields.get("started_ms", 0)),
            "finished_ms": int(fields.get("finished_ms", 0)),
            "name": fn,
            "status": fields.get("status", ""),
            "error": fields.get("error") or None,
        }
        doc["history"].append(entry)
        if len(doc["history"]) > HISTORY_CAP:
            doc["history"] = doc["history"][-HISTORY_CAP:]

    elif kind == "boot":
        doc.setdefault("network", {"ip": "", "ssid": "", "hostname": ""})
        doc["network"]["ip"]       = fields.get("ip", "")
        doc["network"]["ssid"]     = fields.get("ssid", "")
        doc["network"]["hostname"] = fields.get("hostname", "")

    else:
        return doc, False

    doc["last_update_ms"] = _stream_id_to_ms(stream_id)
    return doc, True


def write_doc(r: redis.Redis, unit_id: str, doc: dict) -> str:
    key = state_key(unit_id)
    payload = json.dumps(doc)
    r.execute_command("JSON.SET", key, "$", payload)
    return "set"


def _replay_boot_event(r: redis.Redis, unit_id: str, doc: dict) -> dict:
    """Look back through stream:events for the most recent kind=boot for
    this unit and apply it. No-op if not found."""
    try:
        entries = r.xrevrange(STREAM_EVENTS, count=200)
        for stream_id, fields in entries:
            if (fields.get("unit_id") == unit_id
                    and fields.get("kind") == "boot"):
                doc, _ = _apply_event(doc, fields, stream_id)
                break
    except Exception:
        pass
    return doc


def bootstrap(r: redis.Redis, unit_id: str) -> dict:
    """Seed from the newest stream:readings entry matching this unit,
    then replay the most recent boot event for network identity."""
    doc = _seed_doc(unit_id)
    key = state_key(unit_id)

    # Check if we already have a persisted doc
    try:
        existing = r.execute_command("JSON.GET", key, "$")
        if existing:
            parsed = json.loads(existing)
            if isinstance(parsed, list):
                parsed = parsed[0]
            if parsed.get("unit_id") == unit_id:
                # Backfill the network section if this is a legacy doc
                # written before we tracked it.
                parsed.setdefault(
                    "network", {"ip": "", "ssid": "", "hostname": ""}
                )
                parsed = _replay_boot_event(r, unit_id, parsed)
                write_doc(r, unit_id, parsed)
                print(f"bootstrap: restored existing state:{unit_id}")
                return parsed
    except Exception:
        pass

    # Seed from recent readings
    entries = r.xrevrange(STREAM_READINGS, count=100)
    for stream_id, fields in entries:
        if fields.get("device_id") == unit_id:
            doc, _ = _apply_reading(doc, fields, stream_id)
            doc = _replay_boot_event(r, unit_id, doc)
            action = write_doc(r, unit_id, doc)
            print(f"bootstrap: {action} state:{unit_id} — "
                  f"temp={doc['current']['temp']} hum={doc['current']['humidity']}")
            return doc

    print(f"bootstrap: no recent sample for {unit_id} — waiting for data...")
    doc = _replay_boot_event(r, unit_id, doc)
    write_doc(r, unit_id, doc)
    return doc


def follow(r: redis.Redis, unit_id: str, doc: dict) -> None:
    """Block-read both streams, apply updates, write to Redis.

    Staleness: if no reading arrives for 3 sample cycles, mark the unit
    offline (streaming=false). When data resumes, flip back to true.
    """
    last_readings = "$"
    last_events = "$"
    streams = {STREAM_READINGS: last_readings, STREAM_EVENTS: last_events}

    interval_s = doc.get("stream_interval_ms", 10000) / 1000
    stale_limit = interval_s * 3          # 3 missed cycles = offline
    last_reading_time = time.time()       # track wall-clock of last reading

    while True:
        msgs = r.xread(streams, block=5000)

        # Check staleness every wakeup (even on timeout with no msgs)
        elapsed = time.time() - last_reading_time
        if elapsed > stale_limit and doc.get("streaming") is True:
            doc["streaming"] = False
            write_doc(r, unit_id, doc)
            print(f"         offline  state:{unit_id} — "
                  f"no reading for {elapsed:.0f}s (>{stale_limit:.0f}s)")

        if not msgs:
            continue

        dirty = False
        got_reading = False
        for stream_name, entries in msgs:
            for stream_id, fields in entries:
                streams[stream_name] = stream_id

                if stream_name == STREAM_READINGS:
                    if fields.get("device_id") != unit_id:
                        continue
                    got_reading = True
                    doc, changed = _apply_reading(doc, fields, stream_id)
                    if changed:
                        dirty = True
                        print(f"{stream_id}  changed  state:{unit_id} — "
                              f"temp={doc['current']['temp']} hum={doc['current']['humidity']}")
                    else:
                        print(f"{stream_id}  skip     (no diff)")

                elif stream_name == STREAM_EVENTS:
                    if fields.get("unit_id") != unit_id:
                        continue
                    doc, changed = _apply_event(doc, fields, stream_id)
                    if changed:
                        dirty = True
                        kind = fields.get("kind", "?")
                        if kind == "boot":
                            print(f"{stream_id}  event    state:{unit_id} — "
                                  f"boot ip={fields.get('ip','?')} "
                                  f"ssid={fields.get('ssid','?')!r} "
                                  f"host={fields.get('hostname','?')}.local")
                        else:
                            fn = fields.get("fn", "?")
                            print(f"{stream_id}  event    state:{unit_id} — "
                                  f"{kind} {fn}")

        if got_reading:
            last_reading_time = time.time()

        if dirty:
            write_doc(r, unit_id, doc)


def _connect_or_die(args) -> "redis.Redis":
    """Connect, give a clear hint if we ended up pointed at localhost
    because .env wasn't found and no flags/env-vars were set."""
    r = redis.Redis(
        host=args.host, port=args.port,
        username=args.username, password=args.password,
        decode_responses=True,
    )
    try:
        r.ping()
        return r
    except redis.RedisError as e:
        print(f"redis: cannot connect — {e}", file=sys.stderr)
        if _ENV_PATH is None and args.host == "localhost":
            print(
                "\nHint: no .env was found next to this script and no "
                "--host was passed, so the script defaulted to "
                "localhost:6379 (which isn't reachable here).\n"
                "Fix one of:\n"
                "  • Copy .env.example to .env and fill in your Redis Cloud creds\n"
                "  • Pass --host/--port/--username/--password explicitly\n"
                "  • Or export PICO_REDIS_HOST/PORT/USER/PASSWORD env vars",
                file=sys.stderr,
            )
        sys.exit(1)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Stream projector — tails readings+events, writes nested JSON to state:<unit>."
    )
    # Accept the unit either positionally (`participant.py pico-unit-2`) or
    # via --unit. Positional wins if both are given.
    p.add_argument("unit", nargs="?", default=None,
                   help="unit_id to track, e.g. pico-unit-2 "
                        "(default: pico-unit-1)")
    p.add_argument("--unit", dest="unit_flag", default=None,
                   help="same as the positional argument")
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
    args.unit = args.unit or args.unit_flag or "pico-unit-1"

    if _ENV_PATH:
        print(f"env: loaded from {_ENV_PATH}")

    r = _connect_or_die(args)

    # Probe JSON.SET
    try:
        r.execute_command("JSON.SET", "__probe:participant__", "$", '{"ok":true}')
        r.delete("__probe:participant__")
    except redis.ResponseError as e:
        if "unknown command" in str(e).lower():
            print("redis: RedisJSON module not loaded.", file=sys.stderr)
            sys.exit(1)
        raise

    # Ensure stream:events exists (Pico creates it on first fn dispatch,
    # but we need it for XREAD before any commands have been sent)
    try:
        r.xinfo_stream(STREAM_EVENTS)
    except redis.ResponseError:
        # Create with a dummy entry then trim it
        r.xadd(STREAM_EVENTS, {"kind": "init", "unit_id": "_bootstrap"})
        print(f"created {STREAM_EVENTS} stream")

    print(f"connected redis://{args.host}:{args.port} — tracking {args.unit!r}")
    doc = bootstrap(r, args.unit)

    # Surface network identity up front so attendees know where their
    # Pico lives on the LAN. Comes from the kind=boot event projected
    # into doc["network"]; empty until the Pico has emitted it.
    net = doc.get("network") or {}
    print(f"  unit_id : {doc.get('unit_id')}")
    print(f"  ip      : {net.get('ip') or '(waiting for boot event)'}")
    if net.get("hostname"):
        print(f"  hostname: {net['hostname']}.local")
    else:
        print(f"  hostname: (waiting for boot event)")
    print(f"  ssid    : {net.get('ssid') or '(waiting for boot event)'}")

    try:
        follow(r, args.unit, doc)
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
