"""BRIEFING QUALITY CONTROL (PART 18).

두 단계로 검증한다.
1) 규칙 기반 — 숫자 대조, 미래 날짜, 오래된 데이터, 투자권유 표현
2) AI 검증 — 모순, 추정을 사실처럼 쓴 표현 (analyst.verify)

규칙 검증은 AI 없이도 항상 동작한다.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any

from ..analysis.trends import MetricStats

log = logging.getLogger(__name__)

# 브리핑 본문에서 숫자를 뽑는다 (쉼표/소수점/부호 포함)
NUMBER_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")
DATE_RE = re.compile(r"20\d{2}[-.]\d{1,2}[-.]\d{1,2}")

# 투자 권유로 읽힐 수 있는 표현
ADVICE_PATTERNS = [
    r"매수\s*(추천|권장|타이밍|시점)", r"매도\s*(추천|권장|타이밍|시점)",
    r"목표가", r"비중\s*(확대|축소)", r"사야", r"팔아야", r"진입\s*가",
    r"손절", r"익절", r"저가\s*매수",
]

# AI 추정을 단정한 표현
OVERCLAIM_PATTERNS = [r"확실(히|하다)", r"반드시 오를", r"반드시 내릴", r"틀림없", r"보장"]


def _collect_known_numbers(stats: dict[str, MetricStats]) -> set[float]:
    """브리핑에 등장해도 되는 숫자 집합."""
    known: set[float] = set()

    def add(v):
        if v is None:
            return
        try:
            f = float(v)
        except (TypeError, ValueError):
            return
        known.add(round(f, 2))
        known.add(round(f, 1))
        known.add(round(f))

    for st in stats.values():
        if not st.available:
            continue
        add(st.value)
        add(st.percentile_5y)
        for chg in st.changes.values():
            add(chg.get("prev"))
            add(chg.get("abs"))
            add(chg.get("pct"))
        for v in (st.stats_5y or {}).values():
            add(v)
        for k in ("current", "previous", "abs_change", "pct_change"):
            add((st.yoy or {}).get(k))
    return known


def rule_check(
    briefing: str,
    stats: dict[str, MetricStats],
    today: date | None = None,
    tolerance: float = 0.02,
) -> list[dict[str, Any]]:
    """규칙 기반 검증. 발견된 문제 리스트를 반환."""
    today = today or date.today()
    issues: list[dict[str, Any]] = []
    known = _collect_known_numbers(stats)

    # 1) 미래 날짜를 과거 사실처럼 서술했는지
    for m in DATE_RE.finditer(briefing):
        raw = m.group().replace(".", "-")
        try:
            d = date.fromisoformat(raw)
        except ValueError:
            continue
        # ⑨ 향후 7일 섹션은 미래 날짜가 정상이다
        section = briefing[: m.start()].rsplit("━", 1)[-1]
        if d > today and "앞으로 7일" not in section and "실적" not in section:
            issues.append({"type": 6, "quote": m.group(), "detail": "미래 날짜가 과거 사실처럼 서술됨"})

    # 2) 데이터에 없는 숫자
    # 지표 이름 자체에 숫자가 있으므로(S&P500, Russell 2000, 10Y-2Y) 먼저 제거한다.
    scan = briefing
    for label in sorted({st.label for st in stats.values()}, key=len, reverse=True):
        scan = scan.replace(label, "·")
    for noise in ("10Y-2Y", "07:00", "5년", "1년", "20일", "5일", "3개월", "1개월", "퍼센타일"):
        scan = scan.replace(noise, "·")

    unknown: list[str] = []
    for m in NUMBER_RE.finditer(scan):
        token = m.group().replace(",", "")
        try:
            v = float(token)
        except ValueError:
            continue
        if abs(v) < 3 or 1900 < abs(v) < 2100:  # 순번, 연도, 미세 수치는 제외
            continue
        if any(abs(v - k) <= max(tolerance, abs(k) * tolerance) for k in known):
            continue
        unknown.append(m.group())
    if unknown:
        issues.append(
            {
                "type": 1,
                "quote": ", ".join(sorted(set(unknown))[:10]),
                "detail": f"데이터에서 대조되지 않는 숫자 {len(set(unknown))}건",
            }
        )

    # 3) 투자 권유 표현
    for pat in ADVICE_PATTERNS:
        m = re.search(pat, briefing)
        if m:
            issues.append({"type": 8, "quote": m.group(), "detail": "투자 권유로 읽힐 수 있는 표현"})

    # 4) 과잉 단정
    for pat in OVERCLAIM_PATTERNS:
        m = re.search(pat, briefing)
        if m:
            issues.append({"type": 7, "quote": m.group(), "detail": "추정을 단정적으로 서술"})

    # 5) 오래된 데이터가 현재값처럼 쓰였는지
    for st in stats.values():
        if not st.available or not st.data_date or st.freq != "daily":
            continue
        gap = (today - date.fromisoformat(st.data_date)).days
        if gap > 5 and f"[{st.data_date} 기준]" not in briefing:
            issues.append(
                {
                    "type": 2,
                    "quote": st.label,
                    "detail": f"{gap}일 지난 데이터인데 기준일 표기 없음",
                }
            )
    return issues


def apply_ai_fixes(briefing: str, ai_result: dict[str, Any] | None) -> tuple[str, list[dict[str, Any]]]:
    """AI가 지적한 문구를 치환한다. 원문에 없는 quote는 무시한다."""
    if not ai_result or ai_result.get("verdict") == "PASS":
        return briefing, []
    applied = []
    for issue in ai_result.get("issues", []):
        q, fix = issue.get("quote"), issue.get("fix")
        if q and fix and q in briefing:
            briefing = briefing.replace(q, fix, 1)
            applied.append(issue)
    return briefing, applied


def report(issues: list[dict[str, Any]]) -> str:
    if not issues:
        return "품질검증 통과"
    lines = [f"품질검증 지적 {len(issues)}건"]
    for i in issues[:10]:
        lines.append(f"  [type {i.get('type')}] {i.get('quote','')[:60]} — {i.get('detail','')}")
    return "\n".join(lines)
