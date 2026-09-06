"""MORNING BRIEFING GENERATOR (PART 17).

정량 데이터로 뼈대를 만들고, AI 해석을 각 섹션에 끼워 넣는다.
AI가 없어도 브리핑은 생성된다(해석 문장만 빠짐).
"""
from __future__ import annotations

from datetime import date
from typing import Any

from ..analysis.regime import emoji
from ..analysis.trends import MetricStats

LINE = "━" * 18
NO_DATA = "데이터 없음"


# ------------------------------------------------------------------ 포맷터
def fmt_value(st: MetricStats | None) -> str:
    if not st or not st.available or st.value is None:
        return NO_DATA
    v = st.value
    if st.unit in ("%", "%p"):
        return f"{v:.2f}%"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:,.2f}"


def fmt_line(st: MetricStats | None, period: str = "1D") -> str:
    """'S&P500 6,123 (+0.42%)' 형태."""
    if not st or not st.available:
        return f"· {NO_DATA}"
    chg = st.changes.get(period) or {}
    base = f"· {st.label} {fmt_value(st)}"
    if chg.get("prev") is None:
        return base
    if st.unit in ("%", "%p"):
        return f"{base} ({chg['abs']:+.0f}bp)"
    pct = chg.get("pct")
    return f"{base} ({pct:+.2f}%)" if pct is not None else base


def _stale_mark(st: MetricStats | None, today: date) -> str:
    """데이터 기준일이 오늘/어제가 아니면 날짜를 명시한다(오래된 값을 현재값처럼 쓰지 않는다)."""
    if not st or not st.available or not st.data_date:
        return ""
    d = date.fromisoformat(st.data_date)
    return "" if (today - d).days <= 3 else f" [{st.data_date} 기준]"


