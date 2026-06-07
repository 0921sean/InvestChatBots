# InvestChatBots

AI 투자 토론방 — 7개의 AI 봇이 평일 국장·미장 사이클로 20개 섹터·68개 종목을 분석하고, 과반수 동의 시 가상 포트폴리오에 자동 매매하는 시스템.

---

## 개요

평일 **오전 6시(국장 KRX) / 오후 6시(미장 US)** 두 번, 7개의 AI 봇이 섹터 토론을 거쳐 종목별 매수/관망/매도를 투표한다. **4표 이상(7봇 과반)** 매수 동의 시 가상 포트폴리오에 매수가 체결되고 ntfy/메일 알림이 발송된다. 메인 사이클이 끝난 이후엔 텔레그램 채널에서 언급된 신규 종목을 워치리스트 서브 사이클(국장 12시 / 미장 0시)로 추가 분석한다.

> 모든 시각은 **한국 시간(KST)** 기준. 자세한 운영 규칙은 `CLAUDE.md` 참조.

---

## 시스템 구조

```
메인 사이클 (평일)
  06:00 국장(KRX, 11섹터) / 18:00 미장(US, 9섹터)
  → 섹터별 토론 1라운드 → 합의 추출(JSON)
  → 섹터 내 종목 분석 (7봇 투표)
  → 매수 4표 이상 → 가상 포트폴리오 체결 + ntfy/메일 알림
  → 완료 시 cycle_rest 진입
  → 진입 직후 해당 시장 보유 종목 점검(홀드/매도 재투표) + -20% 손절 체크 자동 실행

워치리스트 (서브 사이클 — 메인 cycle_rest 이후에만)
  12:00 국장 후보 / 00:00 미장 후보(화~금)
  → 텔레그램 채널 스캔 → 오늘 미분석 신규 종목 감지
  → 봇들 즉석 1라운드 토론 → 매수 4표 이상 시 체결
```

---

## AI 봇 구성 (활성 7봇)

런타임 발화 순서는 `prompts.py`의 `AGENT_ORDER`가 단일 진실원천이다. **현재 활성 봇은 전원 Claude Haiku** (`claude-haiku-4-5-20251001`).

| 봇 | 모델 | 역할 |
|---|---|---|
| 드가자 | Claude Haiku | 낙관론자 — 매수 기회·상승 모멘텀 우선 탐색 |
| INTJ | Claude Haiku | 리스크 관리자 — 하방 시나리오·포트폴리오 집중도 감시 |
| 퀀트중독자 | Claude Haiku | PER·ROE·EPS 수치 + 절대 기준선(영업익 성장 15%↑·PER 한국≤30/미국≤35) |
| 빅픽처 | Claude Haiku | 금리·환율·지정학 등 거시 큰 그림 |
| 차트천재 | Claude Haiku | 주봉·일봉 패턴·수렴돌파·이동평균 |
| 경력직 | Claude Haiku | 꿈 종목은 차트 우선, 그 외는 5년 연간 재무 검증 |
| 실적왕 | Claude Haiku | PEG 균형 + 10배 비전·경영진 평가 |

> Claude Code 구독 토큰 사용 (별도 API 크레딧 불필요) — `agents.py`가 `claude` CLI 서브프로세스로 호출.
> `prompts.py`에는 **원칙주의자·감독·류·알렉스** 페르소나도 정의돼 있으나 현재 `AGENT_ORDER` 미포함(비활성).

---

## 분석 섹터 (20개 · 종목 68개)

**국내 KRX (11섹터 · 33종목)**
반도체 / 2차전지 / 바이오·헬스케어 / 방산·우주 / 자동차·모빌리티 / 조선·해운 / 인터넷·플랫폼 / 금융·보험 / 철강·소재 / 에너지 / 통신

**미국 US (9섹터 · 35종목)**
미국 빅테크·플랫폼 / 미국 AI·반도체 / 미국 전기차·모빌리티 / 미국 금융 / 미국 헬스케어·제약 / 미국 소비재·리테일 / 미국 에너지 / 미국 산업재·방산 / 미국 미디어·통신

> 현재 진행 시장은 `cycle_state.market`이 단일 진실원천. `_active_sectors()`가 해당 시장 섹터만 반환한다.

---

## 주요 기능

- **시장별 듀얼 사이클**: 평일 06시 국장 / 18시 미장, APScheduler CronTrigger(`Asia/Seoul`)로 분리 실행
- **섹터 토론 → 합의 추출**: 토론 후 Claude Haiku로 섹터 합의를 **JSON 강제 출력** → 3단 파싱 폴백(순수 파싱 → 정리 후 재시도 → 정규식 필드 재추출) + enum·화이트리스트 검증
- **확신도 기반 투자금**: 매수 투표 수에 따라 초기 자본(1억) 대비 비중 차등 — 4~5표 5% / 6표 8% / 7표 12% (8표 15% 티어는 7봇 체제에선 도달 불가)
- **보유 종목 점검**: 각 메인 사이클 완료 직후 해당 시장 포지션만 홀드 vs 매도 재투표 (매도 과반 시 자동 청산)
- **손절**: 보유 종목 −20%(`STOP_LOSS_PCT`) 도달 시 자동 손절 — 각 메인 사이클 완료 직후 해당 시장만 체크
- **워치리스트**: 텔레그램 채널(`.env`의 `TELEGRAM_GROUPS`)에서 언급 종목 감지 → 오늘 미분석 신규 종목 즉석 분석
- **블로그 학습**: 네이버 블로그 크롤링 → 과거 유사 가격 구간의 블로거 판단을 Anthropic API(Haiku)로 추출·캐싱 후 프롬프트에 참조
- **멤버 분석**: 텔레그램 방 발화자 투자 스타일 자동 분석 → 봇 추가 후보 발굴
- **알림**: ntfy.sh 푸시 + Gmail SMTP (매수/매도 체결, 합의 결과, 토큰 소진/복구 등)
- **토큰 운영**: Claude CLI 사용량(`usage`·`total_cost_usd`)을 일별 DB 적재, 소진 감지 시 30분 간격 자동 재시도·복구 알림
- **웹 대시보드**: 실시간 채팅(폴링), 포트폴리오, 섹터 합의, 토큰 사용량, 방문 통계(봇 UA·owner 본인 제외), 후원·피드백

