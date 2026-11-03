"""漲跌預測的「統計基準模型」。

目的不是保證準確，而是提供一個**可檢驗**的對照組：
- 只用當日收盤（含）以前的價格／成交量特徵，標籤是未來 N 個交易日是否上漲。
- 以 walk-forward（滾動重訓、訓練集與測試集間隔 N 日避免標籤洩漏）計算樣本外成績。
- 與「永遠猜訓練期多數方向」的天真基準比較；未優於基準時，機率只當作無訊號。

上游 daily_stock_analysis 的 LLM 看多／看空結論另行記錄，並與本模型用同一套
`tracking.py` 追蹤實際命中率，讓兩者都接受事後檢驗。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd

MODEL_NAME = "logit_v1"


@dataclass
class Prediction:
    ok: bool
    reason: Optional[str] = None
    horizon: int = 5
    asof: Optional[pd.Timestamp] = None
    prob_up: Optional[float] = None
    signal: str = "無訊號"
    beats_baseline: bool = False
    n_train: int = 0
    n_oos: int = 0
    oos_start: Optional[pd.Timestamp] = None
    oos_end: Optional[pd.Timestamp] = None
    acc_model: Optional[float] = None
    acc_naive: Optional[float] = None
    up_rate_oos: Optional[float] = None
    brier_model: Optional[float] = None
    brier_clim: Optional[float] = None
    bss: Optional[float] = None
    auc: Optional[float] = None
    features: List[str] = field(default_factory=list)


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def build_features(history: pd.DataFrame, benchmark: Optional[pd.Series] = None) -> pd.DataFrame:
    close = history["close"].astype(float)
    volume = history["volume"].astype(float)
    r1 = close.pct_change()
    f = pd.DataFrame(index=history.index)
    f["r1"] = r1
    f["r5"] = close.pct_change(5)
    f["r10"] = close.pct_change(10)
    f["r20"] = close.pct_change(20)
    f["vol20"] = r1.rolling(20).std()
    f["ma20_gap"] = close / close.rolling(20).mean() - 1
    f["ma60_gap"] = close / close.rolling(60).mean() - 1
    f["rsi14"] = (_rsi(close) - 50) / 50
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    f["macd_hist"] = (macd - macd.ewm(span=9, adjust=False).mean()) / close
    logv = np.log1p(volume.clip(lower=0))
    std = logv.rolling(20).std().replace(0, np.nan)
    f["vol_z20"] = (logv - logv.rolling(20).mean()) / std
    if benchmark is not None and not benchmark.empty:
        b = benchmark.astype(float).sort_index()
        b = b[~b.index.duplicated(keep="last")]
        b = b.reindex(b.index.union(history.index)).ffill().reindex(history.index)
        f["bench_r5"] = b.pct_change(5, fill_method=None)
        f["bench_r20"] = b.pct_change(20, fill_method=None)
    return f.replace([np.inf, -np.inf], np.nan)


def make_labels(close: pd.Series, horizon: int) -> pd.Series:
    fwd = close.shift(-horizon) / close - 1
    y = (fwd > 0).astype(float)
    y[fwd.isna()] = np.nan
    return y


def _model():
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    return make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=1000))


def walk_forward(
    history: pd.DataFrame,
    benchmark: Optional[pd.Series] = None,
    horizon: int = 5,
    min_train_rows: int = 250,
    retrain_every: int = 21,
    min_oos_samples: int = 120,
) -> Prediction:
    pred = Prediction(ok=False, horizon=horizon)
    if history is None or len(history) < min_train_rows + horizon + 60:
        pred.reason = f"價格歷史僅 {0 if history is None else len(history)} 筆，少於模型所需"
        return pred

    X = build_features(history, benchmark)
    # 基準指數若缺資料過多就不用，避免把可用樣本砍光。
    for col in ("bench_r5", "bench_r20"):
        if col in X and X[col].notna().mean() < 0.8:
            X = X.drop(columns=col)
    y = make_labels(history["close"], horizon)
    n = len(X)
    idx = np.arange(n)
    valid = X.notna().all(axis=1).to_numpy()
    labeled = y.notna().to_numpy()
    usable = valid & labeled
    Xv, yv = X.to_numpy(), y.to_numpy()
    pred.features = list(X.columns)

    probs, truths, base_rates, test_pos = [], [], [], []
    cum_usable = np.cumsum(usable)
    first = None
    for t in range(n):
        cut = t - 1 - horizon
        if cut >= 0 and cum_usable[cut] >= min_train_rows:
            first = t
            break
    if first is None:
        pred.reason = "可用訓練樣本不足"
        return pred

    for start in range(first, n, retrain_every):
        train = usable & (idx <= start - 1 - horizon)
        test = usable & (idx >= start) & (idx < start + retrain_every)
        if not test.any():
            continue
        ytr = yv[train]
        if len(np.unique(ytr)) < 2:
            continue
        model = _model().fit(Xv[train], ytr)
        p = model.predict_proba(Xv[test])[:, 1]
        probs.extend(p.tolist())
        truths.extend(yv[test].tolist())
        base_rates.extend([float(ytr.mean())] * int(test.sum()))
        test_pos.extend(idx[test].tolist())

    if len(probs) < min_oos_samples:
        pred.reason = f"樣本外僅 {len(probs)} 筆，少於 {min_oos_samples} 筆門檻"
        pred.n_oos = len(probs)
        return pred

    p = np.asarray(probs)
    yt = np.asarray(truths)
    br = np.asarray(base_rates)
    pred.n_oos = len(p)
    pred.oos_start = X.index[test_pos[0]]
    pred.oos_end = X.index[test_pos[-1]]
    pred.acc_model = float(((p >= 0.5) == (yt == 1)).mean())
    pred.acc_naive = float(((br >= 0.5) == (yt == 1)).mean())
    pred.up_rate_oos = float(yt.mean())
    pred.brier_model = float(((p - yt) ** 2).mean())
    pred.brier_clim = float(((br - yt) ** 2).mean())
    pred.bss = 1 - pred.brier_model / pred.brier_clim if pred.brier_clim > 0 else None
    if len(np.unique(yt)) == 2:
        from sklearn.metrics import roc_auc_score

        pred.auc = float(roc_auc_score(yt, p))

    final_train = usable & (idx <= n - 1 - horizon)
    if not valid[-1]:
        pred.reason = "最新一日特徵不完整，無法產生機率"
        return pred
    model = _model().fit(Xv[final_train], yv[final_train])
    pred.n_train = int(final_train.sum())
    pred.prob_up = float(model.predict_proba(Xv[-1:])[:, 1][0])
    pred.asof = X.index[-1]
    pred.ok = True
    pred.beats_baseline = bool(
        pred.acc_model - pred.acc_naive >= 0.02 and pred.bss is not None and pred.bss > 0
    )
    if not pred.beats_baseline:
        pred.signal = "無明確訊號（樣本外未優於基準）"
    elif pred.prob_up >= 0.55:
        pred.signal = "偏多"
    elif pred.prob_up <= 0.45:
        pred.signal = "偏空"
    else:
        pred.signal = "中性"
    return pred


def direction_of(pred: Prediction) -> int:
    if not pred.ok or not pred.beats_baseline or pred.prob_up is None:
        return 0
    if pred.prob_up >= 0.55:
        return 1
    if pred.prob_up <= 0.45:
        return -1
    return 0
