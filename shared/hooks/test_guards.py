#!/usr/bin/env python3
"""Offline tests for guard.py. No network, no third-party deps.
Run: python3 test_guards.py   (exit 0 = all pass)

Tests the ENGINE (guard.py) against a self-contained FIXTURE config, so this
file passes identically in every deployment regardless of which
guard.config.json ships alongside it. A final light check confirms the
shipped config (if present) is at least valid JSON.
"""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "guard.py")

# Self-contained fixture — the tests assert guard.py behavior GIVEN this config.
FIXTURE = {
    "secret_guard": {
        "decision": "deny",
        "protected_paths": ["sheets-creds.json", "*service-account*.json", "*.pem"],
    },
    "send_guard": {
        "decision": "deny",
        "allow_drafts": True,
        "blocked_tools": ["mcp__claude_ai_Gmail__send_message"],
        "blocked_bash_patterns": ["curl\\b.*\\b(apply|application|submit)\\b", "gh pr create"],
    },
    "data_guard": {
        "decision": "ask",
        "protected_paths": ["profile.yaml", "*/pack.yaml"],
    },
}

_checks = 0
_fails = []
_FIX_PATH = None
_MISSING = os.path.join(HERE, "__nonexistent_config__.json")


def run(tool, tool_input, config, cwd=""):
    env = dict(os.environ)
    env["GUARD_CONFIG"] = config
    event = {"hook_event_name": "PreToolUse", "tool_name": tool,
             "tool_input": tool_input, "cwd": cwd}
    p = subprocess.run([sys.executable, GUARD], input=json.dumps(event),
                       capture_output=True, text=True, env=env)
    decision = "allow"
    if p.stdout.strip():
        decision = json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"]
    return decision, p.stderr


