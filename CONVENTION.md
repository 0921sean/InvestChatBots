# Git / 개발 컨벤션 (솔로)

> 블록깨비 팀 컨벤션(Offroad 스타일)의 솔로 버전. **본질 = 추적성: 코드 변경이 어느 목표에서 왔는지 이슈로 이어지게.**
> 라이브 사이트 절대 규칙은 [CLAUDE.md](CLAUDE.md) B장 참조 — 컨벤션보다 우선한다.

## 작업 단위: Epic · Task · 단발 (하이브리드)

큰 다단계 작업은 **Epic 이슈 + Task PR들**로, 작은 단발 작업은 **1이슈=1PR**로.

| | 언제 | 이슈 | PR |
| --- | --- | --- | --- |
| **Epic** | 큰 테마·다단계(피봇, 실험, 리팩터 묶음) | `[Epic] #N - 주제` + **투두 체크리스트 본문**(언제든 편집) | (없음 — 아래 Task들이 채움) |
| **Task** | 에픽의 투두 하나 | (에픽이 대신) | `[Type] #M - 내용`(**PR 자기 번호 M**), 본문 `Part of #N` |
| **단발** | 버그 하나·소규모 독립 작업 | `[Type] #K - 내용` | `[Type] #K - 내용`, 본문 `Closes #K`(기존 방식) |

- **Epic 이슈 본문** = 살아있는 계획. 투두를 `- [ ]`로 두고 PR 머지될 때마다 `- [x]`로 체크(수동/자동). 계속 수정 가능.
- **Task PR은 에픽을 안 닫는다** — `Part of #N`(참조만). 에픽은 **마지막 Task PR이 `Closes #N`** 하거나 수동으로 닫음.
- **번호 규칙**: 한 Task의 브랜치·커밋·PR은 **그 Task PR 번호 M**을 공유. 에픽 번호 N은 본문 참조로만.
- 애매하면 Epic. 5줄 미만 후속은 새 Task 대신 관련 PR에 얹거나 chore.

## `#번호` 포맷 (한 Task/단발 = 자기 번호 공유)

한 Task PR(또는 단발)의 **브랜치·커밋·PR은 그 PR 번호를 공유**한다. (Epic 번호는 본문 참조로만.)

| 위치 | 포맷 | 예 |
| --- | --- | --- |
| 브랜치 | `type/#번호` | `feat/#50` |
| 커밋 | `[Type/#번호] 내용` | `[Feat/#50] Q 블렌드 정식화` |
| PR 제목 | `[Type] #번호 - 내용` | `[Feat] #50 - 백테스트 Q 블렌드` |
| PR 본문 | **`Closes #자기이슈`**(머지 시 자동종료) + Task는 `Part of #에픽` 추가(링크·에픽 안 닫음) | `Closes #55` `Part of #53` |
| (Epic 이슈) | `[Epic] #번호 - 주제` + 투두 체크리스트 | `[Epic] #47 - AI펀드 피봇` |

- 이슈 제목은 생성 후 부여된 번호를 확인해 `#번호`를 끼워 넣는다.
- 한 브랜치의 전 커밋은 **같은 `#번호`**, Type만 바뀔 수 있다(예: 구현 `[Feat/#12]` → 리팩터 `[Refactor/#12]`).
- 한국어 설명 OK. ❌ `update`·`wip`·`작업함` 같은 무의미 메시지·빈 커밋 금지.

### 커밋 쪼개기 기준
**규칙은 "이슈1=커밋1"이 아니라 "커밋 = 리뷰 가능한 하나의 논리 단위"다.**
- **1커밋:** 상수 1개 + 그 테스트처럼 원자적 변경. (억지로 쪼개 빈 커밋 만들지 말 것)
- **여러 커밋:** 한 PR에 (a)리팩터·스캐폴딩(동작 무변경) → (b)동작 변경 → (c)테스트 → (d)문서처럼 **구분되는 단계**가 있으면 단계별로. diff가 이야기처럼 읽혀 셀프 리뷰가 쉬워지게.
- **이슈 남발 자제:** 5줄 미만 후속·정리는 새 이슈 대신 관련 PR에 얹거나 chore로 묶는다.

