"""한국 수급 데이터 (pykrx).

삼성전자·SK하이닉스의 외국인/기관 순매수를 수집한다.
pykrx는 KRX 사이트를 스크래핑하므로 구조 변경 시 깨질 수 있다.
실패해도 전체 파이프라인은 계속 진행하고 '데이터 없음'으로 표시한다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from ..config import KOREA_FLOW

log = logging.getLogger(__name__)

SOURCE_URL = "http://data.krx.co.kr"


def fetch_flows(days: int = 400) -> list[dict[str, Any]]:
    """외국인/기관 순매수 금액(원)을 market_data 행 형태로 반환."""
    try:
        from pykrx import stock
    except ImportError:
        log.warning("pykrx 미설치 → 수급 데이터 건너뜀")
        return []

    end = datetime.now()
    start = end - timedelta(days=days)
    fmt = "%Y%m%d"
    collected_at = end.isoformat(timespec="seconds")
    rows: list[dict[str, Any]] = []

    for name, code in KOREA_FLOW.items():
        try:
            df = stock.get_market_trading_value_by_date(
                start.strftime(fmt), end.strftime(fmt), code
            )
        except Exception as exc:
            log.warning("pykrx %s 수급 조회 실패: %s", name, exc)
            continue
        if df is None or df.empty:
            continue

        col_map = {"외국인합계": "foreign", "기관합계": "institution"}
        for kr_col, suffix in col_map.items():
            if kr_col not in df.columns:
                continue
            for idx, val in df[kr_col].dropna().items():
                rows.append(
                    {
                        "key": f"{name}_{suffix}_net",
                        "data_date": idx.date().isoformat()
                        if hasattr(idx, "date")
                        else str(idx)[:10],
                        "value": float(val),
                        "unit": "KRW",
                        "source": "KRX (pykrx)",
                        "source_url": SOURCE_URL,
                        "symbol": code,
                        "collected_at": collected_at,
                    }
                )
    return rows


def collect(repo, days: int = 400) -> int:
    rows = fetch_flows(days)
    if rows:
        repo.upsert_series("market_data", rows)
    log.info("korea flow %d rows", len(rows))
    return len(rows)
