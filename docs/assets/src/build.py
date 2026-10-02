"""Build the README graphics for agent_db_guard.

Every command, exit code, decision and message shown comes from a committed
capture in ./captures (written by a real run of the hook; see each capture's
"command" and "commit" fields). Margin notes are commentary, not data.

    python docs/assets/src/build.py
    uv run --with playwright==1.56.0 --with pillow python docs/assets/src/render.py docs/assets/src docs/assets
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import readme_kit as k  # noqa: E402

CAP = HERE / "captures"
REPO = "AGENT-DB-GUARD"


def replay_events(name: str) -> tuple[list[dict], str]:
    """Parse replay.py output into events: command, exit, outcome, detail lines."""
    cap = k.load_capture(CAP / f"{name}.json")
    events, cur = [], None
    for line in cap["output"].splitlines():
        m = re.match(r"\[(\d+)\] \$ (.*)", line)
        if m:
            cur = {"n": int(m.group(1)), "cmd": m.group(2), "detail": []}
            events.append(cur)
            continue
        m = re.match(r"\s+exit (\d+) \| (.*)", line)
        if m and cur is not None:
            cur["exit"], cur["outcome"] = int(m.group(1)), m.group(2)
            continue
        if cur is not None and line.strip():
            cur["detail"].append(line.strip())
    if not events or any("exit" not in e for e in events):
        raise SystemExit(f"capture {name} did not parse into complete events")
    # Footer dates are the operator's local (US Central) calendar date.
    local = datetime.fromisoformat(cap["captured_at"]).astimezone(ZoneInfo("America/Chicago"))
    return events, local.date().isoformat()


def matched(e: dict) -> str:
    for d in e["detail"]:
        if d.startswith("Matched destructive operation:"):
            return d
    return ""


def is_ask(e: dict) -> bool:
    return e["outcome"] == "ASK"


session, run_date = replay_events("session")
limits, _ = replay_events("limits")
malformed, _ = replay_events("malformed")
ask_raw = k.load_capture(CAP / "hook_ask_raw.json")
inert_raw = k.load_capture(CAP / "hook_inert_raw.json")
FOOT = f"{REPO} · REAL RUN {run_date}"
n_ask = sum(is_ask(e) for e in session)
n_quiet = len(session) - n_ask


def outcome_html(e: dict) -> str:
    if is_ask(e):
        return (f"<span style='color:var(--amber);font-weight:600'>exit {e['exit']} · ASK</span>"
                f"<span style='color:var(--muted)'> · {k.esc(matched(e).replace('Matched destructive operation: ', 'matched '))}</span>")
    return f"<span style='color:var(--good)'>exit {e['exit']} · {k.esc(e['outcome'])}</span>"


# ── hero ───────────────────────────────────────────────────────────────────────────────────
by_n = {e["n"]: e for e in session}
hero_cards = ""
for n, tag in [(1, "PROD READ"), (3, "LOCAL MIGRATE"), (5, "PROD MIGRATE")]:
    e = by_n[n]
    border = "var(--amber)" if is_ask(e) else "var(--line)"
    hero_cards += (
        f"<div class='card' style='padding:16px 20px;margin-bottom:16px;border-color:{border};width:560px'>"
        f"<div class='k' style='font-size:11px;color:{'var(--amber)' if is_ask(e) else 'var(--dim)'}'>{tag}</div>"
        f"<div class='mono' style='font-size:13px;margin-top:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>$ {k.esc(e['cmd'])}</div>"
        f"<div class='mono' style='font-size:13px;margin-top:8px'>{outcome_html(e)}</div></div>")
hero_right = (f"<div><div class='k' style='font-size:11px;color:var(--dim);margin-bottom:14px'>ONE AGENT SESSION, THREE CALLS</div>"
              f"{hero_cards}</div>")
hero = k.hero(
    kicker="CLAUDE CODE PRETOOLUSE HOOK",
    title="Reads go through.",
    accent="Prod writes get a human.",
    lede_html="A config-driven hook that asks before an AI agent runs a destructive command against your production database.",
    rules=[
        ("Destructive AND prod, or nothing", "A local migrate or a prod SELECT never prompts."),
        ("Whole words, not substrings", "drop fires on drop_rows, not on sync_raindrop."),
        ("Ask, never deny; fail open, visibly", "A broken guard says so instead of looking installed."),
    ],
    pill="ONE FILE · STDLIB · PYTHON 3.9+",
    right_html=hero_right,
    footer_left=FOOT,
)

# ── anatomy: the whole session ───────────────────────────────────────────────────────────
NOTES = {
    1: "Prod read. The prod host is there, but nothing destructive: silent.",
    2: "Read-only subcommand through the prod wrapper: silent.",
    3: "Destructive, but local: silent. No prompt to learn to ignore.",
    4: "archive_to_dropbox contains drop as a substring, not a word: silent.",
    5: "Wrapper word means prod, migrate is destructive: ask.",
    6: "A write hidden after a read in one -c string.",
    7: "The SQL is in a file the hook cannot see, so it asks.",
    8: "ORM delete piped into the Django shell.",
    9: "Quoting the subcommand does not hide it.",
}
rows = ""
for e in session:
    accent = is_ask(e)
    rows += (
        f"<div style='display:flex;align-items:center;height:64px;border-top:1px solid var(--line)'>"
        f"<div style='width:3px;height:44px;background:{'var(--amber)' if accent else 'transparent'};margin-right:16px;flex:none'></div>"
        f"<div class='mono' style='width:26px;font-size:12px;color:var(--dim);flex:none'>{e['n']:02d}</div>"
        f"<div style='width:820px;flex:none;padding-right:24px'>"
        f"<div class='mono' style='font-size:13.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>$ {k.esc(e['cmd'])}</div>"
        f"<div class='mono' style='font-size:12.5px;margin-top:7px'>{outcome_html(e)}</div></div>"
        f"<div style='font-size:15px;line-height:1.35;color:{'var(--ivory)' if accent else 'var(--muted)'}'>{k.esc(NOTES[e['n']])}</div>"
        f"</div>")
reason_first = json.loads(ask_raw["output"])["hookSpecificOutput"]["permissionDecisionReason"].splitlines()[0]
anatomy_body = (
    f"<div style='position:absolute;left:56px;top:38px'><div class='k'>ANATOMY OF A SESSION · {len(session)} BASH CALLS THROUGH THE REAL HOOK</div>"
    f"<div class='serif' style='font-size:30px;margin-top:10px'>{n_quiet} pass silently. {k.em(f'{n_ask} stop for a human.')}</div></div>"
    f"<div style='position:absolute;right:56px;top:46px;width:380px;font-size:13px;color:var(--muted);line-height:1.45;text-align:right'>"
    f"Every ask carries the reason:<br><span class='mono' style='color:var(--ivory);font-size:12px'>{k.esc(reason_first)}</span></div>"
    f"<div style='position:absolute;left:56px;right:56px;top:126px;border-bottom:1px solid var(--line)'>{rows}</div>"
)
anatomy = k.page(anatomy_body, "anatomy", FOOT)

# ── how it works ─────────────────────────────────────────────────────────────────────────
boxes = (
    k.box(56, 262, 230, 150, "PreToolUse payload", ["stdin JSON from", "Claude Code", "tool_name: Bash", "tool_input.command"])
    + k.box(346, 262, 230, 150, "Config", ["$AGENT_DB_GUARD_CONFIG", "or walk up from cwd", ".agent-db-guard.json"])
    + k.box(636, 262, 230, 150, "Destructive?", ["subcommand: exact / verb", "shell: ORM write marker", "psql: write verb, -f,", "pipe or < redirect"])
    + k.box(926, 262, 200, 150, "Prod?", ["host / IP substring", "wrapper word", "(whole word)"])
    + k.box(1186, 262, 158, 150, "Ask", ["permission", "Decision:", "\"ask\"", "+ reason"], accent=True)
    + k.box(56, 530, 230, 130, "Not Bash / malformed", ["exit 0, no output", "normal permission", "flow"])
    + k.box(346, 530, 230, 130, "Missing / invalid", ["exit 0 +", "systemMessage:", "\"guard is INERT\""], accent=True)
    + k.box(636, 530, 490, 130, "No / no", ["exit 0, no decision: Claude Code's normal permission",
                                            "flow decides. Silence never auto-approves."])
)
arrow_specs = [
    (286, 337, 346, 337), (576, 337, 636, 337), (866, 337, 926, 337, "yes"), (1126, 337, 1186, 337, "yes"),
    (171, 412, 171, 530, "bad shape", True, "right"), (461, 412, 461, 530, "none", True, "right"),
    (751, 412, 751, 530, "no", True, "right"), (1026, 412, 1026, 530, "no", True, "right"),
]
how = k.flow(
    kicker="HOW IT WORKS",
    title_html=f"Two questions, one {k.em('conjunction')}",
    subline="Pure string analysis in one stdlib file. It never connects to a database and never runs the command.",
    boxes_html=boxes,
    arrow_specs=arrow_specs,
    footer_left=f"{REPO} · HOW IT WORKS",
)

# ── when it cannot guard ─────────────────────────────────────────────────────────────────
inert_msg = json.loads(inert_raw["output"])["systemMessage"]
cards = []
for e in malformed:
    cmd = e["cmd"].replace("(malformed payload) ", "")
    cards.append(("MALFORMED PAYLOAD", cmd, f"exit {e['exit']} · {e['outcome']}", "fails open, no traceback"))
cards.append(("NO CONFIG", inert_msg, "exit 0 · systemMessage", "shown to the user, not just logged"))
for e in limits:
    cards.append(("KNOWN MISS", e["cmd"], f"exit {e['exit']} · {e['outcome']}", "out of scope: see README"))
fail = k.catalog(
    kicker="WHEN IT CANNOT GUARD",
    title_html=f"Where it stops, {k.em('in the open')}",
    sub_html="Real runs. Wrong-shape payloads fail open with no traceback, a missing config is announced, and the documented misses pass unchecked.",
    cards=cards,
    footer_left=FOOT,
    cols=4,
    card_height=200,
)

k.write_pages(HERE, {"hero": hero, "anatomy": anatomy, "architecture": how, "limits": fail})
