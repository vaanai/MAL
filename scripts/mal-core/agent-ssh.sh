#!/usr/bin/env bash
# Tool-neutral helper: Cloudflare Access TCP → SSH to mal-core-0 or mal-fast-0.
# Never prints secret values. Tests and --dry-run do not open a connection.
# Cleans up the decoded key (always) and an optional temp cloudflared on EXIT.
set -euo pipefail

CLOUDFLARED_VER="${MAL_CLOUDFLARED_VERSION:-2026.9.1}"
SSH_USER="${MAL_SSH_USER:-ubuntu}"

usage() {
  cat <<'EOF'
Usage: scripts/mal-core/agent-ssh.sh --host core|fast [options] [ssh args...]

Hosts (Access-gated hostnames, not public :22):
  core   ssh.tradervaan.com      mal-core-0   ED25519 SHA256:Hy68mL6wisJ2t+z/JDcSNATPlyA8sudv4Za7A2Y8Ejs
  fast   ssh-fast.tradervaan.com mal-fast-0   ED25519 SHA256:q5o6Bf1Vo83LtQhwdSQfIL5D4mkRMjELjaL//Y8XXzQ

A host-key fingerprint mismatch stops the script. It does not continue.

Secrets (already in the environment; never pass them on the CLI):
  MAL_SSH_KEY_B64                 base64 of the OpenSSH private key
  CF_ACCESS_CLIENT_ID             Access service token id
  CF_ACCESS_CLIENT_SECRET         Access service token secret
Legacy names still work:
  CURSOR_CLOUD_AGENT_SSH_KEY
  CLOUDFLARE_ACCESS_CLIENT_ID
  CLOUDFLARE_ACCESS_CLIENT_SECRET
When both a neutral name and a legacy name are set, the neutral name wins.

Optional:
  MAL_SSH_KEY_FINGERPRINT         pin the client key (ssh-keygen -lf SHA256)
  MAL_CURSOR_KEY_FINGERPRINT      same check, legacy name
  MAL_SSH_LOCAL_LISTEN            default 127.0.0.1:2222 (core) or :2223 (fast)
  MAL_CLOUDFLARED_BIN             path to cloudflared
  MAL_KEEP_CLOUDFLARED=1          do not delete a temp-downloaded binary on exit

Options:
  --dry-run                       print the resolved host and secret *names*; do not connect
  --ssh-config                    print a ~/.ssh/config snippet (ProxyCommand cloudflared access ssh --hostname %h)
  --check-host-key FILE           compare FILE's SHA256 fingerprint to the pinned host key; no connect

Always-on machine: see tools/oracle_ssh_smoke.md. cloudflared reads
CLOUDFLARE_ACCESS_CLIENT_ID and CLOUDFLARE_ACCESS_CLIENT_SECRET. Export those
from the neutral names in the shell profile if that is where the token lives.

Examples:
  scripts/mal-core/agent-ssh.sh --host core --dry-run
  scripts/mal-core/agent-ssh.sh --host core hostname
  scripts/mal-core/agent-ssh.sh --host fast -- /var/lib/mal/logs
EOF
}

HOST_NAME=""
DRY_RUN=0
DO_SSH_CONFIG=0
CHECK_KEY_FILE=""
SSH_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --host)
      HOST_NAME="${2:-}"
      shift 2
      ;;
    --host=*)
      HOST_NAME="${1#--host=}"
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --ssh-config)
      DO_SSH_CONFIG=1
      shift
      ;;
    --check-host-key)
      CHECK_KEY_FILE="${2:-}"
      shift 2
      ;;
    --)
      shift
      SSH_ARGS+=("$@")
      break
      ;;
    *)
      SSH_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ "${DO_SSH_CONFIG}" == "1" ]]; then
  cat <<'EOF'
# Tool-neutral MAL SSH. Port 22 on the public internet is not this path.
# Stop if the host key does not match the fingerprint. Never use StrictHostKeyChecking=no.
# Map neutral token names onto the names cloudflared reads, in the shell profile:
#   export CLOUDFLARE_ACCESS_CLIENT_ID="${CF_ACCESS_CLIENT_ID:-$CLOUDFLARE_ACCESS_CLIENT_ID}"
#   export CLOUDFLARE_ACCESS_CLIENT_SECRET="${CF_ACCESS_CLIENT_SECRET:-$CLOUDFLARE_ACCESS_CLIENT_SECRET}"

Host mal-core ssh.tradervaan.com
  HostName ssh.tradervaan.com
  User ubuntu
  ProxyCommand cloudflared access ssh --hostname %h
  IdentitiesOnly yes
  IdentityFile ~/.ssh/mal_agent
  UpdateHostKeys no

