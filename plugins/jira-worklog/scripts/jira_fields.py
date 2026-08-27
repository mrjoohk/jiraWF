#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MCP 응답에서 Jira 필드를 관대하게 추출한다.

왜 관대해야 하는가: MCP 래퍼는 Jira REST 응답을 그대로 넘기기도 하고
평탄화하기도 한다. 필드 경로를 하나로 못박으면 첫 실행에서 조용히 빈 값이
나오고, 그것이 일지에 "변동 없음"으로 기록된다 — 가장 나쁜 실패 모드다.
그래서 여러 경로를 시도하고, **실제로 적중한 경로를 기록**해 첫 실행 후
사람이 확인할 수 있게 한다.
"""
HITS = {}

# state.json 이 이 값과 다르면 스크립트는 **실행하지 않고 중단한다.**
# DESIGN 은 처음부터 그렇게 못박았지만 오래도록 문서에만 있었고, 실제로는
# 에이전트가 문서를 지키는 데 전적으로 의존했다(대장 F-014). 구스키마에
# 대고 마감까지 가면 roots 구조가 어긋난 채 티켓이 생성되는데, 그것은
# 되돌리기 가장 비싼 실패다. 그래서 여기서 기계가 막는다.
SCHEMA_VERSION = 3


def require_schema(state, where=""):
    """state.json 의 schema_version 을 확인하고, 다르면 종료 코드 2로 중단한다.

    2를 쓰는 이유: 1은 "검사 위반"(다시 돌리면 될 수도 있다)이고 2는
    "설정이 깨졌다"(사람이 고치기 전에는 뭘 해도 소용없다)이다. 대처가
    다르므로 코드로 구별한다.
    """
    import sys as _sys
    got = state.get("schema_version")
    if got == SCHEMA_VERSION:
        return
    src = f" ({where})" if where else ""
    print(f"schema_version 불일치{src}: 기대 {SCHEMA_VERSION}, 실제 {got!r}. "
          "구스키마로 계속 도는 것보다 멈추는 편이 복구 가능하다 — "
          "/wf-init 을 다시 실행해 마이그레이션하십시오.", file=_sys.stderr)
    raise SystemExit(2)


def dig(obj, *paths, default=None, tag=None):
    """dotted path 여러 개를 순서대로 시도해 첫 non-None 값을 반환."""
    for path in paths:
        cur = obj
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur is not None:
            if tag:
                HITS.setdefault(tag, path)
            return cur
    if tag:
        HITS.setdefault(tag, "<none>")
    return default


def norm_issue(d):
    """Jira 이슈 dict → 이 워크플로우가 쓰는 평탄한 형태."""
    parent_key = dig(d, "fields.parent.key", "parent.key", "parentKey", tag="parent")
    return {
        "key": dig(d, "key", "issueKey", tag="key"),
        "summary": dig(d, "fields.summary", "summary", default="", tag="summary"),
        "status": dig(d, "fields.status.name", "status.name", "status",
                      default="", tag="status"),
        "status_category": dig(d, "fields.status.statusCategory.key",
                               "status.statusCategory.key", default="", tag="status_category"),
        "type": dig(d, "fields.issuetype.name", "issuetype.name", "issueType",
                    default="", tag="type"),
        "is_subtask": bool(dig(d, "fields.issuetype.subtask", "issuetype.subtask",
                               default=False, tag="is_subtask")),
        "parent_key": parent_key,
        "parent_summary": dig(d, "fields.parent.fields.summary", "parent.fields.summary",
                              "parent.summary", default="", tag="parent_summary"),
        "updated": dig(d, "fields.updated", "updated", default="", tag="updated"),
        "duedate": dig(d, "fields.duedate", "duedate", tag="duedate"),
        "priority": dig(d, "fields.priority.name", "priority.name", default="", tag="priority"),
        "labels": dig(d, "fields.labels", "labels", default=[], tag="labels"),
        "changelog": dig(d, "changelog.histories", "changelog", default=[], tag="changelog"),
        "comments": dig(d, "fields.comment.comments", "comments", default=[], tag="comments"),
    }


def issue_list(raw):
    """MCP 응답 최상위에서 이슈 배열을 찾아낸다.

    형태가 최소 두 갈래다. 평탄한 ``{"issues": [...]}`` 와, 커넥터가 한 겹
    감싼 ``{"issues": {"nodes": [...]}}``. 후자를 놓치면 조회는 성공했는데
    집계가 0건이 되고 일지에는 "변동 없음"으로 남는다 — 이 모듈이 막으려는
    바로 그 실패 모드이므로, 감싼 형태도 함께 본다.

    적중한 경로는 ``HITS["issue_list"]``에 남는다. 0건이 나왔을 때
    조회 문제인지 경로 문제인지 가르는 근거다.
    """
    if isinstance(raw, list):
        HITS.setdefault("issue_list", "<root list>")
        return raw
    for path in ("issues", "results", "data.issues",
                 "structuredContent.issues", "nodes"):
        v = dig(raw, path)
        if isinstance(v, list):
            HITS.setdefault("issue_list", path)
            return v
        if isinstance(v, dict) and isinstance(v.get("nodes"), list):
            HITS.setdefault("issue_list", path + ".nodes")
            return v["nodes"]
    HITS.setdefault("issue_list", "<none>")
    return []
