"""저장소 계층 (PART 22).

향후 PostgreSQL 등으로 교체할 수 있도록 Repository 인터페이스와
SQLite 구현을 분리한다. 상위 모듈은 Repository 타입만 알면 된다.
"""
from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA = """
-- 가격/지수 시계열. key+data_date가 유일.
CREATE TABLE IF NOT EXISTS market_data (
    key          TEXT NOT NULL,
    data_date    TEXT NOT NULL,   -- 데이터가 실제로 가리키는 날짜 (YYYY-MM-DD)
    value        REAL,
    unit         TEXT,
    source       TEXT NOT NULL,
    source_url   TEXT,
    symbol       TEXT,
    collected_at TEXT NOT NULL,
    PRIMARY KEY (key, data_date)
);

-- 매크로/유동성 시계열. 발표주기가 달라 별도 테이블로 둔다.
CREATE TABLE IF NOT EXISTS macro_data (
    key          TEXT NOT NULL,
    data_date    TEXT NOT NULL,
    value        REAL,
    unit         TEXT,
    freq         TEXT,
    source       TEXT NOT NULL,
    source_url   TEXT,
    symbol       TEXT,
    collected_at TEXT NOT NULL,
    PRIMARY KEY (key, data_date)
);

-- 뉴스는 메타데이터만 보관한다(본문 저장 금지).
CREATE TABLE IF NOT EXISTS news (
    url          TEXT PRIMARY KEY,
    headline     TEXT NOT NULL,
    source       TEXT NOT NULL,
    published_at TEXT,
    collected_at TEXT NOT NULL,
    impact       TEXT,
    direction    TEXT,
    why          TEXT
);

CREATE TABLE IF NOT EXISTS events (
    event_date   TEXT NOT NULL,
    title        TEXT NOT NULL,
    importance   TEXT,
    source       TEXT,
    kst_time     TEXT,
    PRIMARY KEY (event_date, title)
);

CREATE TABLE IF NOT EXISTS daily_briefing (
    date          TEXT PRIMARY KEY,
    generated_at  TEXT NOT NULL,
    market_regime TEXT,
    market_score  REAL,
    briefing_text TEXT NOT NULL,
    snapshot_json TEXT,            -- 그날 사용한 수치 스냅샷(변화감지/품질검증용)
    sent          INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS market_signals (
    date        TEXT NOT NULL,
    key         TEXT NOT NULL,
    signal      TEXT NOT NULL,
    percentile  REAL,
    value       REAL,
    detail      TEXT,
    PRIMARY KEY (date, key, signal)
);

CREATE TABLE IF NOT EXISTS company_signals (
    date      TEXT NOT NULL,
    company   TEXT NOT NULL,      -- samsung / hynix
    factor    TEXT NOT NULL,      -- HBM, DRAM, FX ...
    score     INTEGER NOT NULL,   -- -2 ~ +2
    reason    TEXT,
    PRIMARY KEY (date, company, factor)
);

CREATE INDEX IF NOT EXISTS idx_market_date ON market_data(data_date);
CREATE INDEX IF NOT EXISTS idx_macro_date ON macro_data(data_date);
CREATE INDEX IF NOT EXISTS idx_news_pub ON news(published_at);
"""


