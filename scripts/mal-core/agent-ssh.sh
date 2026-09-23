#!/usr/bin/env bash
# Cursor agent helper: Cloudflare Access TCP → SSH to mal-core-0.
# Requires Runtime Secrets in the environment. Never prints secret values.
# Cleans up decoded key (always) and optional temp cloudflared on EXIT.
set -euo pipefail

CLOUDFLARED_VER="${MAL_CLOUDFLARED_VERSION:-2026.9.1}"
ACCESS_HOST="${MAL_SSH_ACCESS_HOSTNAME:-ssh.tradervaan.com}"
LOCAL_LISTEN="${MAL_SSH_LOCAL_LISTEN:-127.0.0.1:2222}"
SSH_USER="${MAL_SSH_USER:-ubuntu}"
EXPECT_FP="${MAL_CURSOR_KEY_FINGERPRINT:-SHA256:HH+tRTOIpyvYorf+FhZ7INifqOm2L7NTcvPLdqMPVTo}"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KNOWN_HOSTS="${MAL_SSH_KNOWN_HOSTS:-${SELF_DIR}/mal-core-known_hosts}"

# Official GitHub release SHA256 for cloudflared ${CLOUDFLARED_VER}
# https://github.com/cloudflare/cloudflared/releases/tag/2026.9.1
CF_SHA256_AMD64="03f1f25d1cc93b9ad6c60569d44060bc4f17ed97075760ed8cfca4b12dcd68cc"
CF_SHA256_ARM64="3d97437c71848bd8df68041e12436b484a661d95073ea1937f01a845ce88faa3"

usage() {
  cat <<'EOF'
Usage: scripts/mal-core/agent-ssh.sh [ssh args...]

Runtime Secrets (must already be in the environment; never pass on CLI / argv):
  CURSOR_CLOUD_AGENT_SSH_KEY          base64 of OpenSSH private PEM
  CLOUDFLARE_ACCESS_CLIENT_ID         Access service token id
  CLOUDFLARE_ACCESS_CLIENT_SECRET     Access service token secret

cloudflared is started with TUNNEL_SERVICE_TOKEN_* in the environment
(not --id/--secret on argv). Download is SHA256-pinned for 2026.9.1.
Host keys: StrictHostKeyChecking=yes against scripts/mal-core/mal-core-known_hosts.

Optional:
  MAL_SSH_ACCESS_HOSTNAME   default ssh.tradervaan.com (Access-gated, not public :22)
  MAL_SSH_LOCAL_LISTEN      default 127.0.0.1:2222 (must match known_hosts)
  MAL_CLOUDFLARED_BIN       path to an already-trusted cloudflared
  MAL_KEEP_CLOUDFLARED=1    do not delete a temp-downloaded binary on exit
  MAL_SSH_KNOWN_HOSTS       override known_hosts path

Example:
  scripts/mal-core/agent-ssh.sh hostname
  scripts/mal-core/agent-ssh.sh -- /var/lib/mal/eng/healthcheck.sh
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

for v in CURSOR_CLOUD_AGENT_SSH_KEY CLOUDFLARE_ACCESS_CLIENT_ID CLOUDFLARE_ACCESS_CLIENT_SECRET; do
  if [[ -z "${!v:-}" ]]; then
    echo "agent-ssh: missing env ${v}" >&2
    exit 1
  fi
done

ARCH="$(uname -m)"
case "${ARCH}" in
  x86_64|amd64) CF_ASSET="cloudflared-linux-amd64"; CF_SHA="${CF_SHA256_AMD64}" ;;
  aarch64|arm64) CF_ASSET="cloudflared-linux-arm64"; CF_SHA="${CF_SHA256_ARM64}" ;;
  *) echo "agent-ssh: unsupported arch ${ARCH}" >&2; exit 1 ;;
esac

verify_cf_sha() {
  local bin="$1"
  echo "${CF_SHA}  ${bin}" | sha256sum -c - >/dev/null
}

