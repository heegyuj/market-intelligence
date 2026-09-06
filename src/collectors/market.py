"""시장 데이터 수집 (yfinance).

원칙
 - 값을 못 구하면 None. 절대 추정하거나 다른 지표로 대체하지 않는다.
 - 폴백 티커를 쓴 경우 symbol 필드에 실제 사용 티커를 남겨 추적 가능하게 한다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from ..config import ALL_METRICS, HISTORY_YEARS, Metric

log = logging.getLogger(__name__)

SOURCE_URL = "https://finance.yahoo.com/quote/{symbol}"


def _download(symbol: str, start: str) -> Any:
    import yfinance as yf

    df = yf.download(
        symbol,
        start=start,
        progress=False,
        auto_adjust=False,
        threads=False,
    )
    if df is None or df.empty:
        return None
    # yfinance 최신 버전은 MultiIndex 컬럼을 반환한다.
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)
    return df


def fetch_metric(metric: Metric, years: int = HISTORY_YEARS) -> list[dict[str, Any]]:
    """지표 하나의 히스토리를 [{key, data_date, value, ...}] 로 반환. 실패 시 빈 리스트."""
    start = (datetime.now() - timedelta(days=int(365.25 * years) + 10)).strftime("%Y-%m-%d")
    candidates = [metric.symbol, *metric.fallbacks]
    collected_at = datetime.now().isoformat(timespec="seconds")

    for symbol in candidates:
        try:
            df = _download(symbol, start)
        except Exception as exc:  # 네트워크/파싱 오류
            log.warning("%s(%s) 다운로드 실패: %s", metric.key, symbol, exc)
            continue
        if df is None or "Close" not in df:
            log.warning("%s(%s) 데이터 없음", metric.key, symbol)
            continue

        if symbol != metric.symbol:
            log.warning("%s: 기본 티커 실패 → 폴백 %s 사용", metric.key, symbol)

        rows = []
        for idx, val in df["Close"].dropna().items():
            rows.append(
                {
                    "key": metric.key,
                    "data_date": idx.date().isoformat(),
                    "value": float(val),
                    "unit": metric.unit,
                    "source": "Yahoo Finance",
                    "source_url": SOURCE_URL.format(symbol=symbol),
                    "symbol": symbol,
                    "collected_at": collected_at,
                }
            )
        if rows:
            return rows

    log.error("%s: 모든 티커 실패 → 데이터 없음", metric.key)
    return []


def collect(repo, metrics: list[Metric] | None = None, years: int = HISTORY_YEARS) -> dict[str, int]:
    """yfinance 소스 지표를 모두 수집해 market_data 테이블에 적재."""
    targets = metrics or [m for m in ALL_METRICS if m.source == "yfinance"]
    result: dict[str, int] = {}
    for m in targets:
        rows = fetch_metric(m, years)
        if rows:
            repo.upsert_series("market_data", rows)
        result[m.key] = len(rows)
        log.info("market %-14s %5d rows", m.key, len(rows))
    return result
