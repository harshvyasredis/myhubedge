# Makefile — workshop orchestrator
#
# This file is the front door for first-time setup, daily run loops, and
# diagnostics. The educational primitives the workshop teaches —
# XADD, FT.SEARCH, JSON.SET, FT.AGGREGATE — live in command_cheatsheet.md
# and you should still type those by hand. Make wraps only the *plumbing*.
#
# Run `make` (or `make help`) to see what's available.

.DEFAULT_GOAL := help
.PHONY: help setup install warmup bootstrap create-index register-functions \
        ping status cli redis-url projector nl workshop reset-indexes clean

# Override on the command line:  make projector UNIT=pico-unit-2
UNIT ?= pico-unit-1

UV  := uv
RUN := $(UV) run

##@ Setup

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "Usage: make <target>\n"} \
	      /^##@/ {printf "\n\033[1m%s\033[0m\n", substr($$0, 5)} \
	      /^[a-zA-Z_-]+:.*?##/ {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' \
	      $(MAKEFILE_LIST)
	@printf "\nVariables (override on the command line):\n"
	@printf "  \033[36m%-20s\033[0m %s\n" "UNIT" "Pico unit id (default: pico-unit-1)"

setup: install warmup bootstrap ## Full first-run bootstrap (deps + model + indexes + fns)
	@echo ""
	@echo "Setup complete. Suggested next steps:"
	@echo "  make ping       — verify Redis connectivity"
	@echo "  make status     — see what landed in your DB"
	@echo "  make projector  — start the state-doc projector for $(UNIT)"

install: ## Install Python deps into .venv (uv sync)
	$(UV) sync

warmup: ## Download / verify the embedding model (~44 MB, one-time)
	$(RUN) warmup.py

##@ Database bootstrap

bootstrap: create-index register-functions ## Build idx:state + idx:functions and seed fn:* docs

create-index: ## Build idx:state and idx:functions
	$(RUN) create_index.py

register-functions: ## Embed function phrases and write fn:<name> JSON docs
	$(RUN) register_functions.py

##@ Inspect

ping: ## Verify Redis connectivity (PING + server version)
	@$(RUN) python -c "$$PING_PROG"

status: ## Show DB inventory: keys, streams, indexes, fn:*, state:*
	@$(RUN) python -c "$$STATUS_PROG"

cli: ## Open an interactive redis-cli prompt connected to the workshop DB
	@set -a; . ./.env; set +a; \
	  redis-cli -h "$$PICO_REDIS_HOST" -p "$$PICO_REDIS_PORT" \
	            -a "$$PICO_REDIS_PASSWORD" --no-auth-warning

redis-url: ## Print an `export REDIS_URL=…` line you can eval into your shell
	@set -a; . ./.env; set +a; \
	  echo "export REDIS_URL=\"redis://$$PICO_REDIS_USER:$$PICO_REDIS_PASSWORD@$$PICO_REDIS_HOST:$$PICO_REDIS_PORT\""

##@ Run

projector: ## Run participant.py for UNIT (override: make projector UNIT=pico-unit-2)
	$(RUN) participant.py $(UNIT)

nl: ## Start the natural-language command client
	$(RUN) send_nl.py

workshop: ## Run the guided 7-step walkthrough
	$(RUN) workshop_flow.py --unit $(UNIT)

##@ Maintenance

reset-indexes: ## Drop + recreate idx:state and idx:functions (NEVER on shared Redis)
	@printf "This will DROPINDEX idx:state + idx:functions on $$PICO_REDIS_HOST\n"
	@read -p "Continue? [y/N] " ans && [ "$$ans" = "y" ] || (echo "aborted"; exit 1)
	$(RUN) create_index.py --recreate

clean: ## Remove .venv, hf_cache, *.egg-info, __pycache__
	rm -rf .venv hf_cache *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +

# ── Inline Python programs invoked by `ping` and `status`. ──
# Kept here so the Makefile is the single artifact; no extra ops scripts.

define PING_PROG
import os, sys
sys.path.insert(0, ".")
import participant   # side-effect: loads .env into os.environ
import redis
r = redis.Redis(host=os.environ["PICO_REDIS_HOST"],
                port=int(os.environ["PICO_REDIS_PORT"]),
                username=os.environ["PICO_REDIS_USER"],
                password=os.environ["PICO_REDIS_PASSWORD"],
                decode_responses=True, socket_connect_timeout=5)
print(f"host    {os.environ['PICO_REDIS_HOST']}:{os.environ['PICO_REDIS_PORT']}")
print(f"PING    {r.ping()}")
print(f"server  Redis {r.info('server')['redis_version']}")
endef
export PING_PROG

define STATUS_PROG
import os, sys
sys.path.insert(0, ".")
import participant
import redis
r = redis.Redis(host=os.environ["PICO_REDIS_HOST"],
                port=int(os.environ["PICO_REDIS_PORT"]),
                username=os.environ["PICO_REDIS_USER"],
                password=os.environ["PICO_REDIS_PASSWORD"],
                decode_responses=True, socket_connect_timeout=5)

print(f"DBSIZE  {r.dbsize()}")
print()

print("Streams")
for s in ("stream:readings", "stream:commands", "stream:events"):
    try:
        info = r.xinfo_stream(s)
        print(f"  {s:<18} {info['length']:>6} entries")
    except redis.ResponseError:
        print(f"  {s:<18} (does not exist)")

print()
print("Indexes")
indexes = r.execute_command("FT._LIST") or []
if not indexes:
    print("  (none)")
else:
    for idx in indexes:
        info_list = r.execute_command("FT.INFO", idx)
        info = dict(zip(info_list[0::2], info_list[1::2]))
        print(f"  {idx:<18} num_docs={info.get('num_docs', '?')}")

print()
fn_keys = sorted(r.scan_iter(match="fn:*"))
print(f"fn:* docs   ({len(fn_keys)})")
for k in fn_keys[:8]:
    print(f"  {k}")

print()
state_keys = sorted(r.scan_iter(match="state:*"))
print(f"state:* docs ({len(state_keys)})")
for k in state_keys[:8]:
    print(f"  {k}")
endef
export STATUS_PROG
