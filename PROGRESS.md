# PROGRESS — InvestChatBots (살아있는 진행상황)

> **매 세션 프로토콜:** 시작 시 이 파일을 먼저 읽고, 끝에 갱신한다.
> (CLAUDE.md = *고정 지침·가드레일* / 이 파일 = *매 세션 바뀌는 진행상황·다음 할 일*)
> `claude -p` 는 스테이트리스라, 이 파일이 세션 간 기억이다.
> ⚠️ **ICB는 라이브 사이트다.** 코드 변경 전 Think Before Coding + Surgical Changes 준수(CLAUDE.md §A).
> updated: 2026-08-07 새벽 (**#116~#121 전부 머지 + 전 깃 히스토리 투자자 이름 스크럽(재작성) + 플래그 활성화·재배포 완료.** 아침 05:40~06:20 새 파이프라인 첫 실주행 — 모든 매수는 /owner/approvals 결재로. 상세 세션 로그.)

## 🚧 다음 세션 — S/Q 재설계 로드맵 (2026-08-05 확정)
> ② 큐레이션 흐름 **전부 구현 완료**. 다음은 활성화(아래 ⚠️) + ③~⑥.
- **① S 시드 = 사람 큐레이션** ✅ 구조 완성. 역할분담: **발굴·조사=Claude(웹서치)** / **승인=사용자(/owner/seeds)** / **평가·매매=라이브 S봇**.
- **② 6시 큐레이션 흐름 — 전부 ✅ (로직 완성, 활성화 대기)**:
  - a. ✅ 병목 시드 **DB 테이블화**(`bottleneck_seed`). db 헬퍼 add/get/decide/backfill. (#110 머지)
  - b. ✅ `source_bottleneck`이 **DB approved 시드** 읽음. 빈 테이블은 하드코딩 14시드 approved 백필. (#110 머지)
  - c. ✅ **Admin 승인 UI**: `GET /owner/seeds` + 오너 API `/api/bottleneck/seeds`·`/decide`·`/add`. (#110 머지)
  - d. ✅ **6시 웹서치 에이전트**(브랜치 `feat/bottleneck-curation`): `aifund.run_bottleneck_curation()` = Claude 웹서치로 무명 상류 초크포인트 조사 → pending. `_call_claude_cli(allowed_tools=['WebSearch'])`. scheduler 05:50 mon-fri(06 대형주 전에 pending 준비). 게이트 `BOTTLENECK_CURATION_ENABLED`(기본 off). 수동트리거 `POST /api/bottleneck/curate`. **결정: launchd 아니라 in-app 스케줄러**(서버가 Mac mini 로컬 DB 접근 이미 있음 → 더 단순). **비용=별도 크레딧 아님, 구독 토큰 버킷**(하루 1콜). E2E검증(AXTI·KLIC·MOD 발굴).
  - e. ✅ **S 피드 "결재 올림" + ntfy**: `submit_bottleneck_candidates()`가 pending+S내레이션+오너ntfy(링크=`/owner/seeds`). (#110 머지)
  - **⚠️ 남은 활성화**: `feat/bottleneck-curation` 머지 → `.env`에 `BOTTLENECK_CURATION_ENABLED=true`(원하면 `SITE_URL`도) → §1 절차로 재시작. 안 켜면 05:50 잡 no-op.
- **③ S 렌즈 추가 심화**: 오늘 페르소나에 "저마진·적자여도 논지로, 시총vsTAM·희석 본다" 한 줄 넣음(라이브). GitHub `W-Y-P/공급망 병목-aleabitoreddit-skill`의 SKILL.md 프레임으로 더 정교화 가능(선택).
- **④ 시드 정비**: 현 14개에 AVGO·ANET·MRVL(유명 '참치') 섞임 → 무명 상류로 교체. **이제 도구 완비**: 6시 에이전트가 매일 무명 상류 후보를 pending에 올리고(②d), `/owner/seeds`에서 참치 시드 반려·신규 승인. 남은 건 운영(며칠 결재하며 참치 정리).
- **⑤ 거시자문 봇 = 비투표 매크로 자문**(트레이딩봇 X). blog_posts(보유 277편) 원문 보유. `병목자문` 패턴처럼 AGENT_PROFILES 자문 봇으로 — "거시적으로 지금 사면 안 된다" 시장레벨 경고. (KR/서사형이라 트레이딩 룰로는 안 맞음.)
- **⑥ 유니버스 큐레이션 일반화**: 섹터·시드 다 "사람이 정비→봇이 굴림". 오늘 섹터 잡탕 1건 고침(아래), V·MA 정합은 미결.

> updated(원): 2026-08-02 (🎯 컷오버 완료 — AI펀드 자동 가동. committee 은퇴, fund 스케줄 라이브. #53·#89 닫힘, #98 활성화)

## 🧪 지금 방향 (2026-07-27~ US-only 실험)
- **국장(KR) 사이클 임시 off** (`orchestrator.KR_CYCLE_ENABLED=False`) — US-only로 전략 포텐셜 관찰. 재개는 True+재시작.
- 양 계좌 시드 통일 **1억**: 장기=확신도 티어 500/1000/1500만, 트레이딩=플랫 500만·MAX 20.
- US 섹터 9→17 확장(병목·성장·꿈주). 공급망 병목 "공급망 병목" 이론 접목: 규칙엔진 유니버스 병목 시드 + 미장 라운드 비투표 자문 코멘트.
- KR 거래내역·보유는 제거(백업 `bak-pre-usonly`), US-only로 재베이스. **미장 마스터하면 국장 재개 + 코인/QQQ/SCHD 포트폴리오 전략 도입 예정.**
- US-only 현재 성적(시드 각 1억): 장기(위원회) **+4.69%** / 트레이딩(규칙엔진) **+1.08%**.

## 지금 방향
- 핵심 질문(이번 사이클): **"관전만으로 사람이 다시 오는가?"** — 재방문/리텐션 증명이 최우선.
- 가상 포트폴리오 단계. EverydayNews(아침 메일)가 유입구.
- 상세 OKR·오케 코멘트: 워크스페이스 `okr/investchatbots.md`(권위본) 참조.

## 🎯 현재 = AI펀드 자동 가동 중 (컷오버 완료 2026-08-02)
- **라이브 `/` = fund**(committee 은퇴). 스케줄러: **06 대형주**(run_largecap_cycle) · **0·12·18 발굴**(run_discovery_cycle), 평일 KST. `NEW_DESK_ENABLED=true`(.env 게이트, 끄면 no-op·복구).
- **committee 보존**: 코드·DB·conversation loop 그대로(cycle_rest idle). 스케줄러 잡만 제거 → 필요시 scheduler.py 복구로 재가동.
- **계좌**: 대형주(id7) 보유 TSMC·엔비디아(각 400만)+현금 42.7M+실현 702,684 / 발굴주(id8) 시드 5000만·0보유. 사이징 대형주 500만(10%·max10)·발굴주 250만(5%·max20).
- **⚠️ 다음(가장 중요) = 첫 자동 사이클 모니터링**: 오늘 00:00 발굴 / 내일 06:00 대형주가 첫 실주행. 봇 실매매·토큰 시작. §1 재시작 절차 준수, cycle_state 무접촉. 발굴 매수율 낮으면(랜덤풀에 P/W/S 보수적) 문턱 튜닝 검토.
- **완료 이슈**: #53(데스크 빌드)·#89(관전 UX A~G)·#98(컷오버 배선·활성화). 설계 `docs/AIFUND_PIVOT.md`.
- [ ] 채팅 = **관전 피드로 재개념화**(안전장치 ①큐 폐기, ②면책·③권유금지만). 유저 질문 라이트 유지.
- [ ] 리텐션 계측 정확화 (재방문율 신호가 아직 부정확 — 실방문/재방문 정의 재확인)
- [ ] (수익화 뒤 레이어) 구독 → EverydayNews 발송목록 + 채팅 입력권한 제한

## 안정 완료된 것 (요약)
- 국장/미장 이원화 사이클(06시 KRX / 18시 US), 보유점검·손절·워치리스트 스케줄.
- 7봇 위원회 토론 → 과반 매매, 가상계좌 포트폴리오.
- 최근 안정화: FD 누수 방지(urlopen close + OHLC 캐시), fetch_one MultiIndex 크래시 수정, 규칙봇 결정 대화체.
- 릴스 자동 파이프라인 삭제 → 당분간 수동 업로드(파급력 우선).

## 최근 세션 로그 (최신이 위)
- **2026-08-06~07 (대량 세션 — 사람-in-the-loop 매수 + 봇 개성·신중함 재설계)**
  - **머지됨(main)**: #111(6시 큐레이션 ②d)·#112(백필가드)·#113(거시자문 Z + 이니셜 ROT13)·#114(매수 결재). ②d는 **라이브 활성**(05:50, `BOTTLENECK_CURATION_ENABLED=true`, 시드 14 approved 백필). 매수 결재 코드는 main에 있으나 **라이브 미배포·플래그 미설정**.
  - **전부 머지됨(2026-08-07 새벽, #116→#121 순)**. 같은 새벽에 **전 히스토리 재작성**: 커밋 메시지+blob에서 투자자 이름 완전 제거(문서·json 전부 / 코드는 CI 검증된 안전 치환만, 추세돌파·평균회귀 한글 표시는 코드 내 유지·영어 식별자 유지), mer_blog 원문파일 히스토리째 삭제, PR/이슈 메타데이터 12건 정리. 동작 무변경(214 테스트 통과). GitHub 서버 잔존 객체는 Support 요청 필요(초안 전달).
    - **#116** 전략가 실명 스크럽(레포 public — 실명→역할어. 과거 커밋 메시지 히스토리는 A안=보존).
    - **#117** 매수 결재 '상사 보고' 상세화(봇별 역할라벨+근거 본문) + **발굴 S 트랙 독립**(A픽=P/W/S 위원회, S픽=S 독자 → 결재).
    - **#118** Z(거시) 브리핑 — 05:40 블로그 새글 크롤+지수 → 오너 보고, `/owner/approvals` 상단 카드. `MACRO_BRIEFING_ENABLED`.
    - **#119** 관찰 단계(신중한 매수) — 매수판단→관찰 등록→사이클마다 자기논지 재점검→최소 2회 후 확신 시 결재(관찰 서사). `OBSERVATION_REQUIRED`.
    - **#120** Q 지수 스윙 계좌(id9·1,000만) — QQQ/SPY+1배 인버스(PSQ/SH), 200MA±20일 돌파/50MA 청산 미러, 06:10 자동(무LLM·결재 없음). `Q_INDEX_ENABLED`. +init_db 신규DB ALTER 누락 잠복버그 수정.
    - **#121** 자문단 — M 발굴 코멘트(브리핑 재사용·토큰0) + **R 리스크 오피서**(전천후 렌즈, 집중도·상관·시나리오·현금, 06:20 비투표). `RISK_OFFICER_ENABLED`. 공개레터 E.
  - **확정 정책**: 모든 매수=오너 결재(승인=봇 판단시점 가격 체결, 만료없음, 매도는 자동) / M=veto 아닌 보고 / 대형주 게이트=Q veto만 / 시드결재(/owner/seeds)≠매수결재(/owner/approvals).
  - **로스터(내부→공개 ROT13)**: A→N 리서치 · P→C 성장주 · W→J 가치 · H→U 실적 · S→F 병목(독자트랙) · Q→D 타이밍+지수계좌 · M→Z 거시브리핑 · R→E 리스크. 위원회 한글명은 비치환.
  - **⚠️ 다음 = 머지 → .env 플래그 4종(`BUY_APPROVAL_REQUIRED`·`MACRO_BRIEFING_ENABLED`·`OBSERVATION_REQUIRED`·`Q_INDEX_ENABLED`·선택 `RISK_OFFICER_ENABLED`) → §1 재시작.** 이후 관전: 결재함 UX·관찰 서사 품질·Q지수 첫 신호.
  - 보류: 추세돌파 별도 봇(Q와 중복이라 안 함), M veto(보고로 대체), 대형주 관찰 단계(매일 재분석 구조라 미적용).
- **2026-08-05 (밤 3 — ②d 활성화 배포 + 백필가드 픽스)** — 로드맵 ② 라이브 가동.
  - **#111 머지** → main(c13125b). **배포**: `launchctl kickstart -k com.investchatbots.app`(launchd 관리)로 §1 재시작(phase=cycle_rest 유지). 새 PID·엔드포인트 라이브(/owner/seeds 307·/api/bottleneck/seeds 403).
  - **활성화**: `.env`에 `BOTTLENECK_CURATION_ENABLED=true` + `SITE_URL=https://investchatbots.com`(ntfy 절대링크). → **내일 05:50부터 6시 큐레이션 실가동**(하루 1콜 구독토큰).
  - **⚠️ 라이브 위험 발견·해소**: 백필은 `_bottleneck_seed()` 첫 호출 때만인데, 큐레이션이 그 전에 pending 넣으면 백필 영구 skip → **S 워치리스트 소실**. (1)라이브 DB에 14 approved 시드 **수동 백필로 즉시 보호** (2)픽스 `fix/curation-backfill-guard`: 큐레이션이 exclude 계산 전 `_bottleneck_seed()` 먼저 호출(향후 재발 방지). PR 대기.
  - **관전 팁**: 즉시 첫 후보 원하면 오너쿠키로 `POST /api/bottleneck/curate`(토큰 소모+폰 ntfy). 결재는 `/owner/seeds`.
- **2026-08-05 (밤 2 — 6시 큐레이션 에이전트 ②d + #108/#109/#110 머지)** — 로드맵 ② 완성.
  - **#108·#109 머지**(merge-commit, 스택 공유커밋 보존) → main. **#110 머지**(②a/②b/②c/②e) → main.
  - **②d(브랜치 `feat/bottleneck-curation`, 커밋 d935969)**: `run_bottleneck_curation()` — Claude 웹서치로 무명 상류 초크포인트 조사(하루 1콜) → pending 결재 큐. `_call_claude_cli`에 `allowed_tools` 추가(하위호환)로 헤드리스 웹서치. scheduler 05:50 mon-fri. 게이트 `BOTTLENECK_CURATION_ENABLED`(기본 off). 수동 `POST /api/bottleneck/curate`.
  - **설계 결정**: launchd 대신 **in-app 스케줄러**(서버 이미 Mac mini 로컬 DB 접근 → 단순). 비용은 **크레딧 아닌 구독 토큰 버킷**(agents가 ANTHROPIC_API_KEY 제거하고 CLI 구독토큰 사용) — 채팅·봇과 공유하는 그 5시간 버킷. 하루 1콜로 최소화.
  - **검증**: 헤드리스 웹서치 실동작 확인(`--allowedTools WebSearch` → Bloomberg 헤드라인). 실제 큐레이션 1콜 E2E(임시 DB) — **AXTI(InP 기판 공급난)·KLIC(HBM4 TCB 본딩)·MOD(액체냉각 백로그)** 발굴, 제외·pending·S결재·ntfy 정상. 신규 테스트 4건, 전체 189통과(사전 실패 11 무관).
  - **⚠️ 활성화 대기**: 이 브랜치 머지 → `.env` `BOTTLENECK_CURATION_ENABLED=true` → §1 재시작.
- **2026-08-05 (밤 — S 시드 큐레이션 인프라 ②a/②b/②c/②e)** — 브랜치 `feat/bottleneck-seed-db` → PR #110 머지. 커밋 3개(#98).
  - **②a/②b(커밋 059a2bb)**: 병목 시드 `strategy_private` 하드코딩 → DB `bottleneck_seed` 테이블(ticker·rationale·status·source). `source_bottleneck`이 approved만 소싱. 빈 테이블은 최초 1회 하드코딩 14시드 approved 백필 → **라이브 무접촉 보존**. `trading_engine`의 `US_BOTTLENECK_SEED` 직접참조(committee 유니버스)는 무변경.
  - **②c/②e(커밋 85bce59)**: `GET /owner/seeds`(폰 결재 페이지) + 오너 API 3종(목록/decide/add). `aifund.submit_bottleneck_candidates()` = pending 등록+S '결재 올림' 내레이션+오너 ntfy(②d가 부를 훅). ⚠️ `/owner/seeds`를 동적 `/owner/{token}`보다 먼저 선언(선점).
  - **검증**: 신규 테스트 6건 통과·전체 회귀 0(기존 실패 11건은 사전 존재=옛 로스터/채팅라우팅 드리프트, stash로 확인). TestClient E2E로 엔드포인트 구동(anon 403·오너 결재·400·페이지 렌더 200).
  - **⚠️ 남은 것 = ②d(6시 에이전트)** — 매일 API비용+launchd cron이라 가드레일 §3상 **명시 승인 후** 착수. submit 훅은 준비됨.
  - **⚠️ git**: 이 브랜치가 #108·#109(미머지) 위에 있음 → #108·#109 먼저 머지하면 이 PR diff가 깔끔. 아직 push 안 함.
- **2026-08-05 (대량 세션 — 라이브 버그 fix 9건 + S/Q 재설계 착수)**
  - **머지 완료(#101~107, origin/main)**: dotenv 로딩순서(NEW_DESK_ENABLED가 import시점 False로 굳던 컷오버 게이트 버그) · 관전피드 H봇 색누락 · 매수이유 모달 초보친화(A종목소개+friendlyReason, 원본 reasoning은 ranks 크레딧용 보존) · **yfinance 병목완화**(이름 영속캐시 db.stock_names + .info 3→2회 fail-fast + `/api/largecap/run` in-process 트리거) · **유저채팅 봇을 committee→P/W/H/S**(ROOT_IS_FUND 게이트, +desk='fund' 태그로 fund피드 노출) · **벤치마크 스냅샷 배선**(07-31 멈춤 — committee 스케줄러 은퇴 때 같이 빠짐 → scheduler에 잡 추가, SPY baseline 백필).
  - **미머지 PR**: #108(S 재소싱 버그) · #109(Q veto). 둘 다 CI 그린, 사용자 `!`로 머지 예정.
  - **인프라 원인 규명**: 34분 대형주 사이클이 **OOM으로 kill**(Mac mini 8GB, 스왑 87%). → 재개는 in-process(`/api/largecap/run`)로 메모리 안전하게. yfinance 병목이 사이클 길이 늘려 압박 가중.
  - **✅ S 재소싱 버그 수정(#108, 라이브)**: `source_bottleneck`이 영구 thesis캐시로 시드 제외 → 14 시드 소진 후 S 영구 휴면이던 것. '오늘분석+보유'만 제외하는 상시 워치리스트로. 소싱 0→4 복귀.
  - **✅ 섹터 정비(라이브, strategy_private)**: `우주·양자·로보틱스` 잡탕(RKLB우주+IONQ양자+ISRG의료로봇) 해체 → ISRG를 헬스케어로, `우주·항공`(RKLB·LUNR·RDW)·`양자컴퓨팅`(IONQ·RGTI·QBTS) 분리. **17→18섹터·63종목.** (나머지 16섹터는 응집 OK. V금융/MA핀테크 split은 미결.)
  - **✅ S 렌즈 보강(라이브, strategy_private)**: S 페르소나에 밸류렌즈 추가 — 병목픽은 저마진·적자여도 PER/ROE로 거르지 말고 시총vsTAM·희석으로. (S 페르소나는 원래 병목논지 중심이라 렌즈는 문제 아니었고, 재소싱이 진짜 원인이었음.)
  - **✅ Q veto 전환(#109, 라이브)**: 대형주가 P/W/H 승인해도 Q 진입신호(돌파/눌림) 하드게이트에 막혀 매수 0 + 청산은 Q단독 → 계좌 마름(비대칭). Q를 veto(약세장·200일선아래만 차단, 과매수는 오판위험이라 제외)로. 청산도 veto·추세매수는 M(50MA)로 덜 eager.
  - **진단 데이터**: 봇 매수율 P19%·H21%·**W3%·S2.5%**(OR게이트 사실상 P·H만 방아쇠). 관망은 데이터부족 아닌 실제 재무 깐깐거절. 대형주 전량 Q 청산돼 보유 0(→Q veto로 해결).
  - **🔬 공급망 병목(S 원형) 심층연구**: 기사+과거글 웹리서치. 핵심 — "참치(엔비디아) 말고 깻잎(무명 상류)", 논문·특허·capex로 초크포인트 발굴, **P/E 안 씀**(적자多), 시총vsTAM·선행PSR·인수가치·희석으로 판단, 초집중·고변동감내. **결론: 완전복제 불가**(봇이 그 리서치 못 함 + 반사성으로 픽 복제=펌핑끝물 매수) → "프레임만 취하고 발굴은 사람이 큐레이션". 상세 로드맵 상단 "🚧 다음 세션".
  - **⚠️ 배포 상태**: 라이브 서버는 `feat/q-veto-largecap` 워킹카피로 재시작됨(= main머지분 + #108재소싱 + #109Qveto + strategy_private 섹터/렌즈 전부 포함). #108·#109는 main 머지만 남음. main정렬은 머지 후 checkout+재시작.

- **2026-08-01 (저녁·관전 UX 재설계 결정 + fund 폴리시)** — 사용자와 대량 이터레이션. **결정 확정(빌드는 단계별 진행 예정)**.
  - **확정 결정**:
    - **닉네임 = 단일 알파벳 A·P·W·H·S·Q**(동물명 폐기). 코드·내레이션·UI 통일로 닉네임 불일치 버그 제거. ✅완료
    - **직위(職位) 시스템** = Members에서 역할 대신 직위. **올빼미(A)=신입 고정**, 나머지 한국 직급 사다리, **각 봇 '픽 성과'(그 봇이 매수 찬성한 종목들 평균 수익률)로 승진**(공유 데스크 유지·계좌 안 쪼갬). Q=아무 직위나. ⏳
    - **섹터 토론 → 종목 분석 2단계**(대형주): ①A가 "○○ 섹터 어때요?" → P/W/H 섹터 의견(형식 자유) → **리서치 보드에 섹터합의** ②종목별로 A가 TradingView 데이터 채팅 투척 → P/W/H 판단. **섹터=대형주 유니버스에 섹터 태그 도입**. ⏳
    - **종목 판단은 무조건 `[결정] {관망/매수/매도} | 이유: …` 포맷**(committee 포맷). 섹터 언급은 형식 자유. ⏳
    - **출근(초록) = 현재 단계 활성 봇만**. 06 대형주엔 S 꺼짐, Q는 P/W/H 핸드오프 후 켜짐. **초록 테두리 = 멤버 박스 전체(닉+직위+모델 감싸는 사각형)**. ⏳
    - **루트 컷오버**: `/` = fund, **committee 완전 제거**(라우트/토글 삭제, DB는 보존). ⚠️ fund 스케줄러 미배선이라 컷오버 후에도 자동매매는 Phase 2 배선 전까진 안 돎. ⏳
    - **깃 컨벤션 완화**: Epic이 수십개 안고가지 말고 **응집 묶음(1a~1d)=이슈 1개 + PR 몇개**. 단발=1이슈1PR. CONVENTION.md 반영 필요. ⏳
  - **이번 세션 완료(PR #84·#85 머지 + #86 브랜치 `fix/fund-price-emoji` 미머지)**: 이모지 제거 · **US 현재가 TradingView 우선+yfinance 폴백**(현재가 뜸) · Members 라벨 모델+역할(역할↑/모델↓) · **전략가 이름 전부 제거**(가치·성장주·텐배거·추세돌파·평균회귀→중립) · 태그라인 "인생 졸업을 향해" · 'A 리서치'→'리서치 보드' · 알파벳 닉네임 · **fund 청산 실현손익 표시**(payload에 pnl 필드 추가) · Q strat 감지 버그(개명 후 항상 B폴백) 수정 · 벤치마크 SPY·코스피·코스닥 · 현재섹터 box.
  - **Q 계좌 정리**: 레거시 `run_q_desk`(계좌 id6) 50개 완전 삭제. 백업 `bak-pre-qarchive-20260801-113755`.
  - **06 대형주 사이클 1회 테스트**(NEW_DESK_ENABLED 임시 패치): A소싱→P/W/H분석→관심종목 5(GOOGL·AMZN·NVDA·AVGO·TSM, **전부 H만 승인** — P/W는 yfinance 리밋으로 재무데이터 없어 관망)→Q집행(신규매수0, **아마존 청산 +17.57%**). **파이프라인 작동 확인. 단 yfinance 리밋이 재무·속도 반복 병목**(analyze_candidate는 아직 yfinance `info` 의존).
  - **S 확정**: 자체 소싱(US_BOTTLENECK_SEED) + OR게이트 자기픽 판단 유지(현 코드 그대로). 이유: 소싱이 S의 엣지, P/W 밸류렌즈에 안 죽게 OR게이트.
  - **✅ A단계(PR #87)**: `ranks.py` 직위(職位) 시스템 — 찬성봇 픽 평균 수익률로 한국 직급 사다리(신입→주임→대리→과장→차장→부장→이사→상무). A=신입 고정. 대형주 매수 reasoning에 '관심 P,W,H' 기록해 크레딧 보존. Members에 role→rank. **라이브 배포**(H=과장).
  - **✅ B단계(PR #87)**: 봇별 출근 판정(각 봇 최신 fund 내레이션 퇴근여부) → 06엔 S 자동off·Q 핸드오프후on. 초록 테두리를 **멤버 박스 전체**(닉+직위+모델)로. **라이브 배포**.
  - **✅ 피드 정리(D 일부 선처리)**: 옛 테스트 사이클 fund 메시지 46개 삭제(백업 `bak-pre-fundfeedclear-20260802-014312`). committee(desk NULL) 73809개 무관.
  - **✅ C단계(PR #91, C1~C3 통합)**: 대형주 섹터 토론 흐름 — **17섹터**(committee US 정합·59종목) · 2단계(섹터별 A질문→P/W/H 섹터의견→종목 `[결정] X|이유:` OR게이트, **매일 전 섹터 재분석**·캐시폐기) · 섹터합의 리서치보드 카드 · **재무 last-good 캐시**(리밋 폴백 → P/W/H 관망만 나오던 문제 해소). 미머지·라이브 재무검증은 API리밋으로 다음 실사이클 이월.
  - **✅ D단계(PR #92)**: 관전 피드 끊김 수정 — `trim_at_sentence`(350자 후 문장컷)가 응답 끝 `[결정]` 줄을 잘라 메시지가 결론 전 끊기고 관망 오폴백되던 버그. `call_agent(trim=)` 추가(기본 True, committee 무변경), fund 종목분석만 trim=False.
  - **✅ E단계(PR #94)**: 현재 섹터 박스 = 대형주 데스크 진행형(섹터명·N/17·종목칩, 오늘 섹터합의 리포트에서 도출). committee 스타일 복원.
  - **✅ F단계(PR #95)**: 루트 컷오버 게이트 `ROOT_IS_FUND`. 기본 off=committee 무변경(완전 되돌림), on=`/`=fund·토글숨김. **committee 라우트/스케줄러/DB 보존(라우팅만)**.
  - **✅ G단계(PR #93)**: 컨벤션 — Epic=응집 묶음, 테마 바뀌면 새 Epic. CONVENTION.md·CLAUDE.md §6.
  - **🎯 Epic #89 전 단계(A~G) 완료.** 머지 대기: E(#94)·F(#95). D·G는 머지됨.
  - **⚠️⚠️ 실제 컷오버(다음 큰 단계, 별도 승인)**: ①스케줄러 배선(06 대형주 A/P/W/H/Q · 12/18/24 발굴 A/P/W/S, 일봉 종가) ②`NEW_DESK_ENABLED=True` ③`ROOT_IS_FUND=true` + 재시작. **셋을 함께** 해야 `/`가 빈 피드 안 됨. 실매매·토큰 시작이라 §1·§4 가드레일. committee 은퇴는 이때.(A가 "○○섹터 어때요?"→P/W/H 섹터의견→리서치보드 섹터합의→종목별 A가 TradingView 투척→P/W/H 판단)+**종목판단 무조건 `[결정] {관망/매수/매도} | 이유:` 포맷**+대형주 유니버스 섹터태그 D)채팅 끊김 조사·수정 E)섹터 박스 데스크 진행형 배선 F)루트 컷오버(`/`=fund, committee 제거) G)CONVENTION.md(이슈=응집묶음). **⚠️ C에서 P/W 재무판단이 yfinance `info` 의존→리밋시 관망만 나옴, 재무 소스도 손볼 것.**
- **2026-08-01 (오후·통일 데스크 재설계)** — 4봇 경쟁/v1·v2 병존 구조를 **대형주/발굴주 2데스크로 통일, v1(committee) 대체**로 대전환. 설계 권위본 `docs/AIFUND_PIVOT.md` 갈아엎음.
  - **로스터**: A(발굴·리포트) · P/W/S/**H**(펀더, H=구 committee '실적왕' 개명·sonnet) · Q(대형주 타이밍 엔진). 4봇 경쟁·1억×4 폐기.
  - **구조**: 대형주(P/W/H OR게이트 → 관심종목 캐시 → **Q가 B+M 진입타이밍**·익절손절 + P/W/H 강한 펀더매도 청산) / 발굴주(A·S 발굴 → P/W/S OR게이트 즉시매수 → 논지청산). Q는 대형주 타이밍만(계좌 없음). "장기 메인 + 퀀트 보조지표" 철학.
  - **구현 1a~1e**(PR #76~80): 계좌·largecap_watch 캐시 · H페르소나·데스크로스터 · run_new_desk_cycle 4슬롯 재배선(run_largecap_select/execute·run_discovery_desk/review·source_largecap) · UI(2계좌·Members 모델만·나 유지) · Q 설명형 내레이션(haiku)+봇 핸드오프.
  - **머니**(백업 후): 대형주/발굴주 5000/5000 시드 + committee 장기에서 '실적왕+역추세봇 둘다 매수'한 **3종목(TSMC·엔비디아·아마존) 대형주 승계**(Option B: committee 원장 무접촉 복사). 백업 `bak-pre-deskmigrate-20260801-092858`.
  - **배포**: 라이브 8001 재시작 → `/fund` 통일 데스크 반영(대형주 3종목·발굴주). committee 무접촉(cycle_rest).
  - **일정 확정**(PR #81): 06 대형주(A/P/W/H/Q, US 종가 직후 신선 일봉) · 12/18/24 발굴(A/P/W/S). 전부 일봉 종가(장중 실행 폐기). 모든 사이클 매수까지 → 볼맛.
  - **v1 70/30 재분할**: 장기 7000만/트레이딩 3000만(비율 축소 재작성, 수익률 보존 4.69%/1.11%) + 벤치마크 bot_nav 재기준. 백업 `bak-pre-7030split-20260801-071503`.
  - ⚠️ **다음 = 컷오버**(Phase 2 스케줄러 배선 + NEW_DESK_ENABLED=True + committee 은퇴). 봇 실매매·토큰 시작이라 §1·§4 신중.
- **2026-08-01** — AI펀드 **v2 UI·소싱·내레이션 완성 + 방향 전환**(브랜치 `feat/fund-mode-chrome`, PR [#71](../../pull/71) 미머지).
  - **방향 전환**: 컷오버(committee 은퇴) 폐기 → **v1(committee)·v2(펀드) 둘 다 유지**, 좌상단 `v1↔v2` 토글(owner 전용). `/fund` = index.html 셸 재사용(`window.__FUND_MODE__`), v1 `/`는 무영향.
  - **v2 관전 UI**: Members(글자만·수익률 스파크라인·출근 초록테두리) · 관전 피드(봇색·봇별 출근/퇴근·A 분석완료·매번 다른 varied 멘트, `messages.desk='fund'`로 v1 피드 분리) · 2×2 사분면 가상계좌(티커·진입·현재·투자금) · **📋 A 리서치 패널**(섹터합의 자리, 사업요약 한글화[haiku]+핵심재무, 일반/병목 태그).
  - **소싱 철학 분리**: A=일반풀 발굴+전 후보 리포트 / P·W=A리스트 분석 / **S=`US_BOTTLENECK_SEED` 자체소싱**(발굴이 엣지) / Q=전 유니버스. `run_new_desk_cycle`을 `_process_pool`로 분리.
  - **DB 추가**: `messages.desk`(v1/v2 분리) · `fund_nav`(수익률 스파크라인) · `fund_report`(A 리포트). 오프라인 테스트 다수 추가, 회귀 0(기존 실패 10건은 옛-로스터 baseline 드리프트).
  - **보류(v1용, 사용자가 나중에)**: v1 실적왕→P+W+S OR게이트 재구현 · 장기/트레이딩 7000/3000 복원.
  - ⚠️ **다음**: PR #71 머지 → 라이브 배포(committee cycle_rest 후 §1 재시작) · Phase 2 스케줄러(봇별 시간 분산=라이브 배선 체크포인트).
- **2026-07-31** — AI펀드 피봇 대규모 설계·백테스트 세션. **Epic [#53](../../issues/53)** 로 묶음.
  - **AI펀드 정체 전환**: "협업 단일펀드"→**"전략별 계좌 겨루기"**. 로스터 P/W/S(장기·LLM)+R/M/B(트레이딩·룰봇). 설계 권위본 `docs/AIFUND_PIVOT.md` 갈아엎음. 드가자 삭제.
  - **committee v1 통째 백업**: git 태그 `committee-v1` + tarball(`~/Desktop/CSB/MyApps/InvestChatBots_committee-v1_2026-07-30.tar.gz`, strategy_private·DB·env 포함). 컷오버 전 복원점.
  - **코어(PR #48)**: P/W/S 발굴 분석(관대 게이트)·데이터 패킷 심화(마진·성장·FCF·사업요약)·T 하드 스탑. 페르소나는 실제 방법론 프레임워크로 심화(private).
  - **백테스트(PR #50)**: `backtest.py`에 regime(장세)별 분해 + M(추세돌파 트렌드템플릿+돌파) + M 시장필터(SPY 200MA) + 하드스탑 옵션 + **Q(B+M 블렌드) 정식화**(자본인지 `portfolio_sim`).
  - **핵심 발견**(US 514종목 6년): B(횡보/하락 강세)+M(상승 강세) **상호보완** → **Q 블렌드 +164%/MDD-11.7%**가 각 단독 압도. **단 Q≈QQQ(+166%)** — 생존편향 감안 시 아직 시장 못 이김, 낮은 낙폭이 잠정 강점. 하드스탑은 역효과(2%사이징이 이미 방어). R(로스)는 룰봇 최약→보류.
  - **Git 컨벤션**: Epic+Task 하이브리드 채택(#52) → 최종 정리(#62): **Task=PR만(이슈 안 만듦, 에픽번호·Part of #N), 단발만 Closes.** committee v1 백업(태그 `committee-v1`+tarball).
  - **오후: 구조 4봇 경쟁으로 확정 + Phase 1 실행배관 완성.**
    - **로스터 최종**: A(소싱·리서치브리프 룰) + **P·W·S·Q 4봇 각자 1억 독립 경쟁**(총 4억). P/W/S 위원회 폐지·각자 단독. Q=B+M 룰봇(전 유니버스 자체 스캔). R·D 삭제. **1북 아님 — 봇별 계좌.** 채팅=관전피드 재개념화.
    - **A 리서치 브리프(#56)**: 재무+**분기추세**(뉴스無·중립) → P/W/S 공용.
    - **Phase 1a(#60)**: 봇 계좌 4개(P/W/S/Q=id3~6, 각 1억, `ensure_fund_accounts` lazy seed).
    - **Phase 1b(#64)**: `execute_buys`(봇별 자기 계좌 매수)·`execute_thesis_sell`(논지 청산).
    - **Phase 1c(#66)**: `run_q_desk`(전 유니버스 B+M 스캔→Q 계좌), `backtest.minervini_entry/exit` 라이브 신호.
    - **하드스탑 제거(#65)**: 백테스트상 역효과+P/W/S는 논지로 청산 → 2% 사이징이 방어. 각 봇 자기 기준 청산.
  - ⚠️ **다음 세션 = Phase 2**(파이프라인+스케줄러). 전부 `NEW_DESK_ENABLED=False` 뒤 — 라이브(committee) 무영향.
- **2026-07-27** — 대규모 세션.
  - **Git 컨벤션·자동화 도입**: 이슈→`type/#N`브랜치→`[Type/#N]`커밋→PR→승인머지. 자동화(pr/issue-label·pr-title-lint·CI·delete_branch_on_merge). 커밋 쪼개기 가이드. allow리스트+`gh pr merge`는 ask. **머지는 항상 사용자 승인**·PR코멘트는 PR에서 응답.
  - **전략 비공개화**: 봇 페르소나 프롬프트·지표/튜닝 파라미터 → `strategy_private.py`(gitignore) + `*_example`(플레이스홀더 폴백). 앞으로 튜닝은 GitHub 미노출.
  - **자본 재편**: 트레이딩 2,500만→1억(플랫 500만/MAX20), 장기 7,500만→1억(티어 500/1000/1500만). 과거·현재 포지션 소급 재작성(백업).
  - **US-only 피벗**: KR 데이터 제거·재베이스, KR 사이클 off 토글(§4에 명시), US 섹터 9→17, 공급망 병목 병목 이론(발굴 시드 + 비투표 자문 봇).
- **2026-07-17** — (Agent Orchestration 롤아웃) PROGRESS.md 도입 + CLAUDE.md 상단에 세션 프로토콜 추가. 코드 변경 없음(docs).
