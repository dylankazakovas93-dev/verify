"""STAGE 5 -- diagnose the 300-pt ceiling saturation + volatility-regime behaviour."""
from __future__ import annotations
import numpy as np, pandas as pd
from causal_touches import load
from common import WINNER, score_decoupled
from engine import load_1m_ohlcv, pf, maxdd

CAUSAL="/home/user/verify/audit/nq_1m_causal.csv.gz"

def daily_atr(bars, n=14):
    """Prior-session ATR(14) from RTH daily bars; strictly completed sessions."""
    rth=bars.between_time("09:30","16:00")
    d=rth.resample("1D").agg({"high":"max","low":"min","close":"last"}).dropna()
    pc=d["close"].shift(1)
    tr=pd.concat([d["high"]-d["low"],(d["high"]-pc).abs(),(d["low"]-pc).abs()],axis=1).max(axis=1)
    atr=tr.rolling(n).mean().shift(1)   # shift => only prior completed sessions
    return atr

def main():
    touches,bars=load()
    kept=score_decoupled(touches,bars,WINNER).copy()
    kept["uncapped_stop"]=WINNER.sl_mult*kept.anchor
    kept["uncapped_tgt"]=WINNER.tp_mult*kept.anchor
    kept["stop_d"]=np.minimum(kept.uncapped_stop,WINNER.cap_ceiling)
    kept["tgt_d"]=np.minimum(kept.uncapped_tgt,WINNER.cap_ceiling)
    kept["stop_capped"]=kept.uncapped_stop>=WINNER.cap_ceiling
    kept["tgt_capped"]=kept.uncapped_tgt>=WINNER.cap_ceiling
    kept["risk_mnq"]=kept.stop_d*2.0

    atr=daily_atr(bars)
    ad=atr.copy(); ad.index=ad.index.date
    kept["sess_d"]=pd.to_datetime(kept.sess_date).dt.date
    kept["prior_atr"]=kept.sess_d.map(ad.to_dict())
    kept["price"]=kept.level

    print("=== per-year saturation ===")
    rows=[]
    for y,s in kept.groupby("year"):
        rows.append(dict(year=y,n=len(s),
            stop_cap_pct=round(100*s.stop_capped.mean(),1),
            tgt_cap_pct=round(100*s.tgt_capped.mean(),1),
            med_uncap_stop=round(s.uncapped_stop.median(),0),
            med_uncap_tgt=round(s.uncapped_tgt.median(),0),
            med_act_stop=round(s.stop_d.median(),0),
            med_act_tgt=round(s.tgt_d.median(),0),
            med_1h_range=round(s.anchor.median(),1),
            med_prior_atr=round(s.prior_atr.median(),0) if s.prior_atr.notna().any() else None,
            med_price=round(s.price.median(),0),
            avg_risk_mnq=round(s.risk_mnq.mean(),0)))
    print(pd.DataFrame(rows).to_string(index=False))

    print("\n=== volatility quintiles (by prior_1h_range = anchor) ===")
    kept["q"]=pd.qcut(kept.anchor,5,labels=["Q1_low","Q2","Q3","Q4","Q5_high"])
    for q,s in kept.groupby("q",observed=True):
        p=s.raw_pnl
        print(f"  {q:8} n={len(s):4} anchor[{s.anchor.min():.0f}-{s.anchor.max():.0f}] "
              f"net={p.sum():8.1f} pf={pf(p):5.3f} avg={p.mean():6.1f} maxdd={maxdd(p):8.1f} "
              f"stopcap%={100*s.stop_capped.mean():4.0f} tgtcap%={100*s.tgt_capped.mean():4.0f}")

    print("\n=== overall ===")
    print(f"  stop capped: {100*kept.stop_capped.mean():.1f}%   target capped: {100*kept.tgt_capped.mean():.1f}%")
    print(f"  median actual stop {kept.stop_d.median():.0f}pt (${kept.stop_d.median()*2:.0f} 1xMNQ), target {kept.tgt_d.median():.0f}pt")

if __name__=="__main__":
    main()
