# Workshop Flow

Run each step in order. Explain the concept, run the demo, let participants try.

---

## Step 1 — Streams: The Pico is already talking

**Explain:** Every Pico appends sensor readings to a shared Redis stream.
The stream is an append-only log — every message stays, newest at the bottom.

**Redis Insight:**
```
XREVRANGE stream:readings + - COUNT 3
```

**Point out:** `device_id`, `temperature` (always F), `humidity`, `timestamp`.
The stream ID itself is the Redis server timestamp in ms.

---

## Step 2 — Your projector: Turn the stream into state

**Explain:** The stream is raw. Each participant runs a projector that
filters for their unit and writes a nested JSON doc — their view of
their unit's state. Only diffs trigger a write.

**Terminal (each participant):**
```bash
python participant.py --unit pico-unit-1
```

**Output to watch for:**
```
1777049642609-0  changed  state:pico-unit-1 — temp=100.9 hum=38.0
1777049652341-0  skip     (no diff)
1777049662100-0  changed  state:pico-unit-1 — temp=101.7 hum=38.0
```

**Redis Insight — see the doc:**
```
JSON.GET state:pico-unit-1 $
```

**Point out:** `current`/`prior` rotation, `streaming: true`, `history: []` (empty — no commands yet).

---

## Step 3 — Search index: Query the fleet

**Explain:** RediSearch indexes the JSON docs automatically. One query
aggregates across all units — Redis does the math server-side.

**Redis Insight:**
```
FT.SEARCH idx:state "@streaming:{true}" RETURN 3 unit_id temp humidity

FT.AGGREGATE idx:state "@streaming:{true}" GROUPBY 0
  REDUCE AVG 1 @temp AS temp_avg
  REDUCE AVG 1 @humidity AS hum_avg
  REDUCE COUNT 0 AS online
```

**On the Pico:** Press KEY0 (wake OLED), then KEY1 (fleet query).
Shows `on: N`, `off: N`, averages, `updated:`.

---

## Step 4 — Commands: Talk back to the Pico

**Explain:** Commands are just stream entries with `{target, fn, args}`.
The Pico tails via a consumer group and dispatches to a function registry.
Every execution emits `fn_start`/`fn_end` events.

**Redis Insight — turn on the LED:**
```
XADD stream:commands * target pico-unit-1 fn led_on args "{}"
```

**Watch it execute:**
```
XREVRANGE stream:events + - COUNT 4
```

**Redis Insight — blink:**
```
XADD stream:commands * target pico-unit-1 fn blink args "{\"times\":\"5\",\"ms\":\"200\"}"
```

**Redis Insight — show a message on screen:**
```
XADD stream:commands * target pico-unit-1 fn say args "{\"message\":\"hello from Redis Insight\",\"secs\":\"8\"}"
```

**Redis Insight — turn off:**
```
XADD stream:commands * target pico-unit-1 fn led_off args "{}"
```

**Then check the state doc updated with history:**
```
JSON.GET state:pico-unit-1 $.history
```

**CLI alternative:**
```bash
python send_command.py --target pico-unit-1 --fn blink --arg times=3
python send_command.py --target pico-unit-1 --fn say --arg message="hi" --arg secs=5
```

---

## Step 5 — Vector search: Functions as embeddings

**Explain:** Each function has 5 natural-language phrases describing it.
All 5 are embedded with `redis/langcache-embed-v3-small` (300 dims),
averaged into one searchable vector, and stored in the JSON doc.
RediSearch indexes the vectors for KNN cosine search.

**Show what's registered:**
```
FT.SEARCH idx:functions "*" RETURN 2 name description
```

**Show the full doc with phrases and code:**
```
JSON.GET fn:blink $.name $.description $.phrases $.code
```

**Run the registration (facilitator, already done):**
```bash
python register_functions.py
```

---

## Step 6 — NL dispatch: Plain English to Pico

**Explain:** Your query gets embedded with the same model, KNN finds
the closest function, target is parsed from the text, command dispatched,
Pico executes, response comes back. No LLM — just vector math.

**Terminal (each participant):**
```bash
python send_nl.py
```

**Try these:**
```
> blink the led on unit 1
> turn off the light
> send a message to unit 1
> say "hello everyone" on all units
> flash the led 5 times
```

**Point out the timing output:**
```
  embed query:      86 ms
  redis KNN search: 47 ms
  dispatch XADD:    44 ms
  pico round-trip: 145 ms
  total:           328 ms
```

---

## Step 7 — Make it yours

**Ideas for participants:**

- Edit `participant.py` — add custom fields to your JSON doc
  (`location`, `notes`, `alert: temp > 80`)
- Create a dynamic function at runtime:
  ```bash
  python send_command.py --target pico-unit-1 --fn create_function \
    --arg name=flash_fast \
    --arg 'code=async def handler(args):
      _led.on(); await asyncio.sleep_ms(50)
      _led.off(); return "flash"'
  ```
  Then call it: `python send_command.py --target pico-unit-1 --fn flash_fast`
- Write your own FT.AGGREGATE queries against the fleet
- Try `FT.SEARCH idx:state "@temp:[90 +inf]"` to find hot units

---

## Recap

```
stream:readings  ─── append-only sensor log (Pico writes)
stream:commands  ─── function calls (you write)
stream:events    ─── execution lifecycle (Pico writes)
state:<unit>     ─── nested JSON projection (participant.py writes on diff)
fn:<name>        ─── function docs + embeddings (register_functions.py writes)
idx:state        ─── RediSearch over state docs
idx:functions    ─── vector KNN over function docs
```

Every piece is a Redis primitive. The Pico is a producer/consumer.
Your laptop is the projector + query layer. Redis is the backbone.
