# Build 1 — job-search-skill

**Goal:** the generalized, end-to-end job-application pipeline as a Claude Code Skill —
`discover → score → tailor → record → Ready for Review` (a human gates submission), producing JSON
state + tailored PDFs. The valuable "whole thing" is **this core loop**, not the Google glue.

**Source to genericize:** an upstream job-search pipeline (kept private; not part of this repo) —
`discover.py`, `rank.py`, `generate.py`, `record.py`, `audit.py`, plus `pipeline_config.py` (the
config-single-source pattern), `application_record.schema.json`, `run.sh`, and the offline
test-suite pattern (`test_*.py`). **Never copy** anything on the never-copy list (real credentials,
personal profile data, gap stores, `runs/`, `artifacts/`, review content, scratch). SKILL.md spine:
rules → setup → script table → run order → PRINCIPLES.

> **Source state (re-checked 2026-07-07):** the source is now a fully operational, twice-reviewed
> system — weekly loop proven live, 714 records, 13 offline test suites green. Two of the original
> P0s were fixed in the source itself; the conversion job is porting a hardened source, not
> repairing a broken one. Statuses below updated accordingly.

## v1 scope (tight core — per the readiness review)
- Scripts: discover, rank (emit/ingest, cumulative merge), generate (emit/build/critique), record,
  audit, a genericized pipeline_config. **JSON-only state.**
- The **human drives the judge step by hand** (read the worklist, score/tailor, write responses) —
  scripted fan-out is v2.
- Ship: `SKILL.md` + `README.md` + `profile.yaml.example` + `target_companies.csv.example` +
  `.gitignore` + offline tests with genericized fixtures.

## Definition of done (statuses updated 2026-07-07)
- **P0 — secret containment: STILL OPEN.** The source folder holds a live GCP service-account key
  + real sheet IDs with no git/.gitignore; key rotation outstanding. `.gitignore` in the build
  excludes secrets/personal data; nothing personal in the shipped tree.
- **P0 — the chain closes: FIXED IN SOURCE** (2026-07-03, REVIEW-2 P7): one
  `generate.artifact_path()` owner (`artifacts/{Company} - {Role} [{ats_job_id}]/`, band =
  metadata, id suffix unconditional), imported by `record.py`; `runs/` populated, dedup live.
  Build task: **port it and verify** a default end-to-end run writes non-empty
  `runs/{posting_id}.json` in the genericized tree.
- **P0 — personal logic lifted: PARTIAL IN SOURCE.** Done there: threshold/comp-floor via
  `pipeline_config.py` → `profile.search.*`; tailoring policy (cut orders, protect_orgs,
  headlines, voice) lives in `profile.yaml → tailoring_policy`; INSTRUCTIONS genericized.
  **Remaining for the skill:** the location/title gate regexes in `rank.py` are still literal
  hand-tuned personal constants — they must become profile-driven (`profile.search.locations`,
  title allow/deny lists) or clearly-marked user-editable config.
- **P1 — `SKILL.md` is the single authority**; both CLAUDE.md copies retired. OPEN.
- **P1 — `generate build` validates judge responses: STILL OPEN IN SOURCE** (verified 2026-07-07:
  bare `json.load`, no shape check / auto-unwrap / fail-loud on zero-valid). Fix in the build +
  add a malformed-responses fixture test.
- **P1 — preflight/cold-start block** (inputs exist? the discover-needs-real-internet /
  403-in-sandbox gotcha). OPEN.

## Defer to v2
Sheets/Drive/Apps-Script projection (`publish_*`, `color_sheet`, `layout_sheet`, the two Apps
Scripts), scripted batch fan-out + 3×-run-and-average orchestration, the source's optional
modules — likelihood/next-action layer, timebound triggers, the gaps loop, de-listing, the
human-state mirror (`backup_tracker`) — and the **blind screener pass** (Build 2's engine
reviewing built packages as a recruiter would: resume + JD rubric only, no profile access;
distinct from the critique gate, which is an informed mechanics check). Each is production-proven in the source (see
PATTERN.md "Proven extensions") and ports cleanly later; none is needed for the v1 closed loop.

**Done = passes [../../REVIEW.md](../../REVIEW.md) at "ready."** This build doubles as one of Malik's
strongest on-lane portfolio artifacts once genericized (user memory: `candidacy-gaps-goals`).
