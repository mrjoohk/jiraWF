#!/usr/bin/env bash
# 회귀 시험 — 각 검사는 "되돌리면 실패하는" 음성 대조 형태다.
# 검사가 무력화되면 이 시험이 깨진다. 통과만으로는 아무것도 증명하지 못하므로,
# 정상 경로와, 위반·계약 위반을 주입한 음성 대조를 함께 돌린다.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
S="$ROOT/plugins/jira-worklog/scripts"
W="$(mktemp -d)"
PASS=0; FAIL=0

ok()   { echo "  PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }
check(){ # check <이름> <기대 exit> <실제 exit>
  if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (기대 exit=$2, 실제 exit=$3)"; fi
}

setup() {
  rm -rf "$W"; mkdir -p "$W/.workflow/agg" "$W/daily"
  cp "$HERE/fixtures/state.fixture.json" "$W/.workflow/state.json"
}

run_pipeline() {
  python3 "$S/aggregate.py" --changes "$HERE/fixtures/raw_changes.json" \
    --open "$HERE/fixtures/raw_open.json" --state "$W/.workflow/state.json" \
    --out "$W/.workflow/agg/260814.json" --date 2026-08-14 >/dev/null
  python3 "$S/render_daily.py" --agg "$W/.workflow/agg/260814.json" \
    --narrative "$HERE/fixtures/narrative.md" --out "$W/daily/260814.md" \
    --state "$W/.workflow/state.json" >/dev/null
}
checks() {
  python3 "$S/loop_checks.py" --daily "$W/daily/260814.md" \
    --agg "$W/.workflow/agg/260814.json" --state "$W/.workflow/state.json" >/dev/null 2>&1
  echo $?
}

echo "== 정상 경로 =="
setup; run_pipeline
check "정상 파이프라인은 통과한다" 0 "$(checks)"

# 집계 결과가 기대와 맞는가 (결정성)
python3 - "$W/.workflow/agg/260814.json" <<'PY'
import json,sys
a=json.load(open(sys.argv[1],encoding='utf-8'))
c=a["counts"]
assert c["new"]==1 and c["continuing"]==1 and c["stalled"]==1, c
assert c["children_pending"]==1, c
assert a["today_local_ids"][0]=="R-001", a["today_local_ids"]
PY
check "집계 분류가 기대값과 일치한다" 0 "$?"

echo "== 음성 대조: 검사를 무력화하면 실패해야 한다 =="

setup; run_pipeline
sed -i 's/코멘트 2건 추가./코멘트 2건 추가. 관련 XXX-999./' "$W/daily/260814.md"
check "INV-4: 원본에 없는 이슈 키를 잡는다" 1 "$(checks)"

setup; run_pipeline
python3 - "$W/daily/260814.md" <<'PY'
import sys
p=sys.argv[1]; s=open(p,encoding='utf-8').read()
s=s.replace('<!-- MEMO:END -->','- 사람 메모: XXX-777 확인\n<!-- MEMO:END -->')
open(p,'w',encoding='utf-8').write(s)
PY
check "INV-4: 진행 메모 안의 키는 검사하지 않는다" 0 "$(checks)"

setup; run_pipeline
python3 - "$W/.workflow/last_render.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p,encoding='utf-8'))
d["memo_existed_before"]=True; d["memo_hash_before"]="0000000000000000"
json.dump(d,open(p,"w",encoding="utf-8"))
PY
check "INV-1: 진행 메모 변경을 잡는다" 1 "$(checks)"

setup; run_pipeline
# INV-2는 이력 대비 후퇴를 본다. 먼저 1회 검사해 이력을 만든 뒤 후퇴시킨다.
checks >/dev/null
python3 - "$W/.workflow/state.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p,encoding='utf-8')); d["watermark"]="2026-08-01T13:00:00+09:00"
json.dump(d,open(p,"w",encoding="utf-8"),ensure_ascii=False)
PY
check "INV-2: watermark 후퇴를 잡는다" 1 "$(checks)"
check "INV-2: 이력이 없는 첫 실행은 통과한다 (후퇴 아님)" 0 "$(setup; run_pipeline; checks)"

setup; run_pipeline
python3 - "$W/.workflow/state.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p,encoding='utf-8'))
d["roots"]["R-001"]["children"][1]["jira_key"]="XXX-160"
json.dump(d,open(p,"w",encoding="utf-8"),ensure_ascii=False)
PY
check "INV-3: 승인 없는 push를 잡는다" 1 "$(checks)"

echo "== 응답 형태 =="
# 커넥터가 {"issues": {"nodes": [...]}} 로 감싸 보내는 경우. 이 경로를 놓치면
# 조회는 성공했는데 집계가 0건이 되고 일지에는 "변동 없음"으로 남는다.
setup
python3 "$S/aggregate.py" --changes "$HERE/fixtures/raw_changes_wrapped.json" \
  --open "$HERE/fixtures/raw_open_wrapped.json" --state "$W/.workflow/state.json" \
  --out "$W/.workflow/agg/260814.json" --date 2026-08-14 >/dev/null
python3 - "$W/.workflow/agg/260814.json" <<'PY'
import json,sys
a=json.load(open(sys.argv[1],encoding='utf-8'))
c=a["counts"]
assert c["new"]==1 and c["continuing"]==1 and c["stalled"]==1, c
assert a["field_paths_used"].get("issue_list")=="issues.nodes", a["field_paths_used"]
PY
check "감싼 응답도 평탄한 응답과 같게 집계된다" 0 "$?"

echo "== INV-5: 에픽 스코프 =="
# 폴더마다 프로젝트·에픽이 다르다. 조회에서 에픽 필터가 빠지면 옆 폴더가 맡은
# 태스크까지 끌어오고, 그대로 마감하면 하위 티켓이 두 번 생긴다.
set_epic() {
  python3 - "$W/.workflow/state.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p,encoding='utf-8'))
d["epic_key"]="XXX-180"; d["epic_name"]="센서 캘리브레이션"
json.dump(d,open(p,"w",encoding="utf-8"),ensure_ascii=False)
PY
}

setup; set_epic
python3 "$S/aggregate.py" --changes "$HERE/fixtures/raw_changes_epic.json" \
  --open "$HERE/fixtures/raw_open_epic.json" --state "$W/.workflow/state.json" \
  --out "$W/.workflow/agg/260814.json" --date 2026-08-14 >/dev/null
python3 "$S/render_daily.py" --agg "$W/.workflow/agg/260814.json" \
  --narrative "$HERE/fixtures/narrative.md" --out "$W/daily/260814.md" \
  --state "$W/.workflow/state.json" >/dev/null
check "스코프에 맞게 좁혀 수집하면 통과한다" 0 "$(checks)"

setup; set_epic; run_pipeline
check "스코프 밖까지 수집하면 잡는다 (에픽 필터 누락)" 1 "$(checks)"

setup; run_pipeline; set_epic
check "집계 후 스코프를 바꾸면 잡는다 (재집계 누락)" 1 "$(checks)"

echo "== 제목 필터: 같은 에픽을 나눠 맡기 =="
# 두 폴더가 같은 에픽을 맡되 제목으로 가른다. include 쪽과 exclude 쪽이
# 합쳐서 빠짐없이 덮는지가 핵심이다 — 둘 다 include만 쓰면 어느 쪽에도
# 안 걸리는 태스크가 조용히 사라진다.
set_title() {  # set_title <json배열 include> <json배열 exclude>
  INC="$1" EXC="$2" python3 - "$W/.workflow/state.json" <<'PY'
import json,os,sys
p=sys.argv[1]; d=json.load(open(p,encoding='utf-8'))
d["epic_key"]="XXX-180"; d["epic_name"]="센서 캘리브레이션"
d["title_include"]=json.loads(os.environ["INC"])
d["title_exclude"]=json.loads(os.environ["EXC"])
json.dump(d,open(p,"w",encoding="utf-8"),ensure_ascii=False)
PY
}
agg_epic() {
  python3 "$S/aggregate.py" --changes "$HERE/fixtures/raw_changes_epic.json" \
    --open "$HERE/fixtures/raw_open_epic.json" --state "$W/.workflow/state.json" \
    --out "$W/.workflow/agg/260814.json" --date 2026-08-14 >/dev/null 2>&1
}
keys_of() {  # 집계에 남은 root 키
  python3 - "$W/.workflow/agg/260814.json" <<'PY'
import json,sys
a=json.load(open(sys.argv[1],encoding='utf-8'))
print(",".join(sorted(r["key"] for e in a["epics"] for r in e["roots"])))
PY
}

setup; set_title '["문서화"]' '[]'; agg_epic
got=$(keys_of)
if [ "$got" = "XXX-188" ]; then ok "include가 걸린 건만 맡는다"; else bad "include (실제 '$got')"; fi

setup; set_title '[]' '["문서화"]'; agg_epic
got2=$(keys_of)
if [ "$got2" = "XXX-201" ]; then ok "exclude가 나머지를 맡는다"; else bad "exclude (실제 '$got2')"; fi

if [ "$got,$got2" = "XXX-188,XXX-201" ]; then
  ok "include 쪽과 exclude 쪽이 합쳐 에픽을 빠짐없이 덮는다"
else
  bad "상보성 깨짐 ('$got' + '$got2')"
fi

setup; set_title '["문서화"]' '[]'; agg_epic
python3 - "$W/.workflow/agg/260814.json" <<'PY'
import json,sys
a=json.load(open(sys.argv[1],encoding='utf-8'))
t=a["title_filtered"]
assert t["count"]==1 and t["keys"]==["XXX-201"], t
assert a["out_of_scope"]["count"]==0, a["out_of_scope"]   # 제목 제외는 스코프 위반이 아니다
PY
check "제목으로 빠진 건이 기록에 남는다 (스코프 위반은 아니다)" 0 "$?"

setup; set_title '["문서화"]' '[]'; agg_epic
python3 "$S/render_daily.py" --agg "$W/.workflow/agg/260814.json" \
  --narrative "$HERE/fixtures/narrative.md" --out "$W/daily/260814.md" \
  --state "$W/.workflow/state.json" >/dev/null
check "제목 필터가 걸려 있어도 검사를 통과한다" 0 "$(checks)"

setup; set_title '["문서화"]' '[]'; agg_epic
python3 "$S/render_daily.py" --agg "$W/.workflow/agg/260814.json" \
  --narrative "$HERE/fixtures/narrative.md" --out "$W/daily/260814.md" \
  --state "$W/.workflow/state.json" >/dev/null
set_title '["온도"]' '[]'
check "제목 필터를 바꾸고 재집계 안 하면 잡는다" 1 "$(checks)"

setup; set_title '["([미완성"]' '[]'
python3 "$S/aggregate.py" --changes "$HERE/fixtures/raw_changes_epic.json" \
  --open "$HERE/fixtures/raw_open_epic.json" --state "$W/.workflow/state.json" \
  --out "$W/.workflow/agg/260814.json" --date 2026-08-14 >/dev/null 2>&1
check "잘못된 정규식은 조용히 넘어가지 않고 중단한다" 2 "$?"

echo "== 파생 레코드 계약 =="
# 마감(worklog-close)이 state에 파생을 기록할 때 local_id 같은 필드를 빼면,
# 다음 일일 배치의 render_daily가 그 필드를 직접 읽다가 죽는다. 문서로만
# 막으면 같은 사고가 다시 난다 — 조용히 빠뜨리는 대신 크게 실패하는지를
# 기계가 확인한다.
setup
python3 - "$W/.workflow/state.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p,encoding='utf-8'))
d["roots"]["R-001"]["children"][1].pop("local_id")
json.dump(d,open(p,"w",encoding="utf-8"),ensure_ascii=False)
PY
python3 "$S/aggregate.py" --changes "$HERE/fixtures/raw_changes.json" \
  --open "$HERE/fixtures/raw_open.json" --state "$W/.workflow/state.json" \
  --out "$W/.workflow/agg/260814.json" --date 2026-08-14 >/dev/null 2>&1
python3 "$S/render_daily.py" --agg "$W/.workflow/agg/260814.json" \
  --narrative "$HERE/fixtures/narrative.md" --out "$W/daily/260814.md" \
  --state "$W/.workflow/state.json" >/dev/null 2>&1
check "파생 레코드에 local_id가 없으면 조용히 넘어가지 않고 실패한다" 1 "$?"

echo "== 신규 태스크: 제목 확정과 범위 검사 =="
# Jira에 없던 일을 메인 태스크로 만들 때, 제목이 이 폴더의 범위에 안 들어오면
# 티켓은 Jira에 남고 이 폴더 일지에서는 사라진다. 만들기 전에 막아야 한다.
set_prefix() {  # set_prefix <include정규식> <접두사>
  INC="$1" PRE="$2" python3 - "$W/.workflow/state.json" <<'PY'
import json,os,sys
p=sys.argv[1]; d=json.load(open(p,encoding='utf-8'))
d["epic_key"]="XXX-180"; d["title_include"]=[os.environ["INC"]]
d["title_exclude"]=[]; d["title_prefix"]=os.environ["PRE"] or None
json.dump(d,open(p,"w",encoding="utf-8"),ensure_ascii=False)
PY
}
title_of() { python3 "$S/new_title.py" --state "$W/.workflow/state.json" --summary "$1" 2>/dev/null; }

setup; set_prefix '\[PFD\]' '[PFD]'
got=$(title_of "케이블 재고 확인")
if [ "$got" = "[PFD] 케이블 재고 확인" ]; then ok "접두사를 붙여 확정한다"; else bad "접두사 (실제 '$got')"; fi

setup; set_prefix '\[PFD\]' '[PFD]'
got=$(title_of "[PFD] 이미 붙어 있음")
if [ "$got" = "[PFD] 이미 붙어 있음" ]; then ok "이미 붙어 있으면 두 번 붙이지 않는다"; else bad "중복 접두사 (실제 '$got')"; fi

setup; set_prefix '\[PFD\]' ''
python3 "$S/new_title.py" --state "$W/.workflow/state.json" --summary "접두사 없이" >/dev/null 2>&1
check "필터는 있는데 접두사가 없으면 막는다" 2 "$?"

setup; set_prefix '\[PFD\]' '[SCH]'
python3 "$S/new_title.py" --state "$W/.workflow/state.json" --summary "엉뚱한 접두사" >/dev/null 2>&1
check "접두사가 필터와 안 맞으면 막는다" 2 "$?"

setup; set_prefix '\[PFD\]' '[PFD]'
got=$(python3 "$S/new_title.py" --state "$W/.workflow/state.json" --summary "회귀 시험 추가" --kind subtask 2>/dev/null)
if [ "$got" = "회귀 시험 추가" ]; then ok "파생은 접두사 대상이 아니다"; else bad "파생 면제 (실제 '$got')"; fi

setup; set_prefix '\[PFD\]' '[PFD]'
err=$(python3 "$S/new_title.py" --state "$W/.workflow/state.json" --summary "[긴급] 서버 점검" 2>&1 >/dev/null)
out=$(title_of "[긴급] 서버 점검")
if [ "$out" = "[PFD] [긴급] 서버 점검" ] && [ -n "$err" ]; then
  ok "다른 대괄호 표시가 있으면 경고하되 막지는 않는다"
else
  bad "대괄호 경고 (제목 '$out', 경고 '$err')"
fi

setup
got=$(title_of "필터 없는 폴더의 새 태스크")
if [ "$got" = "필터 없는 폴더의 새 태스크" ]; then ok "필터가 없으면 제목을 그대로 쓴다"; else bad "무필터 (실제 '$got')"; fi

echo "== INV-3: 마감이 만든 메인 태스크 =="
setup; run_pipeline
python3 - "$W/.workflow/state.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p,encoding='utf-8'))
d["roots"]["R-009"]={"jira_key":"XXX-900","epic":"XXX-180","origin":"local",
                     "first_seen":"2026-08-14","last_seen":"2026-08-14","children":[]}
json.dump(d,open(p,"w",encoding="utf-8"),ensure_ascii=False)
PY
check "승인 기록 없이 만들어진 메인 태스크를 잡는다" 1 "$(checks)"

setup; run_pipeline
python3 - "$W/.workflow/state.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p,encoding='utf-8'))
d["roots"]["R-009"]={"jira_key":"XXX-900","epic":"XXX-180","origin":"local",
                     "approved_at":"2026-08-14T18:00:00+09:00",
                     "first_seen":"2026-08-14","last_seen":"2026-08-14","children":[]}
json.dump(d,open(p,"w",encoding="utf-8"),ensure_ascii=False)
PY
check "승인 기록이 있으면 통과한다" 0 "$(checks)"

setup; run_pipeline
python3 - "$W/.workflow/state.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p,encoding='utf-8'))
d["roots"]["R-009"]={"jira_key":"XXX-900","epic":"XXX-180","origin":"local",
                     "approved_at":"2026-08-14T18:00:00+09:00","jira_key":None,
                     "first_seen":"2026-08-14","last_seen":"2026-08-14","children":[]}
json.dump(d,open(p,"w",encoding="utf-8"),ensure_ascii=False)
PY
check "승인만 되고 아직 안 만들어진 것은 위반이 아니다" 0 "$(checks)"

echo "== 병합 후보 목록 =="
# 초안이 "신규"라고 한 항목을 기존 태스크에 붙일 때 고를 목록.
# 집계 파일에는 이 폴더 범위의 내 미완료 root만 있으므로 따로 조회해 추린다.
set_targets() {
  python3 - "$W/.workflow/state.json" <<'PY'
import json,sys
p=sys.argv[1]; d=json.load(open(p,encoding='utf-8'))
d["epic_key"]="XXX-180"; d["title_include"]=["문서화"]; d["title_exclude"]=[]
json.dump(d,open(p,"w",encoding="utf-8"),ensure_ascii=False)
PY
}
targets_json() {
  python3 "$S/merge_targets.py" --state "$W/.workflow/state.json" \
    --issues "$HERE/fixtures/raw_epic_targets.json" --json
}

setup; set_targets
targets_json > "$W/targets.json"
python3 - "$W/targets.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1],encoding='utf-8'))
keys=[t["key"] for t in d["targets"]]
assert "XXX-155" not in keys, "파생이 후보에 들어갔다"
assert d["count"]==4, d["count"]
PY
check "파생(Sub-task)은 병합 후보가 아니다" 0 "$?"

setup; set_targets; targets_json > "$W/targets.json"
python3 - "$W/targets.json" <<'PY'
import json,sys
t=json.load(open(sys.argv[1],encoding='utf-8'))["targets"]
by={x["key"]:x for x in t}
assert by["XXX-188"]["in_scope"] is True, by["XXX-188"]
assert by["XXX-201"]["in_scope"] is False, by["XXX-201"]   # 제목 필터 밖
assert by["XXX-300"]["done"] is True, by["XXX-300"]
PY
check "범위 안팎과 완료 여부를 표시한다" 0 "$?"

setup; set_targets; targets_json > "$W/targets.json"
python3 - "$W/targets.json" <<'PY'
import json,sys
t=json.load(open(sys.argv[1],encoding='utf-8'))["targets"]
order=[x["key"] for x in t]
# 범위 안·미완료를 갱신 역순으로 먼저, 그다음 범위 안·완료, 마지막이 범위 밖
assert order==["XXX-310","XXX-188","XXX-300","XXX-201"], order
assert [x["index"] for x in t]==[1,2,3,4]
PY
check "범위 안·미완료를 먼저, 범위 밖을 마지막에 놓는다" 0 "$?"

echo "== 메모 보존 =="
setup; run_pipeline
python3 - "$W/daily/260814.md" <<'PY'
import sys
p=sys.argv[1]; s=open(p,encoding='utf-8').read()
s=s.replace('<!-- MEMO:END -->','- [BUG] 사람이 쓴 메모\n<!-- MEMO:END -->')
open(p,'w',encoding='utf-8').write(s)
PY
run_pipeline
if grep -q "사람이 쓴 메모" "$W/daily/260814.md"; then
  ok "재실행해도 진행 메모가 보존된다"
else
  bad "재실행 시 진행 메모가 유실됨"
fi

rm -rf "$W"
echo
echo "결과: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
