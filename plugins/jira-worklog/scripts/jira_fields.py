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
    """MCP 응답 최상위에서 이슈 배열을 찾아낸다."""
    if isinstance(raw, list):
        return raw
    for path in ("issues", "results", "data.issues", "structuredContent.issues"):
        v = dig(raw, path)
        if isinstance(v, list):
            return v
    return []
