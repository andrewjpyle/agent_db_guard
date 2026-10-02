"""Tests for agent_db_guard.

Stdlib unittest only. A DB guard that needed pytest to prove it works would be
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
        "wrapper_words": ["secretsrunner", "aws-vault"],
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
        # The absence-of-work guard: no config must never emit a decision
        # (fail open), and must say so where the user sees it. Claude Code only
        # writes a hook's stderr to the debug log on exit 0, so the warning has to
        # be a top-level systemMessage on stdout.
        with tempfile.TemporaryDirectory() as td:
            env = {g.CONFIG_ENV: str(Path(td) / "missing.json")}
            rc, out, err = self._run(
                {"tool_name": "Bash",
                 "tool_input": {"command": "secretsrunner -- manage.py migrate"}},
                env,
            )
        self.assertEqual(rc, 0)
        msg = json.loads(out)
        self.assertNotIn("hookSpecificOutput", msg)
        self.assertIn("INERT", msg["systemMessage"])
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


class VisibleWhenNotGuarding(unittest.TestCase):
    """Every path where the guard does not check a command must be visible to the
    user (systemMessage), never only a log line."""

    def _run_with_config_text(self, text: str) -> tuple[int, dict]:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / g.CONFIG_BASENAME
            p.write_text(text)
            out, err = io.StringIO(), io.StringIO()
            old = sys.stdout
            sys.stdout = out
            try:
                with redirect_stderr(err):
                    rc = g.main(
                        stdin_text=json.dumps({"tool_name": "Bash", "tool_input": {
                            "command": "secretsrunner -- manage.py migrate"}}),
                        env={g.CONFIG_ENV: str(p)},
                    )
            finally:
                sys.stdout = old
        return rc, json.loads(out.getvalue())

    def assertInert(self, text: str, needle: str):
        rc, msg = self._run_with_config_text(text)
        self.assertEqual(rc, 0)
        self.assertNotIn("hookSpecificOutput", msg)
        self.assertIn(needle, msg["systemMessage"])
        self.assertIn("INERT", msg["systemMessage"])

    def test_unparseable_config_warns(self):
        self.assertInert("{not json", "invalid")

    def test_wrong_shape_config_warns(self):
        self.assertInert(json.dumps({"destructive": ["migrate"]}), "invalid")
        self.assertInert(json.dumps({"destructive": {"verbs": "drop"},
                                     "prod_signals": {"substrings": ["x"]}}), "invalid")

    def test_bad_regex_config_warns(self):
        bad = json.loads(json.dumps(CONFIG))
        bad["destructive"]["subcommand_pattern"] = "manage.py (["
        self.assertInert(json.dumps(bad), "invalid")

    def test_config_without_rules_warns(self):
        self.assertInert(json.dumps({"prod_signals": {"substrings": ["x"]}}), "no destructive rule")

    def test_internal_error_warns_and_fails_open(self):
        original = g.evaluate
        g.evaluate = lambda command, cfg: 1 / 0
        try:
            rc, msg = self._run_with_config_text(json.dumps(CONFIG))
        finally:
            g.evaluate = original
        self.assertEqual(rc, 0)
        self.assertNotIn("hookSpecificOutput", msg)
        self.assertIn("internal error", msg["systemMessage"])


class MalformedPayloads(unittest.TestCase):
    """A payload of the wrong shape must exit 0 with no decision, never a
    traceback and exit 1."""

    def test_wrong_shapes_fail_open_without_crashing(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / g.CONFIG_BASENAME
            p.write_text(json.dumps(CONFIG))
            env = {g.CONFIG_ENV: str(p)}
            for raw in [
                '["not", "an", "object"]',
                '"just a string"',
                "42",
                "null",
                '{"tool_name": "Bash", "tool_input": "psql -c drop"}',
                '{"tool_name": "Bash", "tool_input": {"command": ["psql", "-c", "drop"]}}',
                '{"tool_name": "Bash", "tool_input": null}',
                "",
            ]:
                out = io.StringIO()
                old = sys.stdout
                sys.stdout = out
                try:
                    rc = g.main(stdin_text=raw, env=env)
                finally:
                    sys.stdout = old
                self.assertEqual(rc, 0, raw)
                self.assertEqual(out.getvalue(), "", raw)


class ObviousBypassesAreCaught(unittest.TestCase):
    """Forms a reviewer tries first. Each one ran destructive SQL or a migration
    against prod without a prompt before the fix."""

    def test_script_piped_or_redirected_into_psql(self):
        c = cfg()
        for cmd in [
            "cat cleanup.sql | psql -h prod-db.example.com shop",
            "gunzip -c dump.sql.gz | psql -h prod-db.example.com shop",
            "psql -h prod-db.example.com shop < cleanup.sql",
            "psql -h prod-db.example.com --file=cleanup.sql",
            "psql -h prod-db.example.com -fcleanup.sql",
        ]:
            self.assertEqual(g.destructive_label(cmd, c), "psql (script)", cmd)
            self.assertIsNotNone(g.evaluate(cmd, c), cmd)

    def test_orm_write_piped_into_management_shell(self):
        c = cfg()
        for cmd in [
            "echo 'Order.objects.all().delete()' | secretsrunner -- python manage.py shell",
            "secretsrunner -- python manage.py shell <<EOF\nOrder.objects.all().delete()\nEOF",
        ]:
            self.assertEqual(g.destructive_label(cmd, c), "shell (ORM write)", cmd)
            self.assertIsNotNone(g.evaluate(cmd, c), cmd)

    def test_script_file_into_management_shell(self):
        c = cfg()
        cmd = "secretsrunner -- python manage.py shell < cleanup.py"
        self.assertEqual(g.destructive_label(cmd, c), "shell (script from stdin)")

    def test_quoting_and_escapes_around_the_subcommand(self):
        c = cfg()
        for cmd in [
            "secretsrunner -- python manage.py 'migrate'",
            'secretsrunner -- python manage.py "migrate"',
            'secretsrunner -- python manage.py mi""grate',
            "secretsrunner -- python manage.py mi\\grate",
            "secretsrunner -- python manage.py \\\nmigrate",
        ]:
            self.assertIsNotNone(g.evaluate(cmd, c), cmd)

    def test_case_of_the_client_name(self):
        c = cfg()
        self.assertIsNotNone(g.evaluate('PSQL -h prod-db.example.com -c "DROP TABLE x"', c))
        self.assertIsNotNone(g.evaluate("secretsrunner -- python MANAGE.PY migrate", c))

    def test_sql_verb_case_and_multi_statement(self):
        c = cfg()
        for sql in ["DrOp TABLE x", "SELECT 1; DELETE FROM orders", "select 1;truncate orders"]:
            self.assertIsNotNone(g.evaluate(f'psql -h prod-db.example.com -c "{sql}"', c), sql)

    def test_wrapper_word_with_punctuation(self):
        c = cfg()
        self.assertTrue(g.targets_prod("aws-vault exec prod -- manage.py migrate", c))
        self.assertFalse(g.targets_prod("my-aws-vaultx exec -- manage.py migrate", c))

    def test_prod_host_quoted_or_uppercased(self):
        c = cfg()
        self.assertTrue(g.targets_prod('psql -h "PROD-DB.EXAMPLE.COM" -c "drop table x"', c))


class FixesAddNoFalsePositives(unittest.TestCase):
    """The stdin and quoting fixes must not start asking on prod reads."""

    def test_prod_reads_stay_silent(self):
        c = cfg()
        for cmd in [
            'psql -h prod-db.example.com -c "SELECT * FROM t WHERE n < 5"',
            'psql -h prod-db.example.com -c "SELECT a || b FROM t"',
            "echo 'SELECT count(*) FROM orders' | psql -h prod-db.example.com shop",
            "psql -h prod-db.example.com -c 'SELECT 1' > out.csv",
            "psql -h prod-db.example.com -c 'SELECT 1' | grep 1",
            "psql -h prod-db.example.com <<EOF\nSELECT 1;\nEOF",
            "echo 'print(Order.objects.count())' | secretsrunner -- python manage.py shell",
        ]:
            self.assertIsNone(g.evaluate(cmd, c), cmd)


class ShippedExamplesWork(unittest.TestCase):
    """The configs in examples/ are what people copy. They must load, be usable,
    and fire on the case they were written for."""

    ROOT = Path(__file__).resolve().parent.parent / "examples"

    def _load(self, rel: str) -> g.Config:
        c = g.load_config(self.ROOT / rel)
        self.assertTrue(c.is_usable(), rel)
        return c

    def test_django_example(self):
        c = self._load("django.agent-db-guard.json")
        self.assertIsNotNone(g.evaluate("DB_HOST=prod-db.example.com python manage.py migrate", c))
        self.assertIsNotNone(g.evaluate("DB_HOST=prod-db.example.com django-admin flush", c))
        self.assertIsNone(g.evaluate("DB_HOST=prod-db.example.com python manage.py sync_raindrop", c))

    def test_generic_example(self):
        c = self._load("generic.agent-db-guard.json")
        self.assertIsNotNone(g.evaluate('psql -h prod.db.internal -c "DROP TABLE x"', c))
        self.assertIsNotNone(g.evaluate("mycli wipe_cache --host prod.db.internal", c))
        self.assertIsNone(g.evaluate('psql -h prod.db.internal -c "SELECT 1"', c))

    def test_demo_config(self):
        c = self._load("demo/.agent-db-guard.json")
        self.assertIsNotNone(g.evaluate("secretsrunner -- python manage.py migrate", c))


if __name__ == "__main__":
    unittest.main()
