#!/usr/bin/env python3
"""guard.py — portable PreToolUse guard hook (attestation / send / data / secret).

Reads a Claude Code PreToolUse event on stdin, consults an OPTIONAL
guard.config.json next to this script, and emits allow / ask / deny.

Design (see DESIGN.md for the full contract):
  * FAIL-CLOSED with a hardcoded SAFETY FLOOR.
      - The FLOOR always runs, needs NO config and NO third-party deps.
      - Config only ADDS to the floor.
      - Config unreadable  -> floor still protects, everything else allowed,
        loud stderr notice (session is never bricked by a bad config).
      - Per-call eval error with a valid config -> that call is denied.
      - Unparseable hook event -> denied (we can't tell what it is).
  * STDLIB ONLY. The hook runs under whatever `python3` the shell resolves
    (often bare system python), so config is JSON, not YAML — no import that
    could be missing. A safety guard must not degrade to floor-only silently.
  * Speaks up only to block/ask; stays SILENT (exit 0, no output) to allow,
    so normal permission flow proceeds untouched.

Guards, in precedence order:
  attestation_guard — denies a SHIP (install/push of a skill) whose review gate
      is not green, by invoking review_gate.check from the harness rather than
      hoping an agent runs it. Narrow: silent unless the call really is a ship.
      Fail-CLOSED on the ship path. The FLOOR additionally denies hand-writing
      any attestation or history file — those have exactly one writer.
  secret_guard / send_guard / data_guard — as before.
"""
import sys, os, json, re, fnmatch

# ── Hardcoded safety floor ────────────────────────────────────────────────
# Universally-dangerous cases. MUST work with zero config and zero deps.
FLOOR_TOOL_SUBSTRINGS = ("send_message", "send_email", "send_mail")
FLOOR_BASH_PATTERNS = (
    re.compile(r"curl\b[^\n]*\b(apply|application|submit)\b", re.I),
    re.compile(r"\bwget\b[^\n]*\b(apply|application|submit)\b", re.I),
)
FILE_TOOLS = ("Write", "Edit", "MultiEdit")

# Attestations are written by ONE writer: review_gate.py (and lint_egress.py
# --attest). Hand-editing one launders unreviewed content past the gate, and
# hand-editing the append-only history destroys the audit trail. Both are always
# wrong, everywhere, so this lives in the floor: no config, no deps, no bypass.
FLOOR_ATTESTATION_FILES = (
    ".review-attestation.json",
    ".review-history.jsonl",
    ".egress-lint-attestation.json",
)
# Shell verbs that would rewrite/remove an attestation file rather than read it.
# Matched ADJACENT to the filename, within one command segment (no ; && || between),
# so `cat x/.review-attestation.json` and `grep attestation . > out.txt` stay allowed
# while `rm .../.review-history.jsonl` and `echo {} > .../.review-attestation.json`
# do not. Mentioning the file is fine; targeting it with a mutating verb is not.
_SEG = r"[^;&|\n]*"
_MUTATING_VERB = r"\b(rm|mv|cp|tee|truncate|dd|shred|install)\b"


def _floor_attest_bash(cmd):
    """Name of the attestation file this command would mutate, or None."""
    for name in FLOOR_ATTESTATION_FILES:
        esc = re.escape(name)
        if re.search(r">>?\s*[^\s;&|]*" + esc, cmd):              # redirect target
            return name
        if re.search(_MUTATING_VERB + _SEG + esc, cmd, re.I):     # verb, same segment
            return name
        if re.search(r"\bsed\b" + _SEG + r"-i" + _SEG + esc, cmd, re.I):
            return name
    return None


# ── Output helpers ────────────────────────────────────────────────────────
def _resp(decision, reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason}}))
    sys.exit(0)

def deny(reason): _resp("deny", reason)
def ask(reason):  _resp("ask", reason)
def allow():      sys.exit(0)                       # silent -> normal flow
def warn(msg):    print("[guard] " + msg, file=sys.stderr)


