"""
ALTERNATIVE_RETEST_ELIGIBLE -- single-position dynamic-cap level-fade
engine, retest-eligible variant. NOT the canonical first-touch strategy;
see simulate_strict_first_touch.py for that.

Rule that makes this a distinct variant, stated explicitly: a level/side
is only marked "used" once a trade actually opens from it. If its
physical first touch occurs while another position is already open, the
touch is simply not evaluated (the whole entry check is gated on
`active is None`) -- the level remains eligible and can still be entered
on a later retest. This is a legitimate, separate strategy, not a bug,
and is not comparable to the strict one-shot-per-level canonical baseline
without saying so explicitly.

Otherwise: cap = min(1.5 x prior completed 1h range, 200), TP = SL = cap,
conditional BE@45 via the bar-open check, 19:00-11:00 ET entry window
(11:00-19:00 blocked), session spans 19:00 ET -> next-day 15:00 ET, SAL
after the first real loss, no same-minute exit -> re-entry, PF sums every
realized pnl (TP/SL/BE/cutoff). Single active position at a time,
verified zero overlaps.

Uses this repo's own levels.py (NQ_PARAMS, generate_levels) and the touch
condition ported from the uploaded reactions.py's _first_touch().

Usage:
    python3 simulate_retest_eligible.py path/to/nq_1m.csv data/vxn_daily.csv
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from levels import generate_levels, load_1m_ohlcv, load_vxn_daily, NQ_PARAMS

LINE_DAYS      = 20
SL_CAP         = 200.0
CAP_MULT       = 1.5
BE_BAR         = 45
ENTRY_BLOCK    = (11 * 60, 19 * 60)   # skip 11:00-19:00 ET (no new entries until reentry window)
SESSION_CUTOFF_MIN = 15 * 60          # 15:00 ET
REENTRY_MIN        = 19 * 60          # 19:00 ET


@dataclass
class _Trade:
    sess_label:   object
    entry_time:   object
    entry_price:  float
    side:         str
    stop:         float
    target:       float
    level_date:   object
    bar_count:    int = 0
    checked_be:   bool = False
    armed_be:     bool = False
    exit_time:    object = None
    exit_price:   float = None
    exit_reason:  str = None
    pnl:          float = None

    def unrealised(self, price: float) -> float:
        return price - self.entry_price if self.side == "long" else self.entry_price - price


def bar_ranges(bars: pd.DataFrame) -> pd.Series:
    r = bars.resample("60min", label="left", closed="left").agg({"high": "max", "low": "min"}).dropna()
    return r["high"] - r["low"]


def simulate_retest_eligible(bars: pd.DataFrame, vxn: pd.Series, params=NQ_PARAMS) -> pd.DataFrame:
    levels_df = generate_levels(bars, vxn, params, "09:30", "16:00")
    if levels_df.empty:
        return pd.DataFrame()

    all_level_dates = sorted(levels_df["session_date"].unique())
    all_level_dates_arr = np.array(all_level_dates)
    levels_df = levels_df.sort_values("created_at").reset_index(drop=True)

    ranges = bar_ranges(bars)

    idx = bars.index
    hour = idx.hour
    minute_of_day = hour * 60 + idx.minute
    sess_date_arr = np.where(hour >= 19, (idx.normalize() + pd.Timedelta(days=1)).date, idx.normalize().date)

    prev_hour_start = idx.floor("60min") - pd.Timedelta(minutes=60)
    anchor_arr = ranges.reindex(prev_hour_start).values
    cap_arr = np.minimum(CAP_MULT * anchor_arr, SL_CAP)

    op = bars["open"].to_numpy()
    hi = bars["high"].to_numpy()
    lo = bars["low"].to_numpy()
    cl = bars["close"].to_numpy()
    n = len(bars)

    trades: list[_Trade] = []
    active: Optional[_Trade] = None
    cur_sess = None
    sal_triggered = False
    # each (level, side) fires at most once ever, matching nq_cond_be45.py's
    # single first_touch() search over the level's whole lifetime -- global,
    # never reset per session (unlike SAL, which is per-session).
    used_sides: set = set()
    prev_close = None

    lvl_created = levels_df["created_at"].to_numpy()
    lvl_upper = levels_df["upper_level"].to_numpy()
    lvl_lower = levels_df["lower_level"].to_numpy()
    lvl_sessdate = levels_df["session_date"].to_numpy()
    n_lvls = len(levels_df)

    elig_upper = elig_lower = elig_created = elig_sessdate = np.array([])

    for i in range(n):
        t = idx[i]
        sess = sess_date_arr[i]

        if sess != cur_sess:
            if active is not None:
                active.exit_time, active.exit_price = t, op[i]
                active.exit_reason, active.pnl = "cutoff", round(active.unrealised(op[i]), 2)
                trades.append(active)
                active = None
            cur_sess, sal_triggered = sess, False

            # eligible levels: those ranked in the last LINE_DAYS sessions
            # (inclusive) as of today -- precomputed once per session, not
            # per bar, exactly like simulate.py's live_levels slice.
            rank = np.searchsorted(all_level_dates_arr, sess, side="right") - 1
            if rank >= 0:
                lo_rank = max(0, rank - LINE_DAYS + 1)
                elig_upper = lvl_upper[lo_rank: rank + 1]
                elig_lower = lvl_lower[lo_rank: rank + 1]
                elig_created = lvl_created[lo_rank: rank + 1]
                elig_sessdate = lvl_sessdate[lo_rank: rank + 1]
            else:
                elig_upper = elig_lower = elig_created = elig_sessdate = np.array([])

        anchor = anchor_arr[i]
        cap = cap_arr[i]

        exited_this_bar = False

        if active is not None:
            active.bar_count += 1

            if active.bar_count == BE_BAR and not active.checked_be:
                active.checked_be = True
                if active.unrealised(op[i]) > 0:
                    active.armed_be = True
                    active.stop = active.entry_price

            if minute_of_day[i] >= SESSION_CUTOFF_MIN:
                active.exit_time, active.exit_price = t, op[i]
                active.exit_reason, active.pnl = "cutoff", round(active.unrealised(op[i]), 2)
                trades.append(active)
                active, exited_this_bar = None, True
            else:
                stop_hit = (hi[i] >= active.stop) if active.side == "short" else (lo[i] <= active.stop)
                tgt_hit = (lo[i] <= active.target) if active.side == "short" else (hi[i] >= active.target)
                if stop_hit:
                    pnl = round(active.unrealised(active.stop), 2)
                    reason = "BE" if active.armed_be else "SL"
                    if reason == "SL":
                        sal_triggered = True
                    active.exit_time, active.exit_price = t, active.stop
                    active.exit_reason, active.pnl = reason, pnl
                    trades.append(active)
                    active, exited_this_bar = None, True
                elif tgt_hit:
                    pnl = round(active.unrealised(active.target), 2)
                    active.exit_time, active.exit_price = t, active.target
                    active.exit_reason, active.pnl = "TP", pnl
                    trades.append(active)
                    active, exited_this_bar = None, True

        # no same-minute exit -> re-entry: a position that just closed this bar
        # cannot be replaced by a new one until the next bar.
        if (active is None and not exited_this_bar and not sal_triggered and prev_close is not None
                and not (ENTRY_BLOCK[0] <= minute_of_day[i] < ENTRY_BLOCK[1])
                and anchor is not None and not np.isnan(anchor) and cap > 0
                and len(elig_created) > 0):
            live_mask = (elig_created <= t)
            if live_mask.any():
                for j in np.nonzero(live_mask)[0]:
                    ldate = elig_sessdate[j]
                    upper, lower = elig_upper[j], elig_lower[j]
                    # touch condition ported verbatim from the uploaded reactions.py
                    # _first_touch(): level inside the bar's range, OR close crosses
                    # it relative to the prior bar's close -- broader than a simple
                    # prev_close-vs-high/low crossing check.
                    touched_upper = (hi[i] >= upper and lo[i] <= upper) or \
                                     (cl[i] >= upper and prev_close < upper) or \
                                     (cl[i] <= upper and prev_close > upper)
                    touched_lower = (hi[i] >= lower and lo[i] <= lower) or \
                                     (cl[i] >= lower and prev_close < lower) or \
                                     (cl[i] <= lower and prev_close > lower)
                    if (ldate, "upper") not in used_sides and touched_upper:
                        active = _Trade(sess, t, upper, "short", upper + cap, upper - cap, ldate)
                        used_sides.add((ldate, "upper"))
                        break
                    if (ldate, "lower") not in used_sides and touched_lower:
                        active = _Trade(sess, t, lower, "long", lower - cap, lower + cap, ldate)
                        used_sides.add((ldate, "lower"))
                        break

        prev_close = cl[i]

    if active is not None:
        active.exit_time, active.exit_price = idx[-1], cl[-1]
        active.exit_reason, active.pnl = "cutoff", round(active.unrealised(cl[-1]), 2)
        trades.append(active)

    if not trades:
        return pd.DataFrame()

    df = pd.DataFrame([{
        "sess_label": t.sess_label, "entry_time": t.entry_time, "exit_time": t.exit_time,
        "side": t.side, "entry_price": t.entry_price, "exit_price": t.exit_price,
        "exit_reason": t.exit_reason, "pnl": t.pnl, "level_date": t.level_date,
    } for t in trades])
    df["cum_pnl"] = df["pnl"].cumsum().round(2)
    return df


def summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    tp, sl = df[df["exit_reason"] == "TP"], df[df["exit_reason"] == "SL"]
    p = df["pnl"]
    # PF must include every realized trade (TP/SL/BE/cutoff can each be
    # positive or negative), not just TP wins over SL losses.
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
    trades = simulate_retest_eligible(bars, vxn)
    print(f"n={len(trades)}")
    print(summary(trades))
    if not trades.empty:
        for yr, sub in trades.groupby(trades["entry_time"].apply(lambda x: x.year)):
            print(yr, summary(sub))
