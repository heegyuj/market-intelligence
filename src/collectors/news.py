"""뉴스 수집 (공개 RSS).

PART 4 원칙: headline / source / published_at / url 만 저장한다.
본문은 저장하지 않고, AI 분석에도 헤드라인만 넘긴다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import NEWS_FEEDS, NEWS_PRIORITY_KEYWORDS

log = logging.getLogger(__name__)


def _parse_time(entry) -> str | None:
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc).isoformat()
            except Exception:
                continue
    return None


def _priority(headline: str) -> int:
    h = headline.lower()
    return sum(1 for kw in NEWS_PRIORITY_KEYWORDS if kw in h)


def fetch(hours: int = 24, limit: int = 60) -> list[dict[str, Any]]:
    """최근 N시간 헤드라인을 우선순위 키워드 매칭 순으로 정렬해 반환."""
    try:
        import feedparser
    except ImportError:
        log.error("feedparser 미설치 → 뉴스 수집 불가")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    collected_at = datetime.now().isoformat(timespec="seconds")
    seen: set[str] = set()
    items: list[dict[str, Any]] = []

    for source, url in NEWS_FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception as exc:
            log.warning("RSS 실패 %s: %s", source, exc)
            continue
        if getattr(feed, "bozo", 0) and not feed.entries:
            log.warning("RSS 파싱 실패 %s", source)
            continue

        for e in feed.entries:
            link = getattr(e, "link", None)
            title = getattr(e, "title", None)
            if not link or not title or link in seen:
                continue
            published = _parse_time(e)
            if published:
                try:
                    if datetime.fromisoformat(published) < cutoff:
                        continue
                except ValueError:
                    pass
            seen.add(link)
            items.append(
                {
                    "url": link,
                    "headline": title.strip(),
                    "source": source,
                    "published_at": published,
                    "collected_at": collected_at,
                    "_priority": _priority(title),
                }
            )

    items.sort(key=lambda x: (-x["_priority"], x.get("published_at") or ""), reverse=False)
    items.sort(key=lambda x: -x["_priority"])
    for i in items:
        i.pop("_priority", None)
    return items[:limit]


def collect(repo, hours: int = 24, limit: int = 60) -> list[dict[str, Any]]:
    items = fetch(hours, limit)
    if items:
        repo.save_news(items)
    log.info("news %d headlines", len(items))
    return items
