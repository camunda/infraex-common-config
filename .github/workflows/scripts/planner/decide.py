"""`planner decide`: build, validate and write the decision artifact for a pull request."""

from __future__ import annotations

import json
import os

from . import config, gitutil, memory, registry, schema
from . import impact as imp
from . import rules
from .store import DirStore
from .util import now_iso


def run(a) -> dict:
    repo = a.repo
    base_cfg_text = gitutil.show(repo, a.base_sha, a.config_path)
    cfg = config.load(base_cfg_text)
    reg = registry.load_registry(repo, a.base_sha, cfg)
    mb = gitutil.merge_base(repo, a.base_sha, a.head_sha)
    changed = gitutil.changed_files(repo, a.base_sha, a.head_sha)
    now = a.now or now_iso()
    events = DirStore(a.store_dir).events() if a.store_dir else []
    runs = None
    if a.runs_json and os.path.exists(a.runs_json):
        with open(a.runs_json) as fh:
            runs = json.load(fh)
    mem = memory.build(events, cfg, now, runs=runs, pin=a.weights_pin or None, default_branch=a.default_branch)
    labels = json.loads(a.labels) if a.labels else []
    if not isinstance(labels, list):
        labels = []
    mode = "gate" if not a.mode else (a.mode if a.mode in ("report", "gate") else "report")  # unset = gate, unknown = safe
    inp = rules.Inputs(
        repo=repo, slug=a.slug, pr=int(a.pr), head_sha=a.head_sha, base_sha=a.base_sha, merge_base=mb, event=a.event,
        actor=a.actor or "", head_repo_is_fork=str(a.fork).lower() == "true", labels=[str(x) for x in labels][:100], mode=mode,
        cfg=cfg, registry=reg, head_tree=imp.Tree(repo, a.head_sha), base_tree=imp.Tree(repo, a.base_sha), changed=changed,
        memory=mem, now=now, registry_ref=a.base_sha, base_branch=a.base_branch, default_branch=a.default_branch,
        gate_suites=[x for x in a.gate_suites.split(",") if x] if a.gate_suites else None,
        gate_base_branches=[x for x in a.gate_base_branches.split(",") if x],
        planner_version=a.planner_version or rules.__version__)
    if a.gate_suites == "none":
        inp.gate_suites = []
    body = rules.decide(inp)
    ev = rules.finalize(body, now)
    schema.require_valid(ev)
    return ev


def facts_run(a) -> dict:
    """planner.facts/1 for the Camunda process: everything about the PR, no memory, no decision."""
    from . import facts as facts_mod
    from .util import canonical, sha256_hex
    repo = a.repo
    cfg = config.load(gitutil.show(repo, a.base_sha, a.config_path))
    reg = registry.load_registry(repo, a.base_sha, cfg)
    mb = gitutil.merge_base(repo, a.base_sha, a.head_sha)
    changed = gitutil.changed_files(repo, a.base_sha, a.head_sha)
    labels = json.loads(a.labels) if a.labels else []
    labels = [str(x) for x in labels][:100] if isinstance(labels, list) else []
    mode = "gate" if not a.mode else (a.mode if a.mode in ("report", "gate") else "report")
    gate_suites = None
    if a.gate_suites == "none":
        gate_suites = []
    elif a.gate_suites:
        gate_suites = [x for x in a.gate_suites.split(",") if x]
    f = facts_mod.build(repo, a.slug, int(a.pr), a.head_sha, a.base_sha, mb, a.event, a.actor or "", str(a.fork).lower() == "true", labels,
                        mode, cfg, reg, imp.Tree(repo, a.head_sha), imp.Tree(repo, a.base_sha), changed, a.base_branch, a.default_branch,
                        gate_suites, [x for x in a.gate_base_branches.split(",") if x], a.planner_version or rules.__version__)
    f["labels_hash"] = sha256_hex(canonical(sorted(labels)))[:16]
    errs = schema.validate_facts(f)
    if errs:
        raise schema.Invalid("facts invalid: %s" % "; ".join(errs[:5]))
    return f
