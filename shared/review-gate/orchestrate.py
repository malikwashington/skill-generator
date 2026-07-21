#!/usr/bin/env python3
"""orchestrate.py — the review orchestrator: the SOLE producer of evidence-backed review
results, and the only sanctioned path to a passing attestation.

review_gate.py refuses any full-pass attestation whose lenses aren't backed by hash-bound
evidence transcripts. This tool PRODUCES that bundle: given the lens agents' outputs, it writes
each lens transcript stamped with the CURRENT content hash, assembles the result, and calls
review_gate.attest. You cannot mint a passing attestation by hand without either running this
(over real lens outputs) or forging self-consistent, hash-bound transcripts — a deliberate act,
not an omission. The only bypass is `review_gate.py override` (human, recorded).

Flow — the review harness spawns one agent per required lens (from review-tiers.json), each
writing its structured output to `<lens-dir>/<lens>.json`:
    {"lens": "<name>", "verdict": "pass|concerns|fail", "run_id": "<agent id>",
     "findings": [{"severity": "...", "confirmed": true|false, ...}, ...]}
then:
    orchestrate.py finalize --skill <dir> --tier <t> --lens-dir <dir> \
        --run-id <id> --reviewed-at <iso> [--review-verdict solid-with-fixes]

finalize refuses if any required lens output is missing (a lens that didn't run), writes the
evidence, assembles the result, and attests. If the review's findings contain a CONFIRMED
serious finding, the gate itself refuses the pass — the orchestrator never forces one.

Stdlib only; imports review_gate as the library.
"""
import sys, os, json, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import review_gate as rg

EVIDENCE_SUBDIR = os.path.join("reviews", "results", "evidence")


def _die(msg):
    print("[orchestrate] " + msg, file=sys.stderr)
    sys.exit(2)


def _atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def _partition_findings(findings, lens):
    """Split a lens's raw 'findings' into well-formed DEFECTS and NOTES, enforcing the
    reporting contract: a finding is a LIVE DEFECT (a valid severity in KNOWN_SEVERITIES),
    not verification narration. Positive confirmations ("verified fixed", "looks clean",
    "by design"), status lines with an out-of-band severity ('info', 'resolved'), and
    non-dict entries are NOTES — logged and dropped, never counted or attested. This stops
    a sloppy lens from manufacturing a false human-review tripwire (a serious-severity but
    unconfirmed note) or blowing the tier's finding cap with non-defects."""
    defects, notes = [], []
    for f in findings:
        if isinstance(f, dict) and f.get("severity") in rg.KNOWN_SEVERITIES:
            f.setdefault("lens", lens)
            defects.append(f)
        else:
            notes.append(f)
    return defects, notes


