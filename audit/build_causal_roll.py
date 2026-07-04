"""Build a CAUSAL continuous NQ series and a session->contract map.

The shipped build_futures_data.py picks each session's front contract from
that SAME session's total volume -- non-causal (you can't know full-session
volume at the open). This rebuilds using ONLY the previous session's volume
(monotonic roll preserved), which is knowable at the open. Also emits the
per-session contract for both the causal and the shipped (non-causal) rule
so Stage 3 can identify roll dates and cross-contract trades.
"""
from __future__ import annotations
import glob, io, re, sys, hashlib
import pandas as pd, zstandard as zstd

RAW = "/tmp/claude-0/-home-user-verify/0bbb5195-16bc-5b21-932e-e050f813481b/scratchpad/repro/data/raw_databento/*.ohlcv-1m.csv.zst"
OUT = "/home/user/verify/audit/nq_1m_causal.csv.gz"
MAP = "/home/user/verify/audit/session_contract_map.csv"
ROOT="NQ"; MONTH={"H":3,"M":6,"U":9,"Z":12}
pat=re.compile(rf"^{ROOT}([HMUZ])(\d)$")
def expiry(sym):
    m=pat.match(sym); d=int(m.group(2)); y=2020+d if d<=6 else 2010+d
    return (y,MONTH[m.group(1)])

def load():
    dctx=zstd.ZstdDecompressor(); frames=[]
    for f in sorted(glob.glob(RAW)):
        with open(f,'rb') as fh, dctx.stream_reader(fh) as r:
            frames.append(pd.read_csv(io.TextIOWrapper(r,encoding='utf-8')))
    raw=pd.concat(frames,ignore_index=True)
    raw=raw[raw["symbol"].str.match(pat)].copy()
    raw["ts_event"]=pd.to_datetime(raw["ts_event"],utc=True)
    raw["ts_ny"]=raw["ts_event"].dt.tz_convert("America/New_York")
    raw["session_date"]=raw["ts_ny"].dt.date
    return raw

def pick(raw, causal):
    vol=raw.groupby(["session_date","symbol"])["volume"].sum().reset_index()
    vol["expiry"]=vol["symbol"].apply(expiry)
    dates=sorted(vol["session_date"].unique())
    by={d:g for d,g in vol.groupby("session_date")}
    front={}; cur=None
    for i,d in enumerate(dates):
        src = by[dates[i-1]] if (causal and i>0) else by[d]   # previous session's volume if causal
        day=src.sort_values("volume",ascending=False)
        if cur is not None:
            cand=day[day["expiry"]>=cur]
            row=cand.iloc[0] if not cand.empty else day.iloc[0]
        else:
            row=day.iloc[0]
        # ensure chosen symbol actually trades today; else fall back to today's top monotonic
        if row["symbol"] not in set(by[d]["symbol"]):
            today=by[d].sort_values("volume",ascending=False)
            cand=today[today["expiry"]>=(cur or (0,0))]
            row=cand.iloc[0] if not cand.empty else today.iloc[0]
        front[d]=row["symbol"]; cur=expiry(row["symbol"])
    return front

def main():
    raw=load()
    causal=pick(raw,True); noncausal=pick(raw,False)
    m=pd.DataFrame({"session_date":sorted(causal)})
    m["causal_contract"]=m["session_date"].map(causal)
    m["shipped_contract"]=m["session_date"].map(noncausal)
    m["differs"]=m["causal_contract"]!=m["shipped_contract"]
    m.to_csv(MAP,index=False)
    print(f"sessions: {len(m)}  causal!=shipped on {m.differs.sum()} sessions")
    # build causal continuous bars
    raw["front"]=raw["session_date"].map(causal)
    fr=raw[raw["symbol"]==raw["front"]].copy().sort_values("ts_ny").drop_duplicates("ts_ny",keep="first")
    out=fr[["ts_ny","open","high","low","close","volume"]].rename(columns={"ts_ny":"timestamp"})
    out.sort_values("timestamp").to_csv(OUT,index=False,compression="gzip")
    h=hashlib.sha256(open(OUT,'rb').read()).hexdigest()
    print(f"wrote {OUT}  sha256={h}")
    # roll dates (causal)
    rolls=m[m["causal_contract"]!=m["causal_contract"].shift(1)].dropna()
    print("causal roll dates:", list(rolls["session_date"].astype(str)))

if __name__=="__main__":
    main()
