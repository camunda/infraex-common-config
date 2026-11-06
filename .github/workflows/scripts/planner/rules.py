"""Decision rules R1-R11 (see design.md section 2.1 and research.md section 2.1).

`decide` is a pure function of its `Inputs`: the PR diff, the base-commit suite registry, the
memory snapshot, the labels and the configuration. It reads the PR only as data.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

from . import RULES_VERSION, SCHEMA, __version__, gitutil, hcl, memory, pathglob
from . import impact as imp
from .util import PATH_RE, cadence, canonical, compact_ts, event_id, sha256_hex, suite_name

NEVER = 100000.0  # "age" of a suite that has never run: older than anything


@dataclass
class Inputs:
    repo: str
    slug: str
    pr: int
    head_sha: str
    base_sha: str
    merge_base: str
    event: str
    actor: str
    head_repo_is_fork: bool
    labels: list
    mode: str
    cfg: dict
    registry: dict
    head_tree: imp.Tree
    base_tree: imp.Tree
    changed: list
    memory: dict
    now: str
    registry_ref: str
    base_branch: str = "main"
    default_branch: str = "main"
    gate_suites: list | None = None  # override of cfg["gate_suites"] (variable PLANNER_GATE_SUITES)
    gate_base_branches: list = field(default_factory=lambda: ["main"])
    planner_version: str = __version__


def parse_labels(labels: list, registry: dict) -> dict:
    """skip_all, skip_<stem>, run_<stem>, planner:run-all. Returns sets of suite names."""
    stems = {s.name[:-4] if s.name.endswith(".yml") else s.name.rsplit(".", 1)[0]: s.name for s in registry.values()}
    out = {"skip_all": False, "skip": set(), "run": set(), "run_all": False, "seen": []}
    for lb in labels:
        if lb == "skip_all":
            out["skip_all"] = True
        elif lb == "planner:run-all":
            out["run_all"] = True
        elif lb.startswith("skip_") and lb[5:] in stems:
            out["skip"].add(stems[lb[5:]])
        elif lb.startswith("run_") and lb[4:] in stems:
            out["run"].add(stems[lb[4:]])
        else:
            continue
        out["seen"].append(lb)
    return out


def _is_docs(path: str) -> bool:
    return bool(re.search(r"\.(md|MD|txt)$", path))


def _is_plumbing(path: str) -> bool:
    return path.startswith((".github/workflows/", ".github/actions/", ".github/planner-config.json"))


def decide(inp: Inputs) -> dict:
    cfg, reg, changed = inp.cfg, inp.registry, inp.changed
    expensive = set(cfg["expensive_layers"])
    primary_exclude = re.compile(cfg["primary_exclude"])
    labels = parse_labels(inp.labels, reg)
    trig = imp.triggered_suites(reg, changed)

    edges = imp.module_edges(inp.head_tree)
    mods = imp.modules_of(edges)
    roots = imp.ra_roots(edges, mods)
    owners = imp.owner_suites(reg, imp.owner_modules(inp.base_tree, imp.modules_of(imp.module_edges(inp.base_tree))))
    users = imp.action_users(inp.base_tree)

    run: dict = {}
    fired: list = []
    rot_reasons: list = []
    rot_modules: set = set()
    rot_candidates: set = set()
    mandatory: dict = {}
    impacted_modules: list = []
    ras_hit: list = []
    interfaces: list = []
    consumers_map: dict = {}

    def add(name, why):
        why = why[:290]
        run.setdefault(name, [])
        if why not in run[name]:
            run[name].append(why)

    def fire(rule, subject, reason, evidence=None):
        fired.append({"rule": rule, "subject": subject[:300], "reason": reason[:500], "evidence": [e[:300] for e in (evidence or [])[:20]]})

    def primary_suites_for(paths):
        files = []
        for p in paths:
            files += inp.head_tree.under(p)
        names = [n for n in imp.owning_suites(reg, files) if reg[n].layer == "L4"]
        pri = [n for n in names if not primary_exclude.search(n)]
        return sorted(pri or names)

    # ---- R1: cheap layers that path filters already trigger
    for name in trig:
        if reg[name].layer in ("L0", "L1", "L2"):
            add(name, "R1 cheap layer")

    judge_changed = any(pathglob.match(g, f) for g in cfg["judge_paths"] for f in changed)
    plumbing = any(_is_plumbing(f) for f in changed)
    new_workflows = sorted({suite_name(f) for f in changed if re.match(r"^\.github/workflows/[^/]+\.ya?ml$", f) and suite_name(f) not in reg})

    docs_only = bool(changed) and all(_is_docs(c) for c in changed)
    if docs_only:
        fire("R6", "docs only", "only documentation files changed: cheap layers only")

    # ---- modules, engine pins, RA roots, shared tooling
    pin_deps: dict = {}
    if not docs_only:
        seen_mods = set()
        for c in changed:
            m = imp.module_of(mods, c)
            if not m or m in seen_mods:
                continue
            seen_mods.add(m)
            impacted_modules.append(m)
            consumers = imp.consumers_of(edges, m)
            ras = sorted({imp.ra_key(r) for r in consumers})
            ras_hit += [r for r in ras if r not in ras_hit]
            own = owners.get(m, [])
            consumers_map[m] = sorted(set(primary_suites_for(ras)) | set(own))[:100]
            for n in own:
                add(n, "R2 owner suite builds module %s" % m)
            iface_changed = [x for x in changed if x.startswith(m + "/") and posixpath.basename(x) in ("variables.tf", "outputs.tf")]
            old_texts = {f: gitutil.show(inp.repo, inp.merge_base, "%s/%s" % (m, f)) for f in ("variables.tf", "outputs.tf", "main.tf")}
            new_texts = {f: inp.head_tree.read("%s/%s" % (m, f)) for f in ("variables.tf", "outputs.tf", "main.tf")}
            ifd = hcl.iface_diff(old_texts, new_texts, m)
            interfaces.append(ifd)
            if iface_changed:
                ra_files = [f for ra in ras for f in inp.head_tree.under(ra)]
                for name in imp.owning_suites(reg, ra_files):
                    if reg[name].kind.startswith("golden"):
                        add(name, "R2 interface files of %s changed: consumer golden plan (offline)" % m)
            if ifd["significant"]:
                rot_reasons.append("R9 significant interface change in %s" % m)
                rot_modules.add(m)
                rot_candidates |= set(primary_suites_for(ras))
                fire("R9", m, "removed or renamed output, removed variable, new required variable or changed type",
                     [k + ": " + ",".join(v) for k, v in ifd.items() if isinstance(v, list) and v and k != "changed_defaults"])
            elif not own:
                rot_reasons.append("R3 %s has no module-level suite" % m)
                rot_modules.add(m)
                rot_candidates |= set(primary_suites_for(ras))
                fire("R3", m, "no module-level suite: one consumer is rotated in")
            else:
                fire("R2", m, "module change, interface not significant; owner suite %s" % ", ".join(own))
            # circuit breaker: consumers whose pair has recent confirmed escapes must run
            for n in primary_suites_for(ras):
                b = memory.breaker_for(inp.memory, m, n)
                if b:
                    mandatory.setdefault(n, []).append("R7b circuit breaker on %s (escape mass %.1f)" % (m, b["mass"]))
                    fire("R7b", "%s|%s" % (m, n), "confirmed escapes within the window: suite is mandatory for this module", b["evidence"])

        # engine pins (renovate-annotated versions)
        pin_carrier_dirs: dict = {}
        for f in changed:
            if not f.endswith((".tf", ".tfvars", ".yml", ".yaml")) or f.startswith(".github/"):
                continue
            for pc in hcl.pin_changes(gitutil.show(inp.repo, inp.merge_base, f), inp.head_tree.read(f), cfg["major_level"]):
                pin_deps.setdefault((pc["dep"], pc["from"], pc["to"], pc["major"]), []).append(f)
        engine_pins = []
        for (dep, a, b, is_major), files in sorted(pin_deps.items()):
            engine_pins.append({"dep": dep, "from": a, "to": b, "major": is_major})
            if not is_major:
                continue
            carriers = {posixpath.dirname(p) for p, _, _ in inp.head_tree.grep(r"depName=%s([[:space:]]|$)" % re.escape(dep), ["*.tf", "*.tfvars"])}
            for d in sorted(carriers):
                m = imp.module_of(mods, d + "/x")
                if m:
                    rot_modules.add(m)
                    for n in owners.get(m, []):
                        add(n, "R8 engine major %s %s->%s: module owner suite" % (dep, a, b))
                    rot_candidates |= set(primary_suites_for([imp.ra_key(r) for r in imp.consumers_of(edges, m)]))
                else:
                    rot_candidates |= set(primary_suites_for([imp.ra_key(d)]))
            rot_reasons.append("R8 engine major %s %s->%s" % (dep, a, b))
            fire("R8", "%s %s -> %s" % (dep, a, b), "annotated version pin changed its major in %d file(s)" % len(files), files[:5])

        # RA roots changed outside modules
        ra_files: dict = {}
        for c in changed:
            if imp.module_of(mods, c) or _is_docs(c) or c.startswith((".github/", ".lint/")) or c == ".tool-versions":
                continue
            root = next((r for r in roots if c.startswith(r + "/")), None)
            if root:
                ra_files.setdefault(imp.ra_key(root), []).append(c)
        for ra, fs in sorted(ra_files.items()):
            if ra not in ras_hit:
                ras_hit.append(ra)
            if all(hcl.pin_only_diff(gitutil.diff_text(inp.repo, inp.base_sha, inp.head_sha, f), f) for f in fs):
                fire("R10", ra, "only version pins, goldens or docs changed (%d files): no own from-scratch run" % len(fs))
            else:
                for n in primary_suites_for([ra]):
                    add(n, "R4 RA root %s changed beyond version pins" % ra)
                fire("R4", ra, "RA root changed: its suites run")

        # shared actions and tooling (R5)
        for c in changed:
            if c.startswith(".github/actions/"):
                a = c.split("/")[2]
                us = sorted(users.get(a, set()) & {n for n in trig if reg[n].layer in expensive})
                if us:
                    rot_reasons.append("R5 shared action %s" % a)
                    rot_candidates |= set(us)
                    fire("R5", a, "shared action change: one suite that uses it is rotated in (%d use it)" % len(us))
            elif c == ".tool-versions":
                us = sorted(n for n in trig if reg[n].layer in expensive and ".tool-versions" in trig[n]["files"])
                if us:
                    rot_reasons.append("R5 .tool-versions")
                    rot_candidates |= set(us)
                    fire("R5", ".tool-versions", "toolchain pin change: one suite is rotated in")
            elif re.match(r"^\.github/workflows/[^/]+\.ya?ml$", c):
                n = suite_name(c)
                if n in reg and n in trig:
                    add(n, "R5 the changed workflow itself")
        for n in new_workflows:
            fire("R5", n, "workflow not in the base registry: not narrowed")

    # ---- R7: rotation
    rotation = None
    if rot_candidates:
        cand = sorted(c for c in rot_candidates if c in reg)
        # prefer from-scratch RA suites; special lifecycles (failover, create/destroy...) and single-resource
        # procedure suites are only rotated in when nothing else can stand in
        pri = [c for c in cand if reg[c].layer == "L4" and not primary_exclude.search(c)]
        cand = pri or cand
        rows = []
        for n in cand:
            best = 0.0
            wmax = 1.0
            age_seen = None
            for m in (sorted(rot_modules) or [None]):
                a = memory.age_days(inp.memory, n, m)
                a_eff = NEVER if a is None else a
                w = memory.weight(inp.memory, m, n)
                if a_eff * w >= best:
                    best, wmax, age_seen = a_eff * w, w, a
            rows.append({"suite": n, "age_days": None if age_seen is None else round(age_seen, 2), "weight": round(wmax, 2),
                         "score": round(best, 2), "cost_min": memory.cost(inp.memory, n)})
        rows.sort(key=lambda r: (-r["score"], r["cost_min"] if r["cost_min"] is not None else 1e9, r["suite"]))
        tie = cfg["learn"]["tie_percent"] / 100.0
        picked = rows[0]
        for r in rows[1:]:
            if picked["score"] > 0 and r["score"] >= picked["score"] * (1 - tie):
                if (r["cost_min"] if r["cost_min"] is not None else 1e9) < (picked["cost_min"] if picked["cost_min"] is not None else 1e9):
                    picked = r
        rot_reasons = list(dict.fromkeys(rot_reasons))
        add(picked["suite"], "R7 rotation (%s): highest staleness x weight" % "; ".join(rot_reasons))
        rotation = {"reasons": rot_reasons, "candidates": rows[:50], "picked": picked["suite"]}
    for n, whys in mandatory.items():
        for w in whys:
            add(n, w)

    # ---- judge or plumbing change: do not narrow
    if judge_changed:
        for n, e in trig.items():
            if reg[n].layer in expensive:
                add(n, "R11 the planner configuration or caller changed: nothing is narrowed")
        fire("R11", "judge", "the planner config or its caller changed in this PR: the path-triggered set runs")

    # ---- labels (optional escape hatches; never required)
    applied, conflicts = [], []
    if labels["run_all"]:
        for n, e in trig.items():
            if reg[n].layer in expensive:
                add(n, "R11 label planner:run-all: path-triggered set restored")
        applied.append("planner:run-all")
    for n in sorted(labels["run"]):
        add(n, "R11 label run_%s" % n[:-4])
        applied.append("run_%s" % n[:-4])
    for n in sorted(labels["skip"]):
        if n in labels["run"]:
            conflicts.append("skip_%s with run_%s: skip wins" % (n[:-4], n[:-4]))
        if n in run:
            run.pop(n)
        applied.append("skip_%s" % n[:-4])
    if labels["skip_all"]:
        for n in list(run):
            if reg[n].layer != "L0":
                run.pop(n)
        applied.append("skip_all")

    # ---- the decision map and enforcement
    gate_list = inp.gate_suites if inp.gate_suites is not None else cfg["gate_suites"]
    reasons_for_skip = {}
    for n, e in trig.items():
        if reg[n].layer in ("ops",):
            continue
        reasons_for_skip[n] = ["path filter matched, no rule needs this suite"]
    effective_mode = inp.mode
    gate_reason = None
    if inp.head_repo_is_fork:
        effective_mode, gate_reason = "report", "fork pull request"
    elif inp.base_branch not in inp.gate_base_branches:
        effective_mode, gate_reason = "report", "base branch %s is not gated" % inp.base_branch
    decision = {}
    for n in sorted(trig):
        s = reg[n]
        if s.layer == "ops":
            continue
        act = "run" if n in run else "skip"
        enforced = effective_mode == "gate" and n in gate_list and s.layer in expensive
        decision[n] = {"action": act, "enforced": bool(enforced), "layer": s.layer,
                       "reasons": (run.get(n) or reasons_for_skip.get(n, []))[:20]}
    for n, whys in run.items():
        if n not in decision and n in reg:
            decision[n] = {"action": "run", "enforced": False, "layer": reg[n].layer,
                           "reasons": whys[:19] + ["not path-triggered: this suite will only start if its paths: filter is widened"]}
    for n in new_workflows:
        decision[n] = {"action": "run", "enforced": False, "layer": "unknown", "reasons": ["new workflow, not in the base registry"]}
    if gate_reason:
        fire("R12", "mode", "gating is off for this PR: %s (decision recorded as report)" % gate_reason)

    safety = {}
    for n, d in decision.items():
        if d["action"] == "skip" and d["layer"] in expensive:
            s = reg[n]
            ls = memory.last_success(inp.memory, n, None)
            safety[n] = {"next_scheduled": cadence(s.crons[0]) if s.crons else "no schedule (PR/dispatch only)",
                         "last_full_success_at": ls["at"] if ls else None}

    mem_inputs = []
    for n in sorted(set(rot_candidates) | set(safety)):
        ls = memory.last_success(inp.memory, n, None)
        a = memory.age_days(inp.memory, n, None)
        mem_inputs.append({"suite": n, "source": (ls or {}).get("source", "none"), "last_full_success_at": ls["at"] if ls else None,
                           "age_days": None if a is None else round(a, 2), "weight": round(max([memory.weight(inp.memory, m, n) for m in rot_modules] or [1.0]), 2)})

    changed_sorted = sorted(changed)
    labels_hash = sha256_hex(canonical(sorted(inp.labels)))[:16]
    w = inp.memory.get("weights")
    return {
        "schema": SCHEMA, "type": "decision", "repo": inp.slug, "pr": inp.pr, "head_sha": inp.head_sha,
        "base_sha": inp.base_sha, "merge_base": inp.merge_base, "event": inp.event, "actor": inp.actor[:100],
        "head_repo_is_fork": inp.head_repo_is_fork, "mode": effective_mode, "planner_version": inp.planner_version,
        "rules_version": RULES_VERSION, "weights_version": w["id"] if w else "none", "registry_ref": inp.registry_ref,
        "plumbing_change": plumbing, "judge_changed": judge_changed,
        "changed_files": {"count": len(changed_sorted), "sha256": sha256_hex(canonical(changed_sorted)),
                          "paths": [p for p in changed_sorted if PATH_RE.match(p)][:200], "truncated": len(changed_sorted) > 200},
        "impacted": {"modules": sorted(set(impacted_modules)), "ras": sorted(set(ras_hit)),
                     "suites_path_triggered": sorted(trig), "engine_pins": engine_pins if not docs_only else [],
                     "interface": [i for i in interfaces], "consumers": consumers_map},
        "rules_fired": fired[:200], "memory_inputs": mem_inputs, "rotation": rotation, "decision": decision,
        "overrides": {"labels": sorted(labels["seen"]), "applied": applied, "conflicts": conflicts},
        "safety_net": safety, "labels_hash": labels_hash,
    }


def finalize(decision: dict, ts: str) -> dict:
    """Add id and timestamp. The id hashes the whole body, so a retried write is idempotent."""
    body = dict(decision)
    body["ts"] = ts
    body["id"] = event_id("dec", body)
    return body
