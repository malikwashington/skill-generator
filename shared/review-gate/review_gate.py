#!/usr/bin/env python3
"""review_gate.py — core library + CLI for the un-skippable review gate.

A skill only ships if it carries a PASSING, HASH-KEYED review attestation.
The attestation records the sha256 of the skill's content at review time; the
gate recomputes that hash and REFUSES if a single byte drifted since. This
makes "review then quietly edit" impossible: any post-review change invalidates
the attestation and re-locks the gate.

Design (mirrors the house hooks at ../hooks/):
  * STDLIB ONLY — runs under bare system python3, no third-party imports.
  * ATOMIC WRITES — tmp file + os.replace, so a crashed write never leaves a
    half-written (and thus untrustworthy) attestation.
  * FAIL LOUD on the GATE — the `check`/`check-all` subcommands exit 2 with a
    loud stderr reason when a skill is unattested/stale. (The Stop hook, by
    contrast, fails OPEN — see review_gate_hook.py.)
  * `attest` is the SOLE writer of attestations, and it refuses to launder a
    non-pass verdict into a passing attestation.
  * CADENCE IS STRUCTURAL, not prose. Only a FULL passing review writes the
    shippable attestation; an INCREMENTAL review (any verdict) only appends to
    the append-only `.review-history.jsonl` log and never touches the
    attestation. Combined with hash-keying, "ship on an incremental" and "ship
    v2 on a v1 review" are both impossible by construction, not by convention.

Attestation file (`.review-attestation.json`, at the skill root):
  {skill, artifact_hash, review_tier, review_scope:"full", verdict,
   review_verdict, confirmed_findings, run_id, reviewed_at, reviewer_version:"1"}

Review-history log (`.review-history.jsonl`, at the skill root, append-only):
  one compact JSON object per line — either a review record —
  {scope, verdict, artifact_hash, review_verdict, confirmed_findings, run_id,
   reviewed_at}
  or a new-baseline (redesign) marker written by `mark-baseline` —
  {scope:"new-baseline", artifact_hash, note, marked_at}. A baseline marker
  declares a fundamental redesign: it records nothing to the attestation and
  deletes nothing, but `next-scope` reads it (when it is the most-recent event)
  to reset the cadence to a FULL review.
"""
import sys, os, json, hashlib, argparse, tempfile, fnmatch, subprocess

ATTEST_NAME = ".review-attestation.json"
HISTORY_NAME = ".review-history.jsonl"
REVIEWER_VERSION = "1"

# Skill-DISCOVERY prune set (used by pre-push to find skill dirs without descending into
# noise). NOT the content-hash exclusion — that is the git-aware `artifact_files` + the
# `_HASH_*` fallback sets below.
EXCLUDE_DIRS = {"reviews", "__pycache__", ".git"}   # any depth; discovery only

# Content-hash scoping for the non-git fallback walk (see artifact_files / _shippable_via_walk).
# The git path honors .gitignore instead; these mirror its root-only rules for a bare dir. The
# distinction is load-bearing: VCS/bytecode/test caches are non-content at ANY depth, but the
# review gate's OWN outputs ('reviews/' and the attestation + history files) are excluded ONLY at
# the skill root — a directory or file with one of those names NESTED deeper (e.g.
# lib/reviews/loader.py) is real content and MUST be hashed, or a post-attestation edit under a
# nested 'reviews/' would go undetected, defeating the drift guarantee.
_HASH_PRUNE_ANYWHERE = {"__pycache__", ".pytest_cache", ".git"}
_HASH_PRUNE_ROOT = {"reviews"}
_HASH_SKIP_FILES_ROOT = {ATTEST_NAME, HISTORY_NAME}


def warn(msg):
    print("[review-gate] " + msg, file=sys.stderr)


def _rel_to_skill(path, skill_dir):
    """A path recorded in the attestation/history must be RELATIVE to the skill — an absolute
    path (os.path.abspath) leaks the reviewer's machine layout + OS username into a public repo
    when the review artifacts travel with the skill. Falls back to the basename if the target
    sits outside the skill dir."""
    ap, root = os.path.abspath(path), os.path.abspath(skill_dir)
    rp = os.path.relpath(ap, root)
    return rp if not rp.startswith("..") else os.path.basename(ap)


# ── Candidate severity ─────────────────────────────────────────────────────
# A "serious" candidate is critical/high/medium. Lows are shippable. This set is
# the criterion; it lives here, in the sole writer, so no caller can restate it.
SERIOUS_SEVERITIES = frozenset({"critical", "high", "medium"})
KNOWN_SEVERITIES = SERIOUS_SEVERITIES | {"low"}


class ResultError(ValueError):
    """A review-result artifact that cannot be trusted to derive counts from."""


def derive_counts(result):
    """Recompute the gate's counts FROM THE RAW FINDINGS. Never trust a summary.

    The gate blocks on CONFIRMED findings at their adversarially-CORRECTED severity, not
    on raw RAISED severity (2026-07-10). The skeptic panel exists precisely to
    correct a reviewer's severity; gating on the raised number wastes that signal and lets
    a reviewer's 'medium' label block code the panel unanimously rated low -- contradicting
    the standing rule that lows are shippable. Disposition per finding:
      - CONFIRMED (skeptic majority say real) AND corrected severity in {crit,high,med}
        -> BLOCKING. Fix it.
      - CONFIRMED but corrected to low -> a real low; shippable, does NOT block.
      - REFUTED at a raised serious severity -> human-review queue: never dropped, a HUMAN
        adjudicates (the skeptic-refute routes it there but can never unilaterally clear
        it), and it is PENDING -- a pass is not clean until the queue is adjudicated.

    A caller does not get to tell us these numbers; we count them off findings[], so the
    number that gates is the number the review computed, not one a model transcribed.

    Returns {blocking_serious, serious_confirmed(==blocking_serious), serious_candidates,
             human_review, confirmed_findings}. Raises ResultError on anything malformed.
    """
    if not isinstance(result, dict):
        raise ResultError("result artifact is not a JSON object")
    findings = result.get("findings")
    if not isinstance(findings, list):
        raise ResultError("result artifact has no 'findings' list (got %r). "
                          "A full pass must be derived from raw findings, not a summary."
                          % type(findings).__name__)
    blocking = human_review = candidates = confirmed = 0
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            raise ResultError("findings[%d] is not an object" % i)
        raised = f.get("severity")
        if raised not in KNOWN_SEVERITIES:
            raise ResultError("findings[%d].severity=%r is not one of %s"
                              % (i, raised, sorted(KNOWN_SEVERITIES)))
        # corrected severity is the skeptics' rating; fall back to raised if absent or
        # 'not-a-bug' (which is not a KNOWN severity, so it can never make a finding block).
        corrected = f.get("corrected_severity")
        effective = corrected if corrected in KNOWN_SEVERITIES else raised
        is_conf = bool(f.get("confirmed"))
        if is_conf:
            confirmed += 1
        if raised in SERIOUS_SEVERITIES:
            candidates += 1
        if is_conf and effective in SERIOUS_SEVERITIES:
            blocking += 1                      # confirmed AND still serious after review
        elif (not is_conf) and raised in SERIOUS_SEVERITIES:
            human_review += 1                  # refuted serious -> pending human adjudication
    return {"blocking_serious": blocking,
            "serious_confirmed": blocking,
            "serious_candidates": candidates,  # raised serious, kept for the audit trail
            "human_review": human_review,
            "confirmed_findings": confirmed}


