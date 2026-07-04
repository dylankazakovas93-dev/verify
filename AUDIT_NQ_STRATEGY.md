# Adversarial Audit — NQ Level-Fade Strategy

**Claim under audit:** net **+37,303 pts**, **PF 1.903**, **1,328 trades**, NQ futures, 2018–2026.
**Config audited:** `engine.py` grid winner — `ExecParams(cap_mult=4.0, sl_mult=12.0, tp_mult=5.0, cap_ceiling=300, be_bars=150, be_mechanic='close_cross', trail_frac=0.0, round_step=50.0, round_tol=2, round_size_mult=2.0)`, NQ level params `sigma_mult=1.25, ib_minutes=60, fixed_offset=15.75`.
**Source:** branch `claude/repo-reproducibility-setup-4m69kb`.

## Verdict

**I could not find a disqualifying flaw, and I could not find any lookahead bias.** The engine is causal throughout, and the headline number reproduces exactly (`+37,303.1 / PF 1.903 / n=1328`). A rolling walk-forward is positive in **14 of 14** windows. That is genuinely more than most backtests survive.

**But the claim as *worded* is the flattering framing of a materially more modest reality.** Three specific descriptors — "PF 1.90," "85% win rate," and "12:5 risk:reward" — each overstate what the trades actually do, and roughly half the P&L comes from the two most recent (and partly in-sample) years. The right one-line summary is: *a real, causally-clean, out-of-sample-positive edge whose advertised stat line is inflated by framing, an in-sample-fit sizing knob, and recency.*

---

## What is clean (checked and passed)

**No lookahead anywhere.** I traced every surface the prompt named:

- **Level generation** (`generate_levels`): uses `cash_open` (first RTH bar), the **prior** VXN close (`_prior_close` filters strictly `index < session_date`), and the IB from the first 60 min. Levels only go live at `created_at` (first bar ≥ IB cutoff), and `build_touches` only scans `bars.loc[created_at:expiry]`. A level can never be touched before it exists. ✔
- **Entry / first-touch** (`_first_touch`): causal — uses `close.shift(1)` for cross detection; entry price is the level; the P&L path starts on the bar *after* the touch (`.iloc[1:]`). ✔
- **Anchor** (`_prev_completed_range`): the prior *completed* clock-hour range (`ts.floor("60min") − 60min`). Never the current/forming hour. ✔
- **Stop/target ordering** (`sim_trade`): the stop is checked **before** the target within each bar — the conservative choice when a bar straddles both. ✔
- **Breakeven** (`close_cross`): arms at minute 150 off `cl[i-1]` (prior close) — no peek at the current bar. ✔
- **Stop-After-Loss** (`apply_sal`): a post-hoc causal filter with a documented `kind="stable"` tie-break. No lookahead, and the tie-break reasoning is correct. ✔

**The result reproduces exactly.** `+37,303.1 / PF 1.903 / n=1328`, matching `results/grid_winner_nq.txt` to the decimal. It is not fabricated.

**Net is not carried by the forced-close bucket.** I initially suspected the 583 "cutoff" (15:00 mark-to-market) exits — 44% of trades — were driving the number. They are not: that bucket is a near-wash (**−1,069 pts, 284 wins / 299 losses**). The net decomposes as **+52,784 (TP) − 14,412 (SL) − 1,069 (cutoff) + 0 (BE)**. The edge is genuine target capture, not a coin-flip at the bell.

---

## Findings (real problems, ranked)

### 1. "85% win rate" excludes 75% of the trades and is an artifact of stop width
Reported `tWR = 85.2%` is `TP / (TP+SL)` only. It ignores the **408 breakeven** and **583 cutoff** exits — **991 of 1,328 trades (75%)**.

| Metric | Value |
|---|---|
| tWR (TP/(TP+SL)) | 85.2% |
| **Outright win rate (pnl > 0)** | **43.0%** |
| Breakeven (pnl = 0) | 30.7% |
| Losses (pnl < 0) | 26.3% |

Only **50 of 1,328** trades (3.8%) ever hit the stop, because the stop is ~300 pts wide. Almost every adverse move is converted to breakeven or ridden to the 15:00 cutoff, which is *why* TP/(TP+SL) looks like 85%. The repo's own `nq_verify.py`, run with a normal-width stop, reports **tWR 59%** on the same touch set. The 85% is a property of the 300-pt stop, not of predictive skill.

### 2. "12:5 risk:reward" misrepresents what trades experience — the flat 300 ceiling dominates
The `12×`/`5×` multipliers rarely bind. The **300-pt ceiling** governs most trades:

| Year | % stop pinned at 300 | % target pinned at 300 |
|---|---|---|
| 2018 | 29% | 4.5% |
| 2020 | 72% | 33% |
| 2022 | 92% | 45% |
| 2025 | 89% | 46% |
| 2026 | **99%** | **69%** |
| **All** | **70%** | **31%** |

By 2022–2026 the "12:5" description is fiction: the majority of trades run a flat **300 stop / 300 target = 1:1**. **Realized R:R is 1.16:1**, not 2.4:1. On *hard* resolutions the profile is actually inverted — average SL loss **−288** vs average TP win **+184** — i.e. a **negative** reward:risk that only nets positive because wins are ~6× more frequent than stop-outs. That is a "pick up pennies in front of the steamroller" profile: rare (3.8%) but large stop-outs, under-sampled tail. The headline "12:5 R:R, PF 1.90" reads as a favorable-payoff strategy; it is the opposite.

