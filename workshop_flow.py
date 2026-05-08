"""workshop_flow.py — guided presentation flow for the Pico + Redis workshop.

Runs each demo step in order. Press Enter to advance, or type 'skip' to
jump ahead. Functions execute live on connected Picos while the facilitator
explains each concept.

Run:
    uv run workshop_flow.py --unit pico-unit-1

Each step:
  1. Shows an ASCII visual of the architecture layer being demonstrated.
  2. Explains the concept.
  3. Executes the demo (live Redis commands, Pico function calls, etc.).
  4. Shows the result and what participants should try on their own.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import socket

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

BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def banner(title: str, step: int, total: int) -> None:
    w = 62
    print(f"\n{BOLD}{'='*w}")
    print(f"  STEP {step}/{total}: {title}")
    print(f"{'='*w}{RESET}\n")


def ascii_art(art: str) -> None:
    for line in art.strip().split("\n"):
        print(f"  {CYAN}{line}{RESET}")
    print()


def explain(text: str) -> None:
    for line in text.strip().split("\n"):
        print(f"  {line}")
    print()


def participant_try(text: str) -> None:
    print(f"  {GREEN}{BOLD}YOUR TURN:{RESET}")
    for line in text.strip().split("\n"):
        print(f"  {GREEN}  {line}{RESET}")
    print()


def wait_enter() -> str:
    try:
        resp = input(f"  {DIM}[Enter to continue, 'skip' to jump]{RESET} ")
        return resp.strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n  bye")
        sys.exit(0)


def run_step(r: redis.Redis, unit_id: str, step: int, total: int) -> bool:
    """Run one presentation step. Returns False to skip remaining."""

    # ── STEP 1: THE ARCHITECTURE ──
    if step == 1:
        banner("THE ARCHITECTURE", step, total)
        ascii_art("""
    ┌─────────────────────┐         ┌──────────────────────┐
    │   Pico 2 W (x N)   │  XADD   │    Redis Cloud       │
    │                     │────────▶│                      │
    │  DHT11 sensor       │         │  stream:readings     │
    │  OLED display       │◀────────│  stream:commands     │
    │  LED + buttons      │ XREAD   │  stream:events       │
    │                     │         │                      │
    └─────────────────────┘         │  state:<unit> (JSON) │
                                    │  fn:<name>   (JSON)  │
           ┌────────────────────────│                      │
           │  XREAD + JSON.SET      │  idx:state  (search) │
           ▼                        │  idx:functions (vec)  │
    ┌─────────────────────┐         └──────────────────────┘
    │  Your Python Client │
    │  participant.py     │  Each participant runs their own
    │  send_nl.py         │  projector + command client
    └─────────────────────┘
        """)
        explain("""
    Every Pico streams sensor data into Redis. YOUR laptop reads
    that stream and writes a JSON "projection" — YOUR view of YOUR
    unit's state. Redis indexes those JSON docs so anyone can query
    the whole fleet.

    This is CQRS: Commands flow one way, Queries read the other.
        """)

    # ── STEP 2: STREAMS — SEEING LIVE DATA ──
    elif step == 2:
        banner("REDIS STREAMS — LIVE SENSOR DATA", step, total)
        ascii_art("""
    Pico ──XADD──▶ stream:readings
                    │
                    ├── {device_id, temperature, humidity, timestamp}
                    ├── {device_id, temperature, humidity, timestamp}
                    └── ...  (append-only log, newest at bottom)
        """)
        entries = r.xrevrange("stream:readings", count=3)
        print(f"  {BOLD}Latest 3 readings from stream:readings:{RESET}\n")
        for sid, fields in entries:
            ts_ms = int(sid.split("-")[0])
            age = time.time() - ts_ms / 1000
            print(f"    {YELLOW}{sid}{RESET}  age={age:.0f}s")
            print(f"      device={fields.get('device_id')}  "
                  f"temp={fields.get('temperature')}F  "
                  f"humidity={fields.get('humidity')}%")
        print()
        participant_try("""
    Open Redis Insight and run:
      XREVRANGE stream:readings + - COUNT 5
        """)

    # ── STEP 3: JSON PROJECTION ──
    elif step == 3:
        banner("JSON PROJECTION — YOUR STATE DOC", step, total)
        ascii_art("""
    stream:readings ──▶ participant.py ──▶ state:<unit_id>
    stream:events   ──▶      │
                              ▼
                         JSON.SET state:pico-unit-1 $ '{
                           "unit_id": "pico-unit-1",
                           "current": {"temp": 100.9, "humidity": 38},
                           "prior":   {"temp": 100.1, "humidity": 38},
                           "history": [...],
                           ...
                         }'
        """)
        try:
            val = r.execute_command("JSON.GET", f"state:{unit_id}", "$")
            doc = json.loads(val)[0]
            print(f"  {BOLD}Current state:{unit_id}:{RESET}\n")
            print(f"    unit_id:      {doc['unit_id']}")
            print(f"    streaming:    {doc['streaming']}")
            ct = doc.get("current", {})
            print(f"    current.temp: {ct.get('temp')}F")
            print(f"    current.hum:  {ct.get('humidity')}%")
            pr = doc.get("prior")
            if pr:
                print(f"    prior.temp:   {pr.get('temp')}F")
            print(f"    history:      {len(doc.get('history', []))} entries")
            print(f"    last_update:  {doc.get('last_update_ms')}")
        except Exception as e:
            print(f"    {YELLOW}(run participant.py first: {e}){RESET}")
        print()
        participant_try("""
    Run:  uv run participant.py --unit <your-unit>
    Then in Redis Insight:
      JSON.GET state:<your-unit> $
        """)

    # ── STEP 4: SEARCH INDEX + FLEET QUERIES ──
    elif step == 4:
        banner("SEARCH INDEX — FLEET QUERIES", step, total)
        ascii_art("""
    state:pico-unit-1  ─┐
    state:pico-unit-2  ─┼──▶  idx:state  ──▶  FT.AGGREGATE
    state:pico-unit-3  ─┘                      FT.SEARCH
                                                │
                         ┌──────────────────────┘
                         ▼
                    "Give me the average temp across all units
                     that reported in the last 2 minutes"
        """)
        now_ms = int(time.time() * 1000)
        since_ms = now_ms - 120000
        try:
            agg = r.execute_command(
                "FT.AGGREGATE", "idx:state",
                f"@last_update_ms:[{since_ms} +inf]",
                "GROUPBY", "0",
                "REDUCE", "AVG", "1", "@temp", "AS", "temp_avg",
                "REDUCE", "AVG", "1", "@humidity", "AS", "hum_avg",
                "REDUCE", "COUNT", "0", "AS", "fleet_size",
            )
            print(f"  {BOLD}FT.AGGREGATE result:{RESET}")
            print(f"    {agg}\n")
            if len(agg) > 1:
                row = dict(zip(agg[1][0::2], agg[1][1::2]))
                print(f"    fleet_size: {row.get('fleet_size')}")
                print(f"    temp_avg:   {row.get('temp_avg')}F")
                print(f"    hum_avg:    {row.get('hum_avg')}%")
        except Exception as e:
            print(f"    {YELLOW}aggregate error: {e}{RESET}")
        print()
        participant_try("""
    In Redis Insight:
      FT.SEARCH idx:state "*" LIMIT 0 5
      FT.AGGREGATE idx:state "*" GROUPBY 0
        REDUCE AVG 1 @temp AS temp_avg
        REDUCE COUNT 0 AS n
        """)

    # ── STEP 5: COMMANDS — REMOTE FUNCTION CALLS ──
    elif step == 5:
        banner("COMMANDS — REMOTE FUNCTION CALLS", step, total)
        ascii_art("""
    You ──XADD──▶ stream:commands ──XREADGROUP──▶ Pico
                   {target, fn, args}              │
                                                   ├── fn_start ──▶ stream:events
                                                   │   (executing...)
                                                   └── fn_end   ──▶ stream:events
                                                       {status, result, timing}
        """)
        explain("""
    Commands are just stream entries with {target, fn, args}.
    The Pico tails via a consumer group, dispatches through
    functions.lookup(), and emits lifecycle events.
        """)
        print(f"  {BOLD}Sending: blink 3 times to {unit_id}{RESET}\n")
        payload = {
            "target": unit_id,
            "fn": "blink",
            "args": json.dumps({"times": "3", "ms": "200"}),
            "sender": socket.gethostname(),
        }
        sid = r.xadd("stream:commands", payload)
        print(f"    sent: {sid}")
        print(f"    waiting for Pico...", end="", flush=True)
        # Wait for fn_end
        last_id = "$"
        deadline = time.time() + 10
        while time.time() < deadline:
            msgs = r.xread({"stream:events": last_id}, block=1000)
            if not msgs:
                continue
            for _, entries in msgs:
                for eid, fields in entries:
                    last_id = eid
                    if fields.get("stream_id") == sid and fields.get("kind") == "fn_end":
                        print(f" done!")
                        print(f"    result: {fields.get('result')}")
                        print(f"    status: {fields.get('status')}")
                        started = int(fields.get("started_ms", 0))
                        finished = int(fields.get("finished_ms", 0))
                        if started and finished:
                            print(f"    exec:   {finished - started} ms on Pico")
                        deadline = 0  # break outer
                        break
        print()
        participant_try("""
    uv run send_command.py --target <your-unit> --fn blink --arg times=5
    uv run send_command.py --target <your-unit> --fn say --arg message="hello"
        """)

    # ── STEP 6: VECTOR SEARCH — NL DISPATCH ──
    elif step == 6:
        banner("VECTOR SEARCH — NATURAL LANGUAGE", step, total)
        ascii_art("""
    fn:led_on    ─┐   embedding[300]
    fn:led_off   ─┤   from redis/langcache-embed-v3-small
    fn:blink     ─┼──▶ idx:functions ──▶ FT.SEARCH KNN
    fn:say       ─┤                      │
    fn:create_fn ─┘                      ▼
                                    "blink the light"
                        ┌───────────────────────────┐
        user query ──▶  │  embed ──▶ KNN search     │
                        │  resolve target            │
                        │  XADD stream:commands      │
                        │  wait for Pico response    │
                        └───────────────────────────┘
        """)
        explain("""
    Each function has a description embedded as a 300-dim vector
    (redis/langcache-embed-v3-small from HuggingFace). Your natural
    language query gets embedded the same way, then KNN finds
    the closest function. No LLM needed — just vector math.
        """)
        # Show registered functions
        fn_keys = sorted([k for k in r.keys("fn:*")])
        print(f"  {BOLD}Registered functions:{RESET}\n")
        for k in fn_keys:
            val = r.execute_command("JSON.GET", k, "$.name", "$.description")
            doc = json.loads(val)
            name = doc.get("$.name", [k])[0] if isinstance(doc, dict) else k
            desc_list = doc.get("$.description", [""]) if isinstance(doc, dict) else [""]
            desc = desc_list[0] if desc_list else ""
            print(f"    {YELLOW}{name:<18}{RESET} {desc[:55]}")
        print()
        participant_try("""
    uv run send_nl.py
    Then type: "flash the led on unit 1"
               "blink 5 times"
               "show hello on the screen"
        """)

    # ── STEP 7: YOUR OWN CLIENT ──
    elif step == 7:
        banner("BUILD YOUR OWN — PARTICIPANT CLIENT LOGIC", step, total)
        ascii_art("""
    ┌────────────────────────────────────────────────────┐
    │  YOUR participant.py                               │
    │                                                    │
    │  def transform(sample):                            │
    │      # Add YOUR fields to the JSON doc             │
    │      return {                                      │
    │          ...sample fields...,                       │
    │          "location": "window desk",                │
    │          "alert": temp > 80,                       │
    │          "notes": "near the AC vent",              │
    │      }                                             │
    │                                                    │
    │  # YOUR custom queries:                            │
    │  FT.SEARCH idx:state "@location:{window*}"         │
    │  FT.SEARCH idx:state "@temp:[80 +inf]"             │
    └────────────────────────────────────────────────────┘
        """)
        explain("""
    participant.py is YOUR projector — you decide what fields
    go into your unit's JSON doc. Add location, notes, alerts,
    derived metrics... anything. The search index picks them
    up automatically.

    You can also create NEW functions at runtime:
        """)
        print(f"  {BOLD}Dynamic function registration:{RESET}\n")
        print(f'    uv run send_command.py --target {unit_id} \\')
        print(f'      --fn create_function \\')
        print(f'      --arg name=flash_fast \\')
        print(f'      --arg \'code=async def handler(args):')
        print(f'        _led.on(); await asyncio.sleep_ms(50)')
        print(f'        _led.off(); return "flash"\'')
        print()
        participant_try("""
    1. Edit participant.py — add your own fields to the doc
    2. Create a dynamic function on your Pico
    3. Use send_nl.py to call it with natural language
        """)

    return True


def main() -> None:
    p = argparse.ArgumentParser(description="Workshop presentation flow.")
    p.add_argument("--unit", default="pico-unit-1")
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
    p.add_argument("--start", type=int, default=1,
                   help="Start at step N (default: 1)")
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
        sys.exit(1)

    total_steps = 7
    print(f"\n{BOLD}{'='*62}")
    print(f"  PICO + REDIS WORKSHOP")
    print(f"  {total_steps} steps — live demos with {args.unit}")
    print(f"{'='*62}{RESET}")

    for step in range(args.start, total_steps + 1):
        run_step(r, args.unit, step, total_steps)
        if step < total_steps:
            resp = wait_enter()
            if resp == "skip":
                continue

    print(f"\n{BOLD}{'='*62}")
    print(f"  WORKSHOP COMPLETE")
    print(f"{'='*62}{RESET}\n")
    print(f"  Recap of what you built:")
    print(f"    1. Pico streams sensor data to Redis")
    print(f"    2. Your projector writes nested JSON docs")
    print(f"    3. RediSearch indexes for fleet queries")
    print(f"    4. Remote commands via stream:commands")
    print(f"    5. Vector search for natural-language dispatch")
    print(f"    6. Your own custom fields and functions")
    print(f"\n  Keep your Pico running — it's your IoT device now!\n")


if __name__ == "__main__":
    main()
