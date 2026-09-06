"""파이프라인 실행 (PART 1).

  python -m src.main               정상 실행 (수집 → 분석 → 브리핑 → 카카오 발송)
  python -m src.main --test        카카오 발송 없이 브리핑만 생성 후 출력
  python -m src.main --send-test   카카오 테스트 메시지 1건 발송
  python -m src.main --backfill    5년 히스토리 전체 재수집
  python -m src.main --no-ai       AI 호출 없이 정량 데이터만으로 생성

GitHub Actions에서는 DB가 캐시이므로 유실될 수 있다.
브리핑과 수치 스냅샷은 reports/ 에 텍스트로 남기고, DB가 비어 있으면 거기서 복구한다.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from . import config
from .ai.analyst import Analyst
from .analysis import change_detector, regime as regime_mod, semiconductor, signals as signal_mod
from .analysis.trends import MetricStats, compute
from .briefing import generator, quality
from .collectors import events as events_col, korea, macro, market, news as news_col
from .notification.kakao import KakaoSender
from .storage.database import get_repository

log = logging.getLogger("market-intelligence")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("yfinance").setLevel(logging.ERROR)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("peewee").setLevel(logging.ERROR)


# --------------------------------------------------------------------- 아카이브
def restore_archive(repo) -> int:
    """reports/*.snapshot.json 을 DB로 복구 (Actions 캐시 유실 대비)."""
    if repo.recent_briefings(1):
        return 0
    restored = 0
    for f in sorted(config.REPORT_DIR.glob("*.snapshot.json")):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
            md = config.REPORT_DIR / f.name.replace(".snapshot.json", ".md")
            repo.save_briefing(
                on_date=payload["date"],
                briefing_text=md.read_text(encoding="utf-8") if md.exists() else "",
                market_regime=payload.get("market_regime"),
                market_score=payload.get("market_score"),
                snapshot=payload.get("snapshot"),
                sent=True,
            )
            restored += 1
        except Exception as exc:
            log.warning("아카이브 복구 실패 %s: %s", f.name, exc)
    if restored:
        log.info("아카이브에서 브리핑 %d건 복구", restored)
    return restored


def archive(on_date: str, briefing: str, snapshot: dict, regime_name: str, score) -> Path:
    config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    md = config.REPORT_DIR / f"{on_date}.md"
    md.write_text(briefing, encoding="utf-8")
    # 카카오 링크 모드용 고정 URL 대상. 항상 최신 브리핑을 가리킨다.
    # GitHub 마크다운 렌더러가 단일 줄바꿈을 무시해 레이아웃이 뭉개지므로
    # 코드블록으로 감싼다. 날짜별 원본(.md)은 평문 그대로 둔다(아카이브 복구용).
    (config.REPORT_DIR / "latest.md").write_text(
        f"# {on_date} 마켓 브리핑\n\n```text\n{briefing}\n```\n", encoding="utf-8"
    )
    (config.REPORT_DIR / f"{on_date}.snapshot.json").write_text(
        json.dumps(
            {"date": on_date, "market_regime": regime_name, "market_score": score,
             "snapshot": snapshot},
            ensure_ascii=False, default=str,
        ),
        encoding="utf-8",
    )
    return md


# --------------------------------------------------------------------- 수집
def collect_all(repo, years: int, skip: bool = False) -> tuple[list[dict], list[dict], list[str]]:
    gaps: list[str] = []
    if skip:
        log.info("수집 건너뜀 (--skip-collect)")
        return repo.recent_news(24), repo.upcoming_events(7), gaps

    log.info("[1/4] 시장 데이터 수집")
    res = market.collect(repo, years=years)
    gaps += [f"{config.METRIC_BY_KEY[k].label} 수집 실패" for k, n in res.items() if n == 0]

    log.info("[2/4] 매크로 데이터 수집")
    res = macro.collect(repo, years=years)
    gaps += [f"{config.METRIC_BY_KEY[k].label} 수집 실패" for k, n in res.items() if n == 0]

    log.info("[3/4] 한국 수급 수집")
    if korea.collect(repo) == 0:
        gaps.append("한국 외국인/기관 수급 수집 실패")

    log.info("[4/4] 뉴스·이벤트 수집")
    headlines = news_col.collect(repo, hours=24)
    if not headlines:
        gaps.append("뉴스 수집 실패")
    upcoming = events_col.collect(repo, days=7)

    gaps += [v for v in config.UNAVAILABLE.values()]
    return headlines, upcoming, gaps


# --------------------------------------------------------------------- 분석
def build_stats(repo, as_of: date) -> tuple[dict[str, MetricStats], dict[str, list]]:
    stats: dict[str, MetricStats] = {}
    raw: dict[str, list] = {}
    since = (as_of.replace(year=as_of.year - config.HISTORY_YEARS - 1)).isoformat()

    for m in config.ALL_METRICS:
        table = "macro_data" if m.source == "fred" else "market_data"
        rows = repo.get_series(table, m.key, since=since)
        raw[m.key] = rows
        stats[m.key] = compute(
            m.key, m.label, rows, unit=m.unit, freq=m.freq, as_of=as_of,
            source="FRED" if m.source == "fred" else "Yahoo Finance",
            note=m.note,
        )
    # 수급 시계열
    for name in config.KOREA_FLOW:
        for suffix in ("foreign", "institution"):
            key = f"{name}_{suffix}_net"
            raw[key] = repo.get_series("market_data", key, since=since)
    return stats, raw


def build_ai_context(
    stats, regime, semi, samsung_scores, hynix_scores, changes, headlines, upcoming, weekly
) -> dict:
    """AI에 넘길 압축 컨텍스트. 5년치 원본을 넘기지 않는다(토큰 절약)."""
    def slim(st: MetricStats) -> dict:
        return {
            "label": st.label, "value": st.value, "unit": st.unit,
            "data_date": st.data_date,
            "changes": {k: v for k, v in st.changes.items() if v.get("prev") is not None},
            "percentile_5y": round(st.percentile_5y, 1) if st.percentile_5y is not None else None,
            "stats_5y": {k: v for k, v in (st.stats_5y or {}).items()
                         if k in ("average", "high", "low")},
            "yoy": st.yoy if st.yoy.get("available") else None,
        }

    return {
        "as_of": date.today().isoformat(),
        "metrics": {k: slim(v) for k, v in stats.items() if v.available},
        "unavailable_metrics": [k for k, v in stats.items() if not v.available],
        "macro_regime_quant": regime,
        "semiconductor": semi,
        "samsung_factors": samsung_scores,
        "hynix_factors": hynix_scores,
        "change_detection": changes,
        "news_headlines": [
            {"headline": h["headline"], "source": h["source"], "published_at": h.get("published_at")}
            for h in headlines[:25]
        ],
        "upcoming_events": upcoming,
        "recent_briefings": weekly,
    }


# --------------------------------------------------------------------- 실행
def run(args) -> int:
    today = date.today()
    repo = get_repository(config.DB_PATH)
    restore_archive(repo)

    years = config.HISTORY_YEARS if args.backfill or not repo.recent_briefings(1) else 2
    headlines, upcoming, gaps = collect_all(repo, years=years, skip=args.skip_collect)

    log.info("분석 시작")
    stats, raw = build_stats(repo, today)
    available = sum(1 for s in stats.values() if s.available)
    log.info("지표 %d/%d 확보", available, len(stats))

    regime = regime_mod.evaluate(stats)
    sig = signal_mod.detect(stats, raw)
    semi = semiconductor.cycle_score(stats, raw, headlines)
    samsung_scores = semiconductor.company_scores("samsung", stats, raw)
    hynix_scores = semiconductor.company_scores("hynix", stats, raw)

    prev = repo.previous_briefing(today.isoformat())
    changes = change_detector.compare(stats, prev)
    weekly = change_detector.week_context(repo.recent_briefings(7))

    # AI
    analyst = Analyst()
    if args.no_ai:
        analyst.enabled = False
    triaged = analyst.triage_news(headlines) if analyst.enabled else []
    if triaged:
        repo.save_news(triaged)

    ctx = build_ai_context(stats, regime, semi, samsung_scores, hynix_scores,
                           changes, triaged or headlines, upcoming, weekly)
    ai = analyst.analyze(ctx) if analyst.enabled else None
    if analyst.enabled and ai is None:
        log.error("AI 분석 실패 → 정량 데이터만으로 브리핑 생성")
        gaps.append("AI 분석 실패")

    # 정성 항목 병합 (PART 12)
    _merge_qualitative(samsung_scores, (ai or {}).get("samsung_qualitative"))
    _merge_qualitative(hynix_scores, (ai or {}).get("hynix_qualitative"))
    samsung_v = semiconductor.verdict(samsung_scores)
    hynix_v = semiconductor.verdict(hynix_scores)

    briefing = generator.build(
        stats=stats, regime=regime, signals=signal_mod.to_alert_lines(sig),
        semi=semi, samsung=samsung_v, hynix=hynix_v, changes=changes,
        events=upcoming, ai=ai, news=triaged, today=today, data_gaps=gaps,
    )

    # 품질검증 (PART 18)
    issues = quality.rule_check(briefing, stats, today)
    if analyst.enabled:
        ai_check = analyst.verify(briefing, ctx)
        briefing, applied = quality.apply_ai_fixes(briefing, ai_check)
        issues += applied
    log.info(quality.report(issues))

    snapshot = change_detector.build_snapshot(
        stats, {"regime": regime["regime"], "semi_score": semi.get("score")}
    )
    repo.save_briefing(
        on_date=today.isoformat(), briefing_text=briefing,
        market_regime=regime["regime"], market_score=regime["total"],
        snapshot=snapshot, sent=False,
    )
    path = archive(today.isoformat(), briefing, snapshot, regime["regime"], regime["total"])
    log.info("아카이브 저장: %s", path)

    if args.test:
        print("\n" + briefing + "\n")
        print(f"[--test] 카카오 발송 생략 · 본문 {len(briefing)}자 · "
              f"분할 시 {len(KakaoSender.chunk(briefing))}건")
        return 0

    sender = KakaoSender()
    summary = generator.summarize_for_kakao(briefing, ai, stats)
    result = sender.send_briefing(briefing, summary)
    log.info("카카오 발송 결과: %s", result)
    if result.get("ok"):
        repo.save_briefing(
            on_date=today.isoformat(), briefing_text=briefing,
            market_regime=regime["regime"], market_score=regime["total"],
            snapshot=snapshot, sent=True,
        )
        return 0
    log.error("카카오 발송 실패: %s", result.get("errors") or result.get("error"))
    return 1


def _merge_qualitative(scores: list[dict], ai_items) -> None:
    if not ai_items:
        return
    by_factor = {s["factor"]: s for s in scores}
    for item in ai_items:
        f = item.get("factor")
        if f in by_factor and by_factor[f]["score"] is None:
            try:
                by_factor[f]["score"] = max(-2, min(2, int(item.get("score", 0))))
            except (TypeError, ValueError):
                continue
            by_factor[f]["reason"] = f"(뉴스 기반) {item.get('reason', '')}"


def send_test() -> int:
    sender = KakaoSender()
    if not sender.configured:
        print("카카오 인증정보가 없습니다. .env를 확인하세요.")
        return 1
    sender.refresh()
    try:
        sender.send_text(
            f"✅ MARKET INTELLIGENCE 테스트\n{datetime.now():%Y-%m-%d %H:%M} 발송 정상"
        )
        print("테스트 메시지 발송 성공. 카카오톡 '나와의 채팅'을 확인하세요.")
        return 0
    except Exception as exc:
        print(f"발송 실패: {exc}")
        return 1


def main() -> int:
    p = argparse.ArgumentParser(description="AI Morning Market Intelligence")
    p.add_argument("--test", action="store_true", help="발송 없이 브리핑만 생성")
    p.add_argument("--send-test", action="store_true", help="카카오 테스트 메시지 발송")
    p.add_argument("--backfill", action="store_true", help="5년 히스토리 전체 재수집")
    p.add_argument("--skip-collect", action="store_true", help="수집 생략, DB 데이터로 분석")
    p.add_argument("--no-ai", action="store_true", help="AI 호출 없이 정량 데이터만")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    setup_logging(args.verbose)
    if args.send_test:
        return send_test()
    try:
        return run(args)
    except Exception:
        log.exception("파이프라인 실패")
        return 1


if __name__ == "__main__":
    sys.exit(main())
