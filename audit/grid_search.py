"""
Staged cheap-tier grid search over ExecParams, per instrument.

Full factorial across every knob (cap_mult x cap_ceiling x sl/tp ratios x
be_bars x be_mechanic x trail_frac x round-number boost) would be tens of
thousands of combos. Instead this runs coordinate-descent-style stages,
each holding prior stages' winners fixed and searching one axis:

  1. risk sizing      : cap_mult x cap_ceiling (symmetric baseline)
  2. asymmetric R:R    : sl_mult / tp_mult as ratios off the stage-1 base
  3. BE mechanics/time : be_bars x be_mechanic
  4. trailing stop     : trail_frac
  5. round-number size : round_tol x round_size_mult

`staged_search()` runs this against whatever touch set it's handed --
used both for the single 2018/2021/2025 in-sample split (run_instrument,
below) and for each window of the rolling walk-forward
(walkforward.py), so the same search logic backs both.

NQ and ES are run independently start to finish -- winners from one are
never applied to the other.

DD-aware objective: the first pass at this (see git history) ranked purely
on in-sample net/PF, which walked every axis to the edge of its searched
range (more risk mechanically buys more net/PF with no penalty) and blew
up drawdown 4x for a marginal profit gain. This version computes the true
baseline's in-sample maxDD once (ExecParams() locked defaults) and rejects
any candidate whose in-sample maxDD exceeds MAX_DD_MULT x that baseline,
before ranking survivors on net/PF.
"""
from __future__ import annotations

import itertools
import time
from dataclasses import replace

import pandas as pd

from engine import (
    ExecParams, NQ_LEVEL_PARAMS, ES_LEVEL_PARAMS, build_touches, load_1m_ohlcv,
    load_vol_daily, apply_sal, score_touches, summarize,
)

IN_SAMPLE_YEARS = {2018, 2021, 2025}
MAX_DD_MULT = 1.5  # candidate's in-sample |maxDD| may not exceed this x the true baseline's

INSTRUMENTS = {
    "nq": {"bars": "data/nq_1m_2018_2026.csv.gz", "vol": "data/vxn_daily.csv",
           "level": NQ_LEVEL_PARAMS, "cap_ceilings": [100, 150, 200, 250, 300, 400, 500, 600]},
    "es": {"bars": "data/es_1m_2018_2026.csv.gz", "vol": "data/vix_daily.csv",
           "level": ES_LEVEL_PARAMS, "cap_ceilings": [15, 20, 25, 30, 40, 50, 75, 100, 150]},
}

CAP_MULTS = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0]
RR_RATIOS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
BE_BARS_GRID = [15, 30, 45, 60, 90, 120, 150, 180]
BE_MECHANICS = ["open_cross", "close_cross"]
TRAIL_FRACS = [0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 0.75]
ROUND_TOLS = [0, 1, 2, 3, 5, 8, 12, 15, 20]
ROUND_MULTS = [1.25, 1.5, 2.0, 3.0, 4.0]


def make_rank_key(baseline_maxdd: float):
    dd_budget = MAX_DD_MULT * abs(baseline_maxdd)

    def rank_key(s: dict) -> tuple:
        dd = abs(s["maxdd"])
        pf = s["pf"] if s["pf"] is not None else 0.0
        pf = pf if pf != float("inf") else 1e9
        within_budget = dd <= dd_budget
        return (within_budget, s["net"], pf)
    return rank_key


