#!/usr/bin/env bash
# Push new strategy files to the production host and register them, without a full deploy.
#
#   deploy/sync-strategies.sh                     # every new strategies/*.yaml -> root@hesynth.xyz
#   deploy/sync-strategies.sh -n                  # dry run: show what would be sent, change nothing
#   deploy/sync-strategies.sh orb5_*.yaml         # only these files (names or globs inside strategies/)
#   deploy/sync-strategies.sh --watch             # also put their tickers on the paper watchlist
#   deploy/sync-strategies.sh -y --host root@1.2.3.4
#
# The host defaults to $LLAMAOS_HOST, else root@hesynth.xyz.
#
# Strategy files are immutable once registered (the registry keys on each file's SHA-256), so this
# only ADDS files. A file that exists on the server with different content is refused: save the
# change as a new version (name.v2.yaml with version: 2). Files the server wrote (drafts from the
# compiler or Idea Lab) are never touched.
#
# New files are registered as drafts and validated inside the running api container, i.e. against
# the live risk controls and the code that is deployed. A file that uses a builtin added after the
# last deploy shows up as an error until you deploy the code (push to main, or deploy/deploy.sh).
set -euo pipefail

HOST=${LLAMAOS_HOST:-root@hesynth.xyz}
DIR=/opt/llamaos
DRY=0 YES=0 WATCH=0
PATTERNS=()
while [ $# -gt 0 ]; do
  case "$1" in
    -n|--dry-run) DRY=1 ;;
    -y|--yes) YES=1 ;;
    --watch) WATCH=1 ;;
    --host) HOST=${2:?--host needs user@host}; shift ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown option: $1 (see --help)"; exit 2 ;;
    *) PATTERNS+=("${1#strategies/}") ;;
  esac
  shift
done
case "$HOST" in *@*) ;; *) HOST="root@$HOST" ;; esac
cd "$(dirname "$0")/.."
PY=.venv/bin/python; [ -x "$PY" ] || PY=python3

say() { echo -e "\033[1;36m==>\033[0m $*"; }

# ---------------------------------------------------------------- local files
FILES=()
shopt -s nullglob
for pat in "${PATTERNS[@]:-*.yaml}"; do
  matched=(strategies/$pat)
  [ ${#matched[@]} -gt 0 ] && [ -f "${matched[0]}" ] || { echo "no strategy file matches: strategies/$pat"; exit 1; }
  for f in "${matched[@]}"; do FILES+=("${f#strategies/}"); done
done
shopt -u nullglob

# ---------------------------------------------------------------- compare with the server
say "Comparing ${#FILES[@]} local file(s) with $HOST:$DIR/strategies"
REMOTE=$(ssh "$HOST" "cd $DIR/strategies && sha256sum -- *.yaml 2>/dev/null || true")
PLAN=$(printf '%s\n' "$REMOTE" | "$PY" -c '
import hashlib, sys
remote = {}
for line in sys.stdin:
    h, _, name = line.strip().partition("  ")
    if name: remote[name] = h
for name in sys.argv[1:]:
    h = hashlib.sha256(open("strategies/" + name, "rb").read()).hexdigest()
    print(("same" if remote[name] == h else "conflict") if name in remote else "new", name)
' "${FILES[@]}")

NEW=() CONFLICT=()
while read -r kind name; do
  case "$kind" in new) NEW+=("$name") ;; conflict) CONFLICT+=("$name") ;; esac
done <<< "$PLAN"
echo "    $(grep -c '^same' <<< "$PLAN" || true) already on the server, ${#NEW[@]} new, ${#CONFLICT[@]} conflicting"

if [ ${#CONFLICT[@]} -gt 0 ]; then
  echo
  echo "These files differ from the server's copy. Registered versions are immutable, so save the"
  echo "change as a new version (name.vN+1.yaml with a matching version:) and sync that instead:"
  printf '    %s\n' "${CONFLICT[@]}"
  exit 1
fi
if [ ${#NEW[@]} -eq 0 ]; then say "Nothing to send: the server has every file."; exit 0; fi

# ---------------------------------------------------------------- local validation
say "Validating locally"
"$PY" - "${NEW[@]}" <<'PY'
import sys
from pathlib import Path
from common.config import build_risk, risk_defaults
from dsl.validator import validate
d, raw = risk_defaults()
d["account"]["allow_short"] = True  # the server decides with its live risk controls; this checks the file itself
risk = build_risk(d, raw)
bad = 0
for name in sys.argv[1:]:
    v = validate(Path("strategies", name).read_text(), risk)
    print(f"    {'ok ' if v.ok else 'ERR'}  {name}")
    for e in v.errors:
        print(f"           {e}")
    bad += not v.ok
sys.exit(1 if bad else 0)
PY

if [ $DRY = 1 ]; then say "Dry run: would send ${#NEW[@]} file(s) to $HOST. Nothing changed."; exit 0; fi
if [ $YES = 0 ]; then
  read -r -p "Send ${#NEW[@]} file(s) to $HOST and register them as drafts? [y/N] " a
  [ "$a" = y ] || [ "$a" = Y ] || { echo "Cancelled."; exit 1; }
fi

# ---------------------------------------------------------------- upload (never overwrites)
say "Uploading"
TMP=$(ssh "$HOST" "mktemp -d /tmp/llamaos-strategies.XXXXXX")
scp -q "${NEW[@]/#/strategies/}" "$HOST:$TMP/"
ssh "$HOST" "set -e; cd $TMP
  for f in *.yaml; do
    if [ -e $DIR/strategies/\$f ]; then echo \"    skipped \$f: appeared on the server meanwhile\"; continue; fi
    cp -n \"\$f\" $DIR/strategies/ && chown 10001:10001 $DIR/strategies/\$f && echo \"    sent \$f\"
  done
  rm -rf $TMP"

# ---------------------------------------------------------------- register + validate on the server
say "Registering on the server (live risk controls, deployed code)"
EXTRA=(); [ $WATCH = 1 ] && EXTRA=(--watch)
if ssh "$HOST" "cd $DIR && docker compose exec -T api python - ${EXTRA[*]:-} ${NEW[*]}" < deploy/sync_strategies_remote.py; then
  say "Done. Review and promote them at https://hesynth.xyz/strategies"
else
  echo
  echo "Some files are not valid on the server. 'unknown function' means the deployed code is older than"
  echo "these files: deploy the code (push to main, or deploy/deploy.sh $HOST); they re-validate on their own."
  exit 1
fi
