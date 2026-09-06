"""반도체 분석 엔진 (PART 11, 12).

정량 데이터로 계산 가능한 항목만 점수를 낸다.
HBM 점유율, 파운드리 수주, 메모리 고정거래가 같은 항목은 무료 데이터가 없으므로
점수를 만들어내지 않고 None으로 남긴 뒤 AI가 뉴스 기반으로 판단하게 한다.
그 경우 근거에 '뉴스 기반 정성 판단'임을 반드시 표시한다.

특정 가격의 매수/매도는 지시하지 않는다(PART 12).
"""
from __future__ import annotations

from typing import Any

from .trends import MetricStats, relative_strength

# 메모리/HBM 관련 뉴스 판별 키워드
MEMORY_KEYWORDS = ["hbm", "dram", "nand", "memory", "메모리", "디램", "낸드"]
CAPEX_KEYWORDS = ["capex", "data center", "datacenter", "설비투자", "데이터센터"]


def _chg(st: MetricStats | None, period: str) -> float | None:
    if not st or not st.available:
        return None
    return (st.changes.get(period) or {}).get("pct")


def _bucket(v: float | None, strong: float, mild: float) -> int | None:
    if v is None:
        return None
    if v >= strong:
        return 2
    if v >= mild:
        return 1
    if v <= -strong:
        return -2
    if v <= -mild:
        return -1
    return 0


def cycle_score(
    stats: dict[str, MetricStats],
    raw_series: dict[str, list[tuple[str, float]]],
    news: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Semiconductor Cycle Score (-2 ~ +2)."""
    parts: list[dict[str, Any]] = []

    for key, label, strong, mild in (
        ("sox", "SOX 1개월", 8, 2),
        ("nvda", "NVDA 1개월", 10, 3),
        ("mu", "MU(마이크론) 1개월", 12, 4),
    ):
        v = _chg(stats.get(key), "1M")
        s = _bucket(v, strong, mild)
        if s is not None:
            parts.append({"factor": label, "score": s, "reason": f"{label} {v:+.1f}%"})

    # SOX 상대강도 — 반도체가 시장을 주도하는지
    if "sox" in raw_series and "sp500" in raw_series:
        rs = relative_strength(raw_series["sox"], raw_series["sp500"], window=20)
        dn = rs.get("change_nd_pct")
        s = _bucket(dn, 5, 1)
        if s is not None:
            parts.append(
                {"factor": "SOX 상대강도(20일)", "score": s, "reason": f"S&P500 대비 {dn:+.1f}%"}
            )

    memory_news = _count_news(news, MEMORY_KEYWORDS)
    capex_news = _count_news(news, CAPEX_KEYWORDS)

    scores = [p["score"] for p in parts]
    total = round(sum(scores) / len(scores)) if scores else None
    return {
        "score": total,
        "components": parts,
        "coverage": f"{len(parts)}개 항목",
        "memory_news_count": memory_news,
        "capex_news_count": capex_news,
        "unavailable": [
            "메모리 고정거래가(DRAM/NAND spot·contract) — 무료 공개 데이터 없음",
            "HBM 공급계약·점유율 — 공시/뉴스 의존",
        ],
    }


def _count_news(news: list[dict[str, Any]] | None, keywords: list[str]) -> int:
    if not news:
        return 0
    return sum(1 for n in news if any(k in n["headline"].lower() for k in keywords))


# ------------------------------------------------------------------ 개별 종목
SAMSUNG_FACTORS = ["Memory", "HBM", "Foundry", "AI", "NVIDIA", "DRAM", "NAND", "FX", "Foreign Flow"]
HYNIX_FACTORS = ["HBM", "DRAM", "AI", "NVIDIA", "Memory Price", "CAPEX", "Foreign Flow"]

# 정량 계산 가능한 항목 → 계산 방식
QUANTIFIABLE = {"NVIDIA", "FX", "Foreign Flow", "AI"}


def company_scores(
    company: str,
    stats: dict[str, MetricStats],
    raw_series: dict[str, list[tuple[str, float]]],
) -> list[dict[str, Any]]:
    factors = SAMSUNG_FACTORS if company == "samsung" else HYNIX_FACTORS
    out: list[dict[str, Any]] = []

    for f in factors:
        score: int | None = None
        reason = "무료 정량 데이터 없음 → AI가 뉴스 기반으로 판단"

        if f == "NVIDIA":
            v = _chg(stats.get("nvda"), "5D")
            score = _bucket(v, 6, 2)
            if score is not None:
                reason = f"NVDA 5일 {v:+.1f}%"

        elif f == "AI":
            v = _chg(stats.get("sox"), "5D")
            score = _bucket(v, 5, 1.5)
            if score is not None:
                reason = f"SOX 5일 {v:+.1f}% (AI 수요 프록시)"

        elif f == "FX":
            v = _chg(stats.get("usdkrw"), "1M")
            if v is not None:
                # 원화 약세(환율 상승) = 수출기업 실적에 단기 우호적
                score = _bucket(v, 3, 1)
                reason = f"USD/KRW 1개월 {v:+.1f}% ({'원화 약세' if v > 0 else '원화 강세'})"

        elif f == "Foreign Flow":
            key = f"{company}_foreign_net"
            series = raw_series.get(key, [])
            if series:
                recent = [v for _, v in series[-5:]]
                total = sum(recent)
                score = 2 if total > 5e11 else (1 if total > 0 else (-1 if total > -5e11 else -2))
                reason = f"외국인 5일 누적 순매수 {total/1e8:,.0f}억원"

        out.append({"company": company, "factor": f, "score": score, "reason": reason})

    return out


def verdict(scores: list[dict[str, Any]]) -> dict[str, Any]:
    """🟢/🟡/🔴 판정. 정량 점수가 있는 항목만으로 계산한다."""
    valid = [s["score"] for s in scores if s["score"] is not None]
    if not valid:
        return {"emoji": "⚪", "label": "판단 보류", "avg": None, "coverage": 0}
    avg = sum(valid) / len(valid)
    if avg >= 0.7:
        e, label = "🟢", "Positive"
    elif avg <= -0.7:
        e, label = "🔴", "Negative"
    else:
        e, label = "🟡", "Neutral"
    return {"emoji": e, "label": label, "avg": round(avg, 2), "coverage": len(valid)}
