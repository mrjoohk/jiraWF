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
from jira_fields import HITS, issue_list, norm_issue  # noqa: E402

STALL_DAYS = 7
WIP_LIMIT = 3


def load(path, default=None):
    if not path or not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def days_between(a, b):
    fmt = "%Y-%m-%d"
    return (dt.datetime.strptime(b, fmt) - dt.datetime.strptime(a, fmt)).days


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
    ap = argparse.ArgumentParser()
    ap.add_argument("--changes", required=True)
    ap.add_argument("--open", dest="open_", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--date", required=True)
    a = ap.parse_args()

    state = load(a.state, {}) or {}
    roots_state = state.setdefault("roots", {})
    today = a.date

    changed = [norm_issue(i) for i in issue_list(load(a.changes, {}) or {})]
    open_ = [norm_issue(i) for i in issue_list(load(a.open_, {}) or {})]
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

    print(f"aggregate: root {len(flat)}건 "
          f"(신규 {counts['new']} / 이어서 {counts['continuing']} / 정체 {counts['stalled']}), "
          f"미등록 파생 {counts['children_pending']}건 → {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
