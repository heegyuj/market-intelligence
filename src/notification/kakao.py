"""카카오톡 '나에게 보내기' 발송 (PART 20).

중요한 제약
  기본 텍스트 템플릿은 **최대 200자**까지만 표시된다.
  (https://developers.kakao.com/docs/latest/ko/message-template/default)
  따라서 3~4분 분량 브리핑을 한 건으로 보낼 수 없다. 두 가지 모드를 지원한다.

  - split : 200자 단위로 나눠 여러 건 발송 (기본값, 추가 설정 불필요)
  - link  : 요약 1건 + 전체 브리핑 URL 버튼 (BRIEFING_PUBLIC_URL 필요,
            해당 도메인을 카카오 앱 [제품 링크 관리] > [웹 도메인]에 등록해야 함)

토큰
  access_token 은 만료가 짧으므로 발송 직전 refresh_token 으로 항상 재발급한다.
  refresh_token 이 갱신되어 돌아오면 로그로 알린다(GitHub Secrets 수동 갱신 필요).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

from ..config import (
    BRIEFING_PUBLIC_URL,
    KAKAO_ACCESS_TOKEN,
    KAKAO_CLIENT_SECRET,
    KAKAO_REFRESH_TOKEN,
    KAKAO_REST_API_KEY,
    KAKAO_SEND_MODE,
    ROOT,
)

log = logging.getLogger(__name__)

TOKEN_URL = "https://kauth.kakao.com/oauth/token"
SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
MAX_TEXT = 190  # 200자 제한. 페이지 표시(1/8) 여유분 확보
TOKEN_FILE = ROOT / "token_store.json"


class KakaoError(RuntimeError):
    pass


class KakaoSender:
    def __init__(
        self,
        rest_api_key: str | None = None,
        client_secret: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        mode: str | None = None,
    ):
        self.rest_api_key = rest_api_key or KAKAO_REST_API_KEY
        self.client_secret = client_secret or KAKAO_CLIENT_SECRET
        self.access_token = access_token or KAKAO_ACCESS_TOKEN
        self.refresh_token = refresh_token or KAKAO_REFRESH_TOKEN
        self.mode = mode or KAKAO_SEND_MODE or "split"

    @property
    def configured(self) -> bool:
        return bool(self.rest_api_key and (self.refresh_token or self.access_token))

    # ------------------------------------------------------------------ 토큰
    def refresh(self) -> str | None:
        """refresh_token으로 access_token 재발급."""
        if not (self.rest_api_key and self.refresh_token):
            log.warning("refresh_token 없음 → 기존 access_token 사용")
            return self.access_token

        data = {
            "grant_type": "refresh_token",
            "client_id": self.rest_api_key,
            "refresh_token": self.refresh_token,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret

        try:
            r = requests.post(TOKEN_URL, data=data, timeout=15)
            r.raise_for_status()
            payload = r.json()
        except Exception as exc:
            log.error("카카오 토큰 갱신 실패: %s", exc)
            return self.access_token

        self.access_token = payload.get("access_token", self.access_token)
        if payload.get("refresh_token"):
            self.refresh_token = payload["refresh_token"]
            log.warning(
                "⚠️ 새 refresh_token이 발급됐습니다. GitHub Secrets의 "
                "KAKAO_REFRESH_TOKEN을 갱신하세요. (token_store.json에 저장함)"
            )
            self._persist(payload)
        return self.access_token

    def _persist(self, payload: dict[str, Any]) -> None:
        try:
            TOKEN_FILE.write_text(
                json.dumps(
                    {
                        "access_token": payload.get("access_token"),
                        "refresh_token": payload.get("refresh_token"),
                        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    },
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
        except Exception as exc:  # 저장 실패해도 발송은 계속
            log.warning("토큰 파일 저장 실패: %s", exc)

    # ------------------------------------------------------------------ 발송
    def _post(self, template: dict[str, Any]) -> dict[str, Any]:
        if not self.access_token:
            raise KakaoError("access_token 없음")
        r = requests.post(
            SEND_URL,
            headers={"Authorization": f"Bearer {self.access_token}"},
            data={"template_object": json.dumps(template, ensure_ascii=False)},
            timeout=15,
        )
        if r.status_code != 200:
            raise KakaoError(f"HTTP {r.status_code}: {r.text[:300]}")
        return r.json()

    def send_text(self, text: str, link_url: str | None = None, button_title: str | None = None) -> dict[str, Any]:
        url = link_url or BRIEFING_PUBLIC_URL or "https://developers.kakao.com"
        template: dict[str, Any] = {
            "object_type": "text",
            "text": text[:MAX_TEXT],
            "link": {"web_url": url, "mobile_web_url": url},
        }
        if button_title:
            template["button_title"] = button_title[:14]
        return self._post(template)

    @staticmethod
    def chunk(text: str, size: int = MAX_TEXT) -> list[str]:
        """섹션 구분선(━)을 우선 경계로 삼아 자른다."""
        blocks = [b.strip() for b in text.split("━" * 18) if b.strip()]
        chunks: list[str] = []
        buf = ""
        for b in blocks:
            if len(buf) + len(b) + 1 <= size - 8:
                buf = f"{buf}\n{b}".strip()
                continue
            if buf:
                chunks.append(buf)
                buf = ""
            # 블록 자체가 길면 줄 단위로 쪼갠다
            cur = ""
            for line in b.splitlines():
                while len(line) > size - 8:
                    if cur:
                        chunks.append(cur)
                        cur = ""
                    chunks.append(line[: size - 8])
                    line = line[size - 8 :]
                if len(cur) + len(line) + 1 > size - 8:
                    chunks.append(cur)
                    cur = line
                else:
                    cur = f"{cur}\n{line}".strip()
            if cur:
                buf = cur
        if buf:
            chunks.append(buf)
        return chunks

    def send_briefing(self, briefing: str, summary: str, dry_run: bool = False) -> dict[str, Any]:
        """모드에 따라 발송. 결과 요약 dict 반환(예외를 밖으로 던지지 않는다)."""
        if not self.configured:
            return {"ok": False, "sent": 0, "error": "카카오 인증정보 미설정"}

        if dry_run:
            chunks = self.chunk(briefing)
            return {"ok": True, "sent": 0, "dry_run": True, "would_send": len(chunks),
                    "mode": self.mode, "summary_len": len(summary)}

        self.refresh()

        messages: list[tuple[str, str | None]] = []
        if self.mode in ("link", "both"):
            if not BRIEFING_PUBLIC_URL:
                log.warning("BRIEFING_PUBLIC_URL 미설정 → split 모드로 전환")
                self.mode = "split"
            else:
                messages.append((summary, "전체 브리핑 보기"))
        if self.mode in ("split", "both"):
            chunks = self.chunk(briefing)
            total = len(chunks)
            for i, c in enumerate(chunks, 1):
                messages.append((f"[{i}/{total}]\n{c}", None))

        sent, errors = 0, []
        for text, button in messages:
            try:
                self.send_text(text, button_title=button)
                sent += 1
                time.sleep(0.6)  # 시간당 호출 제한(1000회) 여유
            except KakaoError as exc:
                log.error("카카오 발송 실패: %s", exc)
                errors.append(str(exc))
                break  # 토큰 문제면 나머지도 실패한다

        return {"ok": sent > 0 and not errors, "sent": sent,
                "total": len(messages), "errors": errors, "mode": self.mode}
