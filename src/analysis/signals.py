"""MARKET SIGNAL ENGINE (PART 10).

고정 임계값을 쓰지 않는다. 5년 분포의 percentile과
일간 변동의 z-score(역시 5년 분포 기준)로 판단한다.
"""
from __future__ import annotations

from typing import Any

from ..config import RELATIVE_PAIRS, SIGNAL_KEYS
from .trends import MetricStats, relative_strength, zscore_of_daily_move

# percentile 임계
P_EXTREME_HIGH = 95.0
P_HIGH = 85.0
P_LOW = 15.0
P_EXTREME_LOW = 5.0
# 일간 변동 z-score 임계
Z_SPIKE = 2.0
Z_EXTREME = 3.0


def detect(
    stats: dict[str, MetricStats],
    raw_series: dict[str, list[tuple[str, float]]],
) -> list[dict[str, Any]]:
    """이상 신호 리스트를 반환. 신호가 없으면 빈 리스트."""
    out: list[dict[str, Any]] = []

    for key in SIGNAL_KEYS:
        st = stats.get(key)
        if not st or not st.available:
            continue
        label = st.label
        p = st.percentile_5y

        # 1) 수준(level) 신호 — 5년 분포에서 극단인가
        if p is not None:
            if p >= P_EXTREME_HIGH:
                out.append(_sig(key, "LEVEL_EXTREME_HIGH", st,
                                f"{label} 5년 분포 {p:.0f}퍼센타일 (상위 5% 구간)"))
            elif p >= P_HIGH:
                out.append(_sig(key, "LEVEL_HIGH", st,
                                f"{label} 5년 분포 {p:.0f}퍼센타일"))
            elif p <= P_EXTREME_LOW:
                out.append(_sig(key, "LEVEL_EXTREME_LOW", st,
                                f"{label} 5년 분포 {p:.0f}퍼센타일 (하위 5% 구간)"))
            elif p <= P_LOW:
                out.append(_sig(key, "LEVEL_LOW", st,
                                f"{label} 5년 분포 {p:.0f}퍼센타일"))

        # 2) 변동(move) 신호 — 오늘 움직임이 평소 대비 얼마나 큰가
        z = zscore_of_daily_move(raw_series.get(key, []))
        if z is None:
            continue
        d1 = st.changes.get("1D", {})
        detail_move = _fmt_change(label, d1, st.unit)
        if z >= Z_EXTREME:
            out.append(_sig(key, "MOVE_SURGE_EXTREME", st, f"{detail_move} (5년 일간분포 +{z:.1f}σ)", z))
        elif z >= Z_SPIKE:
            out.append(_sig(key, "MOVE_SURGE", st, f"{detail_move} (+{z:.1f}σ)", z))
        elif z <= -Z_EXTREME:
            out.append(_sig(key, "MOVE_PLUNGE_EXTREME", st, f"{detail_move} ({z:.1f}σ)", z))
        elif z <= -Z_SPIKE:
            out.append(_sig(key, "MOVE_PLUNGE", st, f"{detail_move} ({z:.1f}σ)", z))

    out.extend(_relative_signals(raw_series, stats))
    return out


def _relative_signals(
    raw_series: dict[str, list[tuple[str, float]]], stats: dict[str, MetricStats]
) -> list[dict[str, Any]]:
    res = []
    for a, b, label in RELATIVE_PAIRS:
        if a not in raw_series or b not in raw_series:
            continue
        rs = relative_strength(raw_series[a], raw_series[b])
        d1 = rs.get("change_1d_pct")
        dn = rs.get("change_nd_pct")
        if d1 is None:
            continue
        # 상대강도 하루 변화가 1%p를 넘으면 주도권 변화로 본다.
        if abs(d1) >= 1.0:
            direction = "강해짐" if d1 > 0 else "약해짐"
            detail = f"{label}: 상대강도 하루 {d1:+.2f}% ({direction})"
            if dn is not None:
                detail += f", 20일 누적 {dn:+.2f}%"
            res.append(
                {
                    "key": f"rs_{a}_{b}",
                    "signal": "RELATIVE_STRENGTH_SHIFT",
                    "percentile": None,
                    "value": rs.get("ratio"),
                    "detail": detail,
                }
            )
    return res


def _sig(key: str, signal: str, st: MetricStats, detail: str, z: float | None = None) -> dict[str, Any]:
    return {
        "key": key,
        "signal": signal,
        "percentile": st.percentile_5y,
        "value": st.value,
        "detail": detail + (f" [z={z:.2f}]" if z is not None else ""),
    }


def _fmt_change(label: str, chg: dict[str, Any], unit: str) -> str:
    if not chg or chg.get("prev") is None:
        return f"{label} 변화 데이터 없음"
    if unit in ("%", "%p"):
        return f"{label} 하루 {chg['abs']:+.0f}bp"
    pct = chg.get("pct")
    return f"{label} 하루 {pct:+.2f}%" if pct is not None else f"{label} 하루 {chg['abs']:+.2f}"


def to_alert_lines(signals: list[dict[str, Any]], limit: int = 5) -> list[str]:
    """브리핑 ⑧번 섹션용 문자열. 심각도 순으로 정렬."""
    rank = {
        "MOVE_SURGE_EXTREME": 0, "MOVE_PLUNGE_EXTREME": 0,
        "LEVEL_EXTREME_HIGH": 1, "LEVEL_EXTREME_LOW": 1,
        "MOVE_SURGE": 2, "MOVE_PLUNGE": 2,
        "RELATIVE_STRENGTH_SHIFT": 3,
        "LEVEL_HIGH": 4, "LEVEL_LOW": 4,
    }
    ordered = sorted(signals, key=lambda s: rank.get(s["signal"], 9))
    return [f"🚨 {s['detail']}" for s in ordered[:limit]]