def load_result(path, skill_dir):
    """Load a review-result artifact and verify it reviewed THIS content.

    The result records the artifact hash the reviewers actually read. If the
    source moved between review and attest, attesting would key a passing
    attestation to bytes nobody reviewed — so that is refused here.
    """
    try:
        with open(path) as f:
            result = json.load(f)
    except Exception as e:
        raise ResultError("cannot read result artifact %s: %s" % (path, e))
    if not isinstance(result, dict):
        raise ResultError("result artifact is not a JSON object")
    if result.get("review_scope") != "full":
        raise ResultError("result artifact review_scope=%r; a full pass needs 'full'"
                          % result.get("review_scope"))
    reviewed = result.get("artifact_hash")
    if not isinstance(reviewed, str) or not reviewed:
        raise ResultError("result artifact has no 'artifact_hash' — cannot prove which "
                          "bytes were reviewed. Emit it with: review_gate.py hash --skill <dir>")
    current = compute_artifact_hash(skill_dir)
    if reviewed != current:
        raise ResultError("result artifact reviewed hash %s... but content is now %s... — "
                          "the source changed after the review. Re-review the current content."
                          % (reviewed[:12], current[:12]))
    return result


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_evidence(L, name, skill_dir, reviewed_hash):
    """Verify a lens's evidence: a transcript file, hash-bound to the reviewed content.

    'declared a lens' is not 'ran a lens'. Each lens must carry evidence {path, sha256} of
    its actual review transcript, and the gate checks (a) the file exists UNDER the skill
    (no traversal), (b) its sha256 matches what the result declares (immutable reference),
    and (c) the transcript itself records artifact_hash == the reviewed content and its own
    lens name. (c) binds each lens to the exact bytes it reviewed — so a lens transcript from
    an EARLIER version of the skill can't be reused for a later one. Returns {lens,path,sha256}.
    Faking a lens now requires forging a self-consistent, hash-bound transcript — a deliberate
    act, not an omission. Evidence lives under reviews/ (excluded from the content hash), so
    writing it never perturbs the hash it attests to."""
    ev = L.get("evidence")
    if not isinstance(ev, dict):
        raise ResultError("lens %r has no 'evidence' {path, sha256} — a declared lens must "
                          "carry its review transcript, hash-bound to the content." % name)
    rel, declared = ev.get("path"), ev.get("sha256")
    if not (isinstance(rel, str) and rel.strip()):
        raise ResultError("lens %r evidence has no 'path'" % name)
    if not (isinstance(declared, str) and declared.strip()):
        raise ResultError("lens %r evidence has no 'sha256'" % name)
    full = os.path.realpath(os.path.join(skill_dir, rel))
    root = os.path.realpath(skill_dir)
    if not (full == root or full.startswith(root + os.sep)):
        raise ResultError("lens %r evidence path %r escapes the skill dir" % (name, rel))
    if not os.path.isfile(full):
        raise ResultError("lens %r evidence file not found: %s" % (name, rel))
    actual = _sha256_file(full)
    if actual != declared:
        raise ResultError("lens %r evidence sha256 mismatch (declared %s..., file %s...) — "
                          "the transcript was altered after the result referenced it."
                          % (name, declared[:12], actual[:12]))
    try:
        with open(full, encoding="utf-8") as f:
            transcript = json.load(f)
    except Exception as e:
        raise ResultError("lens %r evidence is not readable JSON: %s" % (name, e))
    if not isinstance(transcript, dict):
        raise ResultError("lens %r evidence transcript is not a JSON object" % name)
    if transcript.get("artifact_hash") != reviewed_hash:
        raise ResultError("lens %r transcript reviewed hash %s... but this result binds to "
                          "%s... — the lens reviewed different bytes (stale evidence)."
                          % (name, str(transcript.get("artifact_hash"))[:12], reviewed_hash[:12]))
    if transcript.get("lens") != name:
        raise ResultError("lens %r evidence transcript labels itself %r — lens/evidence mismatch."
                          % (name, transcript.get("lens")))
    return {"lens": name, "path": rel, "sha256": declared}


