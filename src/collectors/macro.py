"""금리·매크로·유동성 수집 (FRED API).

FRED는 무료 API 키가 필요하다: https://fredaccount.stlouisfed.org/apikeys
키가 없으면 해당 지표는 전부 '데이터 없음'으로 처리하고 파이프라인은 계속 진행한다.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

import requests

from ..config import ALL_METRICS, FRED_API_KEY, HISTORY_YEARS, Metric

log = logging.getLogger(__name__)

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
SERIES_URL = "https://fred.stlouisfed.org/series/{sid}"


def fetch_series(series_id: str, years: int = HISTORY_YEARS, api_key: str | None = None) -> list[tuple[str, float]]:
    """FRED 관측치를 [(date, value)] 로 반환. '.'(결측)은 제외한다."""
    key = api_key or FRED_API_KEY
    if not key:
        log.warning("FRED_API_KEY 없음 → %s 건너뜀", series_id)
        return []

    start = (datetime.now() - timedelta(days=int(365.25 * years) + 40)).strftime("%Y-%m-%d")
    params = {
        "series_id": series_id,
        "api_key": key,
        "file_type": "json",
        "observation_start": start,
    }
    for attempt in range(3):
        try:
            r = requests.get(FRED_URL, params=params, timeout=20)
            if r.status_code == 429:  # rate limit
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            obs = r.json().get("observations", [])
            out = []
            for o in obs:
                v = o.get("value")
                if v in (None, ".", ""):
                    continue
                try:
                    out.append((o["date"], float(v)))
                except ValueError:
                    continue
            return out
        except Exception as exc:
            log.warning("FRED %s 실패(%d회차): %s", series_id, attempt + 1, exc)
            time.sleep(1.5 * (attempt + 1))
    return []


def collect(repo, metrics: list[Metric] | None = None, years: int = HISTORY_YEARS) -> dict[str, int]:
    targets = metrics or [m for m in ALL_METRICS if m.source == "fred"]
    collected_at = datetime.now().isoformat(timespec="seconds")
    result: dict[str, int] = {}

    for m in targets:
        obs = fetch_series(m.symbol, years)
        rows: list[dict[str, Any]] = [
            {
                "key": m.key,
                "data_date": d,
                "value": v,
                "unit": m.unit,
                "freq": m.freq,
                "source": "FRED (Federal Reserve Bank of St. Louis)",
                "source_url": SERIES_URL.format(sid=m.symbol),
                "symbol": m.symbol,
                "collected_at": collected_at,
            }
            for d, v in obs
        ]
        if rows:
            repo.upsert_series("macro_data", rows)
        result[m.key] = len(rows)
        log.info("macro  %-14s %5d rows", m.key, len(rows))
    return result
