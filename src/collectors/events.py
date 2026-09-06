"""향후 7일 이벤트 캘린더 (PART 14).

3개 소스를 합친다.
1) FRED releases/dates  — CPI, 고용, PCE, GDP 등 공식 지표 발표일 (자동)
2) yfinance earnings_dates — NVDA/MU/AVGO 등 실적 발표일 (자동, 잠정치 포함)
3) data/event_calendar.json — FOMC 등 수동 등록 (FRED에 없음)

무료로 신뢰할 수 있는 통합 경제캘린더 API가 없어 FOMC는 수동 관리한다.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from ..config import ET, FRED_API_KEY, KST, ROOT, SEMI_EARNINGS_TICKERS

log = logging.getLogger(__name__)

RELEASE_DATES_URL = "https://api.stlouisfed.org/fred/releases/dates"

# 브리핑에 넣을 가치가 있는 발표만 남긴다. (키워드, 중요도)
RELEASE_FILTERS = [
    ("consumer price index", "HIGH"),
    ("employment situation", "HIGH"),
    ("personal income and outlays", "HIGH"),  # PCE 물가 포함
    ("gross domestic product", "HIGH"),
    ("producer price index", "MEDIUM"),
    ("advance monthly sales for retail", "MEDIUM"),
    ("unemployment insurance weekly claims", "MEDIUM"),
    ("industrial production", "LOW"),
    ("h.4.1", "LOW"),  # Fed 대차대조표
]

MANUAL_FILE = ROOT / "data" / "event_calendar.json"


def _from_fred(days: int) -> list[dict[str, Any]]:
    if not FRED_API_KEY:
        log.warning("FRED_API_KEY 없음 → 지표 발표일정 건너뜀")
        return []
    today = date.today()
    params = {
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "realtime_start": today.isoformat(),
        "realtime_end": (today + timedelta(days=days)).isoformat(),
        "include_release_dates_with_no_data": "true",
        "limit": 1000,
    }
    try:
        r = requests.get(RELEASE_DATES_URL, params=params, timeout=20)
        r.raise_for_status()
        payload = r.json().get("release_dates", [])
    except Exception as exc:
        log.warning("FRED 발표일정 조회 실패: %s", exc)
        return []

    out = []
    for item in payload:
        name = (item.get("release_name") or "").lower()
        for kw, importance in RELEASE_FILTERS:
            if kw in name:
                out.append(
                    {
                        "event_date": item["date"],
                        "title": item.get("release_name", "").strip(),
                        "importance": importance,
                        "source": "FRED release calendar",
                        "kst_time": None,  # 발표 시각은 FRED가 제공하지 않음
                    }
                )
                break
    return out


def _from_earnings(days: int) -> list[dict[str, Any]]:
    try:
        import yfinance as yf
    except ImportError:
        return []

    horizon = date.today() + timedelta(days=days)
    out = []
    for ticker in SEMI_EARNINGS_TICKERS:
        try:
            df = yf.Ticker(ticker).earnings_dates
        except Exception as exc:
            log.warning("%s 실적일정 조회 실패: %s", ticker, exc)
            continue
        if df is None or df.empty:
            continue
        for idx in df.index:
            try:
                d = idx.date()
            except AttributeError:
                continue
            if not (date.today() <= d <= horizon):
                continue
            # 미국 실적발표는 장 마감 후가 일반적 → 한국시간 다음날 아침
            kst_time = None
            try:
                kst_time = idx.tz_convert(ET).astimezone(KST).strftime("%m/%d %H:%M KST")
            except Exception:
                pass
            out.append(
                {
                    "event_date": d.isoformat(),
                    "title": f"{ticker} 실적 발표",
                    "importance": "HIGH" if ticker in ("NVDA", "MU") else "MEDIUM",
                    "source": "Yahoo Finance (잠정 일정)",
                    "kst_time": kst_time,
                }
            )
    return out


def _from_manual(days: int) -> list[dict[str, Any]]:
    if not MANUAL_FILE.exists():
        return []
    try:
        data = json.loads(MANUAL_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("수동 이벤트 파일 파싱 실패: %s", exc)
        return []
    horizon = date.today() + timedelta(days=days)
    out = []
    for item in data:
        try:
            d = date.fromisoformat(item["event_date"])
        except (KeyError, ValueError):
            continue
        if date.today() <= d <= horizon:
            out.append(
                {
                    "event_date": item["event_date"],
                    "title": item["title"],
                    "importance": item.get("importance", "MEDIUM"),
                    "source": item.get("source", "수동 등록"),
                    "kst_time": item.get("kst_time"),
                }
            )
    return out


def collect(repo, days: int = 7) -> list[dict[str, Any]]:
    events = _from_fred(days) + _from_earnings(days) + _from_manual(days)
    # 중복 제거
    dedup = {(e["event_date"], e["title"]): e for e in events}
    rows = sorted(dedup.values(), key=lambda x: x["event_date"])
    if rows:
        repo.save_events(rows)
    log.info("events %d items", len(rows))
    return rows
