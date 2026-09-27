---
cursor:
  subagentId: "bc-1cd96103-3e68-5155-8c93-dc0b889e1efe"
---

# LAYA engine options

As of 2026-09-25. Web + MAL repo only. Host is Oracle Always Free `mal-core-0`: 2 aarch64 OCPU, 12 GB, no GPU. Decision budget is under 100 ms on an already-built feature packet. Edge is vs humans and copy-traders, not colocated MEV ([plan](plan.md)).

In the MAL repo, **LAYA** is the decision slot after precompute and before the risk gate ([LAB_STATE](https://github.com/vaanai/MAL/blob/main/LAB_STATE.md), [DEC-008](https://github.com/vaanai/MAL/blob/main/DEC/DEC-008-stack-phase-gates.md)). Rules fill it today. The ~9 ms “local Laya” line in [STACK-OPTIONS-LAYA-VS-VPS-BRIEF](https://github.com/vaanai/MAL/blob/main/ARTIFACTS/STACK-OPTIONS-LAYA-VS-VPS-BRIEF.md) is a vendor GPU number, not a measurement on this box.

## What JEV and LAYA are

| | JEV | LAYA (the package) |
| --- | --- | --- |
| What | Closed **typed-decision API**. State in, `choice` / `score` / `noul` probabilities out. No text generation. | Open-weight model with the same question types and a Jev-compatible `POST /v1/systemone`. |
| Who | [TypeSafe](https://typesafe.ai). SDKs: [typesafe-sdk-python](https://github.com/typesafe-ai/typesafe-sdk-python) (MIT, pushed 2026-09-21), [typesafe-sdk-js](https://github.com/typesafe-ai/typesafe-sdk-js). | [NandhaKishor M](https://github.com/NandhaKishorM) / Convai Innovations. [GitHub](https://github.com/NandhaKishorM/laya) created **2026-09-18**, last push 2026-09-24, Apache-2.0. PyPI [`laya` 0.3.7](https://pypi.org/project/laya/). Weights: [convaiinnovations/laya](https://huggingface.co/convaiinnovations/laya). |
| Weights | Not released. [No self-host](https://systemonemodels.org/guides/how-to-get-jev-access/). Early access. | Released. English + typed-decisions = ModernBERT-large, **421M**, ~0.8 GB. Multilingual = mmBERT-base, **322M**. |
| Price | [$0.042 / 1M input tokens](https://pydantic.dev/articles/jev-evals); output free. | $0 if you run it. Hosted mirror: [api.impossibl.com](https://pypi.org/project/laya/). |
| Domain | Support tickets, routing, guardrails. Not Solana. [jev-trader](https://github.com/jarrodwatts/jev-trader) is a Monad order-book demo, not a pump.fun bot. | Same. Authors: base checkpoints are **near chance** on their own typed-decisions set (0.36 vs 0.32 random). The 0.77 number is a checkpoint fine-tuned on that benchmark, slightly above Jev’s published 0.73. |

LAYA is a real, installable project. It is **not** a usable hot-path engine on this box, and it is not a trading model.

Vendor latency is **32.8–39.5 ms on a T4 GPU**. An independent ONNX export (~843 MB fp16) measured about **150 ms** for one short question on a 10-core desktop CPU, and said load swung that by more than 10× ([inferenceprince/laya-onnx](https://huggingface.co/inferenceprince/laya-onnx)). There is no Ampere A1 number. A 421M bidirectional encoder on 2 Neoverse cores should be treated as **over 100 ms** until timed. Fine-tune notebook is **~4–5 h on 2× T4** for ~30k questions. That job does not fit 12 GB with no GPU. Two resident checkpoints also eat RAM the observe loop needs.

## What else fits

| Option | Fits this host? | Notes |
| --- | --- | --- |
| Pump.fun sniper bots | No, as the brain | [chainstacklabs/pumpfun-bonkfun-bot](https://github.com/chainstacklabs/pumpfun-bonkfun-bot) (Apache-2.0, ~970 stars, active through 2026-04): listeners including PumpPortal, exits are time / TP-SL / manual. It **sends** trades. Borrow the exit-rule shapes only. LLM scorers (OpenAI-in-the-loop bots) are seconds, not 100 ms. |
| **LightGBM** | **Yes** | [MIT](https://github.com/lightgbm-org/LightGBM). PyPI 4.7, Linux aarch64 wheels in CI. Single-row predict is tens of microseconds on x86 ([lleaves bench](https://github.com/siboehm/lleaves): LightGBM ~52 µs, ONNX Runtime ~11 µs, batch 1). On 2 ARM cores, still well under 1 ms. JSON flatten dominates. |
| sklearn `HistGradientBoosting` | Yes, fallback | BSD-3, already the usual scientific stack. Slower to train, same latency class. No separate C++ wheel drama. |
| XGBoost + ONNX | Possible, heavier | [Apache-2.0](https://github.com/dmlc/xgboost). Linux aarch64 wheels exist and ship CUDA bits, so the install is fatter for no GPU win. |
| Vowpal Wabbit bandit | Later, not first | [BSD](https://github.com/VowpalWabbit/vowpal_wabbit). Online `hold` vs `exit` once paper logs a propensity. aarch64 build is less boring than a LightGBM wheel. |
| Torch RL (SB3 / CleanRL) | No | Sample-hungry, GPU-shaped, fights the 2-core observe loop. Tape labels are already supervised. |

## Recommendation

**Primary: LightGBM, rules stay the risk gate.** One booster for entry, one for exit. Fallback if the aarch64 wheel mis-installs: sklearn `HistGradientBoosting` with the same labels and the same JSON contract.

Why: the packet is numeric (new-token state, wallet moves, graph slots). Trees train on the box from our own paper rows, predict in well under 100 ms, and stay auditable. Convai LAYA would still need a GPU fine-tune on our tape before it beat a coin-flip, and it would not make the latency budget.

**Install.** `pip install lightgbm` (pin 4.7.x). Wheel is megabytes. A few hundred trees is a 1–20 MB text model. Keep it out of the observe process. Do not install PyTorch.

**Plug-in.** Sealed JSONL stays the spine. A flatten step, off the hot path, writes one numeric row per decision: hot-packet L1 fields, regime tags, capped graph slots ([hot packet v0](https://github.com/vaanai/MAL/blob/main/ARTIFACTS/HOT-PACKET-V0.md)), plus wallet features when they exist. Nulls stay missing; do not impute from the future. Hot path: load the booster once, `predict` one row, emit `{action, p, size, model_id, model_ms}`. The existing risk gate can veto. Log the proposal next to the rules decision so DEC-008’s “beats rules on the full book” test still works. `authorize_run` stays false until that test exists.

**Entry labels.** One row per create at time T. Label from the sealed horizon after fees (start with 60s, keep the other horizons). Include rejects ([DEC-007](https://github.com/vaanai/MAL/blob/main/DEC/DEC-007-full-detect-book-anti-selection-bias.md)). Size is a second model or a clip on `p(win)`, not a third system.

**Exit / hold.** Use the tape exit grid already decided: full price path, 30s–30min, plus TP and trailing-stop rules. For each paper position, emit ticks (about 1 Hz, or each tape print — subsample). Features knowable at that tick only: time-in-trade, unrealized after fees, peak and drawdown since entry, curve and wallet flow since entry. Label = the grid action that won on the forward path (hold vs exit, and which rule). Train a second booster. That is the exit policy. A contextual bandit is optional later, only after those logged propensities exist. Do not start with RL.

**Training on the box.** `num_threads=1`, low priority, not inside the listener. Tens of thousands of decision rows: minutes. Exit ticks will be larger; subsample before they crowd the 2 cores. 100k × ~100 float64 columns is tens of MB, fine in 12 GB. Retrain when a new sealed day lands. Hot path only reloads the file.

**Do not.** Put Convai LAYA or cloud JEV on the hot path. Do not adopt a pump sniper as the decision engine. Do not fine-tune a 421M encoder here. Revisit LAYA only if a GPU host appears and a tree model has already lost to rules for a reason that looks like model class, not missing tape.
