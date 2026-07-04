from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from levels import generate_levels, NQ_PARAMS, InstrumentParams

# ── strategy constants ────────────────────────────────────────────────────────
LINE_DAYS       = 20      # a level expires once 20 newer sessions have generated levels
ENTRY_END       = "11:00" # no new entries after this time (ET)
SESSION_CUTOFF  = "15:00" # force-close any open position at this time (ET)
COND_BE_BAR     = 45      # bar index (minutes since entry) at which conditional BE is checked
TRAIL_TRIGGER   = 200.0   # arm trailing stop once unrealised P&L hits this (pts)
TRAIL_DIST      = 20.0    # trail distance once armed  (10% of TRAIL_TRIGGER)
BE_BAND         = 5.0     # exit within ±this of entry is classified as BE, not SL/TP


@dataclass
class _Trade:
    session_date: object
    entry_time:   object
    entry_price:  float
    side:         str           # 'long' | 'short'
    stop:         float
    level_date:   object        # session_date of the level that triggered this entry
    exit_time:    object = None
    exit_price:   float  = None
    exit_reason:  str    = None # 'TP' | 'SL' | 'BE' | 'cutoff'
    pnl:          float  = None

    def unrealised(self, price: float) -> float:
        return price - self.entry_price if self.side == "long" else self.entry_price - price


