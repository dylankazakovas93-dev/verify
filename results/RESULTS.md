# NQ Level-Fade Backtest Results

Data: continuous front-month NQ 1-minute OHLCV, 2018-01-01 through 2026-06-07
(2170 sessions), built by `build_nq_data.py` from raw Databento GLBX.MDP3
dumps in `data/raw_databento/`. VXN daily closes from `data/vxn_daily.csv`.
No changes were made to the strategy logic in `levels.py` / `simulate.py`.
These are the numbers the code produces — unadjusted.

Reproduce with:
```bash
python3 run_sweep_report.py
```

## sl_pts sensitivity sweep (75-125, step 5)

| sl_pts | n    | net (pts) | PF    | tWR%  | TP  | SL  | BE  | cutoff | avg_win | avg_loss | max_dd  |
|-------:|-----:|----------:|------:|------:|----:|----:|----:|-------:|--------:|---------:|--------:|
| 75     | 2144 | -2489.47  | 0.581 | 18.2  | 176 | 790 | 565 | 613    | 195.4   | -75.0    | -3701.45|
| 80     | 2152 | -2783.53  | 0.594 | 19.5  | 182 | 749 | 578 | 643    | 195.5   | -80.0    | -4328.34|
| 85     | 2156 | -3559.64  | 0.592 | 20.5  | 185 | 719 | 583 | 669    | 195.6   | -85.0    | -4995.13|
| 90     | 2156 | -3577.03  | 0.595 | 21.5  | 187 | 683 | 587 | 699    | 195.7   | -90.0    | -4934.49|
| 95     | 2157 | -4294.21  | 0.596 | 22.4  | 188 | 651 | 591 | 727    | 195.9   | -95.0    | -5675.64|
| 100    | 2159 | -4796.50  | 0.605 | 23.6  | 192 | 623 | 594 | 750    | 196.2   | -100.0   | -6287.44|
| 105    | 2162 | -5045.12  | 0.616 | 24.8  | 195 | 591 | 599 | 777    | 196.0   | -105.0   | -6443.14|
| 110    | 2164 | -4836.19  | 0.624 | 25.9  | 196 | 560 | 604 | 804    | 196.1   | -110.0   | -6232.03|
| 115    | 2166 | -4902.40  | 0.637 | 27.2  | 198 | 530 | 608 | 830    | 196.0   | -115.0   | -6266.52|
| 120    | 2166 | -5181.77  | 0.650 | 28.4  | 200 | 503 | 609 | 854    | 196.1   | -120.0   | -6241.50|
| 125    | 2168 | -4107.95  | 0.685 | 30.4  | 204 | 467 | 616 | 881    | 196.1   | -125.0   | -5876.06|

Every sl_pts value in the requested 75-125 range produces a **net loss** and
**PF < 1** over 2018-2026. Widening the stop monotonically raises PF and tWR%
(fewer SL hits) but net P&L does not turn positive anywhere in this range —
the "cutoff" bucket (forced 15:00 ET close, `n=613..881`) is the largest or
second-largest exit category throughout, meaning a large share of trades
never reach TP, SL, or BE and are closed at whatever the market happens to be
doing at session end.

Full per-trade logs: `results/trades_sl<N>.csv`. Full monthly P&L tables:
`results/monthly_pnl_sl<N>.csv`. Equity curves for all 11 sl_pts values:
`results/equity_curves.png`.

## Monthly net P&L (sl_pts=100 example)

See `results/monthly_pnl_sl100.csv` for the full 2018-01 through 2026-06
table (103 months). No sustained profitable regime is visible — monthly
P&L alternates sign throughout the whole period without a clear trend.
