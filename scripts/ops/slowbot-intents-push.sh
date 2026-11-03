#!/bin/sh
# Push new slowbot live-intent rows from research-0 to mal-fast-0 with append semantics. POSIX sh. No key, no wallet.
#
# Idempotent by byte offset: the remote file's size is the cursor. Each run sends only the bytes after it, cut at the last complete line of the
# local file. Before appending, the remote prefix is checked against the local prefix by sha256; a mismatch (the remote is not a prefix of the
# local file, e.g. the runner dir was reset) refuses and sends nothing. A push that dies half way leaves a torn last line on the remote; the next
# run continues from the remote size, and the executor skips a torn final line (tools.slowbot.intents.read_intents).
#
# Env (all optional except SRC):
#   SLOWBOT_INTENTS_SRC   local live-intents.jsonl (the runner's OUT/live-intents.jsonl)
#   SLOWBOT_INTENTS_HOST  default mal-fast-0
#   SLOWBOT_INTENTS_DEST  remote file, default /var/lib/mal-live/slowbot-h/intents/live-intents.jsonl (Helm creates the directory for the push user)
#   SLOWBOT_SSH           default "ssh -o BatchMode=yes" (tests point it at a local shim)
# Exit: 0 ok (or nothing to send), 2 usage, 3 remote unreachable, 4 remote is ahead of local, 5 prefix mismatch, 6 send failed.
set -u
SRC=${SLOWBOT_INTENTS_SRC:-}
HOST=${SLOWBOT_INTENTS_HOST:-mal-fast-0}
DEST=${SLOWBOT_INTENTS_DEST:-/var/lib/mal-live/slowbot-h/intents/live-intents.jsonl}
SSH=${SLOWBOT_SSH:-ssh -o BatchMode=yes}
[ -n "$SRC" ] || { echo "slowbot-intents-push: SLOWBOT_INTENTS_SRC is required" >&2; exit 2; }
[ -f "$SRC" ] || { echo "pushed=0 reason=no_source"; exit 0; }

TMP=$(mktemp "${TMPDIR:-/tmp}/slowbot-push.XXXXXX") || exit 2
TMP2=$TMP.c
trap 'rm -f "$TMP" "$TMP2"' EXIT HUP INT TERM

# Snapshot the local file at one size, then cut to the last complete line.
lsz=$(wc -c < "$SRC" | tr -d ' ')
head -c "$lsz" "$SRC" > "$TMP"
nl=$(wc -l < "$TMP" | tr -d ' ')
head -n "$nl" "$TMP" > "$TMP2"
clen=$(wc -c < "$TMP2" | tr -d ' ')

DIR=$(dirname "$DEST")
rsz=$($SSH "$HOST" "mkdir -p '$DIR' && touch '$DEST' && wc -c < '$DEST'" | tr -d ' ') || { echo "slowbot-intents-push: remote unreachable" >&2; exit 3; }
case "$rsz" in ''|*[!0-9]*) echo "slowbot-intents-push: bad remote size" >&2; exit 3;; esac

if [ "$rsz" -gt "$clen" ]; then echo "slowbot-intents-push: remote ($rsz) is ahead of local complete lines ($clen); refusing" >&2; exit 4; fi
if [ "$rsz" -eq "$clen" ] && [ "$rsz" -eq 0 ]; then echo "pushed=0 remote_bytes=$rsz"; exit 0; fi

if [ "$rsz" -gt 0 ]; then
  lh=$(head -c "$rsz" "$TMP2" | sha256sum | cut -d' ' -f1)
  rh=$($SSH "$HOST" "head -c $rsz '$DEST' | sha256sum | cut -d' ' -f1") || exit 3
  [ "$lh" = "$rh" ] || { echo "slowbot-intents-push: remote prefix differs from local; refusing" >&2; exit 5; }
fi
if [ "$rsz" -eq "$clen" ]; then echo "pushed=0 remote_bytes=$rsz"; exit 0; fi

tail -c +$((rsz + 1)) "$TMP2" | $SSH "$HOST" "cat >> '$DEST'" || { echo "slowbot-intents-push: send failed" >&2; exit 6; }
echo "pushed=$((clen - rsz)) remote_bytes=$clen"
