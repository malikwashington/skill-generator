# Review structure — gate every build before "done"

A build is not "ready" until it passes this. The source pipeline *felt* done and wasn't — no
SKILL.md, value welded to one person, the chain never closed (empty state), and a live key on disk.
Rigor is the point of this project.

## How to run
Spawn a multi-agent review (one agent per lens), each **reading the actual build artifacts** (not
the plan), returning a structured verdict; then synthesize a blunt readiness call + a prioritized
gap list. Re-run after closing gaps.

**Lens coverage is now enforced in code, not left to this prose.** The result artifact a full-pass
attestation is derived from must declare a `lenses[]` (each lens a distinct run with a verdict), and
[`shared/review-gate/`](shared/review-gate/) refuses the attestation if a tier's `required_lenses`
aren't all present. So under-running the review — the failure that prompted this — is structurally
refused, not just discouraged here. Each lens below maps to a `required_lenses` entry.

## The 5 lenses (rubric)
1. **Skill design / DX** — is there ONE authoritative `SKILL.md` (frontmatter + a concrete
   when-to-invoke trigger)? Is the invocation flow clear (what the user types, what happens, what
   they decide)? Does the `emit→judge→ingest` loop read cleanly for a model driver? Where would a
   user or the model stall?
2. **Completeness** — does it run the WHOLE core loop end-to-end, *unattended, with documented
   defaults*? Any broken seam, half-wired step, or manual glue a human had to supply?
3. **Packaging / portability** — can a STRANGER install + run it with none of the author's accounts?
   Deps, setup friction, **no secrets / no personal data**, example inputs, `.gitignore`, onboarding.
4. **Robustness / failure-modes** — validation on every judge response, atomic writes, retries,
   schema checks, preflight/cold-start. Does it fail LOUD, never silently ship garbage?
5. **Adoption / honest appeal** — would anyone use it? Value vs alternatives, an honest "who it's for
   / not for," and a scope-bloat check.

## Always-check failure modes (from real incidents this project inherits)
- [ ] No secret or personal datum anywhere in the shippable tree (grep for keys, emails, real names).
- [ ] Judge-response validation: empty `{}` and double-wrapped `{"responses":…}` both fail loud.
- [ ] Inputs validated (the YAML colon-space-as-dict trap won't yield blank output).
- [ ] One canonical artifact path; the source-of-state is actually written (non-empty after a default run).
- [ ] Tuning / personal logic is in user data, not hardcoded.
- [ ] Idempotent + dedup; atomic writes; no un-idempotent destructive side effects (the Drive
      uploader made 40 dup folders — don't ship that class of thing).

## Gate
Readiness ∈ {`not_ready`, `close`, `ready`}. **Ship only at `ready`.** Record each review's verdict +
gaps alongside the build's `SPEC.md`.
