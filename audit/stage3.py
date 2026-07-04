"""STAGE 3 -- contract-roll audit on the CAUSAL series.

Does the edge survive (a) a causal roll rule, (b) removing trades whose level
was generated on a different contract than the entry, (c) removing trades
within +/-5 sessions of a roll?
"""
from __future__ import annotations
import numpy as np, pandas as pd
import engine as E
from engine import (NQ_LEVEL_PARAMS, generate_levels, _bar_ranges, _first_touch,
                    _entry_allowed, _prev_completed_range, _session_cutoff,
                    _session_date, DEFAULT_WINDOW, LINE_DAYS, load_1m_ohlcv,
                    load_vol_daily, sim_trade, near_round_number, pf, maxdd)
from common import WINNER, VXN, score_decoupled

CAUSAL="/home/user/verify/audit/nq_1m_causal.csv.gz"
MAP="/home/user/verify/audit/session_contract_map.csv"

def build_touches_aug(bars, vol, lp, window=DEFAULT_WINDOW):
    levels=generate_levels(bars,vol,lp); ranges=_bar_ranges(bars)
    lvls=list(levels.itertuples(index=False)); recs=[]
    for i,lv in enumerate(lvls):
        expiry=lvls[i+LINE_DAYS].created_at if i+LINE_DAYS<len(lvls) else bars.index[-1]
        search=bars.loc[lv.created_at:expiry]
        for side,col in (("upper",lv.upper_level),("lower",lv.lower_level)):
            lvl=float(col); ft=_first_touch(search,lvl)
            if ft is None or not _entry_allowed(ft,window): continue
            anchor=_prev_completed_range(ranges,ft)
            if anchor is None: continue
            co=_session_cutoff(ft,window)
            if co is None: continue
            if bars.loc[ft:co].iloc[1:].empty: continue
            recs.append({"sess_date":_session_date(ft,window),
                         "year":pd.Timestamp(_session_date(ft,window)).year,
                         "side":side,"touched_at":ft,"level":lvl,"anchor":anchor,
                         "sign":-1.0 if side=="upper" else 1.0,"path_start":ft,"path_end":co,
                         "level_session":str(lv.session_date)})
    return pd.DataFrame(recs)

def summ(p):
    return dict(n=len(p),net=round(p.sum(),1),pf=round(pf(p),3),maxdd=round(maxdd(p),1))

def main():
    bars=load_1m_ohlcv(CAUSAL); vol=load_vol_daily(VXN)
    touches=build_touches_aug(bars,vol,NQ_LEVEL_PARAMS)
    print(f"causal-series touches: {len(touches)}")
    kept=score_decoupled(touches,bars,WINNER)
    # attach level_session (merge by touched_at+level)
    key=touches.set_index(["touched_at","level"])["level_session"].to_dict()
    kept["level_session"]=[key.get((t,l)) for t,l in zip(kept.touched_at,kept.level)]

    cmap=pd.read_csv(MAP)
    c={str(d):s for d,s in zip(cmap.session_date,cmap.causal_contract)}
    kept["entry_contract"]=kept["sess_date"].map(c)
    kept["level_contract"]=kept["level_session"].map(c)
    kept["cross"]= kept["entry_contract"]!=kept["level_contract"]

    # roll sessions and +/-5 buffer
    sessions=list(cmap.session_date.astype(str))
    rank={d:i for i,d in enumerate(sessions)}
    roll_idx=[i for i in range(1,len(cmap)) if cmap.causal_contract.iloc[i]!=cmap.causal_contract.iloc[i-1]]
    buf=set()
    for ri in roll_idx:
        for j in range(ri-5,ri+6):
            if 0<=j<len(sessions): buf.add(sessions[j])
    kept["near_roll"]=kept["sess_date"].isin(buf)

    p=kept["raw_pnl"]
    print("\n=== edge survival (raw single-contract pnl) ===")
    print(f"  causal series, ALL trades          : {summ(p)}")
    print(f"  shipped series baseline (Stage 1)   : n=1328 net=32860.0 pf=1.836")
    sub=kept[~kept.cross]["raw_pnl"]
    print(f"  exclude cross-contract ({kept.cross.sum()} trades): {summ(sub)}")
    sub2=kept[~kept.near_roll]["raw_pnl"]
    print(f"  exclude +/-5 sessions of roll ({kept.near_roll.sum()} trades): {summ(sub2)}")
    sub3=kept[(~kept.cross)&(~kept.near_roll)]["raw_pnl"]
    print(f"  exclude BOTH: {summ(sub3)}")
    # per-year net of the survivors (both exclusions) to check no year flips negative
    yr=kept[(~kept.cross)&(~kept.near_roll)].groupby("year")["raw_pnl"].agg(["size","sum"])
    print("\n=== per-year after both exclusions ===")
    print(yr.round(1).to_string())

if __name__=="__main__":
    main()
