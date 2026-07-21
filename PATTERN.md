# The Worklist Pattern — the shared core

**One sentence:** deterministic code owns I/O, state, and merging, and delegates every *fuzzy
decision* to LLM "judges" through a structured worklist contract — `emit` a batch of judgment
requests, let any judge fill them, then `ingest` the validated responses — with reliability checks
and a human gate before any irreversible action.

## Three ideas
1. **The worklist contract (`emit -> judge -> ingest`).** Logic lives in code; judgment lives in the
   judge; they meet at a JSON request/response schema. The **judge is swappable**: Claude Code
   subagents (near-zero cost), an API, a local model, or a human. No business logic in prompts →
   testable, debuggable.
2. **Reliability primitives for fuzzy judgment.** run-averaging (cancel model noise), adversarial
   verify (a skeptic panel that must *refute*), loop-until-dry (discovery), judge panels (diverse
   lenses). This is what makes LLM judgment trustworthy enough to drive a decision.
3. **State + gate.** A JSON source-of-state per item with dedup (idempotent, resumable); projections
   derived FROM it (never the reverse); a human checkpoint before the consequential action.

## The contract (concrete)
- **`emit`** — a deterministic script reads inputs → writes `requests.json`:
  `{ "<id>": { context, instructions, response_schema } }`. **The judge's instructions + schema
  travel INSIDE the worklist** — a judge reads one self-describing file and nothing else.
- **`judge`** — any judge reads `requests.json`, writes `responses.json`:
  `{ "responses": { "<id>": { <matches response_schema> } } }`. For volume, fan out across batches;
  for reliability, run N times + average, or add an adversarial-verify pass.
- **`ingest` / `build`** — a deterministic script reads `responses.json`, **validates (decode + shape
  + schema)**, merges deterministically, applies thresholds, writes outputs + the source-of-state record.

## Non-negotiables (learned the hard way on the source pipeline)
- **Validate every judge response:** try/except decode, auto-unwrap a double `{"responses": …}`,
  reject non-dict-of-dicts, skip-loud on malformed, FAIL on zero-valid. *(The original `build`
  silently shipped blank artifacts on exactly these shapes.)*
- **One canonical artifact path** that the generator writes and the recorder reads. *(The original
  broke at this seam → the source-of-state was never produced.)*
- **Tuning / personal logic lives in user DATA, not code** (comp floor, locations, titles,
  selection cut-orders). *(Hardcoding it = "one person's tool," not a skill.)*
- **Atomic state writes** (tmp + `os.replace`); **dedup** against existing records (never reprocess);
  no un-idempotent destructive side effects.
- **Validate inputs too:** e.g. a `": "` colon-space inside a YAML bullet silently parses to a dict
  and yields blank output — guard it.

## The seven incident classes (check at BUILD time, not just review time)
Every one of these has bitten a real build at least once; class (f) was reintroduced in new
code ONE DAY after being fixed elsewhere. Walk this list while writing, before the gate:
- **(a) garbage judge output** — every response load: try/except decode, auto-unwrap double
  `{"responses":…}`, shape/schema check, skip-loud per item, DIE on zero valid.
- **(b) YAML colon-space** — any string field can silently parse as a dict; `_require_str`
  everything, catch `yaml.YAMLError` with a quote-the-string hint.
- **(c) ids are filenames** — validate against `^[A-Za-z0-9][A-Za-z0-9._-]*$` + no `..`.
- **(d) projections that shrink** — human-facing queues/reports/docs are rebuilt from ALL
  records on every write, never from the current batch.
- **(e) non-atomic writes** — tmp + `os.replace` for records AND human-facing outputs.
- **(f) config drift between stages** — thresholds/policy used downstream are FROZEN into the
  worklist or the record at emit/build; live config is consulted only to WARN about drift.
- **(g) bare reads mid-loop** — record/state loads are defensive (skip-loud or die-with-
  guidance), never a raw traceback after partial mutation.

## Proven extensions (production-tested on the source, 2026-07)
Optional modules, not part of the minimal core. Each is a candidate v2 feature for any skill built
from the pattern; each earned its place by failing without it or paying for itself in the field.

