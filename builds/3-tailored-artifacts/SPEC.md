# Build 3 — tailored-artifacts

**Goal:** the resume-tailoring engine, generalized — **personalized artifacts at scale**: select from
a superset profile and tailor one artifact per target, in the target's language, with a truth
guardrail. First use: venue/client sales-outreach — personalized outreach at scale.

## v1 scope (tight core)
- **`emit`** — a superset profile/offer + a list of targets (each with context) + a response schema →
  a per-target worklist.
- **`judge`** — tailor each artifact: interpret the target, select the relevant proof points, mirror
  the target's framing, **truth guardrail both ways** (never fabricate; never undersell). Grounded voice.
- **`build`** — render (email / letter / proposal / doc). **Stop before send** — a human reviews + sends.
- Reuse `generate.py`'s interpretive method (genericized out of Build 1) — same core, different artifact.
- The source (as of 2026-07) has directly reusable machinery beyond the method: the
  **critique gate** (`generate.py critique`/`critique-report` — deterministic checks + agentic
  review, findings routed to the responsible stage) and the **`tailoring_policy` pattern**
  (all person-specific tailoring rules in the user's data file, script carries generic mechanics
  only). Port both — they're what made the source's packages pass review 11/11.

## Notes
- **Lower polish is fine** (personal), but it still follows the pattern + the human gate (never auto-send).
- Good place to prove the core generalizes beyond resumes with minimal new code.

**Done = it produces tailored outreach the author actually uses.** Run a *light* [../../REVIEW.md](../../REVIEW.md)
pass (skip the public-packaging lens; keep robustness + the human-gate check).
