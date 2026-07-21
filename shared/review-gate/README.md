# review-gate — un-skippable adversarial review, enforced in code

*Makes a rigorous review a **precondition of shipping**, not a step a model can decide to skip.
The enforcement is code and hooks, not prose — because a stated rule ("run the review first")
drifts, and the whole point is that it can't.*

---

## The problem it solves
The factory produces skills. Its quality bar is an independent, adversarial, multi-agent review
(6 hostile lenses → adversarial verify → completeness critic). But if that review is invoked by
*instruction* — "the process says to review before shipping" — a drifting or forgetful model skips
it, half-runs it, or declares "looks good." That is the exact failure the review exists to catch,
now applied to the review itself.

**review-gate removes the decision from the model.** A skill only ships if it carries a **passing,
hash-keyed review attestation**, and that is checked by *code* at every ship boundary.

## The guarantee
Every attestation records the **sha256 of the skill's content at review time**. The gate recomputes
that hash and **refuses if a single byte drifted**. So:

- You **cannot ship** a skill that was never reviewed (no attestation → gate fails).
- You **cannot ship v2 on a v1 review** — editing anything after attesting changes the hash and
  **re-locks the gate** (this is the load-bearing property; verified by test).
- You **cannot launder a failing review** — `attest` refuses any verdict that isn't `pass`.
- You **cannot ship on an incremental review** — an incremental `attest` writes *nothing* to the
  attestation; it only appends to the history log, so the gate still sees "no attestation."
- You **cannot ship on an under-scoped review** — a full pass must DECLARE the tier's required
  reviewing passes (lenses), each as a distinct run; a `findings[]` list alone no longer proves the
  review's breadth. See **Lens coverage** below.

## Lens coverage — the review's *breadth* is enforced, not just its *existence*

`attest` derives the findings count from a result artifact, but a findings list alone can't tell a
3-lens pass from a 6-lens pass — so "I ran fewer lenses" used to be invisible to the gate (the exact
drift the gate kills, one level up). Now each tier declares `required_lenses` in
[review-tiers.json](review-tiers.json), and the result artifact must carry a `lenses[]`:

```json
"lenses": [
  {"lens": "correctness", "run_id": "<distinct id>", "verdict": "pass"},
  {"lens": "security",    "run_id": "<distinct id>", "verdict": "concerns"},
  {"lens": "dx",          "run_id": "<distinct id>", "verdict": "pass"},
  {"lens": "packaging",   "run_id": "<distinct id>", "verdict": "pass"}
]
```

