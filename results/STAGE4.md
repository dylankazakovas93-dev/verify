# STAGE 4 — Concurrency & Duplicate-Exposure Audit

`audit/stage4.py`; output `results/stage4_output.txt`. Causal series, winner config, raw pnl.

## Concurrency
| Metric | Value |
|---|---|
| Max concurrent open positions | **7** |
| Median concurrency while exposed | 1 |
| 95th percentile | 3 |
| 99th percentile | 4 |
| Max combined open stop risk (unrestricted) | **1,956 pts = $3,912 on 1 MNQ each** |

## Results under concurrency caps (raw pnl)
| Rule | n | net | PF | maxDD | max combined stop risk |
|---|---|---|---|---|---|
| Unrestricted | 1331 | 33,744 | 1.865 | −1234.6 | 1,956 pts ($3,912) |
| **Max 2 open** | 1239 | 24,239 | 1.621 | −1464.8 | 600 pts ($1,200) |
| **Max 1 open** | 1037 | **7,109** | **1.185** | **−2875.6** | 300 pts ($600) |

**The headline depends materially on multi-position exposure.** One-position-at-a-time — the only capital-safe mode for a $2k / 1-MNQ account — collapses PF to 1.19 and *worsens* drawdown to −2,876 pts (−$5,751 on 1 MNQ). Max-2 preserves PF 1.62 but combined stop risk is $1,200 (×2 sizing would double it, breaching budget).

## Duplicate near-levels (same direction, causal priority = earlier entry)
| Dedup tol | n | net | PF | dropped |
|---|---|---|---|---|
| 2 pt | 1300 | 31,436 | 1.806 | 31 |
| 5 pt | 1269 | 29,454 | 1.755 | 62 |
| 10 pt | 1231 | 26,836 | 1.688 | 100 |
| 20 pt | 1167 | 21,419 | 1.555 | 164 |

Nearby levels *do* partly rebook the same reaction (20-pt dedup removes 12% of trades but 37% of net), but the edge survives (PF 1.55 at 20 pt). A **≥5–10 pt same-direction dedup** will be preregistered in the final spec.

**Deployability flag:** even unrestricted, raw single-MNQ drawdown is −$2,469, already over the $2,000 limit — before costs. The redesign must cut stop size and cap concurrency.
