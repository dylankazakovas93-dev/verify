"""
Rolling walk-forward validation: retrain (full 5-stage search) on a
trailing 1-year window, test on the next 6 months never seen by that
search, then roll both windows forward by 6 months and repeat across the
full 2018-2026 history. This is a much stronger check than the single
2018/2021/2025 in-sample split -- it asks whether *continuously
retraining* this way would have actually worked walking forward through
real time, not just whether one arbitrary 3-year slice looked good.

Usage:
    python3 walkforward.py nq
    python3 walkforward.py es
"""
from __future__ import annotations

import time

import pandas as pd

from engine import (
    NQ_LEVEL_PARAMS, ES_LEVEL_PARAMS, build_touches, load_1m_ohlcv, load_vol_daily,
    apply_sal, score_touches, summarize_extended,
)
from grid_search import staged_search, INSTRUMENTS

TRAIN_MONTHS = 12
TEST_MONTHS = 6
STEP_MONTHS = 6


def make_windows(start, end):
    windows = []
    train_start = pd.Timestamp(start)
    while True:
        train_end = train_start + pd.DateOffset(months=TRAIN_MONTHS)
        test_end = train_end + pd.DateOffset(months=TEST_MONTHS)
        if test_end > end:
            break
        windows.append((train_start, train_end, test_end))
        train_start = train_start + pd.DateOffset(months=STEP_MONTHS)
    return windows


def run(name: str) -> None:
    cfg = INSTRUMENTS[name]
    print(f"\n{'='*70}\n{name.upper()} walk-forward: train {TRAIN_MONTHS}mo -> test {TEST_MONTHS}mo, step {STEP_MONTHS}mo\n{'='*70}")
    t0 = time.time()
    bars = load_1m_ohlcv(cfg["bars"])
    vol = load_vol_daily(cfg["vol"])
    touches, bars = build_touches(bars, vol, cfg["level"])
    print(f"touches built: {len(touches)}  ({time.time()-t0:.1f}s)")

    start = touches["touched_at"].min().normalize()
    end = touches["touched_at"].max()
    windows = make_windows(start, end)
    print(f"{len(windows)} windows, {start.date()} .. {end.date()}")

    rows = []
    all_test_trades = []
    for i, (tr_start, tr_end, te_end) in enumerate(windows):
        tw = time.time()
        train = touches[(touches["touched_at"] >= tr_start) & (touches["touched_at"] < tr_end)]
        test = touches[(touches["touched_at"] >= tr_end) & (touches["touched_at"] < te_end)]
        if len(train) < 20 or test.empty:
            continue

        exe = staged_search(train, bars, cfg["cap_ceilings"], verbose=False)
        test_kept = apply_sal(score_touches(test, bars, exe))
        s = summarize_extended(test_kept, years_spanned=TEST_MONTHS / 12)
        rows.append({
            "window": i, "train_start": tr_start.date(), "train_end": tr_end.date(),
            "test_end": te_end.date(), "cap_mult": exe.cap_mult, "sl_mult": round(exe.sl_mult, 2),
            "tp_mult": round(exe.tp_mult, 2), "cap_ceiling": exe.cap_ceiling,
            "be_bars": exe.be_bars, "be_mechanic": exe.be_mechanic, "trail_frac": exe.trail_frac,
            "round_tol": exe.round_tol, "round_size_mult": exe.round_size_mult,
            **{k: s[k] for k in ("n", "net", "pf", "maxdd", "twr", "outright_wr", "sharpe")},
        })
        if not test_kept.empty:
            all_test_trades.append(test_kept)
        print(f"  window {i}: train {tr_start.date()}..{tr_end.date()}  test ..{te_end.date()}  "
              f"n={s['n']} net={s['net']} pf={s['pf']}  ({time.time()-tw:.1f}s)")

    log = pd.DataFrame(rows)
    log.to_csv(f"results/walkforward_{name}.csv", index=False)

    if all_test_trades:
        chained = pd.concat(all_test_trades, ignore_index=True).sort_values("touched_at")
        chained["cum_pnl"] = chained["pnl"].cumsum()
        chained.to_csv(f"results/walkforward_{name}_trades.csv", index=False)
        full = summarize_extended(chained, years_spanned=(chained["touched_at"].max() - chained["touched_at"].min()).days / 365.25)
        print(f"\nchained OOS-only equity (every test window back to back, never re-using a trained window's own trades):")
        print(full)
        neg_windows = log[log["net"] <= 0]
        print(f"\nwindows with negative test-period net: {len(neg_windows)} / {len(log)}")
        if not neg_windows.empty:
            print(neg_windows[["window", "train_start", "test_end", "n", "net", "pf"]].to_string(index=False))

    print(f"\ntotal time: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    import sys
    insts = sys.argv[1:] or list(INSTRUMENTS)
    for name in insts:
        run(name)
