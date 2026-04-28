# Command Cheat Sheet

Indexed by function. Each row shows both **Redis Insight** (paste into Workbench / CLI panel) and **redis-cli** (terminal). Where a Python script is the cleanest form, that's listed too.

The redis-cli examples assume `$REDIS_URL` is set:

```bash
export REDIS_URL="redis://default:***REMOVED***@redis-11720.c74.us-east-1-4.ec2.cloud.redislabs.com:11720"
redis-cli -u "$REDIS_URL" PING       # → PONG
```

---

## 0. Connect to the workshop's Redis Cloud Enterprise database

### Redis Insight (GUI)

1. Open Redis Insight → **Add Redis database** → **Connect to a Redis Database** → **Add Database Manually**
2. Fill in:
   - **Host**:     `redis-11720.c74.us-east-1-4.ec2.cloud.redislabs.com`
   - **Port**:     `11720`
   - **Username**: `default`
   - **Password**: `***REMOVED***`
3. Open the database → **Workbench** (or **CLI**) tab → paste commands.

### redis-cli (terminal)

```bash
brew install redis     # macOS, once
redis-cli -u "redis://default:***REMOVED***@redis-11720.c74.us-east-1-4.ec2.cloud.redislabs.com:11720"
```

---

## 1. View the live sensor stream

| Client     | Command |
|------------|---------|
| Insight    | `XREVRANGE stream:readings + - COUNT 10` |
| Insight    | `XLEN stream:readings` |
| redis-cli  | `redis-cli -u "$REDIS_URL" XREVRANGE stream:readings + - COUNT 10` |
| redis-cli  | `redis-cli -u "$REDIS_URL" XLEN stream:readings` |

Look for: `device_id`, `temperature` (°F), `humidity`, `timestamp`.

---

## 2. Find every unit currently online / offline (TAG)

| Client     | Command |
|------------|---------|
| Insight    | `FT.SEARCH idx:state "@streaming:{true}" RETURN 3 unit_id temp humidity` |
| Insight    | `FT.SEARCH idx:state "@streaming:{false}" LIMIT 0 0` |
| redis-cli  | `redis-cli -u "$REDIS_URL" FT.SEARCH idx:state '@streaming:{true}' RETURN 3 unit_id temp humidity` |
| redis-cli  | `redis-cli -u "$REDIS_URL" FT.SEARCH idx:state '@streaming:{false}' LIMIT 0 0` |

`{true}` / `{false}` is the TAG-syntax filter; `LIMIT 0 0` returns just the count.

---

## 3. Find a unit by its number (TAG match on unit_id)

| Client     | Command |
|------------|---------|
| Insight    | `FT.SEARCH idx:state "@unit_id:{pico-unit-2}" RETURN 4 unit_id temp humidity last_update_ms` |
| redis-cli  | `redis-cli -u "$REDIS_URL" FT.SEARCH idx:state '@unit_id:{pico-unit-2}' RETURN 4 unit_id temp humidity last_update_ms` |

For multiple units: `@unit_id:{pico-unit-1|pico-unit-2}`.

---

## 4. Find a unit by location (extended schema)

`location` isn't in the default schema. To use it: tag the doc, then teach the index about the new field once.

| Step | Insight | redis-cli |
|------|---------|-----------|
| Tag the doc | `JSON.SET state:pico-unit-1 $.location '"outdoor"'` | `redis-cli -u "$REDIS_URL" JSON.SET state:pico-unit-1 '$.location' '"outdoor"'` |
| Extend index | `FT.ALTER idx:state SCHEMA ADD $.location AS location TAG` | `redis-cli -u "$REDIS_URL" FT.ALTER idx:state SCHEMA ADD '$.location' AS location TAG` |
| Query | `FT.SEARCH idx:state "@location:{outdoor}" RETURN 3 unit_id location temp` | `redis-cli -u "$REDIS_URL" FT.SEARCH idx:state '@location:{outdoor}' RETURN 3 unit_id location temp` |