class Repository(ABC):
    """상위 모듈이 의존하는 인터페이스."""

    @abstractmethod
    def upsert_series(self, table: str, rows: Sequence[dict[str, Any]]) -> int: ...

    @abstractmethod
    def get_series(self, table: str, key: str, since: str | None = None) -> list[tuple[str, float]]: ...

    @abstractmethod
    def latest(self, table: str, key: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def save_news(self, rows: Sequence[dict[str, Any]]) -> int: ...

    @abstractmethod
    def recent_news(self, hours: int = 24) -> list[dict[str, Any]]: ...

    @abstractmethod
    def save_events(self, rows: Sequence[dict[str, Any]]) -> int: ...

    @abstractmethod
    def upcoming_events(self, days: int = 7) -> list[dict[str, Any]]: ...

    @abstractmethod
    def save_briefing(self, **kwargs: Any) -> None: ...

    @abstractmethod
    def get_briefing(self, on_date: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def recent_briefings(self, limit: int = 7) -> list[dict[str, Any]]: ...

    @abstractmethod
    def save_signals(self, on_date: str, rows: Sequence[dict[str, Any]]) -> int: ...

    @abstractmethod
    def save_company_signals(self, on_date: str, rows: Sequence[dict[str, Any]]) -> int: ...


class SQLiteRepository(Repository):
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------ 시계열
    def upsert_series(self, table: str, rows: Sequence[dict[str, Any]]) -> int:
        if table not in ("market_data", "macro_data"):
            raise ValueError(f"허용되지 않은 테이블: {table}")
        if not rows:
            return 0
        cols = list(rows[0].keys())
        placeholders = ",".join("?" * len(cols))
        sql = (
            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(key, data_date) DO UPDATE SET "
            + ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("key", "data_date"))
        )
        with self._conn() as c:
            c.executemany(sql, [tuple(r[col] for col in cols) for r in rows])
        return len(rows)

    def get_series(self, table: str, key: str, since: str | None = None) -> list[tuple[str, float]]:
        sql = f"SELECT data_date, value FROM {table} WHERE key=? AND value IS NOT NULL"
        params: list[Any] = [key]
        if since:
            sql += " AND data_date >= ?"
            params.append(since)
        sql += " ORDER BY data_date"
        with self._conn() as c:
            return [(r["data_date"], r["value"]) for r in c.execute(sql, params)]

    def latest(self, table: str, key: str) -> dict[str, Any] | None:
        sql = (
            f"SELECT * FROM {table} WHERE key=? AND value IS NOT NULL "
            f"ORDER BY data_date DESC LIMIT 1"
        )
        with self._conn() as c:
            row = c.execute(sql, (key,)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------ 뉴스
    def save_news(self, rows: Sequence[dict[str, Any]]) -> int:
        if not rows:
            return 0
        sql = (
            "INSERT INTO news (url, headline, source, published_at, collected_at, "
            "impact, direction, why) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(url) DO UPDATE SET impact=excluded.impact, "
            "direction=excluded.direction, why=excluded.why"
        )
        with self._conn() as c:
            c.executemany(
                sql,
                [
                    (
                        r["url"], r["headline"], r["source"], r.get("published_at"),
                        r.get("collected_at", datetime.now().isoformat()),
                        r.get("impact"), r.get("direction"), r.get("why"),
                    )
                    for r in rows
                ],
            )
        return len(rows)

    def recent_news(self, hours: int = 24) -> list[dict[str, Any]]:
        sql = (
            "SELECT * FROM news WHERE published_at >= datetime('now', ?) "
            "ORDER BY published_at DESC"
        )
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, (f"-{hours} hours",))]

    # ------------------------------------------------------------ 이벤트
    def save_events(self, rows: Sequence[dict[str, Any]]) -> int:
        if not rows:
            return 0
        sql = (
            "INSERT INTO events (event_date, title, importance, source, kst_time) "
            "VALUES (?,?,?,?,?) ON CONFLICT(event_date, title) DO UPDATE SET "
            "importance=excluded.importance, kst_time=excluded.kst_time"
        )
        with self._conn() as c:
            c.executemany(
                sql,
                [
                    (r["event_date"], r["title"], r.get("importance", "MEDIUM"),
                     r.get("source"), r.get("kst_time"))
                    for r in rows
                ],
            )
        return len(rows)

    def upcoming_events(self, days: int = 7) -> list[dict[str, Any]]:
        today = date.today().isoformat()
        sql = (
            "SELECT * FROM events WHERE event_date >= ? AND event_date <= date(?, ?) "
            "ORDER BY event_date"
        )
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, (today, today, f"+{days} days"))]

    # ------------------------------------------------------------ 브리핑
    def save_briefing(
        self,
        on_date: str,
        briefing_text: str,
        market_regime: str | None = None,
        market_score: float | None = None,
        snapshot: dict[str, Any] | None = None,
        sent: bool = False,
    ) -> None:
        sql = (
            "INSERT INTO daily_briefing (date, generated_at, market_regime, market_score, "
            "briefing_text, snapshot_json, sent) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(date) DO UPDATE SET generated_at=excluded.generated_at, "
            "market_regime=excluded.market_regime, market_score=excluded.market_score, "
            "briefing_text=excluded.briefing_text, snapshot_json=excluded.snapshot_json, "
            "sent=excluded.sent"
        )
        with self._conn() as c:
            c.execute(
                sql,
                (
                    on_date, datetime.now().isoformat(), market_regime, market_score,
                    briefing_text, json.dumps(snapshot or {}, ensure_ascii=False, default=str),
                    int(sent),
                ),
            )

    def get_briefing(self, on_date: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM daily_briefing WHERE date=?", (on_date,)).fetchone()
        return dict(row) if row else None

    def recent_briefings(self, limit: int = 7) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM daily_briefing ORDER BY date DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def previous_briefing(self, before_date: str) -> dict[str, Any] | None:
        """직전 브리핑(오늘 제외). 주말/휴일로 하루 이상 건너뛸 수 있으므로 날짜 계산 대신 조회."""
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM daily_briefing WHERE date < ? ORDER BY date DESC LIMIT 1",
                (before_date,),
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------ 신호
    def save_signals(self, on_date: str, rows: Sequence[dict[str, Any]]) -> int:
        if not rows:
            return 0
        sql = (
            "INSERT OR REPLACE INTO market_signals (date, key, signal, percentile, value, detail) "
            "VALUES (?,?,?,?,?,?)"
        )
        with self._conn() as c:
            c.executemany(
                sql,
                [
                    (on_date, r["key"], r["signal"], r.get("percentile"),
                     r.get("value"), r.get("detail"))
                    for r in rows
                ],
            )
        return len(rows)

    def save_company_signals(self, on_date: str, rows: Sequence[dict[str, Any]]) -> int:
        if not rows:
            return 0
        sql = (
            "INSERT OR REPLACE INTO company_signals (date, company, factor, score, reason) "
            "VALUES (?,?,?,?,?)"
        )
        with self._conn() as c:
            c.executemany(
                sql,
                [(on_date, r["company"], r["factor"], r["score"], r.get("reason")) for r in rows],
            )
        return len(rows)


def get_repository(path: str | Path) -> Repository:
    """팩토리. 나중에 DSN을 보고 Postgres 구현을 반환하도록 확장한다."""
    return SQLiteRepository(path)
