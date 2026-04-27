# Creating the unit state document by hand

This is a side-quest. You **don't have to run any of these commands** — `participant.py` issues the same `JSON.SET` automatically the first time it sees your unit. Walking through it manually just makes the consumer's job concrete: you'll see the exact byte payload your app writes, and you'll be able to query it before any sensor data lands.

If you run these commands first and *then* start `participant.py`, the consumer's `bootstrap()` will detect the existing key, load it, and pick up where you left off. No collision, no duplicate work.

---

## What you're building

Each Pico unit gets one Redis key — `state:<unit_id>` — holding a nested JSON document. The document is:

- **The query surface.** `idx:state` indexes seven of its fields (`unit_id`, `current.temp`, `current.humidity`, `last_update_ms`, `streaming`, `pending_code`, `event_pending`) so `FT.SEARCH` and `FT.AGGREGATE` can hit it.
- **A view, not a log.** Streams hold the raw event log; this doc is the projected current view. One document per unit, overwritten in place.
- **Owned by `participant.py`.** Your consumer is the only writer. The Pico itself never speaks JSON — it only XADDs to streams.

### Schema

```json
{
  "unit_id":            "pico-unit-1",         // TAG    — your unit's id
  "streaming":          true,                  // TAG    — true | false
  "stream_interval_ms": 10000,                 // number — sample cadence
  "current": {
    "temp":     null,                          // NUMERIC — °F (null until first reading)
    "humidity": null,                          // NUMERIC — %
    "ts_ms":    null                           // number  — event-time of the reading
  },
  "prior":           null,                     // last "current" before the most recent change
  "event_pending":   false,                    // TAG    — true while a command is running
  "pending_code":    null,                     // TAG    — fn name while running
  "last_event_id":   null,                     // last stream:events id we processed
  "last_update_ms":  null,                     // NUMERIC — when the doc last changed
  "history":         []                        // recent fn_end entries (capped at 20)
}
```

Comments are illustrative — strip them before sending to Redis (the commands below already do).

---

## Pre-flight

```bash
export REDIS_URL="redis://default:<PASS>@<HOST>:<PORT>"
redis-cli -u "$REDIS_URL" PING                 # → PONG
```

Pick a unit id you've been assigned; the examples use `pico-unit-1`. **Substitute your own** so attendees don't trample each other.

---

## 1. Seed the document

This is the byte-identical payload `participant.py:_seed_doc()` builds and `write_doc()` then sends.

### Redis Insight (Workbench / CLI panel)

```
JSON.SET state:pico-unit-1 $ '{"unit_id":"pico-unit-1","streaming":true,"stream_interval_ms":10000,"current":{"temp":null,"humidity":null,"ts_ms":null},"prior":null,"event_pending":false,"pending_code":null,"last_event_id":null,"last_update_ms":null,"history":[]}'
```

### redis-cli

```bash
redis-cli -u "$REDIS_URL" JSON.SET state:pico-unit-1 '$' '{
  "unit_id":            "pico-unit-1",
  "streaming":          true,
  "stream_interval_ms": 10000,
  "current": {"temp": null, "humidity": null, "ts_ms": null},
  "prior":           null,
  "event_pending":   false,
  "pending_code":    null,
  "last_event_id":   null,
  "last_update_ms":  null,
  "history":         []
}'
```

Both commands return `OK`. The key now exists, indexed by `idx:state`, ready to be queried — but with `null` sensor values until a real reading lands.

---

## 2. Verify

### Redis Insight

```
JSON.GET state:pico-unit-1 $
JSON.GET state:pico-unit-1 $.current
JSON.GET state:pico-unit-1 $.streaming
JSON.TYPE state:pico-unit-1 $.history
```

### redis-cli

```bash
redis-cli -u "$REDIS_URL" JSON.GET state:pico-unit-1 $
redis-cli -u "$REDIS_URL" JSON.GET state:pico-unit-1 $.current
redis-cli -u "$REDIS_URL" JSON.GET state:pico-unit-1 $.streaming
redis-cli -u "$REDIS_URL" JSON.TYPE state:pico-unit-1 $.history
```

`JSON.TYPE` is useful for sanity-checking nested types when something doesn't query the way you expect.

---

## 3. Patch a nested path

`participant.py:_apply_reading()` rotates `current` → `prior` and stamps `last_update_ms` on every change. You can do the same by hand:

### Redis Insight

```
JSON.SET state:pico-unit-1 $.current '{"temp":72.4,"humidity":41.0,"ts_ms":1761003020000}'
JSON.SET state:pico-unit-1 $.last_update_ms 1761003020000
JSON.SET state:pico-unit-1 $.streaming "true"
```

### redis-cli

```bash
redis-cli -u "$REDIS_URL" JSON.SET state:pico-unit-1 '$.current' \
  '{"temp":72.4,"humidity":41.0,"ts_ms":1761003020000}'
redis-cli -u "$REDIS_URL" JSON.SET state:pico-unit-1 '$.last_update_ms' 1761003020000
```

`participant.py:_apply_event()` appends history entries on `fn_end`:

```
JSON.ARRAPPEND state:pico-unit-1 $.history '{"started_ms":0,"finished_ms":0,"name":"manual","status":"ok","error":null}'
```

```bash
redis-cli -u "$REDIS_URL" JSON.ARRAPPEND state:pico-unit-1 '$.history' \
  '{"started_ms":0,"finished_ms":0,"name":"manual","status":"ok","error":null}'
```

`JSON.ARRAPPEND` returns the new array length. Run it twice and you'll see `1`, then `2`.

---

## 4. Hand off to your consumer

```bash
python participant.py --unit pico-unit-1
```

Expected first line:

```
bootstrap: restored existing state:pico-unit-1
```

That's `bootstrap()` reading your hand-built doc back out, recognising the matching `unit_id`, and resuming from there instead of overwriting. From this point on, the consumer is in control: it'll overwrite `$.current` on the next sensor reading and your hand-set values will roll into `$.prior`.

If you want a clean slate, delete the key and let `participant.py` build it from scratch:

```bash
redis-cli -u "$REDIS_URL" DEL state:pico-unit-1
python participant.py --unit pico-unit-1     # → bootstrap: set state:pico-unit-1 — temp=… hum=…
```

---

## Reference: where each command lives in `participant.py`

| Manual command                                | Source line                                 |
|-----------------------------------------------|---------------------------------------------|
| `JSON.SET state:<unit> $ <seed>`              | `_seed_doc()` lines 86–99 → `write_doc()` 173–177 |
| `JSON.SET state:<unit> $.current ...`         | `_apply_reading()` lines 102–135 (whole-doc rewrite) |
| `JSON.SET state:<unit> $.last_update_ms ...`  | `_apply_reading()` / `_apply_event()` (sets `doc["last_update_ms"]`) |
| `JSON.ARRAPPEND state:<unit> $.history ...`   | `_apply_event()` lines 138–170 (Python-side `doc["history"].append(...)`) |
| `JSON.GET state:<unit> $`                     | `bootstrap()` lines 180–211 (existence check before re-seeding) |

Reading those five spots is enough to internalize the whole consumer.
