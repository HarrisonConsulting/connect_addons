#!/usr/bin/env bash
# Pull oduist/connect_addons_ng into this fork as a vendor merge.
#
# The modules this fork tracks against NG live on a vendor line of snapshot
# commits. Each snapshot holds only the tracked modules exactly as NG ships
# them at one upstream sha, with the previous snapshot as its parent, and is
# merged into our branch. Git therefore merges three ways against the last
# snapshot: upstream changes arrive, our changes stay, and a conflict appears
# only where both sides touched the same lines. Our patches on top of NG are
# `git log <snapshot>..HEAD -- <tracked modules>`.
#
# Usage:
#   scripts/sync-ng-upstream.sh [--dry-run] [--ref <upstream-ref>]
#   scripts/sync-ng-upstream.sh --apply [--ref <upstream-ref>] [--adopt <module>]...
#   scripts/sync-ng-upstream.sh --init-base <upstream-sha> --modules "<m1> <m2>"
#
#   --dry-run    default. Fetch, report what NG changed, preview the merge with
#                git merge-tree. Writes nothing but a dangling snapshot object.
#   --apply      merge the snapshot into the current gdo/* branch, then set
#                each changed module's manifest version (see VERSION RULE).
#   --ref        upstream ref or sha to sync to (default: ng/19.0).
#   --adopt      start tracking another NG module from this sync onward.
#   --init-base  record <upstream-sha> as the vendor base of --modules without
#                changing any file (a merge -s ours). Run once per fork.
#
# VERSION RULE, per module whose files the merge changed:
#   NG version  > ours  -> take NG's version, so NG's migration folders run in
#                          order. Every new NG migration is listed as a hotspot
#                          and must be read before the gate.
#   NG version <= ours  -> bump our last version component by one.
#
# Exit: 0 clean or up to date, 1 conflicts (dry run: would conflict), 2 usage
# or precondition failure.
set -euo pipefail

NG_URL=https://github.com/oduist/connect_addons_ng.git
NG_REMOTE=ng
CE_URL=https://github.com/oduist/connect_addons.git
CE_REMOTE=oduist
SUBJECT_PREFIX='Import connect_addons_ng'

# Files where our fork deliberately departs from NG. A sync touching one of
# these needs a reviewer who knows why, even when git merges it cleanly.
KNOWN_HOTSPOTS=(
  'connect/migrations/'                  # NG archives and DROPs tables ours still use
  'connect/models/schedule.py'           # our connect.schedule shape, not NG slots/special days
  'connect/views/schedule.xml'
  'connect/models/settings.py'           # provider-neutral settings split into settings_hc.py
  'connect/models/license.py'            # licence enforcement, see the licence task before editing
  'connect/__manifest__.py'              # the softphone bundle is ours and lives in connect
  'connect/static/src/'
  'connect_twilio/__manifest__.py'
  'connect_twilio/static/src/components/phone/'  # NG phone, not bundled here
  'connect_twilio/controllers/twilio_webhooks.py'  # signature validation is owned here
  'connect_twilio/models/ir_http.py'
  'connect_twilio/models/settings.py'
  'connect_twilio/migrations/'
)

mode=dry-run ref="$NG_REMOTE/19.0" init_sha='' init_modules='' adopt=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) mode=dry-run ;;
    --apply) mode=apply ;;
    --ref) ref=$2; shift ;;
    --adopt) adopt+=("$2"); shift ;;
    --init-base) mode=init; init_sha=$2; shift ;;
    --modules) init_modules=$2; shift ;;
    -h|--help) sed -n '2,36p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

top=$(git rev-parse --show-toplevel)
cd "$top"
[ -f connect/__manifest__.py ] || { echo "run inside the connect_addons repository" >&2; exit 2; }

ensure_remote() {
  local name=$1 url=$2
  git remote get-url "$name" >/dev/null 2>&1 || git remote add "$name" "$url"
  git fetch --quiet "$name"
}

manifest_version() {  # <rev> <module>
  git show "$1:$2/__manifest__.py" 2>/dev/null \
    | grep -oP "['\"]version['\"]\s*:\s*['\"]\K[^'\"]+" | head -1
}

