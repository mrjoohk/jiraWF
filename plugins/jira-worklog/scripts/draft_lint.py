#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""초안의 본문이 '문서'가 되어 가는지 본다.

Jira 태스크 설명과 코멘트는 팀에 사실을 공유하는 자리다. 그런데 재료가
많으면 초안은 자연스럽게 목차와 표를 갖춘 보고서로 자란다. 그렇게 자란
본문은 읽는 사람이 인과를 다시 찾아내야 하고, 대개 아무도 안 읽는다.

문체는 기계가 판정할 수 없다. 대신 **문서로 자랐다는 표시**는 셀 수 있다 —
머리말, 표, 길이. 이 셋을 세어 승인 전에 사람에게 보인다.

  draft_lint.py --draft .workflow/draft.json
  draft_lint.py --draft ... --json

종료 코드
  0  지적 없음
  1  지적 있음 — **중단이 아니라 경고다.** 승인 표에 함께 보이고, 사람이
     그대로 승인할 수 있다. 문체는 판단이지 규칙 위반이 아니기 때문이다.
  2  파일을 읽을 수 없음 (설정 오류)
"""
import argparse
import json
import re
import sys

MAX_CHARS = 600            # 이보다 길면 대개 배경 설명이 들어와 있다
HEADING = re.compile(r"^#{1,6}\s", re.M)
TABLE_SEP = re.compile(r"^\s*\|?[\s:-]*\|[\s:|-]*$", re.M)
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.M)


def _utf8_stdout():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def lint(text, where):
    """한 덩어리 본문에서 '문서로 자란 표시'를 센다."""
    out = []
    if not text:
        return out
    heads = HEADING.findall(text)
    if heads:
        out.append({"where": where, "kind": "heading", "count": len(heads),
                    "hint": "머리말(##)은 목차가 필요할 만큼 길다는 뜻이다. "
                            "인과 문장으로 펴거나 상세 문서로 링크한다."})
    rows = TABLE_ROW.findall(text)
    if TABLE_SEP.search(text) or len(rows) >= 2:
        out.append({"where": where, "kind": "table", "count": len(rows),
                    "hint": "표는 값을 나열할 뿐 인과를 말하지 않는다. "
                            "중요한 수치만 문장에 넣고 나머지는 근거 경로로."})
    if len(text) > MAX_CHARS:
        out.append({"where": where, "kind": "length", "count": len(text),
                    "hint": f"{MAX_CHARS}자를 넘는다. 배경 설명이 섞였는지 본다."})
    return out


def main():
    _utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    try:
        with open(a.draft, encoding="utf-8") as f:
            draft = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"초안을 읽을 수 없다: {a.draft} — {exc}", file=sys.stderr)
        return 2

    findings = []
    for i, r in enumerate(draft.get("new_roots") or []):
        tag = r.get("ref") or r.get("local_id") or f"new_roots[{i}]"
        findings += lint(r.get("description"), f"{tag} 설명")
    for i, c in enumerate(draft.get("comments") or []):
        tag = c.get("jira_key") or c.get("local_id") or f"comments[{i}]"
        findings += lint(c.get("body"), f"{tag} 코멘트")
    for i, c in enumerate(draft.get("children") or []):
        tag = c.get("local_id") or c.get("summary") or f"children[{i}]"
        findings += lint(c.get("description"), f"{tag} 설명")

    if a.json:
        json.dump({"count": len(findings), "findings": findings},
                  sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 1 if findings else 0

    if not findings:
        print("draft_lint: 지적 없음")
        return 0

    print(f"draft_lint: {len(findings)}건 — 승인 전에 확인하십시오 (중단 아님)")
    for f in findings:
        print(f"  [{f['kind']}] {f['where']} ({f['count']}) — {f['hint']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
