# USE-CASES — where the abstracted skills apply

The point of this factory: the job-search pipeline is one instance of a general shape —
**watch an external inventory, judge each item against your own profile/rubric, produce a
tailored response for the best ones, track everything in durable local state, and stop at a
human gate.** This file maps that shape onto other domains, two ways: the whole workflow, and
each stage as a standalone skill.

## A. The full workflow, re-targeted
Same loop (`discover → score → tailor → record → human gate`), different nouns. Roughly in
order of how directly the existing code transfers:

| use case | discover | score against | tailor | human gate |
|---|---|---|---|---|
| **Job search** (origin) | ATS boards | candidate profile | resume + cover letter | submit |
| **Venue/booking outreach** | venue/promoter/event listings | performer profile + offer | pitch email + one-sheet cover note | send |
| **Grant seeking** (nonprofits, artists, researchers) | grants.gov, foundation lists, arts councils | org/artist profile | LOI / proposal draft | submit |
| **RFP / bid pipeline** (agencies, contractors) | SAM.gov, RFP aggregators, portals | capability profile, bid/no-bid rubric | capability statement, proposal sections | bid decision |
| **Freelance marketplaces** | Upwork/Contra/job boards | skills profile + rate floor (≈ comp floor) | per-gig proposal | submit |
| **Investor / fundraising outreach** | investor databases, portfolio pages | thesis-fit vs company profile | pitch email + deck talking points | send |
| **Speaking / CFP pipeline** | conference CFP feeds | talk-abstract inventory | per-conference abstract | submit |
| **PR / press outreach** | outlets, journalists, beats | story vs beat fit | per-outlet pitch | send |
| **Admissions / program applications** | program listings | applicant profile | essays / statements | submit |
| **Apartment / real-estate hunt** | listing feeds | needs + budget (hard gates: location, price floor/ceiling) | inquiry message | contact/apply |
| **Hiring, reversed** (Build 2 territory) | inbound applicant queue | role rubric | interview plan per candidate | advance/reject |
| **Vendor / procurement evaluation** (Build 2) | inbound proposals | weighted rubric | clarifying-question sets | award decision |
| **Compliance / underwriting / claims triage** (Build 2) | case queue | policy/regulatory rubric | flag rationale + provenance | the decision itself |

The mechanics transfer almost verbatim: `posting_id` → any stable item id; comp floor → any hard
numeric gate (rate floor, grant size, rent ceiling); de-listing → listings/CFPs/RFPs that close;
the gaps loop → "what the market keeps asking for that my profile can't credit yet."

## B. Each stage as a standalone skill
Each row of the pipeline is independently useful — that's what makes this a factory rather than
one product.

- **discover** (poll → normalize → dedup vs state → de-list vanished):
  price/inventory watching, regulatory-docket or public-notice monitoring, arXiv/paper alerts on
  a research profile, competitor job-board watching as hiring-signal intel, new-release/tender
  monitoring. Anywhere "watch a set of external boards and tell me only what's new."
- **score-queue** (emit → judge → ingest vs a rubric; run-averaged; hard gates; cumulative merge):
  support-ticket triage, lead qualification, resume screening, moderation queues, literature-review
  relevance screening, deal/portfolio screening, demo/submission triage (A&R, film festivals,
  accelerators). This is Build 2's core — the reliability + audit-trail posture is the product.
- **tailor-artifacts** (select from a superset → mirror the target's language → truth guardrail →
  render → critique gate): personalized outreach at scale, donor/renewal letters, per-funder
  proposal variants, per-segment marketing copy, personalized lesson plans or client reports.
  This is Build 3's core.
- **record-state** (schema-validated JSON records, one file per item, dedup, field-level
  ownership, projections): a local-first CRM/funnel for any of the above — the sheet is a
  dashboard, never the source of truth.
- **likelihood / next-action** (a second judged metric, deliberately distinct from fit:
  P(success | act) + the single highest-leverage action + the boosted score after it):
  deal win-probability over a CRM export, grant-success likelihood, "which of these 50 leads do I
  work first and what's the one move that raises the odds."
- **gaps-loop** (aggregate what judges couldn't credit → agent curates → human ticks → fold into
  the profile): product-feature gap mining from lost deals/RFP requirements, skills-demand mining
  from a JD corpus, service-offering gaps from client requests.
- **timebound** (read-only deadline/staleness/stage-change triggers over the state store):
  follow-up nudges, renewal reminders, gone-cold alerts on any funnel.
- **critique-gate** (deterministic checks + agentic review, findings routed to the responsible
  stage): batch QA of any generated document set against brand/compliance/accuracy rules —
  usable even on documents this pipeline didn't produce.

## C. Mapping to the three builds
- **Build 1** ships the full workflow for its origin domain (job search) — the reference
  implementation of the whole loop.
- **Build 2** ships **score-queue** hardened for regulated/high-stakes review — the rows above
  marked "Build 2."
- **Build 3** ships **tailor-artifacts** — first target: venue/booking outreach
  (row 2 of table A, tailoring stage first; the full booking workflow is its natural v2).
- Remaining standalone stages (discover, record-state, likelihood, gaps-loop, timebound,
  critique-gate) are future factory outputs once the first three prove the extraction method.

## D. Positioning
The pattern, stated independent of any single use case:

> **A human-gated LLM judgment pipeline** — deterministic code owns I/O, state, and merging;
> LLM judges handle scoring and tailoring through a structured worklist contract; reliability
> comes from run-averaging and adversarial verification; a schema-validated JSON
> source-of-state carries full provenance; every consequential action stops at a human gate.
> Instances span opportunity screening + tailored response generation across recruiting,
> procurement/RFP, grants, and sales outreach.

- The enterprise-legible instance is screening/triage with audit trails (Build 2): reliability,
  human-in-the-loop, and provenance are the load-bearing properties.
- Skills are named by capability or pattern, never by a personal use case: the queue-scoring
  engine ships as **docket-llm**, the blind screener as **blind-review**.
- **Screener architecture note:** the blind-reviewer core is fully generic (it IS Build 2's
  engine); the job-specific parts are a thin swappable "screening pack" — rubric compiler
  (JD → must-haves/disqualifiers), reader persona + calibration, artifact conventions, hard
  gates. Domain packs are data, not code.
