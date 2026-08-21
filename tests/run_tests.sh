#!/usr/bin/env bash
# 회귀 시험 — 각 검사는 "되돌리면 실패하는" 음성 대조 형태다.
# 검사가 무력화되면 이 시험이 깨진다. 통과만으로는 아무것도 증명하지 못하므로,
# 정상 경로 1건 + 위반을 주입한 음성 대조 5건을 함께 돌린다.
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