def simulate(
    ohlcv_1m:  pd.DataFrame,
    vxn_close: pd.Series,
    sl_pts:    float,
    params:    InstrumentParams = NQ_PARAMS,
    rth_start: str = "09:30",
    rth_end:   str = "16:00",
) -> pd.DataFrame:
    """
    Replay the level-fade strategy over historical 1-minute bars.

    Parameters
    ----------
    ohlcv_1m   : 1-minute OHLCV, tz-aware (America/New_York), from load_1m_ohlcv()
    vxn_close  : daily VXN closes, from load_vxn_daily()
    sl_pts     : initial stop-loss distance in points from entry price
    params     : instrument parameter set (default NQ_PARAMS)
    rth_start/rth_end : regular-session window used for both level gen and simulation

    Returns
    -------
    DataFrame with one row per trade:
        session_date, entry_time, exit_time, side, entry_price, exit_price,
        exit_reason, pnl, cum_pnl, level_date
    """
    levels_df = generate_levels(ohlcv_1m, vxn_close, params, rth_start, rth_end)
    if levels_df.empty:
        return pd.DataFrame()

    all_session_dates = sorted(levels_df["session_date"].unique())
    session_rank = {d: i for i, d in enumerate(all_session_dates)}

    sessions_bars = ohlcv_1m.between_time(rth_start, rth_end)
    tz = ohlcv_1m.index.tz

    trades: list[_Trade] = []

    for session_date, day_bars in sessions_bars.groupby(sessions_bars.index.date):
        if day_bars.empty:
            continue

        rank = session_rank.get(session_date)
        if rank is None:
            continue

        # levels eligible today: created in the last LINE_DAYS sessions (inclusive)
        min_rank  = max(0, rank - LINE_DAYS + 1)
        live_dates = set(all_session_dates[min_rank : rank + 1])
        live_levels = levels_df[levels_df["session_date"].isin(live_dates)].copy()

        entry_end_dt      = pd.Timestamp(f"{session_date} {ENTRY_END}",     tz=tz)
        session_cutoff_dt = pd.Timestamp(f"{session_date} {SESSION_CUTOFF}", tz=tz)

        sal_triggered  = False      # Stop-After-Loss flag for this session
        used_levels:   set = set()  # level dates already entered today
        active: Optional[_Trade] = None
        bar_count      = 0          # bars since entry
        trail_armed    = False
        peak_unreal    = 0.0
        prev_bar       = None

        for bar_time, bar in day_bars.iterrows():

            # ── manage open position ─────────────────────────────────────────
            if active is not None:
                bar_count += 1
                unreal = active.unrealised(bar["close"])

                # arm / update trailing stop
                if unreal >= TRAIL_TRIGGER:
                    trail_armed = True
                if trail_armed:
                    peak_unreal = max(peak_unreal, unreal)
                    if active.side == "long":
                        new_stop = active.entry_price + (peak_unreal - TRAIL_DIST)
                        active.stop = max(active.stop, new_stop)
                    else:
                        new_stop = active.entry_price - (peak_unreal - TRAIL_DIST)
                        active.stop = min(active.stop, new_stop)

                # conditional BE at bar 45 (only if trail not yet armed)
                if bar_count == COND_BE_BAR and not trail_armed:
                    if active.unrealised(bar["open"]) > 0:
                        active.stop = active.entry_price

                # session cutoff → exit at open of cutoff bar
                if bar_time >= session_cutoff_dt:
                    active.exit_time   = bar_time
                    active.exit_price  = bar["open"]
                    active.exit_reason = "cutoff"
                    active.pnl         = round(active.unrealised(bar["open"]), 2)
                    trades.append(active)
                    active = None
                    break

                # check stop hit (worst-case intra-bar)
                stop_hit = (
                    (active.side == "long"  and bar["low"]  <= active.stop) or
                    (active.side == "short" and bar["high"] >= active.stop)
                )

                if stop_hit:
                    exit_px = active.stop
                    pnl     = round(active.unrealised(exit_px), 2)
                    reason  = (
                        "BE" if abs(pnl) <= BE_BAND else
                        "TP" if trail_armed          else
                        "SL"
                    )
                    if reason == "SL":
                        sal_triggered = True

                    active.exit_time   = bar_time
                    active.exit_price  = exit_px
                    active.exit_reason = reason
                    active.pnl         = pnl
                    trades.append(active)
                    active      = None
                    trail_armed = False
                    peak_unreal = 0.0
                    bar_count   = 0
                    prev_bar    = bar
                    continue

            # ── entry logic ──────────────────────────────────────────────────
            if active is None and not sal_triggered:
                # only enter while entry window is open
                if bar_time > entry_end_dt:
                    prev_bar = bar
                    continue

                # levels must be created at or before this bar
                live_now = live_levels[live_levels["created_at"] <= bar_time]

                if prev_bar is not None and not live_now.empty:
                    for _, lvl in live_now.iterrows():
                        ldate = lvl["session_date"]
                        if ldate in used_levels:
                            continue

                        # SHORT at upper: price approaches from below, first touch
                        if (prev_bar["close"] < lvl["upper_level"] and
                                bar["high"] >= lvl["upper_level"]):
                            active = _Trade(
                                session_date=session_date,
                                entry_time=bar_time,
                                entry_price=lvl["upper_level"],
                                side="short",
                                stop=lvl["upper_level"] + sl_pts,
                                level_date=ldate,
                            )
                            used_levels.add(ldate)
                            bar_count = trail_armed = False
                            peak_unreal = 0.0
                            bar_count = 0
                            break

                        # LONG at lower: price approaches from above, first touch
                        if (prev_bar["close"] > lvl["lower_level"] and
                                bar["low"] <= lvl["lower_level"]):
                            active = _Trade(
                                session_date=session_date,
                                entry_time=bar_time,
                                entry_price=lvl["lower_level"],
                                side="long",
                                stop=lvl["lower_level"] - sl_pts,
                                level_date=ldate,
                            )
                            used_levels.add(ldate)
                            trail_armed = False
                            peak_unreal = 0.0
                            bar_count = 0
                            break

            prev_bar = bar

        # end-of-session force-close (last bar)
        if active is not None:
            last_bar = day_bars.iloc[-1]
            active.exit_time   = day_bars.index[-1]
            active.exit_price  = last_bar["close"]
            active.exit_reason = "cutoff"
            active.pnl         = round(active.unrealised(last_bar["close"]), 2)
            trades.append(active)

    if not trades:
        return pd.DataFrame()

    df = pd.DataFrame([{
        "session_date": t.session_date,
        "entry_time":   t.entry_time,
        "exit_time":    t.exit_time,
        "side":         t.side,
        "entry_price":  t.entry_price,
        "exit_price":   t.exit_price,
        "exit_reason":  t.exit_reason,
        "pnl":          t.pnl,
        "level_date":   t.level_date,
    } for t in trades])

    df["cum_pnl"] = df["pnl"].cumsum().round(2)
    return df


def summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    tp  = df[df["exit_reason"] == "TP"]
    sl  = df[df["exit_reason"] == "SL"]
    gross_w = tp["pnl"].sum()
    gross_l = sl["pnl"].abs().sum()
    twr_n   = len(tp) + len(sl)
    return {
        "n":          len(df),
        "net":        round(df["pnl"].sum(), 2),
        "pf":         round(gross_w / gross_l, 3) if gross_l else None,
        "twr_pct":    round(len(tp) / twr_n * 100, 1) if twr_n else None,
        "tp":         len(tp),
        "sl":         len(sl),
        "be":         len(df[df["exit_reason"] == "BE"]),
        "cutoff":     len(df[df["exit_reason"] == "cutoff"]),
        "avg_win":    round(tp["pnl"].mean(), 1)  if len(tp) else None,
        "avg_loss":   round(sl["pnl"].mean(), 1)  if len(sl) else None,
        "max_dd":     round((df["cum_pnl"] - df["cum_pnl"].cummax()).min(), 2),
    }
