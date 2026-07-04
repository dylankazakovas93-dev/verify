# verify

Reproducible backtest of an NQ futures intraday level-fade strategy.

## Contents

- `levels.py` — level generation: sigma-day formula, IB (initial balance)
  calculation, sigma offsets. Produces `upper_level` / `lower_level` per
  session from 1-minute OHLCV + daily VXN closes.
- `simulate.py` — full strategy simulation: entries (first-touch fade),
  stop/exit rules (fixed stop, conditional break-even at bar 45, 200pt-trigger
  20pt trailing stop), session cutoff, and Stop-After-Loss (SAL).
- `run_backtest.py` — CLI entry point that runs the sl_pts sensitivity sweep
  and prints summary stats + monthly P&L per value.
- `data/vxn_daily.csv` — CBOE Nasdaq-100 Volatility Index daily closes
  (date, open, high, low, close), used as the implied-vol input to the level
  formula.
- `docs/prompt_for_claude.txt` — the original task specification: strategy
  rules, level formula, and reporting requirements.

## Strategy rules (implemented in code — do not change without discussion)

See `docs/prompt_for_claude.txt` for the full rule set. Summary:

- **Levels**: `sigma_day = cash_open * (vxn_prior_close/100) / sqrt(252)`;
  `imp_up/dn = cash_open ± 1.25*sigma_day`; IB = first 60 min from 09:30 ET;
  `upper/lower_level = avg(ib_ext, imp) ∓ 15.75`; live at first bar ≥ 10:30 ET.
- **Entry**: fade on first touch from the appropriate side, one entry per
  level per session, entry window 10:30–11:00 ET only.
- **Levels expire** after `LINE_DAYS=20` sessions from creation.
- **Exit**: fixed stop (`sl_pts`, tuned parameter); conditional BE at bar 45
  if in profit; trailing stop arms at +200pts unrealised, trails 20pts behind
  peak; session force-close at 15:00 ET; Stop-After-Loss (no re-entry same
  session after an SL exit).

## Data needed to run

1. **NQ 1-minute OHLCV CSV** (`timestamp`/`datetime`, `open`, `high`, `low`,
   `close`) — not yet committed to this repo, to be provided separately.
2. **VXN daily CSV** — already in `data/vxn_daily.csv`.

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

- [x] Strategy code (`levels.py`, `simulate.py`) committed.
- [x] VXN daily data committed.
- [x] NQ 1-minute OHLCV data committed (`data/raw_databento/` raw Databento
      dumps + `build_nq_data.py` to derive the continuous front-month series
      + `data/nq_1m_2018_2026.csv.gz`, the built series used for the backtest).
- [x] Backtest run + sensitivity sweep results reported — see
      `results/RESULTS.md`.

Report actual output only — the logic in `levels.py`/`simulate.py` is not to
be adjusted to hit any target PF/tWR. Result: at every sl_pts value tested
(75-125), the strategy is net negative with PF < 1 over 2018-2026.
