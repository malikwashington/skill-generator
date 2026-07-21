# skill-generator

A harness for building **&mdash; and hardening &mdash; LLM-judgment pipelines you can trust** as
self-contained Claude Code skills. Deterministic code owns state and safety; LLM judges make the fuzzy calls behind a
validated JSON contract; and a skill **can't ship until it passes a code-enforced, hash-keyed
adversarial review** — governance the code enforces, not a prose "please review first." The skills
built this way tailor an artifact per target or decide what to act on next, always with a human in
the loop before any consequential action.

> **[The system map →](https://malikwashington.github.io/skill-generator/)** &nbsp;·&nbsp; a visual overview of the full private pipeline these skills were spun out of. Capability-by-capability detail in [USE-CASES.md](USE-CASES.md).

**What ships in *this* repo is the governance** — the [review gate](shared/review-gate/) and the
[fail-closed guards](shared/hooks/) — plus the *worklist pattern* it enforces ([PATTERN.md](PATTERN.md)).
The runnable `emit → judge → ingest` engine lives in each shipped skill (below) and in the larger
private job-search system this pattern was proven in — discovery, scoring, résumé tailoring, and
source-grounding gates I run on my own data. The pattern is generalized so the same
machinery applies across domains (recruiting, procurement/RFP, grants, sales outreach, moderation,
triage).

**It hardens skills you already have, too** — not only ones built here. Point the review gate and
fail-closed guards at an existing skill and the same enforcement takes hold: it can't ship until
it's passed the adversarial review, and it can't send, submit, or leak at runtime. Build a new
judgment pipeline or harden an inherited one — it's the same governance either way.

I built this to run entirely on my Claude Code subscription and without incurring additional costs:
the shipped skills judge with Claude Code subagents rather than a billable Anthropic API key, so a
full run adds nothing to my bill.

## The core: the worklist pattern

Deterministic code owns I/O, state, and merging; LLM "judges" handle every fuzzy decision through
a structured contract. They meet at a JSON request/response schema — no business logic in prompts.

```
emit  →  judge  →  ingest
```

- **emit** — code reads inputs and writes a self-describing `requests.json`: each item carries its
  context, the judge's instructions, and the response schema. A judge reads one file and nothing else.
- **judge** — any judge (a Claude Code subagent, an API, a local model, or a human) fills in
  `responses.json`. For reliability: run N times and average; add an adversarial-verify pass.
- **ingest** — code validates (decode → shape → schema), merges deterministically, applies
  thresholds, and writes a schema-validated JSON source-of-state with dedup.

The judge is swappable and the logic is testable in isolation. Full spec: [PATTERN.md](PATTERN.md).

## Origin & design philosophy

This pattern wasn't designed in the abstract — it was refined over months building a full,
private pipeline on my own data: a job-search system that discovers roles across a dozen
applicant-tracking systems, scores each against a deep profile, grounds every tailored claim to
its source, composes a one-page résumé, and decides which single move most raises the odds of an
interview. The three public skills below were spun out of that system. The philosophy is what
building it taught me:

- **Code owns state and safety; judges own the fuzzy calls.** Deterministic code does all I/O,
  merging, and gating; an LLM judge makes only the call a rubric can't — behind a validated JSON
  contract, never in free-form prose.
- **Ground every assertion to a source.** A claim that traces to nothing is a fabrication by
  construction; a source-blind judge checks entailment where an informed one rationalizes. Even
  composition decisions — what stays on the page — are cited, not asserted.
- **Reliability is structural, not hoped-for.** Judge N times and average; flag genuine
  disagreement instead of smoothing it away; validate every response against a schema and reject
  anything off-contract, loud.
- **A human gates anything irreversible.** The pipeline stops at the submit button — every time.

### The system it was forged in

That pipeline is **25 capabilities** across a trust spine and seven phases — and it's a loop, not
a line: the gaps it surfaces fold back into the profile the whole system judges against, so each
cycle compounds. Three are the public, standalone repos below; the rest run privately on my own
data. A visual **[system map](https://malikwashington.github.io/skill-generator/)**, and the full
capability-by-capability detail in **[USE-CASES.md](USE-CASES.md)**.

| phase | capabilities | public repo |
|---|---|---|
| **Trust spine** | review gate + fail-closed guards · provenance (source entailment) · confined judges · validate + run-average | **skill-generator** |
| **01 · Discover** | 11-ATS pull · seed→resolve→firehose · source-independent dedup · de-listing + dead-board cache | — |
| **02 · Score** | interpretive 4-dimension fit · 3× run-averaging + disagreement flags · comp + near-miss gates · false-negative audit | — |
| **03 · Ground** | source entailment · grounded coverage map | — |
| **04 · Generate** | per-target tailoring · compose (impact / completeness / readability) · fill-to-page · critique + blind screen | **tailor-artifacts** |
| **05 · Decide** | interview likelihood · next-action by leverage | **lna** |
| **06 · Track** | durable records · dashboard projections · time-based triggers | — |
| **07 · Learn** | gaps loop · claimed-vs-demonstrated — *surfaced demand splits into flesh-out-the-profile or a growth target; either way it folds back in* | — |

**The profile is the artifact at the center** — every role is scored against it, every draft
tailored from it, every gap folded back into it; not a stage in the pipeline but the substrate
every stage runs on. Which sets the system's one honest boundary, stated plainly: the guards
defend against untrusted *external* input — a poisoned job description can't hijack a judge or
reach a document — but grounding proves a claim traces to the profile, **not that the profile is
true**. Catching an operator who misrepresents *themselves* was never a design goal or a tested
property; that integrity is the operator's to hold, by design.

## What makes the judgment trustworthy

Two layers. **This repo ships the governance**, enforced in code here:

- **An un-skippable review gate** ([shared/review-gate/](shared/review-gate/)) — a skill ships only
  with a passing, hash-keyed review attestation; any post-review edit re-locks the gate. Enforced by
  code (a finalize check + a git pre-push hook), not by a prose "please review first."
- **PreToolUse guard/gate hooks** ([shared/hooks/](shared/hooks/)) — a portable, stdlib-only guard
  that hard-blocks send/submit/secret-write surfaces with a hardcoded safety floor and fail-closed
  semantics.

**The worklist pattern prescribes the reliability primitives** — specified in
[PATTERN.md](PATTERN.md) and implemented *inside each shipped skill*, not as an engine in this repo:

- **Run-averaging + disagreement flags** — cancel model noise; surface where independent runs split
  rather than averaging the split away.
- **Strict output validation** — every judge response is schema-checked; off-schema, out-of-range,
  or wrong-shape responses are rejected loud, never coerced.
- **A human gate before any irreversible action** — the model prepares; a human disposes. Nothing
  auto-acts.

The review methodology itself — a multi-agent, six-lens adversarial review with a failure-mode
checklist — is in [REVIEW.md](REVIEW.md). No build is "done" until it passes.

## Shipped skills

Skills built with this factory, each a standalone repo you can run:

- **[tailor-artifacts](https://github.com/malikwashington/tailor-artifacts)** — draft one
  genuinely personalized artifact per target from a superset profile: proof points selected per
  target, the target's language mirrored, anchor facts kept verbatim, a deterministic critique gate
  before anything is surfaced. Stops at *Ready for Review* — never sends.
- **[lna](https://github.com/malikwashington/lna)** (*likelihood / next-action*) — a second-opinion
  decision layer over any list of opportunities. Where a fit score answers "is this a match?", lna
  answers "if I act now, what's the probability it works, and what single move most raises the
  odds?" Output is ranked by leverage.

## Layout

- `PATTERN.md` — the reusable core spec.
- `REVIEW.md` — the rigorous-review structure (rubric + failure-mode checklist + how to run it).
- `USE-CASES.md` — the pattern re-targeted across domains, and each stage as a standalone skill.
- `builds/` — one folder per target skill with its goal, scope, and definition-of-done:
  `1-job-search-skill/`, `2-high-stakes-wedge/`, `3-tailored-artifacts/` (each a `SPEC.md`),
  and `4-rca-tool/` (a pointer README — that build's docs travel with its code).
- `shared/` — reusable core components: the guard/gate hooks, the review gate, the SKILL template.
- `CLAUDE.md` — working conventions for building a skill in this repo.
