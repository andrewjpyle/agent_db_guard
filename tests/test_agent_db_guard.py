"""Tests for agent_db_guard.

Stdlib unittest only — a DB guard that needed pytest to prove it works would be
one more dependency between you and the safety property. Run:

    python3 -m unittest discover -s tests -v

The load-bearing tests are the ones asserting what does NOT fire. A guard that
asks on everything is indistinguishable from a broken one after a week, because
the operator has learned to click through it. The false-positive suite is the
product.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_db_guard as g  # noqa: E402

# A Django-shaped config, inline, so the tests don't depend on examples/ paths.
CONFIG = {
    "destructive": {
        "subcommand_pattern": r"manage\.py\s+([a-zA-Z_][\w-]*)",
        "exact_subcommands": ["migrate", "flush", "loaddata", "dbshell"],
        "verbs": ["seed", "backfill", "truncate", "drop", "delete", "wipe", "purge"],
        "shell_subcommands": ["shell", "shell_plus"],
        "orm_write_markers": [".save(", ".delete(", ".create(", "bulk_create", "cursor.execute"],
        "sql_write_verbs": ["insert", "update", "delete", "drop", "truncate", "alter"],
    },
    "prod_signals": {
        "substrings": ["prod-db.example.com", "10.0.0.5", "prod-droplet"],
        "wrapper_words": ["secretsrunner"],
    },
    "message": "DESTRUCTIVE against PROD. Verify the host.",
}


def cfg() -> g.Config:
    return g.Config(CONFIG)


class DestructiveDetection(unittest.TestCase):
    def test_builtin_destructive_subcommands_are_flagged(self):
        c = cfg()
        for cmd in [
            "python manage.py migrate",
            "python manage.py flush",
            "./manage.py loaddata fixture.json",
            "manage.py dbshell",
        ]:
            self.assertTrue(g.destructive_label(cmd, c), cmd)

    def test_custom_verbs_flagged_as_whole_tokens(self):
        c = cfg()
        for cmd in [
            "manage.py seed_database",
            "manage.py truncate_events",
            "manage.py drop_stale_rows",
            "manage.py backfill_scores",
        ]:
            self.assertTrue(g.destructive_label(cmd, c), cmd)

    def test_shell_dash_c_with_orm_write_is_flagged(self):
        c = cfg()
        cmd = 'manage.py shell -c "User.objects.filter(x=1).delete()"'
        self.assertTrue(g.destructive_label(cmd, c))

    def test_psql_with_inline_write_verb_is_flagged(self):
        c = cfg()
        self.assertTrue(g.destructive_label('psql -c "TRUNCATE events;"', c))
        self.assertTrue(g.destructive_label("psql -f migration.sql", c))


class TheFalsePositiveSuite(unittest.TestCase):
    """These are the tests that matter. Every one is a real command that CONTAINS
    a destructive verb as a substring but is not destructive. If any of these
    starts asking, the guard is on its way to being ignored."""

    def test_substring_of_a_verb_does_not_fire(self):
        c = cfg()
        for benign in [
            "manage.py sync_raindrop",          # contains 'drop'
            "manage.py archive_to_dropbox",     # contains 'drop'
            "manage.py import_raindrops_csv",   # contains 'drop'
            "manage.py update_undeleted_flags", # contains 'delete'
            "manage.py reseed_cache_keys",      # 'reseed' != 'seed' token
        ]:
            self.assertEqual(g.destructive_label(benign, c), "", benign)

    def test_import_is_additive_and_not_flagged(self):
        c = cfg()
        # 'import' is deliberately not a verb: bulk import is a recoverable upsert.
        self.assertEqual(g.destructive_label("manage.py import_businesses", c), "")

    def test_routine_reads_and_writes_are_not_flagged(self):
        c = cfg()
        for cmd in [
            "manage.py runserver",
            "manage.py shell -c \"print(User.objects.count())\"",  # read, no write marker
            "manage.py sync_gsc_metrics",
            "psql -c \"SELECT count(*) FROM events;\"",             # read-only psql
        ]:
            self.assertEqual(g.destructive_label(cmd, c), "", cmd)


class ProdTargeting(unittest.TestCase):
    def test_literal_prod_substrings(self):
        c = cfg()
        self.assertTrue(g.targets_prod("psql -h prod-db.example.com ...", c))
        self.assertTrue(g.targets_prod("psql -h 10.0.0.5 ...", c))

    def test_wrapper_word_matched_as_whole_token(self):
        c = cfg()
        self.assertTrue(g.targets_prod("secretsrunner -- manage.py migrate", c))
        # ...but a word merely CONTAINING the wrapper token must not fire.
        self.assertFalse(g.targets_prod("mysecretsrunnerx -- manage.py migrate", c))

    def test_local_command_is_not_prod(self):
        c = cfg()
        self.assertFalse(g.targets_prod("psql -h localhost -c 'TRUNCATE x'", c))
        self.assertFalse(g.targets_prod("manage.py migrate", c))


class TheConjunction(unittest.TestCase):
    """destructive AND prod → ask. Anything less → silence."""

    def test_destructive_and_prod_asks(self):
        c = cfg()
        self.assertIsNotNone(g.evaluate("secretsrunner -- manage.py migrate", c))
        self.assertIsNotNone(g.evaluate("psql -h prod-db.example.com -c 'DROP TABLE x'", c))

    def test_destructive_but_local_is_silent(self):
        c = cfg()
        self.assertIsNone(g.evaluate("manage.py migrate", c))
        self.assertIsNone(g.evaluate("psql -h localhost -c 'TRUNCATE x'", c))

    def test_prod_but_read_only_is_silent(self):
        c = cfg()
        self.assertIsNone(g.evaluate("psql -h prod-db.example.com -c 'SELECT 1'", c))
        self.assertIsNone(g.evaluate("secretsrunner -- manage.py runserver", c))


class ConfigDiscovery(unittest.TestCase):
    def test_env_var_wins(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "custom.json"
            p.write_text(json.dumps(CONFIG))
            found = g.find_config_path(env={g.CONFIG_ENV: str(p)})
            self.assertEqual(found, p)

    def test_walks_up_from_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / g.CONFIG_BASENAME).write_text(json.dumps(CONFIG))
            deep = root / "a" / "b" / "c"
            deep.mkdir(parents=True)
            found = g.find_config_path(start=deep, env={})
            self.assertEqual(found.resolve(), (root / g.CONFIG_BASENAME).resolve())

    def test_missing_env_path_returns_none_not_crash(self):
        self.assertIsNone(g.find_config_path(env={g.CONFIG_ENV: "/no/such/file.json"}))


class UsabilityGuard(unittest.TestCase):
    """A config that can never fire is the silent-no-op trap; is_usable() is what
    lets main() warn loudly instead of guarding nothing in green silence."""

    def test_config_with_no_prod_signals_is_unusable(self):
        self.assertFalse(g.Config({"destructive": {"verbs": ["drop"]}}).is_usable())

    def test_config_with_no_destructive_rules_is_unusable(self):
        self.assertFalse(g.Config({"prod_signals": {"substrings": ["x"]}}).is_usable())

    def test_full_config_is_usable(self):
        self.assertTrue(cfg().is_usable())


class MainEndToEnd(unittest.TestCase):
    def _run(self, payload: dict, env: dict) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdout
        sys.stdout = out
        try:
            with redirect_stderr(err):
                rc = g.main(stdin_text=json.dumps(payload), env=env)
        finally:
            sys.stdout = old
        return rc, out.getvalue(), err.getvalue()

    def _env_with_config(self, td: str) -> dict:
        p = Path(td) / g.CONFIG_BASENAME
        p.write_text(json.dumps(CONFIG))
        return {g.CONFIG_ENV: str(p)}

    def test_asks_on_destructive_prod_command(self):
        with tempfile.TemporaryDirectory() as td:
            env = self._env_with_config(td)
            rc, out, _ = self._run(
                {"tool_name": "Bash",
                 "tool_input": {"command": "secretsrunner -- manage.py migrate"}},
                env,
            )
            self.assertEqual(rc, 0)
            decision = json.loads(out)["hookSpecificOutput"]["permissionDecision"]
            self.assertEqual(decision, "ask")

    def test_silent_on_local_destructive(self):
        with tempfile.TemporaryDirectory() as td:
            env = self._env_with_config(td)
            rc, out, _ = self._run(
                {"tool_name": "Bash", "tool_input": {"command": "manage.py migrate"}},
                env,
            )
            self.assertEqual(rc, 0)
            self.assertEqual(out, "")  # no decision emitted

    def test_non_bash_tool_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            env = self._env_with_config(td)
            rc, out, _ = self._run(
                {"tool_name": "Read", "tool_input": {"file_path": "/etc/passwd"}}, env
            )
            self.assertEqual(rc, 0)
            self.assertEqual(out, "")

    def test_missing_config_is_inert_but_LOUD(self):
        # The absence-of-work guard: no config must WARN to stderr, and never emit
        # an ask (fail-open), but must not silently look like it did its job.
        rc, out, err = self._run(
            {"tool_name": "Bash",
             "tool_input": {"command": "secretsrunner -- manage.py migrate"}},
            env={},  # no AGENT_DB_GUARD_CONFIG; cwd has no config in the test env
        )
        # If the test runner's cwd happens to contain a real config, skip rather
        # than assert a false failure.
        if out:
            self.skipTest("a real .agent-db-guard.json exists in cwd tree")
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")
        self.assertIn("INERT", err)

    def test_malformed_stdin_fails_open(self):
        rc, out, _ = self._run_raw("this is not json", env={})
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    def _run_raw(self, raw: str, env: dict) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdout
        sys.stdout = out
        try:
            with redirect_stderr(err):
                rc = g.main(stdin_text=raw, env=env)
        finally:
            sys.stdout = old
        return rc, out.getvalue(), err.getvalue()


if __name__ == "__main__":
    unittest.main()
