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

## E. The origin system, capability by capability
Sections A–D describe the *general* shape. This is the *concrete* instance the abstraction was
extracted from — the private job-search pipeline in full, so the pattern has a visible proving
ground. **25 capabilities; three are the public repos above, the rest run privately on my own
data.** It's a loop, not a line — the gaps it surfaces fold back into the profile, so each cycle
compounds. ([Visual map →](https://malikwashington.github.io/skill-generator/).)

**The profile is the hub** — the artifact every stage judges against, tailors from, and enriches;
not a capability but the substrate they all operate on. The system's one honest boundary follows
from that: the guards defend against untrusted *external* input, but grounding proves a claim
traces to the profile, **not that the profile is true**. Catching a user who misrepresents
*themselves* was never designed or tested for — that integrity is the operator's, by design.

**Trust spine** — primitives that run under every stage:
- **skill-generator** *(public)* — a hash-keyed review gate a skill can't ship without, plus fail-closed guards that hard-block send/submit/secret surfaces.
- **provenance** — JD-blind judges verify every synthesized claim entails from its source; a fabrication can't reach a document.
- **confined judges** — scoring, tailoring, and critique run read-only behind a data fence, so untrusted text can't hijack an agent that acts.
- **validate + average** — every response is schema-checked and rejected loud if off-contract; N runs averaged, real disagreement flagged not smoothed.

**01 · Discover** — find every role worth judging without re-processing one twice:
- **multi-ATS pull** — eleven applicant-tracking systems normalized behind one contract.
- **seed → resolve → firehose** — a company name or role lead becomes a real board by probing ATS slugs.
- **source-independent dedup** — the same posting seen across sources collapses to one.
- **de-listing + dead-board cache** — vanished postings self-close; 404 boards are remembered and skipped, so a huge universe stays cheap to re-pull.

**02 · Score** — judge true fit from evidence, then cancel run-to-run noise:
- **interpretive fit** — a four-dimension composite reasoned from the whole profile, crediting transferable evidence.
- **3× run-averaging** — score three times and average in code; a wide split is a finding to re-judge, never averaged away.
- **comp + near-miss gates** — a hard pay floor with a graded near-miss band, so a pay-only miss still surfaces.
- **false-negative audit** — a monthly pass over everything the relevance gate dropped, catching a good role a hand-tuned regex filtered.

**03 · Ground** — tie every assertion to the source:
- **source entailment** — isolated judges see only the statement and its source lines; an informed judge rationalizes, an isolated one checks entailment.
- **grounded coverage map** — every skill and JD term cited to the bullet that homes it, or flagged uncited, so keep/drop decisions are grounded.

**04 · Generate** — express the fit, then compose the strongest single page:
- **tailor-artifacts** *(public)* — one personalized draft per target with a structural truth gate; stops at Ready for Review.
- **compose** — the judge ranks bullets and sections by impact; the code measures the rendered page and cuts only as far as the overflow requires, never stranding a JD term that is truthfully defensible by demonstrated experience.
- **fill-to-page** — an automatic loop that pulls the highest-value unused evidence into an under-filled page.
- **critique + blind screen** — an informed critic gate plus a cold reader who knows only the résumé and the posting.

**05 · Decide** — not "is this a match?" but "if I act now, does it work, and what move helps most?":
- **lna** *(public)* — likelihood + the single highest-leverage next action + the odds after it, ranked by leverage.
- **interview likelihood** — P(first-round interview | apply), judged distinct from fit.

**06 · Track** — the filesystem is the system of record; every dashboard is a projection:
- **durable records** — one schema-validated JSON per posting, with field-level ownership so nothing is written twice.
- **dashboard projections** — shortlist, funnel, near-misses, most-likely, gap-roles — all regenerated from the records.
- **time-based triggers** — overdue/cold nudges and interview-prep packs on a fresh gate stamp.

**07 · Learn** — the loop closes; each cycle feeds the next:
- **gaps loop** — every term the scorer can't yet credit surfaces with demand evidence into a growth tracker (the Profile Gaps sheet); the market's own signal, turned into a to-do list.
- **claimed vs demonstrated** — the sheet separates a skill I'd *claim* is mine from one *demonstrated* in the profile. The first prompts fleshing out the profile until the claim is defensible; the second (a genuine gap) prompts growth. Either way the profile grows only on demonstrated experience, so its claims stay defensible — the same standard `compose`'s fail-safe enforces on the page.
