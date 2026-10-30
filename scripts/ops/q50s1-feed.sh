# s1's feed arguments, sourced with POSIX `.` by scripts/ops/q50s1-shadow-start.sh and scripts/ops/q50s1-smoke.sh so the book and its smoke use the same providers (DEC-031 Amendment 22
# item 10; QP on #1107: no key in any process's argv, unit description or property, job log or journal). Not a job command by itself. POSIX sh. It never reads a key.
#   Q50S1_WS1        wss://api.mainnet-beta.solana.com (public; always the first provider)
#   Q50S1_WS2        the SECOND provider, a parameter (`--ws2 URL` of the start and smoke scripts). There is NO default: empty means mainnet-beta alone (all sockets on WS1).
#                    Every URL must be HOST-ONLY: wss://<host>[:<port>][/], host in [A-Za-z0-9.-], no path, query, fragment or userinfo (QP A1 on #1121: free tiers put the
#                    credential in the path). A credential may only be a query parameter named api-key, added by the launcher (--ws2-keyed); a provider whose URL host or
#                    path carries a credential is not used (DEC-031 Am.22 Erratum 1). Only this key-free form is on a command line, in a unit, in a log, and in `endpoints`.
#   Q50S1_WS2_KEYED  1 (`--ws2-keyed`) when the provider needs a key: scripts/ops/q50s1-ws-launch.py adds it INSIDE the shadow process. It REQUIRES an explicit key
#                    file (`--ws2-key-file PATH`, Q50S1_KEY_FILE; NO default), and the launcher binds it to its provider allowlist (host, file tag, variable, query
#                    parameter: helius -> mainnet.helius-rpc.com, HELIUS_API_KEY, api-key; orbitflare -> fra.rpc.orbitflare.com, ORBITFLARE_API_KEY, api_key).
#   Q50S1_SOCKETS    the PumpSwap socket count `--sockets N` (1 to 3, default 3); Q50S1_WS2_FIRST=1 (`--ws2-first`) lists WS2 as url 0. S* gives PumpSwap socket i the url
#                    i mod n and the one pump.fun (synthetic-class) socket url 0 (S* h5_shadow.build_source / build_pump_source), so `--sockets 2 --ws2-first` puts ONE
#                    socket on mainnet-beta (PumpSwap) and the PumpSwap + pump.fun sockets on WS2. Sockets are a registration-record value (`sockets` of the line).
#                    Helius for the full window is OFF by the manager's decision of 2026-10-30 (probe #1891: about 2.33M credits a day); it needs the owner's word.
#   q50s1_ws2_build  returns 11 unless every URL is a distinct key-free wss:// URL (no query, fragment, userinfo or key-like token) and, when keyed, the key file is readable.
#   q50s1_ws_args    prints the h5_shadow --ws-url arguments (WS1, then WS2 if set); q50s1_endpoints prints the endpoints, one per line.
#   q50s1_key_opts   prints the launcher's key options (empty when not keyed).
Q50S1_WS1=wss://api.mainnet-beta.solana.com
Q50S1_WS2="${Q50S1_WS2:-}"
Q50S1_WS2_KEYED="${Q50S1_WS2_KEYED:-0}"
Q50S1_KEY_FILE="${Q50S1_KEY_FILE:-}"   # no default: a keyed provider names its own key file (security fix 2026-10-30)
Q50S1_SOCKETS="${Q50S1_SOCKETS:-3}"
Q50S1_WS2_FIRST="${Q50S1_WS2_FIRST:-0}"
q50s1_url_ok() {  # host-only: wss://HOST[:PORT][/]
  case "$1" in wss://?*) ;; *) return 1 ;; esac
  q50s1_h="${1#wss://}"; q50s1_h="${q50s1_h%/}"
  case "$q50s1_h" in ''|*[!A-Za-z0-9.:-]*|*:*:*|:*|*:|.*|*.) return 1 ;; esac
  case "$q50s1_h" in *:*) case "${q50s1_h#*:}" in *[!0-9]*) return 1 ;; esac ;; esac
  return 0
}
q50s1_ws2_build() {
  q50s1_url_ok "$Q50S1_WS1" || return 11
  case "$Q50S1_WS2_KEYED" in 0|1) ;; *) return 11 ;; esac
  if [ -n "$Q50S1_WS2" ]; then
    q50s1_url_ok "$Q50S1_WS2" || return 11
    [ "$Q50S1_WS1" != "$Q50S1_WS2" ] || return 11
  else
    [ "$Q50S1_WS2_KEYED" = 0 ] || return 11
  fi
  if [ "$Q50S1_WS2_KEYED" = 1 ]; then
    case "$Q50S1_KEY_FILE" in ''|*' '*) return 11 ;; esac
    [ -r "$Q50S1_KEY_FILE" ] || return 11
  fi
  case "$Q50S1_SOCKETS" in 1|2|3) ;; *) return 11 ;; esac
  case "$Q50S1_WS2_FIRST" in 0) ;; 1) [ -n "$Q50S1_WS2" ] || return 11 ;; *) return 11 ;; esac
  return 0
}
q50s1_ws_args() {  # --sockets N, then the urls in S*'s order (url 0 also feeds the pump.fun socket)
  if [ -z "$Q50S1_WS2" ]; then printf '%s\n' "--sockets $Q50S1_SOCKETS --syn-sockets 1 --ws-url $Q50S1_WS1"
  elif [ "$Q50S1_WS2_FIRST" = 1 ]; then printf '%s\n' "--sockets $Q50S1_SOCKETS --syn-sockets 1 --ws-url $Q50S1_WS2 --ws-url $Q50S1_WS1"
  else printf '%s\n' "--sockets $Q50S1_SOCKETS --syn-sockets 1 --ws-url $Q50S1_WS1 --ws-url $Q50S1_WS2"; fi
}
q50s1_endpoints() {  # in S*'s url order (the registered line records this exact order; the start compares it ordered, QP 6534828256)
  if [ -z "$Q50S1_WS2" ]; then printf '%s\n' "$Q50S1_WS1"
  elif [ "$Q50S1_WS2_FIRST" = 1 ]; then printf '%s\n%s\n' "$Q50S1_WS2" "$Q50S1_WS1"
  else printf '%s\n%s\n' "$Q50S1_WS1" "$Q50S1_WS2"; fi
}
q50s1_key_opts() { [ "$Q50S1_WS2_KEYED" = 1 ] || return 0; printf '%s\n' "--key-file $Q50S1_KEY_FILE --keyed-url $Q50S1_WS2"; }