# ── The floor (config-independent) ────────────────────────────────────────
# DEFENSE IN DEPTH, not the guarantee. The bash arm matches a fixed set of mutating verbs
# and redirects, so an interpreter write (`python3 -c "open(f,'w')"`, `perl -i`, `ed`) can
# slip past it. That is acceptable because the floor is NOT what makes the review binding:
# the actual guarantee is review_gate.check() re-deriving the content hash and verifying each
# lens's hash-bound evidence (so any edit — however written — invalidates the attestation),
# backstopped by the pre-push git hook. The floor just makes the obvious hand-edits loud.
def floor_check(tool, inp):
    low = tool.lower()
    for s in FLOOR_TOOL_SUBSTRINGS:
        if s in low:
            return ("[floor] tool '%s' looks like a send surface — blocked "
                    "(Rule 3: a human sends)." % tool)
    if tool in FILE_TOOLS:
        base = os.path.basename(inp.get("file_path", "") or "")
        if base in FLOOR_ATTESTATION_FILES:
            return ("[floor] '%s' is written by review_gate.py alone — never by hand. "
                    "Editing it would key a passing attestation to content nobody "
                    "reviewed. Run the review and `review_gate.py attest`." % base)
    if tool == "Bash":
        cmd = inp.get("command", "") or ""
        for pat in FLOOR_BASH_PATTERNS:
            if pat.search(cmd):
                return "[floor] Bash command looks like an auto-submit — blocked (Rule 3)."
        hit = _floor_attest_bash(cmd)
        if hit:
            return ("[floor] Bash command would rewrite or remove '%s'. That file has "
                    "exactly one writer (review_gate.py); the history log is "
                    "append-only and must never be reset." % hit)
    return None


# ── Config ────────────────────────────────────────────────────────────────
def load_config():
    path = os.environ.get("GUARD_CONFIG") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "guard.config.json")
    if not os.path.exists(path):
        return None, "no config at %s" % path
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception as e:                           # malformed JSON, IO error
        return None, "parse error: %s" % e
    if not isinstance(data, dict):
        return None, "config is not a JSON object"
    return data, None


def _path_hits(path, patterns):
    if not path:
        return None
    base = os.path.basename(path)
    for pat in patterns:
        if fnmatch.fnmatch(base, pat) or fnmatch.fnmatch(path, pat):
            return pat
        if "*" not in pat and pat in path:           # plain substring, no glob
            return pat
    return None


def _bash_refs(cmd, patterns):
    if not cmd:
        return None
    for pat in patterns:
        if "*" in pat:                               # globs don't apply to free text
            continue
        if pat and pat in cmd:
            return pat
    return None


