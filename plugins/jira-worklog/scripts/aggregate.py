#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""수집된 Jira 원본을 결정적으로 필터·집계·분류한다.

이 스크립트는 Jira를 호출하지 않는다. 입력이 같으면 출력이 항상 같다.
문장화는 하지 않는다 — 그것은 서브에이전트의 일이다.

  aggregate.py --changes raw_changes.json --open raw_open.json \
               --state .workflow/state.json --out .workflow/agg/260814.json \
               --date 2026-08-14
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jira_fields import (HITS, issue_list, norm_issue,  # noqa: E402
                         require_schema)

STALL_DAYS = 7
WIP_LIMIT = 3


def _utf8_stdout():
    """Windows 콘솔 기본 코드페이지(cp949 등)에서 위반 메시지가 깨지지 않도록.

    메시지에 '→'나 '—'가 섞이면 인코딩 오류로 print 자체가 죽는다. 그러면
    사람이 봐야 할 위반 내용이 화면에 아예 안 나오고, 종료 코드만 1로 남아
    "검사에 걸렸다"와 "검사가 죽었다"를 구별할 수 없게 된다.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def load(path, default=None):
    if not path or not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def days_between(a, b):
    fmt = "%Y-%m-%d"
    return (dt.datetime.strptime(b, fmt) - dt.datetime.strptime(a, fmt)).days


def in_scope(iss, project_key, epic_key):
    """이 이슈가 이 폴더가 맡은 범위 안인가.

    파생(Sub-task)의 부모는 에픽이 아니라 메인 태스크이므로 에픽 판정에서
    제외한다. 파생은 어차피 root 아래에 붙으며 별도로 걸러진다.
    """
    if iss.get("is_subtask"):
        return True
    key = iss.get("key") or ""
    if project_key and not key.startswith(project_key + "-"):
        return False
    if epic_key and iss.get("parent_key") != epic_key:
        return False
    return True


def compile_patterns(patterns, field):
    """제목 필터 정규식을 미리 컴파일한다.

    잘못된 정규식을 만나면 **중단한다.** 조용히 건너뛰면 그 폴더는 필터가
    없는 것처럼 동작해 옆 폴더의 태스크까지 끌어안게 되고, 그 사실이
    아무 데도 남지 않는다.
    """
    out = []
    for pat in patterns:
        try:
            out.append(re.compile(pat, re.IGNORECASE))
        except re.error as exc:
            print(f"{field}의 정규식이 잘못되었다: {pat!r} — {exc}",
                  file=sys.stderr)
            raise SystemExit(2)
    return out


def title_match(iss, include, exclude):
    """제목 필터를 통과하는가.

    같은 에픽을 여러 폴더가 제목으로 나눠 맡을 때 쓴다. 파생(Sub-task)은
    제목 규약을 따르지 않으므로 면제한다 — 어차피 root 아래에 붙는다.

    include가 비어 있으면 "전부 통과"다. 두 폴더가 각각 include만 쓰면
    어느 쪽에도 안 걸리는 태스크가 조용히 사라지므로, 한쪽은 exclude로
    나머지를 맡게 하는 편이 안전하다.
    """
    if iss.get("is_subtask"):
        return True
    summary = iss.get("summary") or ""
    if include and not any(r.search(summary) for r in include):
        return False
    if any(r.search(summary) for r in exclude):
        return False
    return True


def next_root_id(roots):
    used = [int(m.group(1)) for k in roots for m in [re.fullmatch(r"R-(\d+)", k)] if m]
    return "R-%03d" % ((max(used) + 1) if used else 1)


def summarize_changes(issue):
    """changelog + comment를 사람이 읽을 항목 리스트로. 문장 생성은 안 한다."""
    out = []
    for h in issue.get("changelog") or []:
        for item in (h.get("items") or []):
            field = item.get("field", "")
            frm, to = item.get("fromString"), item.get("toString")
            if field in ("status", "assignee", "priority", "duedate", "resolution"):
                out.append({"kind": field, "from": frm, "to": to,
                            "at": h.get("created", "")})
    n_comment = len(issue.get("comments") or [])
    if n_comment:
        out.append({"kind": "comment", "count": n_comment})
    return out


def main():
    _utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--changes", required=True)
    ap.add_argument("--open", dest="open_", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--date", required=True)
    a = ap.parse_args()

    state = load(a.state, {}) or {}
    require_schema(state, a.state)
    roots_state = state.setdefault("roots", {})
    today = a.date

    project_key = state.get("project_key")
    epic_key = state.get("epic_key")
    epic_scope_name = state.get("epic_name")

    changed = [norm_issue(i) for i in issue_list(load(a.changes, {}) or {})]
    open_ = [norm_issue(i) for i in issue_list(load(a.open_, {}) or {})]

    # 스코프 밖 이슈는 집계에서 제외하되 **조용히 버리지 않는다**. 한 건이라도
    # 나왔다면 조회 JQL이 이 폴더의 범위와 어긋났다는 뜻이고, 그대로 두면 다른
    # 폴더가 맡은 태스크를 중복 추적하게 된다. INV-5가 이 수를 보고 막는다.
    oos = sorted({i["key"] for i in (changed + open_)
                  if i["key"] and not in_scope(i, project_key, epic_key)})
    changed = [i for i in changed if in_scope(i, project_key, epic_key)]
    open_ = [i for i in open_ if in_scope(i, project_key, epic_key)]

    # 제목 필터는 스코프 위반이 아니다. 같은 에픽을 나눠 맡기로 한 결과이므로
    # 배치를 멈추지 않는다. 대신 제외된 키를 남겨, 어느 폴더도 맡지 않은
    # 태스크가 생겼을 때 사람이 알아볼 수 있게 한다.
    title_include = state.get("title_include") or []
    title_exclude = state.get("title_exclude") or []
    inc = compile_patterns(title_include, "title_include")
    exc = compile_patterns(title_exclude, "title_exclude")
    dropped = sorted({i["key"] for i in open_
                      if i["key"] and not title_match(i, inc, exc)})
    changed = [i for i in changed if title_match(i, inc, exc)]
    open_ = [i for i in open_ if title_match(i, inc, exc)]

    changed_keys = {i["key"] for i in changed if i["key"]}

    # 키 → state 로컬 ID 역인덱스
    by_jira = {v.get("jira_key"): k for k, v in roots_state.items() if v.get("jira_key")}

    epics, counts = {}, {"new": 0, "continuing": 0, "stalled": 0, "children_pending": 0}

    for iss in open_:
        if not iss["key"]:
            continue
        if iss["is_subtask"]:
            continue  # 파생은 root 아래에 붙는다 (§ 계층 3단)

        local_id = by_jira.get(iss["key"])
        if local_id is None:
            local_id = next_root_id(roots_state)
            roots_state[local_id] = {"jira_key": iss["key"], "epic": iss["parent_key"],
                                     "epic_name": iss["parent_summary"],
                                     "origin": "jira",
                                     "first_seen": today, "last_seen": today,
                                     "children": []}
            by_jira[iss["key"]] = local_id
            klass = "new"
        else:
            rs = roots_state[local_id]
            rs["last_seen"] = today
            rs["epic"] = iss["parent_key"] or rs.get("epic")
            rs["epic_name"] = iss["parent_summary"] or rs.get("epic_name")
            age = days_between(rs["first_seen"], today)
            klass = "stalled" if (age >= STALL_DAYS and iss["key"] not in changed_keys) \
                else "continuing"

        counts[klass] += 1
        rs = roots_state[local_id]
        pending = [c for c in rs.get("children", []) if not c.get("jira_key")]
        counts["children_pending"] += len(pending)

        chg = next((c for c in changed if c["key"] == iss["key"]), None)
        ek = iss["parent_key"] or "_NO_EPIC"
        ep = epics.setdefault(ek, {"epic_key": iss["parent_key"],
                                   "epic_name": iss["parent_summary"] or "(과제 미지정)",
                                   "roots": []})
        ep["roots"].append({
            "local_id": local_id, "key": iss["key"], "summary": iss["summary"],
            "status": iss["status"], "type": iss["type"], "priority": iss["priority"],
            "duedate": iss["duedate"], "class": klass,
            "age_days": days_between(rs["first_seen"], today),
            "changes": summarize_changes(chg) if chg else [],
            "children": rs.get("children", []),
        })

    # 정렬: 마감 임박 → 정체 → 우선순위 이름 → 키
    prio_rank = {"Highest": 0, "High": 1, "Medium": 2, "Low": 3, "Lowest": 4}
    for ep in epics.values():
        ep["roots"].sort(key=lambda r: (
            r["duedate"] or "9999-12-31",
            0 if r["class"] == "stalled" else 1,
            prio_rank.get(r["priority"], 9),
            r["key"],
        ))

    flat = [r for ep in epics.values() for r in ep["roots"]]
    flat.sort(key=lambda r: (r["duedate"] or "9999-12-31",
                             0 if r["class"] == "stalled" else 1,
                             prio_rank.get(r["priority"], 9), r["key"]))
    today_ids = [r["local_id"] for r in flat[:WIP_LIMIT]]

    agg = {
        "date": today,
        "watermark_in": state.get("watermark"),
        "generated_by": "aggregate.py",
        "wip_limit": WIP_LIMIT,
        "stall_days": STALL_DAYS,
        "today_local_ids": today_ids,
        "counts": counts,
        "scope": {"project_key": project_key, "epic_key": epic_key,
                  "epic_name": epic_scope_name,
                  "title_include": title_include, "title_exclude": title_exclude},
        "out_of_scope": {"count": len(oos), "keys": oos},
        "title_filtered": {"count": len(dropped), "keys": dropped},
        "epics": sorted(epics.values(), key=lambda e: (e["epic_name"] or "")),
        "all_issue_keys": sorted({r["key"] for r in flat}
                                 | {c["jira_key"] for r in flat
                                    for c in r["children"] if c.get("jira_key")}),
        "field_paths_used": dict(HITS),
    }

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(agg, f, ensure_ascii=False, indent=2)
    with open(a.state, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    scope_txt = project_key or "(프로젝트 미지정)"
    if epic_key:
        scope_txt += f" / 에픽 {epic_key}"
    if title_include or title_exclude:
        scope_txt += " / 제목필터"
    print(f"aggregate: [{scope_txt}] root {len(flat)}건 "
          f"(신규 {counts['new']} / 이어서 {counts['continuing']} / 정체 {counts['stalled']}), "
          f"미등록 파생 {counts['children_pending']}건 → {a.out}")
    if oos:
        print(f"  주의: 스코프 밖 {len(oos)}건을 제외했다 — {', '.join(oos[:5])}"
              f"{' 외' if len(oos) > 5 else ''}. 조회 JQL을 확인하십시오.")
    if dropped:
        print(f"  제목 필터로 {len(dropped)}건이 이 폴더에서 빠졌다: "
              f"{', '.join(dropped)}. 다른 폴더가 맡고 있는지 확인하십시오.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
