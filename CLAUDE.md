# CLAUDE.md — skill-generator

A factory that turns one proven kernel — the **worklist pattern** ([PATTERN.md](PATTERN.md)) —
into production-grade, rigorously-reviewed Claude Code Skills. It was abstracted from a real,
hand-built pipeline; the job here is to generalize that pipeline's stages so they apply to other
use cases ([USE-CASES.md](USE-CASES.md) maps the domains — the full workflow re-targeted, and each
stage as a standalone skill).

**Output target:** finished skills are self-contained folders (code + `SKILL.md` + README +
example inputs + `.gitignore`), nothing personal inside. The builds below are the first outputs;
the standalone-stage skills in USE-CASES.md §B are the backlog.

## What we're building (see `builds/`)
1. **job-search-skill** — the generalized end-to-end job-application pipeline as a skill.
2. **high-stakes-wedge** — a regulated "score a queue against a rubric, reliably, human-gated" skill
   (underwriting / compliance / legal / clinical / grant / admissions screening).
3. **tailored-artifacts** — personalized artifacts at scale.

Each build has a `SPEC.md` (goal, tight-core scope, definition-of-done).

## Prime directives (standing instructions — do not violate)
1. **Security & privacy first.** NEVER copy a secret or personal datum into a build. The upstream
   pipeline holds real credentials and personal data; those never enter a build. Each skill ships
   **code + example inputs + `.gitignore`** only; real inputs are user-supplied and git-ignored.
2. **Ship a tight CORE, not "everything."** The #1 lesson from the readiness review: insisting on the
   whole thing (incl. the Google Sheets/Drive/Apps-Script glue) is what blocks shipping. Each v1 =
   the minimal closed loop that delivers the value; projections, cosmetics, and orchestration are v2.
3. **The kernel is the worklist pattern.** Logic in code, judgment in the judge, `emit -> judge ->
   ingest`; reliability primitives; a JSON source-of-state with dedup; a HUMAN GATE before any
   irreversible action. Read [PATTERN.md](PATTERN.md) before building.
4. **Review rigorously, gate on it.** No build is "done" until it passes [REVIEW.md](REVIEW.md) (the
   6-lens review + the failure-mode checklist). Run the review; close the gaps; re-review. Don't
   declare ready until it clears.
5. **Grounded voice.** All docs/READMEs factual and understated — never superlatives. (User memory:
   `grounded-voice-no-oversell`.)
6. **The human owns the decision.** Especially the high-stakes wedge: the model judges + flags +
   provides provenance; a human gates. Never auto-act.

## How to work
1. Read [PATTERN.md](PATTERN.md), the build's `SPEC.md`, and [REVIEW.md](REVIEW.md).
2. Build the tight core for that skill (generalized from the upstream pipeline, never copying
   secrets/personal data).
3. Run the [REVIEW.md](REVIEW.md) gate (multi-agent, 6 lenses). Record findings.
4. Close P0/P1 gaps; re-review until "ready."

## Layout
- `PATTERN.md` — the reusable kernel spec.
- `REVIEW.md` — the rigorous-review structure (rubric + failure-mode checklist + how to run it).
- `USE-CASES.md` — the pattern re-targeted across domains, and each stage as a standalone skill.
- `builds/{1,2,3}/SPEC.md` — per-skill goal, scope, definition-of-done.
- `shared/` — reusable kernel components: the guard/gate hooks, the review gate, the SKILL template.
