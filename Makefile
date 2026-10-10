PY ?= .venv/bin/python
DASH = services/dashboard

.PHONY: init seed demo replay dev dev-owner dev-code test typecheck build reset-db reset-kill-switch broker models api dashboard

init:            ## create venv, install Python + Node deps, copy example config
	test -d .venv || python3 -m venv .venv
	$(PY) -m pip install -q -r requirements.txt
	cd $(DASH) && npm install --no-audit --no-fund
	test -f config/risk.yaml || cp config/risk.example.yaml config/risk.yaml
	test -f .env || (cp .env.example .env && echo "SESSION_SECRET=$$($(PY) -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env)

seed:            ## simulated market history + recorded signals (offline model)
	$(PY) -m services.broker.seed
	$(PY) -m services.models.backfill

demo: seed dev-owner   ## seed, create a dev owner, backtest + paper the example strategies
	$(PY) scripts/demo_setup.py

replay:          ## rebuild equity history by replaying the running strategies over stored sessions
	$(PY) -m services.broker.replay

dev:             ## run broker, models, api and the Next.js dashboard
	$(PY) scripts/dev.py

dev-owner:
	$(PY) scripts/dev_owner.py

dev-code:        ## print the dev owner's current 2FA code
	@$(PY) scripts/dev_owner.py --code

broker:
	$(PY) -m services.broker
models:
	$(PY) -m services.models
api:
	$(PY) -m services.api
dashboard:
	cd $(DASH) && npm run dev

test:
	$(PY) -m pytest -q tests

typecheck:
	cd $(DASH) && npx tsc --noEmit

build:
	cd $(DASH) && npm run build

reset-db:        ## drop all tables (asks first)
	@read -p "Drop every table in $$DATABASE_URL? [y/N] " a && [ "$$a" = y ] && $(PY) -c "import scripts.env as e; e.load(); from common import db; db.reset(db.get()); print('dropped')"

reset-kill-switch:   ## host-only
	$(PY) scripts/reset_kill_switch.py

paper-setup:     ## real Alpaca history + isolated paper DB + initial owner
	$(PY) scripts/paper_setup.py

paper: build     ## actual Alpaca paper stack (separate from make dev)
	$(PY) scripts/paper.py

backup:
	$(PY) scripts/backup.py

broker-guard:
	$(PY) -m scripts.broker_guard
