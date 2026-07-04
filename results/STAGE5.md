# STAGE 5 — 300-pt Ceiling Saturation & Volatility-Regime Diagnosis

`audit/stage5.py`; output `results/stage5_output.txt`. Causal series, winner config.

## Per-year saturation
| year | n | stop cap % | tgt cap % | med uncap stop | med act stop | med 1h range | med prior ATR | med price | avg risk 1×MNQ |
|---|---|---|---|---|---|---|---|---|---|
| 2018 | 153 | 28.8 | 4.6 | 198 | 198 | 16.5 | 88 | 7092 | $398 |
| 2019 | 151 | 29.1 | 4.6 | 225 | 225 | 18.8 | 91 | 7703 | $427 |
| 2020 | 163 | 71.8 | 32.5 | 456 | 300 | 38.0 | 210 | 10164 | $540 |
| 2021 | 151 | 72.8 | 29.8 | 462 | 300 | 38.5 | 189 | 14051 | $551 |
| 2022 | 143 | 91.6 | 45.5 | 666 | 300 | 55.5 | 313 | 12268 | $590 |
| 2023 | 152 | 74.3 | 28.9 | 484 | 300 | 40.4 | 223 | 14697 | $564 |
| 2024 | 151 | 82.1 | 33.1 | 507 | 300 | 42.2 | 250 | 18831 | $563 |
| 2025 | 189 | 89.4 | 48.1 | 699 | 300 | 58.2 | 326 | 22944 | $586 |
| 2026 | 78 | **98.7** | **70.5** | 1047 | 300 | 87.2 | 418 | 25376 | $598 |

Overall: **69.8% of stops, 31.3% of targets pinned at 300.** Average per-trade risk is **$398→$598 on 1 MNQ every year — over the $300 budget throughout.** The "12× range" stop is fiction in modern NQ (2026 median *uncapped* stop = 1,047 pts).

## Volatility quintiles (by prior_1h_range)
| quintile | anchor range | n | net | PF | avg | maxDD | stop cap % | tgt cap % |
|---|---|---|---|---|---|---|---|---|
| Q1 low | 3–19 | 270 | 1,756 | 1.369 | 6.5 | −986 | 0 | 0 |
| Q2 | 19–31 | 263 | 5,557 | 1.749 | 21.1 | −935 | 50 | 0 |
| Q3 | 31–48 | 268 | 2,354 | 1.252 | 8.8 | −911 | 100 | 0 |
| Q4 | 48–84 | 264 | 6,301 | 1.719 | 23.9 | −1,353 | 100 | 57 |
| **Q5 high** | **85–570** | **266** | **17,775** | **3.036** | **66.8** | −907 | **100** | **100** |

## Verdict: mixture, and the profitable core is the *fixed* regime
- **Q5 (highest vol) = 53% of total net at PF 3.04, and is 100% stop- AND target-capped — i.e. a pure fixed 300/300 system.** The strongest part of the edge is *not* range-normalized; it is fixed-distance.
- The genuinely range-normalized low-vol regime (Q1, 0% capped) is the weakest (PF 1.37).
- Therefore the strategy is a **mixture whose behaviour changes materially by regime**, and its money is made in the high-volatility, fully-saturated 300/300 regime — exactly the regime that is untradeable at $300/MNQ (300 pt = $600).

**Redesign implication (Stage 6):** the question is whether a *smaller* stop/target (Family B/C/D) can retain the high-vol edge while fitting the risk budget, or whether shrinking the stop destroys precisely the Q5 trades that carry the result. That is now the decisive test.