def reason(tool, tool_input, config, cwd=""):
    env = dict(os.environ)
    env["GUARD_CONFIG"] = config
    event = {"hook_event_name": "PreToolUse", "tool_name": tool,
             "tool_input": tool_input, "cwd": cwd}
    p = subprocess.run([sys.executable, GUARD], input=json.dumps(event),
                       capture_output=True, text=True, env=env)
    if not p.stdout.strip():
        return ""
    return json.loads(p.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


def raw(stdin_text, config):
    env = dict(os.environ)
    env["GUARD_CONFIG"] = config
    p = subprocess.run([sys.executable, GUARD], input=stdin_text,
                       capture_output=True, text=True, env=env)
    decision = "allow"
    if p.stdout.strip():
        decision = json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"]
    return decision, p.stderr


def check(label, got, want):
    global _checks
    _checks += 1
    if got != want:
        _fails.append("%s: got %r want %r" % (label, got, want))


def main():
    global _FIX_PATH
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, dir=HERE)
    json.dump(FIXTURE, fh)
    fh.close()
    _FIX_PATH = fh.name
    F = _FIX_PATH
    try:
        # ── FLOOR (config-independent: point at a missing config) ──────────
        d, _ = run("mcp__x__send_message", {"to": "a@b.c"}, _MISSING)
        check("floor blocks send tool w/o config", d, "deny")
        d, _ = run("Bash", {"command": "curl -X POST https://boards.greenhouse.io/x/apply"}, _MISSING)
        check("floor blocks curl-apply w/o config", d, "deny")
        d, err = run("Bash", {"command": "ls -la"}, _MISSING)
        check("floor allows benign bash w/o config", d, "allow")
        check("warns 'floor only' when config missing", "running on safety floor only" in err, True)

        # ── SEND guard ─────────────────────────────────────────────────────
        check("send: denies configured send tool", run("mcp__claude_ai_Gmail__send_message", {"to": "x"}, F)[0], "deny")
        check("send: allows create_draft", run("mcp__claude_ai_Gmail__create_draft", {"to": "x"}, F)[0], "allow")
        check("send: denies bash apply pattern", run("Bash", {"command": "curl https://x/application -d @f"}, F)[0], "deny")
        check("send: denies gh pr create", run("Bash", {"command": "gh pr create --fill"}, F)[0], "deny")

        # ── SECRET guard ───────────────────────────────────────────────────
        check("secret: denies write to creds", run("Write", {"file_path": "/proj/sheets-creds.json", "content": "x"}, F)[0], "deny")
        check("secret: denies bash referencing creds", run("Bash", {"command": "git add sheets-creds.json"}, F)[0], "deny")
        check("secret: glob matches service-account json", run("Write", {"file_path": "/p/my-service-account-key.json"}, F)[0], "deny")
        check("secret: ignores unrelated bash", run("Bash", {"command": "cat README.md"}, F)[0], "allow")

        # ── DATA guard (ask) ───────────────────────────────────────────────
        check("data: asks on profile.yaml write", run("Write", {"file_path": "/proj/profile.yaml"}, F)[0], "ask")
        check("data: asks on pack.yaml edit", run("Edit", {"file_path": "/proj/skills/x/pack.yaml"}, F)[0], "ask")
        check("data: ignores non-data write", run("Write", {"file_path": "/proj/out/scored.json"}, F)[0], "allow")

        # ── Benign / precedence ────────────────────────────────────────────
        check("unrelated Read allowed", run("Read", {"file_path": "/p/x.txt"}, F)[0], "allow")
        check("pipeline bash allowed", run("Bash", {"command": "python3 rank.py ingest"}, F)[0], "allow")

        # ── Fail-closed on garbage event ───────────────────────────────────
        check("unparseable event denied (fail-closed)", raw("not json", F)[0], "deny")
        check("missing tool_input tolerated", raw(json.dumps({"tool_name": "Bash"}), F)[0], "allow")

        # ── Broken config -> floor only, not bricked ───────────────────────
        broken = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, dir=HERE)
        broken.write("{ not valid json ,,,")
        broken.close()
        try:
            check("broken config: floor still blocks send", run("mcp__x__send_message", {"to": "a"}, broken.name)[0], "deny")
            d, err = run("Bash", {"command": "ls"}, broken.name)
            check("broken config: benign still allowed", d, "allow")
            check("broken config: warns", "config unusable" in err, True)
        finally:
            os.unlink(broken.name)

        # ── Shipped config (if any) is at least valid JSON object ──────────
        shipped = os.path.join(HERE, "guard.config.json")
        if os.path.exists(shipped):
            try:
                obj = json.load(open(shipped))
                check("shipped guard.config.json is a JSON object", isinstance(obj, dict), True)
            except Exception as e:
                check("shipped guard.config.json parses (%s)" % e, False, True)

        # ── FLOOR: attestation files have exactly one writer ─────────────────
        # No config at all — these must hold even when guard.config.json is gone.
        for fname in (".review-attestation.json", ".review-history.jsonl",
                      ".egress-lint-attestation.json"):
            d, _ = run("Write", {"file_path": "/x/skills/s/%s" % fname}, _MISSING)
            check("floor denies hand-writing %s" % fname, d, "deny")
        # mutating verbs targeting the file -> deny
        for cmd, why in [
                ("rm skills/s/.review-history.jsonl", "rm of the append-only history"),
                ("echo '{}' > skills/s/.review-attestation.json", "clobber via redirect"),
                ("echo x >> skills/s/.review-history.jsonl", "append via redirect"),
                ("mv /tmp/a.json skills/s/.review-attestation.json", "mv onto it"),
                ("cp /tmp/a.json skills/s/.review-attestation.json", "cp onto it"),
                ("truncate -s 0 skills/s/.review-history.jsonl", "truncate"),
                ("sed -i 's/fail/pass/' skills/s/.review-attestation.json", "in-place sed"),
                ("echo '{}' | tee skills/s/.review-attestation.json", "tee"),
                ("rm skills/s/.egress-lint-attestation.json", "rm of the lint attestation")]:
            d, _ = run("Bash", {"command": cmd}, _MISSING)
            check("floor denies: %s" % why, d, "deny")

        # merely NAMING the file, or reading it, is fine — an over-denying guard
        # gets switched off, taking the real protections with it
        for cmd, why in [
                ("cat skills/s/.review-attestation.json", "cat it"),
                ("python3 review_gate.py check --skill skills/s", "run the gate"),
                ("grep -rn attestation . > /tmp/out.txt", "grep, redirect elsewhere"),
                ("ls -la skills/s/.review-attestation.json", "stat it"),
                ("git log -- skills/s/.review-history.jsonl", "read its history"),
                ("rm /tmp/scratch.json && cat skills/s/.review-attestation.json",
                 "rm in a DIFFERENT segment"),
                ("jq . skills/s/.review-attestation.json", "parse it")]:
            d, _ = run("Bash", {"command": cmd}, _MISSING)
            check("floor ALLOWS: %s" % why, d, "allow")

        d, _ = run("Read", {"file_path": "/x/skills/s/.review-attestation.json"}, _MISSING)
        check("floor ALLOWS Read of an attestation", d, "allow")

        # ── attestation_guard: the ship-block ────────────────────────────────
        rg_path = os.path.join(HERE, "..", "review-gate", "review_gate.py")
        if os.path.exists(rg_path):
            root = tempfile.mkdtemp()
            inst = tempfile.mkdtemp()
            sk = os.path.join(root, "demo")
            os.makedirs(sk)
            with open(os.path.join(sk, "SKILL.md"), "w") as f:
                f.write("demo")
            acfg = {"attestation_guard": {
                "decision": "deny",
                "review_gate": os.path.abspath(rg_path),
                "skills_roots": [root],
                "install_root": inst,
                "ship_bash_patterns": [r"\bgit\s+push\b",
                                       r"\b(cp|rsync)\b[^\n]*\.claude/skills"]}}
            ac = os.path.join(tempfile.mkdtemp(), "attest.config.json")
            with open(ac, "w") as f:
                json.dump(acfg, f)

            # unattested skill -> every ship path is denied
            d, _ = run("Bash", {"command": "cp -r %s ~/.claude/skills/" % sk}, ac, cwd=root)
            check("ship of an unattested skill is DENIED", d, "deny")
            r = reason("Bash", {"command": "cp -r %s ~/.claude/skills/" % sk}, ac, cwd=root)
            check("  and it names the gate", "review gate" in r, True)
            d, _ = run("Bash", {"command": "git push origin main"}, ac, cwd=sk)
            check("git push from inside a skills root is DENIED", d, "deny")
            d, _ = run("Write", {"file_path": os.path.join(inst, "demo", "SKILL.md")}, ac)
            check("writing into install_root is DENIED", d, "deny")

            # narrowness: it must stay silent when the call is not a ship
            d, _ = run("Bash", {"command": "git push origin main"}, ac, cwd="/tmp/elsewhere")
            check("git push OUTSIDE a skills root is allowed (narrow)", d, "allow")
            d, _ = run("Bash", {"command": "grep -rn foo ."}, ac, cwd=root)
            check("an ordinary command is allowed", d, "allow")
            d, _ = run("Edit", {"file_path": os.path.join(sk, "SKILL.md")}, ac, cwd=root)
            check("editing skill source is allowed", d, "allow")

            # attested + current -> the ship goes through
            sys.path.insert(0, os.path.dirname(os.path.abspath(rg_path)))
            import review_gate as _rg                              # noqa: E402
            _rg.write_attestation(sk, {
                "skill": "demo", "artifact_hash": _rg.compute_artifact_hash(sk),
                "review_tier": "standard", "review_scope": "full", "verdict": "pass",
                "review_verdict": "clean", "confirmed_findings": 0,
                "serious_candidates": 0, "run_id": "t", "reviewed_at": "2026-07-09",
                "reviewer_version": "1"})
            d, _ = run("Bash", {"command": "cp -r %s ~/.claude/skills/" % sk}, ac, cwd=root)
            check("ship of an ATTESTED, current skill is allowed", d, "allow")

            # drift re-locks it
            with open(os.path.join(sk, "SKILL.md"), "a") as f:
                f.write("\nedited after review\n")
            d, _ = run("Bash", {"command": "cp -r %s ~/.claude/skills/" % sk}, ac, cwd=root)
            check("drift after attestation re-DENIES the ship", d, "deny")

            # fail-CLOSED: gate unloadable, but the command looks like a ship
            bad = dict(acfg)
            bad["attestation_guard"] = dict(acfg["attestation_guard"],
                                            review_gate="/nonexistent/review_gate.py")
            bc = os.path.join(tempfile.mkdtemp(), "bad.json")
            with open(bc, "w") as f:
                json.dump(bad, f)
            d, _ = run("Bash", {"command": "cp -r %s ~/.claude/skills/" % sk}, bc, cwd=root)
            check("unloadable review gate + ship-shaped command -> DENY (fail-closed)",
                  d, "deny")
            d, _ = run("Bash", {"command": "ls"}, bc, cwd=root)
            check("unloadable review gate + benign command -> allow", d, "allow")
    finally:
        os.unlink(_FIX_PATH)

    if _fails:
        print("FAILED %d/%d checks:" % (len(_fails), _checks))
        for f in _fails:
            print("  - " + f)
        sys.exit(1)
    print("all %d checks passed" % _checks)


if __name__ == "__main__":
    main()
