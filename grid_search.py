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

Touches are built once per instrument (expensive stage, level-gen fixed at
that instrument's current config) and reused across every combo in every
stage. Scored on IN_SAMPLE_YEARS only; out-of-sample is reported for the
winner of each stage for a generalization sanity check, not used to pick
the winner.

NQ and ES are run independently start to finish -- winners from one are
never applied to the other.

DD-aware objective: the first pass at this (see git history) ranked purely
on in-sample net/PF, which walked every axis to the edge of its searched
range (more risk mechanically buys more net/PF with no penalty) and blew
up drawdown 4x for a marginal profit gain. This version computes the true
baseline's in-sample maxDD once (ExecParams() locked defaults) and rejects
any candidate whose in-sample maxDD exceeds MAX_DD_MULT x that baseline,
before ranking survivors on net/PF. Ranges are also widened past every
value that got edge-clipped last time so a winner mid-range means an
actual optimum, not a wall.
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


def score(touches, bars, exe: ExecParams) -> dict:
    scored = score_touches(touches, bars, exe)
    kept = apply_sal(scored)
    ins = kept[kept["year"].isin(IN_SAMPLE_YEARS)]
    oos = kept[~kept["year"].isin(IN_SAMPLE_YEARS)]
    return {"in": summarize(ins), "oos": summarize(oos), "all": summarize(kept)}


def make_rank_key(baseline_maxdd: float):
    dd_budget = MAX_DD_MULT * abs(baseline_maxdd)

    def rank_key(r: dict) -> tuple:
        dd = abs(r["in"]["maxdd"])
        pf = r["in"]["pf"] if r["in"]["pf"] is not None else 0.0
        pf = pf if pf != float("inf") else 1e9
        within_budget = dd <= dd_budget
        # candidates blowing the DD budget always rank below any that don't,
        # regardless of net -- net/PF only break ties within the budget
        return (within_budget, r["in"]["net"], pf)
    return rank_key


def run_instrument(name: str, cfg: dict) -> None:
    print(f"\n{'='*70}\n{name.upper()}\n{'='*70}")
    t0 = time.time()
    bars = load_1m_ohlcv(cfg["bars"])
    vol = load_vol_daily(cfg["vol"])
    touches, bars = build_touches(bars, vol, cfg["level"])
    print(f"touches built: {len(touches)}  ({time.time()-t0:.1f}s)")

    base = ExecParams()  # starting point: current locked-engine defaults
    baseline_maxdd = score(touches, bars, base)["in"]["maxdd"]
    rank_key = make_rank_key(baseline_maxdd)
    print(f"true baseline in-sample maxDD: {baseline_maxdd}  "
          f"(DD budget for all candidates: {MAX_DD_MULT} x = {MAX_DD_MULT*abs(baseline_maxdd):.1f})")
    log_rows = []

    # ── stage 1: risk sizing ──────────────────────────────────────────────
    print("\n-- stage 1: cap_mult x cap_ceiling --")
    best, best_key = None, None
    for mult, ceil in itertools.product(CAP_MULTS, cfg["cap_ceilings"]):
        exe = replace(base, cap_mult=mult, cap_ceiling=ceil)
        r = score(touches, bars, exe)
        log_rows.append({"stage": 1, "cap_mult": mult, "cap_ceiling": ceil, **r["in"]})
        k = rank_key(r)
        if best_key is None or k > best_key:
            best_key, best, best_exe = k, r, exe
    print(f"winner: cap_mult={best_exe.cap_mult} cap_ceiling={best_exe.cap_ceiling}  "
          f"in={best['in']}  oos={best['oos']}")
    base = best_exe

    # ── stage 2: asymmetric R:R ────────────────────────────────────────────
    print("\n-- stage 2: sl_ratio x tp_ratio (off stage-1 base) --")
    best, best_key = None, None
    for sl_r, tp_r in itertools.product(RR_RATIOS, RR_RATIOS):
        exe = replace(base, sl_mult=base.cap_mult * sl_r, tp_mult=base.cap_mult * tp_r)
        r = score(touches, bars, exe)
        log_rows.append({"stage": 2, "sl_ratio": sl_r, "tp_ratio": tp_r, **r["in"]})
        k = rank_key(r)
        if best_key is None or k > best_key:
            best_key, best, best_exe = k, r, exe
    print(f"winner: sl_mult={best_exe.sl_mult:.3f} tp_mult={best_exe.tp_mult:.3f}  "
          f"in={best['in']}  oos={best['oos']}")
    base = best_exe

    # ── stage 3: BE mechanics/timing ───────────────────────────────────────
    print("\n-- stage 3: be_bars x be_mechanic --")
    best, best_key = None, None
    for bb, mech in itertools.product(BE_BARS_GRID, BE_MECHANICS):
        exe = replace(base, be_bars=bb, be_mechanic=mech)
        r = score(touches, bars, exe)
        log_rows.append({"stage": 3, "be_bars": bb, "be_mechanic": mech, **r["in"]})
        k = rank_key(r)
        if best_key is None or k > best_key:
            best_key, best, best_exe = k, r, exe
    print(f"winner: be_bars={best_exe.be_bars} be_mechanic={best_exe.be_mechanic}  "
          f"in={best['in']}  oos={best['oos']}")
    base = best_exe

    # ── stage 4: trailing stop ─────────────────────────────────────────────
    print("\n-- stage 4: trail_frac --")
    best, best_key = None, None
    for tf in TRAIL_FRACS:
        exe = replace(base, trail_frac=tf)
        r = score(touches, bars, exe)
        log_rows.append({"stage": 4, "trail_frac": tf, **r["in"]})
        k = rank_key(r)
        if best_key is None or k > best_key:
            best_key, best, best_exe = k, r, exe
    print(f"winner: trail_frac={best_exe.trail_frac}  in={best['in']}  oos={best['oos']}")
    base = best_exe

    # ── stage 5: round-number size boost ───────────────────────────────────
    print("\n-- stage 5: round_tol x round_size_mult --")
    best_exe = base
    best = score(touches, bars, base)
    best_key = rank_key(best)
    for tol, mult in itertools.product(ROUND_TOLS, ROUND_MULTS):
        if tol == 0:
            continue
        exe = replace(base, round_tol=tol, round_size_mult=mult)
        r = score(touches, bars, exe)
        log_rows.append({"stage": 5, "round_tol": tol, "round_size_mult": mult, **r["in"]})
        k = rank_key(r)
        if k > best_key:
            best_key, best, best_exe = k, r, exe
    print(f"winner: round_tol={best_exe.round_tol} round_size_mult={best_exe.round_size_mult}  "
          f"in={score(touches, bars, best_exe)['in']}  oos={score(touches, bars, best_exe)['oos']}")

    final = score(touches, bars, best_exe)
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
