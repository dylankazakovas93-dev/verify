"""
Shared audit harness for the NQ level-fade final-validation project.

Loads bars + vol once, builds the touch set once (expensive stage), and
provides two scoring paths:

  score_original  -- byte-for-byte the shipped engine: round-number size
                     multiplier is folded into `pnl` BEFORE apply_sal, so
                     sizing can perturb which trades survive SAL.
  score_decoupled -- the corrected model mandated by the audit spec:
                     SAL depends only on the RAW per-contract pnl sign and
                     exit class; the size multiplier is carried in a
                     separate column and never touches eligibility.

Data lives on the repro branch; point AUDIT_DATA at its data/ dir.
"""
from __future__ import annotations
import os, math
import numpy as np, pandas as pd

import engine as E
from engine import (ExecParams, NQ_LEVEL_PARAMS, build_touches, load_1m_ohlcv,
                    load_vol_daily, sim_trade, near_round_number, pf, maxdd)

DATA = os.environ.get("AUDIT_DATA",
    "/tmp/claude-0/-home-user-verify/0bbb5195-16bc-5b21-932e-e050f813481b/scratchpad/repro/data")
NQ_BARS = f"{DATA}/nq_1m_2018_2026.csv.gz"
VXN     = f"{DATA}/vxn_daily.csv"

WINNER = ExecParams(cap_mult=4.0, sl_mult=12.0, tp_mult=5.0, cap_ceiling=300,
                    be_bars=150, be_mechanic='close_cross', be_lock_frac=0.0,
                    trail_frac=0.0, round_step=50.0, round_tol=2, round_size_mult=2.0)
IN_SAMPLE = {2018, 2021, 2025}

_CACHE = {}
def get_touches():
    if "t" not in _CACHE:
        bars = load_1m_ohlcv(NQ_BARS)
        vol  = load_vol_daily(VXN)
        touches, bars = build_touches(bars, vol, NQ_LEVEL_PARAMS)
        _CACHE["t"] = (touches, bars)
    return _CACHE["t"]


def raw_score(touches, bars, exe):
    """One row per touch: raw single-contract pnl + exit + size_mult (kept separate)."""
    rows = []
    for r in touches.itertuples(index=False):
        pnl, ex, exit_time, held = sim_trade(bars, r.touched_at, r.path_end,
                                              r.level, r.sign, r.anchor, exe)
        sm = exe.round_size_mult if near_round_number(r.level, exe.round_step, exe.round_tol) else 1.0
        rows.append({"sess_date": r.sess_date, "year": r.year, "side": r.side,
                     "touched_at": r.touched_at, "level": r.level, "anchor": r.anchor,
                     "raw_pnl": pnl, "size_mult": sm, "exit": ex,
                     "exit_time": exit_time, "bars_held": held,
                     "hold_min": (exit_time - r.touched_at).total_seconds()/60.0})
    return pd.DataFrame(rows)


def _sal(df, key_col):
    """Generic SAL: first real loss (by key_col sign, exit!='BE') blocks the session."""
    kept = []
    for _, day in df.sort_values("touched_at", kind="stable").groupby("sess_date"):
        lost = False
        for _, row in day.iterrows():
            if not lost:
                kept.append(row.to_dict())
                if row[key_col] < -0.1 and row["exit"] != "BE":
                    lost = True
    return pd.DataFrame(kept).sort_values("touched_at").reset_index(drop=True)


def score_original(touches, bars, exe):
    """Shipped behaviour: SAL sees the SIZED pnl."""
    df = raw_score(touches, bars, exe).copy()
    df["pnl"] = df["raw_pnl"] * df["size_mult"]      # sized, as engine does
    kept = _sal(df, "pnl")
    return kept

def score_decoupled(touches, bars, exe):
    """Corrected: SAL sees RAW pnl; sizing carried separately as weighted_pnl."""
    df = raw_score(touches, bars, exe).copy()
    kept = _sal(df, "raw_pnl")                        # eligibility from raw only
    kept["weighted_pnl"] = kept["raw_pnl"] * kept["size_mult"]
    return kept
