# guard hook layer

A portable, opt-in **PreToolUse** guard that hard-blocks consequential actions taken
*around* the deterministic scripts. Three guards in one script:

| guard | fires on | decision |
|---|---|---|
| `send_guard` | a send/submit tool, or a `Bash` submit pattern | **deny** (a human sends — Rule 3) |
| `secret_guard` | writing/committing a protected secret file | **deny** |
| `data_guard` | editing human-authored data (`profile.yaml`, `pack.yaml`, `rubric.yaml`) | **ask** |

Plus a hardcoded **safety floor** (known send tools + obvious `curl …/apply`) that works even
with **no config and no third-party deps** — so a broken config never disables protection and
never bricks the session.

Guard **#4** is a separate **Stop** hook (`gate.py`): it blocks *ending a turn* while any record
is built-but-not-critiqued, making the critique gate impossible to silently skip.

## Files (self-contained; drop the folder into any project as `hooks/`)
- `guard.py` — PreToolUse guard: send / secret / data (stdlib only; bare system `python3`)
- `guard.config.json` — this deployment's PreToolUse rules (JSON, not YAML — see DESIGN.md)
- `test_guards.py` — offline suite: `python3 test_guards.py`
- `gate.py` — Stop hook: the un-skippable critique gate (**fails OPEN** — never traps a turn)
- `gate.config.json` — critique-gate rules (records glob + pending status + remediation command)
- `test_gate.py` — offline suite: `python3 test_gate.py`
- `DESIGN.md` — full design note (contract, decisions, both hook types)

## Enable it (per project or per user)
Add to the applicable `settings.json` (`<project>/.claude/settings.json` for a project like the
job-search source, or `~/.claude/settings.json` for the universal floor across all sessions):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Write|Edit|MultiEdit|mcp__.*",
        "hooks": [
          { "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/hooks/guard.py\"",
            "timeout": 5 }
        ]
      }
    ]
  }
}
```

The guard finds `guard.config.json` next to itself automatically; override with the
`GUARD_CONFIG` env var. `Read` is intentionally **not** matched (avoids a python spawn on every
file read; the real leak vectors — write/commit/copy — go through `Bash`/`Write`).

### Guard #4 — critique gate (Stop hook)
Add a **Stop** hook so a turn cannot end while a built artifact hasn't been critiqued:

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command",
        "command": "python3 \"$CLAUDE_PROJECT_DIR/hooks/gate.py\"", "timeout": 5 } ] }
    ]
  }
}
```

`gate.py` scans (CWD-relative) each `gate.config.json` gate's `records_glob` for records whose
`status` is in `pending_statuses`; if any, it returns `{"decision":"block","reason":…}` telling
the model to run the `remediation` command. It **fails open** and honors `stop_hook_active`
(nudges once per turn, never traps). Override the config path with `GATE_CONFIG`.

## Tune per deployment
Edit `guard.config.json`. The job-search source adds its real ATS apply patterns +
`sheets-creds.json` / `.sheet_id` to `secret_guard`. Nothing personal lives in `guard.py`.
