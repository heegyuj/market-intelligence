"""추세 계산 엔진 (PART 5, 6, 7).

각 지표에 대해
  현재값 → 기간별 변화 → 5년 분포 내 위치 → 전년 동월 비교
를 계산한다. 데이터가 부족하면 해당 항목만 None으로 둔다(추정 금지).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Sequence

import numpy as np
import pandas as pd

LOOKBACKS = {
    "1D": 1,
    "5D": 5,
    "1M": 30,
    "3M": 91,
    "6M": 182,
    "1Y": 365,
    "5Y": 1826,
}


@dataclass
class MetricStats:
    key: str
    label: str
    unit: str
    freq: str
    value: float | None = None
    data_date: str | None = None
    changes: dict[str, dict[str, float | None]] = field(default_factory=dict)
    stats_5y: dict[str, float | None] = field(default_factory=dict)
    percentile_5y: float | None = None
    yoy: dict[str, Any] = field(default_factory=dict)
    source: str | None = None
    source_url: str | None = None
    available: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _to_series(rows: Sequence[tuple[str, float]]) -> pd.Series:
    if not rows:
        return pd.Series(dtype="float64")
    idx = pd.to_datetime([r[0] for r in rows])
    s = pd.Series([r[1] for r in rows], index=idx, dtype="float64").sort_index()
    return s[~s.index.duplicated(keep="last")]


def _value_at(s: pd.Series, target: pd.Timestamp, tolerance_days: int = 7) -> float | None:
    """target 이하의 가장 최근 값. 허용 오차를 넘으면 None(과거값을 억지로 끌어오지 않는다)."""
    prior = s[s.index <= target]
    if prior.empty:
        return None
    last_idx = prior.index[-1]
    if (target - last_idx).days > tolerance_days + 25:
        return None
    return float(prior.iloc[-1])


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old) * 100.0


def compute(
    key: str,
    label: str,
    rows: Sequence[tuple[str, float]],
    unit: str = "",
    freq: str = "daily",
    as_of: date | None = None,
    source: str | None = None,
    source_url: str | None = None,
    note: str = "",
) -> MetricStats:
    st = MetricStats(key=key, label=label, unit=unit, freq=freq, source=source,
                     source_url=source_url, note=note)
    s = _to_series(rows)
    if s.empty:
        return st

    as_of = as_of or date.today()
    cutoff = pd.Timestamp(as_of)
    s = s[s.index <= cutoff]
    if s.empty:
        return st

    st.available = True
    st.value = float(s.iloc[-1])
    st.data_date = s.index[-1].date().isoformat()
    last_ts = s.index[-1]

    # ---- 기간별 변화 ----
    # %/%p 단위 지표(금리, 실업률 등)는 변화율보다 절대 변화(bp/%p)가 의미 있다.
    is_rate = unit in ("%", "%p")
    tol = {"daily": 7, "weekly": 14, "monthly": 45, "quarterly": 120}.get(freq, 7)

    for name, days in LOOKBACKS.items():
        # 월/분기 지표에 1D·5D 변화는 무의미하므로 건너뛴다.
        if freq in ("monthly", "quarterly") and name in ("1D", "5D"):
            continue
        if freq == "weekly" and name == "1D":
            continue
        prev = _value_at(s, last_ts - pd.Timedelta(days=days), tolerance_days=tol)
        if prev is None:
            st.changes[name] = {"prev": None, "abs": None, "pct": None}
            continue
        abs_chg = st.value - prev
        st.changes[name] = {
            "prev": prev,
            "abs": round(abs_chg * (100 if is_rate else 1), 4) if is_rate else round(abs_chg, 4),
            "abs_unit": "bp" if is_rate else unit,
            "pct": None if is_rate else _pct(st.value, prev),
        }

    # ---- 5년 분포 ----
    five_y = s[s.index >= last_ts - pd.Timedelta(days=1826)]
    if len(five_y) >= 20:
        st.stats_5y = {
            "average": float(five_y.mean()),
            "median": float(five_y.median()),
            "high": float(five_y.max()),
            "low": float(five_y.min()),
            "high_date": five_y.idxmax().date().isoformat(),
            "low_date": five_y.idxmin().date().isoformat(),
            "n": int(len(five_y)),
            "years_covered": round((five_y.index[-1] - five_y.index[0]).days / 365.25, 1),
        }
        st.percentile_5y = float((five_y <= st.value).mean() * 100.0)

    # ---- 전년 동월 (PART 6) ----
    st.yoy = _year_over_year(s, st.value, last_ts, freq, is_rate)
    return st


def _year_over_year(
    s: pd.Series, current: float, last_ts: pd.Timestamp, freq: str, is_rate: bool
) -> dict[str, Any]:
    """월간 지표는 같은 '월', 일간 지표는 같은 '날짜'와 비교한다."""
    if freq in ("monthly", "quarterly"):
        target_period = (last_ts - pd.DateOffset(years=1)).to_period("M")
        same_month = s[s.index.to_period("M") == target_period]
        if same_month.empty:
            return {"available": False}
        prev = float(same_month.iloc[-1])
        prev_label = str(target_period)
    else:
        target = last_ts - pd.DateOffset(years=1)
        prev = _value_at(s, target, tolerance_days=10)
        if prev is None:
            return {"available": False}
        prev_label = target.date().isoformat()

    return {
        "available": True,
        "current": current,
        "previous": prev,
        "previous_label": prev_label,
        "abs_change": round((current - prev) * (100 if is_rate else 1), 4),
        "abs_unit": "bp" if is_rate else "",
        "pct_change": None if is_rate else _pct(current, prev),
    }


def relative_strength(
    a: pd.Series | Sequence[tuple[str, float]],
    b: pd.Series | Sequence[tuple[str, float]],
    window: int = 20,
) -> dict[str, float | None]:
    """A/B 상대강도 비율의 최근 변화. 값이 커지면 A가 상대적으로 강해진 것."""
    sa = a if isinstance(a, pd.Series) else _to_series(a)
    sb = b if isinstance(b, pd.Series) else _to_series(b)
    if sa.empty or sb.empty:
        return {"ratio": None, "change_1d_pct": None, "change_nd_pct": None, "window": window}

    joined = pd.concat([sa, sb], axis=1, keys=["a", "b"]).dropna()
    if len(joined) < 2:
        return {"ratio": None, "change_1d_pct": None, "change_nd_pct": None, "window": window}

    ratio = joined["a"] / joined["b"]
    out: dict[str, float | None] = {"ratio": float(ratio.iloc[-1]), "window": window}
    out["change_1d_pct"] = _pct(float(ratio.iloc[-1]), float(ratio.iloc[-2]))
    if len(ratio) > window:
        out["change_nd_pct"] = _pct(float(ratio.iloc[-1]), float(ratio.iloc[-window - 1]))
    else:
        out["change_nd_pct"] = None
    return out


def zscore_of_daily_move(rows: Sequence[tuple[str, float]], lookback_days: int = 1826) -> float | None:
    """오늘 일간 변화가 5년 일간변동 분포에서 몇 시그마인지. 신호 탐지에 사용."""
    s = _to_series(rows)
    if len(s) < 30:
        return None
    s = s[s.index >= s.index[-1] - pd.Timedelta(days=lookback_days)]
    rets = s.pct_change().dropna()
    if len(rets) < 30 or rets.std() == 0:
        return None
    return float((rets.iloc[-1] - rets.mean()) / rets.std())
