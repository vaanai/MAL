# HV6 v6.1: entry-prompt text fix (new bot_ids, v6.0 untouched)

Written 2026-10-26T23:10Z (date -u), BEFORE any v6.1 rehearsal was built or run. PAPER only. Nothing here says HV6 makes money. HV6 has 0 out-of-sample trades. No book has cleared the promotion gate.

Source: QP ruling `/data/mal/hunt-1008/push-1016/hunt30/hv6/QP-RULING-1026-entry-rate.md`, sections (b) and (c), and open item 5.

## 1. The defect (found by reading the text, not from replies)

In `tools/hv6_playbooks/GEN_v0.txt` (v6.0 seed, sha256 `eb8ccc757bbe31e03914023f720fbf1d468975caa4f987252d2213a331c2a652`):

- WHAT YOU ARE LOOKING FOR asks: "Name why this pool should beat a random established pool, using card fields."
- MEASURED DIRECTIONS says: "Every other field is context, not evidence of direction; do not claim that one of them improves the odds."

The only fields allowed to carry direction are negative ones (low `f.quote_eff_sol`, high impact). So the prompt asks for a positive reason and then forbids every positive claim. It leaves almost no admissible reason to BUY.

## 2. The change (exactly one clause, decided once)

In MEASURED DIRECTIONS, and nowhere else:

- old: `do not claim that one of them improves the odds`
- new: `you may argue from them, but say it is a hypothesis, not a lab result`

The sentence becomes: "Every other field is context, not evidence of direction; you may argue from them, but say it is a hypothesis, not a lab result."

This is QP section (c)'s text, used word for word. No variant was written, so no variant can be chosen.

v6.1 seed: `tools/hv6_playbooks_v61/GEN_v0.txt`, sha256 `598f7aaa22339f8e0e7a5e6d6b83eba1efe9be0c81f9a6721f3be33de380b738`. Its bytes equal the v6.0 seed with that one replacement. A test checks this.

## 3. What stays verbatim (honesty lines)

Everything else is unchanged byte for byte. That includes:

- the FROZEN HEADER (header sha256 unchanged, `2358077a...`) and HONESTY;
- BASE RATE (the losses of every universe measured so far, the costs, "Do not buy to fill a quota");
- WHAT YOU ARE LOOKING FOR ("an untested hypothesis, not a lab result");
- SCREEN, NOT REASON ("The tag is never a reason to buy");
- the first part of MEASURED DIRECTIONS ("Only these two are lab results ...", "Every other field is context, not evidence of direction");
- NEUTRAL FIELDS ("Do not let any of them move a decision on its own");
- PLAN, EXITS, CONVICTION.

There is no knob, cap, min_conviction, bound or code change. NEUTRAL FIELDS was read against the new clause and left as is. Its "on its own" still forbids a single neutral field moving a decision, and the new clause allows a stated hypothesis, not a claim.

## 4. Why this is not fitting

- The defect was found by reading the text, and this is QP's pre-stated minimal fix.
- The change is made once, before any rehearsal of v6.1.
- The one rehearsal (pre-registration: `ARTIFACTS/lab/hv6-v61-rehearsal-prereg.md`) uses 192 fresh cards, disjoint from both earlier samples. It is report-only. Its rate is accepted whatever it is. It never selects a text and never triggers a re-edit.
- No outcome (price, exit, P&L) of any card is joined or looked at.
- The rehearsal cards are 10-21..23 walk-2-hour information (DEC-030 section 2 R1). This is disclosed.

What this does NOT do: it does not delete or soften BASE RATE or SCREEN, NOT REASON to raise the count. QP section (b) refuses that.

## 5. Selection (what is wired, what is not)