def finalize(args):
    skill = os.path.abspath(args.skill)
    if not os.path.isdir(skill):
        _die("--skill %s is not a directory" % skill)
    tiers = rg.load_tiers()
    if args.tier not in tiers:
        _die("unknown --tier %r (known: %s)" % (args.tier, ", ".join(sorted(tiers))))
    required = tiers[args.tier].get("required_lenses", [])
    if not required:
        _die("tier %r requires no lenses — nothing to orchestrate (use review_gate directly)"
             % args.tier)
    art_hash = rg.compute_artifact_hash(skill)
    lens_dir = os.path.abspath(args.lens_dir)
    edir = os.path.join(skill, EVIDENCE_SUBDIR)
    os.makedirs(edir, exist_ok=True)

    lenses, all_findings, seen_runs = [], [], set()
    for lens in required:
        src = os.path.join(lens_dir, "%s.json" % lens)
        if not os.path.isfile(src):
            _die("required lens %r produced no output at %s — the lens did not run. "
                 "Every required lens must run; there is no partial pass." % (lens, src))
        try:
            with open(src, encoding="utf-8") as f:
                out = json.load(f)
        except Exception as e:
            _die("lens %r output is not readable JSON: %s" % (lens, e))
        if not isinstance(out, dict):
            _die("lens %r output is not a JSON object" % lens)
        verdict = (out.get("verdict") or "").strip()
        if not verdict:
            _die("lens %r output has no 'verdict' — a lens with no verdict did not run" % lens)
        findings = out.get("findings") or []
        if not isinstance(findings, list):
            _die("lens %r 'findings' must be a list" % lens)
        run_id = (out.get("run_id") or out.get("agent_run_id") or lens).strip() or lens
        if run_id in seen_runs:
            _die("two lenses report run_id %r — each lens must be a distinct run" % run_id)
        seen_runs.add(run_id)
        # enforce the reporting contract: count DEFECTS, drop NOTES (see _partition_findings)
        defects, notes = _partition_findings(findings, lens)
        if notes:
            print("[orchestrate] lens %r: dropped %d non-defect note(s) from findings "
                  "(contract: findings are live defects only, not verification narration)."
                  % (lens, len(notes)), file=sys.stderr)
        # a serious verdict must be backed by real defects — otherwise a mis-encoded serious
        # finding would silently vanish into 'notes'. Refuse the inconsistency, never launder it.
        v = verdict.lower()
        if v in ("fail", "concerns") and not defects:
            _die("lens %r verdict=%r but produced no well-formed defect finding — reconcile the "
                 "lens output. A non-'pass' verdict cannot rest on zero defects; a mis-typed "
                 "severity drops a finding to an uncounted note." % (lens, verdict))
        if v == "fail" and not any(d.get("confirmed")
                                   and d.get("severity") in rg.SERIOUS_SEVERITIES for d in defects):
            _die("lens %r verdict='fail' but carries no CONFIRMED serious defect — reconcile "
                 "(a fail must be backed by a confirmed critical/high/medium)." % lens)
        all_findings.extend(defects)
        # the hash-bound evidence transcript — this is what the gate verifies
        tpath = os.path.join(edir, "%s.json" % lens)
        _atomic_write_json(tpath, {"lens": lens, "artifact_hash": art_hash,
                                   "verdict": verdict, "agent_run_id": run_id,
                                   "findings": defects})
        lenses.append({"lens": lens, "run_id": run_id, "verdict": verdict,
                       "evidence": {"path": os.path.relpath(tpath, skill),
                                    "sha256": rg._sha256_file(tpath)}})

    result = {"skill": os.path.basename(skill.rstrip(os.sep)), "review_scope": "full",
              "artifact_hash": art_hash, "reviewed_at": args.reviewed_at,
              "run_id": args.run_id, "reviewer": "review orchestrator (6-lens fan-out)",
              "lenses": lenses, "findings": all_findings}
    rpath = os.path.join(skill, "reviews", "results", "result-%s.json" % args.run_id)
    _atomic_write_json(rpath, result)
    print("[orchestrate] %d lenses, %d findings -> %s"
          % (len(lenses), len(all_findings), os.path.relpath(rpath, skill)))

    # Attest via the gate. The gate derives the counts + verifies evidence; if a CONFIRMED
    # serious finding exists it REFUSES the pass (we never force one). We ask for a pass;
    # the gate disposes.
    rc = rg.main(["attest", "--skill", skill, "--scope", "full", "--verdict", "pass",
                  "--tier", args.tier, "--review-verdict", args.review_verdict,
                  "--from-result", rpath, "--run-id", args.run_id,
                  "--reviewed-at", args.reviewed_at])
    if rc == 0:
        print("[orchestrate] ATTESTED — evidence-backed %s pass." % args.tier)
    else:
        print("[orchestrate] NOT attested — the gate refused (see above). Fix findings, "
              "re-run the affected lenses, re-finalize.", file=sys.stderr)
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(prog="orchestrate.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("finalize", help="assemble evidence + result from lens outputs, then attest")
    f.add_argument("--skill", required=True)
    f.add_argument("--tier", required=True)
    f.add_argument("--lens-dir", dest="lens_dir", required=True,
                   help="dir holding one <lens>.json per required lens (agent outputs)")
    f.add_argument("--run-id", dest="run_id", required=True)
    f.add_argument("--reviewed-at", dest="reviewed_at", required=True)
    f.add_argument("--review-verdict", dest="review_verdict", default="solid-with-fixes")
    f.set_defaults(func=finalize)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
