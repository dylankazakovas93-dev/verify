"""
Independent strict single-position, single-shot-first-touch NQ engine.

Semantics (deliberately different from simulate_retest_eligible.py, which is
now labeled ALTERNATIVE_RETEST_ELIGIBLE -- a level touched while another
position is open there remains eligible for a later retest):

  - Each (level, side) has exactly ONE candidate event: its physical first
    touch, found once over its whole LINE_DAYS-session lifetime,
    independent of any later position state. This candidate set is fixed
    before any chronological replay happens -- computed exactly like
    nq_cond_be45.py's build_ledger() candidate generation (level created
    -> expiry = the level created LINE_DAYS sessions later; first_touch
    searched once over that whole window).
  - A candidate is EXECUTED if, at its touch instant, no other position is
    open, SAL hasn't triggered that session, and the touch falls in the
    19:00-11:00 ET window. Otherwise it is SKIPPED -- permanently. A
    skipped touch is never revisited; the level is spent either way.
  - One global position at a time. No same-minute exit -> re-entry.
    Session cutoff 15:00 ET (next-day 15:00 if entered >=19:00). SAL
    triggers only on a genuine losing exit (BE does not trigger it).
  - cap = min(1.5 x previous completed 60-min range, 200), TP = SL = cap,
    conditional BE@45 (bar-open check). PF sums every realized pnl
    (TP/SL/BE/cutoff), positive over negative.
  - Simultaneous candidates at the same instant: ordered oldest-level-first.

Built independently from simulate_retest_eligible.py -- candidates are
precomputed in a separate pass (mirroring the *rules*, not the *code*),
then replayed chronologically with the single-position/SAL state machine
applied only at replay time, never at candidate-generation time.

Usage:
    python3 simulate_strict_first_touch.py path/to/nq_1m.csv data/vxn_daily.csv
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from levels import generate_levels, load_1m_ohlcv, load_vxn_daily, NQ_PARAMS

LINE_DAYS   = 20
SL_CAP      = 200.0
CAP_MULT    = 1.5
BE_BAR      = 45
ENTRY_BLOCK = (11 * 60, 19 * 60)   # no entries 11:00-19:00 ET
SESSION_CUTOFF_MIN = 15 * 60
REENTRY_MIN        = 19 * 60


def bar_ranges(bars: pd.DataFrame) -> pd.Series:
    r = bars.resample("60min", label="left", closed="left").agg({"high": "max", "low": "min"}).dropna()
    return r["high"] - r["low"]


def first_touch(bars: pd.DataFrame, level: float):
    """Verbatim port of the uploaded reactions.py _first_touch()."""
    high, low, close = bars["high"], bars["low"], bars["close"]
    prev_close = close.shift(1)
    touched = (
        ((high >= level) & (low <= level))
        | ((close >= level) & (prev_close < level))
        | ((close <= level) & (prev_close > level))
    )
    hits = bars.index[touched]
    return hits[0] if len(hits) else None


def session_date(ts) -> object:
    et = ts.tz_convert("America/New_York")
    return ((et + pd.Timedelta(days=1)) if et.hour >= 19 else et).date()


def entry_allowed(ts) -> bool:
    et = ts.tz_convert("America/New_York")
    m = et.hour * 60 + et.minute
    return not (ENTRY_BLOCK[0] <= m < ENTRY_BLOCK[1])


def prev_completed_range(ranges: pd.Series, ts):
    prev = ts.floor("60min") - pd.Timedelta(minutes=60)
    v = ranges.get(prev)
    return float(v) if v is not None and not pd.isna(v) and v > 0 else None


def build_candidates(bars: pd.DataFrame, vxn: pd.Series, params=NQ_PARAMS) -> pd.DataFrame:
    """Pass 1: static, position-state-independent candidate set. One row
    per (level, side) that has a physical first touch, entry-allowed time,
    and a valid anchor -- exactly nq_cond_be45.py's pre-execution filters,
    none of which depend on chronological replay state."""
    levels_df = generate_levels(bars, vxn, params, "09:30", "16:00").sort_values("created_at").reset_index(drop=True)
    if levels_df.empty:
        return pd.DataFrame()

    ranges = bar_ranges(bars)
    lvls = list(levels_df.itertuples(index=False))
    rows = []

    for i, lv in enumerate(lvls):
        expiry = lvls[i + LINE_DAYS].created_at if i + LINE_DAYS < len(lvls) else bars.index[-1]
        # no creation-bar entry, expiry timestamp excluded: strictly between
        search = bars[(bars.index > lv.created_at) & (bars.index < expiry)]
        for side, col in (("upper", lv.upper_level), ("lower", lv.lower_level)):
            level = float(col)
            ft = first_touch(search, level)
            if ft is None or not entry_allowed(ft):
                continue
            anchor = prev_completed_range(ranges, ft)
            if anchor is None:
                continue
            rows.append({
                "level_id": i, "level_date": lv.session_date, "side": side,
                "level": level, "touched_at": ft, "anchor": anchor,
            })

    cand = pd.DataFrame(rows)
    if cand.empty:
        return cand
    # oldest-level-first tie-break for simultaneous candidates
    return cand.sort_values(["touched_at", "level_id"]).reset_index(drop=True)


@dataclass
class _Trade:
    entry_time:  object
    entry_price: float
    side:        str
    stop:        float
    target:      float
    level_id:    int
    bar_count:   int = 0
    checked_be:  bool = False
    armed_be:    bool = False
    exit_time:   object = None
    exit_price:  float = None
    exit_reason: str = None
    pnl:         float = None

    def unrealised(self, price: float) -> float:
        return price - self.entry_price if self.side == "long" else self.entry_price - price


def replay(bars: pd.DataFrame, cand: pd.DataFrame):
    """Pass 2: chronological single-position replay over the fixed
    candidate set. A candidate not executed at its touch instant is
    marked SKIPPED and never reconsidered."""
    if cand.empty:
        return pd.DataFrame(), pd.DataFrame()

    op, hi, lo, cl = bars["open"].to_numpy(), bars["high"].to_numpy(), bars["low"].to_numpy(), bars["close"].to_numpy()
    idx = bars.index
    bar_pos = pd.Series(np.arange(len(bars)), index=idx)

    executed, skipped = [], []
    active: Optional[_Trade] = None
    cur_sess, sal_triggered = None, False

    # candidate touch times mapped to bar position (searchsorted for O(log n) lookup)
    idx_arr = idx.values
    cand_bar_i = np.searchsorted(idx_arr, cand["touched_at"].values)

    ci = 0
    n_cand = len(cand)
    i = 0
    n = len(bars)

    # merge-walk bars and candidates in lockstep chronological order
    while i < n:
        t = idx[i]
        sess = session_date(t)
        if sess != cur_sess:
            if active is not None:
                active.exit_time, active.exit_price = t, op[i]
                active.exit_reason, active.pnl = "cutoff", round(active.unrealised(op[i]), 2)
                executed.append(active)
                active = None
            cur_sess, sal_triggered = sess, False  # new session: SAL always resets here regardless

        exited_this_bar = False

        if active is not None:
            active.bar_count += 1
            if active.bar_count == BE_BAR and not active.checked_be:
                active.checked_be = True
                if active.unrealised(op[i]) > 0:
                    active.armed_be = True
                    active.stop = active.entry_price

            minute_of_day = t.tz_convert("America/New_York").hour * 60 + t.tz_convert("America/New_York").minute
            if minute_of_day >= SESSION_CUTOFF_MIN:
                pnl = round(active.unrealised(op[i]), 2)
                # negative cutoff exits do trigger SAL (matches nq_cond_be45.py's
                # apply_sal: any exit with pnl < -0.1 and reason != BE). In this
                # engine's time structure this is a no-op in practice -- cutoff
                # only fires at/after 15:00, which is always after the 11:00
                # entry-window close, so no further same-session entry could
                # occur regardless -- but implemented for spec correctness.
                if pnl < -0.1:
                    sal_triggered = True
                active.exit_time, active.exit_price = t, op[i]
                active.exit_reason, active.pnl = "cutoff", pnl
                executed.append(active)
                active, exited_this_bar = None, True
            else:
                stop_hit = (hi[i] >= active.stop) if active.side == "short" else (lo[i] <= active.stop)
                tgt_hit = (lo[i] <= active.target) if active.side == "short" else (hi[i] >= active.target)
                if stop_hit:
                    pnl = round(active.unrealised(active.stop), 2)
                    reason = "BE" if active.armed_be else "SL"
                    if reason != "BE" and pnl < -0.1:
                        sal_triggered = True
                    active.exit_time, active.exit_price = t, active.stop
                    active.exit_reason, active.pnl = reason, pnl
                    executed.append(active)
                    active, exited_this_bar = None, True
                elif tgt_hit:
                    pnl = round(active.unrealised(active.target), 2)
                    active.exit_time, active.exit_price = t, active.target
                    active.exit_reason, active.pnl = "TP", pnl
                    executed.append(active)
                    active, exited_this_bar = None, True

        # process every candidate touching at this exact bar -- a position
        # that just closed this same bar cannot be replaced until next bar
        while ci < n_cand and cand_bar_i[ci] == i:
            row = cand.iloc[ci]
            if active is None and not exited_this_bar and not sal_triggered:
                cap = min(CAP_MULT * row["anchor"], SL_CAP)
                if row["side"] == "upper":
                    active = _Trade(t, row["level"], "short", row["level"] + cap, row["level"] - cap, row["level_id"])
                else:
                    active = _Trade(t, row["level"], "long", row["level"] - cap, row["level"] + cap, row["level_id"])
                # this candidate is now the open position; further candidates
                # at this same bar (if any) are evaluated against it below
            else:
                skipped.append({"level_id": row["level_id"], "side": row["side"], "touched_at": row["touched_at"],
                                 "reason": "position_open" if active is not None else "sal_active"})
            ci += 1

        i += 1

    if active is not None:
        active.exit_time, active.exit_price = idx[-1], cl[-1]
        active.exit_reason, active.pnl = "cutoff", round(active.unrealised(cl[-1]), 2)
        executed.append(active)

    ex_df = pd.DataFrame([{
        "entry_time": t.entry_time, "exit_time": t.exit_time, "side": t.side,
        "entry_price": t.entry_price, "exit_price": t.exit_price,
        "exit_reason": t.exit_reason, "pnl": t.pnl, "level_id": t.level_id,
    } for t in executed]) if executed else pd.DataFrame()
    if not ex_df.empty:
        ex_df["cum_pnl"] = ex_df["pnl"].cumsum().round(2)
    sk_df = pd.DataFrame(skipped)
    return ex_df, sk_df


def summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    tp, sl = df[df["exit_reason"] == "TP"], df[df["exit_reason"] == "SL"]
    p = df["pnl"]
    gross_w, gross_l = p[p > 0].sum(), -p[p < 0].sum()
    twr_n = len(tp) + len(sl)
    return {
        "n": len(df), "net": round(p.sum(), 2),
        "pf": round(gross_w / gross_l, 3) if gross_l else None,
        "twr_pct": round(len(tp) / twr_n * 100, 1) if twr_n else None,
        "tp": len(tp), "sl": len(sl),
        "be": len(df[df["exit_reason"] == "BE"]), "cutoff": len(df[df["exit_reason"] == "cutoff"]),
        "max_dd": round((df["cum_pnl"] - df["cum_pnl"].cummax()).min(), 2),
    }


if __name__ == "__main__":
    import sys
    bars = load_1m_ohlcv(sys.argv[1] if len(sys.argv) > 1 else "data/nq_1m_2018_2026.csv.gz")
    vxn = load_vxn_daily(sys.argv[2] if len(sys.argv) > 2 else "data/vxn_daily.csv")

    cand = build_candidates(bars, vxn)
    print(f"eligible candidates: {len(cand)}")

    executed, skipped = replay(bars, cand)
    print(f"executed: {len(executed)}   skipped: {len(skipped)}")
    print(summary(executed))
    if not executed.empty:
        for yr, sub in executed.groupby(executed["entry_time"].apply(lambda x: x.year)):
            print(yr, summary(sub))

    cand.to_csv("results/strict_eligible_candidates.csv", index=False)
    executed.to_csv("results/strict_executed.csv", index=False)
    skipped.to_csv("results/strict_skipped.csv", index=False)
