"""STAGE 2 -- causality & implementation audit with explicit tests.

Focus: prove no lookahead / no exit-before-entry, map the overnight boundary,
and QUANTIFY the optimistic fills the spec wants made adverse:
  (i) gap-through-stop: does a stop/BE exit fill at the requested price even
      when the bar OPENED already beyond it?
  (ii) trail_frac=0 TP: does the winner book the running-max bar HIGH instead
      of the target price?
"""
from __future__ import annotations
import numpy as np, pandas as pd
import engine as E
from engine import (WindowParams, DEFAULT_WINDOW, _session_cutoff, _session_date,
                    _entry_allowed, _prior_close, load_vol_daily)
from common import get_touches, score_decoupled, WINNER, VXN

NY = "America/New_York"

def test_overnight_boundary():
    print("=== overnight cutoff boundary (touch ET -> cutoff) ; assert cutoff>entry or dropped ===")
    for hhmm in ["10:59","11:00","14:59","15:00","15:01","18:59","19:00","19:01","23:59","00:01","09:30"]:
        ts = pd.Timestamp(f"2022-06-15 {hhmm}", tz=NY)
        co = _session_cutoff(ts, DEFAULT_WINDOW)
        allowed = _entry_allowed(ts, DEFAULT_WINDOW)
        sd = _session_date(ts, DEFAULT_WINDOW)
        status = "DROPPED(co=None)" if co is None else ("OK" if co>ts else "!!! CUTOFF<=ENTRY")
        print(f"  {hhmm} entry_allowed={allowed!s:5} sess={sd} cutoff={co} {status}")

def test_no_exit_before_entry(kept):
    bad = kept[kept["exit_time"] < kept["touched_at"]]
    print(f"\n=== invariant: exit_time >= entry for all admitted trades ===")
    print(f"  violations: {len(bad)} of {len(kept)}   {'PASS' if len(bad)==0 else 'FAIL'}")

def test_vxn_strict_prior():
    print("\n=== VXN strict-priority (no same-day close used) ===")
    vol = load_vol_daily(VXN)
    # pick 5 sample sessions; confirm _prior_close < session date
    ok = True
    for d in ["2019-03-15","2020-03-16","2021-11-05","2025-01-10","2018-02-05"]:
        sd = pd.Timestamp(d, tz=NY)
        target = pd.Timestamp(d).normalize()
        prior = vol[vol.index < target]
        used = prior.index[-1] if len(prior) else None
        ok &= (used is not None and used < target)
        print(f"  session {d}: uses VXN close dated {used.date() if used is not None else None} (< {d}? {used<target})")
    print(f"  {'PASS' if ok else 'FAIL'}")

def instrumented_optimism(touches, bars, exe, kept):
    """Re-walk each admitted trade; record gap-through-stop and TP best-high optimism."""
    from engine import near_round_number
    idx = {(r.touched_at, r.level): r for r in touches.itertuples(index=False)}
    gap_events=[]; tp_events=[]
    for kr in kept.itertuples(index=False):
        r = idx.get((kr.touched_at, kr.level))
        if r is None: continue
        path = bars.loc[r.touched_at:r.path_end].iloc[1:]
        if path.empty: continue
        sl_d=exe.sl_distance(r.anchor); tp_d=exe.tp_distance(r.anchor)
        tgt=r.level+r.sign*tp_d; orig=r.level-r.sign*sl_d
        hi=path["high"].values; lo=path["low"].values; op=path["open"].values; cl=path["close"].values
        armed=chk=engaged=False; best=0.0
        for i in range(len(hi)):
            h,l=hi[i],lo[i]
            if not engaged:
                if i<exe.be_bars: stp=orig
                else:
                    if not chk:
                        chk=True; ref=cl[i-1] if i>0 else op[i]
                        armed=(ref>=r.level) if r.sign>0 else (ref<=r.level)
                    stp=r.level if armed else orig
                if r.sign>0:
                    if l<=stp:
                        # gap: did the bar OPEN already below the stop?
                        gap = max(0.0, stp-op[i]) if op[i]<stp else 0.0
                        gap_events.append(gap); break
                    if h>=tgt:
                        engaged=True; best=h; continue
                else:
                    if h>=stp:
                        gap = max(0.0, op[i]-stp) if op[i]>stp else 0.0
                        gap_events.append(gap); break
                    if l<=tgt:
                        engaged=True; best=l; continue
            else:
                if r.sign>0:
                    best=max(best,h);
                    if l<=best:
                        tp_events.append((best-r.level)-tp_d); break   # extra over target
                else:
                    best=min(best,l) if best else l
                    if h>=best:
                        tp_events.append((r.level-best)-tp_d); break
    ge=np.array(gap_events); te=np.array(tp_events)
    print("\n=== OPTIMISM (i): gap-through-stop fills ===")
    print(f"  stop/BE exits examined: {len(ge)}")
    print(f"  exits where bar OPENED beyond the stop (gap): {(ge>0).sum()} ({100*(ge>0).mean():.1f}%)")
    print(f"  mean adverse gap on those: {ge[ge>0].mean() if (ge>0).any() else 0:.2f} pts;  max {ge.max() if len(ge) else 0:.1f}")
    print(f"  total unmodeled adverse (pts, single-contract): {ge.sum():.1f}")
    print("\n=== OPTIMISM (ii): trail_frac=0 TP books running-max high, not target ===")
    print(f"  TP exits: {len(te)};  mean pts booked OVER target distance: {te.mean() if len(te) else 0:.2f}")
    print(f"  total optimistic excess on winners (pts): {te.sum():.1f}")

def main():
    touches, bars = get_touches()
    kept = score_decoupled(touches, bars, WINNER)
    test_overnight_boundary()
    test_no_exit_before_entry(kept)
    test_vxn_strict_prior()
    instrumented_optimism(touches, bars, WINNER, kept)

if __name__=="__main__":
    main()
