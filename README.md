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
| `tools/` | Local offline CLIs (EXP-001 mislabel; EXP-002/002b paper runner; EXP-003 marks coverage; no live trading) |
| `data/observe/` | Local JSONL captures (samples gitignored) |

## Phase 0

- **Observe-only** — no trading bot in this repo yet  
- **No database** — markdown/JSON in git ([DEC-002](DEC/DEC-002-memory-first-no-db-local.md))  
- **Local-first** — home PC / WSL2+Docker when needed  
- **Persistent agents:** Grok managers (Helm / Scout / Graph / Proof); Cursor workers ship artifacts via PR  

## Research

- [API, cost & latency brief](ARTIFACTS/API-COST-LATENCY-BRIEF.md) — phase-0 API matrix, monthly cost tiers, latency gaps vs Pump.fun  
- [Post-create marks brief](ARTIFACTS/POST-CREATE-MARKS-BRIEF.md) — 1s–60s ticks on sealed creates (EXP-003; unblocks EXP-002) 

## Observe client (phase 0)

Local-first PumpPortal WebSocket ingest with **regime_id at ingest** ([DEC-004](DEC/DEC-004-regime-id-encoding.md)). No API key required for `subscribeNewToken` + `subscribeMigration`.

### Setup (WSL / Linux / macOS)

```bash
cd /path/to/MAL
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-observe.txt
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

Schema: [ARTIFACTS/OBSERVE-JSONL-SCHEMA.md](ARTIFACTS/OBSERVE-JSONL-SCHEMA.md). Capture is local `python -m observe` (population). Locked Proof sample: [EXP/EXP-001-regime-stage-mislabel.md](EXP/EXP-001-regime-stage-mislabel.md) (`python -m tools.exp001_mislabel`). Paper book: [EXP/EXP-002-evaluate-runner-v0.md](EXP/EXP-002-evaluate-runner-v0.md). Marks: [EXP/EXP-003-post-create-marks.md](EXP/EXP-003-post-create-marks.md) (`python -m tools.exp003_marks`).

### EXP-002b paper runner (rules v1, offline)

PowerShell from repo root (sealed JSONL on disk only):

```powershell
python -m tools.exp002_paper_runner `
  data\observe\observe-2026-09-20.jsonl `
  data\observe\observe-2026-09-21.jsonl `
  --rules v1 --seed 1 --output-dir data\observe --prefix _exp002b
```

Docs: [EXP/EXP-002b-evaluate-rules-v1.md](EXP/EXP-002b-evaluate-rules-v1.md). Vacuous v0 archive: `--rules v0 --prefix _exp002`.

### Troubleshooting (Windows / long captures)

- **`ws_closed code=1006`** or **`ws_handshake_rejected status_code=502`** — usually a PumpPortal or network blip. The client logs a warning, backs off, and **retries until you stop it** (`Ctrl+C`). Capture should resume on the same daily JSONL file.
- **Process exited on reconnect** — you were likely on an older build that did not catch handshake failures. `git pull`, `pip install -r requirements-observe.txt` (includes **certifi** for reliable TLS on Windows), then restart `python -m observe`.
- **TLS / certificate errors on Windows** — ensure `certifi` is installed from `requirements-observe.txt`; the client uses Mozilla’s CA bundle via certifi for `wss://` connections.

## Seats (override legacy org chart)

See [DEC-001](DEC/DEC-001-lean-four-override.md): **Helm, Scout, Graph, Proof** — not Research/Strategy/Ops as runtime roles.
