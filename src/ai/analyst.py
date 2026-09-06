"""AI 애널리스트 (Anthropic Claude API).

API 키가 없거나 호출이 실패하면 예외를 던지지 않고 None을 반환한다.
그 경우 브리핑은 '정량 데이터만' 담긴 축약본으로 발송된다.
숫자가 없는 것보다 해석이 없는 편이 낫다.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from ..config import AI_MODEL, AI_MODEL_LIGHT, ANTHROPIC_API_KEY
from . import prompts

log = logging.getLogger(__name__)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class Analyst:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or ANTHROPIC_API_KEY
        self.model = model or AI_MODEL
        self.light_model = AI_MODEL_LIGHT
        self._client = None
        self.enabled = bool(self.api_key)
        if not self.enabled:
            log.warning("ANTHROPIC_API_KEY 없음 → AI 분석 비활성화 (정량 데이터만 발송)")

    @property
    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    # ------------------------------------------------------------------ 내부
    def _call(self, prompt: str, model: str, max_tokens: int = 4000, retries: int = 3) -> str | None:
        if not self.enabled:
            return None
        for attempt in range(retries):
            try:
                resp = self.client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=prompts.SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
                )
                return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            except Exception as exc:
                log.warning("AI 호출 실패 (%d/%d): %s", attempt + 1, retries, exc)
                time.sleep(2 * (attempt + 1))
        return None

    def _call_json(self, prompt: str, model: str, max_tokens: int = 4000) -> dict[str, Any] | None:
        raw = self._call(prompt, model, max_tokens)
        if raw is None:
            return None
        text = _FENCE.sub("", raw.strip())
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 앞뒤 설명이 붙은 경우 첫 JSON 객체만 추출
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass
            log.error("AI 응답 JSON 파싱 실패: %s", text[:300])
            return None

    # ------------------------------------------------------------------ 공개
    def triage_news(self, headlines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """헤드라인에 impact/direction/why를 붙여 상위 항목만 반환."""
        if not headlines or not self.enabled:
            return []
        result = self._call_json(prompts.news_triage(headlines), self.light_model, 2000)
        if not result:
            return []
        out = []
        for sel in result.get("selected", [])[:8]:
            i = sel.get("i")
            if not isinstance(i, int) or not (0 <= i < len(headlines)):
                continue
            item = dict(headlines[i])
            item.update(
                {
                    "impact": sel.get("impact", "LOW"),
                    "direction": sel.get("direction", "NEUTRAL"),
                    "why": sel.get("why", ""),
                }
            )
            out.append(item)
        return out

    def analyze(self, context: dict[str, Any]) -> dict[str, Any] | None:
        """메인 분석. 실패 시 None."""
        return self._call_json(prompts.main_analysis(context), self.model, 6000)

    def verify(self, briefing: str, context: dict[str, Any]) -> dict[str, Any] | None:
        """PART 18 품질검증의 AI 파트."""
        return self._call_json(prompts.quality_check(briefing, context), self.model, 3000)
