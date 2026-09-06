"""전역 설정.

- 티커/시리즈 ID는 전부 여기에 모은다.
- 값을 못 구하면 추정하지 않고 None을 반환한다(PART 3 원칙).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

try:  # python-dotenv는 로컬 실행 편의용. GitHub Actions에서는 Secrets가 직접 주입된다.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent
KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")  # 미국 서머타임 자동 반영


# ---------------------------------------------------------------- 환경변수
def _env(key: str, default: str | None = None) -> str | None:
    v = os.environ.get(key, default)
    return v.strip() if isinstance(v, str) else v


ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
AI_MODEL = _env("AI_MODEL", "claude-opus-5")
AI_MODEL_LIGHT = _env("AI_MODEL_LIGHT", "claude-haiku-4-5-20251001")
FRED_API_KEY = _env("FRED_API_KEY")

KAKAO_REST_API_KEY = _env("KAKAO_REST_API_KEY")
KAKAO_CLIENT_SECRET = _env("KAKAO_CLIENT_SECRET")
KAKAO_ACCESS_TOKEN = _env("KAKAO_ACCESS_TOKEN")
KAKAO_REFRESH_TOKEN = _env("KAKAO_REFRESH_TOKEN")
KAKAO_SEND_MODE = _env("KAKAO_SEND_MODE", "split")
BRIEFING_PUBLIC_URL = _env("BRIEFING_PUBLIC_URL")

DB_PATH = ROOT / _env("DB_PATH", "data/market.db")
REPORT_DIR = ROOT / _env("REPORT_DIR", "reports")

# 5년 히스토리 기준(PART 5)
HISTORY_YEARS = 5


# ---------------------------------------------------------------- 지표 정의
@dataclass(frozen=True)
class Metric:
    """수집 대상 지표 하나."""

    key: str  # 내부 식별자
    label: str  # 브리핑에 표시할 이름
    category: str  # us_market / mega_tech / korea / rates / fx / commodity / macro / liquidity
    source: str  # yfinance / fred / pykrx
    symbol: str  # 티커 또는 FRED series id
    unit: str = "index"
    fallbacks: tuple[str, ...] = field(default_factory=tuple)
    # 월/주 단위 지표는 전일 대비가 무의미하므로 표기용 주기를 남긴다
    freq: str = "daily"
    note: str = ""


# --- 미국 지수 -------------------------------------------------------------
US_MARKET = [
    Metric("sp500", "S&P500", "us_market", "yfinance", "^GSPC"),
    Metric("nasdaq", "NASDAQ", "us_market", "yfinance", "^IXIC"),
    Metric("nasdaq100", "NASDAQ100", "us_market", "yfinance", "^NDX"),
    Metric("dow", "Dow Jones", "us_market", "yfinance", "^DJI"),
    Metric("russell2000", "Russell 2000", "us_market", "yfinance", "^RUT"),
    # ^SOX는 Yahoo에서 간헐적으로 막힌다. SOXX ETF로 폴백(지수가 아닌 ETF임을 라벨에 표기).
    Metric("sox", "SOX(필라델피아 반도체)", "us_market", "yfinance", "^SOX", fallbacks=("SOXX",)),
    Metric("vix", "VIX", "us_market", "yfinance", "^VIX"),
]

# --- 메가테크 --------------------------------------------------------------
MEGA_TECH = [
    Metric(t.lower(), t, "mega_tech", "yfinance", t, unit="USD")
    for t in ["NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META", "AVGO", "AMD", "MU"]
]

# --- 한국 -----------------------------------------------------------------
KOREA = [
    Metric("kospi", "KOSPI", "korea", "yfinance", "^KS11"),
    Metric("kosdaq", "KOSDAQ", "korea", "yfinance", "^KQ11"),
    Metric("samsung", "삼성전자", "korea", "yfinance", "005930.KS", unit="KRW"),
    Metric("hynix", "SK하이닉스", "korea", "yfinance", "000660.KS", unit="KRW"),
]

# 외국인/기관 순매수 (pykrx). 실패해도 전체 파이프라인은 계속 진행한다.
KOREA_FLOW = {
    "samsung": "005930",
    "hynix": "000660",
}

# --- 금리 (FRED) -----------------------------------------------------------
RATES = [
    Metric("fed_funds", "Fed Funds(실효)", "rates", "fred", "DFF", unit="%"),
    Metric("ust2y", "미국 2년물", "rates", "fred", "DGS2", unit="%"),
    Metric("ust5y", "미국 5년물", "rates", "fred", "DGS5", unit="%"),
    Metric("ust10y", "미국 10년물", "rates", "fred", "DGS10", unit="%"),
    Metric("ust30y", "미국 30년물", "rates", "fred", "DGS30", unit="%"),
    Metric("spread_10y2y", "10Y-2Y 스프레드", "rates", "fred", "T10Y2Y", unit="%p"),
]

# --- 환율 ------------------------------------------------------------------
FX = [
    # DX-Y.NYB(ICE 달러인덱스)가 1순위. 실패 시 FRED 광의달러지수는 '다른 지수'이므로
    # 폴백하지 않고 데이터 없음 처리한다(서로 다른 값을 같은 이름으로 쓰지 않는다).
    Metric("dxy", "DXY(달러인덱스)", "fx", "yfinance", "DX-Y.NYB"),
    Metric("usdkrw", "USD/KRW", "fx", "yfinance", "KRW=X", unit="KRW"),
    Metric("usdjpy", "USD/JPY", "fx", "yfinance", "JPY=X", unit="JPY"),
    Metric("usdcny", "USD/CNY", "fx", "yfinance", "CNY=X", unit="CNY"),
]

# --- 원자재 ----------------------------------------------------------------
COMMODITIES = [
    Metric("wti", "WTI", "commodity", "yfinance", "CL=F", unit="USD"),
    Metric("brent", "Brent", "commodity", "yfinance", "BZ=F", unit="USD"),
    Metric("gold", "금(선물)", "commodity", "yfinance", "GC=F", unit="USD"),
    Metric("copper", "구리(선물)", "commodity", "yfinance", "HG=F", unit="USD"),
]

# --- 매크로 (FRED) ---------------------------------------------------------
MACRO = [
    Metric("cpi", "CPI", "macro", "fred", "CPIAUCSL", unit="index", freq="monthly"),
    Metric("core_cpi", "Core CPI", "macro", "fred", "CPILFESL", unit="index", freq="monthly"),
    Metric("pce", "PCE 물가", "macro", "fred", "PCEPI", unit="index", freq="monthly"),
    Metric("core_pce", "Core PCE", "macro", "fred", "PCEPILFE", unit="index", freq="monthly"),
    Metric("gdp", "실질 GDP", "macro", "fred", "GDPC1", unit="B$", freq="quarterly"),
    Metric("unemployment", "실업률", "macro", "fred", "UNRATE", unit="%", freq="monthly"),
    Metric("nonfarm", "비농업 고용", "macro", "fred", "PAYEMS", unit="천명", freq="monthly"),
    Metric("jobless_claims", "신규 실업수당 청구", "macro", "fred", "ICSA", unit="건", freq="weekly"),
    Metric("retail_sales", "소매판매", "macro", "fred", "RSAFS", unit="M$", freq="monthly"),
    # 주의: 미시간대 소비자심리. Conference Board 소비자신뢰지수와 다른 지표다.
    Metric(
        "consumer_sentiment",
        "소비자심리(미시간대)",
        "macro",
        "fred",
        "UMCSENT",
        unit="index",
        freq="monthly",
        note="Conference Board 소비자신뢰지수와 다른 지표",
    ),
]

# ISM 제조업/서비스업 PMI는 저작권 문제로 FRED에서 제공이 중단됐다.
# 무료 공개 API가 없으므로 '데이터 없음'으로 처리한다(임의 대체 금지).
UNAVAILABLE = {
    "ism_manufacturing": "ISM 제조업 PMI — FRED 제공 중단(저작권). 유료 소스 필요",
    "ism_services": "ISM 서비스업 PMI — FRED 제공 중단(저작권). 유료 소스 필요",
}

# --- 유동성 (FRED) ---------------------------------------------------------
LIQUIDITY = [
    Metric("fed_balance", "Fed 대차대조표", "liquidity", "fred", "WALCL", unit="M$", freq="weekly"),
    Metric("m2", "M2", "liquidity", "fred", "M2SL", unit="B$", freq="monthly"),
    Metric("reverse_repo", "역레포(ON RRP)", "liquidity", "fred", "RRPONTSYD", unit="B$"),
    Metric("tga", "재무부 일반계정(TGA)", "liquidity", "fred", "WTREGEN", unit="B$", freq="weekly"),
    Metric("nfci", "금융여건지수(NFCI)", "liquidity", "fred", "NFCI", unit="index", freq="weekly"),
]

ALL_METRICS: list[Metric] = (
    US_MARKET + MEGA_TECH + KOREA + RATES + FX + COMMODITIES + MACRO + LIQUIDITY
)
METRIC_BY_KEY = {m.key: m for m in ALL_METRICS}

# 브리핑 본문에 반드시 등장하는 핵심 지표(품질검증에서 누락 체크에 사용)
CORE_KEYS = ["sp500", "nasdaq", "sox", "vix", "ust10y", "ust2y", "dxy", "wti", "gold", "usdkrw"]

# 신호 탐지 대상(PART 10)
SIGNAL_KEYS = ["vix", "ust10y", "dxy", "wti", "gold", "copper", "sox", "usdkrw"]

# 상대강도 비교쌍(PART 10)
RELATIVE_PAIRS = [
    ("sox", "sp500", "SOX vs S&P500"),
    ("nasdaq", "sp500", "NASDAQ vs S&P500"),
    ("russell2000", "sp500", "Russell2000 vs S&P500"),
    ("gold", "ust10y", "Gold vs 금리"),
    ("copper", "gold", "Copper vs Gold"),
    ("kospi", "nasdaq", "KOSPI vs NASDAQ"),
    ("samsung", "hynix", "삼성전자 vs SK하이닉스"),
]

# --- 뉴스 (RSS) ------------------------------------------------------------
# API 키가 필요 없는 공개 RSS만 사용한다. 본문은 저장하지 않는다(PART 4).
NEWS_FEEDS = [
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("CNBC Markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("CNBC Economy", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("MarketWatch Top", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("연합인포맥스 증권", "https://news.einfomax.co.kr/rss/S1N2.xml"),
]

NEWS_PRIORITY_KEYWORDS = [
    "fed", "fomc", "powell", "rate", "cpi", "inflation", "pce", "payroll",
    "employment", "jobless", "treasury", "yield", "oil", "opec",
    "ai", "nvidia", "micron", "semiconductor", "chip", "hbm", "dram", "memory",
    "china", "tariff", "korea", "samsung", "sk hynix", "capex",
]

# --- 반도체 체인(PART 11) ---------------------------------------------------
SEMI_CHAIN_KEYS = ["sox", "nvda", "avgo", "amd", "mu", "samsung", "hynix"]
SEMI_EARNINGS_TICKERS = ["NVDA", "MU", "AVGO", "AMD", "TSM"]
