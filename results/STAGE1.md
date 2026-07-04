# STAGE 1 — Reproduction & Full Metric Breakdown

Reproduced from `audit/stage1.py` (see `results/stage1_output.txt`). Data hashes in `PROVENANCE.md`.
Winner config: `sl_mult=12, tp_mult=5, cap_ceiling=300, be_bars=150, close_cross, trail_frac=0, round_tol=2, round_size_mult=2`.

## Headline reproduces exactly
Original engine (SAL sees the **sized** pnl, as shipped): **n=1328, weighted net = +37,303.1 ptct, PF 1.903** — matches `grid_winner_nq.txt` to the decimal.

## Required breakdown (winner, 2018–2026)

| Metric | Value |
|---|---|
| Raw unweighted per-contract points | **+32,860.0** |
| Weighted point-contract P&L (2×) | **+37,303.1** |
| Dollar P&L, 1×NQ basis ($20/pt) | $746,062 |
| Dollar P&L, 1×MNQ basis ($2/pt) | $74,606 |
| PF before round-number sizing | **1.836** |
| PF after round-number sizing | **1.903** |
| Net before round sizing | +32,860.0 |
| Net after round sizing | +37,303.1 |
| Extra pts purely from the 2× | **+4,443.1** (102 of 1328 trades doubled) |
| True outright win rate (pnl>0) | **43.0%** |
| Target-hit rate excl. breakevens | 31.2% |
| tWR = TP/(TP+SL) | 85.2% |
| Breakeven rate | 30.7% |
| Loss rate | 26.3% |
| Average winner | +137.7 (sized) / +126.4 (raw) |
| Average loser | −118.3 (sized) / −112.6 (raw) |
| Max drawdown (raw) | −1,234.6 |
| Max drawdown (weighted 2×) | −1,532.2 |
| Exit distribution | TP=287, SL=50, BE=408, cutoff=583 |

**The dollar figures above are NOT the deployable P&L** — they assume a fixed 1 contract (with 2× on round levels) and ignore the integer-contract risk budget. Real deployable dollars are computed in Stage 9.

## SAL / position-size independence (mandated correction)
Spec requires SAL to depend only on the raw trade's sign and exit class, never on contract count. The shipped engine folds the 2× into `pnl` **before** `apply_sal`, so in principle sizing could perturb eligibility. I built a decoupled version (`score_decoupled`: SAL keyed on `raw_pnl`, size carried separately).

**Result: admission delta = 0 trades.** For the winner config the coupling is inert — both paths keep the same 1,328 trades with an identical exit mix. (This corrects my prior audit's "~781 pt coupling" estimate, which was an artifact of comparing *disabling* the boost vs *decoupling* it.) The decoupled scorer is used for all downstream stages regardless.

## Per-year (corrected, raw single-contract pnl)

| year | n | net | pf |
|---|---|---|---|
| 2018 | 155 | 1673.5 | 1.722 |
| 2019 | 155 | 290.6 | 1.120 |
| 2020 | 163 | 3743.6 | 1.808 |
| 2021 | 151 | 3299.1 | 1.674 |
| 2022 | 145 | 3022.1 | 1.432 |
| 2023 | 150 | 2712.8 | 1.782 |
| 2024 | 147 | 2564.8 | 1.377 |
| 2025 | 184 | 10731.0 | 2.924 |
| 2026 | 78 | 4822.6 | 3.209 |

Every year net-positive. **Concentration: 2025 alone = 32.7% of raw net; 2025+2026 = 47.3%; the three ex-in-sample years = 47.8%.** This concentration is the central robustness question carried into later stages.
