"""네트워크 없이 분석 파이프라인 전체를 검증한다.

합성 시계열을 DB에 넣고 수집기를 제외한 모든 단계를 돌린다.

  python -m tests.test_pipeline
"""
from __future__ import annotations

import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402
from src.analysis import change_detector, regime as regime_mod, semiconductor  # noqa: E402
from src.analysis import signals as signal_mod  # noqa: E402
from src.analysis.trends import compute  # noqa: E402
from src.briefing import generator, quality  # noqa: E402
from src.notification.kakao import KakaoSender  # noqa: E402
from src.storage.database import get_repository  # noqa: E402

TODAY = date.today()
DB = Path("/tmp/test_market.db")

# 지표별 (시작값, 일간변동성, 연간드리프트)
PROFILE = {
    "vix": (16.0, 0.06, 0.0),
    "ust2y": (3.9, 0.01, 0.0),
    "ust10y": (4.3, 0.01, 0.02),
    "ust5y": (4.0, 0.01, 0.0),
    "ust30y": (4.6, 0.01, 0.0),
    "fed_funds": (4.3, 0.002, -0.1),
    "spread_10y2y": (0.4, 0.01, 0.0),
    "usdkrw": (1350.0, 0.004, 0.02),
    "dxy": (103.0, 0.003, 0.0),
    "wti": (75.0, 0.015, 0.0),
    "gold": (2400.0, 0.008, 0.15),
    "copper": (4.2, 0.012, 0.03),
}
DEFAULT = (100.0, 0.011, 0.12)


def synth(key: str, days: int = 1500) -> list[tuple[str, float]]:
    start, vol, drift = PROFILE.get(key, DEFAULT)
    rnd = random.Random(hash(key) & 0xFFFF)
    rows, v = [], start
    for i in range(days):
        d = TODAY - timedelta(days=days - i)
        if d.weekday() >= 5:
            continue
        v *= math.exp(drift / 252 + rnd.gauss(0, vol))
        rows.append((d.isoformat(), round(v, 4)))
    return rows


def seed(repo) -> None:
    for m in config.ALL_METRICS:
        table = "macro_data" if m.source == "fred" else "market_data"
        rows = synth(m.key)
        if m.freq in ("monthly", "quarterly"):
            rows = [r for r in rows if r[0].endswith("-01") or r[0][8:10] in ("01", "02", "03")][-70:]
        payload = []
        for d, v in rows:
            item = {
                "key": m.key, "data_date": d, "value": v, "unit": m.unit,
                "source": "SYNTHETIC", "source_url": "test://", "symbol": m.symbol,
                "collected_at": TODAY.isoformat(),
            }
            if table == "macro_data":
                item["freq"] = m.freq
            payload.append(item)
        repo.upsert_series(table, payload)

    # 외국인 수급
    rnd = random.Random(7)
    for name in config.KOREA_FLOW:
        repo.upsert_series("market_data", [
            {"key": f"{name}_foreign_net", "data_date": (TODAY - timedelta(days=i)).isoformat(),
             "value": rnd.uniform(-3e11, 4e11), "unit": "KRW", "source": "SYNTHETIC",
             "source_url": "test://", "symbol": config.KOREA_FLOW[name],
             "collected_at": TODAY.isoformat()}
            for i in range(1, 30)
        ])


