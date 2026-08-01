# Git / 개발 컨벤션 (솔로)

> 블록깨비 팀 컨벤션(Offroad 스타일)의 솔로 버전. **본질 = 추적성: 코드 변경이 어느 목표에서 왔는지 이슈로 이어지게.**
> 라이브 사이트 절대 규칙은 [CLAUDE.md](CLAUDE.md) B장 참조 — 컨벤션보다 우선한다.

## 작업 단위: Epic · Task · 단발 (하이브리드)

큰 다단계 작업은 **Epic 이슈 + Task PR들**로, 작은 단발 작업은 **1이슈=1PR**로.

| | 언제 | 이슈 | PR |
| --- | --- | --- | --- |
| **Epic** | **하나의 응집 묶음**(1a~1d처럼 함께 가는 몇 개 Task) | `[Epic] #N - 주제` + **투두 체크리스트 본문** | (없음 — 아래 Task들이 채움) |
| **Task** | 에픽의 투두 하나 | **❌ 안 만듦** | `[Type] #N - 내용`(**에픽 번호 N**), 본문 `Part of #N`. 브랜치는 슬러그 |
| **단발** | 에픽 밖 독립 작업(버그 하나 등) | `[Type] #K - 내용` | `[Type] #K - 내용`, 본문 `Closes #K` |

- **Epic 스코프 = 하나의 응집 묶음.** "1a~1d를 함께 진행" 정도 한 덩어리에 Epic 하나. 수십 개를 무한정 안고 가지 말 것 — **테마가 바뀌면(빌드→관전UX 등) 옛 Epic에 얹지 말고 새 Epic**을 판다. (예: 데스크 빌드 `#53` ≠ 관전 UX 재설계 `#89`.) Epic이 비대해지면 쪼갠다.
- **핵심: Task는 이슈를 새로 안 만든다.** 이슈는 **Epic 하나뿐.** Task = PR만. (안 그러면 이슈=PR 1:1 중복.)
- **Epic 이슈 본문** = 살아있는 계획. 투두 `- [ ]` → 관련 Task PR 머지 시 `- [x]` 체크. 여기에 Task 한 줄씩 추가.
- **Task PR 번호**: 제목·커밋에 **에픽 번호 N**을 쓴다(예 `[Feat] #53 - Q 실행`). 브랜치는 겹치니 **슬러그**(`feat/q-exec`). 본문 `Part of #N`(에픽 안 닫음).
- **`Closes`는 단발 전용**(자기 이슈 종료). Task는 `Closes` 안 씀 — 에픽 체크리스트로 추적, 에픽은 마지막에 수동 종료.
- 애매하면 Epic. 5줄 미만 후속은 새 PR 대신 관련 PR에 얹거나 chore.

## `#번호` 포맷

**Task는 에픽 번호 N**을, **단발은 자기 이슈 번호 K**를 쓴다.

| 위치 | Task (에픽 하위) | 단발 |
| --- | --- | --- |
| 브랜치 | `type/슬러그` (`feat/q-exec` — 번호 겹쳐서 슬러그) | `type/#K` |
| 커밋 | `[Type/#N] 내용` (에픽 번호) | `[Type/#K] 내용` |
| PR 제목 | `[Type] #N - 내용` (에픽 번호) | `[Type] #K - 내용` |
| PR 본문 | `Part of #N` (**Closes 안 씀** — 에픽 체크리스트로 추적) | **`Closes #K`** (머지 시 자동종료) |
| 이슈 | ❌ 없음 (Epic 하나가 전부) | `[Type] #K - 내용` |

- 한 브랜치의 전 커밋은 같은 `#번호`, Type만 바뀔 수 있다(구현 `[Feat/#N]` → 리팩터 `[Refactor/#N]`).
- Epic 이슈: `[Epic] #N - 주제` + 투두 체크리스트.
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

1. 큰 테마면 **Epic 이슈**(`[Epic] #N` + 투두 체크리스트) 먼저 + **투두에 이번 Task 한 줄 추가**. 단발이면 일반 이슈.
2. **Task**: `type/슬러그` 브랜치(예 `feat/q-exec`). **단발**: `type/#K` 브랜치.
3. 커밋 — Task `[Type/#N]`(에픽 번호) / 단발 `[Type/#K]` → push. (여러 커밋 OK)
4. PR → `main`. 본문 — **Task `Part of #N`**(Closes 안 씀) / **단발 `Closes #K`**(자동종료).
5. **셀프 리뷰**(diff 훑기) + CI 통과. 수정은 같은 브랜치에 커밋 추가.
6. 머지 → Task면 **에픽 투두 `- [x]` 체크**(수동). 단발이면 `Closes`로 자동 종료. 에픽은 다 끝나면 수동 종료.

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
- PR 본문 **`Closes #K`** → 머지 시 그 이슈 자동 종료. **단발 전용.** Task는 자기 이슈가 없으니 `Part of #N`(에픽 참조·안 닫음)만 — 에픽 체크리스트로 추적, 에픽은 수동 종료.
