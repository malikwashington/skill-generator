#!/usr/bin/env python3
"""Offline tests for the review gate. No network, no deps, no pytest.
Run: python3 test_review_gate.py   (exit 0 = all pass)

Covers: hash determinism + order-stability, hash-changes-on-edit, attest->check
pass, the KEY drift property (edit after attest -> check fails), missing
attestation, tier bars, and the Stop hook's block/allow/fail-open behavior.
"""
import json, os, subprocess, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import review_gate as rg                                # noqa: E402

HOOK = os.path.join(HERE, "review_gate_hook.py")

_checks = 0
_fails = []


def check(label, got, want):
    global _checks
    _checks += 1
    if got != want:
        _fails.append("%s: got %r want %r" % (label, got, want))


def ok(label, cond):
    check(label, bool(cond), True)


# ── Helpers ────────────────────────────────────────────────────────────────
def make_skill(files):
    """Create a temp skill dir with {relpath: content}. Returns its path."""
    d = tempfile.mkdtemp()
    for rel, content in files.items():
        full = os.path.join(d, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
    return d


def attest(skill_dir, tier="standard", verdict="pass",
           review_verdict="solid-with-fixes", findings=0, run_id="run-1"):
    """Write an attestation keyed to the CURRENT content (via the library)."""
    data = {
        "skill": os.path.basename(skill_dir),
        "artifact_hash": rg.compute_artifact_hash(skill_dir),
        "review_tier": tier,
        "review_scope": "full",
        "verdict": verdict,
        "review_verdict": review_verdict,
        "confirmed_findings": findings,
        "run_id": run_id,
        "reviewed_at": "2026-07-09T00:00:00Z",
        "reviewer_version": "1",
    }
    rg.write_attestation(skill_dir, data)


def write_result(skill_dir, findings=None, artifact_hash=None, scope="full",
                 name="test-result.json"):
    """Write a review-result artifact under reviews/ (excluded from the hash).

    `findings` is the raw list the gate recomputes its counts from.
    """
    rdir = os.path.join(skill_dir, "reviews", "results")
    os.makedirs(rdir, exist_ok=True)
    path = os.path.join(rdir, name)
    payload = {"review_scope": scope,
               "artifact_hash": (artifact_hash if artifact_hash is not None
                                 else rg.compute_artifact_hash(skill_dir)),
               "findings": [] if findings is None else findings}
    with open(path, "w") as f:
        json.dump(payload, f)
    return path


def low(confirmed=True):
    """A shippable (low) finding — never blocks a pass."""
    return {"severity": "low", "file": "a.py", "confirmed": confirmed}


def run_attest(skill_dir, scope, verdict="pass", tier="standard",
               review_verdict="solid-with-fixes", findings=0, run_id="run-1",
               reviewed_at="2026-07-09T00:00:00Z", from_result=None):
    """Invoke `review_gate.py attest` via the CLI; return the CompletedProcess.

    A full pass takes its counts from a result artifact (the gate derives them);
    every other combination still passes --confirmed-findings on the CLI.
    """
    cmd = [sys.executable, os.path.join(HERE, "review_gate.py"), "attest",
           "--skill", skill_dir, "--scope", scope, "--tier", tier,
           "--verdict", verdict, "--review-verdict", review_verdict,
           "--run-id", run_id, "--reviewed-at", reviewed_at]
    if scope == "full" and verdict == "pass":
        if from_result is None:
            # `findings` here means "this many confirmed LOW findings" — low is
            # shippable, so the pass still stands and confirmed_findings == findings.
            from_result = write_result(skill_dir, [low() for _ in range(findings)])
        cmd += ["--from-result", from_result]
    else:
        cmd += ["--confirmed-findings", str(findings)]
    return subprocess.run(cmd, capture_output=True, text=True)


def history_lines(skill_dir):
    """Number of records in the append-only history log (0 if absent)."""
    return len(rg.load_history(skill_dir))


_NUDGE_STATE = None                                      # per-run temp nudge-state file


def run_hook(root, stop_hook_active=False, garbage=False, states=None, nudge_state=None):
    """Invoke the Stop hook; return (decision, stderr).

    Each call gets a FRESH nudge-state file unless one is passed, so tests that do
    not care about once-per-hash dedup keep their old semantics.
    """
    env = dict(os.environ)
    env["REVIEW_SHIP_ROOT"] = root
    env.pop("REVIEW_GATE_NUDGE_STATES", None)
    if states:
        env["REVIEW_GATE_NUDGE_STATES"] = states
    if nudge_state is None:
        nudge_state = os.path.join(tempfile.mkdtemp(), "nudged.json")
    env["REVIEW_GATE_NUDGE_STATE"] = nudge_state
    if garbage:
        stdin = "not json {{{"
    else:
        stdin = json.dumps({"hook_event_name": "Stop", "cwd": root,
                            "stop_hook_active": stop_hook_active})
    p = subprocess.run([sys.executable, HOOK], input=stdin,
                       capture_output=True, text=True, env=env)
    decision = "allow"
    if p.stdout.strip():
        decision = json.loads(p.stdout).get("decision", "allow")
    return decision, p.stderr


def main():
    tiers = rg.load_tiers()
    tmpdirs = []

    def skill(files):
        d = make_skill(files)
        tmpdirs.append(d)
        return d

    try:
        # ── Hash: determinism ─────────────────────────────────────────────
        s1 = skill({"SKILL.md": "hello", "lib/a.py": "print(1)\n"})
        h_a = rg.compute_artifact_hash(s1)
        h_b = rg.compute_artifact_hash(s1)
        check("hash deterministic (same dir twice)", h_a, h_b)

        # ── Hash: order-stability (add files in different order) ───────────
        s_x = skill({})
        with open(os.path.join(s_x, "b.txt"), "w") as f: f.write("B")
        with open(os.path.join(s_x, "a.txt"), "w") as f: f.write("A")
        s_y = skill({})
        with open(os.path.join(s_y, "a.txt"), "w") as f: f.write("A")
        with open(os.path.join(s_y, "b.txt"), "w") as f: f.write("B")
        check("hash independent of file-creation order",
              rg.compute_artifact_hash(s_x), rg.compute_artifact_hash(s_y))

        # ── Hash: changes on content change ───────────────────────────────
        s2 = skill({"SKILL.md": "hello", "lib/a.py": "print(1)\n"})
        h_before = rg.compute_artifact_hash(s2)
        with open(os.path.join(s2, "lib/a.py"), "w") as f:
            f.write("print(2)\n")
        ok("hash changes when content changes", rg.compute_artifact_hash(s2) != h_before)

        # ── Hash: excludes attestation + reviews/ + pyc ───────────────────
        s3 = skill({"SKILL.md": "x"})
        h_clean = rg.compute_artifact_hash(s3)
        attest(s3)                                       # writes .review-attestation.json
        os.makedirs(os.path.join(s3, "reviews"))
        with open(os.path.join(s3, "reviews", "r1.md"), "w") as f: f.write("log")
        with open(os.path.join(s3, "junk.pyc"), "w") as f: f.write("x")
        check("hash ignores attestation/reviews/pyc", rg.compute_artifact_hash(s3), h_clean)

        # ── attest -> check passes ────────────────────────────────────────
        s4 = skill({"SKILL.md": "content", "run.py": "x=1\n"})
        attest(s4, tier="standard", findings=2)
        good, reason = rg.check(s4, tiers)
        ok("attest then check passes", good)

        # ── KEY property: edit after attest -> check FAILS (drift) ─────────
        with open(os.path.join(s4, "run.py"), "w") as f:
            f.write("x=2  # sneaky post-review edit\n")
        bad, reason = rg.check(s4, tiers)
        ok("edit after attest -> check FAILS (drift)", not bad)
        ok("  drift reason mentions stale", "STALE" in reason or "stale" in reason)

        # ── Missing attestation -> fail ───────────────────────────────────
        s5 = skill({"SKILL.md": "unreviewed"})
        bad, reason = rg.check(s5, tiers)
        ok("missing attestation -> fail", not bad)
        ok("  missing reason says no attestation", "no review attestation" in reason)

        # ── Tier bars ─────────────────────────────────────────────────────
        # standard allows <=3 findings
        s6 = skill({"SKILL.md": "x"})
        attest(s6, tier="standard", findings=3)
        ok("standard allows exactly 3 findings", rg.check(s6, tiers)[0])
        s7 = skill({"SKILL.md": "x"})
        attest(s7, tier="standard", findings=4)
        ok("standard rejects 4 findings", not rg.check(s7, tiers)[0])

        # high requires 0 findings + solid-with-fixes verdict
        s8 = skill({"SKILL.md": "x"})
        attest(s8, tier="high", findings=0, review_verdict="solid-with-fixes")
        ok("high allows 0 findings + solid-with-fixes", rg.check(s8, tiers)[0])
        s9 = skill({"SKILL.md": "x"})
        attest(s9, tier="high", findings=1, review_verdict="solid-with-fixes")
        ok("high rejects 1 finding", not rg.check(s9, tiers)[0])
        s10 = skill({"SKILL.md": "x"})
        attest(s10, tier="high", findings=0, review_verdict="needs-work")
        ok("high rejects wrong review_verdict", not rg.check(s10, tiers)[0])

        # a 'fail' verdict never passes (even trivial)
        s11 = skill({"SKILL.md": "x"})
        attest(s11, tier="trivial", verdict="fail", findings=0)
        ok("fail verdict never passes (trivial)", not rg.check(s11, tiers)[0])

        # ── attest CLI refuses to launder a non-pass verdict ──────────────
        s12 = skill({"SKILL.md": "x"})
        p = run_attest(s12, scope="full", verdict="fail",
                       review_verdict="needs-work", run_id="r")
        check("attest CLI refuses verdict=fail (exit 2)", p.returncode, 2)
        ok("  no attestation written on refusal",
           rg.load_attestation(s12) is None)
        ok("  full+fail STILL appends a history line (audit)",
           history_lines(s12) == 1)

        # ── CADENCE: incremental attest is history-only, never ship-eligible ─
        s13 = skill({"SKILL.md": "draft", "run.py": "x=1\n"})
        p = run_attest(s13, scope="incremental", verdict="pass")
        check("incremental attest exit 0 (logging is not a failure)", p.returncode, 0)
        ok("  incremental writes NO attestation", rg.load_attestation(s13) is None)
        ok("  incremental appends exactly one history line", history_lines(s13) == 1)
        ok("  incremental prints NOT ship-eligible", "NOT ship-eligible" in p.stdout)
        ok("  incremental-only skill is NOT shippable", not rg.check(s13, tiers)[0])
        ok("  its gate reason is the missing attestation",
           "no review attestation" in rg.check(s13, tiers)[1])

        # ── CADENCE: full+pass writes the attestation AND appends history ────
        s14 = skill({"SKILL.md": "final", "run.py": "x=1\n"})
        p = run_attest(s14, scope="full", verdict="pass", tier="standard", findings=1)
        check("full+pass attest exit 0", p.returncode, 0)
        att14 = rg.load_attestation(s14)
        ok("  full+pass writes an attestation", att14 is not None)
        check("  attestation records review_scope=full",
              (att14 or {}).get("review_scope"), "full")
        ok("  full+pass appends a history line", history_lines(s14) == 1)
        ok("  full+pass skill IS shippable", rg.check(s14, tiers)[0])

        # ── CADENCE: an incremental THEN a full+pass — only full is shippable ─
        s15 = skill({"SKILL.md": "v1", "run.py": "x=1\n"})
        run_attest(s15, scope="incremental", verdict="fail",
                   review_verdict="needs-work", run_id="inc-1")
        ok("  after only-incremental, not shippable", not rg.check(s15, tiers)[0])
        run_attest(s15, scope="full", verdict="pass", tier="standard", run_id="full-1")
        ok("  after the full pass, shippable", rg.check(s15, tiers)[0])
        ok("  both reviews are on the history trail", history_lines(s15) == 2)

        # ── history logging never perturbs the content hash ─────────────────
        s16 = skill({"SKILL.md": "stable", "lib/a.py": "print(1)\n"})
        h_pre = rg.compute_artifact_hash(s16)
        for i in range(3):
            run_attest(s16, scope="incremental", verdict="fail",
                       review_verdict="needs-work", run_id="inc-%d" % i)
        ok("  3 incremental logs appended", history_lines(s16) == 3)
        check("  content hash unchanged by history writes",
              rg.compute_artifact_hash(s16), h_pre)
        run_attest(s16, scope="full", verdict="pass", tier="standard", run_id="full-x")
        check("  content hash still unchanged after full attest",
              rg.compute_artifact_hash(s16), h_pre)

        # ── history subcommand reflects the logged sequence ─────────────────
        p = subprocess.run(
            [sys.executable, os.path.join(HERE, "review_gate.py"),
             "history", "--skill", s16], capture_output=True, text=True)
        check("history subcommand exit 0", p.returncode, 0)
        ok("  history shows the incremental run-ids", "inc-0" in p.stdout and "inc-2" in p.stdout)
        ok("  history shows the full run-id (newest last)", "full-x" in p.stdout)
        ok("  history shows both scopes", "incremental" in p.stdout and "full" in p.stdout)
        # empty/missing history -> clear line
        s17 = skill({"SKILL.md": "never-reviewed"})
        p = subprocess.run(
            [sys.executable, os.path.join(HERE, "review_gate.py"),
             "history", "--skill", s17], capture_output=True, text=True)
        ok("  empty history says no reviews recorded", "no reviews recorded" in p.stdout)

        # ── drift still invalidates a full+pass attestation (new fields) ────
        s18 = skill({"SKILL.md": "orig", "run.py": "x=1\n"})
        run_attest(s18, scope="full", verdict="pass", tier="standard")
        ok("  full+pass shippable before edit", rg.check(s18, tiers)[0])
        with open(os.path.join(s18, "run.py"), "w") as f:
            f.write("x=2  # post-review drift\n")
        bad, reason = rg.check(s18, tiers)
        ok("  edit after full+pass -> gate FAILS (drift, new fields)", not bad)
        ok("  drift reason still mentions stale", "STALE" in reason or "stale" in reason)

        # ── next-scope: the full-vs-incremental decision, computed in code ──
        def run_next_scope(skill_dir):
            """Invoke the CLI; return (token_on_stdout, stderr)."""
            p = subprocess.run(
                [sys.executable, os.path.join(HERE, "review_gate.py"),
                 "next-scope", "--skill", skill_dir], capture_output=True, text=True)
            return p.stdout.strip(), p.stderr

        def hist_line(skill_dir, scope, verdict, hh):
            """Append a raw history line directly (bypasses attest's full-pass writer)."""
            rg.append_history(skill_dir, {
                "scope": scope, "verdict": verdict, "artifact_hash": hh,
                "review_verdict": "needs-work", "confirmed_findings": 1,
                "run_id": "r", "reviewed_at": ""})

        # empty history, no attestation -> full (bootstrap)
        ns0 = skill({"SKILL.md": "x"})
        check("next-scope: empty history -> full", rg.next_scope(ns0), "full")
        tok, err = run_next_scope(ns0)
        check("next-scope CLI: empty -> full on stdout", tok, "full")
        ok("  CLI explanation goes to stderr", "[next-scope]" in err and "bootstrap" in err)

        # last entry {full, fail}, no attestation -> incremental
        ns1 = skill({"SKILL.md": "x"})
        hist_line(ns1, "full", "fail", rg.compute_artifact_hash(ns1))
        check("next-scope: last full/fail -> incremental", rg.next_scope(ns1), "incremental")

        # last entry {incremental, fail} -> incremental
        ns2 = skill({"SKILL.md": "x"})
        hist_line(ns2, "incremental", "fail", rg.compute_artifact_hash(ns2))
        check("next-scope: last incremental/fail -> incremental", rg.next_scope(ns2), "incremental")

        # last entry {incremental, pass}, no attestation -> full (attempt baseline)
        ns3 = skill({"SKILL.md": "x"})
        hist_line(ns3, "incremental", "pass", rg.compute_artifact_hash(ns3))
        check("next-scope: last incremental/pass -> full", rg.next_scope(ns3), "full")

        # a full+pass attestation whose hash == current content -> up-to-date
        ns4 = skill({"SKILL.md": "x", "run.py": "x=1\n"})
        attest(ns4, tier="standard")
        check("next-scope: current full+pass attestation -> up-to-date",
              rg.next_scope(ns4), "up-to-date")

        # full+pass attestation then content edited (hash drift) -> full
        ns5 = skill({"SKILL.md": "x", "run.py": "x=1\n"})
        attest(ns5, tier="standard")
        with open(os.path.join(ns5, "run.py"), "w") as f:
            f.write("x=2  # drift\n")
        check("next-scope: drift after full+pass -> full", rg.next_scope(ns5), "full")

        # a realistic sequence via the attest CLI: full/fail, several incremental/fail,
        # one incremental/pass -> full; then a full+pass attest + an edit -> full
        ns6 = skill({"SKILL.md": "draft", "run.py": "x=1\n"})
        run_attest(ns6, scope="full", verdict="fail",
                   review_verdict="needs-work", run_id="full-0")
        for i in range(3):
            run_attest(ns6, scope="incremental", verdict="fail",
                       review_verdict="needs-work", run_id="inc-%d" % i)
        run_attest(ns6, scope="incremental", verdict="pass",
                   review_verdict="solid-with-fixes", run_id="inc-pass")
        check("next-scope: realistic seq ending incremental/pass -> full",
              rg.next_scope(ns6), "full")
        ok("  still no attestation after that sequence", rg.load_attestation(ns6) is None)
        run_attest(ns6, scope="full", verdict="pass", tier="standard", run_id="full-1")
        check("next-scope: after the full+pass attest -> up-to-date",
              rg.next_scope(ns6), "up-to-date")
        with open(os.path.join(ns6, "run.py"), "w") as f:
            f.write("x=3  # post-ship edit\n")
        check("next-scope: after full+pass then edit -> full",
              rg.next_scope(ns6), "full")

        # bonus sanity: [full/fail, incr/fail, incr/fail, incr/fail] no attestation -> incremental
        ns7 = skill({"SKILL.md": "x"})
        hh7 = rg.compute_artifact_hash(ns7)
        hist_line(ns7, "full", "fail", hh7)
        for _ in range(3):
            hist_line(ns7, "incremental", "fail", hh7)
        check("next-scope: full/fail then 3x incremental/fail -> incremental",
              rg.next_scope(ns7), "incremental")

        # ── mark-baseline: a declared redesign resets the cadence to FULL ──
        def run_mark_baseline(skill_dir, note="", marked_at="2026-07-09T00:00:00Z"):
            """Invoke `review_gate.py mark-baseline` via the CLI."""
            argv = [sys.executable, os.path.join(HERE, "review_gate.py"),
                    "mark-baseline", "--skill", skill_dir]
            if note:
                argv += ["--note", note]
            if marked_at is not None:
                argv += ["--marked-at", marked_at]
            return subprocess.run(argv, capture_output=True, text=True)

        def raw_history_lines(skill_dir):
            """Raw text lines of the append-only history log (for byte-exact compare)."""
            path = os.path.join(skill_dir, rg.HISTORY_NAME)
            if not os.path.exists(path):
                return []
            with open(path) as f:
                return f.readlines()

        # after mark-baseline the newest entry is new-baseline; next-scope -> full,
        # even though the entry BEFORE it was incremental/fail (would yield incremental).
        mb0 = skill({"SKILL.md": "v1", "run.py": "x=1\n"})
        hist_line(mb0, "incremental", "fail", rg.compute_artifact_hash(mb0))
        check("mark-baseline: before marking, incremental/fail -> incremental",
              rg.next_scope(mb0), "incremental")
        p = run_mark_baseline(mb0, note="rewrote the whole approach")
        check("  mark-baseline exit 0", p.returncode, 0)
        ok("  mark-baseline prints confirmation", "marked new-baseline" in p.stdout)
        hist = rg.load_history(mb0)
        check("  newest entry is a new-baseline marker", hist[-1].get("scope"), "new-baseline")
        check("  after mark-baseline, next-scope -> full", rg.next_scope(mb0), "full")
        tok, err = run_next_scope(mb0)
        check("  next-scope CLI: new-baseline -> full on stdout", tok, "full")
        ok("  CLI explanation mentions new-baseline/redesign",
           "new-baseline" in err and "redesign" in err)

        # mark-baseline writes NO attestation and REMOVES no prior history lines.
        mb1 = skill({"SKILL.md": "v1", "run.py": "x=1\n"})
        run_attest(mb1, scope="full", verdict="fail",
                   review_verdict="needs-work", run_id="full-0")
        run_attest(mb1, scope="incremental", verdict="fail",
                   review_verdict="needs-work", run_id="inc-0")
        before_lines = raw_history_lines(mb1)
        before_count = len(before_lines)
        run_mark_baseline(mb1, note="ground-up redesign")
        after_lines = raw_history_lines(mb1)
        ok("  mark-baseline writes NO attestation", rg.load_attestation(mb1) is None)
        check("  mark-baseline grows history by exactly 1 line",
              len(after_lines), before_count + 1)
        ok("  prior history lines are unchanged (nothing deleted/rewritten)",
           after_lines[:before_count] == before_lines)

        # realistic sequence: [full/fail, incr/fail, incr/fail] -> incremental;
        # then mark-baseline -> full; then a later incremental/fail -> incremental again.
        mb2 = skill({"SKILL.md": "draft", "run.py": "x=1\n"})
        run_attest(mb2, scope="full", verdict="fail",
                   review_verdict="needs-work", run_id="full-0")
        run_attest(mb2, scope="incremental", verdict="fail",
                   review_verdict="needs-work", run_id="inc-0")
        run_attest(mb2, scope="incremental", verdict="fail",
                   review_verdict="needs-work", run_id="inc-1")
        check("  seq [full/fail, incr/fail, incr/fail] -> incremental",
              rg.next_scope(mb2), "incremental")
        run_mark_baseline(mb2, note="new baseline")
        check("  after mark-baseline -> full", rg.next_scope(mb2), "full")
        run_attest(mb2, scope="incremental", verdict="fail",
                   review_verdict="needs-work", run_id="inc-2")
        check("  a later incremental/fail (now newest) -> incremental again",
              rg.next_scope(mb2), "incremental")

        # history renders a mixed log (reviews + a new-baseline marker) without error.
        mb3 = skill({"SKILL.md": "draft", "run.py": "x=1\n"})
        run_attest(mb3, scope="full", verdict="fail",
                   review_verdict="needs-work", run_id="full-0")
        run_mark_baseline(mb3, note="mixed-log redesign")
        run_attest(mb3, scope="incremental", verdict="fail",
                   review_verdict="needs-work", run_id="inc-0")
        p = subprocess.run(
            [sys.executable, os.path.join(HERE, "review_gate.py"),
             "history", "--skill", mb3], capture_output=True, text=True)
        check("  history renders a mixed log exit 0", p.returncode, 0)
        ok("  mixed-log history shows the new-baseline scope", "new-baseline" in p.stdout)
        ok("  mixed-log history shows the baseline note", "mixed-log redesign" in p.stdout)
        ok("  mixed-log history shows review scopes too",
           "full" in p.stdout and "incremental" in p.stdout)

        # ── check-all: one bad skill among good ones -> exit 2 ────────────
        root = tempfile.mkdtemp(); tmpdirs.append(root)
        g1 = os.path.join(root, "good1"); os.makedirs(g1)
        with open(os.path.join(g1, "SKILL.md"), "w") as f: f.write("g1")
        attest(g1, tier="standard", findings=0)
        g2 = os.path.join(root, "good2"); os.makedirs(g2)
        with open(os.path.join(g2, "SKILL.md"), "w") as f: f.write("g2")
        attest(g2, tier="standard", findings=1)
        bad1 = os.path.join(root, "bad1"); os.makedirs(bad1)
        with open(os.path.join(bad1, "SKILL.md"), "w") as f: f.write("unreviewed")
        p = subprocess.run(
            [sys.executable, os.path.join(HERE, "review_gate.py"),
             "check-all", "--root", root], capture_output=True, text=True)
        check("check-all exit 2 with one bad skill", p.returncode, 2)
        ok("  check-all names the bad skill", "bad1" in (p.stdout + p.stderr))
        ok("  check-all passes the good ones", "PASS good1" in p.stdout)

        # ── Stop hook behavior ────────────────────────────────────────────
        # The hook nudges on DRIFT, not on never-reviewed. bad1 is unattested
        # (the normal state of a skill under construction): an alarm that fires
        # every turn gets switched off, so it stays quiet. The ship-block, not
        # this hook, is what refuses to install bad1.
        d, err = run_hook(root, stop_hook_active=False)
        check("hook stays quiet on a never-reviewed skill (default)", d, "allow")

        # ...but the ship-block still refuses that same skill.
        ok("never-reviewed skill is NOT shippable", not rg.check(bad1, rg.load_tiers())[0])

        # opt in, and 'missing' nudges too
        d, err = run_hook(root, stop_hook_active=False, states="missing,stale")
        check("hook nudges on 'missing' when asked to", d, "block")

        # drift: a passing full, then an edit -> nudge
        drifted = tempfile.mkdtemp(); tmpdirs.append(drifted)
        dr = os.path.join(drifted, "dr"); os.makedirs(dr)
        with open(os.path.join(dr, "SKILL.md"), "w") as f: f.write("reviewed")
        attest(dr, tier="standard", findings=0)
        d, err = run_hook(drifted, stop_hook_active=False)
        check("hook quiet while the attestation is current", d, "allow")
        with open(os.path.join(dr, "SKILL.md"), "a") as f: f.write("\nedited after review\n")
        d, err = run_hook(drifted, stop_hook_active=False)
        check("hook BLOCKS once when a passing review goes stale", d, "block")

        # the hook finds the skill you are standing IN, not just its subdirs
        d, err = run_hook(dr, stop_hook_active=False)
        check("hook scans the skill dir it was invoked inside", d, "block")

        # pending + stop_hook_active -> allow (never trap) + warn
        d, err = run_hook(drifted, stop_hook_active=True)
        check("hook allows when stop_hook_active (no trap)", d, "allow")
        ok("  hook warns it's still pending", "STILL pending" in err)

        # ── nudge ONCE PER CONTENT HASH, not once per turn ───────────────────
        # A stale skill stays stale until a full review passes — that can take days.
        # Nudging every turn is alarm fatigue, and a hook that always fires gets
        # uninstalled, taking the ship-block's early warning with it.
        ns = os.path.join(tempfile.mkdtemp(), "nudged.json")
        d, _ = run_hook(drifted, nudge_state=ns)
        check("first drift nudges", d, "block")
        d, err = run_hook(drifted, nudge_state=ns)
        check("same content, second turn -> SILENT (no alarm fatigue)", d, "allow")
        ok("  and it says why it stayed quiet", "already reported" in err)
        d, _ = run_hook(drifted, nudge_state=ns)
        check("same content, third turn -> still silent", d, "allow")

        # a NEW edit is a new drift event: speak up again
        with open(os.path.join(dr, "SKILL.md"), "a") as f: f.write("\nedited again\n")
        d, _ = run_hook(drifted, nudge_state=ns)
        check("a fresh edit nudges again (new content hash)", d, "block")
        d, _ = run_hook(drifted, nudge_state=ns)
        check("...then goes quiet again at the new hash", d, "allow")

        # going quiet NEVER relaxes the gate: the ship-block still refuses it
        ok("silenced skill is still NOT shippable", not rg.check(dr, rg.load_tiers())[0])

        # a second skill drifting is its own event, not masked by the first
        dr2 = os.path.join(drifted, "dr2"); os.makedirs(dr2)
        with open(os.path.join(dr2, "SKILL.md"), "w") as f: f.write("reviewed")
        attest(dr2, tier="standard", findings=0)
        with open(os.path.join(dr2, "SKILL.md"), "a") as f: f.write("\nedited\n")
        d, _ = run_hook(drifted, nudge_state=ns)
        check("a different skill drifting nudges on its own", d, "block")

        # unreadable nudge state -> nudge rather than silently miss
        bad_ns = os.path.join(tempfile.mkdtemp(), "bad.json")
        with open(bad_ns, "w") as f: f.write("not json {{{")
        d, _ = run_hook(drifted, nudge_state=bad_ns)
        check("corrupt nudge state -> nudges (never silently misses)", d, "block")

        # unwritable nudge state -> still nudges, warns, does not crash
        d, err = run_hook(drifted, nudge_state="/nonexistent-root/x/nudged.json")
        check("unwritable nudge state -> still nudges", d, "block")

        # clean root -> allow silently
        clean = tempfile.mkdtemp(); tmpdirs.append(clean)
        c1 = os.path.join(clean, "ok1"); os.makedirs(c1)
        with open(os.path.join(c1, "SKILL.md"), "w") as f: f.write("ok")
        attest(c1, tier="standard", findings=0)
        d, err = run_hook(clean, stop_hook_active=False)
        check("hook allows when clean", d, "allow")

        # garbage stdin -> fail-open allow + warn
        d, err = run_hook(root, garbage=True)
        check("hook fails-open on garbage stdin", d, "allow")
        ok("  garbage warns", "could not parse" in err)

        # ── derive_counts: the criterion is computed, never declared ──────────
        serious = {"severity": "high", "file": "a.py", "confirmed": True}
        refuted = {"severity": "medium", "file": "b.py", "confirmed": False}
        c = rg.derive_counts({"findings": [serious, refuted, low(), low(False)]})
        check("derive: serious counts crit/high/med only", c["serious_candidates"], 2)
        check("derive: serious_confirmed", c["serious_confirmed"], 1)
        check("derive: refuted serious -> human review", c["human_review"], 1)
        check("derive: confirmed_findings counts all severities", c["confirmed_findings"], 2)
        check("derive: lows alone are shippable",
              rg.derive_counts({"findings": [low(), low()]})["serious_candidates"], 0)
        for bad, why in [({}, "no findings key"),
                         ({"findings": "x"}, "findings not a list"),
                         ({"findings": [{"severity": "bogus"}]}, "unknown severity"),
                         ({"findings": [1]}, "finding not an object")]:
            try:
                rg.derive_counts(bad)
                ok("  derive REFUSES malformed (%s)" % why, False)
            except rg.ResultError:
                ok("  derive REFUSES malformed (%s)" % why, True)

        # ── the omission bypass is closed ────────────────────────────────────
        by = make_skill({"SKILL.md": "x"}); tmpdirs.append(by)
        p = subprocess.run(
            [sys.executable, os.path.join(HERE, "review_gate.py"), "attest",
             "--skill", by, "--scope", "full", "--tier", "standard", "--verdict", "pass",
             "--review-verdict", "clean", "--confirmed-findings", "0", "--run-id", "omit"],
            capture_output=True, text=True)
        check("full pass WITHOUT --from-result is refused", p.returncode, 2)
        ok("  no attestation written", rg.load_attestation(by) is None)
        ok("  and it says why", "--from-result" in p.stderr)

        # a hand-typed serious count on a full pass is refused outright
        r_ok = write_result(by, [])
        p = subprocess.run(
            [sys.executable, os.path.join(HERE, "review_gate.py"), "attest",
             "--skill", by, "--scope", "full", "--tier", "standard", "--verdict", "pass",
             "--review-verdict", "clean", "--serious-candidates", "0",
             "--from-result", r_ok, "--run-id", "typed"],
            capture_output=True, text=True)
        check("hand-typed --serious-candidates on a full pass is refused", p.returncode, 2)
        ok("  no attestation written", rg.load_attestation(by) is None)

        # ── a CONFIRMED serious finding blocks the pass, derived from findings ───────
        p = run_attest(by, scope="full", verdict="pass",
                       from_result=write_result(by, [serious], name="serious.json"))
        check("derived blocking_serious>0 blocks the pass", p.returncode, 2)
        ok("  no attestation written", rg.load_attestation(by) is None)
        ok("  refusal names CONFIRMED serious", "CONFIRMED serious" in p.stderr)
        ok("  the failed attempt IS on the audit trail", history_lines(by) >= 1)
        hist = rg.load_history(by)[-1]
        check("  history records the DERIVED blocking count", hist.get("blocking_serious"), 1)

        # a CONFIRMED finding the skeptics CORRECTED to low does NOT block (lows shippable) ──
        conf_low = {"severity": "medium", "file": "c.py", "confirmed": True,
                    "corrected_severity": "low"}
        p = run_attest(by, scope="full", verdict="pass",
                       from_result=write_result(by, [conf_low], name="conflow.json"))
        check("confirmed finding corrected to low PASSES (gate on corrected severity)",
              p.returncode, 0)
        ok("  attestation written", rg.load_attestation(by) is not None)

        # a REFUTED serious candidate does NOT hard-block; it is PENDING human adjudication ──
        by2 = make_skill({"SKILL.md": "y"}); tmpdirs.append(by2)
        p = run_attest(by2, scope="full", verdict="pass",
                       from_result=write_result(by2, [refuted], name="refuted.json"))
        check("a REFUTED serious candidate is refused until adjudicated", p.returncode, 2)
        ok("  refusal cites the human-review queue", "human-review queue" in p.stderr)
        ok("  no attestation written yet", rg.load_attestation(by2) is None)
        # WITH the operator's recorded adjudication of the queue, the pass goes through
        p = subprocess.run(
            [sys.executable, os.path.join(HERE, "review_gate.py"), "attest",
             "--skill", by2, "--scope", "full", "--tier", "standard", "--verdict", "pass",
             "--review-verdict", "clean", "--from-result",
             write_result(by2, [refuted], name="refuted2.json"),
             "--adjudicated-human-review", "1", "--adjudication", "reviewed: false positive",
             "--run-id", "adj"], capture_output=True, text=True)
        check("adjudicated human-review queue lets the pass through", p.returncode, 0)
        att = rg.load_attestation(by2)
        ok("  attestation records the adjudication",
           att is not None and att.get("human_review_adjudication") == "reviewed: false positive")
        # a WRONG adjudication count is refused (can't wave through more than were queued)
        p = subprocess.run(
            [sys.executable, os.path.join(HERE, "review_gate.py"), "attest",
             "--skill", by2, "--scope", "full", "--tier", "standard", "--verdict", "pass",
             "--review-verdict", "clean", "--from-result",
             write_result(by2, [refuted, {**refuted, "file": "d.py"}], name="two.json"),
             "--adjudicated-human-review", "1", "--adjudication", "x", "--run-id", "adj2"],
            capture_output=True, text=True)
        check("adjudication count must equal the derived queue size", p.returncode, 2)

        # ── the result must be bound to the bytes that were reviewed ─────────
        stale_r = write_result(by, [], artifact_hash="deadbeef" * 8, name="stale.json")
        p = run_attest(by, scope="full", verdict="pass", from_result=stale_r)
        check("a result reviewing OTHER bytes is refused", p.returncode, 2)
        ok("  refusal explains the drift", "changed after the review" in p.stderr)

        p = run_attest(by, scope="full", verdict="pass",
                       from_result=write_result(by, [], scope="incremental",
                                                name="inc.json"))
        check("an incremental result cannot attest a full pass", p.returncode, 2)

        missing = os.path.join(by, "reviews", "results", "nope.json")
        p = run_attest(by, scope="full", verdict="pass", from_result=missing)
        check("a missing result artifact is refused", p.returncode, 2)

        # ── the happy path still works, and records its provenance ───────────
        good = write_result(by, [low()], name="good.json")
        p = run_attest(by, scope="full", verdict="pass", from_result=good)
        check("a clean derived result attests", p.returncode, 0)
        att = rg.load_attestation(by)
        check("  attestation records derived serious_candidates", att["serious_candidates"], 0)
        check("  attestation records derived confirmed_findings", att["confirmed_findings"], 1)
        check("  attestation records its provenance",
              att.get("counts_derived_from"), os.path.abspath(good))
        ok("  and the skill is now shippable", rg.check(by, rg.load_tiers())[0])

        # writing results under reviews/ never disturbs the hash it is keyed to
        h_before = rg.compute_artifact_hash(by)
        write_result(by, [serious], name="another.json")
        check("results under reviews/ do not change the artifact hash",
              rg.compute_artifact_hash(by), h_before)

    finally:
        for d in tmpdirs:
            shutil.rmtree(d, ignore_errors=True)

    if _fails:
        print("FAILED %d/%d checks:" % (len(_fails), _checks))
        for f in _fails:
            print("  - " + f)
        sys.exit(1)
    print("all %d checks passed" % _checks)


if __name__ == "__main__":
    main()
