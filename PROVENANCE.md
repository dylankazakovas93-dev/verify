# Data & Code Provenance

Development data (2018–2026) lives on branch `claude/repo-reproducibility-setup-4m69kb`.
Audit code in `audit/` runs against it via `AUDIT_DATA=<that repo>/data`.
The 2013–2015 master-OOS data is intentionally withheld by the operator until the spec is frozen (Stage 12).

## SHA-256 — input data

```
226ee52d719bafe306105bc2343182d6c291fba2a412ff29d51923adac1db49a  data/nq_1m_2018_2026.csv.gz
76cc072c941542183d8c82174fe4f552b0f140f9cb368c302ad51f651992dc4e  data/vxn_daily.csv
fd1691e6a2d5f0a132bc281186e30bc573889475d451cfc1d21c71e120375d8e  data/vix_daily.csv
1e7bf7df90fd353e5f2eb0f6ed706fd11a655f474e1f1445784258ac714c9027  data/es_1m_2018_2026.csv.gz
e55c17379a26e04760c6eb331a3300959ce5067779fdf3df097e3c4762f70e52  data/raw_databento/glbx-mdp3-20180101-20191230.ohlcv-1m.csv.zst
738b657513aca8d5130936e96c2dcef3b5571fe979702cdf1b12022375aaf5ff  data/raw_databento/glbx-mdp3-20200101-20201230.ohlcv-1m.csv.zst
5012a9f1685175a7a4d83d5999e37f06abf084f0035c25279e1d01f3ae030750  data/raw_databento/glbx-mdp3-20210101-20221230.ohlcv-1m.csv.zst
fbc646a1be1e854e8aa187290ed74d5b58471367385a97197000267b51a72219  data/raw_databento/glbx-mdp3-20230101-20241230.ohlcv-1m.csv.zst
4a56638dad7a79c8d0d42a28da2a2bab58273fb0b8274f47bf54da05b7dc7cad  data/raw_databento/glbx-mdp3-20250101-20260607.ohlcv-1m.csv.zst
```

## Data coverage (verified)
- NQ 1-minute: 2018-01-01 18:00 ET → 2026-06-07 19:59 ET. **No pre-2018 bars.**
- VXN daily: 2017-12-01 → 2026-06-08. **No pre-2017 closes.**
- VIX daily: 2017-01-03 → 2026-07-03.

**Implication:** the 2013–2015 master-OOS requires NQ 1-minute bars AND VXN daily closes for that span, neither of which exists in-repo. The operator must supply both (Databento GLBX.MDP3 NQ ohlcv-1m + CBOE VXN history) at freeze time, built through the *same* `build_futures_data.py` roll and `load_vol_daily` path used here.
