# Build 4 — Portable RCA Tool

**Shipped to `~/Desktop/skills/rca/`.** Design docs, implementation, and the review
history all live there together.

Unlike builds 1–3 (which left `SPEC.md` here), this build's docs travelled with the
code for two reasons:

- `SECURITY.md` and `GOVERNANCE.md` are not design notes. They are the artifact a
  client, a stakeholder, or an employer reads to decide whether to trust the tool.
  They ship with the product.
- The review gate keys its attestation to a directory hash. Pointing it at a
  docs-only folder would attest the prose while leaving the code unreviewed. The
  gate now covers `skills/rca/` — code and claims together.

Review state as of the move: **no passing full review.** Six reviews in
`.review-history.jsonl`; the last full failed with 4 confirmed findings (since
fixed). `python3 shared/review-gate/review_gate.py next-scope --skill ~/Desktop/skills/rca`
computes what is owed.
