"""Facts: everything the policy needs to know about a pull request, as plain data.

`build` reads the PR diff, the base-commit suite registry and the module graph and returns a JSON
document (schema planner.facts/1). It contains no memory and makes no decision: the decision is a
pure function of facts + memory (policy.py here, the DMN decision tables in the Camunda process).
Everything that comes from the PR is data: paths are pattern-checked and capped, no free text from
the PR (title, body, branch, commit messages) is included.
"""

from __future__ import annotations

import posixpath
import re

from . import gitutil, hcl, pathglob
from . import impact as imp
from .util import PATH_RE, SUITE_RE, canonical, cadence, sha256_hex, suite_name

FACTS_SCHEMA = "planner.facts/1"


def parse_labels(labels: list, registry: dict) -> dict:
    """skip_all, skip_<stem>, run_<stem>, planner:run-all, as lists of suite file names."""
    stems = {s.name[:-4] if s.name.endswith(".yml") else s.name.rsplit(".", 1)[0]: s.name for s in registry.values()}
    out = {"skip_all": False, "skip": [], "run": [], "run_all": False, "seen": []}
    for lb in labels:
        if lb == "skip_all":
            out["skip_all"] = True
        elif lb == "planner:run-all":
            out["run_all"] = True
        elif lb.startswith("skip_") and lb[5:] in stems:
            out["skip"].append(stems[lb[5:]])
        elif lb.startswith("run_") and lb[4:] in stems:
            out["run"].append(stems[lb[4:]])
        else:
            continue
        out["seen"].append(lb)
    out["skip"], out["run"] = sorted(set(out["skip"])), sorted(set(out["run"]))
    return out


def _is_docs(path: str) -> bool:
    return bool(re.search(r"\.(md|MD|txt)$", path))


def _is_plumbing(path: str) -> bool:
    return path.startswith((".github/workflows/", ".github/actions/", ".github/planner-config.json"))


