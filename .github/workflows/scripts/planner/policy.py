"""The decision policy, R1-R12, as a pure function of facts and memory.

This is the reference implementation of the DMN decision tables in the Camunda process
(camunda/ci-test-planner.dmn). A conformance test evaluates both on the same inputs.
"""

from __future__ import annotations

from . import RULES_VERSION, SCHEMA
from . import memory
from .util import PATH_RE

NEVER = 100000.0  # age of a suite that has never run: older than anything
COST_UNKNOWN = 1000000000.0  # sorts after every known cost


def plan(facts: dict, cm: dict) -> dict:
    """facts: planner.facts/1. cm: compact memory (memory.compact). Returns a decision body (no id, ts)."""
    suites = facts["suites"]
    expensive = set(facts["expensive_layers"])
    lab = facts["labels"]
    run: dict = {}
    fired: list = []
    rot_reasons: list = []
    rot_modules: set = set()
    rot_candidates: set = set()
    mandatory: dict = {}

    def add(name, why):
        why = why[:290]
        run.setdefault(name, [])
        if why not in run[name]:
            run[name].append(why)

    def fire(rule, subject, reason, evidence=None):
        fired.append({"rule": rule, "subject": subject[:300], "reason": reason[:500], "evidence": [e[:300] for e in (evidence or [])[:20]]})

    trig = [n for n, s in suites.items() if s["path_triggered"]]
    for n in trig:
        if suites[n]["layer"] in ("L0", "L1", "L2"):
            add(n, "R1 cheap layer")
    if facts["docs_only"]:
        fire("R6", "docs only", "only documentation files changed: cheap layers only")

    for sig in facts["signals"]:
        t = sig["type"]
        if t == "module":
            m = sig["module"]
            for n in sig["owner_suites"]:
                add(n, "R2 owner suite builds module %s" % m)
            for n in sig["golden_suites"]:
                add(n, "R2 interface files of %s changed: consumer golden plan (offline)" % m)
            if sig["significant"]:
                rot_reasons.append("R9 significant interface change in %s" % m)
                rot_modules.add(m)
                rot_candidates |= set(sig["consumer_suites"])
                fire("R9", m, "removed or renamed output, removed variable, new required variable or changed type", sig["iface_evidence"])
            elif not sig["owner_suites"]:
                rot_reasons.append("R3 %s has no module-level suite" % m)
                rot_modules.add(m)
                rot_candidates |= set(sig["consumer_suites"])
                fire("R3", m, "no module-level suite: one consumer is rotated in")
            else:
                fire("R2", m, "module change, interface not significant; owner suite %s" % ", ".join(sig["owner_suites"]))
            for n in sig["consumer_suites"]:
                b = cm["breaker"].get("%s|%s" % (m, n))
                if b:
                    mandatory.setdefault(n, []).append("R7b circuit breaker on %s" % m)
                    fire("R7b", "%s|%s" % (m, n), "confirmed escapes within the window: suite is mandatory for this module", b["evidence"])
        elif t == "engine_major":
            label = "%s %s->%s" % (sig["dep"], sig["from"], sig["to"])
            for m in sig["modules"]:
                rot_modules.add(m)
            for n in sig["owner_suites"]:
                add(n, "R8 engine major %s: module owner suite" % label)
            rot_candidates |= set(sig["candidate_suites"])
            rot_reasons.append("R8 engine major %s" % label)
            fire("R8", "%s %s -> %s" % (sig["dep"], sig["from"], sig["to"]), "annotated version pin changed its major in %d file(s)" % sig["files_n"], sig["files"])
        elif t == "ra_root":
            if sig["pin_only"]:
                fire("R10", sig["ra"], "only version pins, goldens or docs changed (%d files): no own from-scratch run" % sig["files_n"])
            else:
                for n in sig["suites"]:
                    add(n, "R4 RA root %s changed beyond version pins" % sig["ra"])
                fire("R4", sig["ra"], "RA root changed: its suites run")
        elif t == "shared_action":
            rot_reasons.append("R5 shared action %s" % sig["name"])
            rot_candidates |= set(sig["suites"])
            fire("R5", sig["name"], "shared action change: one suite that uses it is rotated in (%d use it)" % len(sig["suites"]))
        elif t == "tool_versions":
            rot_reasons.append("R5 .tool-versions")
            rot_candidates |= set(sig["suites"])
            fire("R5", ".tool-versions", "toolchain pin change: one suite is rotated in")
        elif t == "workflow_changed":
            add(sig["suite"], "R5 the changed workflow itself")
    for n in facts["new_workflows"]:
        fire("R5", n, "workflow not in the base registry: not narrowed")

    # R7 rotation
    rotation = None
    if rot_candidates:
        cand = sorted(c for c in rot_candidates if c in suites)
        pri = [c for c in cand if suites[c]["primary"]]
        cand = pri or cand
        rows = []
        for n in cand:
            entries = []
            for m in (sorted(rot_modules) or [None]):
                a = memory.c_age(cm, n, m)
                w = memory.c_weight(cm, m, n)
                entries.append({"age": a, "w": w, "score": (NEVER if a is None else a) * w})
            best = max(e["score"] for e in entries)
            chosen = [e for e in entries if e["score"] == best][-1]
            cost = memory.c_cost(cm, n)
            rows.append({"suite": n, "age_days": chosen["age"], "weight": chosen["w"], "score": best, "cost_min": cost,
                         "cost_key": cost if cost is not None else COST_UNKNOWN})
        rows.sort(key=lambda r: (-r["score"], r["cost_key"], r["suite"]))
        tie = facts["tie_percent"] / 100.0
        top = rows[0]
        if top["score"] > 0:
            near = [r for r in rows if r["score"] >= top["score"] * (1 - tie)]
            picked = sorted(near, key=lambda r: (r["cost_key"], -r["score"], r["suite"]))[0]
        else:
            picked = top
        out_rows = [{"suite": r["suite"], "age_days": None if r["age_days"] is None else round(r["age_days"], 2),
                     "weight": round(r["weight"], 2), "score": round(r["score"], 2), "cost_min": r["cost_min"]} for r in rows[:50]]
        rot_reasons = list(dict.fromkeys(rot_reasons))
        add(picked["suite"], "R7 rotation (%s): highest staleness x weight" % "; ".join(rot_reasons))
        rotation = {"reasons": rot_reasons, "candidates": out_rows, "picked": picked["suite"]}
    for n, whys in mandatory.items():
        for w in whys:
            add(n, w)

    if facts["judge_changed"]:
        for n in trig:
            if suites[n]["layer"] in expensive:
                add(n, "R11 the planner configuration or caller changed: nothing is narrowed")
        fire("R11", "judge", "the planner config or its caller changed in this PR: the path-triggered set runs")

    applied, conflicts = [], []
    if lab["run_all"]:
        for n in trig:
            if suites[n]["layer"] in expensive:
                add(n, "R11 label planner:run-all: path-triggered set restored")
        applied.append("planner:run-all")
    for n in lab["run"]:
        add(n, "R11 label run_%s" % n[:-4])
        applied.append("run_%s" % n[:-4])
    for n in lab["skip"]:
        if n in lab["run"]:
            conflicts.append("skip_%s with run_%s: skip wins" % (n[:-4], n[:-4]))
        run.pop(n, None)
        applied.append("skip_%s" % n[:-4])
    if lab["skip_all"]:
        for n in list(run):
            if suites.get(n, {}).get("layer") != "L0":
                run.pop(n)
        applied.append("skip_all")

    mode = facts["requested_mode"] if facts["requested_mode"] in ("report", "gate") else "report"
    gate_reason = None
    if facts["head_repo_is_fork"]:
        mode, gate_reason = "report", "fork pull request"
    elif facts["base_branch"] not in facts["gate_base_branches"]:
        mode, gate_reason = "report", "base branch %s is not gated" % facts["base_branch"]
    decision = {}
    for n in sorted(trig):
        s = suites[n]
        if s["layer"] == "ops":
            continue
        enforced = mode == "gate" and n in facts["gate_suites"] and s["layer"] in expensive
        decision[n] = {"action": "run" if n in run else "skip", "enforced": bool(enforced), "layer": s["layer"],
                       "reasons": (run.get(n) or ["path filter matched, no rule needs this suite"])[:20]}
    for n, whys in run.items():
        if n not in decision and n in suites:
            decision[n] = {"action": "run", "enforced": False, "layer": suites[n]["layer"],
                           "reasons": whys[:19] + ["not path-triggered: this suite will only start if its paths: filter is widened"]}
    for n in facts["new_workflows"]:
        decision[n] = {"action": "run", "enforced": False, "layer": "unknown", "reasons": ["new workflow, not in the base registry"]}
    if gate_reason:
        fire("R12", "mode", "gating is off for this PR: %s (decision recorded as report)" % gate_reason)

    safety = {}
    for n, d in decision.items():
        if d["action"] == "skip" and d["layer"] in expensive:
            safety[n] = {"next_scheduled": suites[n]["next_scheduled"],
                         "last_full_success_at": (cm["suites"].get(n) or {}).get("last_full_success_at")}
    mem_inputs = []
    for n in sorted(set(rot_candidates) | set(safety)):
        s = cm["suites"].get(n) or {}
        a = memory.c_age(cm, n, None)
        mem_inputs.append({"suite": n, "source": s.get("source", "none"), "last_full_success_at": s.get("last_full_success_at"),
                           "age_days": None if a is None else round(a, 2),
                           "weight": round(max([memory.c_weight(cm, m, n) for m in rot_modules] or [1.0]), 2)})
    imp_ = facts["impacted"]
    return {
        "schema": SCHEMA, "type": "decision", "repo": facts["repo"], "pr": facts["pr"], "head_sha": facts["head_sha"],
        "base_sha": facts["base_sha"], "merge_base": facts["merge_base"], "event": facts["event"], "actor": facts["actor"],
        "head_repo_is_fork": facts["head_repo_is_fork"], "mode": mode, "planner_version": facts["planner_version"],
        "rules_version": RULES_VERSION, "weights_version": cm["weights_version"], "registry_ref": facts["registry_ref"],
        "plumbing_change": facts["plumbing_change"], "judge_changed": facts["judge_changed"],
        "changed_files": facts["changed_files"],
        "impacted": {"modules": imp_["modules"], "ras": imp_["ras"], "suites_path_triggered": imp_["suites_path_triggered"],
                     "engine_pins": imp_["engine_pins"], "interface": imp_["interface"], "consumers": imp_["consumers"]},
        "rules_fired": fired[:200], "memory_inputs": mem_inputs, "rotation": rotation, "decision": decision,
        "overrides": {"labels": sorted(lab["seen"]), "applied": applied, "conflicts": conflicts},
        "safety_net": safety, "labels_hash": facts.get("labels_hash", ""),
    }
