"""Build + cache the canonical CAUSAL augmented touch set (Stage 3 onward)."""
from __future__ import annotations
import os, pickle
import pandas as pd
from engine import load_1m_ohlcv, load_vol_daily, NQ_LEVEL_PARAMS
from common import VXN
from stage3 import build_touches_aug

CAUSAL="/home/user/verify/audit/nq_1m_causal.csv.gz"
PKL="/home/user/verify/audit/_causal_touches.pkl"

def load():
    bars=load_1m_ohlcv(CAUSAL)
    if os.path.exists(PKL):
        touches=pickle.load(open(PKL,"rb"))
    else:
        vol=load_vol_daily(VXN)
        touches=build_touches_aug(bars,vol,NQ_LEVEL_PARAMS)
        pickle.dump(touches,open(PKL,"wb"))
    return touches, bars
