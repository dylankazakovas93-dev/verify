"""
Build a continuous 1-minute OHLCV series from raw Databento GLBX.MDP3
ohlcv-1m dumps for a given instrument (NQ or ES).

The raw files contain every simultaneously-listed contract for the parent
symbol (e.g. "NQ.FUT" / "ES.FUT"), including calendar-spread symbols
(e.g. "NQH0-NQM0") alongside outright contracts (e.g. "NQH0"). Only
outright quarterly contracts (H/M/U/Z) are valid front-month candidates --
a spread symbol's OHLC is the spread differential, not a tradable price,
so it must never be picked even if it briefly has the highest volume.

For each session date we pick the highest-volume outright contract (the
de facto front month), with the roll constrained to be monotonic: once
we've rolled to a given contract, we never revert to an earlier-expiry
one even if that earlier contract has a stray high-volume day. No price
adjustment is applied across rolls, since every strategy input
(cash_open, IB, sigma_day) is recomputed fresh each session from that
session's own bars.

Usage:
    python3 build_futures_data.py nq   # data/raw_databento/*.zst    -> data/nq_1m_2018_2026.csv.gz
    python3 build_futures_data.py es   # data/raw_databento_es/*.zst -> data/es_1m_2018_2026.csv.gz
"""
from __future__ import annotations

import argparse
import glob
import io
import re

import pandas as pd
import zstandard as zstd

INSTRUMENTS = {
    "nq": {"raw_glob": "data/raw_databento/*.ohlcv-1m.csv.zst", "out": "data/nq_1m_2018_2026.csv.gz", "root": "NQ"},
    "es": {"raw_glob": "data/raw_databento_es/*.ohlcv-1m.csv.zst", "out": "data/es_1m_2018_2026.csv.gz", "root": "ES"},
}

MONTH_CODE = {"H": 3, "M": 6, "U": 9, "Z": 12}


def load_raw(path: str) -> pd.DataFrame:
    dctx = zstd.ZstdDecompressor()
    with open(path, "rb") as fh:
        with dctx.stream_reader(fh) as reader:
            return pd.read_csv(io.TextIOWrapper(reader, encoding="utf-8"))


def outright_pattern(root: str) -> re.Pattern:
    return re.compile(rf"^{root}([HMUZ])(\d)$")


def expiry_key(symbol: str, pattern: re.Pattern) -> tuple[int, int]:
    """(year, month) sort key. Single year digit assumed unambiguous within a <10yr span."""
    m = pattern.match(symbol)
    letter, digit = m.group(1), int(m.group(2))
    year = 2020 + digit if digit <= 6 else 2010 + digit  # covers 2018-2026 without collision
    return (year, MONTH_CODE[letter])


def build(raw_glob: str, root: str) -> pd.DataFrame:
    files = sorted(glob.glob(raw_glob))
    if not files:
        raise FileNotFoundError(f"no raw files matched {raw_glob}")

    raw = pd.concat([load_raw(f) for f in files], ignore_index=True)

    pattern = outright_pattern(root)
    raw = raw[raw["symbol"].str.match(pattern)].copy()

    raw["ts_event"] = pd.to_datetime(raw["ts_event"], utc=True)
    raw["ts_ny"] = raw["ts_event"].dt.tz_convert("America/New_York")
    raw["session_date"] = raw["ts_ny"].dt.date

    vol_by_symbol = raw.groupby(["session_date", "symbol"])["volume"].sum().reset_index()
    vol_by_symbol["expiry"] = vol_by_symbol["symbol"].apply(lambda s: expiry_key(s, pattern))

    front_symbol_by_date: dict = {}
    current_expiry = None
    for session_date, day in vol_by_symbol.sort_values("session_date").groupby("session_date"):
        day = day.sort_values("volume", ascending=False)
        if current_expiry is not None:
            candidates = day[day["expiry"] >= current_expiry]
            row = candidates.iloc[0] if not candidates.empty else day.iloc[0]
        else:
            row = day.iloc[0]
        front_symbol_by_date[session_date] = row["symbol"]
        current_expiry = row["expiry"]

    raw["front_symbol"] = raw["session_date"].map(front_symbol_by_date)
    front_df = raw[raw["symbol"] == raw["front_symbol"]].copy()

    front_df = front_df.sort_values("ts_ny").drop_duplicates(subset=["ts_ny"], keep="first")

    out = front_df[["ts_ny", "open", "high", "low", "close", "volume"]].rename(
        columns={"ts_ny": "timestamp"}
    )
    return out.sort_values("timestamp")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("instrument", choices=sorted(INSTRUMENTS))
    args = ap.parse_args()

    cfg = INSTRUMENTS[args.instrument]
    out = build(cfg["raw_glob"], cfg["root"])
    print(f"rows: {len(out)}  range: {out['timestamp'].min()} .. {out['timestamp'].max()}")
    out.to_csv(cfg["out"], index=False, compression="gzip")
    print(f"wrote {cfg['out']}")
