"""MACRO REGIME ENGINE (PART 8).

6개 축에 -2 ~ +2 점수를 매긴다.

부호 규약: **+는 위험자산에 우호적** 방향이다.
  예) 금리가 오르면 Rates 축은 마이너스.

여기서 나오는 점수는 '정량 데이터만 본 1차 판단'이다.
뉴스와 맥락을 반영한 최종 판단은 AI 애널리스트가 재평가한다(PART 8 마지막 문단).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .trends import MetricStats

AXES = ["Growth", "Inflation", "Rates", "Liquidity", "Earnings", "Risk Appetite"]


@dataclass
class AxisScore:
    axis: str
    score: int | None
    reasons: list[str] = field(default_factory=list)
    inputs_used: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def _clamp(x: float) -> int:
    return int(max(-2, min(2, round(x))))


def _get(stats: dict[str, MetricStats], key: str) -> MetricStats | None:
    st = stats.get(key)
    return st if st and st.available else None


def _chg(st: MetricStats | None, period: str, field_: str = "pct") -> float | None:
    if not st:
        return None
    return (st.changes.get(period) or {}).get(field_)


def score_growth(stats: dict[str, MetricStats]) -> AxisScore:
    a = AxisScore("Growth", None)
    pts: list[float] = []

    claims = _get(stats, "jobless_claims")
    if claims:
        yoy = claims.yoy
        if yoy.get("available") and yoy.get("pct_change") is not None:
            v = yoy["pct_change"]
            pts.append(-1 if v > 10 else (1 if v < -5 else 0))
            a.reasons.append(f"신규 실업수당 청구 전년비 {v:+.1f}%")
            a.inputs_used.append("jobless_claims")
    else:
        a.missing.append("jobless_claims")

    unemp = _get(stats, "unemployment")
    if unemp:
        y = unemp.yoy
        if y.get("available"):
            d = y["current"] - y["previous"]
            pts.append(-1.5 if d >= 0.5 else (-0.5 if d > 0 else 1))
            a.reasons.append(f"실업률 {y['current']:.1f}% (1년 전 {y['previous']:.1f}%)")
            a.inputs_used.append("unemployment")
    else:
        a.missing.append("unemployment")

    retail = _get(stats, "retail_sales")
    if retail and retail.yoy.get("available") and retail.yoy.get("pct_change") is not None:
        v = retail.yoy["pct_change"]
        pts.append(1 if v > 3 else (0 if v > 0 else -1))
        a.reasons.append(f"소매판매 전년비 {v:+.1f}%")
        a.inputs_used.append("retail_sales")

    # 구리/금 비율: 실물경기 선행 프록시
    cu, au = _get(stats, "copper"), _get(stats, "gold")
    if cu and au:
        cu3, au3 = _chg(cu, "3M"), _chg(au, "3M")
        if cu3 is not None and au3 is not None:
            diff = cu3 - au3
            pts.append(1 if diff > 5 else (-1 if diff < -5 else 0))
            a.reasons.append(f"구리-금 3개월 상대성과 {diff:+.1f}%p")
            a.inputs_used += ["copper", "gold"]

    a.score = _clamp(sum(pts) / len(pts) * 2) if pts else None
    return a


def score_inflation(stats: dict[str, MetricStats]) -> AxisScore:
    """+는 '물가가 위험자산에 우호적'(= 안정/둔화) 방향."""
    a = AxisScore("Inflation", None)
    pts: list[float] = []

    for key, label in (("core_cpi", "Core CPI"), ("core_pce", "Core PCE")):
        st = _get(stats, key)
        if not st:
            a.missing.append(key)
            continue
        y = st.yoy
        if not y.get("available") or y.get("pct_change") is None:
            continue
        v = y["pct_change"]  # 물가지수의 전년동월 상승률 = 인플레이션율
        if v < 2.2:
            pts.append(2)
        elif v < 2.7:
            pts.append(1)
        elif v < 3.2:
            pts.append(0)
        elif v < 4.0:
            pts.append(-1)
        else:
            pts.append(-2)
        a.reasons.append(f"{label} 전년동월 {v:+.2f}%")
        a.inputs_used.append(key)

    wti = _get(stats, "wti")
    if wti:
        v = _chg(wti, "3M")
        if v is not None:
            pts.append(-1 if v > 15 else (1 if v < -15 else 0))
            a.reasons.append(f"WTI 3개월 {v:+.1f}%")
            a.inputs_used.append("wti")

    a.score = _clamp(sum(pts) / len(pts)) if pts else None
    return a


def score_rates(stats: dict[str, MetricStats]) -> AxisScore:
    a = AxisScore("Rates", None)
    pts: list[float] = []

    ten = _get(stats, "ust10y")
    if ten:
        bp_1m = (ten.changes.get("1M") or {}).get("abs")
        if bp_1m is not None:
            pts.append(-2 if bp_1m > 40 else (-1 if bp_1m > 15 else (2 if bp_1m < -40 else (1 if bp_1m < -15 else 0))))
            a.reasons.append(f"10년물 1개월 {bp_1m:+.0f}bp (현재 {ten.value:.2f}%)")
            a.inputs_used.append("ust10y")
        if ten.percentile_5y is not None:
            p = ten.percentile_5y
            pts.append(-1 if p > 80 else (1 if p < 20 else 0))
            a.reasons.append(f"10년물 5년 분포 {p:.0f}퍼센타일")
    else:
        a.missing.append("ust10y")

    spread = _get(stats, "spread_10y2y")
    if spread:
        a.inputs_used.append("spread_10y2y")
        if spread.value < 0:
            pts.append(-1)
            a.reasons.append(f"10Y-2Y 역전 상태 ({spread.value:.2f}%p)")
        else:
            a.reasons.append(f"10Y-2Y {spread.value:+.2f}%p")
        bp = (spread.changes.get("1M") or {}).get("abs")
        if bp is not None and abs(bp) > 20:
            a.reasons.append(f"곡선 1개월 {bp:+.0f}bp 변화")

    a.score = _clamp(sum(pts) / len(pts) * 1.5) if pts else None
    return a


def score_liquidity(stats: dict[str, MetricStats]) -> AxisScore:
    a = AxisScore("Liquidity", None)
    pts: list[float] = []

    nfci = _get(stats, "nfci")
    if nfci:
        # NFCI는 음수일수록 금융여건이 완화적
        v = nfci.value
        pts.append(2 if v < -0.5 else (1 if v < -0.2 else (0 if v < 0.1 else -2)))
        a.reasons.append(f"NFCI {v:+.2f} ({'완화적' if v < 0 else '긴축적'})")
        a.inputs_used.append("nfci")
    else:
        a.missing.append("nfci")

    fed = _get(stats, "fed_balance")
    if fed:
        v = _chg(fed, "3M")
        if v is not None:
            pts.append(1 if v > 0.5 else (-1 if v < -1.5 else 0))
            a.reasons.append(f"Fed 대차대조표 3개월 {v:+.2f}%")
            a.inputs_used.append("fed_balance")

    dxy = _get(stats, "dxy")
    if dxy:
        v = _chg(dxy, "1M")
        if v is not None:
            # 달러 강세 = 글로벌 유동성 긴축
            pts.append(-1 if v > 2 else (1 if v < -2 else 0))
            a.reasons.append(f"DXY 1개월 {v:+.1f}%")
            a.inputs_used.append("dxy")

    a.score = _clamp(sum(pts) / len(pts) * 1.5) if pts else None
    return a


def score_earnings(stats: dict[str, MetricStats]) -> AxisScore:
    """주의: 실제 EPS 데이터가 아니라 시장가격 기반 프록시다."""
    a = AxisScore("Earnings", None)
    a.reasons.append("(실적 데이터가 아닌 가격 모멘텀 프록시)")
    pts: list[float] = []

    for key, label, w in (("sp500", "S&P500", 1.0), ("sox", "SOX", 1.0), ("nasdaq", "NASDAQ", 1.0)):
        st = _get(stats, key)
        if not st:
            a.missing.append(key)
            continue
        v = _chg(st, "3M")
        if v is None:
            continue
        pts.append(2 if v > 10 else (1 if v > 3 else (0 if v > -3 else (-1 if v > -10 else -2))))
        a.reasons.append(f"{label} 3개월 {v:+.1f}%")
        a.inputs_used.append(key)

    a.score = _clamp(sum(pts) / len(pts)) if pts else None
    return a


def score_risk_appetite(stats: dict[str, MetricStats]) -> AxisScore:
    a = AxisScore("Risk Appetite", None)
    pts: list[float] = []

    vix = _get(stats, "vix")
    if vix and vix.percentile_5y is not None:
        p = vix.percentile_5y
        pts.append(2 if p < 20 else (1 if p < 40 else (0 if p < 60 else (-1 if p < 85 else -2))))
        a.reasons.append(f"VIX {vix.value:.1f} (5년 {p:.0f}퍼센타일)")
        a.inputs_used.append("vix")
    else:
        a.missing.append("vix")

    rut, spx = _get(stats, "russell2000"), _get(stats, "sp500")
    if rut and spx:
        r, s = _chg(rut, "1M"), _chg(spx, "1M")
        if r is not None and s is not None:
            diff = r - s
            pts.append(1 if diff > 1.5 else (-1 if diff < -1.5 else 0))
            a.reasons.append(f"소형주-대형주 1개월 상대성과 {diff:+.1f}%p")
            a.inputs_used += ["russell2000", "sp500"]

    gold = _get(stats, "gold")
    if gold:
        v = _chg(gold, "1M")
        if v is not None and abs(v) > 4:
            pts.append(-1 if v > 4 else 1)
            a.reasons.append(f"금 1개월 {v:+.1f}% (안전자산 선호 {'상승' if v > 0 else '하락'})")
            a.inputs_used.append("gold")

    a.score = _clamp(sum(pts) / len(pts) * 1.5) if pts else None
    return a


def classify(scores: dict[str, int | None]) -> tuple[str, str]:
    """국면 분류. (국면명, 근거) 반환."""
    g = scores.get("Growth")
    i = scores.get("Inflation")
    r = scores.get("Rates")
    risk = scores.get("Risk Appetite")

    if any(v is None for v in (g, i, risk)):
        return "UNDETERMINED", "핵심 축 점수 산출에 필요한 데이터가 부족합니다."

    if risk <= -2:
        return "RISK OFF", "위험선호가 극단적으로 위축된 상태입니다."
    if g <= -1 and i <= -1:
        return "STAGFLATION RISK", "성장 둔화와 물가 압력이 동시에 나타납니다."
    if g <= -2:
        return "RECESSION RISK", "성장 지표가 뚜렷하게 악화 중입니다."
    if g <= -1 and risk <= -1:
        return "RECESSION RISK", "성장 둔화와 위험회피가 겹칩니다."
    if g >= 1 and i >= 1 and (r or 0) >= 0:
        return "MID EXPANSION", "성장·물가·금리가 모두 우호적입니다."
    if g >= 1 and i <= 0:
        return "LATE EXPANSION", "성장은 유지되나 물가·금리 부담이 남아 있습니다."
    if g >= 0 and risk >= 1:
        return "EARLY EXPANSION", "성장이 바닥을 지나며 위험선호가 회복되고 있습니다."
    if g >= 0 and risk <= 0:
        return "RECOVERY", "지표가 혼조이나 침체 신호는 뚜렷하지 않습니다."
    return "UNDETERMINED", "명확한 국면으로 분류하기 어려운 혼조 상태입니다."


def evaluate(stats: dict[str, MetricStats]) -> dict[str, Any]:
    axes = [
        score_growth(stats),
        score_inflation(stats),
        score_rates(stats),
        score_liquidity(stats),
        score_earnings(stats),
        score_risk_appetite(stats),
    ]
    scores = {a.axis: a.score for a in axes}
    valid = [v for v in scores.values() if v is not None]
    total = sum(valid) if valid else None
    regime, rationale = classify(scores)
    return {
        "axes": [a.__dict__ for a in axes],
        "scores": scores,
        "total": total,
        "coverage": f"{len(valid)}/6",
        "regime": regime,
        "rationale": rationale,
        "method": "정량 규칙 기반 1차 점수 (AI가 뉴스 포함해 재평가)",
    }


def emoji(score: int | None) -> str:
    if score is None:
        return "⚪"
    return {2: "🟢", 1: "🟢", 0: "🟡", -1: "🔴", -2: "🔴"}[score]
