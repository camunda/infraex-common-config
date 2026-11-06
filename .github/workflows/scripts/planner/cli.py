"""Command line: python3 -m planner <decide|record|gate|collect|learn|audit>."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import SCHEMA, __version__


def _decide(a) -> int:
    from . import decide, render
    ev = decide.run(a)
    with open(a.out, "w") as fh:
        json.dump(ev, fh, indent=1, sort_keys=True)
    md = render.summary_md(ev)
    if a.summary_file:
        with open(a.summary_file, "a") as fh:
            fh.write(md)
    print(md)
    return 0


def _learn(a) -> int:
    import hashlib
    from datetime import datetime
    from . import gitutil, learn, schema, store
    from .gh import Gh
    from .util import event_id, now_iso, parse_iso
    cfg_text = ""
    if a.git_dir:
        cfg_text = gitutil.show(a.git_dir, "HEAD", a.config_path)
    from . import config
    cfg = config.load(cfg_text)
    ds = store.DirStore(a.store_dir)
    events = ds.events()
    now = a.now or now_iso()
    weights = [e for e in events if e["type"] == "weights"]
    prev = max(weights, key=lambda e: (e["ts"], e["id"])) if weights else None
    w = learn.compute(events, cfg, now, prev, a.repo)
    schema.require_valid(w)
    print(w["diff"])
    if a.summary_file:
        with open(a.summary_file, "a") as fh:
            fh.write("## Planner learner\n\n" + w["diff"] + "\n")
    if a.dry_run:
        return 0
    st = store.GhStore(Gh(), a.repo, a.branch)
    st.put(store.path_for(w), w, "chore(ci-decisions): weights %s" % w["id"])
    # weekly checkpoint: hash over the week's event files, so later edits to history are detectable
    d = parse_iso(now)
    year, week, _ = d.isocalendar()
    label = "%04d-W%02d" % (year, week)
    h, n = hashlib.sha256(), 0
    for rel in ds.walk():
        if rel.startswith("events/"):
            y, m, dd = (int(x) for x in rel.split("/")[1:4])
            if datetime(y, m, dd).isocalendar()[:2] == (year, week):
                with open(os.path.join(a.store_dir, rel), "rb") as fh:
                    h.update(rel.encode() + b"\0" + hashlib.sha256(fh.read()).digest())
                n += 1
    head = gitutil.run(a.store_dir, "rev-parse", "HEAD", check=False).strip() if os.path.isdir(os.path.join(a.store_dir, ".git")) else ""
    cp = {"schema": SCHEMA, "type": "checkpoint", "ts": now, "repo": a.repo, "week": label, "files": n,
          "sha256": h.hexdigest(), "head_commit": head if len(head) == 40 else "0" * 40}
    cp["id"] = event_id("chk", {"week": label, "sha": cp["sha256"], "files": n})
    schema.require_valid(cp)
    st.put(store.path_for(cp), cp, "chore(ci-decisions): checkpoint %s" % label)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="planner", description="Test-selection planner v%s" % __version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("decide", help="compute the decision for a pull request (read-only)")
    d.add_argument("--repo", required=True, help="path of the checkout (PR head)")
    d.add_argument("--slug", required=True, help="owner/name")
    d.add_argument("--pr", required=True)
    d.add_argument("--head-sha", required=True)
    d.add_argument("--base-sha", required=True)
    d.add_argument("--event", default="pull_request")
    d.add_argument("--actor", default="")
    d.add_argument("--fork", default="false")
    d.add_argument("--labels", default="[]", help="JSON list of label names")
    d.add_argument("--mode", default="gate")
    d.add_argument("--base-branch", default="main")
    d.add_argument("--default-branch", default="main")
    d.add_argument("--gate-base-branches", default="main")
    d.add_argument("--gate-suites", default="", help="comma separated override; 'none' disables gating")
    d.add_argument("--weights-pin", default="")
    d.add_argument("--config-path", default=".github/planner-config.json")
    d.add_argument("--store-dir", default="", help="checked-out ci-decisions branch (memory)")
    d.add_argument("--runs-json", default="", help="Actions runs fallback for memory")
    d.add_argument("--planner-version", default="")
    d.add_argument("--now", default="")
    d.add_argument("--out", default="decision.json")
    d.add_argument("--summary-file", default="")
    d.set_defaults(fn=_decide)

    r = sub.add_parser("record", help="validate and store a decision (privileged job)")
    r.add_argument("--artifact", required=True)
    r.add_argument("--repo", required=True)
    r.add_argument("--branch", default="ci-decisions")
    r.add_argument("--pr", required=True)
    r.add_argument("--head-sha", required=True)
    r.add_argument("--summary-file", default="")
    r.add_argument("--no-comment", action="store_true")
    r.add_argument("--no-status", action="store_true")
    r.set_defaults(fn=lambda a: __import__("planner.record", fromlist=["run"]).run(a))

    g = sub.add_parser("gate", help="a suite's triage step: output should_skip")
    g.add_argument("--repo", required=True)
    g.add_argument("--branch", default="ci-decisions")
    g.add_argument("--event", default="")
    g.add_argument("--fork", default="false")
    g.add_argument("--pr", default="")
    g.add_argument("--head-sha", default="")
    g.add_argument("--suite", required=True)
    g.add_argument("--timeout", default="240")
    g.add_argument("--interval", default="10")
    g.set_defaults(fn=lambda a: __import__("planner.gate", fromlist=["run"]).run(a))

    ru = sub.add_parser("runs", help="recent successful suite runs from the Actions API (memory fallback)")
    ru.add_argument("--repo", required=True)
    ru.add_argument("--git-dir", required=True)
    ru.add_argument("--base-sha", required=True)
    ru.add_argument("--default-branch", default="main")
    ru.add_argument("--config-path", default=".github/planner-config.json")
    ru.add_argument("--per-page", default="20")
    ru.add_argument("--out", default="runs.json")
    ru.set_defaults(fn=lambda a: __import__("planner.runs", fromlist=["run"]).run(a))

    c = sub.add_parser("collect", help="outcomes of finished suite runs, escape attribution")
    c.add_argument("--repo", required=True)
    c.add_argument("--git-dir", default=".")
    c.add_argument("--store-dir", required=True)
    c.add_argument("--branch", default="ci-decisions")
    c.add_argument("--config-path", default=".github/planner-config.json")
    c.add_argument("--backfill-days", default="30")
    c.add_argument("--max-events", default="400")
    c.add_argument("--now", default="")
    c.set_defaults(fn=lambda a: __import__("planner.collect", fromlist=["run"]).run(a))

    l = sub.add_parser("learn", help="weekly weights and checkpoint")
    l.add_argument("--repo", required=True)
    l.add_argument("--git-dir", default="")
    l.add_argument("--store-dir", required=True)
    l.add_argument("--branch", default="ci-decisions")
    l.add_argument("--config-path", default=".github/planner-config.json")
    l.add_argument("--summary-file", default="")
    l.add_argument("--dry-run", action="store_true")
    l.add_argument("--now", default="")
    l.set_defaults(fn=_learn)

    a = sub.add_parser("audit", help="query or verify the store")
    a.add_argument("what", nargs="?", default="query", choices=["query", "verify"])
    a.add_argument("--store-dir", required=True)
    a.add_argument("--pr")
    a.add_argument("--module")
    a.add_argument("--suite")
    a.add_argument("--since")
    a.add_argument("--type", default="")
    a.add_argument("--format", default="text", choices=["text", "json"])
    a.set_defaults(fn=lambda a: __import__("planner.audit", fromlist=["run"]).run(a))
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args) or 0)
