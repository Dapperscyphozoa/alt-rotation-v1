# engine-template

Canonical engine template for the Cyber Psycho federated trading stack.

Forks become independent Render services that coordinate via the
Portfolio Manager (PM) at `https://portfolio-manager-7df2.onrender.com`.

## What's in here

```
engine/
  pm_client.py       — canonical PM contract (DO NOT EDIT in forks)
  hl_data.py         — HL candle fetcher with rate-limit hardening (DO NOT EDIT)
  hl_exchange.py     — HL order placement + cloid hashing (DO NOT EDIT)
  trader.py          — paper/live orchestration, SL/TP/time-stop (DO NOT EDIT)
  persistence.py     — SQLite schema for signals/trades/closures (DO NOT EDIT)
  blacklist.py       — consecutive-loss kill-switch (DO NOT EDIT)
  signal_detector.py — STUB. **THE ONLY FILE FORKS REPLACE.**
  config.py          — generic env-driven config (forks override env vars,
                       not this file)
server.py            — entry: 3 threads (scan/positions/reconcile) + HTTP API
render.yaml          — Render blueprint (forks rename `name`, set HL wallet)
requirements.txt     — numpy, pandas, hyperliquid-python-sdk, eth_account
```

## PM contract (the 3 calls)

Around every order, the trader makes these PM calls:

| Call | Purpose | Failure |
|---|---|---|
| `GET /size/{engine}` | multiplier on default notional (0.0–1.0) | fail-closed → 0.0 |
| `POST /check` | pre-trade gate `{allow, reason, capital_remaining}` | fail-closed → deny |
| `POST /register_cloid` | attribution before order hits HL | best-effort, never blocks |

PM_CHECK_ENABLED=0 makes these no-ops (use during initial soft rollout).

## Endpoints exposed

```
GET  /health              liveness + version + halt status
GET  /state               engine state + open positions + last signal
GET  /pnl                 24h + cumulative
GET  /signals             recent fired signals
GET  /closures            **PM reads this** — recent closed trades
POST /halt                operator halt (X-Halt-Token required if set)
POST /resume              operator resume
POST /scan                manual scan trigger
```

## Fork in 10 minutes

```bash
# 1. Clone template
git clone https://gho_<TOKEN>@github.com/Dapperscyphozoa/engine-template.git my-engine-v1
cd my-engine-v1
rm -rf .git && git init

# 2. Rewrite ONE file — your strategy
$EDITOR engine/signal_detector.py
# See the contract docstring in that file for required payload keys.

# 3. (Optional) Adjust universe / params via env vars in render.yaml or Render UI

# 4. Create new repo
curl -X POST https://api.github.com/user/repos \
  -H "Authorization: token gho_<TOKEN>" \
  -d '{"name":"my-engine-v1","private":false}'

# 5. Push
git remote add origin https://gho_<TOKEN>@github.com/Dapperscyphozoa/my-engine-v1.git
git add . && git commit -m "initial: fork of engine-template"
git push -u origin main

# 6. Create Render service from the repo (web service, Python, free tier or starter)
#    Set ENGINE_NAME, HL_WALLET, HL_PRIVATE_KEY, PM_AUTH_TOKEN, HALT_TOKEN

# 7. Verify
curl https://my-engine-v1.onrender.com/health
curl https://my-engine-v1.onrender.com/state
```

## signal_detector.py contract

`evaluate_latest_bar(df: pd.DataFrame) -> Optional[dict]`

Required payload keys (trader reads):
- `fire_ts` — pandas Timestamp
- `ref_price` — float, entry reference
- `atr` — float
- `trade_side` — `"B"` (buy) or `"A"` (ask/sell)
- `is_long` — bool
- `sl_px` — float, absolute stop price
- `tp_px` — float, absolute take-profit price
- `max_hold_bars` — int, time-stop in bars

Optional debug fields land in `extras_json` automatically.

## Mode safety

- Default: `LIVE_TRADING=0`, `PM_CHECK_ENABLED=1`.
- PM `/size/{engine}` returns 0.0 for `paper` lifecycle stage → no live orders fire.
- Operator (Cyber) manually promotes lifecycle stage from PM admin endpoint.
- Engine respects HALT file at `/var/data/<ENGINE_NAME>_halted` (also via POST /halt).

## Not in template (engine-specific)

- Backtest script — write `backtest.py` in your fork; commit BACKTEST_*.md before promotion
- Strategy params YAML — keep tuned numbers in `config.STRATEGY_PARAMS` or env vars
- Coin universe overrides — `PRIMARY_UNIVERSE` env var, CSV
