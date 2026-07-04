# verify

Reproducible backtest of an NQ futures intraday level-fade strategy.

## Contents

- `levels.py` — level generation: sigma-day formula, IB (initial balance)
  calculation, sigma offsets. Produces `upper_level` / `lower_level` per
  session from 1-minute OHLCV + daily VXN closes.
- `simulate.py` — full strategy simulation: entries (first-touch fade),
  stop/exit rules (fixed stop, conditional break-even at bar 45, 200pt-trigger
  20pt trailing stop), session cutoff, and Stop-After-Loss (SAL). Enforces
  **one open position at a time per session** via an explicit `active`
  trade state — no new entry is considered while a trade is still open.
- `run_backtest.py` — CLI entry point that runs the sl_pts sensitivity sweep
  and prints summary stats + monthly P&L per value.
- `data/vxn_daily.csv` — CBOE Nasdaq-100 Volatility Index daily closes, vol
  input for NQ levels.
- `data/nq_1m_2018_2026.csv.gz` — continuous front-month NQ, 2018-2026,
  built from raw Databento GLBX.MDP3 dumps (`data/raw_databento/`) via
  `build_futures_data.py` (highest-volume quarterly H/M/U/Z contract per
  session, monotonic roll, no price adjustment across rolls).
- `docs/prompt_for_claude.txt` — the original task specification.

## Strategy rules (implemented in code — do not change without discussion)

See `docs/prompt_for_claude.txt` for the full rule set. Summary:

- **Levels**: `sigma_day = cash_open * (vxn_prior_close/100) / sqrt(252)`;
  `imp_up/dn = cash_open ± 1.25*sigma_day`; IB = first 60 min from 09:30 ET;
  `upper/lower_level = avg(ib_ext, imp) ∓ 15.75`; live at first bar ≥ 10:30 ET.
- **Entry**: fade on first touch from the appropriate side, one entry per
  level per session, entry window 10:30–11:00 ET only, **only while no
  other position is open**.
- **Levels expire** after `LINE_DAYS=20` sessions from creation.
- **Exit**: fixed stop (`sl_pts`, tuned parameter); conditional BE at bar 45
  if in profit; trailing stop arms at +200pts unrealised, trails 20pts behind
  peak; session force-close at 15:00 ET; Stop-After-Loss (no re-entry same
  session after an SL exit).

## How to run

```bash
python run_backtest.py path/to/nq_1m.csv --sl-min 75 --sl-max 125 --sl-step 5
```

Or interactively:

```python
from levels import load_1m_ohlcv, load_vxn_daily, NQ_PARAMS
from simulate import simulate, summary

ohlcv = load_1m_ohlcv("path/to/nq_1m.csv")
vxn   = load_vxn_daily("data/vxn_daily.csv")

trades = simulate(ohlcv, vxn, sl_pts=100)
print(summary(trades))
```

## Status

- [x] Fixed-stop engine (`levels.py`/`simulate.py`) verified on NQ,
      2018-2026: **net negative, PF < 1 at every sl_pts from 75-125** — see
      `results/RESULTS.md`. This is the only engine in this repo's history
      that correctly enforces one position open at a time; it is the real,
      current state of this strategy.
- [ ] Everything else attempted after this (a self-contained "locked"
      engine variant, its generalization to ES, a parameter grid search,
      and a walk-forward test) has been removed from this repo. All of it
      shared one underlying simulation approach that evaluated each
      level's touch independently, with no check for whether another
      trade was already open — confirmed to allow genuinely overlapping
      simultaneous positions (147 overlapping trade-pairs across 120
      sessions found in a single spot-check), which is not a valid trading
      constraint. Every profitable number produced by that lineage (the
      NQ "locked" baseline, every tuned NQ variant, and the entire ES
      analysis, which was never run through anything else) inherited this
      flaw. None of it should be treated as validated. `data/es_1m_2018_2026.csv.gz`,
      `data/vix_daily.csv`, `data/raw_databento_es/`, and
      `build_futures_data.py` are kept since they're just data, reusable
      once a correctly-constrained (single-position) ES engine exists.
- [ ] A correctly-constrained ES backtest (mirroring `simulate.py`'s
      single-active-position design) has not yet been built.

Report actual output only — logic is not to be adjusted to hit any target
PF/tWR.
