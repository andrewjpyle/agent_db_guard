#!/usr/bin/env python3
"""agent_db_guard — a Claude Code PreToolUse hook that stops an AI agent from
running a destructive database command against production by accident.

THE PROBLEM

Coding agents run shell commands. Sooner or later one runs a schema migration, a
`TRUNCATE`, a `flush`, or a `loaddata` — and points it at the production
database, because the same command that is safe against a local DB is a disaster
against prod, and nothing in the command's shape tells the agent which one it hit.
"The AI dropped the prod database" is now a recognized failure genre.

THE APPROACH — and why it is not just a keyword blocklist

A command is gated only when it is BOTH:
  (a) database-destructive — a migrate/flush/seed/truncate/drop, a writing
      `psql`, or a management `shell -c` that calls an ORM write; AND
  (b) production-targeted — it carries a signal that its DB is prod.

The conjunction is the whole point. Blocking every destructive command trains the
operator to click through (alarm fatigue), and an alarm everyone dismisses is
worse than no alarm — it launders a real prod write as one more false one. So
routine prod reads and routine LOCAL destructive commands both pass silently;
only destructive-AND-prod stops.

Two more decisions carry the design:

* It ASKS, it does not DENY. A wall gets disabled the first time it's in the way;
  a gate gets read. An intentional, reviewed prod migration must stay possible —
  "ask" is a speed bump with a human (or a reachable supervised agent) at it, not
  a prohibition. In an unattended dispatch, "ask" pauses for supervision, which is
  exactly right.
* It matches WHOLE TOKENS, never substrings. A substring match on "drop" fires on
  `sync_raindrop`, `archive_to_dropbox`, `import_raindrops` — every one benign,
  every false positive feeding the alarm fatigue above. The subcommand name is
  split into word tokens and matched token-by-token.
* It FAILS OPEN. A PreToolUse hook sits in front of every Bash call; a crash here
  would brick the agent's shell. Any internal error logs and exits 0. Pure string
  analysis keeps that path unlikely, and a false miss is recoverable where a
  bricked agent is not.

CONFIG-DRIVEN

What counts as "destructive" and what counts as "prod" is YOUR environment's
answer, not this file's. Both lists live in a JSON config
(`.agent-db-guard.json`), so the engine is reusable and the policy is yours. See
`examples/` for a Django + a generic config. A missing config is a loud warning,
not a silent no-op — a guard that quietly guards nothing is the trap this whole
module exists to avoid.

INSTALL

Register as a PreToolUse hook for Bash in your Claude Code settings:

    {"hooks": {"PreToolUse": [{"matcher": "Bash",
      "hooks": [{"type": "command",
                 "command": "python3 /abs/path/agent_db_guard.py"}]}]}}

Point it at your config with AGENT_DB_GUARD_CONFIG=/abs/path/.agent-db-guard.json,
or drop `.agent-db-guard.json` at your project root (it is discovered by walking
up from the working directory).

Pure stdlib. No dependencies.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

LOG = Path.home() / ".agent_db_guard.log"
CONFIG_BASENAME = ".agent-db-guard.json"
CONFIG_ENV = "AGENT_DB_GUARD_CONFIG"

# Split a subcommand name into word tokens, for whole-token (not substring) match.
_TOKENS = re.compile(r"[^a-z0-9]+")


def log(msg: str) -> None:
    try:
        with LOG.open("a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()}  {msg}\n")
    except Exception:
        pass


class Config:
    """Validated view over the JSON policy. Everything the engine treats as
    'destructive' or 'prod' comes from here — nothing is hardcoded."""

    def __init__(self, data: dict):
        d = data.get("destructive", {})
        p = data.get("prod_signals", {})

        # Exact management subcommands that mutate the DB (migrate, flush, ...).
        self.exact_subcommands = frozenset(
            s.lower() for s in d.get("exact_subcommands", [])
        )
        # Whole-token verbs that mark a CUSTOM subcommand destructive (seed, drop).
        self.verbs = frozenset(v.lower() for v in d.get("verbs", []))
        # ORM write markers that make a `shell -c "..."` destructive.
        self.orm_write_markers = tuple(d.get("orm_write_markers", []))
        # Raw-SQL write verbs (for `psql -c "..."`).
        sql = d.get("sql_write_verbs", [])
        self.sql_write = (
            re.compile(r"\b(" + "|".join(re.escape(v) for v in sql) + r")\b", re.I)
            if sql else None
        )
        # How to pull a subcommand name out of a command line. Default: manage.py.
        self.subcommand_pattern = re.compile(
            d.get("subcommand_pattern", r"manage\.py\s+([a-zA-Z_][\w-]*)")
        )
        # Names that take an inline script/shell (checked for ORM writes).
        self.shell_subcommands = frozenset(
            s.lower() for s in d.get("shell_subcommands", ["shell", "shell_plus"])
        )

        # PROD signals: literal substrings (a host, an IP) ...
        self.prod_substrings = tuple(s.lower() for s in p.get("substrings", []))
        # ... and WRAPPER words: a command runner that always resolves to prod in
        # your setup (e.g. a secrets injector whose only config points at prod).
        # This is the escape hatch for "I can't see the resolved host, but this
        # wrapper means prod." Matched as a whole token, like the verbs.
        self.prod_wrapper_words = frozenset(
            w.lower() for w in p.get("wrapper_words", [])
        )

        self.message = data.get(
            "message",
            "This command runs a DESTRUCTIVE operation against a PRODUCTION "
            "database. Verify the target DB host before approving.",
        )

    def is_usable(self) -> bool:
        """A config that declares no destructive rule can never fire — that is the
        silent-no-op trap. Treat it as unusable so the caller warns."""
        return bool(
            self.exact_subcommands or self.verbs
            or self.orm_write_markers or self.sql_write
        ) and bool(
            self.prod_substrings or self.prod_wrapper_words
        )


def find_config_path(start: Path | None = None, env: dict | None = None) -> Path | None:
    """AGENT_DB_GUARD_CONFIG wins; else walk up from `start` looking for
    `.agent-db-guard.json`. Returns None if nothing is found."""
    env = os.environ if env is None else env
    explicit = env.get(CONFIG_ENV)
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    here = (start or Path.cwd()).resolve()
    for d in [here, *here.parents]:
        candidate = d / CONFIG_BASENAME
        if candidate.is_file():
            return candidate
    return None


def load_config(path: Path) -> Config:
    return Config(json.loads(path.read_text()))


def destructive_label(command: str, cfg: Config) -> str:
    """A short label if the command is DB-destructive per `cfg`, else ''."""
    for sub in cfg.subcommand_pattern.findall(command):
        low = sub.lower()
        if low in cfg.exact_subcommands:
            return f"{sub}"
        tokens = {t for t in _TOKENS.split(low) if t}
        if cfg.verbs.intersection(tokens):
            return f"{sub}"
        if low in cfg.shell_subcommands and "-c" in command:
            if any(m in command for m in cfg.orm_write_markers):
                return f"{sub} -c (ORM write)"
    if re.search(r"\bpsql\b", command):
        # `-f file.sql` runs an arbitrary script; an inline write verb is a write.
        if re.search(r"\s-f\b", command) or (cfg.sql_write and cfg.sql_write.search(command)):
            return "psql (write)"
    return ""


def targets_prod(command: str, cfg: Config) -> bool:
    low = command.lower()
    if cfg.prod_wrapper_words:
        tokens = {t for t in _TOKENS.split(low) if t}
        if cfg.prod_wrapper_words.intersection(tokens):
            return True
    return any(sig in low for sig in cfg.prod_substrings)


def evaluate(command: str, cfg: Config) -> str | None:
    """Return the ASK reason if the command is destructive-AND-prod, else None."""
    label = destructive_label(command, cfg)
    if not label:
        return None
    if not targets_prod(command, cfg):
        return None
    return f"⚠️  {cfg.message}\n\nMatched destructive operation: `{label}`."


def _ask(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }))


def main(stdin_text: str | None = None, env: dict | None = None) -> int:
    env = os.environ if env is None else env
    try:
        raw = sys.stdin.read() if stdin_text is None else stdin_text
    except Exception as e:
        log(f"stdin read failed: {e}")
        return 0
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except Exception as e:
        log(f"json parse failed: {e}; raw={raw[:160]!r}")
        return 0

    if payload.get("tool_name") != "Bash":
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not command:
        return 0

    try:
        cfg_path = find_config_path(env=env)
        if cfg_path is None:
            # Loud, not silent: a hook that guards nothing must SAY so, or it
            # reads as "reviewed and safe" when it is "never looked."
            log(f"NO CONFIG FOUND ({CONFIG_BASENAME} / ${CONFIG_ENV}) — guard is INERT")
            print(
                f"agent_db_guard: no {CONFIG_BASENAME} found and ${CONFIG_ENV} unset "
                f"— DB guard is INERT (allowing command).",
                file=sys.stderr,
            )
            return 0

        cfg = load_config(cfg_path)
        if not cfg.is_usable():
            log(f"CONFIG {cfg_path} declares no destructive+prod rules — guard INERT")
            print(
                f"agent_db_guard: {cfg_path} defines no destructive/prod rules "
                f"— DB guard is INERT.",
                file=sys.stderr,
            )
            return 0

        reason = evaluate(command, cfg)
        if reason is None:
            return 0
        _ask(reason)
        log(f"ASK :: {command[:200]}")
        return 0
    except Exception as e:
        # Fail open — never brick the agent's Bash — but log loudly.
        log(f"guard error (failing open): {e}; cmd={command[:160]!r}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