## Type

`Feat`(새 기능) / `Fix`(버그) / `Hotfix`(라이브 긴급) / `Refactor` / `Design`(UI·스타일) / `Setting`(설정·빌드·CI) / `Docs` / `Test` / `Chore`(잡일)

## 브랜치 전략

- `main` = 안정(라이브). **직접 커밋·푸시 금지.**
- 작업은 `type/#번호` 브랜치 → PR → diff 셀프 리뷰 → CI 통과 → main 머지.
- 라이브 긴급 상황의 **Hotfix만 예외**(사후에 이슈로 기록).

## 개발 순서

1. 큰 테마면 **Epic 이슈**(`[Epic] #N` + 투두 체크리스트) 먼저. 단발이면 일반 이슈.
2. 투두(또는 단발) → `type/#번호` 브랜치. (Task 번호 = 그 PR 번호)
3. 커밋 `[Type/#번호] 내용` → push. (한 PR 안에서 만들고·조정·수정 여러 커밋 OK)
4. PR: 작업 브랜치 → `main`. 본문 **`Closes #자기이슈`**(머지 시 자동종료) — Task는 여기에 `Part of #에픽`(링크) 추가.
5. **셀프 리뷰**(diff 훑기) + CI 통과. 수정은 같은 브랜치에 커밋 추가.
6. 머지 → Task면 **에픽 투두 `- [x]` 체크**. 단발이면 `Closes`로 이슈 자동 종료. 에픽은 마지막 Task가 `Closes #N` 하거나 수동 종료.

## 라벨 (repo 1회, `gh` 로그인 후 실행)

> 타입 라벨만 사용(솔로라 담당자 라벨 제외). 라벨명은 `이름 이모지`, 커밋 Type은 `[이름]`.

```bash
gh label create "Feat 💻"     --color 0E8A16 --description "새 기능" --force
gh label create "Fix 🐛"      --color D93F0B --description "버그 수정" --force
gh label create "Hotfix 🚨"   --color B60205 --description "긴급 수정" --force
gh label create "Refactor ♻️" --color 5319E7 --description "리팩터링" --force
gh label create "Design 🎨"   --color D4C5F9 --description "UI·스타일" --force
gh label create "Setting ⚙️"  --color 555555 --description "설정·빌드" --force
gh label create "Docs 📝"     --color 0075CA --description "문서" --force
gh label create "Test 🧪"     --color C2E0C6 --description "테스트" --force
gh label create "Chore 🧹"    --color CFD3D7 --description "잡일" --force
```

- 마일스톤 = 스프린트/릴리스(예: `출시`, `v1.1`) — 이슈 묶기용. 선택.
- ⚠️ 솔로는 여기까지만. 라벨 체계 과하게 늘리지 말 것.

## CI · 자동화

`.github/workflows/`:
- **ci.yml** — PR·main push마다 `compileall`(문법) + 오프라인 안전 테스트 4종. (통합 테스트는 추후 secrets 워크플로우로 분리)
- **pr-label.yml** — PR 제목 `[Type]` → 타입 라벨 자동 부여.
- **issue-label.yml** — 이슈 제목 `[Type]` → 타입 라벨 자동 부여(템플릿 안 거친 이슈까지).
- **pr-title-lint.yml** — PR 제목이 `[Type] #번호 - 내용` 형식이 아니면 실패(머지 게이트).

repo 설정:
- **머지 후 작업 브랜치 자동 삭제** ON (`delete_branch_on_merge`). 브랜치 목록이 안 쌓인다.
- PR 본문 **`Closes #번호`** → 머지 시 그 이슈 자동 종료. **Task도 반드시 `Closes #자기Task이슈`**(안 그러면 이슈가 안 닫히고 쌓임). `Part of #에픽`은 참조만(에픽 안 닫음 — 수동/마지막 Task가 종료).
