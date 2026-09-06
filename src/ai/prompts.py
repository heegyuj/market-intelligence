"""AI 프롬프트 (PART 2 원칙을 프롬프트로 강제한다)."""
from __future__ import annotations

import json
from typing import Any

SYSTEM = """당신은 한국 개인투자자를 위한 시장 분석가다.

절대 규칙
1. 제공된 데이터에 없는 숫자를 절대 만들지 않는다. 데이터가 없으면 "데이터 없음"이라고 쓴다.
2. 관측된 데이터와 당신의 해석을 명확히 구분한다.
   - 관측: 제공된 JSON에 있는 값
   - 해석: 그 값으로부터 당신이 추론한 내용 → "~로 보인다", "~가능성이 있다"로 표현
3. 특정 종목의 매수/매도/목표가를 제시하지 않는다. 시장 구조와 변수만 설명한다.
4. 미래 가격을 예측하지 않는다. "무엇을 확인해야 하는가"를 제시한다.

분석 방식 (가장 중요)
숫자를 나열하지 말고, 반드시 이 순서로 서술한다.
  현재값 → 변화 → 원인(추정) → 역사적 위치 → 향후 의미

나쁜 예: "S&P500 +0.4%, 10Y 4.75%, VIX 15"
좋은 예: "S&P500이 올랐지만 10년물 금리도 함께 올랐다. 금리 하락에 따른
밸류에이션 확장이 아니라 실적 기대나 특정 섹터 주도일 가능성이 있다."

문체
- 한국어. 간결한 평서체. 과장 없음.
- 이모지는 지정된 위치에만.
- 출력은 반드시 요청된 JSON 형식만. 코드펜스나 설명 문장을 붙이지 않는다."""


def news_triage(headlines: list[dict[str, Any]]) -> str:
    items = [
        {"i": i, "headline": h["headline"], "source": h["source"], "published_at": h.get("published_at")}
        for i, h in enumerate(headlines)
    ]
    return f"""아래는 최근 24시간 금융 헤드라인이다. 본문은 제공되지 않는다.
헤드라인만으로 판단할 수 없으면 impact를 LOW로 둔다.

{json.dumps(items, ensure_ascii=False, indent=1)}

시장 영향이 큰 순으로 최대 8건만 선정하라.
각 항목에 대해 판단하라.
- impact: HIGH | MEDIUM | LOW
- direction: POSITIVE | NEGATIVE | NEUTRAL  (미국 위험자산 기준)
- why: 왜 중요한지 한 문장 (한국어, 40자 이내)

JSON만 출력:
{{"selected":[{{"i":0,"impact":"HIGH","direction":"NEGATIVE","why":"..."}}]}}"""


def main_analysis(context: dict[str, Any]) -> str:
    return f"""다음은 오늘 수집된 시장 데이터다. 여기 없는 숫자는 존재하지 않는 것으로 간주하라.

<데이터>
{json.dumps(context, ensure_ascii=False, indent=1, default=str)}
</데이터>

다음 JSON 형식으로만 답하라. 각 필드는 한국어.

{{
 "us_market": "미국 지수 움직임 해석. 3~4문장. 지수 간 차이(예: 소형주 vs 대형주, SOX vs S&P500)에서 무엇을 읽을 수 있는지 포함.",
 "rates_fx_oil": "금리·달러·유가 해석. 3~4문장. 이들이 서로 같은 방향인지 엇갈리는지, 그것이 무엇을 뜻하는지.",
 "five_year_position": "핵심 지표들이 5년 분포에서 어느 위치인지, 그 위치가 뜻하는 바. 2~3문장.",
 "yoy_structural": "1년 전과 비교해 시장의 가장 중요한 '구조적' 변화 하나. 2~3문장. 단순 등락이 아니라 구조를 말할 것.",
 "changes": {{
   "items": ["직전 브리핑 이후 달라진 것 3개. 각각 한 줄. 반드시 데이터의 change_detection.candidates에서 고를 것"],
   "structural": "이 중 단순 하루 변동과 구조적 변화를 구분해 설명. 2~3문장."
 }},
 "semiconductor": "반도체 사이클 해석. AI CAPEX→GPU→HBM→DRAM 연결고리 관점. 3~4문장.",
 "samsung_qualitative": [{{"factor":"HBM","score":0,"reason":"뉴스 기반 판단 근거"}}],
 "hynix_qualitative": [{{"factor":"HBM","score":0,"reason":"뉴스 기반 판단 근거"}}],
 "regime_review": {{
   "agree": true,
   "regime": "정량엔진 결과를 뉴스까지 보고 재평가한 최종 국면",
   "reason": "정량 점수와 다르게 봤다면 그 이유. 같다면 왜 타당한지. 2~3문장."
 }},
 "korea_link": "미국 시장 결과를 한국 증시 관점으로 해석. NASDAQ→SOX→NVIDIA→환율→삼성전자/SK하이닉스 연결. 3~4문장. 오늘 한국 증시에 가장 중요한 미국 변수 1개를 명시.",
 "today_variables": [
   {{"title":"오늘 확인할 변수", "why":"왜 이 숫자가 중요한지 1~2문장"}}
 ],
 "one_line": "오늘 시장을 한 문장으로. 60자 이내."
}}

주의
- samsung_qualitative / hynix_qualitative 는 데이터에 score가 null로 표시된 항목만 채운다.
  뉴스에도 근거가 없으면 score를 0으로 두고 reason에 "근거 없음"이라고 쓴다.
- today_variables 는 정확히 3개.
- changes.items 는 정확히 3개."""


def quality_check(briefing: str, context: dict[str, Any]) -> str:
    return f"""아래 브리핑 초안을 데이터와 대조해 검증하라.

<데이터>
{json.dumps(context, ensure_ascii=False, indent=1, default=str)}
</데이터>

<브리핑>
{briefing}
</브리핑>

다음 오류만 찾는다.
1. 데이터에 없는 숫자
2. 데이터 날짜와 본문 서술의 불일치
3. 전년/전일 대비 계산이 데이터와 다름
4. 현재보다 미래 시점 데이터를 이미 발생한 것처럼 서술
5. 출처 없는 숫자
6. 서로 모순되는 설명
7. AI 추정을 관측 데이터처럼 단정한 표현
8. 특정 종목 매수/매도 권유로 읽힐 수 있는 표현

JSON만 출력:
{{"issues":[{{"type":1,"quote":"문제 문구 그대로","fix":"수정된 문구"}}],"verdict":"PASS 또는 FIX"}}

문제가 없으면 issues는 빈 배열, verdict는 PASS."""