def check_lens_coverage(result, required_lenses, skill_dir=None, reviewed_hash=None):
    """Verify the review RAN the tier's required passes, each a DISTINCT run backed by a
    hash-bound transcript. Returns (sorted covered lens names, evidence manifest); raises
    ResultError if coverage or evidence is short.

    A findings[] list alone does not prove the review's breadth — a 3-lens pass and a 6-lens
    pass produce the same shape. Each required lens must appear in result['lenses'] as
    {lens, run_id, verdict, evidence:{path, sha256}}: run_ids DISTINCT (one pass can't be
    relabelled as several) and evidence hash-bound to the reviewed content (a declared lens
    must be a RUN lens — see _verify_evidence). Evidence is required whenever the tier
    requires lenses and skill_dir/reviewed_hash are supplied (the attest path always does)."""
    required = sorted(set(required_lenses or []))
    lenses = result.get("lenses")
    if not required:
        names = sorted({str(L.get("lens")) for L in lenses
                        if isinstance(L, dict) and L.get("lens")}) if isinstance(lenses, list) else []
        return names, []
    if not isinstance(lenses, list) or not lenses:
        raise ResultError(
            "result declares no 'lenses[]', but this tier requires the reviewing passes %s. "
            "Each lens must be declared as {\"lens\": name, \"run_id\": id, \"verdict\": v, "
            "\"evidence\": {path, sha256}} and produced by the review orchestrator." % required)
    covered, runs, manifest = set(), [], []
    verify_evidence = skill_dir is not None and reviewed_hash is not None
    for i, L in enumerate(lenses):
        if not isinstance(L, dict):
            raise ResultError("lenses[%d] is not an object" % i)
        name = L.get("lens") if isinstance(L.get("lens"), str) else ""
        run = L.get("run_id") if isinstance(L.get("run_id"), str) else ""
        verdict = L.get("verdict") if isinstance(L.get("verdict"), str) else ""
        if not name.strip():
            raise ResultError("lenses[%d] has no 'lens' name" % i)
        if not run.strip():
            raise ResultError("lens %r has no 'run_id' — each lens must be a distinct, "
                              "identifiable reviewing pass" % name)
        if not verdict.strip():
            raise ResultError("lens %r has no 'verdict' — a lens with no verdict did not run"
                              % name)
        if verify_evidence:
            manifest.append(_verify_evidence(L, name, skill_dir, reviewed_hash))
        covered.add(name)
        runs.append(run)
    dupes = sorted({r for r in runs if runs.count(r) > 1})
    if dupes:
        raise ResultError("two or more lenses share a run_id (%s) — distinct lenses must be "
                          "distinct runs, not one pass relabelled." % dupes)
    missing = [r for r in required if r not in covered]
    if missing:
        raise ResultError("missing required lens(es) for this tier: %s (declared: %s)"
                          % (missing, sorted(covered)))
    return sorted(covered), manifest