### 3. The round-number 2× is baked into the *points* headline (directly relevant to your position-sizing point)
You argued the 2× is position sizing that moves *dollars*, not *points*. **In this code it moves points.** `engine.py:334` computes `pnl * size_mult` and that value rolls straight into the +37,303 points total.

- 101 of 1,328 trades are doubled.
- **+3,661.8 pts (~10% of net) is pure doubling**, and it lifts **PF 1.836 → 1.903**.
- A points/excursion metric is size-invariant by definition: 1 contract's point move is identical whether you hold 1 or 2. So the size-agnostic figure is **~33,641 pts / PF ~1.84**, not 37,303 / 1.90.
- It is also **not a clean post-hoc dollar multiplier**: because the doubled P&L feeds the SAL loss threshold (`pnl < −0.1`), toggling it perturbs which trades survive SAL (~+781 pts of coupling). So it can't be waved off as "just sizing applied at the end."
- Finally, `round_tol=2, round_size_mult=2.0` were **selected by stage 5 of the grid search to maximize in-sample net** — a fitted knob, not a market prior.

This one is squarely a framing error: the +37,303 *points* claim should not contain a contract-count multiplier.

### 4. In-sample fit + heavy recency concentration
The winner was chosen by a ~233-config coordinate-descent search ranked on **in-sample net** over `{2018, 2021, 2025}`. The IS→OOS degradation (PF **2.40 → 1.67**, full **1.90**) is honest and *not* disqualifying by itself. The concentration is the concern:

- **49.6%** of total net comes from the three in-sample years.
- **32.7%** from **2025 alone** (in-sample, PF 3.14).
- **48.7%** from **2025+2026** combined (2026 PF 3.69 — flagged >2σ in `pf_stability_nq.csv`).
- Strip those two juiced recent years and 2018–2024 nets ~19,100 pts (~2,700/yr) — still positive **every** year, but a small fraction of the headline slope.

The strategy is not fake, but its *reported magnitude* leans hard on 2025–2026.

### 5. Reproducibility red flag: three coexisting specs, only the most-optimized one hits the headline
The repo contains three different strategy definitions producing wildly different results on the same data:

| Entry point | Config | Result |
|---|---|---|
| `run_backtest.py` → `simulate.py` (the "reproducible entry point," `RESULTS.md`) | fixed sl sweep, **no take-profit** | **net loss, PF < 1 everywhere** |
| `nq_verify.py` (labeled *"independent verification… strategy is profitable, just run it"*) | symmetric 1.5×/200 cap, BE@45, 10% trail, no 2× | **+18,057 / PF 1.79 / tWR 59%** |
| `engine.py` + `grid_search.py` winner | 12×/5×/300, BE@150, no trail, 2× | **+37,303 / PF 1.90 / tWR 85%** |

A reviewer running the file literally named `nq_verify.py` would **not** reproduce the claim — they'd get half the net and a much plainer win rate. This isn't fraud (the winner *is* reproducible from `engine.py`), but the advertised stat line is the single most-optimized of several results living side by side.

### 6. Minor / bounded issues
- **TP fill optimism.** With `trail_frac=0`, a TP exit books the running-max bar **high**, not the target price — mean realized TP pnl **169.9** vs target distance **155.9**, i.e. **~+14 pts/winner** beyond target, on all 287 winners. It's a within-bar favorable-fill assumption you can't reliably achieve live.
- **15:00–19:00 dead window.** A level whose *first* touch lands in 15:00–19:00 ET returns `cutoff=None` and is silently dropped (and `_first_touch` never revisits it). Neutral-to-conservative — reduces sample, doesn't inflate.
- **Non-price-adjusted roll.** Levels persist 20 sessions; across a quarterly roll the series has an unadjusted price gap, so a persistent level can be "touched" (or missed) across the splice. Bounded (~4 rolls/yr, small NQ gaps) but real.

---

## What actually supports the strategy

- **No lookahead**, verified surface by surface.
- **Reproducible** to the decimal.
- **Walk-forward positive in 14/14** rolling windows (re-fit each), PF 1.13–2.50 — the strongest evidence, and it says the strategy *family* generalizes out of sample. Note the winning params differ wildly window-to-window (ceiling 100–500, sl 1.3–10, tp 0.9–12), so the *specific* `sl=12/tp=5/300` config is not itself stable — the concept is.
- **Every calendar year 2018–2026 is net-positive** (PF 1.26–3.69).

## Trust boundary

Trust it as: *a causally-clean, out-of-sample-positive fade edge with a high-frequency / negative-realized-R:R / rare-large-loss risk profile.*
Do **not** trust the literal stat line "+37,303 pts, PF 1.90, 85% win rate, 12:5 R:R." Size-agnostic it's ~33,600 pts / PF ~1.84; the true win rate is 43%; the realized R:R is ~1.16 (and negative on hard resolutions); and ~half the P&L is 2025–2026. The tail (a day that runs through the 300-pt stop) is under-sampled at 50 events in 8.5 years.
