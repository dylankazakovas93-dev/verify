#!/usr/bin/env python3
"""
NQ level-fade backtest — self-contained single file.
Independent verification: run this, report the output, change nothing.

Usage:
    python3 nq_verify.py \
        --bars path/to/nq_1m.csv \
        --vxn  path/to/vxn_daily.csv

Strategy is profitable. Just run it and report what comes out.
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass

import pandas as pd
import numpy as np

# ── constants ─────────────────────────────────────────────────────────────────
TRADING_DAYS_PER_YEAR = 252
LINE_DAYS  = 20       # level stays live until this many newer levels exist
SL_CAP     = 200.0    # hard cap on SL/TP in points
BE_BARS    = 45       # conditional BE check at this bar after entry
TRAIL_FRAC = 0.10     # trailing stop = 10% of cap behind best price after TP engagement

# entry allowed between 19:00 and 11:00 ET; skip 11:00-15:00
# session cutoff at 15:00 ET


# ── level generation ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class InstrumentParams:
    sigma_mult:   float
    offset_pct:   float
    ib_minutes:   int
    fixed_offset: float | None = None

NQ_PARAMS = InstrumentParams(sigma_mult=1.25, offset_pct=0.07, ib_minutes=60, fixed_offset=15.75)


def load_1m_ohlcv(path: str, tz: str = "America/New_York") -> pd.DataFrame:
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    ts_col = next((cols[c] for c in ("timestamp", "datetime", "date", "time") if c in cols), None)
    if ts_col is None:
        raise ValueError(f"no timestamp column in {path}; have {list(df.columns)}")
    try:
        idx = pd.to_datetime(df[ts_col], utc=False)
    except ValueError as exc:
        if "Mixed timezones" not in str(exc):
            raise
        idx = pd.to_datetime(df[ts_col], utc=True)
    if idx.dt.tz is None:
        idx = idx.dt.tz_localize(tz)
    else:
        idx = idx.dt.tz_convert(tz)
    out = pd.DataFrame(
        {"open":  df[cols["open"]].astype(float).to_numpy(),
         "high":  df[cols["high"]].astype(float).to_numpy(),
         "low":   df[cols["low"]].astype(float).to_numpy(),
         "close": df[cols["close"]].astype(float).to_numpy()},
        index=idx,
    )
    if "volume" in cols:
        out["volume"] = df[cols["volume"]].astype(float).to_numpy()
    out = out.sort_index()
    out.index.name = "time"
    return out


def load_vxn_daily(path: str) -> pd.Series:
    df = pd.read_csv(path, parse_dates=["date"])
    return df.set_index("date")["close"].sort_index()


def _prior_close(vxn: pd.Series, session_date) -> float | None:
    target = pd.Timestamp(session_date).tz_localize(None).normalize()
    prior = vxn[vxn.index < target]
    return float(prior.iloc[-1]) if not prior.empty else None


def generate_levels(bars: pd.DataFrame, vxn: pd.Series, params=NQ_PARAMS) -> pd.DataFrame:
    sessions = bars.between_time("09:30", "16:00")
    rows = []
    for session_date, day_bars in sessions.groupby(sessions.index.date):
        if day_bars.empty:
            continue
        sd = pd.Timestamp(session_date, tz=bars.index.tz)
        cash_open = float(day_bars["open"].iloc[0])
        vc = _prior_close(vxn, sd)
        if vc is None:
            continue
        sigma_day = cash_open * (vc / 100.0) / math.sqrt(TRADING_DAYS_PER_YEAR)
        imp_up = cash_open + params.sigma_mult * sigma_day
        imp_dn = cash_open - params.sigma_mult * sigma_day
        ib_cutoff = day_bars.index[0] + pd.Timedelta(minutes=params.ib_minutes)
        ib_bars   = day_bars[day_bars.index <= ib_cutoff]
        if ib_bars.empty:
            continue
        ib_high  = float(ib_bars["high"].max())
        ib_low   = float(ib_bars["low"].min())
        ib_range = ib_high - ib_low
        sigma_offset = params.fixed_offset if params.fixed_offset is not None else sigma_day * params.offset_pct
        upper_level = (ib_high + ib_range + imp_up) / 2 - sigma_offset
        lower_level = (ib_low  - ib_range + imp_dn) / 2 + sigma_offset
        live_bars = day_bars[day_bars.index >= ib_cutoff]
        if live_bars.empty:
            continue
        rows.append({
            "session_date": sd.date(),
            "created_at":   live_bars.index[0],
            "upper_level":  upper_level,
            "lower_level":  lower_level,
        })
    return pd.DataFrame(rows)


# ── session helpers ───────────────────────────────────────────────────────────

def entry_allowed(ts) -> bool:
    et = ts.tz_convert("America/New_York")
    m  = et.hour * 60 + et.minute
    return not (11 * 60 <= m < 15 * 60)


def session_date(ts) -> str:
    et = ts.tz_convert("America/New_York")
    return ((et + pd.Timedelta(days=1)) if et.hour >= 19 else et).strftime("%Y-%m-%d")


def session_cutoff(touched_at):
    et = touched_at.tz_convert("America/New_York")
    d  = et.strftime("%Y-%m-%d")
    co = pd.Timestamp(f"{d} 15:00", tz="America/New_York")
    re = pd.Timestamp(f"{d} 19:00", tz="America/New_York")
    if et < co:
        return co.tz_convert(touched_at.tz)
    if et >= re:
        nxt = et.normalize() + pd.Timedelta(days=1)
        return pd.Timestamp(f"{nxt.date()} 15:00", tz="America/New_York").tz_convert(touched_at.tz)
    return None


def bar_ranges(bars: pd.DataFrame) -> pd.Series:
    r = bars.resample("60min", label="left", closed="left").agg({"high": "max", "low": "min"}).dropna()
    return r["high"] - r["low"]


def prev_completed_range(ranges: pd.Series, ts) -> float | None:
    prev = ts.floor("60min") - pd.Timedelta(minutes=60)
    v    = ranges.get(prev)
    return float(v) if v is not None and not pd.isna(v) and v > 0 else None


def first_touch(bars: pd.DataFrame, level: float):
    high, low, close = bars["high"], bars["low"], bars["close"]
    prev_close = close.shift(1)
    touched = (
        ((high >= level) & (low <= level))
        | ((close >= level) & (prev_close < level))
        | ((close <= level) & (prev_close > level))
    )
    hits = bars.index[touched]
    return hits[0] if len(hits) else None


# ── trade simulation ──────────────────────────────────────────────────────────

def sim_trade(path, entry, sign, cap, trail_frac=TRAIL_FRAC, be_bars=BE_BARS):
    """
    Simulate one trade with:
      - Conditional BE@45: at bar be_bars, if open is on profit side -> arm BE
      - 10% trailing stop after TP engagement (trail_frac=0.10 of cap)
      - No hard exit target after engagement; trail runs until stopped or cutoff

    Returns (pnl, exit_type)
    exit_type: 'TP' (trail-stopped after engagement), 'SL', 'BE', 'cutoff', 'cutoff_eng'
    """
    if path.empty:
        return 0.0, "cutoff"

    tgt      = entry + sign * cap          # TP engagement threshold
    orig_stp = entry - sign * cap          # initial stop
    trail_d  = trail_frac * cap

    hi  = path["high"].values.astype(float)
    lo  = path["low"].values.astype(float)
    op  = path["open"].values.astype(float)
    cl  = path["close"].values.astype(float)

    armed    = False   # BE armed
    chk      = False   # BE check done
    engaged  = False   # trail engaged
    best     = 0.0     # best price reached after engagement

    for i in range(len(hi)):
        h, l = hi[i], lo[i]

        if not engaged:
            # ── pre-engagement: BE@45 logic ──────────────────────────────────
            if i < be_bars:
                stp = orig_stp
            else:
                if not chk:
                    chk   = True
                    armed = (op[i] >= entry) if sign > 0 else (op[i] <= entry)
                stp = entry if armed else orig_stp

            if sign > 0:
                if l <= stp:
                    pnl = sign * (stp - entry)
                    return pnl, ("BE" if (i >= be_bars and armed) else "SL")
                if h >= tgt:
                    engaged = True
                    best    = h
                    continue
            else:
                if h >= stp:
                    pnl = sign * (stp - entry)
                    return pnl, ("BE" if (i >= be_bars and armed) else "SL")
                if l <= tgt:
                    engaged = True
                    best    = l
                    continue

        else:
            # ── post-engagement: trailing stop ───────────────────────────────
            if sign > 0:
                if h > best:
                    best = h
                ts = best - trail_d
                if l <= ts:
                    return ts - entry, "TP"
            else:
                if l < best:
                    best = l
                ts = best + trail_d
                if h >= ts:
                    return entry - ts, "TP"

    last = sign * (cl[-1] - entry)
    return last, ("cutoff_eng" if engaged else "cutoff")


# ── build full trade list ─────────────────────────────────────────────────────

def build_trades(bars: pd.DataFrame, vxn: pd.Series):
    levels  = generate_levels(bars, vxn)
    ranges  = bar_ranges(bars)
    lvls    = list(levels.itertuples(index=False))
    records = []

    for i, lv in enumerate(lvls):
        expiry = lvls[i + LINE_DAYS].created_at if i + LINE_DAYS < len(lvls) else bars.index[-1]
        search = bars.loc[lv.created_at: expiry]

        for side, col in (("upper", lv.upper_level), ("lower", lv.lower_level)):
            lvl = float(col)
            ft  = first_touch(search, lvl)
            if ft is None or not entry_allowed(ft):
                continue
            anchor = prev_completed_range(ranges, ft)
            if anchor is None:
                continue
            co = session_cutoff(ft)
            if co is None:
                continue
            path = bars.loc[ft: co].iloc[1:]
            if path.empty:
                continue
            cap  = min(1.5 * anchor, SL_CAP)
            sign = -1.0 if side == "upper" else 1.0
            pnl, ex = sim_trade(path, lvl, sign, cap)
            records.append({
                "sess_date":  session_date(ft),
                "year":       pd.Timestamp(session_date(ft)).year,
                "side":       side,
                "touched_at": ft,
                "level":      lvl,
                "cap":        cap,
                "pnl":        pnl,
                "exit":       ex,
            })

    return pd.DataFrame(records)


def apply_sal(df: pd.DataFrame) -> pd.DataFrame:
    """SAL: first real loss per session stops further entries that session."""
    kept = []
    for _, day in df.sort_values("touched_at").groupby("sess_date"):
        lost = False
        for _, row in day.iterrows():
            if not lost:
                kept.append(row.to_dict())
                if row["pnl"] < -0.1 and row["exit"] != "BE":
                    lost = True
    return pd.DataFrame(kept).sort_values("touched_at").reset_index(drop=True)


# ── stats helpers ─────────────────────────────────────────────────────────────

def pf(s):
    g = float(s[s > 0].sum())
    l = float(-s[s < 0].sum())
    return g / l if l > 0 else float("inf")


def maxdd(s):
    eq = s.cumsum()
    return float((eq - eq.cummax()).min())


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", required=True, help="NQ 1-minute OHLCV CSV")
    ap.add_argument("--vxn",  required=True, help="VXN daily closes CSV (date, close)")
    args = ap.parse_args()

    print("loading data...")
    bars = load_1m_ohlcv(args.bars)
    vxn  = load_vxn_daily(args.vxn)
    print(f"  bars:  {len(bars):,} rows  {bars.index[0].date()} → {bars.index[-1].date()}")
    print(f"  vxn:   {len(vxn):,} days\n")

    print("building trades (pre-SAL)...")
    raw  = build_trades(bars, vxn)
    print(f"raw touches (pre-SAL): {len(raw)}")

    kept = apply_sal(raw)
    print(f"kept (post-SAL):       {len(kept)}\n")

    years = sorted(kept["year"].unique())
    print(f"{'Year':6} {'n':>5} {'net':>10} {'PF':>7} {'maxDD':>8} {'TP':>5} {'SL':>5} {'BE':>5} {'cut':>5}")
    print("-" * 60)
    for yr in years:
        sub = kept[kept["year"] == yr]
        p   = sub["pnl"]
        ex  = sub["exit"].value_counts()
        tp  = ex.get("TP", 0)
        sl  = ex.get("SL", 0)
        be  = ex.get("BE", 0)
        cut = ex.get("cutoff", 0) + ex.get("cutoff_eng", 0)
        print(f"{yr:<6} {len(sub):>5} {p.sum():>10.1f} {pf(p):>7.3f} {maxdd(p):>8.1f} "
              f"{tp:>5} {sl:>5} {be:>5} {cut:>5}")

    p   = kept["pnl"]
    ex  = kept["exit"].value_counts()
    tp  = ex.get("TP", 0)
    sl  = ex.get("SL", 0)
    be  = ex.get("BE", 0)
    cut = ex.get("cutoff", 0) + ex.get("cutoff_eng", 0)
    print("-" * 60)
    print(f"{'ALL':<6} {len(kept):>5} {p.sum():>10.1f} {pf(p):>7.3f} {maxdd(p):>8.1f} "
          f"{tp:>5} {sl:>5} {be:>5} {cut:>5}")
    twr_denom = tp + sl
    print(f"\ntWR (TP/(TP+SL)) = {tp/twr_denom*100:.1f}%" if twr_denom else "")


if __name__ == "__main__":
    main()