# ── attestation_guard: the review gate, given a door ──────────────────────
# `review_gate.py check` is a gate nobody called. Here it is invoked by the
# harness on the one action it exists to stop — shipping. A gate an agent
# chooses to call is not a gate.
def _load_review_gate(path):
    """Import review_gate.py from an absolute path. Stdlib only."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_rg_guard", path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load review_gate from %s" % path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _skills_under(rg, roots):
    """{basename: skill_dir} for every skill under every configured root."""
    found = {}
    for root in roots:
        root = os.path.expanduser(root)
        for d in rg.find_skills(root):
            found[os.path.basename(d.rstrip(os.sep))] = d
    return found


def _install_target(cfg, tool, inp):
    """Skill name if this call writes into the installed-skills tree, else None."""
    install_root = os.path.expanduser(cfg.get("install_root", "") or "")
    if not (install_root and tool in FILE_TOOLS):
        return None
    fp = os.path.abspath(inp.get("file_path", "") or "")
    if not fp.startswith(install_root + os.sep):
        return None
    return fp[len(install_root) + 1:].split(os.sep)[0] or None


def _is_ship_bash(cfg, tool, inp):
    """Does this Bash command look like a ship? Cheap: regex only, no imports."""
    if tool != "Bash":
        return False
    cmd = inp.get("command", "") or ""
    return any(re.search(p, cmd, re.I)
               for p in (cfg.get("ship_bash_patterns", []) or []))


def _bash_targets(cfg, rg, inp, cwd):
    """Which skills a ship-shaped Bash command is about to ship. [] = none."""
    roots = cfg.get("skills_roots", []) or []
    known = _skills_under(rg, roots)
    if not known:
        return []
    cmd = inp.get("command", "") or ""

    # (a) the command names skills explicitly (cp -r skills/rca ~/.claude/skills/)
    named = [(n, d) for n, d in sorted(known.items())
             if re.search(r"(^|[/\s'\"])%s([/\s'\"]|$)" % re.escape(n), cmd)]
    if named:
        return named

    # (b) no name, but we are standing inside a skills root (e.g. bare `git push`)
    cwd = os.path.abspath(cwd or "")
    for root in roots:
        root = os.path.abspath(os.path.expanduser(root))
        if cwd == root or cwd.startswith(root + os.sep):
            return sorted(known.items())
    return []                                        # a ship-shaped command elsewhere


def attestation_check(cfg, tool, inp, cwd):
    """Deny a ship whose review gate is not green. Fail-CLOSED on the ship path.

    Cheap precondition first: this runs on EVERY Bash/Write/Edit call, so it must
    decide "not a ship" with regex alone and return before importing anything.
    """
    if not isinstance(cfg, dict):
        return None
    installing = _install_target(cfg, tool, inp)
    ship_bash = _is_ship_bash(cfg, tool, inp)
    if not (installing or ship_bash):
        return None                                  # the overwhelmingly common path

    dec = cfg.get("decision", "deny")
    gate_path = os.path.expanduser(cfg.get("review_gate", "") or "")
    try:
        rg = _load_review_gate(gate_path)
        if installing:
            targets = [(installing, _skills_under(rg, cfg.get("skills_roots", []) or [])
                        .get(installing))]
        else:
            targets = _bash_targets(cfg, rg, inp, cwd)
    except Exception as e:
        # A ship-shaped call whose gate we cannot even load: refuse it.
        return (dec, "[attestation_guard] cannot run the review gate (%s) — refusing "
                     "a ship-shaped call (fail-closed)." % e)
    if not targets:
        return None                                  # ship-shaped, but not of a skill

    try:
        tiers = rg.load_tiers()
        failures = []
        for name, skill_dir in targets:
            if not skill_dir:
                failures.append((name, "no source skill found to check (never reviewed)"))
                continue
            ok_, reason = rg.check(skill_dir, tiers)
            if not ok_:
                failures.append((name, reason))
    except Exception as e:
        return (dec, "[attestation_guard] review gate errored (%s) — ship refused "
                     "(fail-closed)." % e)

    if not failures:
        return None
    lines = "; ".join("%s: %s" % (n, r) for n, r in failures[:4])
    return (dec, "[attestation_guard] BLOCKED — %d skill(s) fail the review gate: %s. "
                 "Nothing ships without a passing, hash-keyed full review. Run the "
                 "adversarial review, fix serious findings, then "
                 "`review_gate.py attest --from-result <review.json>`."
                 % (len(failures), lines))


def evaluate(cfg, tool, inp, cwd=""):
    """Precedence: attestation (deny) -> secret (deny) -> send (deny) -> data (ask)."""
    att = cfg.get("attestation_guard")
    if isinstance(att, dict):
        hit = attestation_check(att, tool, inp, cwd)
        if hit:
            return hit

    sec = cfg.get("secret_guard")
    if isinstance(sec, dict):
        pats = sec.get("protected_paths", []) or []
        dec = sec.get("decision", "deny")
        if tool in FILE_TOOLS:
            hit = _path_hits(inp.get("file_path", ""), pats)
            if hit:
                return (dec, "[secret_guard] write touches protected secret matching '%s'." % hit)
        if tool == "Bash":
            hit = _bash_refs(inp.get("command", ""), pats)
            if hit:
                return (dec, "[secret_guard] Bash command references protected secret matching '%s'." % hit)

    snd = cfg.get("send_guard")
    if isinstance(snd, dict):
        dec = snd.get("decision", "deny")
        blocked_tools = snd.get("blocked_tools", []) or []
        allow_drafts = snd.get("allow_drafts", True)
        is_draft = allow_drafts and "create_draft" in tool
        if not is_draft and tool in blocked_tools:
            return (dec, "[send_guard] '%s' is a send surface — a human sends (Rule 3)." % tool)
        if tool == "Bash":
            cmd = inp.get("command", "") or ""
            for p in (snd.get("blocked_bash_patterns", []) or []):
                if re.search(p, cmd, re.I):
                    return (dec, "[send_guard] Bash command matches auto-submit pattern '%s'." % p)

    dat = cfg.get("data_guard")
    if isinstance(dat, dict) and tool in FILE_TOOLS:
        dec = dat.get("decision", "ask")
        hit = _path_hits(inp.get("file_path", ""), dat.get("protected_paths", []) or [])
        if hit:
            return (dec, "[data_guard] editing human-authored data '%s' — confirm this is "
                         "intentional (nothing auto-edits your data)." % hit)
    return None


# ── Entry ─────────────────────────────────────────────────────────────────
def main():
    raw = sys.stdin.read()
    try:
        event = json.loads(raw)
    except Exception:
        deny("[guard] could not parse hook event JSON (fail-closed).")
    tool = event.get("tool_name", "") or ""
    inp = event.get("tool_input", {})
    if not isinstance(inp, dict):
        inp = {}

    # 1. floor — always, no deps
    hit = floor_check(tool, inp)
    if hit:
        deny(hit)

    # 2. config
    cfg, err = load_config()
    if err:
        warn("config unusable (%s); running on safety floor only." % err)
        allow()

    # 3. config guards — fail-closed per call
    try:
        decision = evaluate(cfg, tool, inp, event.get("cwd", "") or "")
    except Exception as e:
        deny("[guard] error evaluating this call (fail-closed): %s" % e)
    if decision:
        _resp(decision[0], decision[1])
    allow()


if __name__ == "__main__":
    main()
