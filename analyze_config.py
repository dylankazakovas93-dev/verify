"""
Post-hoc validation of a chosen ExecParams config: year-by-year P&L/PF
table over the FULL period (not just in/out-of-sample buckets), an
every-year-positive check, a year-over-year PF stability check (flag any
year whose PF falls outside mean +/- 2*std of the other years' PF -- a
signature of the config being overfit to that one year), and a
perturbation test (nudge each numeric knob +/-10% and +/-20% one at a
time, holding the rest fixed, and report how much in-sample/out-of-sample
net and PF move -- a real optimum degrades gently under perturbation; a
lucky spike falls off a cliff).

Usage:
    python3 analyze_config.py nq
    python3 analyze_config.py es
"""
from __future__ import annotations

import argparse
from dataclasses import replace

import numpy as np
import pandas as pd

from engine import (
    ExecParams, NQ_LEVEL_PARAMS, ES_LEVEL_PARAMS, build_touches, load_1m_ohlcv,
    load_vol_daily, apply_sal, score_touches, summarize, pf as pf_fn, maxdd as maxdd_fn,
)

IN_SAMPLE_YEARS = {2018, 2021, 2025}

INSTRUMENTS = {
    "nq": {"bars": "data/nq_1m_2018_2026.csv.gz", "vol": "data/vxn_daily.csv", "level": NQ_LEVEL_PARAMS},
    "es": {"bars": "data/es_1m_2018_2026.csv.gz", "vol": "data/vix_daily.csv", "level": ES_LEVEL_PARAMS},
}

PERTURB_FIELDS = ["cap_mult", "sl_mult", "tp_mult", "cap_ceiling", "be_bars", "trail_frac"]
PERTURB_PCTS = [-0.20, -0.10, 0.10, 0.20]


def year_table(kept: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for yr, sub in kept.groupby("year"):
        p = sub["pnl"]
        rows.append({
            "year": yr, "n": len(sub), "net": round(p.sum(), 1),
            "pf": round(pf_fn(p), 3) if (p > 0).any() and (p < 0).any() else None,
            "maxdd": round(maxdd_fn(p), 1),
            "in_sample": yr in IN_SAMPLE_YEARS,
        })
    return pd.DataFrame(rows).sort_values("year").reset_index(drop=True)


def check_all_years_positive(tbl: pd.DataFrame) -> list[int]:
    return tbl.loc[tbl["net"] <= 0, "year"].tolist()


def check_pf_stability(tbl: pd.DataFrame) -> pd.DataFrame:
    pfs = tbl.dropna(subset=["pf"]).set_index("year")["pf"]
    rows = []
    for yr, val in pfs.items():
        others = pfs.drop(yr)
        mu, sigma = others.mean(), others.std(ddof=1)
        z = (val - mu) / sigma if sigma > 0 else 0.0
        rows.append({"year": yr, "pf": val, "other_years_mean_pf": round(mu, 3),
                      "other_years_std_pf": round(sigma, 3), "z": round(z, 2),
                      "flag_2std": abs(z) > 2})
    return pd.DataFrame(rows)


def perturb(touches, bars, base: ExecParams) -> pd.DataFrame:
    rows = []
    base_r = score_and_split(touches, bars, base)
    rows.append({"field": "BASE", "pct": 0.0, "value": None, **base_r})
    for field in PERTURB_FIELDS:
        val = getattr(base, field)
        if val is None or val == 0:
            continue
        for pct in PERTURB_PCTS:
            new_val = val * (1 + pct)
            if field == "be_bars":
                new_val = max(1, round(new_val))
            exe = replace(base, **{field: new_val})
            r = score_and_split(touches, bars, exe)
            rows.append({"field": field, "pct": pct, "value": new_val, **r})
    return pd.DataFrame(rows)


def score_and_split(touches, bars, exe: ExecParams) -> dict:
    scored = score_touches(touches, bars, exe)
    kept = apply_sal(scored)
    ins = kept[kept["year"].isin(IN_SAMPLE_YEARS)]
    oos = kept[~kept["year"].isin(IN_SAMPLE_YEARS)]
    ins_s, oos_s = summarize(ins), summarize(oos)
    return {"in_net": ins_s["net"], "in_pf": ins_s["pf"], "in_maxdd": ins_s["maxdd"],
            "oos_net": oos_s["net"], "oos_pf": oos_s["pf"], "oos_maxdd": oos_s["maxdd"]}


def parse_winner_file(path: str) -> ExecParams:
    with open(path) as f:
        line = f.readline().strip()
    # line looks like: ExecParams(cap_mult=2.5, sl_mult=7.5, ...)
    inner = line[line.index("(") + 1: line.rindex(")")]
    kwargs = {}
    for part in inner.split(", "):
        k, v = part.split("=", 1)
        if v == "None":
            kwargs[k] = None
        elif v.startswith("'"):
            kwargs[k] = v.strip("'")
        else:
            kwargs[k] = float(v) if "." in v else int(v)
    return ExecParams(**kwargs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("instrument", choices=sorted(INSTRUMENTS))
    ap.add_argument("--config", help="path to grid_winner_<inst>.txt (defaults to results/grid_winner_<inst>.txt)")
    args = ap.parse_args()

    cfg = INSTRUMENTS[args.instrument]
    winner_path = args.config or f"results/grid_winner_{args.instrument}.txt"
    exe = parse_winner_file(winner_path)
    print(f"config under test: {exe}\n")

    bars = load_1m_ohlcv(cfg["bars"])
    vol = load_vol_daily(cfg["vol"])
    touches, bars = build_touches(bars, vol, cfg["level"])

    scored = score_touches(touches, bars, exe)
    kept = apply_sal(scored)

    tbl = year_table(kept)
    print("=== year-by-year ===")
    print(tbl.to_string(index=False))

    neg_years = check_all_years_positive(tbl)
    print(f"\nall-years-positive check: {'PASS' if not neg_years else 'FAIL -- negative net in ' + str(neg_years)}")

    stab = check_pf_stability(tbl)
    print("\n=== year-over-year PF stability (z vs other years) ===")
    print(stab.to_string(index=False))
    flagged = stab.loc[stab["flag_2std"], "year"].tolist()
    print(f"\n2-std PF stability check: {'PASS' if not flagged else 'FLAG -- ' + str(flagged) + ' outside 2std of other years'}")

    print("\n=== perturbation test (+/-10%/20% each field, one at a time) ===")
    pert = perturb(touches, bars, exe)
    print(pert.to_string(index=False))

    tbl.to_csv(f"results/year_table_{args.instrument}.csv", index=False)
    stab.to_csv(f"results/pf_stability_{args.instrument}.csv", index=False)
    pert.to_csv(f"results/perturbation_{args.instrument}.csv", index=False)
    print(f"\nwrote results/year_table_{args.instrument}.csv, "
          f"results/pf_stability_{args.instrument}.csv, results/perturbation_{args.instrument}.csv")


if __name__ == "__main__":
    main()
