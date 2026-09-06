"""CHANGE DETECTION ENGINE (PART 9) — 이 시스템의 핵심.

직전 브리핑 시점의 수치 스냅샷과 오늘 스냅샷을 비교해
'무엇이 달라졌는가'의 후보를 뽑는다. 최종 3개 선정과
'하루 변동 vs 구조적 변화' 판단은 AI가 한다.

주말/공휴일 때문에 직전 브리핑이 어제가 아닐 수 있으므로
날짜를 계산하지 않고 DB에서 가장 최근 브리핑을 가져온다.
"""
from __future__ import annotations

import json
from typing import Any

from ..config import METRIC_BY_KEY
from .trends import MetricStats


def build_snapshot(stats: dict[str, MetricStats], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """브리핑과 함께 저장할 수치 스냅샷. 다음 날 비교 기준이 된다."""
    snap = {
        "metrics": {
            k: {
                "value": st.value,
                "data_date": st.data_date,
                "percentile_5y": st.percentile_5y,
                "unit": st.unit,
                "label": st.label,
            }
            for k, st in stats.items()
            if st.available
        }
    }
    if extra:
        snap.update(extra)
    return snap


def _load(snapshot_json: str | None) -> dict[str, Any]:
    if not snapshot_json:
        return {}
    try:
        return json.loads(snapshot_json)
    except json.JSONDecodeError:
        return {}


def compare(
    today: dict[str, MetricStats],
    previous_briefing: dict[str, Any] | None,
) -> dict[str, Any]:
    """직전 브리핑 대비 변화 후보 리스트를 반환."""
    if not previous_briefing:
        return {
            "available": False,
            "reason": "직전 브리핑 없음 (첫 실행)",
            "previous_date": None,
            "candidates": [],
        }

    prev_snap = _load(previous_briefing.get("snapshot_json")).get("metrics", {})
    if not prev_snap:
        return {
            "available": False,
            "reason": "직전 브리핑에 수치 스냅샷이 없음",
            "previous_date": previous_briefing.get("date"),
            "candidates": [],
        }

    candidates: list[dict[str, Any]] = []
    for key, st in today.items():
        if not st.available or key not in prev_snap:
            continue
        prev = prev_snap[key]
        pv, cv = prev.get("value"), st.value
        if pv is None or cv is None or pv == 0:
            continue
        # 같은 데이터 날짜면 갱신되지 않은 것 → 변화로 보지 않는다
        if prev.get("data_date") == st.data_date:
            continue

        is_rate = st.unit in ("%", "%p")
        abs_chg = cv - pv
        pct_chg = abs_chg / abs(pv) * 100.0

        pctile_shift = None
        if st.percentile_5y is not None and prev.get("percentile_5y") is not None:
            pctile_shift = st.percentile_5y - prev["percentile_5y"]

        # 중요도: 퍼센타일 이동폭 + 변화율 크기 (금리는 bp 기준 별도 가중)
        magnitude = abs(pct_chg)
        if is_rate:
            magnitude = abs(abs_chg) * 100 / 5  # 5bp를 1%p 변동과 동급으로 취급
        importance = magnitude + (abs(pctile_shift) / 5 if pctile_shift else 0)

        candidates.append(
            {
                "key": key,
                "label": st.label,
                "previous": pv,
                "current": cv,
                "abs_change": round(abs_chg * (100 if is_rate else 1), 4),
                "abs_unit": "bp" if is_rate else st.unit,
                "pct_change": None if is_rate else round(pct_chg, 3),
                "percentile_now": st.percentile_5y,
                "percentile_shift": round(pctile_shift, 1) if pctile_shift is not None else None,
                "importance": round(importance, 3),
                "summary": _summarize(st, pv, cv, is_rate, abs_chg, pct_chg),
            }
        )

    candidates.sort(key=lambda c: -c["importance"])
    return {
        "available": True,
        "previous_date": previous_briefing.get("date"),
        "candidates": candidates[:15],
        "total_compared": len(candidates),
    }


def _summarize(st: MetricStats, pv: float, cv: float, is_rate: bool, abs_chg: float, pct: float) -> str:
    if is_rate:
        return f"{st.label} {pv:.2f}% → {cv:.2f}% ({abs_chg*100:+.0f}bp)"
    fmt = ",.0f" if abs(cv) >= 100 else ".2f"
    return f"{st.label} {pv:{fmt}} → {cv:{fmt}} ({pct:+.2f}%)"


def week_context(briefings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """최근 7일 브리핑 요약 (PART 23). AI에 넘길 압축 형태."""
    out = []
    for b in briefings:
        out.append(
            {
                "date": b.get("date"),
                "regime": b.get("market_regime"),
                "score": b.get("market_score"),
                # 본문 전체가 아니라 한줄평 부분만 추출해 토큰을 아낀다
                "one_line": _extract_one_line(b.get("briefing_text", "")),
            }
        )
    return out


def _extract_one_line(text: str) -> str:
    marker = "ONE-LINE VIEW"
    if marker not in text:
        return ""
    tail = text.split(marker, 1)[1]
    for line in tail.splitlines():
        clean = line.strip().strip('"').strip("━").strip()
        if clean:
            return clean[:200]
    return ""