# ------------------------------------------------------------------ 본문
def build(
    stats: dict[str, MetricStats],
    regime: dict[str, Any],
    signals: list[str],
    semi: dict[str, Any],
    samsung: dict[str, Any],
    hynix: dict[str, Any],
    changes: dict[str, Any],
    events: list[dict[str, Any]],
    ai: dict[str, Any] | None,
    news: list[dict[str, Any]] | None = None,
    today: date | None = None,
    data_gaps: list[str] | None = None,
) -> str:
    today = today or date.today()
    ai = ai or {}
    P: list[str] = []
    add = P.append

    add(LINE)
    add("📈 07:00 MARKET INTELLIGENCE")
    add(today.strftime("%Y.%m.%d"))
    add(LINE)

    # ① 미국시장
    add("\n① 🇺🇸 미국시장\n")
    for k in ("sp500", "nasdaq", "sox", "vix"):
        add(fmt_line(stats.get(k)) + _stale_mark(stats.get(k), today))
    add("\n오늘의 움직임:")
    add(ai.get("us_market") or "· AI 해석 없음 (수치만 참고)")

    # ② 금리·달러·유가
    add(f"\n{LINE}\n\n② 💵 금리·달러·유가\n")
    for k in ("ust10y", "ust2y", "dxy", "wti", "gold"):
        add(fmt_line(stats.get(k)) + _stale_mark(stats.get(k), today))
    sp = stats.get("spread_10y2y")
    if sp and sp.available:
        add(f"· 10Y-2Y {sp.value:+.2f}%p")
    add("\n핵심 변화:")
    add(ai.get("rates_fx_oil") or "· AI 해석 없음")

    # ③ 5년 추세
    add(f"\n{LINE}\n\n③ 📊 5년 추세\n")
    add("현재 위치:")
    for k in ("sp500", "ust10y", "vix", "sox"):
        st = stats.get(k)
        if st and st.available and st.percentile_5y is not None:
            add(f"· {st.label} 5년 {st.percentile_5y:.0f}퍼센타일")
    add("\n5년 평균 대비:")
    for k in ("ust10y", "vix"):
        st = stats.get(k)
        if st and st.available and st.stats_5y:
            avg = st.stats_5y["average"]
            unit = "%" if st.unit in ("%", "%p") else ""
            add(f"· {st.label} {st.value:.2f}{unit} (5년 평균 {avg:.2f}{unit})")
    if ai.get("five_year_position"):
        add(f"\n{ai['five_year_position']}")

    # ④ 전년 동월
    add(f"\n{LINE}\n\n④ 📅 전년 동월\n")
    add("현재 vs 1년 전:")
    shown = 0
    for k in ("sp500", "sox", "ust10y", "usdkrw", "core_cpi"):
        st = stats.get(k)
        if not st or not st.available or not st.yoy.get("available"):
            continue
        y = st.yoy
        if st.unit in ("%", "%p"):
            add(f"· {st.label} {y['current']:.2f}% ← {y['previous']:.2f}% ({y['abs_change']:+.0f}bp)")
        else:
            add(f"· {st.label} {y['pct_change']:+.1f}% (1년 전 대비)")
        shown += 1
    if shown == 0:
        add(f"· {NO_DATA}")
    add("\n가장 크게 달라진 점:")
    add(ai.get("yoy_structural") or "· AI 해석 없음")

    # ⑤ 어제와 달라진 점
    add(f"\n{LINE}\n\n⑤ 🔄 직전 브리핑과 달라진 점\n")
    if changes.get("available"):
        prev_d = changes.get("previous_date")
        items = (ai.get("changes") or {}).get("items") or [
            c["summary"] for c in changes["candidates"][:3]
        ]
        for n, item in zip("1️⃣2️⃣3️⃣", items[:3]):
            add(f"{n} {item}")
        add(f"\n(기준: {prev_d} 브리핑)")
        add("\n구조적 변화:")
        add((ai.get("changes") or {}).get("structural") or "· AI 해석 없음")
    else:
        add(f"· {changes.get('reason', NO_DATA)}")

    # ⑥ 반도체
    add(f"\n{LINE}\n\n⑥ 🔥 반도체\n")
    for k in ("sox", "nvda", "mu"):
        add(fmt_line(stats.get(k)))
    cyc = semi.get("score")
    add(f"· 반도체 사이클 점수 {cyc:+d} ({semi.get('coverage')})" if cyc is not None else f"· 사이클 점수 {NO_DATA}")
    add(f"\n삼성전자 {samsung['emoji']} {samsung['label']}")
    add(f"SK하이닉스 {hynix['emoji']} {hynix['label']}")
    add("\n핵심:")
    add(ai.get("semiconductor") or "· AI 해석 없음")

    # ⑦ 매크로 국면
    add(f"\n{LINE}\n\n⑦ 🌎 매크로 국면\n")
    for axis, sc in regime["scores"].items():
        label = f"{axis:<14}"
        add(f"{label}{emoji(sc)} {sc:+d}" if sc is not None else f"{label}⚪ {NO_DATA}")
    add(f"\n현재 국면: {(ai.get('regime_review') or {}).get('regime') or regime['regime']}")
    review = (ai.get("regime_review") or {}).get("reason")
    add(review or regime["rationale"])

    # ⑧ 알림
    add(f"\n{LINE}\n\n⑧ 🚨 MARKET ALERT\n")
    if signals:
        P.extend(signals)
    else:
        add("특별한 이상신호 없음")

    # ⑨ 향후 7일
    add(f"\n{LINE}\n\n⑨ 📅 앞으로 7일\n")
    if events:
        mark = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}
        for e in events[:8]:
            when = e.get("kst_time") or e["event_date"]
            add(f"{mark.get(e.get('importance'), '🟢')} {when} {e['title']}")
    else:
        add(f"· 등록된 일정 {NO_DATA}")

    # ⑩ 오늘의 숫자
    add(f"\n{LINE}\n\n⑩ 🎯 오늘 반드시 볼 숫자\n")
    tv = ai.get("today_variables") or []
    if tv:
        for n, item in zip("1️⃣2️⃣3️⃣", tv[:3]):
            add(f"{n} {item.get('title','')}")
            if item.get("why"):
                add(f"   → {item['why']}")
    else:
        add("· AI 해석 없음")

    # 한국시장 연결 (PART 16)
    if ai.get("korea_link"):
        add(f"\n{LINE}\n\n🇰🇷 한국시장 관점\n")
        add(ai["korea_link"])

    # 뉴스
    if news:
        add(f"\n{LINE}\n\n📰 주요 뉴스\n")
        d_mark = {"POSITIVE": "▲", "NEGATIVE": "▼", "NEUTRAL": "－"}
        for n in news[:5]:
            add(f"[{n.get('impact','LOW')}] {d_mark.get(n.get('direction'), '－')} {n['headline'][:70]}")
            if n.get("why"):
                add(f"   → {n['why']}")

    add(f"\n{LINE}\n\n🧠 ONE-LINE VIEW\n")
    add(f'"{ai.get("one_line") or "정량 데이터만 수집됨 (AI 해석 없음)"}"')

    if data_gaps:
        add(f"\n{LINE}\n\n⚠️ 수집 실패 항목\n")
        for g in data_gaps[:6]:
            add(f"· {g}")

    add(f"\n{LINE}")
    add("※ 본 자료는 시장 환경 이해를 위한 정보이며")
    add("특정 종목의 매수·매도를 권유하지 않습니다.")
    add(LINE)

    return "\n".join(P)


def summarize_for_kakao(briefing: str, ai: dict[str, Any] | None, stats: dict[str, MetricStats]) -> str:
    """카카오톡 텍스트 템플릿 200자 제한용 요약."""
    ai = ai or {}
    head = []
    for k in ("sp500", "sox", "ust10y", "usdkrw"):
        st = stats.get(k)
        if st and st.available:
            chg = st.changes.get("1D") or {}
            if st.unit in ("%", "%p"):
                head.append(f"{st.label} {st.value:.2f}%")
            elif chg.get("pct") is not None:
                head.append(f"{st.label} {chg['pct']:+.1f}%")
    one = ai.get("one_line") or ""
    text = f"📈 {date.today():%m/%d} 마켓\n" + " / ".join(head)
    if one:
        text += f"\n\n{one}"
    return text[:190]