To make the field stick across reboots, add `"location"` to `_seed_doc()` in `participant.py`.

---

## 5. Numeric range — find units above/below a temperature

| Client     | Command |
|------------|---------|
| Insight    | `FT.SEARCH idx:state "@temp:[80 +inf]" RETURN 2 unit_id temp SORTBY temp DESC` |
| Insight    | `FT.SEARCH idx:state "@temp:[-inf 70]" RETURN 2 unit_id temp SORTBY temp ASC` |
| redis-cli  | `redis-cli -u "$REDIS_URL" FT.SEARCH idx:state '@temp:[80 +inf]' RETURN 2 unit_id temp SORTBY temp DESC` |
| redis-cli  | `redis-cli -u "$REDIS_URL" FT.SEARCH idx:state '@temp:[-inf 70]' RETURN 2 unit_id temp SORTBY temp ASC` |

Works on `humidity` and `last_update_ms` too. Combine with TAG: `"@streaming:{true} @temp:[80 +inf]"`.

---

## 6. Full-text search over function descriptions

`idx:functions` indexes `$.description` as TEXT — useful when you don't remember the exact function name.

| Client     | Command |
|------------|---------|
| Insight    | `FT.SEARCH idx:functions "@description:blink" RETURN 2 name description` |
| Insight    | `FT.SEARCH idx:functions "@description:(message OR display)" RETURN 2 name description` |
| redis-cli  | `redis-cli -u "$REDIS_URL" FT.SEARCH idx:functions '@description:blink' RETURN 2 name description` |
| redis-cli  | `redis-cli -u "$REDIS_URL" FT.SEARCH idx:functions '@description:(message OR display)' RETURN 2 name description` |

For semantic ("describe what I want") matching, use Step 11 (`send_nl.py`) — that runs vector KNN.

---

## 7. Max / Min / Average across the fleet (AGGREGATE)

### Average (online units)

| Client     | Command |
|------------|---------|
| Insight    | `FT.AGGREGATE idx:state "@streaming:{true}" GROUPBY 0`<br>&nbsp;&nbsp;`REDUCE AVG 1 @temp AS temp_avg`<br>&nbsp;&nbsp;`REDUCE AVG 1 @humidity AS hum_avg`<br>&nbsp;&nbsp;`REDUCE COUNT 0 AS online` |
| redis-cli  | <pre>redis-cli -u "$REDIS_URL" FT.AGGREGATE idx:state '@streaming:{true}' \<br>  GROUPBY 0 \<br>  REDUCE AVG 1 @temp AS temp_avg \<br>  REDUCE AVG 1 @humidity AS hum_avg \<br>  REDUCE COUNT 0 AS online</pre> |

### Max / Min temperature across the fleet

| Client     | Command |
|------------|---------|
| Insight    | `FT.AGGREGATE idx:state "@streaming:{true}" GROUPBY 0 REDUCE MAX 1 @temp AS hottest REDUCE MIN 1 @temp AS coldest` |
| redis-cli  | `redis-cli -u "$REDIS_URL" FT.AGGREGATE idx:state '@streaming:{true}' GROUPBY 0 REDUCE MAX 1 @temp AS hottest REDUCE MIN 1 @temp AS coldest` |

### Hottest unit by name

| Client     | Command |
|------------|---------|
| Insight    | `FT.SEARCH idx:state "@streaming:{true}" RETURN 2 unit_id temp SORTBY temp DESC LIMIT 0 1` |
| redis-cli  | `redis-cli -u "$REDIS_URL" FT.SEARCH idx:state '@streaming:{true}' RETURN 2 unit_id temp SORTBY temp DESC LIMIT 0 1` |

---

## 8. Get the IP / hostname / SSID of a specific unit

`participant.py` projects each unit's network identity into `state:<unit>.network`.

