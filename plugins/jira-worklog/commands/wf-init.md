---
description: jira-worklog 최초 1회 초기화 — 커넥터 확인, 프로젝트 판별, 작업 폴더와 예약 작업 생성
---

# wf-init

배포물에 담을 수 없는 것을 이 환경에서 만든다. **여러 번 실행해도 안전해야 한다** — 기존 `watermark`와 `roots`는 절대 지우지 않고 설정 항목만 갱신한다. (초기화 명령이 데이터를 지우면, "뭔가 이상할 때 다시 돌려본다"는 가장 자연스러운 복구 행동이 사고가 된다.)

아래 8단계를 순서대로 수행하고, 각 단계 결과를 사용자에게 한 줄씩 보고한다.

## 1. Atlassian 커넥터 확인
지점 조회를 1회 시도한다. 실패하면 **여기서 중단**하고 커넥터 연결 방법을 안내한다. 이후 단계로 넘어가지 않는다.
> 인증은 배포할 수 없다. 각자 자기 계정으로 연결해야 한다.

## 2. 작업 폴더 결정
업무 데이터를 둘 폴더(`WF_HOME`)를 사용자에게 확인받는다. 기본 제안: `~/jira-worklog`.

**이 폴더는 플러그인 저장소 트리 안이면 안 된다.** 저장소는 public이며, 섞이면 언젠가 업무 기록이 커밋된다. 저장소 안 경로가 제시되면 거부하고 다른 경로를 요청한다.

## 3. 프로젝트 키
키를 입력받아 실제 조회로 유효성을 확인한 뒤 `WF_HOME/jira_project_key_id.txt`에 쓴다. **키는 1개만** 지원한다.

## 4. 프로젝트 유형·이슈 타입 판별 — 이 명령의 핵심
프로젝트 메타데이터를 조회해 다음을 판별하고 `issue_type_map`에 기록한다.

- `project_style`: team-managed / company-managed
- `subtask_type`: 실제 생성 가능한 sub-task 계층 타입 이름
- `bug_marker`:
  - sub-task 계층에 버그용 커스텀 타입이 있으면 → `type:<이름>`
  - 없으면(team-managed는 Subtask 1종만 지원) → `label:bug`

판별에 실패하면 `label:bug`로 폴백하되 **그 사실을 기록하고 사용자에게 알린다.**
> 이 불확실성을 매 실행마다 만나지 않고 초기화 1회로 가두는 것이 이 단계의 목적이다.

## 5. Epic 전제 확인
나에게 할당된 태스크 표본의 `parent`가 Epic인지 확인한다. Epic이 아니면 **경고만 하고 계속 진행**한다 — 과제별 구획이 동작하지 않는다는 점을 알린다.

## 6. state.json
없으면 생성한다.
```json
{"schema_version": 1, "watermark": "<현재 시각 ISO8601>", "last_success": null,
 "project_key": "...", "issue_type_map": {...}, "roots": {}}
```
이미 있으면 `project_key`·`issue_type_map`만 갱신하고 나머지는 **보존**한다.

## 7. 폴더와 .gitignore
`daily/` `weekly/` `logs/` `.workflow/raw/` `.workflow/agg/`를 만들고, `WF_HOME`에 `.gitignore`를 둔다(`daily/`, `weekly/`, `.workflow/`, `state.json`, `jira_project_key_id.txt`).

## 8. 시험 조회
미완료 이슈를 1회 조회해 건수와 과제 목록을 보여준다. 그리고 13:00 평일 예약 작업 등록을 제안한다.
0건이면 JQL·권한·프로젝트 키를 점검하도록 안내한다.

---
마지막에 요약 표(작업 폴더 / 프로젝트 키 / 프로젝트 유형 / 버그 표시 방식 / 이슈 건수 / 예약 등록 여부)를 보여준다.
