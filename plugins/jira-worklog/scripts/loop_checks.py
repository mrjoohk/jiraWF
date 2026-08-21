#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""런타임 불변식 검사 (INV-1~4).

INV-4가 이 파일의 존재 이유다: 서브에이전트가 일지를 쓰면서 이슈 키를
지어내거나 잘못 옮기면 사람 눈에 띄지 않고, 몇 달 뒤 일지를 되짚을 때야
발견되며 그때는 원인을 재구성할 수 없다. 원본 대조가 유일한 방어선이다.

  loop_checks.py --daily daily/260814.md --agg .workflow/agg/260814.json \
                 --state .workflow/state.json [--evidence logs/xxx.json]

종료 코드 0 = 통과, 1 = 위반.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
LOCAL_RE = re.compile(r"\bR-\d{3}(?:\.\d+)?\b")


def load(p, d=None):
    return json.load(open(p, encoding="utf-8")) if p and os.path.exists(p) else d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", required=True)
    ap.add_argument("--agg", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--evidence")
    a = ap.parse_args()

    v = []
    agg = load(a.agg, {}) or {}
    state = load(a.state, {}) or {}
    workdir = os.path.dirname(a.state) or "."

    # INV-1 — 사람이 쓴 진행 메모가 보존되었는가
    rec = load(os.path.join(workdir, "last_render.json"), {}) or {}
    if rec.get("memo_existed_before") and \
            rec.get("memo_hash_before") != rec.get("memo_hash_after"):
        v.append("[INV-1] 진행 메모가 변경되었다 "
                 f"({rec.get('memo_hash_before')} → {rec.get('memo_hash_after')}). "
                 "일일 배치는 메모 구획을 덮으면 안 된다.")

    # INV-2 — watermark 단조 증가
    hist_path = os.path.join(workdir, "watermark_history.json")
    hist = load(hist_path, []) or []
    wm = state.get("watermark")
    if wm:
        if hist and wm < hist[-1]:
            v.append(f"[INV-2] watermark 후퇴: {hist[-1]} → {wm}. "
                     "후퇴는 중복 수집 또는 구간 유실을 뜻한다.")
        if not hist or hist[-1] != wm:
            hist.append(wm)
            json.dump(hist[-200:], open(hist_path, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)

    # INV-3 — 승인 기록 없는 push 없음
    for lid, root in (state.get("roots") or {}).items():
        for c in root.get("children") or []:
            if c.get("jira_key") and not c.get("approved_at"):
                v.append(f"[INV-3] {c.get('local_id', lid)} 가 {c['jira_key']} 로 "
                         "push 되었으나 승인 기록(approved_at)이 없다.")

    # INV-4 — 일지가 언급한 이슈 키가 원본 집계에 실재하는가
    known = set(agg.get("all_issue_keys") or [])
    for r in (state.get("roots") or {}).values():
        if r.get("jira_key"):
            known.add(r["jira_key"])
        if r.get("epic"):
            known.add(r["epic"])
        for c in r.get("children") or []:
            if c.get("jira_key"):
                known.add(c["jira_key"])
    text = open(a.daily, encoding="utf-8").read() if os.path.exists(a.daily) else ""
    # 진행 메모는 사람이 쓴 원문이므로 검사 대상에서 제외한다.
    text = re.sub(r"<!-- MEMO:START -->.*?<!-- MEMO:END -->", "", text, flags=re.S)
    mentioned = set(KEY_RE.findall(text)) - {"XXX-0"}
    ghosts = sorted(k for k in mentioned if k not in known)
    if ghosts:
        v.append(f"[INV-4] 일지가 원본에 없는 이슈 키를 언급한다: {', '.join(ghosts)}. "
                 "서브에이전트의 환각 또는 오타일 수 있다.")

    ev = {
        "tool": "loop_checks",
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "daily": a.daily,
        "checked": {"INV-1": "memo_preserved", "INV-2": "watermark_monotonic",
                    "INV-3": "push_has_approval", "INV-4": "issue_keys_exist"},
        "keys_mentioned": sorted(mentioned),
        "keys_known": len(known),
        "violations": v,
        "exit_code": 1 if v else 0,
    }
    out = a.evidence or os.path.join(workdir, "loop_checks_last.json")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    json.dump(ev, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    for m in v:
        print("  위반: " + m)
    print(f"loop_checks: 위반 {len(v)}건 · evidence {out}")
    return 1 if v else 0


if __name__ == "__main__":
    sys.exit(main())