# ── Content hash ───────────────────────────────────────────────────────────
def _hash_file(full):
    """sha256 hex of a file's bytes, or None if unreadable (not content)."""
    h = hashlib.sha256()
    try:
        with open(full, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _shippable_via_git(skill_dir):
    """[(relpath, filehash)] for the files that would SHIP from skill_dir, when it is a git work
    tree: tracked + untracked-but-not-ignored, honoring .gitignore/.git/info/exclude. Returns
    None if skill_dir isn't a git work tree or git is unavailable.

    This is the primary, robust definition of "what the artifact IS": it covers exactly what a
    clone receives and excludes EVERY gitignored transient (build caches, .pytest_cache,
    .DS_Store, editor cruft) as a CLASS — no denylist to keep chasing. The gate's own root
    outputs (attestation/history/reviews) are gitignored in a real skill, so they drop out here;
    we also skip the root attestation/history files defensively (a self-referential hash is
    impossible even if a user forgets to ignore them)."""
    try:
        top = subprocess.run(["git", "-C", skill_dir, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=30)
        if top.returncode != 0:
            return None
        # --full-name -> names are REPO-TOP-relative (so the join onto repo_top below is right
        # even when skill_dir is a SUBDIR of a larger repo); `-- .` scopes the listing to files
        # under skill_dir. Without --full-name git prints subdir-relative names and the join
        # silently mis-resolves every path, dropping them all -> empty hash for any subdir skill.
        r = subprocess.run(["git", "-C", skill_dir, "ls-files", "-z", "--full-name",
                            "--cached", "--others", "--exclude-standard", "--", "."],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    # git reports a realpath'd toplevel; realpath our side too so a symlinked prefix
    # (e.g. macOS /var -> /private/var) doesn't make every relpath escape with '..'.
    repo_top = os.path.realpath(top.stdout.strip())
    skill_abs = os.path.realpath(skill_dir)
    entries = []
    for name in r.stdout.split("\0"):
        if not name:
            continue
        full = os.path.realpath(os.path.join(repo_top, name))
        rel = os.path.relpath(full, skill_abs)
        if rel.startswith(".."):                        # outside this skill subtree
            continue
        rel = rel.replace(os.sep, "/")
        # Defensively drop the gate's OWN root outputs even if a user forgot to gitignore them
        # (else writing evidence/attestation would perturb the very hash they key). Root only —
        # a nested reviews/ or attestation-named file is real content and stays hashed.
        if rel.split("/", 1)[0] in _HASH_PRUNE_ROOT:          # root 'reviews/' tree
            continue
        if "/" not in rel and rel in _HASH_SKIP_FILES_ROOT:   # root attestation/history file
            continue
        fh = _hash_file(full)
        if fh is not None:
            entries.append((rel, fh))
    entries.sort(key=lambda e: e[0])
    return entries


def _shippable_via_walk(skill_dir):
    """Git-independent fallback: os.walk with a maintained denylist. Used only when skill_dir is
    not a git work tree (e.g. a bare fixture). Less robust than the git path — it can only
    exclude the names it knows — so the git path is preferred whenever available."""
    entries = []
    root_abs = os.path.abspath(skill_dir)
    for root, dirs, files in os.walk(skill_dir):
        at_root = os.path.abspath(root) == root_abs
        # prune non-content dirs in-place. VCS/bytecode/test caches go at any depth; the gate's
        # own 'reviews/' tree only at the skill root (a NESTED 'reviews/' is content, must hash).
        dirs[:] = [d for d in dirs if d not in _HASH_PRUNE_ANYWHERE
                   and not (at_root and d in _HASH_PRUNE_ROOT)]
        for fn in files:
            if fn.endswith(".pyc") or fn in _HASH_PRUNE_ANYWHERE:
                continue
            if at_root and fn in _HASH_SKIP_FILES_ROOT:  # attestation/history: root only
                continue
            rel = os.path.relpath(os.path.join(root, fn), skill_dir).replace(os.sep, "/")
            fh = _hash_file(os.path.join(root, fn))
            if fh is not None:
                entries.append((rel, fh))
    entries.sort(key=lambda e: e[0])
    return entries


def artifact_files(skill_dir):
    """Sorted [(relpath, filehash)] for every SHIPPABLE file under skill_dir — the single
    enumeration of "what the artifact IS" (both the hash and the override's coverage read it).

    Prefers git (honors .gitignore, so all transient noise is excluded as a class); falls back
    to a denylist walk only when skill_dir is not a git work tree.
    """
    via_git = _shippable_via_git(skill_dir)
    # Empty is treated as "fall back to the walk", not as a valid answer: a real skill under
    # review always has content (at minimum a SKILL.md), so an empty git result signals a git
    # anomaly (or a mis-scoped listing) rather than a genuinely empty artifact — never hash "nothing".
    return via_git if via_git else _shippable_via_walk(skill_dir)


def compute_artifact_hash(skill_dir):
    """sha256 hex over the skill's content, order-independent and stable.

    Fold sorted relpath + per-file byte hash into one running sha256. Sorting
    makes it independent of filesystem walk order; hashing the relpath too means
    a rename changes the hash.
    """
    combined = hashlib.sha256()
    for rel, fh in artifact_files(skill_dir):
        combined.update(rel.encode("utf-8"))
        combined.update(b"\0")
        combined.update(fh.encode("ascii"))
        combined.update(b"\0")
    return combined.hexdigest()


# ── Attestation IO ─────────────────────────────────────────────────────────
def load_attestation(skill_dir):
    """Return the attestation dict, or None if absent/unreadable."""
    path = os.path.join(skill_dir, ATTEST_NAME)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def write_attestation(skill_dir, data):
    """Atomically write the attestation (tmp + os.replace)."""
    path = os.path.join(skill_dir, ATTEST_NAME)
    fd, tmp = tempfile.mkstemp(dir=skill_dir, prefix=".attest-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)                           # atomic on POSIX
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ── Review-history log (append-only) ───────────────────────────────────────
def append_history(skill_dir, record):
    """Append one compact JSON record (a single line) to the history log.

    Append-only by construction: every `attest` call — any scope, any verdict —
    lands one line here for audit. HISTORY_NAME is excluded from the content
    hash, so appending never perturbs the artifact hash it audits.
    """
    path = os.path.join(skill_dir, HISTORY_NAME)
    with open(path, "a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def load_history(skill_dir):
    """Return the list of history records (oldest first), skipping bad lines."""
    path = os.path.join(skill_dir, HISTORY_NAME)
    out = []
    if not os.path.exists(path):
        return out
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue                            # tolerate a torn line
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        return out
    return out


# ── Tier config ────────────────────────────────────────────────────────────
def load_tiers(path=None):
    """Load review-tiers.json (next to this script, or $REVIEW_TIERS)."""
    path = path or os.environ.get("REVIEW_TIERS") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "review-tiers.json")
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("review-tiers.json is not a JSON object")
    return data


# ── The check ──────────────────────────────────────────────────────────────
def check(skill_dir, tiers):
    """Return (ok: bool, reason: str). OK requires a current, tier-passing attestation."""
    att = load_attestation(skill_dir)
    if att is None:
        return False, "no review attestation (skill has never passed the review gate)"

    current = compute_artifact_hash(skill_dir)
    recorded = att.get("artifact_hash")
    if recorded != current:
        return False, ("attestation is STALE — content changed since review "
                       "(recorded %s..., now %s...). Re-run the review and re-attest."
                       % (str(recorded)[:12], current[:12]))

    # Belt: only a FULL review earns a shippable attestation. By construction
    # attest never writes one for any other scope, so this can only fail on a
    # hand-forged file — assert it anyway.
    scope = att.get("review_scope")
    if scope != "full":
        return False, ("attestation review_scope=%r is not 'full' — only a FULL "
                       "passing review is ship-eligible" % scope)

    tier = att.get("review_tier")
    bar = tiers.get(tier)
    if not isinstance(bar, dict):
        return False, "unknown review_tier %r (not in tier config)" % tier

    if att.get("verdict") != bar.get("min_verdict", "pass"):
        return False, ("verdict %r does not meet tier '%s' bar (needs %r)"
                       % (att.get("verdict"), tier, bar.get("min_verdict", "pass")))

    max_findings = bar.get("max_confirmed_findings", 0)
    cf = att.get("confirmed_findings")
    if not isinstance(cf, int) or cf > max_findings:
        return False, ("confirmed_findings=%r exceeds tier '%s' max (%d)"
                       % (cf, tier, max_findings))

    # Confirmed-severity criterion: a full passes only if NO finding is CONFIRMED still
    # serious after adversarial correction (blocking_serious == 0). A reviewer's raised
    # 'medium' the skeptics corrected to low does NOT block -- lows are shippable, and the
    # skeptic panel exists to make exactly that correction. Refuted-serious candidates are
    # a separate, human-adjudicated queue (recorded, never silently cleared). Newer
    # attestations carry blocking_serious; older ones fall back to the raised-candidate bar.
    bs = att.get("blocking_serious")
    if bs is not None:
        if not isinstance(bs, int) or bs > 0:
            return False, ("blocking_serious=%r (CONFIRMED critical/high/medium after "
                           "adversarial correction) must be 0 to pass. Fix them and "
                           "re-review." % bs)
    else:
        sc = att.get("serious_candidates")            # legacy attestation: old raised bar
        if sc is not None and (not isinstance(sc, int) or sc > 0):
            return False, ("serious_candidates=%r must be 0 to pass (legacy attestation; "
                           "re-attest to adopt the confirmed-severity criterion)." % sc)

    # A human override is a passing full review by decision of the principal. It
    # satisfies the gate — and it never gets to be quiet about what it did not cover.
    if att.get("attested_by") == "human-override":
        uncovered = att.get("uncovered_files") or []
        msg = ("PASSED BY HUMAN OVERRIDE — reviewer=%s, reviewed_at=%s\n"
               "  justification: %s\n"
               "  covers: %s (%d files reviewed)"
               % (att.get("override_reviewer"), att.get("reviewed_at") or "(unrecorded)",
                  att.get("override_justification"), att.get("covers"),
                  len(att.get("covered_files") or [])))
        if uncovered:
            msg += ("\n  UNREVIEWED BY ANYONE (%d): %s"
                    % (len(uncovered), ", ".join(uncovered[:8]) +
                       (" ..." if len(uncovered) > 8 else "")))
        return True, msg

    allowed = bar.get("require_review_verdict_in")
    if allowed is not None and att.get("review_verdict") not in allowed:
        return False, ("review_verdict %r not in tier '%s' allowed set %s"
                       % (att.get("review_verdict"), tier, allowed))

    # Lens-coverage belt: attest enforces this on write, but re-assert it here so a legacy
    # attestation (predating the gate — no lenses_covered) or a tampered one can't pass a tier
    # that now requires reviewing passes. (Human overrides return above, exempt by design.)
    required_lenses = sorted(set(bar.get("required_lenses") or []))
    if required_lenses:
        covered = att.get("lenses_covered")
        if not isinstance(covered, list):
            return False, ("attestation predates the lens-coverage gate (no lenses_covered) "
                           "but tier '%s' requires %s — re-run the review and re-attest."
                           % (tier, required_lenses))
        missing = [r for r in required_lenses if r not in covered]
        if missing:
            return False, ("attestation is missing required lens(es) for tier '%s': %s "
                           "(covered: %s) — re-review and re-attest." % (tier, missing, sorted(covered)))
        # Evidence belt: the per-lens transcripts must still be present + intact at ship time
        # (deleting/altering a transcript after attest must re-lock the gate). Content drift is
        # already caught above (stale); this catches evidence tampering with content unchanged.
        manifest = att.get("lens_evidence")
        if not isinstance(manifest, list) or len(manifest) < len(required_lenses):
            return False, ("attestation records no per-lens evidence but tier '%s' requires it "
                           "— re-run the review through the orchestrator and re-attest." % tier)
        for ev in manifest:
            try:
                full = os.path.realpath(os.path.join(skill_dir, ev.get("path", "")))
                if not (full == os.path.realpath(skill_dir)
                        or full.startswith(os.path.realpath(skill_dir) + os.sep)):
                    raise ValueError("evidence path escapes skill")
                if not os.path.isfile(full) or _sha256_file(full) != ev.get("sha256"):
                    raise ValueError("missing or altered")
            except Exception:
                return False, ("lens '%s' evidence is missing or altered (%s) — the review "
                               "transcript backing this attestation is gone; re-review and "
                               "re-attest." % (ev.get("lens"), ev.get("path")))

    return True, ("attested current: tier=%s verdict=%s findings=%s lenses=%s run=%s"
                  % (tier, att.get("verdict"), cf, len(att.get("lenses_covered") or []),
                     att.get("run_id")))


# ── Skill discovery ────────────────────────────────────────────────────────
def is_skill_dir(d):
    """A directory is a 'skill' if it carries a SKILL.md or an attestation."""
    return (os.path.exists(os.path.join(d, "SKILL.md"))
            or os.path.exists(os.path.join(d, ATTEST_NAME)))


def find_skills(root):
    """Immediate subdirs of root that look like skills, sorted by name."""
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        d = os.path.join(root, name)
        if os.path.isdir(d) and is_skill_dir(d):
            out.append(d)
    return out


def attestation_state(skill_dir):
    """One of 'attested-current' | 'stale' | 'missing'."""
    att = load_attestation(skill_dir)
    if att is None:
        return "missing"
    if att.get("artifact_hash") == compute_artifact_hash(skill_dir):
        return "attested-current"
    return "stale"


# ── Next-scope decision (full vs incremental, computed in code) ──────────────
def _next_scope(skill_dir):
    """Return (token, reason) — the full-vs-incremental decision, in CODE.

    token is one of 'up-to-date' | 'full' | 'incremental'. reason is a one-line
    human explanation (for stderr). The decision (in priority order):
      1. attestation exists AND its hash == current content -> 'up-to-date'
      2. attestation exists but hash drifted                -> 'full'
         (a passed full is the load-bearing truth; drift ⇒ re-earn it fully)
      3. no attestation (no full has ever passed) -> consult history:
         0. most-recent entry is a new-baseline marker      -> 'full'  (declared redesign)
         a. empty history                                   -> 'full'  (bootstrap)
         b. most-recent entry is incremental AND pass       -> 'full'  (attempt baseline)
         c. otherwise                                        -> 'incremental' (keep grinding)
    """
    current = compute_artifact_hash(skill_dir)
    att = load_attestation(skill_dir)
    if att is not None:
        if att.get("artifact_hash") == current:
            return "up-to-date", ("a full review passed and the content is unchanged "
                                  "since -> up-to-date")
        return "full", ("a full review passed but the content has drifted since "
                        "-> full")

    history = load_history(skill_dir)
    if not history:
        return "full", "no reviews recorded yet (bootstrap) -> full"
    last = history[-1]                                   # oldest first, so [-1] is newest
    # A declared redesign (logged by `mark-baseline`) short-circuits the
    # last-review-scope rules: a fundamental rewrite starts a fresh cadence with
    # a FULL review. Takes precedence over the incremental/pass and grind rules.
    if last.get("scope") == "new-baseline":
        return "full", ("most recent event is a new-baseline (redesign) -> full")
    if last.get("scope") == "incremental" and last.get("verdict") == "pass":
        return "full", ("no full review has passed; last review was a passing "
                        "incremental -> full")
    verdict_word = {"pass": "passed", "fail": "failed"}.get(last.get("verdict"),
                                                            str(last.get("verdict")))
    return "incremental", ("no full review has passed; last review was a %s %s "
                           "-> incremental"
                           % (verdict_word, last.get("scope", "?")))


def next_scope(skill_dir):
    """The next review scope: 'up-to-date' | 'full' | 'incremental'."""
    return _next_scope(skill_dir)[0]


# ── CLI ────────────────────────────────────────────────────────────────────
def cmd_attest(args):
    skill_dir = os.path.abspath(args.skill)
    if not os.path.isdir(skill_dir):
        warn("attest: --skill %s is not a directory" % skill_dir)
        return 2
    reviewed_at = args.reviewed_at
    if not reviewed_at:
        reviewed_at = ""
        warn("attest: --reviewed-at unspecified; recording empty timestamp.")
    artifact_hash = compute_artifact_hash(skill_dir)

    is_full_pass = (args.scope == "full" and args.verdict == "pass")

    # A FULL PASS IS DERIVED, NEVER DECLARED. The one path that writes a shippable
    # attestation takes its numbers from the review's own findings[], so the count
    # that gates the pass is the count the review computed. Hand-typed counts are
    # refused here: an integer a model retypes is a judgment call, and a judgment
    # call is exactly what this gate exists to remove.
    counts = None
    if is_full_pass:
        typed = [n for n, v in (("--serious-candidates", args.serious_candidates),
                                ("--confirmed-findings", args.confirmed_findings))
                 if v is not None]
        if typed:
            warn("attest REFUSED: %s may not be given on a full pass — the counts are "
                 "derived from --from-result. (not logged: nothing was reviewed)"
                 % ", ".join(typed))
            return 2
        if not args.from_result:
            warn("attest REFUSED: a full pass requires --from-result <review.json>; "
                 "the gate recomputes serious_candidates from its findings[] rather "
                 "than trusting a typed number. (not logged: nothing was reviewed)")
            return 2
        try:
            result_doc = load_result(args.from_result, skill_dir)
            counts = derive_counts(result_doc)
        except ResultError as e:
            warn("attest REFUSED: %s" % e)
            return 2
    elif args.confirmed_findings is None:
        warn("attest: --confirmed-findings is required except on a full pass "
             "(where it is derived from --from-result).")
        return 2

    eff_confirmed = counts["confirmed_findings"] if counts else args.confirmed_findings
    eff_serious = counts["serious_candidates"] if counts else args.serious_candidates
    eff_human = counts["human_review"] if counts else args.human_review
    # blocking_serious = CONFIRMED findings still serious after adversarial correction.
    # This, not the raised-candidate count, is what gates a pass. Falls back to the typed
    # serious count for a manual (no --from-result) fail attest.
    eff_blocking = counts["blocking_serious"] if counts else args.serious_candidates

    # EVERY attest call — any scope, any verdict — appends one audit line to the
    # append-only history log. Only a FULL passing review (below) additionally
    # writes the shippable attestation.
    append_history(skill_dir, {
        "scope": args.scope,
        "verdict": args.verdict,
        "artifact_hash": artifact_hash,
        "review_verdict": args.review_verdict,
        "confirmed_findings": eff_confirmed,
        "blocking_serious": eff_blocking,
        "serious_candidates": eff_serious,
        "human_review_pending": eff_human,
        "run_id": args.run_id,
        "reviewed_at": reviewed_at,
        "counts_derived_from": (_rel_to_skill(args.from_result, skill_dir)
                                if counts else None),
    })

    # Incremental reviews are the inner patch loop — history-only, NEVER the
    # ship attestation. Logging one is not a failure.
    if args.scope == "incremental":
        print("incremental review logged; NOT ship-eligible — a FULL passing "
              "review is required to ship.")
        return 0

    # scope == "full" from here down.
    # Refuse to launder a non-pass verdict into a passing attestation — but the
    # failed full review is still on the audit trail (appended above).
    if args.verdict != "pass":
        warn("attest REFUSED: verdict=%r is not 'pass' — the gate only records "
             "passing reviews. Fix the findings and re-review. (logged to history)"
             % args.verdict)
        return 2
    # Confirmed-severity criterion: a CONFIRMED finding still serious after adversarial
    # correction blocks a pass. eff_blocking is DERIVED from findings[], so it cannot be
    # omitted or fudged. (A reviewer's raised 'medium' that the skeptics corrected to low
    # does NOT block -- lows are shippable; gating on the corrected severity is the point.)
    if not isinstance(eff_blocking, int):
        warn("attest REFUSED: blocking_serious could not be derived. (logged to history)")
        return 2
    if eff_blocking > 0:
        warn("attest REFUSED: %d CONFIRMED serious finding(s) (critical/high/medium after "
             "adversarial correction) must be 0 to pass. Fix them and re-review. "
             "(logged to history)" % eff_blocking)
        return 2
    # Human-review queue: refuted-serious candidates are never dropped; a human adjudicates.
    # A pass is PENDING until that adjudication -- the skeptic-refute routes a finding to a
    # human but can never unilaterally clear it. The operator affirms the queue was reviewed
    # via --adjudicated-human-review N (recorded); the count must match what the review found.
    if isinstance(eff_human, int) and eff_human > 0:
        if args.adjudicated_human_review != eff_human:
            warn("attest REFUSED: %d finding(s) are in the human-review queue (refuted "
                 "serious — a human must adjudicate, never auto-cleared). Re-run with "
                 "--adjudicated-human-review %d after reviewing them, plus "
                 "--adjudication '<disposition>'. (logged to history)"
                 % (eff_human, eff_human))
            return 2
        if not (args.adjudication or "").strip():
            warn("attest REFUSED: --adjudicated-human-review requires --adjudication "
                 "'<why each queued finding is cleared or accepted>' (recorded).")
            return 2
    tiers = load_tiers()
    if args.tier not in tiers:
        warn("attest: unknown --tier %r (known: %s)" % (args.tier, ", ".join(sorted(tiers))))
        return 2
    # Lens-coverage gate: a full pass must RUN the tier's required reviewing passes, each a
    # distinct run backed by a hash-bound transcript — so "I ran fewer lenses" and "I declared
    # a lens I didn't run" are both structurally refused, not merely discouraged in prose.
    # Evidence is verified against the CURRENT content (skill_dir + artifact_hash), closing the
    # bare-findings hand path: no evidence bundle => no attestation.
    try:
        covered_lenses, lens_evidence = check_lens_coverage(
            result_doc, tiers[args.tier].get("required_lenses", []),
            skill_dir=skill_dir, reviewed_hash=artifact_hash)
    except ResultError as e:
        warn("attest REFUSED: %s (logged to history)" % e)
        return 2
    data = {
        "skill": os.path.basename(skill_dir.rstrip(os.sep)),
        "artifact_hash": artifact_hash,
        "review_tier": args.tier,
        "review_scope": "full",
        "verdict": args.verdict,
        "review_verdict": args.review_verdict,
        "confirmed_findings": eff_confirmed,
        "blocking_serious": eff_blocking,
        "serious_candidates": eff_serious,
        "human_review_pending": eff_human,
        "human_review_adjudication": (args.adjudication or None) if eff_human else None,
        "lenses_covered": covered_lenses,
        "lens_evidence": lens_evidence,
        "run_id": args.run_id,
        "reviewed_at": reviewed_at,
        "reviewer_version": REVIEWER_VERSION,
        "counts_derived_from": _rel_to_skill(args.from_result, skill_dir),
    }
    write_attestation(skill_dir, data)
    print("attested %s -> hash %s... tier=%s verdict=%s findings=%d serious_candidates=%d "
          "(derived from %s)"
          % (data["skill"], data["artifact_hash"][:12], args.tier,
             args.verdict, eff_confirmed, eff_serious, args.from_result))
    return 0


def cmd_override(args):
    """Human override: a documented manual review that COUNTS as a passing full.

    The human is the principal. An operator who has read the artifact and accepts
    it may say so, and that judgement is ship-eligible -- the gate exists to stop
    an agent from skipping review, not to stop the person who owns the thing.

    What the override may NOT do is claim coverage it does not have. It records
    exactly which files the human attests to having reviewed (--covers), and
    computes the complement. Files outside that set are named, in the attestation
    and on every `check`, for as long as the override stands. An override that
    covers the docs does not launder the code.
    """
    skill_dir = os.path.abspath(args.skill)
    if not os.path.isdir(skill_dir):
        warn("override: --skill %s is not a directory" % skill_dir)
        return 2

    justification = (args.justification or "").strip()
    if len(justification) < 20:
        warn("override REFUSED: --justification must be a real sentence (>=20 chars). "
             "An undocumented override is indistinguishable from a skipped review.")
        return 2
    if not (args.reviewer or "").strip():
        warn("override REFUSED: --reviewer is required. Someone owns this decision.")
        return 2
    if not args.covers:
        warn("override REFUSED: --covers is required (repeatable glob, e.g. --covers '*.md'). "
             "State what you actually reviewed; use --covers '*' to attest the whole artifact.")
        return 2

    files = [rel for rel, _ in artifact_files(skill_dir)]
    covered, uncovered = [], []
    for rel in files:
        if any(fnmatch.fnmatch(rel, pat) for pat in args.covers):
            covered.append(rel)
        else:
            uncovered.append(rel)

    if not covered:
        warn("override REFUSED: --covers %s matched no files in %s"
             % (args.covers, skill_dir))
        return 2

    artifact_hash = compute_artifact_hash(skill_dir)
    reviewed_at = args.reviewed_at or ""
    if not reviewed_at:
        warn("override: --reviewed-at unspecified; recording empty timestamp.")

    append_history(skill_dir, {
        "scope": "override",
        "verdict": "pass",
        "artifact_hash": artifact_hash,
        "review_verdict": "human-override",
        "confirmed_findings": 0,
        "run_id": "override:%s" % args.reviewer,
        "reviewed_at": reviewed_at,
        "justification": justification,
        "covers": list(args.covers),
        "uncovered_files": uncovered,
    })

    data = {
        "skill": os.path.basename(skill_dir.rstrip(os.sep)),
        "artifact_hash": artifact_hash,
        "review_tier": args.tier,
        "review_scope": "full",
        "verdict": "pass",
        "review_verdict": "human-override",
        "confirmed_findings": 0,
        "run_id": "override:%s" % args.reviewer,
        "reviewed_at": reviewed_at,
        "reviewer_version": REVIEWER_VERSION,
        # The honesty fields. `check` reads these and says so, every time.
        "attested_by": "human-override",
        "override_reviewer": args.reviewer,
        "override_justification": justification,
        "covers": list(args.covers),
        "covered_files": covered,
        "uncovered_files": uncovered,
    }
    write_attestation(skill_dir, data)
    print("OVERRIDE attested %s @ %s... by %s"
          % (data["skill"], artifact_hash[:12], args.reviewer))
    print("  covered   (%d): %s" % (len(covered), ", ".join(covered[:6]) +
                                    (" ..." if len(covered) > 6 else "")))
    if uncovered:
        warn("  UNREVIEWED (%d): %s" % (len(uncovered), ", ".join(uncovered[:6]) +
                                        (" ..." if len(uncovered) > 6 else "")))
        warn("  These files ship attested-by-override but were reviewed by NO ONE.")
    return 0


def cmd_mark_baseline(args):
    """Log a new-baseline (redesign) marker — the SOLE writer of baseline events.

    Mirrors `attest`'s discipline: it ONLY appends one line to the append-only
    history log. It does NOT write/modify the attestation and deletes nothing.
    When it is the most-recent event, `next-scope` reads it to reset the cadence
    to a FULL review (a fundamental rewrite starts a fresh cadence).
    """
    skill_dir = os.path.abspath(args.skill)
    if not os.path.isdir(skill_dir):
        warn("mark-baseline: --skill %s is not a directory" % skill_dir)
        return 2
    marked_at = args.marked_at
    if not marked_at:
        marked_at = ""
        warn("mark-baseline: --marked-at unspecified; recording empty timestamp.")
    artifact_hash = compute_artifact_hash(skill_dir)
    note = args.note or ""

    # Append-only: one baseline marker line, nothing touched elsewhere.
    append_history(skill_dir, {
        "scope": "new-baseline",
        "artifact_hash": artifact_hash,
        "note": note,
        "marked_at": marked_at,
    })

    skill_name = os.path.basename(skill_dir.rstrip(os.sep))
    print("marked new-baseline for %s @ %s" % (skill_name, artifact_hash[:12]))
    print("a fundamental redesign was declared (history preserved) -> next review "
          "resets to FULL." + (("  note: " + note) if note else ""))
    return 0


def cmd_hash(args):
    skill_dir = os.path.abspath(args.skill)
    if not os.path.isdir(skill_dir):
        warn("hash: --skill %s is not a directory" % skill_dir)
        return 2
    print(compute_artifact_hash(skill_dir))
    return 0


def cmd_check(args):
    skill_dir = os.path.abspath(args.skill)
    tiers = load_tiers()
    ok, reason = check(skill_dir, tiers)
    if ok:
        print("PASS %s — %s" % (os.path.basename(skill_dir.rstrip(os.sep)), reason))
        return 0
    warn("GATE FAILED for %s: %s" % (os.path.basename(skill_dir.rstrip(os.sep)), reason))
    return 2


def cmd_check_all(args):
    root = os.path.abspath(args.root)
    tiers = load_tiers()
    skills = find_skills(root)
    failures = []
    for s in skills:
        ok, reason = check(s, tiers)
        name = os.path.basename(s.rstrip(os.sep))
        if ok:
            print("PASS %s" % name)
        else:
            print("FAIL %s — %s" % (name, reason))
            failures.append(name)
    if not skills:
        warn("check-all: no skills found under %s" % root)
    if failures:
        warn("GATE FAILED: %d skill(s) not shippable: %s" % (len(failures), ", ".join(failures)))
        return 2
    return 0


def cmd_status(args):
    root = os.path.abspath(args.root)
    skills = find_skills(root)
    if not skills:
        print("(no skills found under %s)" % root)
        return 0
    width = max(len(os.path.basename(s.rstrip(os.sep))) for s in skills)
    print("%-*s  %-16s  %s" % (width, "SKILL", "STATE", "TIER/VERDICT"))
    for s in skills:
        name = os.path.basename(s.rstrip(os.sep))
        state = attestation_state(s)
        att = load_attestation(s) or {}
        detail = ""
        if att:
            detail = "%s / %s (findings=%s)" % (
                att.get("review_tier", "?"), att.get("verdict", "?"),
                att.get("confirmed_findings", "?"))
        print("%-*s  %-16s  %s" % (width, name, state, detail))
    return 0


def cmd_history(args):
    skill_dir = os.path.abspath(args.skill)
    name = os.path.basename(skill_dir.rstrip(os.sep))
    records = load_history(skill_dir)
    if not records:
        print("no reviews recorded for %s" % name)
        return 0
    print("%-12s  %-8s  %-14s  %-14s  %s"
          % ("SCOPE", "VERDICT", "HASH", "RUN", "REVIEWED-AT"))
    for r in records:                                   # oldest first, newest last
        # new-baseline markers carry no verdict/run_id — they have a note and a
        # marked_at instead. Render them readably rather than as "?" noise.
        if r.get("scope") == "new-baseline":
            note = r.get("note", "") or "-"
            print("%-12s  %-8s  %-14s  %-14s  %s" % (
                "new-baseline", "-", str(r.get("artifact_hash", "?"))[:12],
                "(redesign)", (r.get("marked_at", "") or "-") + "  note: " + note))
            continue
        print("%-12s  %-8s  %-14s  %-14s  %s" % (
            r.get("scope", "?"), r.get("verdict", "?"),
            str(r.get("artifact_hash", "?"))[:12], r.get("run_id", "?"),
            r.get("reviewed_at", "?") or "-"))
    return 0


def cmd_next_scope(args):
    skill_dir = os.path.abspath(args.skill)
    if not os.path.isdir(skill_dir):
        warn("next-scope: --skill %s is not a directory" % skill_dir)
        return 2
    token, reason = _next_scope(skill_dir)
    # The token is the ONLY thing on stdout, so callers can parse it cleanly;
    # the human explanation goes to stderr.
    print("[next-scope] " + reason, file=sys.stderr)
    print(token)
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="review_gate.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("attest", help="log a review; a FULL pass writes the hash-keyed attestation (sole writer)")
    a.add_argument("--skill", required=True)
    a.add_argument("--scope", required=True, choices=["full", "incremental"],
                   help="full: ship-eligible on pass. incremental: history-only, never ship-eligible.")
    a.add_argument("--tier", required=True)
    a.add_argument("--verdict", required=True, choices=["pass", "fail"])
    a.add_argument("--review-verdict", dest="review_verdict", required=True)
    a.add_argument("--from-result", dest="from_result", default=None, metavar="REVIEW_JSON",
                   help="REQUIRED for a full pass: the review's own result artifact. The gate "
                        "recomputes serious_candidates/confirmed_findings from its findings[] "
                        "and verifies its artifact_hash matches the current content. Counts are "
                        "derived, never typed.")
    a.add_argument("--confirmed-findings", dest="confirmed_findings", type=int, default=None,
                   help="required EXCEPT on a full pass, where it is derived from --from-result.")
    a.add_argument("--serious-candidates", dest="serious_candidates", type=int, default=None,
                   help="count of critical/high/medium candidates RAISED (confirmed or refuted); "
                        ">0 blocks a pass. Lows are shippable. REFUSED on a full pass — derived there.")
    a.add_argument("--human-review", dest="human_review", type=int, default=None,
                   help="count of refuted serious candidates escalated to human review (audit).")
    a.add_argument("--adjudicated-human-review", dest="adjudicated_human_review", type=int,
                   default=0, help="on a full pass with a non-empty human-review queue, the "
                   "count the operator affirms they adjudicated; must equal the derived "
                   "human_review count. The queue is never auto-cleared.")
    a.add_argument("--adjudication", dest="adjudication", default="",
                   help="recorded disposition of the human-review queue (why each refuted "
                        "serious finding is cleared or accepted). Required with --adjudicated-human-review.")
    a.add_argument("--run-id", dest="run_id", required=True)
    a.add_argument("--reviewed-at", dest="reviewed_at", default="")
    a.set_defaults(func=cmd_attest)

    hs = sub.add_parser("hash", help="print the current artifact hash (what a review must bind to)")
    hs.add_argument("--skill", required=True)
    hs.set_defaults(func=cmd_hash)

    o = sub.add_parser("override",
                       help="human override: a documented manual review that counts as a passing full")
    o.add_argument("--skill", required=True)
    o.add_argument("--reviewer", required=True, help="who owns this decision")
    o.add_argument("--justification", required=True,
                   help="why the override is warranted (>=20 chars; recorded permanently)")
    o.add_argument("--covers", action="append", required=True, metavar="GLOB",
                   help="repeatable glob of what was ACTUALLY reviewed, e.g. --covers '*.md'. "
                        "Everything else is recorded as unreviewed. Use '*' for the whole artifact.")
    o.add_argument("--tier", default="critical")
    o.add_argument("--reviewed-at", dest="reviewed_at", default="")
    o.set_defaults(func=cmd_override)

    mb = sub.add_parser("mark-baseline",
                        help="log a new-baseline (redesign) marker; resets the next review to FULL (sole writer of baseline events)")
    mb.add_argument("--skill", required=True)
    mb.add_argument("--note", default="", help="free-text explanation of the redesign")
    mb.add_argument("--marked-at", dest="marked_at", default="")
    mb.set_defaults(func=cmd_mark_baseline)

    c = sub.add_parser("check", help="the GATE: exit 0 if shippable, 2 otherwise")
    c.add_argument("--skill", required=True)
    c.set_defaults(func=cmd_check)

    ca = sub.add_parser("check-all", help="check every skill under --root")
    ca.add_argument("--root", required=True)
    ca.set_defaults(func=cmd_check_all)

    s = sub.add_parser("status", help="print each skill's attestation state")
    s.add_argument("--root", required=True)
    s.set_defaults(func=cmd_status)

    h = sub.add_parser("history", help="print a skill's append-only review-history log")
    h.add_argument("--skill", required=True)
    h.set_defaults(func=cmd_history)

    ns = sub.add_parser("next-scope",
                        help="compute the next review scope in code: up-to-date | full | incremental")
    ns.add_argument("--skill", required=True)
    ns.set_defaults(func=cmd_next_scope)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
