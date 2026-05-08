# Pico + Redis Workshop Client

Talk to a live Raspberry Pi Pico fleet through Redis Cloud using natural language.

## Quick Start

This project uses [`uv`](https://docs.astral.sh/uv/) for dependency management. Install once:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# or, if you have pip:  pip install uv
```

Then:

```bash
# 1. Clone and enter the directory
git clone <this-repo>
cd myhubedge

# 2. Install dependencies into a managed .venv (creates uv.lock-pinned env)
uv sync

# 3. Set up your Redis credentials.
#    Copy the template and fill in PICO_REDIS_HOST / PORT / USER / PASSWORD.
#    `.env` is gitignored — never commit it.
cp .env.example .env
$EDITOR .env

# 4. Download + cache the embedding model into ./hf_cache/ (~44 MB, one time).
#    Subsequent runs load from cache — no internet needed after this.
#    Re-run anytime to verify the cache is intact.
uv run warmup.py

# 5. Start your projector (keeps your unit's state doc alive)
uv run participant.py pico-unit-1

# 6. In another terminal — natural-language command client
uv run send_nl.py
```

`uv run <script>` automatically activates the project's `.venv` for the duration of the command — no `source .venv/bin/activate` needed. If you prefer the classic flow, `source .venv/bin/activate` still works after `uv sync`.

**Re-entering the project later?** Just `cd` in and `uv run …`. If `pyproject.toml` or `uv.lock` changed, `uv sync` brings the env up to date.

Then type natural language commands:

```
> blink the led on unit 1
> turn on the LED on all units
> say "hello workshop" on unit 1
> flash the light 5 times
```

## Workshop Overview — files in run order

Run the scripts in the order below. The Redis side (indexes + function embeddings) is already provisioned for you — you can jump straight into Phase 1.

### Phase 1 — Run these (in order)

| # | File | What it does | When to run it |
|---|------|--------------|----------------|
| 1 | `participant.py`  | Your projector — tails `stream:readings` + `stream:events` and writes nested JSON to `state:<your-unit>`. Diff-only; marks your unit offline after 3 missed cycles. | **Keep running continuously in a dedicated terminal tab.** Steps 2 and 3 won't show results without it. |
| 2 | `send_command.py` | Direct command sender — XADDs `{target, fn, args}` onto `stream:commands`. | Run on demand from another tab. No natural language; pass the function name + args. |
| 3 | `send_nl.py`      | Interactive REPL — embeds your English query, KNN-finds the closest function, dispatches it. | Run in a second tab while `participant.py` is alive. Shows the timing breakdown for each request. |

### Phase 2 — Optional guided walkthrough

| # | File | What it does | When to run it |
|---|------|--------------|----------------|
| 4 | `workshop_flow.py` | 7-step ASCII-visualized walkthrough mirroring `WORKSHOP.md`. | Optional — handy if you want to follow the same flow on your own laptop. |

### Reference docs (read, don't run)

| File | Read it for |
|------|-------------|
| `README.md` (this file) | Workshop progression with paired Redis Insight + redis-cli templates, plus the Step 3 query exercises |
| `WORKSHOP.md`           | Step-by-step session walkthrough (explain → run → try) |
| `json_doc_creation.md`  | Standalone walkthrough of manually creating + patching your unit's JSON state doc |

### Configuration (edit, don't run)

| File | Purpose |
|------|---------|
| `.env`              | Redis Cloud credentials. Copy `.env.example` to `.env` and fill in `PICO_REDIS_HOST`, `PICO_REDIS_PORT`, `PICO_REDIS_USER`, `PICO_REDIS_PASSWORD`. Gitignored — never commit it. |
| `pyproject.toml` + `uv.lock` | Python deps. Installed via `uv sync` in Quick Start. Run scripts with `uv run <name>.py`. |

### Already set up for you (don't rerun on the shared Redis)

The shared Redis already has its indexes and function embeddings live, so you can skip these. They're included for transparency — and so you can replay them against your team's own deployment, whether that's **Redis Cloud Enterprise** or **Redis Enterprise Software** running in your environment.

| File | What it does |
|------|--------------|
| `create_index.py`      | Builds `idx:state` + `idx:functions`. Safe to re-run with `--recreate` against a Redis Cloud Enterprise database or Redis Enterprise Software cluster you administer. |
| `register_functions.py`| Embeds 5 phrases per function with `redis/langcache-embed-v3-small` (300 dims) → `fn:<name>` docs. |

## Connecting to Redis

Credentials live in `.env` (gitignored). Copy the template once:

```bash
cp .env.example .env
# then edit .env and fill in PICO_REDIS_HOST / PICO_REDIS_PORT / PICO_REDIS_USER / PICO_REDIS_PASSWORD
```

The Python scripts auto-load `.env` from the directory they live in. Pick one client:

### Redis Insight (GUI)

1. Download from https://redis.io/insight/
2. **Add Redis database** → **Connect to a Redis Database** → **Add Database Manually**
3. Fill in:
   - Host: value of `PICO_REDIS_HOST` in `.env`
   - Port: value of `PICO_REDIS_PORT`
   - Username: `default`
   - Password: value of `PICO_REDIS_PASSWORD`
4. Open the database → **Workbench** (or **CLI**) tab — paste commands directly.

### Redis CLI (terminal)

```bash
# Install (macOS)
brew install redis

# Connect using values from .env — substitute <HOST>, <PORT>, <PASS>
redis-cli -h <HOST> -p <PORT> -a '<PASS>' --no-auth-warning

# Or as a single URL (recommended — survives copy/paste better)
redis-cli -u "redis://default:<PASS>@<HOST>:<PORT>"

# One-shot (no interactive shell)
redis-cli -u "redis://default:<PASS>@<HOST>:<PORT>" PING
```

To use the `$REDIS_URL` shorthand throughout this README:

```bash
export REDIS_URL="redis://default:<PASS>@<HOST>:<PORT>"
redis-cli -u "$REDIS_URL" PING       # → PONG
```

> **Quoting note.** Redis Insight expects backslash-escaped JSON in `XADD args` (`"{\"k\":\"v\"}"`). Redis CLI is happier with single-quoted JSON (`'{"k":"v"}'`). The templates below show both.

---

## Workshop Progression

Each step pairs an **Insight** template (Workbench/CLI panel) with a **redis-cli** template (terminal). Run them in order — each builds on the previous.

### Step 1 — Streams: the Pico is already talking

Every Pico XADDs to a shared append-only log. Newest at the bottom.

**Insight**
```
XREVRANGE stream:readings + - COUNT 3
XLEN stream:readings
```

**redis-cli**
```bash
redis-cli -u "$REDIS_URL" XREVRANGE stream:readings + - COUNT 3
redis-cli -u "$REDIS_URL" XLEN stream:readings
```

Look for: `device_id`, `temperature` (°F), `humidity`, `timestamp`.

---

### Step 2 — Projector: stream → state doc

`participant.py` is **your app** — a Redis consumer that filters `stream:readings` for your unit and writes a nested JSON document at `state:<unit_id>`. It's the only writer of that doc; the Pico itself never touches JSON. Diff-only — duplicate readings are skipped.

**Terminal (run continuously)**
```bash
uv run participant.py --unit pico-unit-1
```

The consumer is idempotent: if `state:<unit>` already exists, it's restored from Redis; otherwise it's seeded fresh. So you don't need to create the doc by hand — just running the script is enough. (If you'd like to see the raw `JSON.SET` your app issues, the standalone walkthrough is in [`json_doc_creation.md`](./json_doc_creation.md).)

#### The schema your app writes

Pay attention to the **types** here — every field in this shape becomes something you can query in Step 3.

```json
{
  "unit_id":            "pico-unit-1",         // TAG    — exact match
  "streaming":          true,                  // TAG    — true | false
  "stream_interval_ms": 10000,                 // number
  "current": {
    "temp":     71.2,                          // NUMERIC, SORTABLE — °F
    "humidity": 43.1,                          // NUMERIC, SORTABLE — %
    "ts_ms":    1761003010123                  // number — event time
  },
  "prior":   { "temp": 71.0, "humidity": 43.0, "ts_ms": 1761003000098 },
  "event_pending":  false,                     // TAG
  "pending_code":   null,                      // TAG    — fn name while running
  "last_event_id":  "1761003008912-0",
  "last_update_ms": 1761003010123,             // NUMERIC, SORTABLE
  "history": [
    { "started_ms": 1761003008900, "finished_ms": 1761003008912,
      "name": "blink", "status": "ok", "error": null }
  ]
}
```

The fields marked `TAG` / `NUMERIC` are indexed by `idx:state` — those are the ones you'll target in Step 3 queries.

#### Patch a field by hand

The doc isn't read-only. RedisJSON lets you mutate any nested path. These three are the same kinds of writes `participant.py` issues automatically — running them yourself helps cement the model.

**Insight**
```
JSON.SET   state:pico-unit-1 $.current '{"temp":72.4,"humidity":41.0,"ts_ms":1761003020000}'
JSON.SET   state:pico-unit-1 $.last_update_ms 1761003020000
JSON.ARRAPPEND state:pico-unit-1 $.history '{"started_ms":0,"finished_ms":0,"name":"manual","status":"ok","error":null}'
JSON.GET   state:pico-unit-1 $
```

**redis-cli**
```bash
redis-cli -u "$REDIS_URL" JSON.SET state:pico-unit-1 '$.current' \
  '{"temp":72.4,"humidity":41.0,"ts_ms":1761003020000}'
redis-cli -u "$REDIS_URL" JSON.SET state:pico-unit-1 '$.last_update_ms' 1761003020000
redis-cli -u "$REDIS_URL" JSON.ARRAPPEND state:pico-unit-1 '$.history' \
  '{"started_ms":0,"finished_ms":0,"name":"manual","status":"ok","error":null}'
redis-cli -u "$REDIS_URL" JSON.GET state:pico-unit-1 $
```

Your manual values stick until the next real sensor reading lands (then `_apply_reading()` overwrites `current` and rotates `prior`).

#### Where this lives in `participant.py`

The consumer follows the same three-step shape on every loop tick: **build → mutate → write**.

**1. Build the seed** — `participant.py:_seed_doc()` (lines 86–99):

```python
def _seed_doc(unit_id: str, interval_ms: int = 10000) -> dict:
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
```

**2. Mutate on each event** — `_apply_reading()` (lines 102–135) rotates `current` → `prior` and stamps `last_update_ms`, but **only if temp or humidity actually changed**:

```python
changed = (new_temp != old_temp or new_hum != old_hum
           or old_temp is None or was_offline)
if not changed:
    return doc, False    # dismiss duplicate — no JSON.SET issued
...
doc["prior"] = dict(doc["current"])
doc["current"] = {"temp": new_temp, "humidity": new_hum, "ts_ms": ts_ms}
doc["last_update_ms"] = ts_ms
```

`_apply_event()` (lines 138–170) does the equivalent for command lifecycle events — flipping `event_pending` and appending to `history` (capped at 20).

**3. Write through to Redis** — `write_doc()` (lines 173–177). One `JSON.SET` on the root path, identical to the Insight command you ran by hand:

```python
def write_doc(r: redis.Redis, unit_id: str, doc: dict) -> str:
    key = state_key(unit_id)                          # "state:pico-unit-1"
    payload = json.dumps(doc)
    r.execute_command("JSON.SET", key, "$", payload)  # whole-doc replace
    return "set"
```

Terminal output while it's running:

```
1777049642609-0  changed  state:pico-unit-1 — temp=72.4 hum=41.0
1777049652341-0  skip     (no diff)
1777049662100-0  changed  state:pico-unit-1 — temp=73.1 hum=41.0
```

Each `changed` line is one `JSON.SET`. Each `skip` is a stream message that the consumer **read but deliberately did not project** — that's the diff-only contract.

---

### Step 3 — Search index: query the fleet

`idx:state` indexes every `state:<unit>` doc. The fields it knows about (from `create_index.py`):

| Index alias       | JSON path             | Type             |
|-------------------|-----------------------|------------------|
| `unit_id`         | `$.unit_id`           | TAG              |
| `temp`            | `$.current.temp`      | NUMERIC, SORTABLE |
| `humidity`        | `$.current.humidity`  | NUMERIC, SORTABLE |
| `last_update_ms`  | `$.last_update_ms`    | NUMERIC, SORTABLE |
| `streaming`       | `$.streaming`         | TAG              |
| `pending_code`    | `$.pending_code`      | TAG              |
| `event_pending`   | `$.event_pending`     | TAG              |

TAGs use `{value}` syntax. NUMERICs use `[min max]` ranges (`-inf` / `+inf` open the bound).

#### Exercise — query the fleet

**a) Find a specific unit** (TAG match)

Insight
```
FT.SEARCH idx:state "@unit_id:{pico-unit-2}" RETURN 3 unit_id temp humidity
```
redis-cli
```bash
redis-cli -u "$REDIS_URL" FT.SEARCH idx:state '@unit_id:{pico-unit-2}' \
  RETURN 3 unit_id temp humidity
```

**b) Find every unit below 70 °F** (numeric range, upper-bounded)

Insight
```
FT.SEARCH idx:state "@temp:[-inf 70]" RETURN 2 unit_id temp SORTBY temp ASC
```
redis-cli
```bash
redis-cli -u "$REDIS_URL" FT.SEARCH idx:state '@temp:[-inf 70]' \
  RETURN 2 unit_id temp SORTBY temp ASC
```

**c) Find every unit above 80 °F** (numeric range, lower-bounded)

Insight
```
FT.SEARCH idx:state "@temp:[80 +inf]" RETURN 2 unit_id temp SORTBY temp DESC
```
redis-cli
```bash
redis-cli -u "$REDIS_URL" FT.SEARCH idx:state '@temp:[80 +inf]' \
  RETURN 2 unit_id temp SORTBY temp DESC
```

**d) Find online units in a humidity band** (combined predicates — AND)

Insight
```
FT.SEARCH idx:state "@streaming:{true} @humidity:[40 60]" RETURN 3 unit_id temp humidity
```
redis-cli
```bash
redis-cli -u "$REDIS_URL" FT.SEARCH idx:state \
  '@streaming:{true} @humidity:[40 60]' RETURN 3 unit_id temp humidity
```

**e) Find every offline unit** (TAG negation by value)

Insight
```
FT.SEARCH idx:state "@streaming:{false}" RETURN 2 unit_id last_update_ms
```
redis-cli
```bash
redis-cli -u "$REDIS_URL" FT.SEARCH idx:state '@streaming:{false}' \
  RETURN 2 unit_id last_update_ms
```

**f) Aggregate the fleet** (server-side averages + count)

Insight
```
FT.AGGREGATE idx:state "@streaming:{true}" GROUPBY 0
  REDUCE AVG 1 @temp AS temp_avg
  REDUCE AVG 1 @humidity AS hum_avg
  REDUCE COUNT 0 AS online
  REDUCE MAX 1 @last_update_ms AS newest_ms
```
redis-cli
```bash
redis-cli -u "$REDIS_URL" FT.AGGREGATE idx:state '@streaming:{true}' \
  GROUPBY 0 \
  REDUCE AVG 1 @temp AS temp_avg \
  REDUCE AVG 1 @humidity AS hum_avg \
  REDUCE COUNT 0 AS online \
  REDUCE MAX 1 @last_update_ms AS newest_ms
```

**g) Stretch — find all "outdoor" units** (extend the schema)

`location` isn't indexed by default, so this exercise is two parts: tag your doc, then teach the index about the new field.

Insight
```
JSON.SET state:pico-unit-1 $.location '"outdoor"'
FT.ALTER idx:state SCHEMA ADD $.location AS location TAG
FT.SEARCH idx:state "@location:{outdoor}" RETURN 3 unit_id location temp
```
redis-cli
```bash
redis-cli -u "$REDIS_URL" JSON.SET state:pico-unit-1 '$.location' '"outdoor"'
redis-cli -u "$REDIS_URL" FT.ALTER idx:state SCHEMA ADD '$.location' \
  AS location TAG
redis-cli -u "$REDIS_URL" FT.SEARCH idx:state '@location:{outdoor}' \
  RETURN 3 unit_id location temp
```

> Why `'"outdoor"'`? `JSON.SET` expects valid JSON — an unquoted `outdoor` is invalid; the inner quotes make it a JSON string. The outer single quotes are shell escaping.

To make this stick across reboots, also add a `location` field to `_seed_doc()` in `participant.py` so your consumer regenerates it every run.

On the Pico: KEY0 wakes the OLED, then KEY1 runs the same fleet aggregate (query **f**).

---

### Step 4 — Commands: talk back to the Pico

Commands are stream entries with `{target, fn, args}`. The Pico tails via a consumer group, dispatches to the function registry, and emits `fn_start`/`fn_end` events.

**Insight**
```
XADD stream:commands * target pico-unit-1 fn led_on args "{}"
XADD stream:commands * target pico-unit-1 fn blink args "{\"times\":\"5\",\"ms\":\"200\"}"
XADD stream:commands * target pico-unit-1 fn say args "{\"message\":\"hello workshop\",\"secs\":\"8\"}"
XADD stream:commands * target pico-unit-1 fn led_off args "{}"

XREVRANGE stream:events + - COUNT 4
JSON.GET state:pico-unit-1 $.history
```

**redis-cli**
```bash
redis-cli -u "$REDIS_URL" XADD stream:commands '*' target pico-unit-1 fn led_on args '{}'
redis-cli -u "$REDIS_URL" XADD stream:commands '*' target pico-unit-1 fn blink \
  args '{"times":"5","ms":"200"}'
redis-cli -u "$REDIS_URL" XADD stream:commands '*' target pico-unit-1 fn say \
  args '{"message":"hello workshop","secs":"8"}'
redis-cli -u "$REDIS_URL" XADD stream:commands '*' target pico-unit-1 fn led_off args '{}'

redis-cli -u "$REDIS_URL" XREVRANGE stream:events + - COUNT 4
redis-cli -u "$REDIS_URL" JSON.GET state:pico-unit-1 $.history
```

**Python alternative**
```bash
uv run send_command.py --target pico-unit-1 --fn blink --arg times=5 --arg ms=200
```

---

### Step 5 — Vector search: functions as embeddings

Each function has 5 natural-language phrases embedded with `redis/langcache-embed-v3-small` (300 dims), normalized-averaged into one searchable vector.

**Insight**
```
FT.SEARCH idx:functions "*" RETURN 2 name description
JSON.GET fn:blink $.name $.description $.phrases
FT.INFO idx:functions
```

**redis-cli**
```bash
redis-cli -u "$REDIS_URL" FT.SEARCH idx:functions '*' RETURN 2 name description
redis-cli -u "$REDIS_URL" JSON.GET fn:blink '$.name' '$.description' '$.phrases'
redis-cli -u "$REDIS_URL" FT.INFO idx:functions
```

---

### Step 6 — NL dispatch: plain English to Pico

Your query gets embedded with the same model, KNN finds the closest function, target is parsed from the text, command dispatched. No LLM — just vector math.

**Terminal**
```bash
uv run send_nl.py
```

Try:
```
> blink the led on unit 1
> turn off the light
> say "hello everyone" on all units
> flash the led 5 times
```

Watch the timing breakdown (`embed query` / `redis KNN search` / `dispatch XADD` / `pico round-trip`).

---

### Step 7 — Make it yours

Register a brand-new function at runtime:

**Insight**
```
XADD stream:commands * target pico-unit-1 fn create_function args "{\"name\":\"flash_fast\",\"code\":\"async def handler(args):\\n  _led.on(); await asyncio.sleep_ms(50); _led.off(); return 'flash'\"}"
XADD stream:commands * target pico-unit-1 fn flash_fast args "{}"
```

**redis-cli**
```bash
redis-cli -u "$REDIS_URL" XADD stream:commands '*' target pico-unit-1 \
  fn create_function \
  args '{"name":"flash_fast","code":"async def handler(args):\n  _led.on(); await asyncio.sleep_ms(50); _led.off(); return '\''flash'\''"}'

redis-cli -u "$REDIS_URL" XADD stream:commands '*' target pico-unit-1 fn flash_fast args '{}'
```

Other ideas:
- Edit `participant.py` — add custom fields (`location`, `notes`, `alert: temp > 80`) to your state doc
- Write your own `FT.AGGREGATE` against the fleet
- Combine `FT.SEARCH idx:state "@temp:[80 +inf]"` with a command dispatch to alert hot units

---

## Resetting on Redis Cloud Enterprise / Redis Enterprise Software

If you replay this stack on a **Redis Cloud Enterprise** database or a **Redis Enterprise Software** cluster you administer and want a clean slate:

```bash
uv run create_index.py --recreate    # rebuilds idx:state + idx:functions, leaves JSON docs
uv run register_functions.py         # overwrites fn:* with fresh embeddings
```

Don't run these against the shared workshop Redis — you'll wipe everyone's `fn:*` docs while the embedder warms up.

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