Each required lens must appear as `{lens, run_id, verdict, evidence:{path, sha256}}`. The gate
requires each with a `verdict` (a lens with none didn't run), a **distinct `run_id`** (so one pass
can't be relabelled as several), and — the load-bearing part — **hash-bound evidence**: a transcript
file that exists under the skill, whose sha256 matches, and which itself records
`artifact_hash == the reviewed content`. So a declared lens must be a *run* lens, bound to the exact
bytes it reviewed (a transcript from an earlier version can't be reused). `check` re-verifies the
evidence at ship time, so deleting or altering a transcript re-locks the gate. Default tiers:
`standard` requires correctness / security / dx / completeness / packaging / adoption (6);
`high` adds adversarial-verify; `critical` adds completeness-critic. The *enforcement* is code; the
*taxonomy* is data ([review-tiers.json](review-tiers.json)).

### The orchestrator is the only producer
[`orchestrate.py`](orchestrate.py) is the sole sanctioned path to a passing attestation. The review
harness spawns one agent per required lens (each writes `<lens-dir>/<lens>.json` with its verdict +
findings); `orchestrate.py finalize` then writes each hash-bound transcript, assembles the result,
and calls `attest`. It refuses if any required lens produced no output (a lens that didn't run), and
it never *forces* a pass — it asks for one and the gate disposes (a confirmed serious finding is
refused by the gate). There is no bare-`attest --from-result` shortcut: without a complete evidence
bundle, no attestation.

**The honest limit** (unchanged, and stated plainly): you own the machine, so you can always edit the
hook, `git push --no-verify`, or forge a self-consistent hash-bound transcript. What the gate
guarantees is that shipping without a real, full-breadth, current-content review is a **deliberate,
visible act of circumvention** — never accidental omission, never a quiet model shortcut. The single
sanctioned bypass is `override` (human-named, justified, permanently recorded).

## The enforcement stack (where each layer sits)

| layer | mechanism | role |
|---|---|---|
| **Attestation** | hash-keyed `.review-attestation.json` at the skill root; `attest` is the **sole writer** | the proof of review, bound to exact content |
| **Ship gate (code)** | `review_gate.py check` — exit 0 shippable, exit 2 + loud reason otherwise | the load-bearing check; the finalize step calls it |
| **Git pre-push (code)** | `pre-push` hook runs `check-all` over the repo; **fails closed** (blocks the push) | can't *publish* unreviewed work, even from an unhooked session |
| **Stop hook (belt)** | `review_gate_hook.py` blocks ending a turn while a skill is unattested/stale; **fails open** | catches in-session drift live; never traps the user |
| **Tier calibration** | `review-tiers.json` — universal gate, review *depth* scaled to stakes | rigor everywhere without over-reviewing trivia |

**The principle:** the model *proposes* a skill; **code disposes** of whether it ships. Drift is
irrelevant because the model was never the one deciding.

## Files
- `review_gate.py` — core library + CLI (`attest` / `mark-baseline` / `check` / `check-all` /
  `status` / `history` / `next-scope` / `override` / `hash`). Stdlib only.
- `orchestrate.py` — the review orchestrator: assembles hash-bound evidence from the lens agents'
  outputs and attests. The sole sanctioned producer of a passing attestation.
- `review-tiers.json` — the tier bars + each tier's `required_lenses`. Override path with `$REVIEW_TIERS`.
- `review_gate_hook.py` — **Stop** hook; fail-open; nudges once per turn (honors `stop_hook_active`).
- `pre-push` — git pre-push hook; fail-closed; recurses to find skills anywhere under the repo root.
- `test_review_gate.py` — 156 offline checks; `test_orchestrate.py` — 18 more; `../hooks/test_guards.py` — 54; `../hooks/test_gate.py` — 15. No third-party deps.

## The attestation
`<skill>/.review-attestation.json`, written **only** by `review_gate.py attest` — and *only* by a
**full + pass** call (an incremental or failing review never writes it):
```json
{
  "skill": "my-skill",
  "artifact_hash": "<sha256 of skill content at review time>",
  "review_tier": "high",
  "review_scope": "full",
  "verdict": "pass",
  "review_verdict": "solid-with-fixes",
  "confirmed_findings": 0,
  "run_id": "wf_...",
  "reviewed_at": "<iso, supplied by caller>",
  "reviewer_version": "1"
}
```
`review_scope` is always `"full"` by construction — `attest` only ever writes the attestation for a
full passing review — and `check` asserts it defensively.

## The review-history log
`<skill>/.review-history.jsonl`, append-only, one compact JSON object per line. **Every** `attest`
call lands a line here regardless of scope or verdict — the full audit trail of what was reviewed
when. It is excluded from the content hash (see below), so appending to it never invalidates the
attestation it audits. Read it with `review_gate.py history --skill <dir>`:
```json
{"scope":"incremental","verdict":"fail","artifact_hash":"...","review_verdict":"needs-work","confirmed_findings":2,"run_id":"wf_1","reviewed_at":"..."}
{"scope":"new-baseline","artifact_hash":"...","note":"rewrote the approach ground-up","marked_at":"..."}
{"scope":"full","verdict":"pass","artifact_hash":"...","review_verdict":"solid-with-fixes","confirmed_findings":0,"run_id":"wf_2","reviewed_at":"..."}
```
Alongside review records, the log can carry a **`new-baseline` (redesign) marker** — written
**only** by `mark-baseline` (the sole writer of baseline events, just as `attest` is the sole writer
of attestations). It has no verdict; it declares a fundamental rewrite, writes nothing to the
attestation, and deletes nothing. `next-scope` reads it when it is the most-recent event and returns
`full` — so a redesign→full is *computed*, not hand-picked.

## The content hash
`compute_artifact_hash()` folds every file's `(relpath, sha256)` into one sha256, **sorted by
relpath** so it's independent of walk order, and hashing the relpath too so a **rename** changes it.
Excluded (so they never perturb the hash): the attestation itself, the `.review-history.jsonl` log,
`reviews/`, `.git/`, `__pycache__/`, `*.pyc`. Writing the attestation *or* appending to the history
log therefore never invalidates the hash it's keyed to (proven by test).

## Review tiers (the calibration)
The **gate is universal**; the review **depth** scales to stakes. A passing attestation must clear
its tier's bar:

| tier | bar |
|---|---|
| `trivial` | any passing attestation |
| `standard` | `pass` + ≤ 3 confirmed findings |
| `high` | `pass` + 0 confirmed findings + `review_verdict ∈ {solid-with-fixes}` |
| `critical` | `pass` + 0 confirmed findings + `review_verdict ∈ {solid-with-fixes}` |

Edit `review-tiers.json` to tune bars or add tiers; `check()` enforces `min_verdict`,
`max_confirmed_findings`, and the optional `require_review_verdict_in` allow-list.

## CLI
```bash
# record a FULL passing review — the ONLY call that writes the ship attestation
# (sole writer; refuses non-pass verdicts; also appends to the history log)
python3 review_gate.py attest --skill ./my-skill --scope full --tier high \
  --verdict pass --review-verdict solid-with-fixes \
  --confirmed-findings 0 --run-id wf_abc --reviewed-at 2026-07-09T12:00:00Z

# log an incremental review — history-only, NEVER ship-eligible (exit 0)
python3 review_gate.py attest --skill ./my-skill --scope incremental --tier high \
  --verdict pass --review-verdict solid-with-fixes \
  --confirmed-findings 1 --run-id wf_abc --reviewed-at 2026-07-09T11:00:00Z

# declare a fundamental redesign — appends a new-baseline marker to history (sole
# writer of baseline events; writes NO attestation and deletes nothing). The next
# next-scope reads it and returns `full`, resetting the review cadence:
python3 review_gate.py mark-baseline --skill ./my-skill \
  --note "rewrote the approach ground-up" --marked-at 2026-07-09T12:00:00Z

python3 review_gate.py check   --skill ./my-skill       # the gate: exit 0 / exit 2
python3 review_gate.py check-all --root ./skills        # gate every skill; exit 2 if any fail
python3 review_gate.py status  --root ./skills          # attested-current | stale | missing
python3 review_gate.py history --skill ./my-skill       # the append-only review trail

# compute the NEXT review scope in code (never chosen by hand). Prints exactly one
# token to stdout — up-to-date | full | incremental — with a one-line human
# explanation on stderr, so the token stays machine-parseable:
python3 review_gate.py next-scope --skill ./my-skill    # -> full   (stderr: [next-scope] ...)
NEXT=$(python3 review_gate.py next-scope --skill ./my-skill 2>/dev/null)
```
`--scope` is required. `--scope full --verdict fail` still refuses to attest (exit 2) but logs the
failed review to history; `--scope incremental` never writes the attestation under any verdict.

## Wiring (each layer is opt-in)
1. **Ship gate** — the factory's finalize step runs `check --skill <dir>` and refuses to ship on
   non-zero. (The review workflow runs first and produces the verdict fed to `attest`.)
2. **Git pre-push** — `ln -sf ../../shared/review-gate/pre-push .git/hooks/pre-push`. Blocks a push
   if any skill fails the gate. **This is the real "cannot be skipped" boundary** — a bypassed
   session can't route around it.
3. **Stop hook** — register `review_gate_hook.py` as a `Stop` hook in `settings.json` (set
   `REVIEW_SHIP_ROOT` to the skills root). Nudges you in-session; belt, not spine (hooks are
   session-only).

## Fail directions (deliberately split)
- **Gate + pre-push fail CLOSED** — an unloadable checker or an unattested skill *blocks*; waving
  unreviewed work through is the worse outcome at a ship boundary.
- **Stop hook fails OPEN** — a Stop hook that denied on error would trap you in an un-endable turn.
  Same discipline as the guard-hook layer's `gate.py`.

## The honest limit
You can always bypass your **own** gates — edit the finalize script, `git push --no-verify`, delete
the hook. No system stops its author with root. The achievable and correct bar is: **skipping the
review requires a deliberate, visible act of circumvention — never accidental omission or model
drift.** That is the same bar the client-data quarantine holds, and stating it plainly is the
mature posture, not a hole.

## Review cadence (full ↔ incremental — enforced in code, not documented)
Reviews come in two scopes — **full** (the whole artifact, nothing excluded) and **incremental**
(only the changed/resolved parts, cheaper). The cadence isn't a process you're asked to follow; it
is a **structural property of `attest`**:

- **Only a `--scope full --verdict pass` call writes `.review-attestation.json`.** That is the sole
  path to a shippable attestation. It stamps `review_scope: "full"` into the file, and `check`
  asserts it.
- **An incremental review writes *nothing* to the attestation** — it only appends to
  `.review-history.jsonl` and exits 0. A skill whose only reviews were incremental has no
  attestation, so `check` fails with the plain "no attestation" reason. **Shipping on an
  incremental is therefore impossible by construction, not by convention.**
- **A failing full review still refuses the attestation (exit 2)** but is appended to history, so
  the audit trail records *that* the full review ran and failed.
- **Hash-drift re-locks regardless of scope.** Because the attestation is keyed to the content hash,
  editing anything after a full pass invalidates it — so "ship v2 on a v1 review" is impossible too.
- **`history --skill <dir>`** prints the append-only trail (scope, verdict, hash-prefix, run-id,
  timestamp), newest last — the full record of what was reviewed when.

The intended loop still is: full baseline → resolve → incrementals until one passes → a final full →
ship on that full pass. But you don't have to *trust* the loop: the only artifact the gate honors is
a full passing attestation, and nothing else can forge one. **The cadence is enforced in code, not
documented.**

### Which scope next? — `next-scope` (COMPUTED, not chosen)
The full-vs-incremental choice used to be a judgment call. It no longer is: `next-scope --skill <dir>`
**computes it in code** and prints exactly one token to **stdout** — `up-to-date` | `full` |
`incremental` — with a one-line human explanation on **stderr** (so the stdout token stays
machine-parseable). It reads only existing artifacts: the current content hash, the hash-keyed
attestation (written solely by a full+pass), and the append-only history. The decision, exactly as
implemented:

| # | condition | result |
|---|---|---|
| 1 | attestation exists **and** its `artifact_hash` == current content hash | `up-to-date` |
| 2 | attestation exists but the hash **drifted** (a passed full, now edited) | `full` — a passed full is the load-bearing truth; drift ⇒ re-earn it fully |
| 3.0 | no attestation, most-recent history entry is a `new-baseline` marker | `full` — a declared redesign resets the cadence; takes precedence over the last-review-scope rules below |
| 3a | no attestation, **history empty** | `full` — bootstrap: the first review is full |
| 3b | no attestation, most-recent history entry is `incremental` **and** `pass` | `full` — an incremental cleared ⇒ attempt the full baseline |
| 3c | no attestation, otherwise (last review was a failed full, or any failed/other incremental) | `incremental` — keep grinding |

So a history of `[full/fail, incremental/fail, incremental/fail, incremental/fail]` with no
attestation returns `incremental` (rule 3c); the moment an incremental *passes* it flips to `full`
(rule 3b); once a full passes and nothing has drifted it reports `up-to-date` (rule 1); edit a byte
after that and it returns `full` again (rule 2). **The cadence is now computed, not chosen** — the
same discipline as the gate itself: the model proposes, code disposes.

**Declaring a redesign (new baseline).** An incremental review resolves findings against the current
design; a fundamental *rewrite* is different — its correct next review is a fresh `full`, not another
incremental. That decision is **logged, not hand-picked**: `mark-baseline --skill <dir>` appends a
`new-baseline` event to the history (the audit trail stays intact — nothing is deleted, no
attestation is touched), and `next-scope` reads it (rule 3.0) to return `full`. So a redesign→full is
*computed* from an auditable event, exactly like every other scope decision.

## Meant to be standard
This is the enforcement primitive behind "rigor is structural, not discretionary." Applied
factory-wide (finalize gate + pre-push + the `SKILL.md` template declaring a `review_tier`), **no
skill ships from the factory without passing an adversarial review calibrated to its stakes.**