def main() -> int:
    DB.unlink(missing_ok=True)
    repo = get_repository(DB)
    seed(repo)
    print("1) 합성 데이터 적재 완료")

    stats, raw = {}, {}
    since = (TODAY.replace(year=TODAY.year - 6)).isoformat()
    for m in config.ALL_METRICS:
        table = "macro_data" if m.source == "fred" else "market_data"
        rows = repo.get_series(table, m.key, since=since)
        raw[m.key] = rows
        stats[m.key] = compute(m.key, m.label, rows, unit=m.unit, freq=m.freq, as_of=TODAY)
    for name in config.KOREA_FLOW:
        raw[f"{name}_foreign_net"] = repo.get_series("market_data", f"{name}_foreign_net", since=since)

    ok = sum(1 for s in stats.values() if s.available)
    assert ok == len(stats), f"지표 계산 실패: {ok}/{len(stats)}"
    spx = stats["sp500"]
    assert spx.percentile_5y is not None and spx.stats_5y["n"] > 1000
    assert spx.yoy["available"], "전년동월 비교 실패"
    assert "1D" in spx.changes and spx.changes["1D"]["pct"] is not None
    assert "1D" not in stats["cpi"].changes, "월간 지표에 1D 변화가 계산됨"
    print(f"2) 추세 계산 OK — 지표 {ok}개, S&P500 5년 {spx.percentile_5y:.0f}퍼센타일, "
          f"1Y {spx.changes['1Y']['pct']:+.1f}%")

    reg = regime_mod.evaluate(stats)
    assert reg["regime"], "국면 분류 실패"
    print(f"3) 매크로 국면 OK — {reg['regime']} (총점 {reg['total']}, 커버리지 {reg['coverage']})")

    sig = signal_mod.detect(stats, raw)
    lines = signal_mod.to_alert_lines(sig)
    print(f"4) 신호 탐지 OK — {len(sig)}건 (표시 {len(lines)}건)")

    semi = semiconductor.cycle_score(stats, raw, [{"headline": "Micron HBM supply update"}])
    s_scores = semiconductor.company_scores("samsung", stats, raw)
    h_scores = semiconductor.company_scores("hynix", stats, raw)
    sv, hv = semiconductor.verdict(s_scores), semiconductor.verdict(h_scores)
    assert sv["emoji"] in ("🟢", "🟡", "🔴", "⚪")
    print(f"5) 반도체 엔진 OK — 사이클 {semi['score']}, 삼성 {sv['emoji']}{sv['label']}, "
          f"하이닉스 {hv['emoji']}{hv['label']}")

    # 변화감지: 어제 브리핑을 심어두고 비교
    snap_prev = change_detector.build_snapshot(stats)
    for k in ("ust10y", "wti"):
        snap_prev["metrics"][k]["value"] *= 0.95
        snap_prev["metrics"][k]["data_date"] = (TODAY - timedelta(days=1)).isoformat()
    repo.save_briefing(
        on_date=(TODAY - timedelta(days=1)).isoformat(),
        briefing_text="어제 브리핑\nONE-LINE VIEW\n\"금리 상승이 지수를 눌렀다\"",
        market_regime="LATE EXPANSION", market_score=1, snapshot=snap_prev,
    )
    prev = repo.previous_briefing(TODAY.isoformat())
    changes = change_detector.compare(stats, prev)
    assert changes["available"] and changes["candidates"], "변화감지 실패"
    top = changes["candidates"][0]
    weekly = change_detector.week_context(repo.recent_briefings(7))
    assert weekly[0]["one_line"], "직전 한줄평 추출 실패"
    print(f"6) 변화감지 OK — 후보 {len(changes['candidates'])}건, 1위: {top['summary']}")

    briefing = generator.build(
        stats=stats, regime=reg, signals=lines, semi=semi, samsung=sv, hynix=hv,
        changes=changes, events=[{"event_date": (TODAY + timedelta(days=2)).isoformat(),
                                  "title": "Consumer Price Index", "importance": "HIGH"}],
        ai=None, news=None, today=TODAY, data_gaps=list(config.UNAVAILABLE.values()),
    )
    for section in ["① 🇺🇸", "② 💵", "③ 📊", "④ 📅", "⑤ 🔄", "⑥ 🔥", "⑦ 🌎",
                    "⑧ 🚨", "⑨ 📅", "⑩ 🎯", "ONE-LINE VIEW"]:
        assert section in briefing, f"섹션 누락: {section}"
    print(f"7) 브리핑 생성 OK — {len(briefing)}자, 전 섹션 존재")

    issues = quality.rule_check(briefing, stats, TODAY)
    advice = [i for i in issues if i["type"] == 8]
    assert not advice, f"투자권유 표현 검출: {advice}"
    print(f"8) 품질검증 OK — {quality.report(issues).splitlines()[0]}")

    chunks = KakaoSender.chunk(briefing)
    over = [c for c in chunks if len(c) > 190]
    assert not over, f"200자 초과 청크 {len(over)}건"
    print(f"9) 카카오 분할 OK — {len(chunks)}건, 최대 {max(len(c) for c in chunks)}자")

    repo.save_briefing(on_date=TODAY.isoformat(), briefing_text=briefing,
                       market_regime=reg["regime"], market_score=reg["total"],
                       snapshot=change_detector.build_snapshot(stats))
    assert repo.get_briefing(TODAY.isoformat())
    print("10) 저장/조회 OK")
    print("\n=== 전체 통과 ===")
    print(briefing[:600])
    return 0


if __name__ == "__main__":
    sys.exit(main())
