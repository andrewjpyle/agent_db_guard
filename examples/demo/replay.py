#!/usr/bin/env python3
"""Replay recorded PreToolUse payloads through the real hook, one process per
payload, exactly as Claude Code would run it.

SAMPLE DATA: Acme Shop is fictional and prod-db.example.com is a reserved
example domain. No command is executed; each payload is only piped to the hook.

    cd examples/demo
    python3 replay.py session.jsonl      # an agent session: reads, then writes
    python3 replay.py limits.jsonl       # documented misses (see Scope in README)
    python3 replay.py malformed.txt      # payloads of the wrong shape

The hook runs with this directory as its working directory, so it finds
`.agent-db-guard.json` here by walking up, the same discovery a real project uses.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE.parent.parent / "agent_db_guard.py"


def describe(stdout: str) -> list[str]:
    if not stdout.strip():
        return ["no decision: normal permission flow"]
    out = json.loads(stdout)
    lines = []
    spec = out.get("hookSpecificOutput")
    if spec:
        lines.append(spec["permissionDecision"].upper())
        lines += spec["permissionDecisionReason"].splitlines()
    if "systemMessage" in out:
        lines.append(f"systemMessage: {out['systemMessage']}")
    return [line for line in lines if line.strip()]


def main(path: str) -> int:
    lines = [line for line in (HERE / path).read_text().splitlines() if line.strip()]
    for i, raw in enumerate(lines, 1):
        try:
            label = json.loads(raw)["tool_input"]["command"]
        except Exception:
            label = f"(malformed payload) {raw}"
        proc = subprocess.run(
            [sys.executable, str(HOOK)], input=raw, cwd=HERE,
            capture_output=True, text=True, timeout=30,
        )
        result = describe(proc.stdout)
        print(f"[{i:02d}] $ {label}")
        print(f"     exit {proc.returncode} | {result[0]}")
        for extra in result[1:]:
            print(f"       {extra}")
        if proc.returncode != 0:
            print(f"       stderr: {proc.stderr.strip().splitlines()[-1]}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