version_gt() {  # a > b as Odoo stores them: up to three parts read as 19.0.<version>
  python3 - "$1" "$2" <<'EOF'
import sys
def p(v):
    if len(v.split('.')) <= 3 and not v.startswith('19.0'):
        v = '19.0.' + v
    return [int(x) for x in v.split('.')]
sys.exit(0 if p(sys.argv[1]) > p(sys.argv[2]) else 1)
EOF
}

bump_last() {
  python3 - "$1" <<'EOF'
import sys
parts = sys.argv[1].split('.')
parts[-1] = str(int(parts[-1]) + 1)
print('.'.join(parts))
EOF
}

# snapshot <upstream-sha> <parent-or-empty> <modules...>  -> prints commit sha
snapshot() {
  local up=$1 parent=$2; shift 2
  local idx; idx=$(mktemp)
  rm -f "$idx"
  for m in "$@"; do
    git cat-file -e "$up:$m" 2>/dev/null || { echo "NG $up has no module $m" >&2; exit 2; }
    GIT_INDEX_FILE=$idx git read-tree --prefix="$m/" "$up:$m"
  done
  local tree; tree=$(GIT_INDEX_FILE=$idx git write-tree)
  rm -f "$idx"
  local msg
  msg=$(printf '%s %s (%s)\n\nUpstream: oduist/connect_addons_ng@%s\nModules: %s\n' \
    "$SUBJECT_PREFIX" "$(git rev-parse --short=8 "$up")" "$*" "$up" "$*")
  if [ -n "$parent" ]; then
    git commit-tree "$tree" -p "$parent" -m "$msg"
  else
    git commit-tree "$tree" -m "$msg"
  fi
}

last_snapshot() {
  git log HEAD --format=%H --grep="^$SUBJECT_PREFIX " -1
}

field() {  # <commit> <Key>
  git log -1 --format=%B "$1" | sed -n "s/^$2: //p" | head -1
}

ensure_remote "$NG_REMOTE" "$NG_URL"

if [ "$mode" = init ]; then
  [ -n "$init_modules" ] || { echo "--init-base needs --modules" >&2; exit 2; }
  [ -z "$(last_snapshot)" ] || { echo "a vendor base already exists: $(last_snapshot)" >&2; exit 2; }
  [ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "working tree is dirty" >&2; exit 2; }
  up=$(git rev-parse --verify "$init_sha^{commit}")
  # shellcheck disable=SC2086
  s0=$(snapshot "$up" '' $init_modules)
  git merge --quiet -s ours --no-ff --allow-unrelated-histories "$s0" \
    -m "Record connect_addons_ng $(git rev-parse --short=8 "$up") as the vendor base of $init_modules"
  echo "vendor base: snapshot $s0 merged as $(git rev-parse --short HEAD)"
  exit 0
fi

