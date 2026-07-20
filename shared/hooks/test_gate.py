#!/usr/bin/env python3
"""Offline tests for gate.py (the Stop-hook critique gate). No network/deps.
Run: python3 test_gate.py   (exit 0 = all pass)

Drives gate.py with synthetic records in a temp dir + a fixture config, so it
passes identically in every deployment.
"""
import json, os, subprocess, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "gate.py")

FIXTURE_CFG = {
    "gates": [
        {"name": "tailor critique", "records_glob": "records/*.json",
         "status_field": "status", "pending_statuses": ["Built - pending critique"],
         "remediation": "python3 tailor.py critique"}
    ]
}

_checks = 0
_fails = []


def check(label, got, want):
    global _checks
    _checks += 1
    if got != want:
        _fails.append("%s: got %r want %r" % (label, got, want))


def run(records, stop_hook_active=False, cfg=FIXTURE_CFG, bad_cfg=False, garbage=False):
    """Set up a temp project with the given records, invoke gate.py, return
    (decision, stderr) where decision is 'block' or 'allow'."""
    proj = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(proj, "records"))
        for i, rec in enumerate(records):
            with open(os.path.join(proj, "records", "r%d.json" % i), "w") as f:
                json.dump(rec, f)
        cfg_path = os.path.join(proj, "gate.config.json")
        if bad_cfg:
            open(cfg_path, "w").write("{ not json ,,,")
        else:
            json.dump(cfg, open(cfg_path, "w"))
        env = dict(os.environ)
        env["GATE_CONFIG"] = cfg_path if not garbage else os.path.join(proj, "missing.json")
        event = {"hook_event_name": "Stop", "cwd": proj, "stop_hook_active": stop_hook_active}
        stdin = "not json" if garbage else json.dumps(event)
        p = subprocess.run([sys.executable, GATE], input=stdin, capture_output=True, text=True, env=env)
        decision = "allow"
        if p.stdout.strip():
            decision = json.loads(p.stdout).get("decision", "allow")
        return decision, p.stderr
    finally:
        shutil.rmtree(proj, ignore_errors=True)


PENDING = {"status": "Built - pending critique", "critique": None}
READY = {"status": "Ready for Review", "critique": {"findings": []}}
REWORK = {"status": "Needs rework", "critique": {"findings": ["x"]}}
SCORED = {"status": "Scored"}


def main():
    # ── Core: pending record blocks the stop ──────────────────────────────
    d, r = run([PENDING])
    check("pending record blocks stop", d, "block")
    check("block reason names remediation", "tailor.py critique" in r or True, True)  # reason on stdout, not stderr

    d, _ = run([READY])
    check("critiqued record allows stop", d, "allow")

    d, _ = run([REWORK])
    check("needs-rework (critique ran) allows stop", d, "allow")

    d, _ = run([SCORED])
    check("unrelated 'Scored' record allows stop", d, "allow")

    d, _ = run([])
    check("no records allows stop", d, "allow")

    d, _ = run([READY, PENDING, SCORED])
    check("mix with one pending blocks", d, "block")

    # ── Loop safety: nudge once per turn ──────────────────────────────────
    d, err = run([PENDING], stop_hook_active=True)
    check("pending + stop_hook_active -> allow (no trap)", d, "allow")
    check("  and warns it's still pending", "STILL pending" in err, True)

    # ── Fail-OPEN (Stop hook must never trap) ─────────────────────────────
    d, err = run([PENDING], garbage=True)
    check("garbage event -> allow (fail-open)", d, "allow")
    check("  garbage warns", "could not parse" in err, True)

    d, err = run([PENDING], bad_cfg=True)
    check("broken config -> allow (fail-open)", d, "allow")
    check("  broken config warns", "config unusable" in err, True)

    # ── Verify block reason content (via stdout directly) ─────────────────
    proj = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(proj, "records"))
        json.dump(PENDING, open(os.path.join(proj, "records", "a.json"), "w"))
        cfg_path = os.path.join(proj, "gate.config.json")
        json.dump(FIXTURE_CFG, open(cfg_path, "w"))
        env = dict(os.environ); env["GATE_CONFIG"] = cfg_path
        event = {"hook_event_name": "Stop", "cwd": proj, "stop_hook_active": False}
        p = subprocess.run([sys.executable, GATE], input=json.dumps(event), capture_output=True, text=True, env=env)
        out = json.loads(p.stdout)
        check("block payload has decision=block", out.get("decision"), "block")
        check("block reason mentions the command", "python3 tailor.py critique" in out.get("reason", ""), True)
    finally:
        shutil.rmtree(proj, ignore_errors=True)

    if _fails:
        print("FAILED %d/%d checks:" % (len(_fails), _checks))
        for f in _fails:
            print("  - " + f)
        sys.exit(1)
    print("all %d checks passed" % _checks)


if __name__ == "__main__":
    main()
