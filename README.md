# verify

Reproducible backtests of intraday level-fade strategies on NQ and ES futures.

## Contents

### Original fixed-stop engine (NQ only)

- `levels.py` — level generation: sigma-day formula, IB (initial balance)
  calculation, sigma offsets. Produces `upper_level` / `lower_level` per
  session from 1-minute OHLCV + daily VXN closes.
- `simulate.py` — full strategy simulation: entries (first-touch fade),
  stop/exit rules (fixed stop, conditional break-even at bar 45, 200pt-trigger
  20pt trailing stop), session cutoff, and Stop-After-Loss (SAL).
- `run_backtest.py` — CLI entry point that runs the sl_pts sensitivity sweep
  and prints summary stats + monthly P&L per value.

### Locked cond-BE45 + 10%-trail engine (NQ and ES)

- `nq_verify.py` — self-contained, NQ-only version of this engine (as
  supplied for independent verification).
- `futures_verify.py` — same trade logic as `nq_verify.py`, generalized to
  take instrument params (`sigma_mult`, `offset_pct`/`fixed_offset`,
  `ib_minutes`) so NQ and ES share one implementation. Overnight entry
  window (19:00–11:00 ET next session, skip 11:00–15:00), dynamic
  cap = min(1.5 × prior completed 1h range, 200pts) used as both stop and
  TP-engagement threshold, conditional BE@45, 10%-of-cap trailing stop once
  price reaches the cap, SAL.

### Data

- `data/vxn_daily.csv` — CBOE Nasdaq-100 Volatility Index daily closes, vol
  input for NQ levels.
- `data/vix_daily.csv` — official CBOE VIX daily OHLC (`VIX_History.csv`),
  2017-01 through the most recent close available, vol input for ES levels.
- `data/raw_databento/`, `data/raw_databento_es/` — raw Databento GLBX.MDP3
  1-minute dumps (every simultaneously-listed contract) for NQ and ES.
- `build_futures_data.py` — derives a continuous front-month series from the
  raw dumps (highest-daily-volume contract per session, no price
  adjustment across rolls): `python3 build_futures_data.py {nq,es}` ->
  `data/nq_1m_2018_2026.csv.gz` / `data/es_1m_2018_2026.csv.gz`.
- `docs/prompt_for_claude*.txt` — the task specifications as given, in order.

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

- [x] Original fixed-stop engine (`levels.py`/`simulate.py`) verified on NQ,
      2018-2026: net negative, PF < 1 at every sl_pts from 75-125 — see
      `results/RESULTS.md`.
- [x] Locked cond-BE45 + 10%-trail engine (`nq_verify.py`) verified on NQ,
      2018-2026: net +18056.7pts, PF 1.787, tWR 59.0% — see
      `results/nq_verify_output.txt`.
- [x] Same engine (`futures_verify.py`) run on ES with VIX as the vol input,
      2018-2026: net +3111.3pts, PF 1.641, tWR 55.2% — see
      `results/es_benchmark_no_vix_gate_output.txt`. This is in the same
      direction as an external report for this exact config (net 3832.76,
      PF 1.794, tWR 63.21%) but not an exact match, most likely due to
      differences in how the continuous ES series / VIX source were built —
      not yet reconciled.
- [ ] In-sample (2018/2021/2025) parameter grid search — not started.

Report actual output only — logic is not to be adjusted to hit any target
PF/tWR.
