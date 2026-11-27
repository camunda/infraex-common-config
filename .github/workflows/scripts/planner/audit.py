"""`planner audit`: query and verify the store (a checked-out copy of the branch)."""

from __future__ import annotations

import json
import sys
from datetime import datetime

from . import render, schema
from .store import DirStore
from .util import canonical, event_id, parse_iso, sha256_hex


def _fmt(e: dict) -> str:
    t = e["type"]
    if t == "decision":
        d = e["decision"]
        runs = sorted(n for n, v in d.items() if v["action"] == "run" and v["layer"] in ("L3", "L4"))
        skips = sorted(n for n, v in d.items() if v["action"] == "skip")
        return "%s decision %s pr#%d %s mode=%s modules=%s run=%s skip=%d rules=%s" % (
            e["ts"], e["id"], e["pr"], e["head_sha"][:8], e["mode"], ",".join(e["impacted"]["modules"]) or "-",
            ",".join(runs) or "-", len(skips), ",".join(sorted({r["rule"] for r in e["rules_fired"]})) or "-")
    if t == "outcome":
        return "%s outcome %s %s pr#%d %s %s %.1fmin%s" % (e["ts"], e["id"], e["suite"], e["pr"], e["head_sha"][:8], e["conclusion"], e["minutes"], " flake?" if e["flake_hint"] else "")
    if t == "escape":
        return "%s escape %s suite=%s candidates=%s confirmed_by=%s" % (e["ts"], e["id"], e["suite"], ",".join("pr#%d:%s" % (c["pr"], c["module"]) for c in e["candidates"]), e["confirmed_by"])
    if t == "weights":
        return "%s weights %s pairs=%d prev=%s" % (e["ts"], e["id"], len(e["pairs"]), e["prev"])
    return "%s %s %s" % (e["ts"], t, e["id"])


def select(events: list, pr=None, module=None, suite=None, since=None, types=None) -> list:
    out = []
    dec_by_id = {e["id"]: e for e in events if e["type"] == "decision"}
    for e in events:
        if types and e["type"] not in types:
            continue
        if since and e["ts"] < since:
            continue
        if pr is not None:
            if e["type"] in ("decision", "outcome") and e["pr"] != pr:
                continue
            if e["type"] == "escape" and not any(c["pr"] == pr for c in e["candidates"]):
                continue
            if e["type"] not in ("decision", "outcome", "escape"):
                continue
        if module:
            if e["type"] == "decision":
                if not any(module in m for m in e["impacted"]["modules"]):
                    continue
            elif e["type"] == "outcome":
                d = dec_by_id.get(e.get("decision_id"))
                if not d or not any(module in m for m in d["impacted"]["modules"]):
                    continue
            elif e["type"] == "escape":
                if not any(module in c["module"] for c in e["candidates"]):
                    continue
            else:
                continue
        if suite:
            if e["type"] in ("outcome", "escape") and suite not in e["suite"]:
                continue
            if e["type"] == "decision" and not any(suite in n for n in e["decision"]):
                continue
            if e["type"] not in ("outcome", "escape", "decision"):
                continue
        out.append(e)
    return out


def verify(events: list) -> list:
    """Problems found: schema errors, decision ids that do not match their content."""
    problems = []
    for e in events:
        path = e.get("_path", "?")
        clean = {k: v for k, v in e.items() if k != "_path"}
        errs = schema.validate(clean)
        if errs:
            problems.append("%s: schema: %s" % (path, errs[0]))
            continue
        if e["type"] == "decision":
            body = {k: v for k, v in clean.items() if k != "id"}
            if event_id("dec", body) != e["id"]:
                problems.append("%s: decision id does not match its content" % path)
    return problems


def run(a) -> int:
    events = DirStore(a.store_dir).events()
    if a.what == "verify":
        problems = verify(events)
        for p in problems:
            print(p)
        print("verified %d events, %d problem(s)" % (len(events), len(problems)))
        return 1 if problems else 0
    types = set(a.type.split(",")) if a.type else None
    sel = select(events, pr=int(a.pr) if a.pr else None, module=a.module, suite=a.suite, since=a.since, types=types)
    if a.format == "json":
        print(json.dumps([{k: v for k, v in e.items() if k != "_path"} for e in sel], indent=1))
    else:
        for e in sel:
            print(_fmt(e))
        print("%d event(s)" % len(sel))
    return 0
