# STAGE 2 — Causality & Implementation Audit

Tests in `audit/stage2.py`; raw output `results/stage2_output.txt`.

## PASS — no lookahead, no exit-before-entry
| Surface | Result |
|---|---|
| IB timing / level creation | Levels use only 09:30–10:30 data, go live at `created_at` (≥ IB cutoff); touches scanned only from `created_at`. Causal. |
| VXN alignment | Strictly prior close on every sampled session (e.g. 2020-03-16 → 2020-03-13). **PASS** |
| 1h range (anchor) | Prior *completed* clock hour (`floor(60min) − 60min`). Causal. |
| Entry detection | `close.shift(1)` cross; entry at level; P&L path starts bar *after* touch. Causal. |
| Stop vs target within bar | Stop checked **before** target = adverse ordering. Correct. |
| Breakeven activation | Minute 150, prior bar close. Causal. |
| SAL | Post-hoc, stable tie-break, now keyed on raw pnl. Causal. |
| Overnight cutoff boundary | 15:00–19:00 touches dropped (co=None); ≥19:00 → next-day 15:00; <15:00 → same-day 15:00. **exit_time ≥ entry: 0/1328 violations.** |
| DST | tz-aware `America/New_York` throughout; verified across a Nov session. |

## FINDINGS — two optimistic fills (spec requires adverse)

### F2.1 (material) — trail_frac=0 TP books the running-max high, not the target
With `trail_frac=0` the post-engagement exit triggers at `best` (running-max bar high) on the bar after the target is touched, not at the target price. Winners therefore realize **+12.8 pts over target on average, +3,683 pts total ≈ 11% of raw net (32,860)**. In live trading a resting limit/market-on-touch fills at ≈ target. **Correction for Stage 6/8: TP fills at the target price** (`tp_d`), minus slippage. This alone trims raw net to ~29,200 pre-cost.

### F2.2 (minor) — gap-through-stop fills at requested price
Only **3 of 458** stop/BE exits had the bar open beyond the stop (0.7%); mean adverse gap 17.5 pts, 52 pts total (0.16% of net). Immaterial, but Stage 8 will fill stops at `min(stop, bar_open_on_adverse_side)`.

### F2.3 (note) — entry bar excluded from path
`path = bars.loc[ft:co].iloc[1:]` drops the entry bar, so an adverse move *within* the entry minute is never counted. Mild optimism; retained but noted.

**Net effect:** the headline is inflated ~11% by F2.1 and ~12% by the 2× sizing (Stage 1) — together roughly a quarter of the weighted headline is fill-mechanics + sizing, not raw signal. The raw, realistic-fill signal is what Stages 3–11 must vindicate.
