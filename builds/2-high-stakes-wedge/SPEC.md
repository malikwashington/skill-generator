# Build 2 — high-stakes-wedge

> **Shipped 2026-07-07 as TWO skills at `~/Desktop/skills/`** (names decided by Malik):
> **docket-llm** — this spec's engine (emit/ingest/verify/decide, rubric-driven, run-averaged,
> audit records, human review queue; worked example: small-grants screening), and
> **blind-review** — the worked-example screener elevated to its own skill (blind cold reader
> + swappable domain packs; resume-vs-posting pack ships as the example).

**Goal:** the worklist pattern as a **reliable, human-gated, auditable "score a queue of items
against a rubric" skill** — for domains *required* to keep a human on the decision: underwriting,
compliance, legal review, clinical / grant / admissions screening, due diligence.

**The wedge:** there, "the model judges + flags, a human owns the decision, every run is
reliability-checked and logged" is not a nice-to-have — it's the *mandated* posture. So the pattern's
defining features (reliability + human gate + provenance) are exactly what those buyers must have,
and the fit is clear. That's why it's a strong entry point.

## v1 scope (tight core)
- **`emit`** — items + a user-supplied **rubric/criteria** + a response schema (score per criterion,
  overall, flags, and **rationale + citations** to the source).
- **`judge`** — **run-averaged** (N runs) + an **adversarial-verify** pass on borderline / high-impact
  items (a skeptic must refute). The reliability primitives are the product, not an afterthought.
- **`ingest`** — validate, merge, threshold into a **human review queue** (auto-approve NOTHING);
  write a per-decision **audit record** (inputs, rubric, scores, the N runs, what judged, timestamp).
  Provenance is the deliverable.
- Ship with ONE concrete worked example domain to prove it: **applicant/resume screening**
  (decided 2026-07-07). Chosen deliberately — it doubles as a **reviewer loop for the job-search
  pipeline**: the same engine blind-screens Build 1/3's built packages (screener sees ONLY the
  resume + the JD-derived rubric, never the superset profile — a real screener's information
  set), run-averaged, findings routed back like the critique gate. One engine, both directions
  of the hiring table; the dogfood IS the demo. JSON-only.

## Differentiators to lead with (what regulated buyers must have)
Reliability (run-averaging + adversarial verify) · human-in-the-loop by design · full
auditability/provenance · local-first (data privacy). State plainly what it does NOT do (no
auto-decisions).

## Defer to v2
Domain-specific integrations, a UI, connectors, workflow/queue management.

**Done = passes [../../REVIEW.md](../../REVIEW.md) at "ready," with the audit-trail + human-gate explicitly verified.**
