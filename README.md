# InvestChatBots

AI 투자 토론방 — 8개의 AI 봇이 매일 13개 섹터·39개 종목을 분석하고, 과반수 동의 시 가상 포트폴리오에 자동 매수하는 시스템.

---

## 개요

매일 오전 6시, 8개의 AI 봇이 섹터 토론을 거쳐 종목별 매수/관망/매도를 투표한다. 5표 이상이면 실제 가상 포트폴리오에 매수가 체결되고 알림이 발송된다. 메인 분석이 끝난 이후엔 텔레그램 채널에서 언급된 새 종목들을 2시간마다 추가 분석한다.

---

## 시스템 구조

```
메인 사이클 (평일 오전 6시)
  → 13개 섹터 × 섹터 토론 1라운드
  → 각 섹터 종목 분석 (8봇 투표)
  → 5표 이상 매수 → 가상 포트폴리오 체결 + ntfy 알림
  → 완료 시 cycle_rest 진입

워치리스트 (매일 8~24~4시, 2시간 간격)
  → 텔레그램 채널 스캔 → 오늘 미분석 신규 종목 감지
  → 봇들 즉석 1라운드 토론 → 5표 이상 시 매수
```

---

## AI 봇 구성 (8봇)

| 봇 | 모델 | 역할 |
|---|---|---|
| 드가자 | Claude Haiku | 낙관론자 — 매수 기회 우선 탐색 |
| INTJ | Claude Sonnet | 리스크 관리자 — 하방 시나리오 집중 |
| 퀀트중독자 | Claude Sonnet | PER·ROE·EPS 수치만으로 판단 |
| 매크로 | Claude Haiku | 금리·환율·지정학 거시경제 |
| 차트천재 | Claude Haiku | 주봉·일봉 패턴·수렴돌파 전문 |
| 원칙주의자 | Claude Haiku | 영업이익 15%↑·PER 30 이하 기준 |
| 경력직 | Claude Sonnet | 꿈 종목 + 5년 연간 재무 검증 |
| 감독 | Claude Sonnet | 10배 잠재력·비전·경영진 신뢰 |

> Claude Code 구독 토큰 사용 (API 크레딧 별도 불필요)

---

## 분석 섹터 (13개)

국내: 반도체 / 2차전지 / 바이오·헬스케어 / 방산·우주 / 자동차·모빌리티 / 조선·해운 / 인터넷·플랫폼 / 금융·보험 / 철강·소재 / 에너지 / 통신

미국: 미국 빅테크 / 미국 AI·반도체

---

## 주요 기능

- **자동 섹터 분석**: 매일 6시 13개 섹터 순차 분석, 과반수 매수 시 자동 체결
- **워치리스트**: 텔레그램 13개 채널에서 언급 종목 감지 → 추가 분석
- **손절**: 보유 종목 -20% 도달 시 9:30 자동 손절
- **알림**: ntfy.sh 푸시 알림 (매수 체결, 합의 결과, 토큰 소진 등)
- **블로그 학습**: 메르(ranto28) 블로그 전체 크롤링 → 과거 유사 구간 참조
- **멤버 분석**: 피델리티 텔레그램 방 발화자 투자 스타일 자동 분석 → 봇 추가 후보
- **매수 확신도 기반 투자금**: 5표→5%, 6표→8%, 7표→12%, 8표→15% (초기 자본 대비)

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
# Telegram
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_GROUP=<채널ID들, 쉼표 구분>
BLOG_URLS=https://blog.naver.com/ranto28

# 알림
NTFY_TOPIC=<your-topic>

# (선택) API 키 — Claude Code 토큰 사용 시 불필요
OPENAI_API_KEY=
GEMINI_API_KEY=
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
# 서버 시작 (포트 8000)
uvicorn main:app --host 0.0.0.0 --port 8000

# 또는 백그라운드
nohup uvicorn main:app --host 0.0.0.0 --port 8000 &

# 맥 잠자기 방지 (자리 비울 때)
caffeinate -d &
```

웹 UI: `http://localhost:8000`

---

## 프로젝트 구조

```
main.py              # FastAPI 서버, API 엔드포인트
orchestrator.py      # 메인 루프, 섹터·종목 분석, 워치리스트
scheduler.py         # APScheduler — 6시 메인, 2시간 워치리스트
prompts.py           # 8봇 프로필 및 프롬프트
agents.py            # Claude CLI subprocess 호출
fetchers.py          # TradingView TA, yfinance, 시황 수집
telegram_fetcher.py  # 텔레그램 채널 메시지 수집
naver_board_fetcher.py # 네이버 종목 오픈톡 수집
blog_fetcher.py      # 네이버 블로그 크롤링
member_analyzer.py   # 피델리티 멤버 투자 스타일 분석
db.py                # SQLite 데이터 레이어
notifier.py          # ntfy.sh 푸시 알림
static/index.html    # 웹 UI (채팅, 포트폴리오, 섹터 합의)
CLAUDE.md            # Claude Code 운영 규칙
```

---

## 운영 규칙

`CLAUDE.md` 참조 — 서버 재시작 절차, cycle_state 관리, 스케줄 원칙 등 핵심 규칙 명시.

---

## 라이선스

Private
