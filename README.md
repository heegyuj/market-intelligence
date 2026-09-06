# AI Morning Market Intelligence

매일 오전 7시(KST), 미국시장·매크로·반도체·한국증시를 분석해 카카오톡으로 보내는 개인용 시스템.

핵심은 뉴스 요약이 아니라 **"어제와 비교해 무엇이 달라졌는가"** 입니다.

---

## 시작하기 전에 알아야 할 3가지

**1. 카카오톡 텍스트 메시지는 200자가 최대입니다.**
카카오 공식 제약이라 우회할 수 없습니다. 브리핑 전문(약 3,000~4,000자)은 한 건으로 못 보냅니다.
두 가지 모드를 지원합니다.

| 모드 | 동작 | 필요한 설정 |
|---|---|---|
| `split` (기본) | 200자씩 나눠 20~25건 연속 발송 | 없음 |
| `link` (권장) | 요약 1건 + "전체 브리핑 보기" 버튼 | 공개 URL + 도메인 등록 |

매일 아침 25건이 연속으로 오는 게 부담이면 `link` 모드를 쓰세요(아래 8단계).

**2. 무료로 못 구하는 데이터가 있습니다.** 이 시스템은 없는 값을 만들어내지 않고 "데이터 없음"으로 표시합니다.

- ISM 제조업/서비스업 PMI — FRED에서 저작권 문제로 제공 중단
- DRAM/NAND 고정거래가 — 무료 공개 소스 없음 (뉴스로만 간접 판단)
- Conference Board 소비자신뢰지수 — 미시간대 소비자심리로 대체 (다른 지표임을 표기)

**3. AI 비용이 듭니다.** 하루 3회 호출(뉴스 분류 + 본분석 + 품질검증), 월 2~5달러 수준입니다.
`AI_MODEL`을 `claude-sonnet-5`로 바꾸면 더 저렴합니다.

---

## 1단계. Python 설치