- **Cumulative merge on ingest.** An incremental batch MERGES into the graded set (this batch's ids
  replace their prior entries; everything else preserved); a from-scratch rewrite is a deliberate
  flag (`--no-merge`), and re-graded items print a loud old→new diff before anything is overwritten.
  *(The source's first live weekly run silently shrank 633 graded records to 74 — replace-mode loses
  history the moment the pipeline goes incremental.)*
- **Field-level ownership.** When humans and the pipeline share a record, every field gets exactly
  ONE authoring surface: machine fields written by the pipeline and projected outward; human fields
  authored on the human's surface (sheet/UI) and mirrored back by a true-mirror sync (blank clears).
  Never two writers on one field.
- **Config single-source.** One tiny module resolves every person/deployment-specific number
  (thresholds, floors) from the user's data file, with resolution order CLI flag > stored value >
  profile > constant. No script carries its own copy.
- **The critique gate.** After `build`, a QA pass over the artifacts: deterministic checks (page
  count, banned phrases, required terms mirrored, anchor facts intact) + an agentic
  "what would make this stronger" review, with every finding ROUTED to the first stage where it
  applies (user data / policy / build / needs-input-only-the-human-has). Loop until clean; don't
  ship a package that hasn't cleared it.
- **The gaps loop (demand feedback).** Judges surface terms the profile/rubric couldn't credit;
  a script aggregates demand across items (counts, distinct days); an agent curates
  relevance; the HUMAN ticks "truthfully mine"; ticked items become fold-into-the-profile action
  rows until the profile actually covers them. The user's data gets richer every cycle — and
  nothing ever auto-edits it.
- **A second-opinion decision layer.** A separate emit→judge→ingest metric deliberately DISTINCT
  from the fit score — e.g. P(success | act now) plus the single highest-leverage `top_action`
  and the boosted score after completing it. Fit says "is this a match"; likelihood says "is this
  worth acting on next, and what one move raises the odds."
- **Freshness / de-listing.** Stamp `last_seen` on every record whose source item is still live;
  mark vanished ones Closed (never delete, never touch human-advanced statuses, reopen on
  reappearance). Any watched external inventory rots without this.
- **Time-bound triggers.** A read-only pass over the state store for overdue actions, gone-cold
  items, and stage-change triggers, emitting notifications/prep-packs — logic separate from the
  schema owner, window-gated, `--dry-run` writes nothing.
- **A thin stage orchestrator.** A shell entry point (`pre` / `post` / …) that sequences the
  scripts with banners and ZERO logic, leaving a slot where the judge runs between stages. This is
  what a cron/VM/scheduled agent calls.
- **Guard-hook layer (session-only belt).** A portable PreToolUse hook (stdlib-only script + JSON
  config) that HARD-BLOCKS consequential actions taken AROUND the scripts — auto-send/submit, secret
  write/commit, edits to human-authored data — with a hardcoded safety FLOOR (works with no config)
  and fail-closed semantics. **In-script checks guard what goes THROUGH the script; the hook guards
  what goes AROUND it** — in the one deployment (a live Claude Code session) where an autonomous
  model can act off-script. Complements, never replaces, the in-script checks; opt-in per project or
  user-global; each deployment carries its own config; JSON (not YAML) because the hook runs under
  bare system `python3`. Origin: the RCA guard-hook pattern, adapted. See `shared/hooks/DESIGN.md`.
  *(Built + deployed 2026-07-08; backport #2.)*
- **Un-skippable review gate (STANDARD).** Rigor made structural: a skill ships only if it carries a
  PASSING, HASH-KEYED review attestation, enforced by CODE (not a prose "please review first" that a
  drifting model ignores). The content hash re-locks the gate on any post-review edit (no shipping v2
  on a v1 review); `attest` refuses to launder a fail. Universal **gate**, review **depth** calibrated
  to stakes via `review-tiers.json` (trivial→critical). Layers: finalize-step `check` + git pre-push
  (fail-closed, the real publish boundary) + a Stop-hook belt (fail-open, never traps). The model
  *proposes*; **code disposes** — drift is irrelevant because the model never decides. Honest bar:
  skipping requires a deliberate, visible act, never accidental omission. Meant to be standard across
  all builds (finalize gate + pre-push + `SKILL.md` declaring a `review_tier`). Built + tested
  (all suites green — counts in `shared/review-gate/README.md`).
