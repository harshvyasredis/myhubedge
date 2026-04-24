# Pico + Redis Workshop Client

Talk to a live Raspberry Pi Pico fleet through Redis Cloud using natural language.

## Quick Start

```bash
# 1. Clone and install
git clone <this-repo>
cd workshop-client
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Start your projector (keeps your unit's state doc alive)
python participant.py --unit pico-unit-1

# 3. In another terminal — interactive NL command client
python send_nl.py
```

Then type natural language commands:

```
> blink the led on unit 1
> turn on the LED on all units
> say "hello workshop" on unit 1
> flash the light 5 times
```

## What Each File Does

| File | Purpose |
|---|---|
| `send_nl.py` | **Interactive NL client** — embed your query, KNN search Redis for the best function, dispatch to Pico, wait for response. Shows timing at every step. |
| `participant.py` | **Stream projector** — tails `stream:readings` + `stream:events`, writes nested JSON state docs to `state:<unit>`. Marks units offline after 3 missed cycles. |
| `send_command.py` | **Direct command sender** — XADD a function call to `stream:commands` by name (no NL). |
| `register_functions.py` | **Function registry** — embeds 5 phrases per function with `redis/langcache-embed-v3-small` (300 dims), stores in `fn:<name>` JSON docs. Run once by facilitator. |
| `create_index.py` | **Index creator** — builds `idx:state` and `idx:functions` RediSearch indexes. Run once by facilitator. |
| `workshop_flow.py` | **Presentation script** — 7-step guided demo with ASCII visuals. |
| `secrets.py` | Redis Cloud credentials. |

## Redis Insight CLI — Quick Commands

```
# See online units
FT.SEARCH idx:state "@streaming:{true}" RETURN 3 unit_id temp humidity

# Send a command directly
XADD stream:commands * target pico-unit-1 fn led_on args "{}"
XADD stream:commands * target pico-unit-1 fn blink args "{\"times\":\"5\"}"
XADD stream:commands * target pico-unit-1 fn say args "{\"message\":\"hello\"}"

# Watch events
XREVRANGE stream:events + - COUNT 5

# Fleet aggregate
FT.AGGREGATE idx:state "@streaming:{true}" GROUPBY 0
  REDUCE AVG 1 @temp AS temp_avg
  REDUCE AVG 1 @humidity AS hum_avg
  REDUCE COUNT 0 AS online

# See registered functions
FT.SEARCH idx:functions "*" RETURN 2 name description
```

## Architecture

```
Pico 2W (MicroPython)          Redis Cloud             Your Laptop
  sensor_task ──XADD──▶  stream:readings  ◀──XREAD── participant.py
  cmd_task ◀──XREADGROUP── stream:commands ◀──XADD── send_nl.py
  cmd_task ──XADD──▶     stream:events    ◀──XREAD── participant.py
                           state:<unit>    ◀──JSON.SET── participant.py
                           fn:<name>       ◀──JSON.SET── register_functions.py
                           idx:state       (RediSearch)
                           idx:functions   (Vector KNN)
```

## Model

Embedding: `redis/langcache-embed-v3-small` from HuggingFace, truncated to 300 dimensions.
Each function has 5 natural-language phrases embedded. The searchable vector is the normalized average of all phrase embeddings.