---

## 기술 스택

- **Backend**: Python · FastAPI 0.115.0 · Uvicorn 0.30.6 · APScheduler 3.10.4 · httpx 0.27.2 · python-dotenv 1.0.1
- **AI/LLM**: anthropic 0.34.0 (블로그 신호 추출 + Claude CLI 경로) · openai 1.47.0 · google-generativeai 0.8.1
  - 현재 활성 봇은 전부 Claude. `agents.py`에 openai·gemini provider 분기가 있으나 활성 프로필 없음(미사용).
- **데이터 수집**: yfinance 1.4.1 · tradingview-ta · pykrx · feedparser 6.0.11 · telethon
- **DB**: SQLite (`investchat.db`, 약 20개 테이블)
- **Frontend**: Vanilla HTML/CSS/JS (`static/index.html`, fetch 폴링 — 프레임워크 없음)
- **배포**: launchd 데몬(앱 + cloudflared) + Cloudflare Tunnel, Mac mini 상시 운영

---

## 설치

```bash
git clone <repo>
cd InvestChatBots
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 환경 변수 설정 (`.env`)

```env
# AI (Claude Code 구독 토큰 사용 시 ANTHROPIC_API_KEY는 블로그 추출에만 필요)
ANTHROPIC_API_KEY=
OPENAI_API_KEY=          # (선택, 현재 미사용)
GEMINI_API_KEY=          # (선택, 현재 미사용)

# Telegram (선택 — 투자방 메시지를 봇 입력에 사용)
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_GROUPS=t.me/group1,-1001234567890   # 쉼표로 여러 방
TELEGRAM_SESSION=telegram_session

# 블로그 (쉼표로 여러 개)
BLOG_URLS=https://blog.naver.com/ranto28

# 알림
NTFY_TOPIC=<your-topic>
NOTIFY_EMAIL=            # (선택) Gmail 발송 주소
SMTP_PASSWORD=           # (선택) Gmail 앱 비밀번호

# 권한
OWNER_TOKEN=             # 미설정 시 부팅마다 임시 토큰 생성(로그 출력)
ADMIN_PASSWORD=          # (선택) /admin 로그인용
```

### 텔레그램 세션 초기화

```bash
python setup_telegram.py
```

### 블로그 초기 크롤링 (최초 1회)

```bash
python setup_blog.py
```

---

## 실행

```bash
# start.sh (포트 8001, 127.0.0.1)
./start.sh

# 또는 직접
source venv/bin/activate
python -m uvicorn main:app --host 127.0.0.1 --port 8001
```

웹 UI: `http://localhost:8001`
운영 환경은 launchd(`com.investchatbots.app` / `com.investchatbots.cloudflared`)로 자동 기동되며 Cloudflare Tunnel을 통해 외부 도메인으로 노출된다.

---

## 프로젝트 구조

```
main.py              # FastAPI 서버, API 엔드포인트(~60개), owner 토큰 인증, 대화 루프
orchestrator.py      # 메인 루프, 섹터·종목 분석, 투표·매매, 합의 추출, 워치리스트
scheduler.py         # APScheduler — 06시 국장 / 18시 미장 메인, 12·00시 워치리스트
prompts.py           # 봇 프로필(AGENT_PROFILES) 및 프롬프트 빌더
agents.py            # Claude CLI subprocess 호출, 재시도·토큰 소진 처리
fetchers.py          # TradingView TA · yfinance · pykrx · 시황/뉴스 수집
telegram_fetcher.py  # 텔레그램 채널 메시지 수집 (telethon)
naver_board_fetcher.py # 네이버 종목 오픈톡 수집
blog_fetcher.py      # 네이버 블로그 크롤링 + Anthropic API 신호 추출
member_analyzer.py   # 텔레그램 멤버 투자 스타일 분석
discovery.py         # pykrx 급등락·거래량 급증 종목 탐지
db.py                # SQLite 데이터 레이어
notifier.py          # ntfy.sh 푸시 + Gmail SMTP 알림
static/index.html    # 웹 UI (채팅, 포트폴리오, 섹터 합의, 통계)
static/admin.html    # 관리자 페이지
CLAUDE.md            # Claude Code 운영 규칙 (절대 규칙 포함)
```

---

## 운영 규칙

`CLAUDE.md` 참조 — 서버 재시작 절차, `cycle_state` 리셋 금지, 시장별 듀얼 사이클 원칙, API 비용 발생 작업 통제 등 핵심 규칙 명시.

---

## 라이선스

Private