CLEAN_CF=0
CF_BIN="${MAL_CLOUDFLARED_BIN:-}"
if [[ -z "${CF_BIN}" ]]; then
  if command -v cloudflared >/dev/null 2>&1; then
    CF_BIN="$(command -v cloudflared)"
    # PATH binary: still require the pinned version when possible.
    if ! verify_cf_sha "${CF_BIN}"; then
      echo "agent-ssh: cloudflared on PATH failed SHA256 pin for ${CLOUDFLARED_VER}; downloading pinned binary" >&2
      CF_BIN=""
    fi
  fi
  if [[ -z "${CF_BIN}" ]]; then
    CF_BIN="/tmp/cloudflared"
    if [[ ! -x "${CF_BIN}" ]] || ! verify_cf_sha "${CF_BIN}"; then
      curl -fsSL -o "${CF_BIN}" "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VER}/${CF_ASSET}"
      chmod 755 "${CF_BIN}"
      verify_cf_sha "${CF_BIN}" || {
        echo "agent-ssh: downloaded cloudflared SHA256 mismatch; refusing to run" >&2
        rm -f "${CF_BIN}"
        exit 1
      }
    fi
    if [[ "${MAL_KEEP_CLOUDFLARED:-0}" != "1" ]]; then
      CLEAN_CF=1
    fi
  fi
elif ! verify_cf_sha "${CF_BIN}"; then
  echo "agent-ssh: MAL_CLOUDFLARED_BIN failed SHA256 pin for ${CLOUDFLARED_VER}" >&2
  exit 1
fi

KEYFILE="$(mktemp /tmp/mal-cursor-key.XXXXXX)"
CF_PID=""
cleanup() {
  if [[ -n "${CF_PID}" ]]; then
    kill "${CF_PID}" 2>/dev/null || true
    wait "${CF_PID}" 2>/dev/null || true
  fi
  rm -f "${KEYFILE}"
  if [[ "${CLEAN_CF}" == "1" ]]; then
    rm -f "${CF_BIN}"
  fi
}
trap cleanup EXIT

umask 077
printf '%s' "${CURSOR_CLOUD_AGENT_SSH_KEY}" | base64 -d > "${KEYFILE}"
chmod 600 "${KEYFILE}"
got="$(ssh-keygen -lf "${KEYFILE}" -E sha256 | awk '{print $2}')"
if [[ "${got}" != "${EXPECT_FP}" ]]; then
  echo "agent-ssh: deploy-key fingerprint mismatch (expected ${EXPECT_FP})" >&2
  exit 1
fi

if [[ ! -f "${KNOWN_HOSTS}" ]]; then
  echo "agent-ssh: missing known_hosts ${KNOWN_HOSTS}" >&2
  exit 1
fi

HOSTPORT="${LOCAL_LISTEN#*:}"
# Reuse an already-running Access listener if present.
if ! python3 -c "import socket,sys; s=socket.socket(); s.settimeout(1); s.connect(('127.0.0.1', int(sys.argv[1]))); s.close()" "${HOSTPORT}" 2>/dev/null; then
  # Service token via env — do not put --id/--secret on argv (shows in ps).
  TUNNEL_SERVICE_TOKEN_ID="${CLOUDFLARE_ACCESS_CLIENT_ID}" \
  TUNNEL_SERVICE_TOKEN_SECRET="${CLOUDFLARE_ACCESS_CLIENT_SECRET}" \
    "${CF_BIN}" access tcp --hostname "${ACCESS_HOST}" --url "${LOCAL_LISTEN}" \
    >/tmp/mal-cloudflared-access.log 2>&1 &
  CF_PID=$!
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
    if python3 -c "import socket,sys; s=socket.socket(); s.settimeout(1); s.connect(('127.0.0.1', int(sys.argv[1]))); s.close()" "${HOSTPORT}" 2>/dev/null; then
      break
    fi
    sleep 0.5
  done
fi

ssh -i "${KEYFILE}" -p "${HOSTPORT}" \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="${KNOWN_HOSTS}" \
  -o GlobalKnownHostsFile=/dev/null \
  -o HashKnownHosts=no \
  -o IdentitiesOnly=yes \
  -o BatchMode=yes \
  -o ConnectTimeout=20 \
  "${SSH_USER}@127.0.0.1" "$@"
