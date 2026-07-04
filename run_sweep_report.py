"""
Runs the sl_pts sensitivity sweep (75-125) on the committed NQ + VXN data,
writes per-sl_pts trade logs to results/trades_sl<N>.csv, a summary table to
results/sweep_summary.csv, and monthly P&L tables to
results/monthly_pnl_sl<N>.csv.
"""
from __future__ import annotations

import os
import time

import pandas as pd

from levels import load_1m_ohlcv, load_vxn_daily, NQ_PARAMS
from simulate import simulate, summary

OUT_DIR = "results"
os.makedirs(OUT_DIR, exist_ok=True)


def monthly_pnl(trades: pd.DataFrame) -> pd.DataFrame:
    df = trades.copy()
    df["month"] = pd.to_datetime(df["exit_time"]).dt.to_period("M").astype(str)
    return df.groupby("month")["pnl"].sum().round(2).reset_index()


def main():
    t0 = time.time()
    ohlcv = load_1m_ohlcv("data/nq_1m_2018_2026.csv.gz")
    vxn = load_vxn_daily("data/vxn_daily.csv")
    print(f"data loaded in {time.time()-t0:.1f}s")

    rows = []
    for sl_pts in range(75, 126, 5):
        t1 = time.time()
        trades = simulate(ohlcv, vxn, sl_pts=sl_pts, params=NQ_PARAMS)
        stats = summary(trades)
        stats["sl_pts"] = sl_pts
        rows.append(stats)
        trades.to_csv(f"{OUT_DIR}/trades_sl{sl_pts}.csv", index=False)
        monthly_pnl(trades).to_csv(f"{OUT_DIR}/monthly_pnl_sl{sl_pts}.csv", index=False)
        print(f"sl_pts={sl_pts} done in {time.time()-t1:.1f}s -> {stats}")

    sweep = pd.DataFrame(rows)
    cols = ["sl_pts", "n", "net", "pf", "twr_pct", "tp", "sl", "be", "cutoff",
            "avg_win", "avg_loss", "max_dd"]
    sweep = sweep[cols]
    sweep.to_csv(f"{OUT_DIR}/sweep_summary.csv", index=False)
    print(sweep.to_string(index=False))
    print(f"total time {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
