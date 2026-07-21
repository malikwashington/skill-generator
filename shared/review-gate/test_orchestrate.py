#!/usr/bin/env python3
"""Offline tests for orchestrate.py — the evidence-backed review orchestrator.
No network, no third-party deps. Run: python3 test_orchestrate.py"""
import io, json, os, shutil, sys, tempfile, contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import review_gate as rg
import orchestrate as orch

_checks = 0
_fails = []


def ok(label, cond):
    global _checks
    _checks += 1
    if not cond:
        _fails.append(label)
        print("  FAIL " + label)
    else:
        print("  ok   " + label)


STD6 = ["correctness", "security", "dx", "completeness", "packaging", "adoption"]


def make_skill(d):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "SKILL.md"), "w") as f:
        f.write("demo\n")
    with open(os.path.join(d, "run.py"), "w") as f:
        f.write("x=1\n")


def write_lens(lens_dir, name, verdict="pass", findings=None, run_id=None):
    os.makedirs(lens_dir, exist_ok=True)
    with open(os.path.join(lens_dir, "%s.json" % name), "w") as f:
        json.dump({"lens": name, "verdict": verdict, "run_id": run_id or ("agent-%s" % name),
                   "findings": findings or []}, f)


def run_finalize(skill, lens_dir, run_id):
    """Invoke orchestrate.finalize; return exit code (SystemExit is how _die exits)."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = orch.main(["finalize", "--skill", skill, "--tier", "standard",
                              "--lens-dir", lens_dir, "--run-id", run_id,
                              "--reviewed-at", "2026-07-20T00:00:00Z"])
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def main():
    tmp = tempfile.mkdtemp(prefix="orch-test-")
    try:
        tiers = rg.load_tiers()

        # 1. a clean 6-lens review -> evidence-backed attestation -> shippable
        s1 = os.path.join(tmp, "s1"); make_skill(s1); ld1 = os.path.join(tmp, "l1")
        for n in STD6[:5]:
            write_lens(ld1, n)
        write_lens(ld1, "adoption", verdict="concerns",
                   findings=[{"severity": "low", "confirmed": True, "title": "minor"}])
        code, _, _ = run_finalize(s1, ld1, "run-1")
        ok("clean 6-lens finalize attests (exit 0)", code == 0)
        att = rg.load_attestation(s1) or {}
        ok("  attestation is evidence-backed (lens_evidence recorded)",
           len(att.get("lens_evidence") or []) == 6)
        ok("  gate check passes", rg.check(s1, tiers)[0])
        ok("  derived count is 1 low", att.get("confirmed_findings") == 1)

        # 2. a missing lens output -> refused ("the lens did not run")
        s2 = os.path.join(tmp, "s2"); make_skill(s2); ld2 = os.path.join(tmp, "l2")
        for n in STD6:
            write_lens(ld2, n)
        os.remove(os.path.join(ld2, "security.json"))
        code, _, err = run_finalize(s2, ld2, "run-2")
        ok("missing lens output refuses (exit 2)", code == 2)
        ok("  reason says the lens did not run", "did not run" in err)
        ok("  no attestation written", rg.load_attestation(s2) is None)

        # 3. a CONFIRMED serious finding -> the gate refuses the pass (orchestrator never forces)
        s3 = os.path.join(tmp, "s3"); make_skill(s3); ld3 = os.path.join(tmp, "l3")
        for n in STD6:
            write_lens(ld3, n)
        write_lens(ld3, "security", verdict="fail",
                   findings=[{"severity": "high", "confirmed": True, "title": "real bug"}])
        code, _, _ = run_finalize(s3, ld3, "run-3")
        ok("confirmed serious finding refuses the pass (exit != 0)", code != 0)
        ok("  no attestation written on refusal", rg.load_attestation(s3) is None)

        # 4. two lenses reporting the same run_id -> refused (distinct runs)
        s4 = os.path.join(tmp, "s4"); make_skill(s4); ld4 = os.path.join(tmp, "l4")
        for n in STD6:
            write_lens(ld4, n, run_id="SAME")
        code, _, err = run_finalize(s4, ld4, "run-4")
        ok("colliding run_ids refuse (exit 2)", code == 2)
        ok("  reason says distinct run", "distinct run" in err)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if _fails:
        print("\nFAILED %d/%d:" % (len(_fails), _checks))
        for f in _fails:
            print("  - " + f)
        return 1
    print("\nall %d checks passed" % _checks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
