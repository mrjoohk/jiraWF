---
name: worklog-daily
description: Jira에서 내 태스크의 지난 실행 이후 변동과 미완료 목록을 수집해 로컬 일일 업무 기록(daily/YYMMDD.md)을 갱신한다. 매일 13:00 예약 실행이 기본이며 수동 재실행도 안전하다. "일일 배치", "오늘 할 일 가져와", "업무일지 갱신", "worklog daily" 등이 언급되면 사용할 것.
---

# 일일 배치

Jira → 로컬 일일 기록. **읽기 전용이다. 이 스킬은 Jira에 아무것도 쓰지 않는다.**

## 절대 규칙

1. **진행 메모 구획(`<!-- MEMO:START -->` ~ `<!-- MEMO:END -->`)을 직접 편집하지 않는다.** 파일 갱신은 반드시 `render_daily.py`를 통한다. 이 스크립트가 메모를 원문 그대로 옮긴다.
2. **문장은 서브에이전트가 쓴다.** 메인 세션이 일지 본문을 직접 작성하지 않는다 — 대화 맥락이 섞여 원본에 없는 내용이 들어간다.
3. **집계는 스크립트가 한다.** 건수·분류·정렬을 눈대중으로 하지 않는다.
4. 어느 단계든 실패하면 **watermark를 전진시키지 않는다.** 다음 실행이 그 구간을 다시 가져가게 둔다.

## 작업 경로

`WF_HOME`(= `wf-init`가 정한 업무 데이터 폴더)을 기준으로 한다. 저장소 트리 안이 아니다.

```
$WF_HOME/
  jira_project_key_id.txt
  .workflow/state.json  .workflow/raw/  .workflow/agg/
  daily/  weekly/  logs/
```

## 절차

### 1. 사전 확인
- `.workflow/state.json`을 읽는다. 없으면 **중단**하고 `/wf-init` 실행을 안내한다.
- `schema_version`이 이 플러그인이 기대하는 값과 다르면 **실행하지 말고 중단**한다. 잘못된 스키마로 계속 도는 것보다 멈추는 편이 복구 가능하다.
- `jira_project_key_id.txt`에서 프로젝트 키를 읽는다(주석·빈 줄 무시, 첫 유효 줄).

### 2. Jira 조회 (MCP)
두 번 조회한다. `{WM}`은 `state.json`의 `watermark`.

- **변동**: `project = {KEY} AND assignee = currentUser() AND updated >= "{WM}" ORDER BY updated DESC` — changelog와 comment를 함께 요청한다.
- **미완료**: `project = {KEY} AND assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC`

응답을 **가공하지 말고 그대로** 저장한다:
`.workflow/raw/{YYMMDD_HHMM}_changes.json`, `.workflow/raw/{YYMMDD_HHMM}_open.json`

> 원본을 남기는 이유: 집계 로직을 고쳐도 재조회 없이 다시 돌릴 수 있고, 결과가 이상할 때 조회 문제인지 집계 문제인지 분리된다. 그리고 INV-4가 대조할 대상이 된다.

### 3. 집계 (결정적)
```
python3 <plugin>/scripts/aggregate.py \
  --changes .workflow/raw/{stamp}_changes.json \
  --open    .workflow/raw/{stamp}_open.json \
  --state   .workflow/state.json \
  --out     .workflow/agg/{YYMMDD}.json \
  --date    {YYYY-MM-DD}
```
출력의 `field_paths_used`를 **첫 실행에서는 반드시 확인**하고 사용자에게 보고한다. `<none>`이 많으면 MCP 응답 형태가 예상과 다르다는 뜻이다.

### 4. 문장화 (서브에이전트)
`worklog-writer` 서브에이전트를 생성한다. **집계 파일 경로만 전달하고 대화 맥락은 넘기지 않는다.**
서브에이전트의 산출물은 "지난 실행 이후 변동" 본문 한 덩어리이며, 임시 파일에 저장한다.

### 5. 파일 갱신
```
python3 <plugin>/scripts/render_daily.py \
  --agg .workflow/agg/{YYMMDD}.json --narrative {임시파일} \
  --out daily/{YYMMDD}.md --state .workflow/state.json
```

### 6. 검사
```
python3 <plugin>/scripts/loop_checks.py \
  --daily daily/{YYMMDD}.md --agg .workflow/agg/{YYMMDD}.json \
  --state .workflow/state.json --evidence logs/{stamp}/evidence.json
```
**종료 코드가 1이면 watermark를 전진시키지 않고**, 위반 내용을 사용자에게 그대로 보고한다. 특히 `INV-4`는 일지에 실재하지 않는 이슈 키가 들어갔다는 뜻이므로 반드시 사람이 본다.

### 7. 마무리
- 검사 통과 시에만 `state.json`의 `watermark`를 이번 조회 시각으로, `last_success`를 현재 시각으로 갱신한다.
- **미마감 감지**: 어제 daily 파일의 메모 구획에 내용이 있는데 그 이후 Jira 변동이 없으면, 오늘 일지 상단에 "어제 마감 누락 의심"을 표시한다. 마감 단계를 빠뜨리면 기록이 조용히 비어버리는 것을 막는 유일한 장치다.
- 사용자에게는 **짧게** 보고한다: 신규/이어서/정체 건수, 미등록 파생 건수, 정체 경고, 파일 경로.
