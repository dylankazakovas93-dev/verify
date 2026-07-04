"""STAGE 1 -- reproduce headline + full metric breakdown, original vs SAL-decoupled."""
from __future__ import annotations
import numpy as np, pandas as pd
from common import (get_touches, score_original, score_decoupled, WINNER, IN_SAMPLE, pf, maxdd)

NQ_PT = 20.0   # $/pt full NQ
MNQ_PT = 2.0   # $/pt micro

def block(kept, pnl_col, weighted_col=None):
    p = kept[pnl_col]
    w = kept[weighted_col] if weighted_col else p
    ex = kept["exit"].value_counts()
    tp, sl, be = ex.get("TP",0), ex.get("SL",0), ex.get("BE",0)
    cut = ex.get("cutoff",0)+ex.get("cutoff_eng",0)
    n = len(kept)
    nonbe = n - be
    wins, losses = p[p>0], p[p<0]
    out = {
        "n": n,
        "raw_net_pts": round(p.sum(),1),
        "weighted_net_ptct": round(w.sum(),1),
        "dollar_NQ": round(w.sum()*NQ_PT,0),
        "dollar_MNQ": round(w.sum()*MNQ_PT,0),
        "pf_raw": round(pf(p),3),
        "pf_weighted": round(pf(w),3),
        "outright_wr_%": round(100*(p>0).mean(),1),
        "target_hit_rate_exBE_%": round(100*tp/nonbe,1) if nonbe else None,
        "twr_TP/(TP+SL)_%": round(100*tp/(tp+sl),1) if (tp+sl) else None,
        "be_rate_%": round(100*be/n,1),
        "loss_rate_%": round(100*(p<0).mean(),1),
        "avg_winner": round(wins.mean(),1) if len(wins) else None,
        "avg_loser": round(losses.mean(),1) if len(losses) else None,
        "maxdd_raw": round(maxdd(p),1),
        "maxdd_weighted": round(maxdd(w),1),
        "exits": f"TP={tp} SL={sl} BE={be} cut={cut}",
    }
    return out

def yearly(kept, pnl_col):
    rows=[]
    for y,sub in kept.groupby("year"):
        p=sub[pnl_col]; ex=sub["exit"].value_counts()
        rows.append({"year":y,"n":len(sub),"net":round(p.sum(),1),"pf":round(pf(p),3),
                     "TP":ex.get("TP",0),"SL":ex.get("SL",0),"BE":ex.get("BE",0),
                     "cut":ex.get("cutoff",0)+ex.get("cutoff_eng",0)})
    return pd.DataFrame(rows)

def main():
    touches, bars = get_touches()
    print(f"touches built: {len(touches)}")

    ko = score_original(touches, bars, WINNER)
    kd = score_decoupled(touches, bars, WINNER)

    print("\n################ STAGE 1 ################")
    print("\n=== A) ORIGINAL engine (SAL sees SIZED pnl) -- headline reproduction ===")
    bo = block(ko, "pnl")
    for k,v in bo.items(): print(f"  {k:26} {v}")

    print("\n=== B) CORRECTED engine (SAL decoupled: raw sign/exit only) ===")
    bd = block(kd, "raw_pnl", "weighted_pnl")
    for k,v in bd.items(): print(f"  {k:26} {v}")

    print("\n=== A vs B: did sizing change trade admission? ===")
    print(f"  original kept n = {len(ko)}   decoupled kept n = {len(kd)}")
    print(f"  original weighted net = {ko['pnl'].sum():.1f}")
    print(f"  decoupled weighted net = {kd['weighted_pnl'].sum():.1f}")
    print(f"  admission delta (trades) = {len(kd)-len(ko)}")

    # round-number contribution (decoupled/clean)
    r = kd[kd.size_mult>1.0]
    print("\n=== round-number sizing contribution (decoupled) ===")
    print(f"  doubled trades: {len(r)} of {len(kd)}")
    print(f"  net w/o doubling (raw):     {kd['raw_pnl'].sum():.1f}   PF {pf(kd['raw_pnl']):.3f}")
    print(f"  net w/  doubling (weighted):{kd['weighted_pnl'].sum():.1f}   PF {pf(kd['weighted_pnl']):.3f}")
    print(f"  extra pts purely from 2x:   {(kd['weighted_pnl'].sum()-kd['raw_pnl'].sum()):.1f}")

    print("\n=== per-year (ORIGINAL, sized pnl) ===")
    print(yearly(ko,"pnl").to_string(index=False))
    print("\n=== per-year (CORRECTED, raw pnl) ===")
    print(yearly(kd,"raw_pnl").to_string(index=False))

    print("\n=== recency / concentration (corrected, raw) ===")
    tot=kd['raw_pnl'].sum()
    for yrs,lbl in [((2025,2026),"2025+2026"),(tuple(IN_SAMPLE),"in-sample yrs")]:
        s=kd[kd.year.isin(yrs)]['raw_pnl'].sum()
        print(f"  {lbl}: {100*s/tot:.1f}% of raw net")
    by=kd.groupby('year')['raw_pnl'].sum(); best=by.idxmax()
    print(f"  best year {best}: {100*by.max()/tot:.1f}% of raw net")

if __name__ == "__main__":
    main()
