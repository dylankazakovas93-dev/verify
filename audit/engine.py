"""
Config-driven level-fade engine shared by NQ and ES grid search.

Split into an expensive stage (level generation + first-touch scanning,
depends on level-gen params, entry-window, and cutoff-time) and a cheap
stage (per-touch stop/target/BE/trail simulation, depends only on
execution params) so a grid search over execution params can re-score a
fixed touch set instead of rebuilding it on every combo.

NQ and ES must use separate LevelParams / ExecParams instances -- a config
tuned for one instrument is never applied to the other.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Optional

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252
LINE_DAYS = 20


@dataclass(frozen=True)
class LevelParams:
    sigma_mult:   float
    ib_minutes:   int
    offset_pct:   Optional[float] = None
    fixed_offset: Optional[float] = None


NQ_LEVEL_PARAMS = LevelParams(sigma_mult=1.25, ib_minutes=60, fixed_offset=15.75)
ES_LEVEL_PARAMS = LevelParams(sigma_mult=1.25, ib_minutes=60, offset_pct=0.02)


@dataclass(frozen=True)
class ExecParams:
    cap_mult:     float = 1.5     # anchor multiplier feeding both sl/tp distance
    sl_mult:      Optional[float] = None  # overrides cap_mult for stop distance if set
    tp_mult:      Optional[float] = None  # overrides cap_mult for target distance if set
    cap_ceiling:  float = 200.0   # hard ceiling on stop/target distance, in points
    be_bars:      int = 45        # minutes since entry (1-min bars)
    be_mechanic:  str = "open_cross"   # "open_cross" | "close_cross" | "profit_lock"
    be_lock_frac: float = 0.0     # for "profit_lock": fraction of unrealised profit kept as BE offset
    trail_frac:   float = 0.10    # fraction of tp distance trailed behind peak after engagement
    round_step:        float = 50.0
    round_tol:         float = 0.0   # 0 disables round-number size boost
    round_size_mult:   float = 1.0

    def sl_distance(self, anchor: float) -> float:
        m = self.sl_mult if self.sl_mult is not None else self.cap_mult
        return min(m * anchor, self.cap_ceiling)

    def tp_distance(self, anchor: float) -> float:
        m = self.tp_mult if self.tp_mult is not None else self.cap_mult
        return min(m * anchor, self.cap_ceiling)


@dataclass(frozen=True)
class WindowParams:
    entry_block_start_min: Optional[int] = 11 * 60   # None => no blackout (trade all day)
    entry_block_end_min:   Optional[int] = 15 * 60
    cutoff_hhmm:            str = "15:00"
    overnight_reentry_hhmm: str = "19:00"


DEFAULT_WINDOW = WindowParams()


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


def load_vol_daily(path: str) -> pd.Series:
    df = pd.read_csv(path, parse_dates=["date"])
    return df.set_index("date")["close"].sort_index()


def _prior_close(vol: pd.Series, session_date) -> Optional[float]:
    target = pd.Timestamp(session_date).tz_localize(None).normalize()
    prior = vol[vol.index < target]
    return float(prior.iloc[-1]) if not prior.empty else None


def generate_levels(bars: pd.DataFrame, vol: pd.Series, params: LevelParams) -> pd.DataFrame:
    sessions = bars.between_time("09:30", "16:00")
    rows = []
    for session_date, day_bars in sessions.groupby(sessions.index.date):
        if day_bars.empty:
            continue
        sd = pd.Timestamp(session_date, tz=bars.index.tz)
        cash_open = float(day_bars["open"].iloc[0])
        vc = _prior_close(vol, sd)
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


def _entry_allowed(ts, window: WindowParams) -> bool:
    if window.entry_block_start_min is None:
        return True
    et = ts.tz_convert("America/New_York")
    m  = et.hour * 60 + et.minute
    return not (window.entry_block_start_min <= m < window.entry_block_end_min)


def _session_date(ts, window: WindowParams) -> str:
    et = ts.tz_convert("America/New_York")
    reentry_h = int(window.overnight_reentry_hhmm.split(":")[0])
    return ((et + pd.Timedelta(days=1)) if et.hour >= reentry_h else et).strftime("%Y-%m-%d")


def _session_cutoff(touched_at, window: WindowParams):
    et = touched_at.tz_convert("America/New_York")
    d  = et.strftime("%Y-%m-%d")
    co = pd.Timestamp(f"{d} {window.cutoff_hhmm}", tz="America/New_York")
    re = pd.Timestamp(f"{d} {window.overnight_reentry_hhmm}", tz="America/New_York")
    if et < co:
        return co.tz_convert(touched_at.tz)
    if et >= re:
        nxt = et.normalize() + pd.Timedelta(days=1)
        return pd.Timestamp(f"{nxt.date()} {window.cutoff_hhmm}", tz="America/New_York").tz_convert(touched_at.tz)
    return None


def _bar_ranges(bars: pd.DataFrame) -> pd.Series:
    r = bars.resample("60min", label="left", closed="left").agg({"high": "max", "low": "min"}).dropna()
    return r["high"] - r["low"]


def _prev_completed_range(ranges: pd.Series, ts) -> Optional[float]:
    prev = ts.floor("60min") - pd.Timedelta(minutes=60)
    v    = ranges.get(prev)
    return float(v) if v is not None and not pd.isna(v) and v > 0 else None


def _first_touch(bars: pd.DataFrame, level: float):
    high, low, close = bars["high"], bars["low"], bars["close"]
    prev_close = close.shift(1)
    touched = (
        ((high >= level) & (low <= level))
        | ((close >= level) & (prev_close < level))
        | ((close <= level) & (prev_close > level))
    )
    hits = bars.index[touched]
    return hits[0] if len(hits) else None


def near_round_number(level: float, step: float, tol: float) -> bool:
    if tol <= 0:
        return False
    nearest = round(level / step) * step
    return abs(level - nearest) <= tol


def build_touches(bars: pd.DataFrame, vol: pd.Series, level_params: LevelParams,
                   window: WindowParams = DEFAULT_WINDOW) -> pd.DataFrame:
    """Expensive stage: level generation + first-touch scan. Independent of ExecParams."""
    levels = generate_levels(bars, vol, level_params)
    ranges = _bar_ranges(bars)
    lvls   = list(levels.itertuples(index=False))
    records = []

    for i, lv in enumerate(lvls):
        expiry = lvls[i + LINE_DAYS].created_at if i + LINE_DAYS < len(lvls) else bars.index[-1]
        search = bars.loc[lv.created_at: expiry]

        for side, col in (("upper", lv.upper_level), ("lower", lv.lower_level)):
            lvl = float(col)
            ft = _first_touch(search, lvl)
            if ft is None or not _entry_allowed(ft, window):
                continue
            anchor = _prev_completed_range(ranges, ft)
            if anchor is None:
                continue
            co = _session_cutoff(ft, window)
            if co is None:
                continue
            path = bars.loc[ft: co].iloc[1:]
            if path.empty:
                continue
            sign = -1.0 if side == "upper" else 1.0
            records.append({
                "sess_date":  _session_date(ft, window),
                "year":       pd.Timestamp(_session_date(ft, window)).year,
                "side":       side,
                "touched_at": ft,
                "level":      lvl,
                "anchor":     anchor,
                "sign":       sign,
                "path_start": ft,
                "path_end":   co,
            })

    df = pd.DataFrame(records)
    return df, bars


def sim_trade(bars: pd.DataFrame, touched_at, path_end, entry: float, sign: float,
              anchor: float, exe: ExecParams):
    """Cheap stage: stop/target/BE/trail simulation for one touch.

    Returns (pnl, exit_reason, exit_time, bars_held) -- exit_time/bars_held
    let callers compute holding-period stats (frequency, avg hold, whether
    exits stay intraday) without re-simulating.
    """
    path = bars.loc[touched_at: path_end].iloc[1:]
    if path.empty:
        return 0.0, "cutoff", path_end, 0

    sl_d = exe.sl_distance(anchor)
    tp_d = exe.tp_distance(anchor)
    tgt      = entry + sign * tp_d
    orig_stp = entry - sign * sl_d
    trail_d  = exe.trail_frac * tp_d

    hi = path["high"].values.astype(float)
    lo = path["low"].values.astype(float)
    op = path["open"].values.astype(float)
    cl = path["close"].values.astype(float)

    armed, chk, engaged = False, False, False
    best = 0.0

    for i in range(len(hi)):
        h, l = hi[i], lo[i]

        if not engaged:
            if i < exe.be_bars:
                stp = orig_stp
            else:
                if not chk:
                    chk = True
                    if exe.be_mechanic == "close_cross":
                        ref = cl[i - 1] if i > 0 else op[i]
                    else:
                        ref = op[i]
                    profit_side = (ref >= entry) if sign > 0 else (ref <= entry)
                    armed = profit_side
                if armed:
                    if exe.be_mechanic == "profit_lock" and exe.be_lock_frac > 0:
                        unreal = sign * (op[i] - entry) if i == exe.be_bars else sign * (cl[i - 1] - entry)
                        stp = entry + sign * max(0.0, exe.be_lock_frac * unreal)
                    else:
                        stp = entry
                else:
                    stp = orig_stp

            if sign > 0:
                if l <= stp:
                    return sign * (stp - entry), ("BE" if (i >= exe.be_bars and armed) else "SL"), path.index[i], i + 1
                if h >= tgt:
                    engaged, best = True, h
                    continue
            else:
                if h >= stp:
                    return sign * (stp - entry), ("BE" if (i >= exe.be_bars and armed) else "SL"), path.index[i], i + 1
                if l <= tgt:
                    engaged, best = True, l
                    continue
        else:
            if sign > 0:
                best = max(best, h)
                ts_ = best - trail_d
                if l <= ts_:
                    return ts_ - entry, "TP", path.index[i], i + 1
            else:
                best = min(best, l) if best else l
                ts_ = best + trail_d
                if h >= ts_:
                    return entry - ts_, "TP", path.index[i], i + 1

    last = sign * (cl[-1] - entry)
    return last, ("cutoff_eng" if engaged else "cutoff"), path.index[-1], len(path)


def score_touches(touches: pd.DataFrame, bars: pd.DataFrame, exe: ExecParams) -> pd.DataFrame:
    rows = []
    for r in touches.itertuples(index=False):
        pnl, ex, exit_time, bars_held = sim_trade(bars, r.touched_at, r.path_end, r.level, r.sign, r.anchor, exe)
        size_mult = exe.round_size_mult if near_round_number(r.level, exe.round_step, exe.round_tol) else 1.0
        hold_min = (exit_time - r.touched_at).total_seconds() / 60.0
        rows.append({"sess_date": r.sess_date, "year": r.year, "touched_at": r.touched_at,
                      "pnl": pnl * size_mult, "exit": ex, "exit_time": exit_time,
                      "bars_held": bars_held, "hold_min": hold_min})
    return pd.DataFrame(rows)


def apply_sal(df: pd.DataFrame) -> pd.DataFrame:
    """
    Stop-After-Loss: first real loss in a session blocks further entries
    that session. Ties (two levels touched in the same 1-minute bar) must
    use a stable sort -- pandas' default quicksort has no tie-break
    guarantee, so a session with simultaneous touches would silently pick
    a different "first" trade (and therefore a different SAL outcome)
    depending on what subset/order of rows happened to reach this function.
    kind="stable" preserves input row order for ties, so build_touches'
    own iteration order (levels oldest-created first, upper before lower)
    is the deterministic tie-break, regardless of how the caller sliced df.
    """
    kept = []
    for _, day in df.sort_values("touched_at", kind="stable").groupby("sess_date"):
        lost = False
        for _, row in day.iterrows():
            if not lost:
                kept.append(row.to_dict())
                if row["pnl"] < -0.1 and row["exit"] != "BE":
                    lost = True
    return pd.DataFrame(kept).sort_values("touched_at").reset_index(drop=True)


def pf(s):
    g = float(s[s > 0].sum())
    l = float(-s[s < 0].sum())
    return g / l if l > 0 else float("inf")


def maxdd(s):
    eq = s.cumsum()
    return float((eq - eq.cummax()).min())


def summarize(kept: pd.DataFrame) -> dict:
    if kept.empty:
        return {"n": 0, "net": 0.0, "pf": None, "maxdd": 0.0, "twr": None}
    p = kept["pnl"]
    ex = kept["exit"].value_counts()
    tp, sl = ex.get("TP", 0), ex.get("SL", 0)
    denom = tp + sl
    return {
        "n": len(kept), "net": round(p.sum(), 1), "pf": round(pf(p), 3),
        "maxdd": round(maxdd(p), 1), "twr": round(tp / denom * 100, 1) if denom else None,
        "tp": tp, "sl": sl, "be": ex.get("BE", 0),
        "cut": ex.get("cutoff", 0) + ex.get("cutoff_eng", 0),
    }


def sharpe(s: pd.Series) -> float | None:
    """Per-trade Sharpe, annualized by sqrt(n) (n trades treated as n return periods)."""
    if len(s) < 2 or s.std(ddof=1) == 0:
        return None
    return float(s.mean() / s.std(ddof=1) * math.sqrt(len(s)))


def sortino(s: pd.Series) -> float | None:
    """Per-trade Sortino: mean / downside semi-deviation (targeted at 0), annualized by sqrt(n)."""
    downside = np.sqrt(np.mean(np.minimum(s.values, 0.0) ** 2))
    if len(s) < 2 or downside == 0:
        return None
    return float(s.mean() / downside * math.sqrt(len(s)))


def summarize_extended(kept: pd.DataFrame, years_spanned: float | None = None) -> dict:
    """summarize() plus R:R, outright WR, Sharpe/Sortino, annualized net, hold-time stats."""
    base = summarize(kept)
    if kept.empty:
        return {**base, "outright_wr": None, "rr_realized": None, "sharpe": None,
                "sortino": None, "ann_net": None, "avg_hold_min": None,
                "median_hold_min": None, "pct_overnight": None}

    p = kept["pnl"]
    wins, losses = p[p > 0], p[p < 0]
    outright_wr = round((p > 0).mean() * 100, 1)
    avg_win = float(wins.mean()) if len(wins) else None
    avg_loss = float(losses.mean()) if len(losses) else None
    rr_realized = round(abs(avg_win / avg_loss), 3) if avg_win and avg_loss else None

    if years_spanned is None:
        years_spanned = (kept["touched_at"].max() - kept["touched_at"].min()).days / 365.25
    ann_net = round(p.sum() / years_spanned, 1) if years_spanned and years_spanned > 0 else None

    hold = kept["hold_min"] if "hold_min" in kept.columns else None
    pct_overnight = None
    if "exit_time" in kept.columns:
        entry_date = kept["touched_at"].dt.tz_convert("America/New_York").dt.date
        exit_date = kept["exit_time"].dt.tz_convert("America/New_York").dt.date
        pct_overnight = round((exit_date != entry_date).mean() * 100, 1)

    return {
        **base,
        "outright_wr": outright_wr,
        "rr_realized": rr_realized,
        "sharpe": round(sharpe(p), 3) if sharpe(p) is not None else None,
        "sortino": round(sortino(p), 3) if sortino(p) is not None else None,
        "ann_net": ann_net,
        "avg_hold_min": round(hold.mean(), 1) if hold is not None else None,
        "median_hold_min": round(hold.median(), 1) if hold is not None else None,
        "pct_held_overnight": pct_overnight,
    }
