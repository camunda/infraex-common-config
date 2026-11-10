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


def decide(inp: Inputs) -> dict:
    """Reference implementation without Camunda: facts, then the Python policy over the rich memory."""
    from . import facts as facts_mod, policy
    f = facts_mod.build(inp.repo, inp.slug, inp.pr, inp.head_sha, inp.base_sha, inp.merge_base, inp.event, inp.actor,
                        inp.head_repo_is_fork, inp.labels, inp.mode, inp.cfg, inp.registry, inp.head_tree, inp.base_tree,
                        inp.changed, inp.base_branch, inp.default_branch, inp.gate_suites, inp.gate_base_branches, inp.planner_version)
    f["labels_hash"] = sha256_hex(canonical(sorted(inp.labels)))[:16]
    cm = memory.compact(inp.memory, f)
    return policy.plan(f, cm)


def finalize(decision: dict, ts: str) -> dict:
    """Add id and timestamp. The id hashes the whole body, so a retried write is idempotent."""
    body = dict(decision)
    body["ts"] = ts
    body["id"] = event_id("dec", body)
    return body
