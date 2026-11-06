import numpy as np
import pandas as pd

from monitor import predict


def _series(returns: np.ndarray, start: str = "2020-01-01") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=len(returns))
    close = 100 * np.exp(np.cumsum(returns))
    rng = np.random.default_rng(1)
    return pd.DataFrame({"close": close, "volume": rng.integers(1_000, 2_000, len(returns))}, index=idx)


def test_labels_have_no_future_for_last_rows():
    hist = _series(np.full(30, 0.01))
    y = predict.make_labels(hist["close"], 5)
    assert y.iloc[-5:].isna().all()
    assert (y.iloc[:-5] == 1).all()


def test_random_walk_does_not_claim_signal():
    rng = np.random.default_rng(42)
    hist = _series(rng.normal(0, 0.02, 1000))
    p = predict.walk_forward(hist, horizon=5, min_train_rows=250, retrain_every=21, min_oos_samples=120)
    assert p.ok
    assert 0 <= p.prob_up <= 1
    assert p.n_oos >= 120
    # 純隨機漫步不應被判讀為偏多／偏空（固定種子，結果可重現）
    assert p.beats_baseline is False
    assert p.signal.startswith("無明確訊號")
    assert predict.direction_of(p) == 0


def test_mean_reverting_series_is_detected():
    # 構造強烈均值回歸：近 5 日漲越多，未來 5 日越可能跌。模型應能在樣本外勝過天真基準。
    rng = np.random.default_rng(7)
    n = 1200
    r = np.zeros(n)
    noise = rng.normal(0, 0.01, n)
    for t in range(5, n):
        r[t] = -0.25 * r[t - 5 : t].sum() + noise[t]
    hist = _series(r)
    p = predict.walk_forward(hist, horizon=5, min_train_rows=250, retrain_every=21, min_oos_samples=120)
    assert p.ok
    assert p.acc_model > p.acc_naive
    assert p.beats_baseline is True


def test_insufficient_history():
    hist = _series(np.full(100, 0.001))
    p = predict.walk_forward(hist, min_train_rows=250)
    assert not p.ok and "少於" in p.reason


def test_benchmark_alignment_handles_other_calendar():
    rng = np.random.default_rng(3)
    hist = _series(rng.normal(0, 0.02, 600))
    bench_idx = pd.date_range(hist.index[0], hist.index[-1], freq="B")[::1]
    bench = pd.Series(100 + np.cumsum(rng.normal(0, 1, len(bench_idx))), index=bench_idx)
    f = predict.build_features(hist, bench)
    assert "bench_r5" in f.columns
    assert f["bench_r5"].iloc[30:].notna().mean() > 0.95