| Client     | Command |
|------------|---------|
| Insight    | `JSON.GET state:pico-unit-2 $.network` |
| Insight    | `JSON.GET state:pico-unit-2 $.network.ip` |
| Insight    | `JSON.GET state:pico-unit-2 $.network.hostname` |
| redis-cli  | `redis-cli -u "$REDIS_URL" JSON.GET state:pico-unit-2 $.network` |
| redis-cli  | `redis-cli -u "$REDIS_URL" JSON.GET state:pico-unit-2 '$.network.ip'` |
| redis-cli  | `redis-cli -u "$REDIS_URL" JSON.GET state:pico-unit-2 '$.network.hostname'` |

For the whole doc: `JSON.GET state:pico-unit-2 $`.

---

## 9. Browse the available functions (vector / text index)

| Client     | Command |
|------------|---------|
| Insight    | `FT.SEARCH idx:functions "*" RETURN 2 name description` |
| Insight    | `JSON.GET fn:blink $.name $.description $.phrases` |
| Insight    | `FT.INFO idx:functions` |
| redis-cli  | `redis-cli -u "$REDIS_URL" FT.SEARCH idx:functions '*' RETURN 2 name description` |
| redis-cli  | `redis-cli -u "$REDIS_URL" JSON.GET fn:blink '$.name' '$.description' '$.phrases'` |

---

## 10. Send a command to a single unit (or "all") via the stream

The Pico tails `stream:commands` via a per-unit consumer group; entries with `target = <unit_id>` or `target = "all"` are dispatched.

### Insight (Workbench escapes JSON with backslashes)

```
XADD stream:commands * target pico-unit-2 fn led_on  args "{}"
XADD stream:commands * target pico-unit-2 fn blink   args "{\"times\":\"5\",\"ms\":\"200\"}"
XADD stream:commands * target pico-unit-2 fn say     args "{\"message\":\"hello\",\"secs\":\"8\"}"
XADD stream:commands * target pico-unit-2 fn led_off args "{}"
XADD stream:commands * target all         fn led_on  args "{}"
```

### redis-cli (single-quote JSON)

```bash
redis-cli -u "$REDIS_URL" XADD stream:commands '*' target pico-unit-2 fn led_on  args '{}'
redis-cli -u "$REDIS_URL" XADD stream:commands '*' target pico-unit-2 fn blink   args '{"times":"5","ms":"200"}'
redis-cli -u "$REDIS_URL" XADD stream:commands '*' target pico-unit-2 fn say     args '{"message":"hello","secs":"8"}'
redis-cli -u "$REDIS_URL" XADD stream:commands '*' target pico-unit-2 fn led_off args '{}'
redis-cli -u "$REDIS_URL" XADD stream:commands '*' target all         fn led_on  args '{}'
```

### Python helper (no JSON quoting headache)

```bash
python send_command.py --target pico-unit-2 --fn led_on
python send_command.py --target pico-unit-2 --fn blink --arg times=5 --arg ms=200
python send_command.py --target all          --fn say --arg message="hello fleet" --arg secs=6
```

### Watch the resulting events

```
XREVRANGE stream:events + - COUNT 5
JSON.GET state:pico-unit-2 $.history
```

```bash
redis-cli -u "$REDIS_URL" XREVRANGE stream:events + - COUNT 5
redis-cli -u "$REDIS_URL" JSON.GET state:pico-unit-2 $.history
```

---

## 11. Natural-language dispatch via `send_nl.py`

Embeds your English prompt with `redis/langcache-embed-v3-small`, runs KNN against `idx:functions`, parses the target unit out of the text, and XADDs to `stream:commands`. Prints timing for each step.

```bash
python send_nl.py
```

Then at the prompt:

```
> blink the led on unit 1
> turn off the LED on all units
> say "welcome workshop" on unit 2
> flash the led 5 times on unit 3
```

One-shot mode:

```bash
python send_nl.py "blink the led on unit 1"
```
