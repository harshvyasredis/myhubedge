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

Credentials resolve: CLI flag > env var > secrets.py > localhost default.

Install:
    pip install redis

Run:
    python client/participant.py --unit pico-unit-1
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time

import redis


HISTORY_CAP = 20


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

    else:
        return doc, False

    doc["last_update_ms"] = _stream_id_to_ms(stream_id)
    return doc, True


def write_doc(r: redis.Redis, unit_id: str, doc: dict) -> str:
    key = state_key(unit_id)
    payload = json.dumps(doc)
    r.execute_command("JSON.SET", key, "$", payload)
    return "set"


def bootstrap(r: redis.Redis, unit_id: str) -> dict:
    """Seed from the newest stream:readings entry matching this unit,
    then replay any stream:events for it."""
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
                print(f"bootstrap: restored existing state:{unit_id}")
                return parsed
    except Exception:
        pass

    # Seed from recent readings
    entries = r.xrevrange(STREAM_READINGS, count=100)
    for stream_id, fields in entries:
        if fields.get("device_id") == unit_id:
            doc, _ = _apply_reading(doc, fields, stream_id)
            action = write_doc(r, unit_id, doc)
            print(f"bootstrap: {action} state:{unit_id} — "
                  f"temp={doc['current']['temp']} hum={doc['current']['humidity']}")
            return doc

    print(f"bootstrap: no recent sample for {unit_id} — waiting for data...")
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
                        fn = fields.get("fn", "?")
                        print(f"{stream_id}  event    state:{unit_id} — "
                              f"{kind} {fn}")

        if got_reading:
            last_reading_time = time.time()

        if dirty:
            write_doc(r, unit_id, doc)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Stream projector — tails readings+events, writes nested JSON to state:<unit>."
    )
    p.add_argument("--unit", default="pico-unit-1",
                   help="unit_id to track (default: pico-unit-1)")
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
        sys.exit(1)

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
    try:
        follow(r, args.unit, doc)
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
