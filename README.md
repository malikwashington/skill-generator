# skill-generator

A harness for building **LLM-judgment pipelines you can trust** as self-contained Claude Code
skills. Deterministic code owns state and safety; LLM judges make the fuzzy calls behind a
validated JSON contract; and a skill **can't ship until it passes a code-enforced, hash-keyed
adversarial review** — governance the code enforces, not a prose "please review first." The skills
built this way tailor an artifact per target or decide what to act on next, always with a human in
the loop before any consequential action.

**What ships in *this* repo is the governance** — the [review gate](shared/review-gate/) and the
[fail-closed guards](shared/hooks/) — plus the *worklist pattern* it enforces ([PATTERN.md](PATTERN.md)).
The runnable `emit → judge → ingest` engine lives in each shipped skill (below) and in the larger
private job-search system this pattern was proven in — discovery, scoring, résumé tailoring, and
source-grounding gates I run on my own data. The pattern is generalized so the same
machinery applies across domains (recruiting, procurement/RFP, grants, sales outreach, moderation,
triage).

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
