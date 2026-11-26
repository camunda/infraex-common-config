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


def _facts(a) -> int:
    from . import decide
    f = decide.facts_run(a)
    with open(a.out, "w") as fh:
        json.dump(f, fh, indent=1, sort_keys=True)
    note = "facts: %d changed files, %d signals, %d suites" % (f["changed_files"]["count"], len(f["signals"]), len(f["suites"]))
    print(note)
    if a.summary_file:
        with open(a.summary_file, "a") as fh:
            fh.write("## Planner facts\n\n%s\n" % note)
    return 0


def _camunda_plan(a) -> int:
    from . import camunda, checkrun
    with open(a.facts) as fh:
        facts = json.load(fh)
    wait = camunda.await_seconds(a.await_seconds)
    try:
        asked = float(a.await_seconds)
    except ValueError:
        asked = None
    if asked != wait:
        print("::warning::await-seconds %r is not a number from 1 to 60, using %g" % (str(a.await_seconds)[:20], wait))
    plan = camunda.decide(facts, None, str(a.advisor).lower() == "true", a.run_ref, wait)
    with open(a.out, "w") as fh:
        json.dump(plan, fh, indent=1, sort_keys=True)
    print("plan: source=%s reason=%s decision=%s" % (plan["source"], plan["reason"], plan["decision_id"]))
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

    f = sub.add_parser("facts", help="facts about a pull request for the Camunda process (read-only)")
    for arg, kw in (("--repo", dict(required=True)), ("--slug", dict(required=True)), ("--pr", dict(required=True)),
                    ("--head-sha", dict(required=True)), ("--base-sha", dict(required=True)), ("--event", dict(default="pull_request")),
                    ("--actor", dict(default="")), ("--fork", dict(default="false")), ("--labels", dict(default="[]")), ("--mode", dict(default="")),
                    ("--base-branch", dict(default="main")), ("--default-branch", dict(default="main")),
                    ("--gate-base-branches", dict(default="main")), ("--gate-suites", dict(default="")),
                    ("--config-path", dict(default=".github/planner-config.json")), ("--planner-version", dict(default="")),
                    ("--out", dict(default="facts.json")), ("--summary-file", dict(default=""))):
        f.add_argument(arg, **kw)
    f.set_defaults(fn=_facts)

    cp = sub.add_parser("camunda-plan", help="ask the Camunda process for the plan; fails open (job with the Camunda secret)")
    cp.add_argument("--facts", required=True)
    cp.add_argument("--out", default="plan.json")
    cp.add_argument("--advisor", default="false", help="true enables the AI advisor step")
    cp.add_argument("--run-ref", default="local")
    cp.add_argument("--await-seconds", default="30")
    cp.set_defaults(fn=_camunda_plan)

    pu = sub.add_parser("publish", help="publish the plan: check run, status, sticky comment (write job, no Camunda secret)")
    pu.add_argument("--plan", required=True)
    pu.add_argument("--repo", required=True)
    pu.add_argument("--pr", required=True)
    pu.add_argument("--head-sha", required=True)
    pu.add_argument("--summary-file", default="")
    pu.add_argument("--no-comment", action="store_true")
    pu.add_argument("--no-status", action="store_true")
    pu.set_defaults(fn=lambda a: __import__("planner.publish", fromlist=["run"]).run(a))

    dm = sub.add_parser("deploy-models", help="deploy the six BPMN/DMN files in one deployment (manual workflow, deployer client)")
    dm.add_argument("--dir", required=True, help="directory with the generated .bpmn and .dmn files")
    dm.add_argument("--tenant-id", default="", help="tenant of the deployment (multi-tenancy only)")
    dm.add_argument("--dry-run", action="store_true", help="check the files and the target, send nothing")
    dm.add_argument("--out", default="", help="write the deployment summary (ids, versions, keys) as JSON")
    dm.add_argument("--summary-file", default="")
    dm.set_defaults(fn=lambda a: __import__("planner.deploy", fromlist=["run"]).run(a))

    g = sub.add_parser("gate", help="a suite's triage step: output should_skip")
    g.add_argument("--repo", required=True)
    g.add_argument("--event", default="")
    g.add_argument("--fork", default="false")
    g.add_argument("--pr", default="")
    g.add_argument("--head-sha", default="")
    g.add_argument("--suite", required=True)
    g.add_argument("--mode", default="report", help="gate lets an enforced skip through; anything else never skips")
    g.add_argument("--timeout", default="240")
    g.add_argument("--interval", default="10")
    g.set_defaults(fn=lambda a: __import__("planner.gate", fromlist=["run"]).run(a))

    ru = sub.add_parser("runs", help="recent successful suite runs from the Actions API (offline decide only)")
    ru.add_argument("--repo", required=True)
    ru.add_argument("--git-dir", required=True)
    ru.add_argument("--base-sha", required=True)
    ru.add_argument("--default-branch", default="main")
    ru.add_argument("--config-path", default=".github/planner-config.json")
    ru.add_argument("--per-page", default="20")
    ru.add_argument("--out", default="runs.json")
    ru.set_defaults(fn=lambda a: __import__("planner.runs", fromlist=["run"]).run(a))

    c = sub.add_parser("collect", help="outcomes of finished suite runs and escapes, sent to the Camunda process")
    c.add_argument("--repo", required=True)
    c.add_argument("--git-dir", default=".")
    c.add_argument("--config-path", default=".github/planner-config.json")
    c.add_argument("--known-failures", default=".github/planner-known-failures.yaml")
    c.add_argument("--since-hours", default="6")
    c.add_argument("--max-docs", default="400")
    c.add_argument("--default-branch", default="main")
    c.add_argument("--dry-run", action="store_true", help="print the documents instead of sending them")
    c.add_argument("--now", default="")
    c.set_defaults(fn=lambda a: __import__("planner.collect", fromlist=["run"]).run(a))

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