base=$(last_snapshot)
[ -n "$base" ] || { echo "no vendor base on HEAD; run --init-base first" >&2; exit 2; }
base_up=$(field "$base" Upstream); base_up=${base_up##*@}
read -r -a tracked <<<"$(field "$base" Modules)"
up=$(git rev-parse --verify "$ref^{commit}")
modules=("${tracked[@]}" "${adopt[@]}")

echo "== connect_addons_ng sync ($mode)"
echo "fork HEAD      $(git rev-parse --short HEAD) on $(git branch --show-current || echo detached)"
echo "vendor base    $(git rev-parse --short "$base") = NG $(git rev-parse --short=8 "$base_up")"
echo "upstream       $ref = NG $(git rev-parse --short=8 "$up") ($(git log -1 --format=%cs "$up"))"
echo "tracked        ${modules[*]}"
echo

echo "== what NG changed since the vendor base"
if git merge-base --is-ancestor "$up" "$base_up" 2>/dev/null; then
  echo "nothing: $ref is already contained in the vendor base"
else
  echo "commits on tracked modules: $(git rev-list --count --no-merges "$base_up..$up" -- "${modules[@]}")" \
       " (all modules: $(git rev-list --count --no-merges "$base_up..$up"))"
  git log --no-merges --format='  %h %cs %s' "$base_up..$up" -- "${modules[@]}" | head -60 || true
  echo "files per tracked module:"
  for m in "${modules[@]}"; do
    n=$(git diff --name-only "$base_up" "$up" -- "$m" | wc -l)
    [ "$n" = 0 ] || printf '  %-28s %s files, version %s -> %s\n' "$m" "$n" \
      "$(manifest_version "$base_up" "$m")" "$(manifest_version "$up" "$m")"
  done
  echo "NG modules we do not track that changed:"
  git diff --name-only "$base_up" "$up" | cut -d/ -f1 | sort -u \
    | grep -vxF -f <(printf '%s\n' "${modules[@]}") | sed 's/^/  /' || true
fi
echo

echo "== hotspots"
new_migrations=$(git diff --name-only --diff-filter=A "$base_up" "$up" -- \
  $(printf '%s/migrations ' "${modules[@]}") 2>/dev/null || true)
if [ -n "$new_migrations" ]; then
  echo "new NG migrations (read every one before the gate):"
  while read -r f; do
    m=${f%%/*}; v=$(cut -d/ -f3 <<<"$f"); ours=$(manifest_version HEAD "$m")
    if [ -n "$ours" ] && ! version_gt "$v" "$ours"; then
      echo "  $f  NEVER RUNS here: $m is already at $ours; port it into our next version folder"
    else
      echo "  $f"
    fi
  done <<<"$new_migrations"
fi
both=$(comm -12 \
  <(git diff --name-only "$base_up" "$up" -- "${modules[@]}" | sort) \
  <(git diff --name-only "$base" HEAD -- "${modules[@]}" | sort))
echo "changed upstream and by us since the base ($(printf '%s' "$both" | grep -c . || true)):"
printf '%s\n' "$both" | grep . | sed 's/^/  /' || true
for h in "${KNOWN_HOTSPOTS[@]}"; do
  hits=$(git diff --name-only "$base_up" "$up" -- "$h" | wc -l)
  [ "$hits" = 0 ] || echo "known hotspot touched upstream: $h ($hits files)"
done
echo

echo "== HC patch series on NG"
# The merge that brought the current snapshot in; our series is what follows it.
landed=$(git log --merges --format='%H %P' HEAD | awk -v s="$base" '$3 == s {print $1}' | tail -1)
echo "commits on tracked modules since snapshot $(git rev-parse --short "$base") landed ($(git rev-parse --short "$landed")):" \
     "$(git rev-list --count --no-merges "$landed..HEAD" -- "${modules[@]}")"
git log --no-merges --format='  %h %s' "$landed..HEAD" -- "${modules[@]}" | head -40 || true
git diff --shortstat "$base" HEAD -- "${modules[@]}" | sed 's/^/  divergence from NG:/'
for m in "${modules[@]}"; do
  printf '  %-28s ours %s, NG %s, %s files differ\n' "$m" "$(manifest_version HEAD "$m")" \
    "$(manifest_version "$base" "$m")" "$(git diff --name-only "$base" HEAD -- "$m" | wc -l)"
done
echo

echo "== legacy CE line ($CE_REMOTE/19.0): our other modules still descend from it"
ensure_remote "$CE_REMOTE" "$CE_URL"
since=$(git log -1 --format=%cI "$base_up")
for m in $(ls -d connect_*/ | tr -d / | grep -vxF -f <(printf '%s\n' "${modules[@]}")); do
  git cat-file -e "$CE_REMOTE/19.0:$m" 2>/dev/null || continue
  n=$(git log --no-merges --cherry-pick --right-only --format=%h "HEAD...$CE_REMOTE/19.0" -- "$m" | wc -l)
  ngv=$(manifest_version "$up" "$m" || true)
  if [ -n "$ngv" ]; then where="NG ships it as $ngv (--adopt $m)"; else where='not in NG'; fi
  [ "$n" = 0 ] || printf '  %-28s %s CE commits not in the fork; %s\n' "$m" "$n" "$where"
done
recent=$(git log --no-merges --cherry-pick --right-only --since="$since" --format='  %h %cs %s' \
  "HEAD...$CE_REMOTE/19.0" || true)
if [ -n "$recent" ]; then echo "newer than the NG base:"; printf '%s\n' "$recent"; fi
echo

new=$(snapshot "$up" "$base" "${modules[@]}")
if [ "$(git rev-parse "$new^{tree}")" = "$(git rev-parse "$base^{tree}")" ] && [ ${#adopt[@]} = 0 ]; then
  echo "== result: up to date, nothing to merge"
  exit 0
fi

if [ "$mode" = dry-run ]; then
  echo "== merge preview (git merge-tree, nothing written)"
  if out=$(git merge-tree --write-tree --name-only --messages HEAD "$new"); then
    tree=$(printf '%s\n' "$out" | head -1)
    git diff --stat "HEAD^{tree}" "$tree" | tail -1 | sed 's/^/  clean merge:/'
    echo "== result: merges cleanly; rerun with --apply on a gdo/* branch"
    exit 0
  fi
  echo "conflicts:"
  printf '%s\n' "$out" | sed -n '2,/^$/p' | grep . | sed 's/^/  /'
  echo "== result: would conflict; rerun with --apply on a gdo/* branch and resolve"
  exit 1
fi

branch=$(git branch --show-current)
case "$branch" in gdo/*) ;; *) echo "--apply runs on a gdo/* branch, not '$branch'" >&2; exit 2 ;; esac
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "working tree is dirty" >&2; exit 2; }
git config rerere.enabled true
git config rerere.autoupdate true

# Per module NG changed: "<module> <ours before> <target> <NG>" by the VERSION RULE.
version_targets() {
  local before=$1 m ours ng target
  for m in "${modules[@]}"; do
    [ -n "$(git diff --name-only "$base" "$new" -- "$m")" ] || continue
    ours=$(manifest_version "$before" "$m"); ng=$(manifest_version "$up" "$m")
    if [ -z "$ours" ] || version_gt "$ng" "$ours"; then target=$ng
    else target=$(bump_last "$ours"); fi
    echo "$m $ours $target $ng"
  done
}

# A manifest conflict is usually both sides moving the version line. Merge the
# manifest with the version masked on all three sides; the VERSION RULE below
# writes the real value. Any other manifest conflict stays for a human.
resolve_manifest_versions() {
  local f tmp stage
  for f in $(git diff --name-only --diff-filter=U -- '*/__manifest__.py'); do
    tmp=$(mktemp -d)
    for stage in 1 2 3; do
      git show ":$stage:$f" 2>/dev/null \
        | sed -E "s/(['\"]version['\"][[:space:]]*:[[:space:]]*['\"])[^'\"]+/\10.0/" >"$tmp/$stage" \
        || { rm -rf "$tmp"; continue 2; }
    done
    if git merge-file -p "$tmp/2" "$tmp/1" "$tmp/3" >"$tmp/out"; then
      cp "$tmp/out" "$f" && git add "$f" && echo "  resolved version-only conflict in $f"
    fi
    rm -rf "$tmp"
  done
}

before=$(git rev-parse HEAD)
msg="Merge connect_addons_ng $(git rev-parse --short=8 "$up") into ${modules[*]}"
if ! git merge --no-ff "$new" -m "$msg"; then
  resolve_manifest_versions
  if [ -n "$(git diff --name-only --diff-filter=U)" ]; then
    echo
    echo "== result: conflicts. Resolve each file keeping our intent and NG's change,"
    echo "   git add them, git commit --no-edit, then set these versions in the same commit:"
    git diff --name-only --diff-filter=U | sed 's/^/  conflict: /'
    version_targets "$before" | awk '{print "  version: " $1 " " $2 " -> " $3 " (NG " $4 ")"}'
    exit 1
  fi
  git commit --quiet --no-edit --cleanup=strip
fi

echo "== versions"
while read -r m ours target ng; do
  sed -i -E "s/(['\"]version['\"][[:space:]]*:[[:space:]]*['\"])[^'\"]+/\1$target/" "$m/__manifest__.py"
  git add "$m/__manifest__.py"
  echo "  $m $ours -> $target (NG $ng)"
done < <(version_targets "$before")
git diff --cached --quiet || git commit --quiet --amend --no-edit
echo "== result: merged as $(git rev-parse --short HEAD). Gate:"
echo "  gdo preflight --strict --source bundled --root <worktree>"
echo "  gdo init <db> --new-build -W <worktree-id>"
