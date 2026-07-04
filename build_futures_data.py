"""
Build a continuous 1-minute OHLCV series from raw Databento GLBX.MDP3
ohlcv-1m dumps for a given instrument (NQ or ES).

The raw files contain every simultaneously-listed contract for the parent
symbol (e.g. "NQ.FUT" / "ES.FUT"), not a single continuous series. For each
session date we pick the contract with the highest traded volume that day
(the de facto front month) and take only its bars for that date — no price
adjustment is applied across rolls, since every strategy input (cash_open,
IB, sigma_day) is recomputed fresh each session from that session's own bars.

Usage:
    python3 build_futures_data.py nq   # data/raw_databento/*.zst    -> data/nq_1m_2018_2026.csv.gz
    python3 build_futures_data.py es   # data/raw_databento_es/*.zst -> data/es_1m_2018_2026.csv.gz
"""
from __future__ import annotations

import argparse
import glob
import io

import pandas as pd
import zstandard as zstd

INSTRUMENTS = {
    "nq": {"raw_glob": "data/raw_databento/*.ohlcv-1m.csv.zst", "out": "data/nq_1m_2018_2026.csv.gz"},
    "es": {"raw_glob": "data/raw_databento_es/*.ohlcv-1m.csv.zst", "out": "data/es_1m_2018_2026.csv.gz"},
}


def load_raw(path: str) -> pd.DataFrame:
    dctx = zstd.ZstdDecompressor()
    with open(path, "rb") as fh:
        with dctx.stream_reader(fh) as reader:
            return pd.read_csv(io.TextIOWrapper(reader, encoding="utf-8"))


def build(raw_glob: str) -> pd.DataFrame:
    files = sorted(glob.glob(raw_glob))
    if not files:
        raise FileNotFoundError(f"no raw files matched {raw_glob}")

    raw = pd.concat([load_raw(f) for f in files], ignore_index=True)

    raw["ts_event"] = pd.to_datetime(raw["ts_event"], utc=True)
    raw["ts_ny"] = raw["ts_event"].dt.tz_convert("America/New_York")
    raw["session_date"] = raw["ts_ny"].dt.date

    vol_by_symbol = raw.groupby(["session_date", "symbol"])["volume"].sum().reset_index()
    front = vol_by_symbol.loc[vol_by_symbol.groupby("session_date")["volume"].idxmax()]
    front_symbol_by_date = front.set_index("session_date")["symbol"].to_dict()

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
    out = build(cfg["raw_glob"])
    print(f"rows: {len(out)}  range: {out['timestamp'].min()} .. {out['timestamp'].max()}")
    out.to_csv(cfg["out"], index=False, compression="gzip")
    print(f"wrote {cfg['out']}")