def build(repo: str, slug: str, pr: int, head_sha: str, base_sha: str, merge_base: str, event: str, actor: str,
          fork: bool, labels: list, requested_mode: str, cfg: dict, registry: dict, head_tree: imp.Tree,
          base_tree: imp.Tree, changed: list, base_branch: str, default_branch: str, gate_suites: list | None,
          gate_base_branches: list, planner_version: str) -> dict:
    reg = registry
    expensive = set(cfg["expensive_layers"])
    primary_exclude = re.compile(cfg["primary_exclude"])
    lab = parse_labels(labels, reg)
    trig = imp.triggered_suites(reg, changed)
    edges = imp.module_edges(head_tree)
    mods = imp.modules_of(edges)
    roots = imp.ra_roots(edges, mods)
    owners = imp.owner_suites(reg, imp.owner_modules(base_tree, imp.modules_of(imp.module_edges(base_tree))))
    users = imp.action_users(base_tree)

    def primary_suites_for(paths):
        files = []
        for p in paths:
            files += head_tree.under(p)
        names = [n for n in imp.owning_suites(reg, files) if reg[n].layer == "L4"]
        pri = [n for n in names if not primary_exclude.search(n)]
        return sorted(pri or names)

    judge_changed = any(pathglob.match(g, f) for g in cfg["judge_paths"] for f in changed)
    plumbing = any(_is_plumbing(f) for f in changed)
    new_workflows = sorted({suite_name(f) for f in changed if re.match(r"^\.github/workflows/[^/]+\.ya?ml$", f) and suite_name(f) not in reg})
    docs_only = bool(changed) and all(_is_docs(c) for c in changed)

    signals: list = []
    impacted_modules: list = []
    ras_hit: list = []
    interfaces: list = []
    consumers_map: dict = {}
    engine_pins: list = []
    if not docs_only:
        seen = set()
        for c in changed:
            m = imp.module_of(mods, c)
            if not m or m in seen:
                continue
            seen.add(m)
            impacted_modules.append(m)
            consumers = imp.consumers_of(edges, m)
            ras = sorted({imp.ra_key(r) for r in consumers})
            ras_hit += [r for r in ras if r not in ras_hit]
            own = owners.get(m, [])
            cons_suites = primary_suites_for(ras)
            consumers_map[m] = sorted(set(cons_suites) | set(own))[:100]
            iface_changed = any(x.startswith(m + "/") and posixpath.basename(x) in ("variables.tf", "outputs.tf") for x in changed)
            old_texts = {f: gitutil.show(repo, merge_base, "%s/%s" % (m, f)) for f in ("variables.tf", "outputs.tf", "main.tf")}
            new_texts = {f: head_tree.read("%s/%s" % (m, f)) for f in ("variables.tf", "outputs.tf", "main.tf")}
            ifd = hcl.iface_diff(old_texts, new_texts, m)
            interfaces.append(ifd)
            goldens = []
            if iface_changed:
                ra_files = [f for ra in ras for f in head_tree.under(ra)]
                goldens = [n for n in imp.owning_suites(reg, ra_files) if reg[n].kind.startswith("golden")]
            signals.append({"type": "module", "module": m, "owner_suites": list(own), "consumer_suites": cons_suites,
                            "golden_suites": goldens, "iface_files_changed": iface_changed,
                            "significant": bool(ifd["significant"]),
                            "iface_evidence": [k + ": " + ",".join(v) for k, v in ifd.items() if isinstance(v, list) and v and k != "changed_defaults"][:20]})
        # engine pins
        pin_deps: dict = {}
        for f in changed:
            if not f.endswith((".tf", ".tfvars", ".yml", ".yaml")) or f.startswith(".github/"):
                continue
            for pc in hcl.pin_changes(gitutil.show(repo, merge_base, f), head_tree.read(f), cfg["major_level"]):
                pin_deps.setdefault((pc["dep"], pc["from"], pc["to"], pc["major"]), []).append(f)
        for (dep, a, b, is_major), files in sorted(pin_deps.items()):
            engine_pins.append({"dep": dep, "from": a, "to": b, "major": is_major})
            if not is_major:
                continue
            carriers = {posixpath.dirname(p) for p, _, _ in head_tree.grep(r"depName=%s([[:space:]]|$)" % re.escape(dep), ["*.tf", "*.tfvars"])}
            pin_modules, own_suites, cands = [], [], []
            for d in sorted(carriers):
                m = imp.module_of(mods, d + "/x")
                if m:
                    pin_modules.append(m)
                    own_suites += owners.get(m, [])
                    cands += primary_suites_for([imp.ra_key(r) for r in imp.consumers_of(edges, m)])
                else:
                    cands += primary_suites_for([imp.ra_key(d)])
            signals.append({"type": "engine_major", "dep": dep, "from": a, "to": b, "files": files[:5], "files_n": len(files),
                            "modules": sorted(set(pin_modules)), "owner_suites": sorted(set(own_suites)),
                            "candidate_suites": sorted(set(cands))})
        # RA roots outside modules
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
            pin_only = all(hcl.pin_only_diff(gitutil.diff_text(repo, base_sha, head_sha, f), f) for f in fs)
            signals.append({"type": "ra_root", "ra": ra, "pin_only": pin_only, "files_n": len(fs), "suites": primary_suites_for([ra])})
        # shared actions and tooling
        for c in changed:
            if c.startswith(".github/actions/"):
                a = c.split("/")[2]
                us = sorted(users.get(a, set()) & {n for n in trig if reg[n].layer in expensive})
                if us:
                    signals.append({"type": "shared_action", "name": a, "suites": us})
            elif c == ".tool-versions":
                us = sorted(n for n in trig if reg[n].layer in expensive and ".tool-versions" in trig[n]["files"])
                if us:
                    signals.append({"type": "tool_versions", "suites": us})
            elif re.match(r"^\.github/workflows/[^/]+\.ya?ml$", c):
                n = suite_name(c)
                if n in reg and n in trig:
                    signals.append({"type": "workflow_changed", "suite": n})

    # per-suite attributes for every suite the policy may mention
    names = set(trig)
    for s in signals:
        for k in ("owner_suites", "consumer_suites", "golden_suites", "candidate_suites", "suites"):
            names |= set(s.get(k, []))
        if "suite" in s:
            names.add(s["suite"])
    names |= set(lab["run"]) | set(lab["skip"])
    suites = {}
    for n in sorted(names):
        if n not in reg:
            continue
        s = reg[n]
        suites[n] = {"layer": s.layer, "kind": s.kind, "path_triggered": n in trig,
                     "primary": s.layer == "L4" and not primary_exclude.search(n),
                     "next_scheduled": cadence(s.crons[0]) if s.crons else "no schedule (PR/dispatch only)"}
    changed_sorted = sorted(changed)
    return {
        "schema": FACTS_SCHEMA, "repo": slug, "pr": pr, "head_sha": head_sha, "base_sha": base_sha, "merge_base": merge_base,
        "event": event, "actor": actor[:100], "head_repo_is_fork": bool(fork), "base_branch": base_branch,
        "default_branch": default_branch, "registry_ref": base_sha, "planner_version": planner_version,
        "requested_mode": requested_mode, "gate_base_branches": list(gate_base_branches),
        "gate_suites": list(gate_suites if gate_suites is not None else cfg["gate_suites"]),
        "expensive_layers": sorted(expensive), "tie_percent": cfg["learn"]["tie_percent"],
        "labels": lab, "docs_only": docs_only, "judge_changed": judge_changed, "plumbing_change": plumbing,
        "new_workflows": new_workflows,
        "changed_files": {"count": len(changed_sorted), "sha256": sha256_hex(canonical(changed_sorted)),
                          "paths": [p for p in changed_sorted if PATH_RE.match(p)][:200], "truncated": len(changed_sorted) > 200},
        "suites": suites, "signals": signals,
        "impacted": {"modules": sorted(set(impacted_modules)), "ras": sorted(set(ras_hit)), "suites_path_triggered": sorted(trig),
                     "engine_pins": engine_pins if not docs_only else [], "interface": interfaces, "consumers": consumers_map},
    }