Python 3.11 이상. [python.org](https://www.python.org/downloads/)에서 설치하며
**"Add Python to PATH"를 반드시 체크**하세요.

```bash
python --version   # 3.11 이상이면 OK
```

## 2단계. 프로젝트 설치

```bash
cd market-intelligence
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## 3단계. API 키 발급

**FRED** (금리·물가·고용·유동성. 무료, 즉시 발급)
1. https://fredaccount.stlouisfed.org/apikeys 가입
2. "Request API Key" → 용도에 "personal research" 입력
3. 32자리 키 복사

**Anthropic** (AI 분석)
1. https://console.anthropic.com 가입
2. Settings → API Keys → Create Key
3. 결제수단 등록 필요 (최소 $5)

주가·환율·원자재는 Yahoo Finance라 키가 필요 없습니다.

## 4단계. 환경변수 설정

```bash
cp .env.example .env
```

`.env`를 열어 채웁니다. **이 파일은 절대 GitHub에 올리지 마세요**(`.gitignore`에 이미 포함).

```
ANTHROPIC_API_KEY=sk-ant-...
FRED_API_KEY=...
```

## 5단계. 카카오 인증

**앱 설정** — https://developers.kakao.com/console/app

1. **애플리케이션 추가하기** → 앱 이름 입력 → REST API 키 복사
2. **카카오 로그인** → 활성화 설정 **ON**
3. **카카오 로그인 → Redirect URI** 등록: `https://example.com/oauth`
   (실제 서버 불필요. 리다이렉트된 주소창의 code 값만 사용합니다)
4. **카카오 로그인 → 동의항목** → "카카오톡 메시지 전송(talk_message)" → **선택 동의**로 설정

> 4번을 빠뜨리면 발송 시 `403 (-402)` 오류가 납니다. 가장 흔한 실패 원인입니다.

**토큰 발급**

```bash
python scripts/kakao_auth.py
```

안내에 따라 주소를 브라우저에 붙여넣고 동의 → 이동한 주소의 `?code=` 뒤 값을 붙여넣으면
`KAKAO_ACCESS_TOKEN`과 `KAKAO_REFRESH_TOKEN`이 출력됩니다. `.env`에 넣으세요.

`access_token`은 프로그램이 발송 직전마다 자동 갱신하므로 `refresh_token`만 잘 보관하면 됩니다.
`refresh_token`은 2개월마다 만료되고, 한 달 안에 사용하면 자동 연장됩니다.
새로 발급되면 로그에 경고가 뜨고 `token_store.json`에 저장되니 그때만 Secrets를 갱신하세요.

## 6단계. 로컬 테스트 (발송 없음)

```bash
python -m src.main --test
```

첫 실행은 5년치를 받느라 3~5분 걸립니다. 이후에는 1분 내외입니다.
브리핑이 화면에 출력되고 `reports/YYYY-MM-DD.md`에 저장됩니다.

문제 파악용 옵션:

```bash
python -m src.main --test --no-ai      # AI 없이 정량 데이터만 (수집 문제 격리)
python -m src.main --test --skip-collect  # 수집 생략, DB로만 분석 (분석 문제 격리)
python -m src.main --backfill          # 5년 히스토리 강제 재수집
python -m tests/test_pipeline.py       # 네트워크 없이 분석 로직 전체 검증
```

## 7단계. 카카오톡 테스트 발송

```bash
python -m src.main --send-test
```

카카오톡 "나와의 채팅"에 테스트 메시지 1건이 오면 성공입니다.

| 오류 | 원인 |
|---|---|
| `403 (-402)` | 동의항목 `talk_message` 미설정 (5단계 4번) |
| `401 (-401)` | access_token 만료 → refresh_token 확인 |
| `KOE320` | 인가 코드를 이미 사용했거나 만료. 5단계를 다시 실행 |

## 8단계. (선택) 링크 모드 설정

25건 분할 발송이 싫다면.

1. GitHub 저장소를 만들고 코드를 올립니다(9단계).
2. `.env` 또는 Secrets에 추가:
   ```
   KAKAO_SEND_MODE=link
   BRIEFING_PUBLIC_URL=https://github.com/<사용자명>/<저장소명>/blob/main/reports/latest.md
   ```
3. 카카오 개발자 콘솔 → **앱 → 제품 링크 관리 → 웹 도메인**에 `https://github.com` 등록

`reports/latest.md`는 매 실행마다 최신 브리핑으로 덮어써지므로 URL은 고정입니다.
저장소를 private으로 두면 본인만 볼 수 있습니다.

## 9단계. GitHub 저장소 생성

```bash
git init
git add .
git commit -m "AI Morning Market Intelligence"
git branch -M main
git remote add origin https://github.com/<사용자명>/<저장소명>.git
git push -u origin main
```

`.env`가 올라가지 않았는지 반드시 확인하세요.

```bash
git ls-files | grep .env    # .env.example만 나와야 정상
```

## 10단계. GitHub Secrets 등록

저장소 → **Settings → Secrets and variables → Actions → New repository secret**

| 이름 | 필수 | 값 |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | Anthropic 키 |
| `FRED_API_KEY` | ✅ | FRED 키 |
| `KAKAO_REST_API_KEY` | ✅ | 카카오 REST API 키 |
| `KAKAO_REFRESH_TOKEN` | ✅ | 5단계 결과 |
| `KAKAO_ACCESS_TOKEN` | | 자동 갱신되므로 선택 |
| `KAKAO_CLIENT_SECRET` | | 콘솔에서 사용 설정한 경우만 |
| `BRIEFING_PUBLIC_URL` | | 링크 모드용 |

`Variables` 탭에는 `KAKAO_SEND_MODE`(split/link), `AI_MODEL`을 넣을 수 있습니다.

## 11단계. 자동 실행 확인

저장소 → **Actions** 탭 → "Morning Market Briefing" → **Run workflow**로 수동 실행해 봅니다.
정상이면 다음 날부터 매일 07:00 KST에 자동 실행됩니다.

```yaml
- cron: "0 22 * * 0-4"   # UTC 22:00 = KST 익일 07:00, 월~금
```

> GitHub의 무료 스케줄러는 부하에 따라 5~30분 늦어질 수 있습니다.
> 정시가 중요하면 cron을 `0 21 * * 0-4`(KST 06:00)로 당기세요.

## 12단계. 오류 해결

| 증상 | 확인 |
|---|---|
| 지표 대부분 "데이터 없음" | `--no-ai`로 실행해 수집 단계 로그 확인. Yahoo가 일시적으로 막는 경우가 있음 |
| 금리·물가만 없음 | `FRED_API_KEY` 확인 |
| ⑤ 섹션이 "직전 브리핑 없음" | 정상. 2회차부터 비교가 시작됨 |
| Actions에서만 5년치를 매번 받음 | DB 캐시 미스. `reports/`의 스냅샷으로 브리핑 이력은 복구되므로 기능 문제는 없음 |
| 반도체 정성 항목이 전부 "근거 없음" | 정상. 해당 뉴스가 없으면 억지로 점수를 만들지 않음 |
| 카카오만 실패 | `python -m src.main --send-test`로 분리 테스트 |

---

## 파일 구조

```
src/
├── config.py               지표·티커·FRED 시리즈 정의 (여기만 고치면 지표 추가 가능)
├── collectors/
│   ├── market.py           yfinance — 지수/개별주/환율/원자재
│   ├── macro.py            FRED — 금리/물가/고용/유동성
│   ├── korea.py            pykrx — 외국인·기관 순매수
│   ├── news.py             공개 RSS — 헤드라인만 저장(본문 저장 안 함)
│   └── events.py           FRED 발표일정 + 실적일정 + 수동 등록
├── analysis/
│   ├── trends.py           기간별 변화·5년 통계·퍼센타일·전년동월
│   ├── regime.py           6축 매크로 국면 점수
│   ├── signals.py          5년 퍼센타일 기반 이상신호
│   ├── semiconductor.py    사이클 점수 + 삼성/하이닉스 영향
│   └── change_detector.py  직전 브리핑 대비 변화 (핵심)
├── ai/
│   ├── prompts.py          프롬프트 (분석 원칙을 여기서 강제)
│   └── analyst.py          Claude API 호출
├── briefing/
│   ├── generator.py        최종 브리핑 조립
│   └── quality.py          품질검증 (규칙 + AI)
├── notification/kakao.py   나에게 보내기 + 토큰 자동 갱신 + 200자 분할
├── storage/database.py     SQLite (Repository 계층 분리 → Postgres 교체 가능)
└── main.py                 파이프라인
```

## 설계 원칙

- **없는 데이터는 만들지 않는다.** 수집 실패 항목은 브리핑 하단에 그대로 노출됩니다.
- **오래된 값을 현재값처럼 쓰지 않는다.** 3일 이상 지난 데이터는 `[기준일]`을 붙입니다.
- **정량과 정성을 구분한다.** 규칙 엔진이 계산한 점수와 AI가 뉴스로 판단한 점수를 분리 표기합니다.
- **투자 판단을 지시하지 않는다.** 품질검증 단계에서 매수/매도/목표가 표현을 자동 검출합니다.

## 지표 추가하기

`src/config.py`에 한 줄 추가하면 수집·통계·퍼센타일·전년동월이 전부 자동으로 붙습니다.

```python
US_MARKET.append(
    Metric("vxn", "VXN(나스닥 변동성)", "us_market", "yfinance", "^VXN")
)
```

## 보안

`.env`, `token_store.json`, `*.db`는 `.gitignore`에 포함되어 있습니다.
키를 실수로 커밋했다면 즉시 해당 서비스에서 **재발급**하세요. 커밋 기록에서 지워도 이미 노출된 것으로 봐야 합니다.
