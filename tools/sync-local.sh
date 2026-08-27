#!/usr/bin/env bash
# 로컬 저장소의 현재 상태를 이 기기의 전역(user 스코프) 설치에 반영한다.
#
# 왜 필요한가: 마켓플레이스를 로컬 디렉터리로 등록해도 설치는 원본을 직접
# 물지 않고 캐시로 복사한다. 캐시 디렉터리는 **버전마다** 따로 만들어지므로,
# 버전이 그대로면 `claude plugin update` 는 "already at the latest version"
# 이라며 아무것도 하지 않는다. 그래서 반영하려면 버전을 올려야 한다.
# 이 스크립트가 그 세 곳을 한꺼번에 올리고 갱신까지 돌린다.
#
#   tools/sync-local.sh                 # 패치 버전 +1 후 반영
#   tools/sync-local.sh --version 0.6.0 # 버전을 지정해 반영
#   tools/sync-local.sh --skip-tests    # 시험 생략 (급할 때만)
#
# 시험을 기본으로 돌리는 이유: 소스가 로컬이면 커밋하지 않은 중간 상태도
# 그대로 전역에 반영된다. GitHub 를 거칠 때는 push 라는 관문이 있었지만
# 이제 없으므로, 그 자리를 회귀 시험이 대신한다.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
MARKET="jiraWF"
PLUGIN="jira-worklog"

SKIP_TESTS=0
SET_VERSION=""
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-tests) SKIP_TESTS=1; shift ;;
    --version)    SET_VERSION="${2:-}"; shift 2 ;;
    -h|--help)    sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "알 수 없는 인자: $1" >&2; exit 2 ;;
  esac
done

# ── python3 확보 ──────────────────────────────────────────────────────
# Windows 에서 python3 는 Microsoft Store 스텁인 경우가 많다. 아무것도
# 출력하지 않고 exit 49 를 내므로, 그대로 두면 시험이 전부 실패하는데
# 원인은 코드가 아니다. 여기서 셔임을 만들어 그 혼란을 막는다.
# 한글 메시지가 Windows 기본 코드페이지(cp949)에서 깨지지 않게 한다.
export PYTHONIOENCODING=utf-8

if ! python3 --version >/dev/null 2>&1; then
  if python --version >/dev/null 2>&1; then
    SHIM="$(mktemp -d)"
    printf '#!/usr/bin/env bash\nexec python "$@"\n' > "$SHIM/python3"
    chmod +x "$SHIM/python3"
    export PATH="$SHIM:$PATH"
    trap 'rm -rf "$SHIM"' EXIT
    echo "note: python3 가 없어 python 으로 가는 셔임을 임시로 만들었다."
  else
    echo "python3 도 python 도 실행할 수 없다. Python 3.8+ 를 설치하십시오." >&2
    exit 1
  fi
fi

# ── 1. 회귀 시험 ──────────────────────────────────────────────────────
if [ "$SKIP_TESTS" -eq 0 ]; then
  echo "== 회귀 시험 =="
  if ! bash "$ROOT/tests/run_tests.sh" >/tmp/sync_tests.$$ 2>&1; then
    tail -30 /tmp/sync_tests.$$
    rm -f /tmp/sync_tests.$$
    echo >&2
    echo "시험이 실패했다. 반영하지 않는다 — 로컬 소스는 관문이 없으므로" >&2
    echo "여기서 막지 않으면 깨진 상태가 그대로 전역에 나간다." >&2
    exit 1
  fi
  tail -1 /tmp/sync_tests.$$
  rm -f /tmp/sync_tests.$$
else
  echo "== 회귀 시험 생략 (--skip-tests) =="
fi

# ── 2. 버전 올리기 (세 곳) ────────────────────────────────────────────
NEW_VERSION="$(SET_VERSION="$SET_VERSION" python3 - "$ROOT" <<'PY'
import io, json, os, re, sys
root = sys.argv[1]
want = os.environ.get("SET_VERSION") or ""

pj = os.path.join(root, "plugins", "jira-worklog", ".claude-plugin", "plugin.json")
cur = json.load(io.open(pj, encoding="utf-8"))["version"]
if want:
    new = want
else:
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", cur)
    if not m:
        sys.stderr.write(f"버전 형식을 해석할 수 없다: {cur!r}\n")
        raise SystemExit(2)
    new = f"{m.group(1)}.{m.group(2)}.{int(m.group(3)) + 1}"

if not re.fullmatch(r"\d+\.\d+\.\d+", new):
    sys.stderr.write(f"버전 형식이 잘못됐다: {new!r}\n")
    raise SystemExit(2)

targets = [
    (pj, f'"version": "{cur}"', f'"version": "{new}"'),
    (os.path.join(root, ".claude-plugin", "marketplace.json"),
     f'"version": "{cur}"', f'"version": "{new}"'),
    (os.path.join(root, "plugins", "jira-worklog", "scripts", "render_daily.py"),
     f'default="{cur}"', f'default="{new}"'),
]
for path, old, rep in targets:
    s = io.open(path, encoding="utf-8").read()
    if old not in s:
        sys.stderr.write(f"{path} 에서 {old!r} 를 찾지 못했다. 세 곳의 버전이 "
                         "어긋나 있을 수 있다.\n")
        raise SystemExit(2)
    io.open(path, "w", encoding="utf-8", newline="\n").write(s.replace(old, rep, 1))
print(new)
PY
)" || exit 2
echo "== 버전 $NEW_VERSION =="

# ── 3. 매니페스트 검증 ────────────────────────────────────────────────
claude plugin validate "$ROOT/plugins/jira-worklog" >/dev/null 2>&1 \
  || { echo "plugin validate 실패" >&2; exit 1; }

# ── 4. 반영 ───────────────────────────────────────────────────────────
echo "== 반영 =="
claude plugin marketplace update "$MARKET"     || exit 1
claude plugin update "$PLUGIN@$MARKET"         || exit 1

echo
echo "완료. 실행 중인 세션은 재시작해야 새 버전을 읽는다."
