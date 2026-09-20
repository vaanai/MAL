# MAL

Meme coin alpha lab — **Pump.fun / Solana first**, observe-before-act, memory-first phase 0.

## Quick reload (managers)

1. [LAB_STATE.md](LAB_STATE.md) — objective, spine, hypotheses, next work  
2. [CONSTITUTION.md](CONSTITUTION.md) — non-negotiables  
3. [ARTIFACTS/SUMMARY.md](ARTIFACTS/SUMMARY.md) — latest research digest  
4. Active decisions in [DEC/](DEC/)

## Repository layout

| Path | Purpose |
| --- | --- |
| `LAB_STATE.md` | Compact current lab state |
| `CONSTITUTION.md` | Rules: knowable-at-T, capped hot packet, kill-attempt, infra discipline |
| `DEC/` | Immutable decision records (`DEC-xxx-*.md`) |
| `EXP/` | Experiment registry format + `EXP-xxx` files |
| `ARTIFACTS/` | Research briefs and manager summaries |
| `observe/` | Scout WS client (phase 0) |
| `data/observe/` | Local JSONL captures (samples gitignored) |

## Phase 0

- **Observe-only** — no trading bot in this repo yet  
- **No database** — markdown/JSON in git ([DEC-002](DEC/DEC-002-memory-first-no-db-local.md))  
- **Local-first** — home PC / WSL2+Docker when needed  
- **Persistent agents:** Grok managers (Helm / Scout / Graph / Proof); Cursor workers ship artifacts via PR  

## Research

- [API, cost & latency brief](ARTIFACTS/API-COST-LATENCY-BRIEF.md) — phase-0 API matrix, monthly cost tiers, latency gaps vs Pump.fun  

## Observe client (phase 0)

Local-first PumpPortal WebSocket ingest with **regime_id at ingest** ([DEC-004](DEC/DEC-004-regime-id-encoding.md)). No API key required for `subscribeNewToken` + `subscribeMigration`.

### Setup (WSL / Linux / macOS / Windows)

```bash
cd /path/to/MAL
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-observe.txt
```

On **Windows**, if Chrome trusts a site but Python fails TLS with `CERTIFICATE_VERIFY_FAILED`, the observe client uses the [certifi](https://github.com/certifi/python-certifi) CA bundle (not the OS store). After upgrading dependencies, you can also point Python at the Windows certificate store:

```bash
pip install pip-system-certs
```

Or set `SSL_CERT_FILE` to certifi’s bundle for that shell session (PowerShell example):

```powershell
python -c "import certifi; print(certifi.where())"   # copy path
$env:SSL_CERT_FILE = "<path printed above>"
python -m observe
```

### Run

```bash
python -m observe
```

- **Output:** append-only JSONL under `data/observe/observe-YYYY-MM-DD.jsonl` (gitignored).
- **Logs:** structured messages on stderr (`ingest_sealed`, reconnects).
- **Stop:** `Ctrl+C` (graceful shutdown).

### Optional environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAL_PUMPPORTAL_WS_URL` | `wss://pumpportal.fun/api/data` | WebSocket endpoint (no key for free streams) |
| `MAL_OBSERVE_OUTPUT_DIR` | `data/observe` | JSONL output directory |
| `MAL_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `MAL_OBSERVE_SSL_INSECURE` | *(unset)* | If `1` / `true` / `yes`, disables TLS verification (logged as **WARNING**; emergency only) |

### Windows TLS troubleshooting

| Symptom | What to try |
| --- | --- |
| `[SSL: CERTIFICATE_VERIFY_FAILED]` connecting to PumpPortal | `pip install -U -r requirements-observe.txt` (refreshes **certifi**) |
| Still fails after upgrade | `pip install pip-system-certs`, then rerun `python -m observe` |
| One-off without changing packages | Set `SSL_CERT_FILE` to the path from `python -c "import certifi; print(certifi.where())"` |
| Last resort (insecure) | `MAL_OBSERVE_SSL_INSECURE=1` — only for debugging; fix CA trust instead |

Schema: [ARTIFACTS/OBSERVE-JSONL-SCHEMA.md](ARTIFACTS/OBSERVE-JSONL-SCHEMA.md). First capture EXP: [EXP/EXP-001-24h-ws-capture.md](EXP/EXP-001-24h-ws-capture.md).

## Seats (override legacy org chart)

See [DEC-001](DEC/DEC-001-lean-four-override.md): **Helm, Scout, Graph, Proof** — not Research/Strategy/Ops as runtime roles.
