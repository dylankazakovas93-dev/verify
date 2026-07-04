"""
Reproducible entry point for the NQ level-fade backtest.

Usage:
    python run_backtest.py path/to/nq_1m.csv [--sl-min 75] [--sl-max 125] [--sl-step 5]

Requires the NQ 1-minute OHLCV CSV (not committed to this repo — provided
separately). VXN daily data lives at data/vxn_daily.csv.
"""
from __future__ import annotations

import argparse

import pandas as pd

from levels import load_1m_ohlcv, load_vxn_daily, NQ_PARAMS
from simulate import simulate, summary

VXN_PATH = "data/vxn_daily.csv"


def monthly_pnl_table(trades: pd.DataFrame) -> pd.DataFrame:
    df = trades.copy()
    df["month"] = pd.to_datetime(df["exit_time"]).dt.to_period("M")
    return df.groupby("month")["pnl"].sum().round(2)


def run_sweep(nq_path: str, sl_min: int, sl_max: int, sl_step: int) -> None:
    ohlcv = load_1m_ohlcv(nq_path)
    vxn = load_vxn_daily(VXN_PATH)

    for sl_pts in range(sl_min, sl_max + 1, sl_step):
        trades = simulate(ohlcv, vxn, sl_pts=sl_pts, params=NQ_PARAMS)
        stats = summary(trades)
        print(f"\n=== sl_pts={sl_pts} ===")
        print(stats)
        if not trades.empty:
            print("\nMonthly net P&L:")
            print(monthly_pnl_table(trades).to_string())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("nq_csv", help="path to NQ 1-minute OHLCV CSV")
    parser.add_argument("--sl-min", type=int, default=75)
    parser.add_argument("--sl-max", type=int, default=125)
    parser.add_argument("--sl-step", type=int, default=5)
    args = parser.parse_args()

    run_sweep(args.nq_csv, args.sl_min, args.sl_max, args.sl_step)
