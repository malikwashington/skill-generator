# Review structure — gate every build before "done"

A build is not "ready" until it passes this. The source pipeline *felt* done and wasn't — no
SKILL.md, value welded to one person, the chain never closed (empty state), and a live key on disk.
Rigor is the point of this project.

## How to run
Spawn a multi-agent review (one agent per lens), each **reading the actual build artifacts** (not
the plan), returning a structured verdict; then synthesize a blunt readiness call + a prioritized
gap list. Re-run after closing gaps. Concretely, per lens the agent writes `<lens-dir>/<lens>.json`
(`{"lens","verdict","run_id","findings":[…]}`), then the orchestrator assembles the evidence and
attests:
```bash
# one agent per required lens writes <lens-dir>/<lens>.json, then:
python3 shared/review-gate/orchestrate.py finalize \
  --skill <skill-dir> --tier standard --lens-dir <lens-dir> \
  --run-id <id> --reviewed-at <iso8601>
# ship gate (what the guard + pre-push enforce):
python3 shared/review-gate/review_gate.py check --skill <skill-dir>
```
`orchestrate.py finalize` is the SOLE producer of an evidence-backed passing attestation; it refuses
if a required lens is missing, and the gate itself refuses if a confirmed serious finding remains.

**Lens coverage is enforced in code, not left to this prose.** The result artifact a full-pass
attestation is derived from must declare a `lenses[]` (each lens a distinct run with hash-bound
evidence), and [`shared/review-gate/`](shared/review-gate/) refuses the attestation if a tier's
`required_lenses` aren't all present. So under-running the review — the failure that prompted this —
is structurally refused, not just discouraged. Each lens below is a `required_lenses` entry in
[`review-tiers.json`](shared/review-gate/review-tiers.json).

## The six lenses (rubric)
The `standard` tier requires all six; `high` adds an **adversarial-verify** pass (skeptics try to
refute each finding), and `critical` adds a **completeness-critic** (what's missing / unverified).
1. **correctness** — validation on every judge response, atomic writes, schema checks,
   preflight/cold-start, edge cases; the core loop computes what it claims and fails LOUD, never
   silently shipping garbage.
2. **security** — no secrets / no personal data anywhere in the shippable tree; safe file handling
   (path traversal, `yaml.safe_load`), fail-closed guards, prompt-injection defense (item content is
   data, never instructions).
3. **dx** — is there ONE authoritative `SKILL.md` (frontmatter + a concrete when-to-invoke trigger)?
   Is the invocation flow clear, and does the `emit→judge→ingest` loop read cleanly for a model
   driver? Where would a user or the model stall?
4. **completeness** — does it run the WHOLE core loop end-to-end, *unattended, with documented
   defaults*? Any broken seam, half-wired step, or manual glue a human had to supply?
5. **packaging** — can a STRANGER install + run it with none of the author's accounts? Deps, setup
   friction, example inputs, `.gitignore`, onboarding; no stray/scratch files.
6. **adoption** — would anyone use it? Value vs alternatives, an honest "who it's for / not for," a
   scope-bloat check, and grounded voice (no oversell).

Reporting contract for every lens: see [Finding-reporting contract](#finding-reporting-contract-what-a-lens-may-put-in-findings) below.

## Always-check failure modes (from real incidents this project inherits)
- [ ] No secret or personal datum anywhere in the shippable tree (grep for keys, emails, real names).
- [ ] Judge-response validation: empty `{}` and double-wrapped `{"responses":…}` both fail loud.
- [ ] Inputs validated (the YAML colon-space-as-dict trap won't yield blank output).
- [ ] One canonical artifact path; the source-of-state is actually written (non-empty after a default run).
- [ ] Tuning / personal logic is in user data, not hardcoded.
- [ ] Idempotent + dedup; atomic writes; no un-idempotent destructive side effects (the Drive
      uploader made 40 dup folders — don't ship that class of thing).

## Finding-reporting contract (what a lens may put in `findings`)
The gate and orchestrator count `findings`, so a lens must report them precisely:
- **`findings` are LIVE DEFECTS only** — a thing that should change. Each carries a severity in
  {`critical`,`high`,`medium`,`low`} and a `confirmed` flag. If nothing is wrong, `findings` is
  empty and the verdict is `pass`. Do **not** put verification narration ("round-2 issue VERIFIED
  FIXED", "grounded voice still clean", "by design", "intentional") in `findings` — those are
  **notes**, not defects. Put them in prose/verdict; the orchestrator drops any `findings` entry
  whose severity isn't a known defect severity (logged), so a mis-encoded note can't blow the
  finding cap or trip a false human-review item.
- **`confirmed: false` is reserved for a defect that was RAISED serious and then REFUTED** by an
  adversarial check — that, and only that, routes to the human-review queue (never auto-cleared).
  Never use `confirmed:false` to mean "this is fine" or "already resolved"; that abuses the queue.
- **The verdict must match the defects.** The orchestrator refuses a `fail`/`concerns` verdict with
  zero well-formed defects, and a `fail` with no confirmed serious defect — so a real serious
  finding can't be laundered into an uncounted note by a severity typo.

## Gate
Readiness ∈ {`not_ready`, `close`, `ready`}. **Ship only at `ready`.** Record each review's verdict +
gaps alongside the build's `SPEC.md`.