def staged_search(train_touches: pd.DataFrame, bars: pd.DataFrame, cap_ceilings: list,
                   verbose: bool = True, log_rows: list | None = None) -> ExecParams:
    """Run all 5 stages against train_touches only, return the winning ExecParams."""
    def score(exe: ExecParams) -> dict:
        return summarize(apply_sal(score_touches(train_touches, bars, exe)))

    base = ExecParams()
    baseline_maxdd = score(base)["maxdd"]
    rank_key = make_rank_key(baseline_maxdd)
    if verbose:
        print(f"train baseline maxDD: {baseline_maxdd}  budget={MAX_DD_MULT}x="
              f"{MAX_DD_MULT*abs(baseline_maxdd):.1f}  (n_train={len(train_touches)})")

    # stage 1: risk sizing
    best_key, best_exe = None, None
    for mult, ceil in itertools.product(CAP_MULTS, cap_ceilings):
        exe = replace(base, cap_mult=mult, cap_ceiling=ceil)
        s = score(exe)
        if log_rows is not None:
            log_rows.append({"stage": 1, "cap_mult": mult, "cap_ceiling": ceil, **s})
        k = rank_key(s)
        if best_key is None or k > best_key:
            best_key, best_exe = k, exe
    if verbose:
        print(f"  stage1: cap_mult={best_exe.cap_mult} cap_ceiling={best_exe.cap_ceiling}")
    base = best_exe

    # stage 2: asymmetric R:R
    best_key, best_exe = None, None
    for sl_r, tp_r in itertools.product(RR_RATIOS, RR_RATIOS):
        exe = replace(base, sl_mult=base.cap_mult * sl_r, tp_mult=base.cap_mult * tp_r)
        s = score(exe)
        if log_rows is not None:
            log_rows.append({"stage": 2, "sl_ratio": sl_r, "tp_ratio": tp_r, **s})
        k = rank_key(s)
        if best_key is None or k > best_key:
            best_key, best_exe = k, exe
    if verbose:
        print(f"  stage2: sl_mult={best_exe.sl_mult:.3f} tp_mult={best_exe.tp_mult:.3f}")
    base = best_exe

    # stage 3: BE mechanics/timing
    best_key, best_exe = None, None
    for bb, mech in itertools.product(BE_BARS_GRID, BE_MECHANICS):
        exe = replace(base, be_bars=bb, be_mechanic=mech)
        s = score(exe)
        if log_rows is not None:
            log_rows.append({"stage": 3, "be_bars": bb, "be_mechanic": mech, **s})
        k = rank_key(s)
        if best_key is None or k > best_key:
            best_key, best_exe = k, exe
    if verbose:
        print(f"  stage3: be_bars={best_exe.be_bars} be_mechanic={best_exe.be_mechanic}")
    base = best_exe

    # stage 4: trailing stop
    best_key, best_exe = None, None
    for tf in TRAIL_FRACS:
        exe = replace(base, trail_frac=tf)
        s = score(exe)
        if log_rows is not None:
            log_rows.append({"stage": 4, "trail_frac": tf, **s})
        k = rank_key(s)
        if best_key is None or k > best_key:
            best_key, best_exe = k, exe
    if verbose:
        print(f"  stage4: trail_frac={best_exe.trail_frac}")
    base = best_exe

    # stage 5: round-number size boost
    best_exe = base
    best_key = rank_key(score(base))
    for tol, mult in itertools.product(ROUND_TOLS, ROUND_MULTS):
        if tol == 0:
            continue
        exe = replace(base, round_tol=tol, round_size_mult=mult)
        s = score(exe)
        if log_rows is not None:
            log_rows.append({"stage": 5, "round_tol": tol, "round_size_mult": mult, **s})
        k = rank_key(s)
        if k > best_key:
            best_key, best_exe = k, exe
    if verbose:
        print(f"  stage5: round_tol={best_exe.round_tol} round_size_mult={best_exe.round_size_mult}")

    return best_exe


def run_instrument(name: str, cfg: dict) -> None:
    print(f"\n{'='*70}\n{name.upper()}\n{'='*70}")
    t0 = time.time()
    bars = load_1m_ohlcv(cfg["bars"])
    vol = load_vol_daily(cfg["vol"])
    touches, bars = build_touches(bars, vol, cfg["level"])
    print(f"touches built: {len(touches)}  ({time.time()-t0:.1f}s)")

    train_touches = touches[touches["year"].isin(IN_SAMPLE_YEARS)]
    log_rows: list = []
    best_exe = staged_search(train_touches, bars, cfg["cap_ceilings"], verbose=True, log_rows=log_rows)

    kept_all = apply_sal(score_touches(touches, bars, best_exe))
    ins = kept_all[kept_all["year"].isin(IN_SAMPLE_YEARS)]
    oos = kept_all[~kept_all["year"].isin(IN_SAMPLE_YEARS)]
    final = {"in": summarize(ins), "oos": summarize(oos), "all": summarize(kept_all)}

    print(f"\nFINAL config for {name.upper()}: {best_exe}")
    print(f"in-sample : {final['in']}")
    print(f"out-of-sample: {final['oos']}")
    print(f"all years : {final['all']}")

    pd.DataFrame(log_rows).to_csv(f"results/grid_log_{name}.csv", index=False)
    with open(f"results/grid_winner_{name}.txt", "w") as f:
        f.write(f"{best_exe}\nin-sample: {final['in']}\nout-of-sample: {final['oos']}\nall: {final['all']}\n")
    print(f"total time: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    import sys
    insts = sys.argv[1:] or list(INSTRUMENTS)
    for name in insts:
        run_instrument(name, INSTRUMENTS[name])
