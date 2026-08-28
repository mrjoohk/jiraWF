#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""스킬 문서 ↔ 코드 계약 대조 (jira-worklog).

스킬 문서는 스크립트의 인자·필드·섹션 제목을 문자열로 인용한다. 그 인용이
코드와 어긋나면 실행 시점에야 드러나고, 그때는 배치가 죽거나 조용히 틀린다.
이 검사는 **문서 쪽 수정을 되돌리면 실패하도록** 짜여 있다 — 통과만으로는
아무것도 증명하지 못하므로, 각 항목은 "문서가 이렇게 적고 있는가"와
"코드가 실제로 그런가"를 함께 본다.

  python3 jiraWF/tools/skill_sync_check.py       # 바깥 프로젝트에서 (증거는 ./logs/)
  python3 tools/skill_sync_check.py              # jiraWF 안에서 (CI 경로)

검사 대상 경로는 **이 파일의 위치를 기준으로** 잡는다. 현재 디렉터리에
의존하면 CI와 로컬이 다른 것을 검사하게 되고, 그 차이는 조용하다.
증거 파일만 현재 디렉터리 기준(`logs/`)이라 두 곳 모두에서 자연스럽게 남는다.

종료 코드 0 = 통과, 1 = 불일치.
"""
import argparse
import datetime
import io
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)                      # jiraWF/
R = os.path.join(_REPO, "plugins", "jira-worklog")
MARKETPLACE = os.path.join(_REPO, ".claude-plugin", "marketplace.json")


def rd(p):
    return io.open(p, encoding="utf-8").read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    a = ap.parse_args()

    daily = rd(f"{R}/skills/worklog-daily/SKILL.md")
    close = rd(f"{R}/skills/worklog-close/SKILL.md")
    weekly = rd(f"{R}/skills/worklog-weekly/SKILL.md")
    drafter = rd(f"{R}/agents/jira-drafter.md")
    init = rd(f"{R}/commands/wf-init.md")
    agg = rd(f"{R}/scripts/aggregate.py")
    rnd = rd(f"{R}/scripts/render_daily.py")
    chk = rd(f"{R}/scripts/loop_checks.py")
    plug = json.loads(rd(f"{R}/.claude-plugin/plugin.json"))
    mkt = json.loads(rd(MARKETPLACE))
    readme = rd(os.path.join(_REPO, "README.md"))
    agents = rd(os.path.join(_REPO, "AGENTS.md"))
    fields = rd(f"{R}/scripts/jira_fields.py")
    newt = rd(f"{R}/scripts/new_title.py")
    mrgt = rd(f"{R}/scripts/merge_targets.py")

    SCHEMA_EXPECTED = 3
    LINT_MAX = 600
    LINT_DESC_MAX = 300
    C = []

    def c(n, ok, d=""):
        C.append({"check": n, "ok": bool(ok), "detail": d})

    for f in ("aggregate.py", "render_daily.py", "loop_checks.py"):
        c(f"스크립트 존재: {f}", os.path.exists(f"{R}/scripts/{f}"))

    for name, args, src in [
        ("aggregate.py", ["--changes", "--open", "--state", "--out", "--date"], agg),
        ("render_daily.py", ["--agg", "--narrative", "--out", "--state", "--version"], rnd),
        ("loop_checks.py", ["--daily", "--agg", "--state", "--evidence"], chk),
    ]:
        for arg in args:
            c(f"{name} 인자 {arg}", f'"{arg}"' in src and arg in daily,
              "스킬 인용 ↔ add_argument")

    for fld in ["project_key", "epic_key", "title_include", "title_exclude"]:
        c(f"state 필드 {fld} — 코드에 실재", f'"{fld}"' in agg or f'"{fld}"' in chk)
        c(f"state 필드 {fld} — close 스킬 인용", fld in close)

    c("schema_version — 스킬 3종이 게이트로 서술",
      all("schema_version" in t for t in (daily, close, weekly)))
    c("schema_version — wf-init이 기록", "schema_version" in init)
    # F-014 해소 후 방향을 뒤집었다. 이전에는 "스크립트가 검사하지 않는다"가
    # 통과 조건이어서, 고치는 순간 이 검사가 실패했다 — 버그를 기대값으로
    # 인코딩해 두면 고치는 사람이 자기가 뭘 깨뜨렸다고 오해한다.
    # import 만 보면 안 된다 — 호출을 지워도 import 는 남아 검사가 통과한다.
    # 실제로 그렇게 반전 시험이 새어 나갔다(2026-08-27). 호출부를 본다.
    CALL = "require_schema(state, a.state)"
    c("schema_version — 스크립트가 실제로 막는다(기계 게이트)",
      all(CALL in src for src in (agg, chk, newt, mrgt))
      and "def require_schema(" in fields and "SCHEMA_VERSION" in fields,
      "F-014 해소 — 호출을 지우면 이 검사가 실패한다")
    c("schema_version — 불일치 시 종료 코드 2",
      "raise SystemExit(2)" in fields)
    c("schema_version — 코드 상수와 스킬 서술이 같은 값",
      f"SCHEMA_VERSION = {SCHEMA_EXPECTED}" in fields
      and f"(**{SCHEMA_EXPECTED}**)" in daily
      and f"**{SCHEMA_EXPECTED}**" in close)

    c("close: out_of_scope 인용", "out_of_scope" in close and '"out_of_scope"' in agg)
    c("close: scope 인용", "scope" in close and '"scope"' in agg)
    c("close: children_pending 인용",
      "children_pending" in close and "children_pending" in agg)

    for fld in ["local_id", "kind", "summary", "approved_at", "jira_key"]:
        c(f"child 필드 {fld} — close 스킬 명시", fld in close)
    c("child local_id — render_daily가 요구", "c['local_id']" in rnd)
    c("child kind/summary — render_daily가 요구",
      'c.get("kind"' in rnd and "c.get('summary'" in rnd)
    c("INV-3 방향(jira_key 있고 approved_at 없음)",
      'c.get("jira_key") and not c.get("approved_at")' in chk)

    for k in ["comments", "children", "unclear", "parent_local_id"]:
        c(f"draft.json 키 {k} — 드래프터 계약과 일치", k in drafter and k in close)
    c("close: local_id는 메인 세션이 부여한다고 명시", "서브에이전트가 만들지 않는다" in close)
    c("드래프터 출력 children에 local_id 없음(계약 확인)",
      '"children": [{"parent_local_id": "R-001", "kind": "bug",' in drafter)

    for sec in ["## 지난 실행 이후 변동", "## 진행 메모", "## 오늘 할 일"]:
        c(f"weekly가 인용한 섹션 제목 {sec} — render_daily에 실재",
          sec in rnd and sec in weekly)

    c("aggregate exit 2 경로 — daily 스킬에 서술",
      "SystemExit(2)" in agg and "종료 코드 2" in daily)
    c("버전 세 곳 일치",
      plug["version"] == mkt["plugins"][0]["version"]
      and f'default="{plug["version"]}"' in rnd,
      f'plugin={plug["version"]} marketplace={mkt["plugins"][0]["version"]}')
    c("weekly: 생성 버전 표기 지시", "생성 버전" in weekly)
    c("weekly: 범위 머리말 지시", "머리말" in weekly)
    c("weekly: loop_checks 비대상 명시", "검사 대상이 **아니다" in weekly)
    c("weekly: Done 판정 근거 명시", "→ Done" in weekly)
    c("close: 절차 0 사전 확인 존재", "### 0. 사전 확인" in close)

    # ── 사용자 매뉴얼 ↔ 스킬 (README가 안내하는 문구가 실제로 걸리는가) ──
    # README는 "이렇게 말하면 됩니다"라고 문구를 나열한다. 스킬의
    # description 이 바뀌면 사용자는 걸리지 않는 말을 배우게 되고, 그
    # 실패는 "왜 안 되지"로만 나타나 원인을 찾기 어렵다.
    def desc(md):
        for line in md.splitlines()[:12]:
            if line.startswith("description:"):
                return line
        return ""

    for phrase, src, who in [
        ("일일 배치", daily, "daily"), ("오늘 할 일 가져와", daily, "daily"),
        ("업무일지 갱신", daily, "daily"),
        ("오늘 한 일 정리해줘", close, "close"), ("마감", close, "close"),
        ("오늘 정리", close, "close"), ("Jira에 올려줘", close, "close"),
        ("주간일지", weekly, "weekly"), ("이번 주 정리", weekly, "weekly"),
        ("주간 롤업", weekly, "weekly"),
    ]:
        c(f"트리거 문구 '{phrase}' — README 안내 ↔ {who} description",
          phrase in readme and phrase in desc(src),
          "README가 알려준 말이 실제로 그 스킬을 부르는가")

    c("README가 설명한 메모 받아쓰기 경로가 close 스킬에 있다",
      "받아쓰" in readme and "받아쓰기이지" in close)
    # ── 스프린트 배치 / 본문 문체 ────────────────────────────────
    lint = rd(f"{R}/scripts/draft_lint.py")
    c("스크립트 존재: draft_lint.py", os.path.exists(f"{R}/scripts/draft_lint.py"))
    for arg in ["--draft"]:
        c(f"draft_lint.py 인자 {arg}", f'"{arg}"' in lint and arg in close)
    c("draft_lint: 1은 경고이지 중단이 아니라고 close·AGENTS 가 같이 말한다",
      "중단이 아니라 경고" in close and "중단 아님" in agents,
      "다른 스크립트의 1(멈춰라)과 뜻이 다르므로 양쪽에 적혀 있어야 한다")
    c("문체 규칙 — 드래프터가 머리말·표를 금지한다",
      "머리말(`##`)과 표를 쓰지 않는다" in drafter)
    c("문체 길이 상한 — 문서와 코드가 같은 값",
      f"MAX_CHARS = {LINT_MAX}" in lint and f"{LINT_MAX}자" in drafter
      and f"{LINT_MAX}자" in readme)
    c("설명 길이 상한 — 설명은 코멘트보다 짧다고 코드·드래프터·README가 같이 말한다",
      f"MAX_DESC_CHARS = {LINT_DESC_MAX}" in lint
      and f"{LINT_DESC_MAX}자" in drafter and f"{LINT_DESC_MAX}자" in readme,
      "상한을 하나로 되돌리면 여기서 걸린다")
    c("로컬 참조 — 드래프터가 금지하고 lint가 세고 close가 승인 표에 보인다",
      "팀원이 자기 자리에서 확인할 수 있는 것만 적는다" in drafter
      and "local_ref" in lint and "local_ref" in close,
      "읽는 사람이 못 여는 경로는 아무것도 전달하지 않는다")
    c("sprint_field — wf-init이 스키마로 찾고 close가 쓴다",
      "gh-sprint" in init and "sprint_field" in close)
    c("sprint_mode — 세 값이 wf-init·close 양쪽에 있다",
      all(v in init and v in close for v in ("`active`", "`backlog`", "`ask`")))
    c("활성 스프린트 판별 — 지나간 것을 걸러낸다고 명시",
      'state == "active"' in close)

    # ── 진행 중 전이 ─────────────────────────────────────────────
    design = rd(os.path.join(_REPO, "DESIGN.md"))
    c("transition_on_update — wf-init이 받고 close가 쓴다",
      "transition_on_update" in init and "transition_on_update" in close)
    c("전이 값 두 가지가 wf-init·close 양쪽에 있다",
      all(v in init and v in close for v in ("`in_progress`", "`none`")))
    c("전이 대상은 statusCategory 로 고른다(이름으로 찾지 않는다)",
      '`new`' in close and 'statusCategory' in close
      and 'to.statusCategory.key == "indeterminate"' in close)
    c("완료 전이는 어디서도 하지 않는다고 close·DESIGN·AGENTS 가 함께 말한다",
      "완료(Done) 전이는 절대 자동으로 하지 않는다" in close
      and "완료 전이는 하지 않는다" in design
      and "완료(Done) 전이를 자동으로 하지 않습니다" in agents,
      "한 곳만 고치면 문서가 서로 다른 말을 하게 된다")
    c("done 상태는 건드리지 않는다고 명시",
      "`done`은 **건드리지 않는다**" in close)
    c("전이는 반영 순서의 마지막", "진행 중 전이" in close and "전이가 마지막" in close)
    c("README가 사용자에게 전이 동작을 알린다",
      "해야 할 일" in readme and "진행 중" in readme and "완료 처리는 하지 않습니다" in readme)

    c("README가 설명한 세 갈래(새 태스크/병합/보류)가 close 스킬에 있다",
      all(k in readme and k in close for k in ("새 태스크", "병합", "보류")))

    # ── 신규 태스크 생성 / 병합 선택 (0.4.0~0.5.0) ────────────────────
    for f in ("new_title.py", "merge_targets.py"):
        c(f"스크립트 존재: {f}", os.path.exists(f"{R}/scripts/{f}"))
    for name, args, src in [
        ("new_title.py", ["--state", "--summary"], newt),
        ("merge_targets.py", ["--state", "--issues"], mrgt),
    ]:
        for arg in args:
            c(f"{name} 인자 {arg}", f'"{arg}"' in src and arg in close,
              "스킬 인용 ↔ add_argument")

    c("new_title: exit 2 경로 — close 스킬에 서술",
      "return 2" in newt and "종료 코드 2" in close)
    c("new_title: 파생은 접두사 면제 — 코드와 문서가 같다",
      '"subtask"' in newt and "파생" in close)
    c("title_prefix — 코드가 읽고 wf-init이 받는다",
      '"title_prefix"' in newt and "title_prefix" in init)
    c("merge_targets: 파생 제외 계약", 'iss["is_subtask"]' in mrgt and "파생" in close)
    c("merge_targets: 범위 안팎 표시", '"in_scope"' in mrgt and "밖" in close)

    for k in ["new_roots", "parent_ref", "ref"]:
        c(f"draft.json 키 {k} — 드래프터 계약과 close 가 일치",
          k in drafter and k in close)
    c("new_roots local_id는 메인 세션이 부여(드래프터는 ref만)",
      "R-nnn" in drafter and "ref" in drafter and "매핑" in close)

    c("roots.origin — aggregate가 jira로 기록",
      '"origin": "jira"' in agg)
    c("roots.origin — close가 local로 기록", '"origin": "local"' in close
      or '"origin":"local"' in close)
    c("INV-3 확장 — 마감이 만든 root도 검사",
      'root.get("origin") == "local"' in chk)
    c("INV-5 — 제목 필터까지 비교",
      'state.get("title_include")' in chk and "title_include" in daily)

    ts = datetime.datetime.now()
    failed = [x["check"] for x in C if not x["ok"]]
    ev = {"tool": "skill_sync_check",
          "timestamp": ts.isoformat(timespec="seconds"),
          "scope": "jiraWF/plugins/jira-worklog/{skills,agents,commands,scripts}",
          "checks": C, "total": len(C), "failed": failed,
          "exit_code": 1 if failed else 0}
    out = a.out or f"logs/{ts:%y%m%d_%H%M}_skill_sync_evidence.json"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    json.dump(ev, io.open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"skill_sync_check: {len(C) - len(failed)}/{len(C)} OK → {out}")
    for x in failed:
        print("  불일치: " + x)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
