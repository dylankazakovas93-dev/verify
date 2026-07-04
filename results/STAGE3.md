# STAGE 3 — Contract-Roll Audit

Causal roll built in `audit/build_causal_roll.py` (sha256 of series in output); test in `audit/stage3.py`.

## The shipped roll is non-causal, but the effect is negligible
`build_futures_data.py:75` picks each session's front contract from **that session's own summed volume** — not knowable at the open. Corrected to a **causal** rule (front contract = highest-volume outright from the **previous** session, monotonic preserved).

- Causal vs shipped front contract differs on only **33 of 2,624 sessions (1.3%)**.
- 34 quarterly rolls (H/M/U/Z), 2018–2026.

## Edge survival (raw single-contract pnl)

| Scenario | n | net | PF | maxDD |
|---|---|---|---|---|
| Shipped series (Stage 1 baseline) | 1328 | 32,860 | 1.836 | −1234.6 |
| **Causal series, all trades** | 1331 | **33,744** | **1.865** | −1234.6 |
| Causal, exclude cross-contract (75 trades) | 1256 | 31,140 | 1.841 | −1285.5 |
| Causal, exclude ±5 sessions of roll (223 trades) | 1108 | 25,569 | 1.743 | −1322.3 |
| Causal, exclude **both** | 1091 | 24,294 | **1.709** | −1322.3 |

Every calendar year remains net-positive after both exclusions (weakest: 2019 +120, 2024 +492).

## Verdict
The causal roll is *slightly better* than the shipped non-causal one, so the roll bug did not manufacture the edge. Removing all roll-adjacent (17% of trades) and cross-contract trades still leaves PF 1.71 with no negative year. **The edge is not a contract-roll artifact.** The causal series (`nq_1m_causal.csv.gz`) is adopted as the canonical series for all downstream stages, and the same causal roll rule is preregistered for the master-OOS build.
