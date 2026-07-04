"""STAGE 4 -- concurrency & duplicate-exposure audit (causal series, winner config)."""
from __future__ import annotations
import numpy as np, pandas as pd
from causal_touches import load
from common import WINNER, score_decoupled
from engine import pf, maxdd

def summ(p): return dict(n=len(p),net=round(float(p.sum()),1),pf=round(pf(p),3),maxdd=round(maxdd(p),1))

def concurrency_stats(kept):
    ev=[]
    for r in kept.itertuples(index=False):
        ev.append((r.touched_at,1)); ev.append((r.exit_time,-1))
    ev.sort()
    cur=0; series=[]
    for _,d in ev:
        cur+=d; series.append(cur)
    s=np.array(series); exposed=s[s>0]
    return dict(max=int(s.max()),
                median_while_exposed=float(np.median(exposed)) if len(exposed) else 0,
                p95=float(np.percentile(exposed,95)) if len(exposed) else 0,
                p99=float(np.percentile(exposed,99)) if len(exposed) else 0)

def cap_concurrency(kept, cap):
    """Sequentially admit by entry; skip if >= cap already open. Also track combined stop risk."""
    k=kept.sort_values("touched_at").reset_index(drop=True)
    open_exits=[]  # list of (exit_time, stop_risk_pts)
    admit=[]; max_comb_risk=0.0
    for r in k.itertuples(index=False):
        open_exits=[(t,s) for (t,s) in open_exits if t> r.touched_at]
        if len(open_exits)>=cap:
            continue
        stop_risk=min(WINNER.sl_mult*r.anchor, WINNER.cap_ceiling)
        admit.append(r._asdict() if hasattr(r,'_asdict') else r)
        open_exits.append((r.exit_time, stop_risk))
        max_comb_risk=max(max_comb_risk, sum(s for _,s in open_exits))
    df=pd.DataFrame([a for a in admit])
    return df, max_comb_risk

def dedup(kept, tol):
    """Drop a same-direction trade if a same-side already-open trade has level within tol.
    Priority = earlier entry (causal)."""
    k=kept.sort_values("touched_at").reset_index(drop=True)
    open_by_side={"upper":[], "lower":[]}  # (exit_time, level)
    keep_rows=[]
    for r in k.itertuples(index=False):
        lst=[(t,l) for (t,l) in open_by_side[r.side] if t> r.touched_at]
        if any(abs(l-r.level)<=tol for (t,l) in lst):
            open_by_side[r.side]=lst
            continue
        lst.append((r.exit_time, r.level)); open_by_side[r.side]=lst
        keep_rows.append(r._asdict() if hasattr(r,'_asdict') else dict(r._asdict()))
    return pd.DataFrame(keep_rows)

def main():
    touches,bars=load()
    kept=score_decoupled(touches,bars,WINNER)
    print(f"kept trades (winner, causal): {len(kept)}")

    print("\n=== concurrency (unrestricted) ===")
    cs=concurrency_stats(kept)
    for k,v in cs.items(): print(f"  {k}: {v}")
    # combined stop risk & 1-min adverse excursion (approx via anchor-based stop distance)
    kept=kept.copy()
    kept["stop_pts"]=np.minimum(WINNER.sl_mult*kept.anchor, WINNER.cap_ceiling)

    print("\n=== results under concurrency caps (raw pnl) ===")
    print(f"  unrestricted: {summ(kept['raw_pnl'])}")
    for cap in (2,1):
        df,mr=cap_concurrency(kept,cap)
        print(f"  max {cap} open: {summ(df['raw_pnl'])}   max_combined_stop_risk_pts={mr:.0f}  (=${mr*2:.0f} 1xMNQ each)")
    dfu,mru=cap_concurrency(kept,999)
    print(f"  max combined open stop risk, UNRESTRICTED: {mru:.0f} pts  (=${mru*2:.0f} on 1 MNQ each)")

    print("\n=== dedup same-direction near-levels (raw pnl) ===")
    for tol in (2,5,10,20):
        d=dedup(kept,tol)
        print(f"  tol {tol:>2}pt: {summ(d['raw_pnl'])}   dropped {len(kept)-len(d)}")

if __name__=="__main__":
    main()