Host mal-fast ssh-fast.tradervaan.com
  HostName ssh-fast.tradervaan.com
  User ubuntu
  ProxyCommand cloudflared access ssh --hostname %h
  IdentitiesOnly yes
  IdentityFile ~/.ssh/mal_agent
  UpdateHostKeys no

# Pinned host keys (ED25519). Refuse the session on mismatch.
# mal-core  SHA256:Hy68mL6wisJ2t+z/JDcSNATPlyA8sudv4Za7A2Y8Ejs
# mal-fast  SHA256:q5o6Bf1Vo83LtQhwdSQfIL5D4mkRMjELjaL//Y8XXzQ
EOF
  exit 0
fi

if [[ -z "${HOST_NAME}" ]]; then
  echo "agent-ssh: --host core|fast is required" >&2
  exit 1
fi

case "${HOST_NAME}" in
  core)
    ACCESS_HOST="ssh.tradervaan.com"
    EXPECT_HOST_FP="SHA256:Hy68mL6wisJ2t+z/JDcSNATPlyA8sudv4Za7A2Y8Ejs"
    DEFAULT_LISTEN="127.0.0.1:2222"
    ;;
  fast)
    ACCESS_HOST="ssh-fast.tradervaan.com"
    EXPECT_HOST_FP="SHA256:q5o6Bf1Vo83LtQhwdSQfIL5D4mkRMjELjaL//Y8XXzQ"
    DEFAULT_LISTEN="127.0.0.1:2223"
    ;;
  *)
    echo "agent-ssh: unknown host ${HOST_NAME} (expected core or fast)" >&2
    exit 1
    ;;
esac

LOCAL_LISTEN="${MAL_SSH_LOCAL_LISTEN:-${DEFAULT_LISTEN}}"

pick_name() {
  local neutral="$1" legacy="$2"
  if [[ -n "${!neutral:-}" ]]; then
    printf '%s' "${neutral}"
  elif [[ -n "${!legacy:-}" ]]; then
    printf '%s' "${legacy}"
  else
    printf '%s' "unset"
  fi
}

KEY_ENV="$(pick_name MAL_SSH_KEY_B64 CURSOR_CLOUD_AGENT_SSH_KEY)"
ID_ENV="$(pick_name CF_ACCESS_CLIENT_ID CLOUDFLARE_ACCESS_CLIENT_ID)"
SECRET_ENV="$(pick_name CF_ACCESS_CLIENT_SECRET CLOUDFLARE_ACCESS_CLIENT_SECRET)"

if [[ "${CHECK_KEY_FILE}" != "" ]]; then
  if [[ ! -f "${CHECK_KEY_FILE}" ]]; then
    echo "agent-ssh: host key file not found" >&2
    exit 1
  fi
  got="$(ssh-keygen -lf "${CHECK_KEY_FILE}" -E sha256 | awk '{print $2}')"
  if [[ "${got}" != "${EXPECT_HOST_FP}" ]]; then
    echo "agent-ssh: host-key fingerprint mismatch (expected ${EXPECT_HOST_FP})" >&2
    exit 1
  fi
  echo "agent-ssh: host key ok (${HOST_NAME})"
  exit 0
fi

if [[ "${DRY_RUN}" == "1" ]]; then
  printf 'host=%s\n' "${HOST_NAME}"
  printf 'access_hostname=%s\n' "${ACCESS_HOST}"
  printf 'ssh_user=%s\n' "${SSH_USER}"
  printf 'local_listen=%s\n' "${LOCAL_LISTEN}"
  printf 'host_key_fingerprint=%s\n' "${EXPECT_HOST_FP}"
  printf 'key_env=%s\n' "${KEY_ENV}"
  printf 'access_id_env=%s\n' "${ID_ENV}"
  printf 'access_secret_env=%s\n' "${SECRET_ENV}"
  printf 'dry_run=1\n'
  if [[ ${#SSH_ARGS[@]} -gt 0 ]]; then
    printf 'remote_argc=%s\n' "${#SSH_ARGS[@]}"
  fi
  exit 0
fi

if [[ "${KEY_ENV}" == "unset" || "${ID_ENV}" == "unset" || "${SECRET_ENV}" == "unset" ]]; then
  echo "agent-ssh: missing env (key=${KEY_ENV} access_id=${ID_ENV} access_secret=${SECRET_ENV})" >&2
  exit 1
fi

KEY_B64=""
CF_ID=""
CF_SECRET=""
case "${KEY_ENV}" in
  MAL_SSH_KEY_B64) KEY_B64="${MAL_SSH_KEY_B64}" ;;
  CURSOR_CLOUD_AGENT_SSH_KEY) KEY_B64="${CURSOR_CLOUD_AGENT_SSH_KEY}" ;;
esac
case "${ID_ENV}" in
  CF_ACCESS_CLIENT_ID) CF_ID="${CF_ACCESS_CLIENT_ID}" ;;
  CLOUDFLARE_ACCESS_CLIENT_ID) CF_ID="${CLOUDFLARE_ACCESS_CLIENT_ID}" ;;