- `hv6_agents.load_playbook(version="v0", release="v6.0")`. `release="v6.1"` reads the v6.1 seed. The default is v6.0, so every existing caller gets v6.0's bytes. Unknown releases are refused.
- Bot ids for v6.1 are `hv6_review.bot_id("v6.1", arm)` = `hv6.1-<arm>`. That function already exists and is unchanged.
- NOT wired in this PR: `hv6_runner` (`BOT_PREFIX = "hv6.0-"`, `load_playbook("v0")`), `hv6_review` (`v0_state`, the `v0_playbook_sha256` check) and `hv6_release` (`procedure`, `playbook_seed_sha256`) still use the v6.0 seed. Threading `release` through them is a runner change, so it needs the md5 decision-equivalence replay proof for v6.0. It is listed under the registration needs below.

## 6. v6.1 registration needs (draft, for manager + QP)

1. **Own clock, later than 10-30.** v6.1 needs its own T0 at a 00:00Z boundary after 2026-10-30, set before any v6.1 row exists. `hv6_release` rule 1 (`window_not_over`: v6.(N+1) clock >= v6.N clock + 12 d, which is 2026-11-11T00Z for a 10-30 v6.0 clock) blocks a concurrent v6.1 through the release chain. So either:
   - (a) v6.1 is a parallel sibling registered by a DEC-031 amendment, outside the s7 release chain. In that case the learned-release numbering must be settled (hv6.1-* is the bot-id namespace of the first learned release), for example by a new prefix such as `hv6.0b-*` or by renumbering learned releases to start at v6.2; or
   - (b) it waits for 11-11 and runs as a release record. That collides with the owner's API hard cap to 11-11 and with v6.0's continuing F and X.

   Manager decision.
2. **Multiplicity under DEC-031.** Every v6.1 bot_id (L, L-nr, P, and F if v6.1 registers its own frozen arm; X is a rule book with no prompt and must not be copied, DEC-031 Am.3 item 5) counts in N with v6.0's five. Any v6.1 vs v6.0 contrast is a new pre-registered contrast. An F_v61 copy needs an explicit amendment, because `hv6_release` refuses new F/X copies (`no_new_fx_copies`). The D-ER disclosure, E1 and E2 from the QP ruling apply to v6.1 under its own clock. E1's threshold for v6.1 is restated for v6.1's window, from arithmetic only.
3. **API spend.** v6.1 shares the owner's 3.00 USD/day K1 cap with v6.0 (`hv6_agents.GLOBAL_DAY_CAP_USD`). It does not get a second 3.00. Today each runner's `BudgetGuard` keeps its own ledger root, so two runners would each enforce 3.00 by themselves (6.00 total). Before T0 one of these is needed:
   - a shared guard root, or
   - split caps written into the registration (for example v6.0 keeps its pinned per-ledger caps and v6.1 gets a pinned share), with a test that sum <= 3.00/day.

   The month guard and the owner's API hard cap to 11-11 also apply.
4. **Proofs that carry over, and proofs that do not.**
   - Carry over (byte-identical inputs): the review and regime templates (`tools/hv6_templates/*`, sha256s pinned at 8cd8fe54 by `tools/test_hv6_v61_prompt.py`), the entry and recheck user templates (`hv5_prompts`), the FROZEN HEADER (`header_sha256` unchanged), the card builder, the validators, the budget code and the regime block spec.
   - Do NOT carry over: P1 (#1682) and P2 (#1683) drill md5 equalities, and P3 (#1684). The playbook sha is in every `llm_call` row's `request_sha256`, so every md5 changes by construction. The v6.1 runner needs a new baseline drill at its final sha, and an md5 decision-equivalence proof that v6.0's path is unchanged by the release-threading change. Also needed: the API smoke at the v6.1 sha, the pack's Spec-shas row (new playbook seed sha `598f7aaa...`), the 0b scope check rewritten for the new path `tools/hv6_playbooks_v61`, the QP re-check of the final sha, and the E1 halt-restart drill if E1 is to halt and not only declare.
   - The runner docstring nit (QP open item 3, WATCH "logged as a skip") is fixed in the threading PR, not here.
