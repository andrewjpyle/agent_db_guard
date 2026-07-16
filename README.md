# agent_db_guard

**A Claude Code hook that stops an AI agent from running a destructive command against your production database by accident.**

Coding agents run shell commands. Sooner or later one runs a migration, a `TRUNCATE`, a `flush`, or a `loaddata` and points it at prod — because the command that's harmless against a local DB is a disaster against production, and nothing in the command's *shape* says which one it hit. "The AI dropped the prod database" is a real genre now.

This is a ~200-line, zero-dependency `PreToolUse` hook. It gates a command only when it is **both** destructive **and** production-targeted, and even then it *asks* — it never silently blocks.

```jsonc
// A destructive command against prod → you get asked first:
$ secretsrunner -- python manage.py migrate
  ⚠️  DESTRUCTIVE against the PRODUCTION database. Verify the target host
      before approving.
      Matched destructive operation: `migrate`.

// The same migration locally → nothing. No prompt, no friction:
$ python manage.py migrate
```

## Why it's not a keyword blocklist

Three decisions separate this from "grep the command for `drop`":

**It gates the conjunction, not the keyword.** A command must be destructive **and** carry a production signal. Blocking every destructive command — including the dozens of harmless local ones — trains you to click through the prompt, and *an alarm you always dismiss is worse than no alarm*: it launders the one real prod write as one more false positive. Routine prod reads pass silently. Local destructive commands pass silently. Only destructive-and-prod stops.

**It matches whole tokens, never substrings.** A substring match on `drop` fires on `sync_raindrop`, `archive_to_dropbox`, `import_raindrops` — all harmless, each one more prompt teaching you to ignore it. The subcommand name is split into word tokens and matched token by token. (This one lesson is why the false-positive test suite is the largest part of the package.)

**It asks; it does not deny.** A wall gets disabled the first time it's in your way. A gate gets read. An intentional, reviewed prod migration must stay possible — so the hook returns `"ask"`, a speed bump with a human at it, not a prohibition. In an unattended agent run, `"ask"` pauses for supervision, which is exactly what you want.

And it **fails open**: it sits in front of every Bash call, so any internal error logs and exits 0 rather than bricking your agent's shell. A missed gate is recoverable; a bricked agent is not.

## It's config-driven — the policy is yours

What counts as "destructive" and what counts as "prod" is *your* environment's answer. Both lists live in a JSON file:

```jsonc
{
  "destructive": {
    "exact_subcommands": ["migrate", "flush", "loaddata"],
    "verbs": ["seed", "truncate", "drop", "wipe", "backfill"],
    "orm_write_markers": [".delete(", ".save(", "bulk_create"],
    "sql_write_verbs": ["insert", "update", "delete", "drop", "truncate"]
  },
  "prod_signals": {
    "substrings": ["prod-db.example.com", "10.0.0.5"],
    "wrapper_words": ["secretsrunner"]
  }
}
```

- **`substrings`** — literal strings that mean prod when they appear in a command: your prod host, its IP, a droplet marker.
- **`wrapper_words`** — a command runner that *always* resolves to prod in your setup (e.g. a secrets injector whose only config points at production). This is the escape hatch for *"I can't see the resolved host, but this wrapper means prod."* Matched as a whole token.

`examples/` has a ready-to-edit Django config and a framework-agnostic one.

## Install

1. Drop `agent_db_guard.py` anywhere.
2. Copy an example to your project root as `.agent-db-guard.json` and **edit `prod_signals` to match your production database.**
3. Register it as a `PreToolUse` hook for Bash in your Claude Code settings:

```jsonc
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "python3 /abs/path/agent_db_guard.py" }
        ]
      }
    ]
  }
}
```

Config is found via `AGENT_DB_GUARD_CONFIG=/abs/path/.agent-db-guard.json`, or by walking up from the working directory to find `.agent-db-guard.json`.

Requires Python 3.9+. No pip, no dependencies.

## A guard that guards nothing says so

If no config is found, or the config declares no rules, the hook is **inert** — and it says so, loudly, on stderr:

```
agent_db_guard: no .agent-db-guard.json found and $AGENT_DB_GUARD_CONFIG unset
— DB guard is INERT (allowing command).
```

This is deliberate. The failure mode of a safety tool is to look installed while protecting nothing; a green, silent no-op reads as *"reviewed and safe"* when it means *"never looked."* An inert guard here is a visible warning, not an assumption.

## Tests

```bash
cd tests && python3 -m unittest discover -v
```

24 tests, stdlib `unittest`, no dependencies. The largest group asserts what does **not** fire — because that's the property that decides whether you'll still trust the prompt in a month.

## License

MIT
