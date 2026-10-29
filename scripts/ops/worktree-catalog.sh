#!/bin/sh
# Read-only catalogue of every git worktree of the MAL main repo.
#
# Writes a TSV (default /data/mal/hunt-1008/ops/WORKTREES.tsv, or arg 1) with the
# header: path branch head_sha pushed dirty_lines locked cited_by
# and prints a summary to stdout.
#
# Read-only by design: only `git worktree list`, `fetch`, `for-each-ref`,
# `grep`, `status --porcelain` run. Nothing is removed, pruned, moved, cleaned,
# checked out, reset or stashed. Dirty lines are counted, never named.
#
# Env overrides: MAL_MAIN (main checkout), OPS_DIR (ops notes dir).

set -u

OUT=${1:-/data/mal/hunt-1008/ops/WORKTREES.tsv}
MAIN=${MAL_MAIN:-$HOME/.miscusi/repos/vaanai/MAL}
OPS_DIR=${OPS_DIR:-/data/mal/hunt-1008/ops}
TAB=$(printf '\t')

TMPD=$(mktemp -d "${TMPDIR:-/tmp}/wtcat.XXXXXX") || exit 1
trap 'rm -rf "$TMPD"' EXIT INT TERM
RECS="$TMPD/recs.tsv"
BODY="$TMPD/body.tsv"
STAT="$TMPD/status.txt"
SHAS="$TMPD/shas.txt"

if ! git -C "$MAIN" fetch -q origin 2>"$TMPD/fetch.err"; then
	echo "warning: git fetch origin failed; pushed= uses cached remote refs" >&2
fi

# 1. Parse porcelain: one record per worktree block.
git -C "$MAIN" worktree list --porcelain | awk -v TAB="$TAB" '
	function flush() {
		if (p != "") print p TAB h TAB b TAB l
	}
	/^worktree /  { flush(); p = substr($0, 10); h = ""; b = "detached"; l = "n"; next }
	/^HEAD /      { h = substr($0, 6); next }
	/^branch /    { b = substr($0, 8); sub(/^refs\/heads\//, "", b); next }
	/^detached/   { b = "detached"; next }
	/^locked/     { r = substr($0, 8); l = "y:" r; next }
	END           { flush() }
' > "$RECS"

TOTAL=$(wc -l < "$RECS" | tr -d ' ')

# 2. pushed: y if some remote-tracking ref contains the head. One lookup per unique sha.
cut -f2 "$RECS" | sort -u > "$SHAS"
: > "$TMPD/pushed.tsv"
while read -r sha; do
	[ -n "$sha" ] || continue
	if git -C "$MAIN" cat-file -e "$sha^{commit}" 2>/dev/null; then
		hit=$(git -C "$MAIN" for-each-ref --contains "$sha" --format='%(refname)' refs/remotes/ 2>/dev/null | head -n 1)
		if [ -n "$hit" ]; then v=y; else v=n; fi
	else
		v=n
	fi
	printf '%s\t%s\n' "$sha" "$v" >> "$TMPD/pushed.tsv"
done < "$SHAS"

# 3. cited_by: which docs mention the worktree basename.
# DEC/EXP tokens are the ID (DEC-031); HANDOFF and ops notes use the file stem.
cite_for() {
	name=$1
	out=""
	# origin/main: docs/HANDOFF.md, DEC/*.md, EXP/*.md
	git -C "$MAIN" grep -l -F -e "$name" origin/main -- docs/HANDOFF.md 'DEC/*.md' 'EXP/*.md' 2>/dev/null |
		sed 's/^[^:]*://' > "$TMPD/cite.a"
	# ops notes: top level and manager17
	grep -l -F -- "$name" "$OPS_DIR"/*.md "$OPS_DIR"/manager17/*.md 2>/dev/null > "$TMPD/cite.b"
	{
		while read -r f; do
			[ -n "$f" ] || continue
			stem=$(basename "$f" .md)
			case "$f" in
				docs/HANDOFF.md) tok=HANDOFF ;;
				DEC/*|EXP/*) case "$stem" in
					DEC-*|EXP-*) tok=$(printf '%s' "$stem" | cut -d- -f1,2) ;;
					*) tok=$stem ;;
				esac ;;
				*) tok=$stem ;;
			esac
			printf '%s\n' "$tok"
		done < "$TMPD/cite.a"
		while read -r f; do
			[ -n "$f" ] || continue
			basename "$f" .md
		done < "$TMPD/cite.b"
	} | sort -u | paste -s -d, - 2>/dev/null
}

# 4. dirty lines: count of status lines, never names. ? on timeout or error.
dirty_for() {
	p=$1
	if [ ! -d "$p" ]; then echo '?'; return; fi
	timeout 10 git -C "$p" --no-optional-locks status --porcelain > "$STAT" 2>/dev/null
	rc=$?
	if [ "$rc" -ne 0 ]; then echo '?'; return; fi
	wc -l < "$STAT" | tr -d ' '
}

# 5. Assemble body rows.
: > "$BODY"
DIRTY_N=0; UNKNOWN_N=0; PUSH_N=0; LOCK_N=0; CITED_N=0; DIRTY_UNPUSHED=0
while IFS="$TAB" read -r path sha branch locked; do
	[ -n "$path" ] || continue
	pushed=$(awk -F '\t' -v s="$sha" '$1 == s { print $2; exit }' "$TMPD/pushed.tsv")
	[ -n "$pushed" ] || pushed=n
	dirty=$(dirty_for "$path")
	base=$(basename "$path")
	cited=$(cite_for "$base")
	printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$path" "$branch" "$sha" "$pushed" "$dirty" "$locked" "$cited" >> "$BODY"
	case "$dirty" in
		'?') UNKNOWN_N=$((UNKNOWN_N+1)) ;;
		0) ;;
		*) DIRTY_N=$((DIRTY_N+1)) ;;
	esac
	[ "$pushed" = n ] && PUSH_N=$((PUSH_N+1))
	case "$locked" in y*) LOCK_N=$((LOCK_N+1)) ;; esac
	[ -n "$cited" ] && CITED_N=$((CITED_N+1))
	if [ "$pushed" = n ] && [ "$dirty" != 0 ]; then
		DIRTY_UNPUSHED=$((DIRTY_UNPUSHED+1))
		printf 'DIRTY+UNPUSHED\t%s\t%s\t%s\t%s\n' "$path" "$branch" "$dirty" "$cited" >> "$TMPD/risky.tsv"
	fi
done < "$RECS"

# 6. Write TSV atomically.
mkdir -p "$(dirname "$OUT")"
{
	printf 'path\tbranch\thead_sha\tpushed\tdirty_lines\tlocked\tcited_by\n'
	cat "$BODY"
} > "$TMPD/out.tsv"
mv "$TMPD/out.tsv" "$OUT"

echo "worktree catalog: $OUT"
echo "total=$TOTAL dirty>0=$DIRTY_N dirty_unknown=$UNKNOWN_N pushed=n=$PUSH_N locked=$LOCK_N cited=$CITED_N dirty_and_pushed_n=$DIRTY_UNPUSHED"
if [ -s "$TMPD/risky.tsv" ]; then
	echo "rows dirty and pushed=n (path, branch, dirty_lines, cited_by):"
	cat "$TMPD/risky.tsv"
fi
exit 0
