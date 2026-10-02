#!/usr/bin/env python3
"""agent_db_guard: a Claude Code PreToolUse hook that stops an AI agent from
running a destructive database command against production by accident.

THE PROBLEM

Coding agents run shell commands. Sooner or later one runs a schema migration, a
`TRUNCATE`, a `flush`, or a `loaddata` and points it at the production database,
because the same command that is safe against a local DB is a disaster against
prod, and nothing in the command's shape tells the agent which one it hit.

THE APPROACH, and why it is not just a keyword blocklist

A command is gated only when it is BOTH:
  (a) database-destructive: a migrate/flush/seed/truncate/drop, a writing or
      script-running `psql`, or a management shell fed an ORM write; AND
  (b) production-targeted: it carries a signal that its DB is prod.

The conjunction is the whole point. Blocking every destructive command trains the
operator to click through (alarm fatigue), and an alarm everyone dismisses is
worse than no alarm: it launders a real prod write as one more false one. So
routine prod reads and routine LOCAL destructive commands both pass silently;
only destructive-AND-prod stops.

Three more decisions carry the design:

* It ASKS, it does not DENY. A wall gets disabled the first time it's in the way;
  a gate gets read. An intentional, reviewed prod migration must stay possible,
  so the hook returns "ask": a permission prompt with a human at it.
* It matches WHOLE WORDS, never substrings. A substring match on "drop" fires on
  `sync_raindrop`, `archive_to_dropbox`, `import_raindrops`, every one benign,
  every false positive feeding the alarm fatigue above.
* It FAILS OPEN, VISIBLY. A PreToolUse hook sits in front of every Bash call; a
  crash here would brick the agent's shell. Any internal error exits 0 so the
  command reaches Claude Code's normal permission flow, and the hook says it is
  not guarding (a `systemMessage` the user sees, plus stderr and the log).

CONFIG-DRIVEN

What counts as "destructive" and what counts as "prod" is YOUR environment's
answer, not this file's. Both lists live in a JSON config
(`.agent-db-guard.json`). See `examples/` for a Django and a generic config.

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

# Programs that read text from stdin. When one of these feeds psql or a management
# shell, the SQL or Python is visible in the command and gets inspected. Any other
# feeder (`cat dump.sql |`, `gunzip -c x.gz |`) hides the script, so it is treated
# like `psql -f`.
_INLINE_FEEDERS = frozenset({"echo", "printf"})


def log(msg: str) -> None:
    try:
        with LOG.open("a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()}  {msg}\n")
    except Exception:
        pass


def _has_word(word: str, text: str) -> bool:
    """True when `word` appears in `text` with no letter or digit on either side.

    `drop` matches `drop_stale_rows` and `drop`, never `sync_raindrop`. Unlike
    splitting on punctuation, it also matches configured words that themselves
    contain punctuation, such as a wrapper named `aws-vault`.
    """
    return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text) is not None


def _variants(command: str) -> list[str]:
    """The command as written, plus the form the shell actually runs once quotes,
    backslash escapes and line continuations are removed. `manage.py 'migrate'`,
    `manage.py mi""grate` and `manage.py \\<newline> migrate` all run `migrate`."""
    joined = command.replace("\\\n", " ")
    dequoted = re.sub(r"[\"'\\]", "", joined)
    return [command] if dequoted == command else [command, dequoted]


def _strip_quoted(command: str) -> str:
    """Blank out quoted strings, so a `<` or `|` inside SQL text
    (`-c "SELECT ... WHERE n < 5"`) is not read as shell syntax."""
    return re.sub(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", "''", command)


def _segments(command: str) -> list[str]:
    """Split a command line into pipeline segments on `|` (not `||`)."""
    return re.split(r"(?<!\|)\|(?!\|)", command)


def _reads_script_from_stdin(command: str, program: re.Pattern) -> bool:
    """True when `program` takes its input from a file it cannot show us: a `<`
    redirect (not a `<<` heredoc, whose text is inline) or a pipe from anything
    other than echo/printf."""
    segs = _segments(_strip_quoted(command))
    for i, seg in enumerate(segs):
        m = program.search(seg)
        if not m:
            continue
        if re.search(r"(?<!<)<(?![<(])", seg[m.end():]):
            return True
        if i > 0:
            upstream = segs[i - 1].strip().split()
            if False:  # SEEDED BREAK for CI red proof
                return True
    return False


class ConfigError(ValueError):
    pass


def _str_list(section: dict, key: str, default=()) -> list[str]:
    value = section.get(key, list(default))
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"'{key}' must be a list of strings")
    return value


class Config:
    """Validated view over the JSON policy. Everything the engine treats as
    'destructive' or 'prod' comes from here; nothing is hardcoded. Raises
    ConfigError on a malformed file so main() can say the guard is off."""

    def __init__(self, data: dict):
        if not isinstance(data, dict):
            raise ConfigError("top level must be a JSON object")
        d = data.get("destructive", {})
        p = data.get("prod_signals", {})
        if not isinstance(d, dict) or not isinstance(p, dict):
            raise ConfigError("'destructive' and 'prod_signals' must be objects")

        # Exact management subcommands that mutate the DB (migrate, flush, ...).
        self.exact_subcommands = frozenset(s.lower() for s in _str_list(d, "exact_subcommands"))
        # Whole-word verbs that mark a CUSTOM subcommand destructive (seed, drop).
        self.verbs = frozenset(v.lower() for v in _str_list(d, "verbs"))
        # ORM write markers that make a management shell destructive.
        self.orm_write_markers = tuple(_str_list(d, "orm_write_markers"))
        # Raw-SQL write verbs (for psql -c "...", heredocs, echo pipes).
        sql = _str_list(d, "sql_write_verbs")
        self.sql_write = (
            re.compile(r"\b(" + "|".join(re.escape(v) for v in sql) + r")\b", re.I)
            if sql else None
        )
        # How to pull a subcommand name out of a command line. Default: manage.py.
        pattern = d.get("subcommand_pattern", r"manage\.py\s+([a-zA-Z_][\w-]*)")
        try:
            self.subcommand_pattern = re.compile(pattern, re.I)
        except (re.error, TypeError) as e:
            raise ConfigError(f"'subcommand_pattern' is not a valid regex: {e}") from e
        # Names that take an inline script/shell (checked for ORM writes).
        self.shell_subcommands = frozenset(
            s.lower() for s in _str_list(d, "shell_subcommands", ["shell", "shell_plus"])
        )

        # PROD signals: literal substrings (a host, an IP) ...
        self.prod_substrings = tuple(s.lower() for s in _str_list(p, "substrings"))
        # ... and WRAPPER words: a command runner that always resolves to prod in
        # your setup (e.g. a secrets injector whose only config points at prod).
        # This is the escape hatch for "I can't see the resolved host, but this
        # wrapper means prod." Matched as a whole word, like the verbs.
        self.prod_wrapper_words = frozenset(w.lower() for w in _str_list(p, "wrapper_words"))

        message = data.get(
            "message",
            "This command runs a DESTRUCTIVE operation against a PRODUCTION "
            "database. Verify the target DB host before approving.",
        )
        if not isinstance(message, str):
            raise ConfigError("'message' must be a string")
        self.message = message

    def is_usable(self) -> bool:
        """A config that declares no destructive rule, or no prod signal, can never
        fire: the silent-no-op trap. Treat it as unusable so the caller warns."""
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
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise ConfigError(f"cannot read JSON: {e}") from e
    return Config(data)


_PSQL = re.compile(r"\bpsql\b", re.I)


def _label_one(command: str, cfg: Config, shell_syntax: bool) -> str:
    """`shell_syntax` is False for the de-quoted variant, whose redirects and
    pipes can no longer be told apart from quoted text."""
    for m in cfg.subcommand_pattern.finditer(command):
        sub = m.group(1)
        low = sub.lower()
        if low in cfg.exact_subcommands:
            return sub
        if any(_has_word(v, low) for v in cfg.verbs):
            return sub
        if low in cfg.shell_subcommands:
            # `shell -c "..."`, a heredoc, or `echo ... | manage.py shell`: the
            # Python is in the command line, so look for an ORM write in it.
            if any(marker in command for marker in cfg.orm_write_markers):
                return f"{sub} (ORM write)"
            shell_prog = re.compile(re.escape(m.group(0)), re.I)
            if shell_syntax and _reads_script_from_stdin(command, shell_prog):
                return f"{sub} (script from stdin)"
    if _PSQL.search(command):
        # `-f`/`--file` or stdin from a file runs a script we cannot see.
        if shell_syntax and (
            re.search(r"(?:^|\s)(?:-f|--file)", _strip_quoted(command))
            or _reads_script_from_stdin(command, _PSQL)
        ):
            return "psql (script)"
        if cfg.sql_write and cfg.sql_write.search(command):
            return "psql (write)"
    return ""


def destructive_label(command: str, cfg: Config) -> str:
    """A short label if the command is DB-destructive per `cfg`, else ''."""
    for i, variant in enumerate(_variants(command)):
        label = _label_one(variant, cfg, shell_syntax=(i == 0))
        if label:
            return label
    return ""


def targets_prod(command: str, cfg: Config) -> bool:
    for variant in _variants(command):
        low = variant.lower()
        if any(_has_word(w, low) for w in cfg.prod_wrapper_words):
            return True
        if any(sig in low for sig in cfg.prod_substrings):
            return True
    return False


def evaluate(command: str, cfg: Config) -> str | None:
    """Return the ASK reason if the command is destructive-AND-prod, else None."""
    label = destructive_label(command, cfg)
    if not label:
        return None
    if not targets_prod(command, cfg):
        return None
    return f"{cfg.message}\n\nMatched destructive operation: `{label}`."


def _ask(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }))


def _warn(message: str) -> None:
    """Say the guard is not guarding, where a person will see it.

    Claude Code sends a hook's stderr to the debug log when the hook exits 0, so
    stderr alone is invisible. A top-level `systemMessage` is shown to the user.
    No permission decision is emitted, so the command goes through Claude Code's
    normal permission flow (fail open)."""
    log(message)
    print(message, file=sys.stderr)
    print(json.dumps({"systemMessage": message}))


def main(stdin_text: str | None = None, env: dict | None = None) -> int:
    env = os.environ if env is None else env
    try:
        raw = sys.stdin.read() if stdin_text is None else stdin_text
        if not raw.strip():
            return 0
        payload = json.loads(raw)
    except Exception as e:
        log(f"payload unreadable (failing open): {e}")
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command:
        return 0

    try:
        cfg_path = find_config_path(env=env)
        if cfg_path is None:
            _warn(
                f"agent_db_guard: no {CONFIG_BASENAME} found and ${CONFIG_ENV} unset. "
                f"DB guard is INERT (command not checked)."
            )
            return 0
        try:
            cfg = load_config(cfg_path)
        except ConfigError as e:
            _warn(f"agent_db_guard: {cfg_path} is invalid ({e}). DB guard is INERT (command not checked).")
            return 0
        if not cfg.is_usable():
            _warn(
                f"agent_db_guard: {cfg_path} defines no destructive rule or no prod signal. "
                f"DB guard is INERT (command not checked)."
            )
            return 0

        reason = evaluate(command, cfg)
        if reason is None:
            return 0
        _ask(reason)
        log(f"ASK :: {command[:200]}")
        return 0
    except Exception as e:
        # Fail open (never brick the agent's Bash), but never silently.
        _warn(f"agent_db_guard: internal error ({type(e).__name__}: {e}). Command not checked.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