esac
case "${SECRET_ENV}" in
  CF_ACCESS_CLIENT_SECRET) CF_SECRET="${CF_ACCESS_CLIENT_SECRET}" ;;
  CLOUDFLARE_ACCESS_CLIENT_SECRET) CF_SECRET="${CLOUDFLARE_ACCESS_CLIENT_SECRET}" ;;
esac

# cloudflared reads the legacy names. Do not echo them.
export CLOUDFLARE_ACCESS_CLIENT_ID="${CF_ID}"
export CLOUDFLARE_ACCESS_CLIENT_SECRET="${CF_SECRET}"

ARCH="$(uname -m)"
case "${ARCH}" in
  x86_64|amd64) CF_ASSET="cloudflared-linux-amd64" ;;
  aarch64|arm64) CF_ASSET="cloudflared-linux-arm64" ;;
  *) echo "agent-ssh: unsupported arch ${ARCH}" >&2; exit 1 ;;
esac

CLEAN_CF=0
CF_BIN="${MAL_CLOUDFLARED_BIN:-}"
if [[ -z "${CF_BIN}" ]]; then
  if command -v cloudflared >/dev/null 2>&1; then
    CF_BIN="$(command -v cloudflared)"
  else
    CF_BIN="/tmp/cloudflared"
    if [[ ! -x "${CF_BIN}" ]]; then
      curl -fsSL -o "${CF_BIN}" "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VER}/${CF_ASSET}"
      chmod 755 "${CF_BIN}"
    fi
    if [[ "${MAL_KEEP_CLOUDFLARED:-0}" != "1" ]]; then
      CLEAN_CF=1
    fi
  fi
fi

KEYFILE="$(mktemp /tmp/mal-ssh-key.XXXXXX)"
KNOWN="$(mktemp /tmp/mal-ssh-known.XXXXXX)"
CF_PID=""
cleanup() {
  if [[ -n "${CF_PID}" ]]; then
    kill "${CF_PID}" 2>/dev/null || true
    wait "${CF_PID}" 2>/dev/null || true
  fi
  rm -f "${KEYFILE}" "${KNOWN}"
  if [[ "${CLEAN_CF}" == "1" ]]; then
    rm -f "${CF_BIN}"
  fi
}
trap cleanup EXIT

umask 077
printf '%s' "${KEY_B64}" | base64 -d > "${KEYFILE}"
chmod 600 "${KEYFILE}"
client_fp="$(ssh-keygen -lf "${KEYFILE}" -E sha256 | awk '{print $2}')"
expect_client="${MAL_SSH_KEY_FINGERPRINT:-${MAL_CURSOR_KEY_FINGERPRINT:-}}"
if [[ -n "${expect_client}" && "${client_fp}" != "${expect_client}" ]]; then
  echo "agent-ssh: client-key fingerprint mismatch (expected ${expect_client})" >&2
  exit 1
fi

HOSTPORT="${LOCAL_LISTEN#*:}"
if ! python3 -c "import socket,sys; s=socket.socket(); s.settimeout(1); s.connect(('127.0.0.1', int(sys.argv[1]))); s.close()" "${HOSTPORT}" 2>/dev/null; then
  "${CF_BIN}" access tcp --hostname "${ACCESS_HOST}" --url "${LOCAL_LISTEN}" \
    --id "${CF_ID}" --secret "${CF_SECRET}" \
    >/tmp/mal-cloudflared-access.log 2>&1 &
  CF_PID=$!
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
    if python3 -c "import socket,sys; s=socket.socket(); s.settimeout(1); s.connect(('127.0.0.1', int(sys.argv[1]))); s.close()" "${HOSTPORT}" 2>/dev/null; then
      break
    fi
    sleep 0.5
  done
fi

ssh-keyscan -T 10 -p "${HOSTPORT}" -t ed25519 127.0.0.1 > "${KNOWN}" 2>/dev/null || true
if [[ ! -s "${KNOWN}" ]]; then
  echo "agent-ssh: host-key scan failed; not connecting" >&2
  exit 1
fi
got_host="$(ssh-keygen -lf "${KNOWN}" -E sha256 | awk '{print $2}')"
if [[ "${got_host}" != "${EXPECT_HOST_FP}" ]]; then
  echo "agent-ssh: host-key fingerprint mismatch (expected ${EXPECT_HOST_FP})" >&2
  exit 1
fi

ssh -i "${KEYFILE}" -p "${HOSTPORT}" \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="${KNOWN}" \
  -o UpdateHostKeys=no \
  -o IdentitiesOnly=yes \
  -o BatchMode=yes \
  -o ConnectTimeout=20 \
  "${SSH_USER}@127.0.0.1" "${SSH_ARGS[@]}"
