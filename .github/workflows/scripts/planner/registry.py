"""Suite registry: which workflows exist, what triggers them, which layer they prove.

Read from the PR's **base** commit so a pull request cannot change how it is judged.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from . import gitutil, yamlio
from .util import suite_name


@dataclass
class Suite:
    path: str
    name: str
    display: str
    layer: str
    kind: str
    events: list
    pr_event: str | None
    paths: list
    paths_ignore: list
    crons: list
    text: str = ""
    gated_by_triage: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def is_call_only(self) -> bool:
        return set(self.events) <= {"workflow_call"}


def classify(name: str, cfg: dict):
    for rx, layer, kind in cfg["layers"]:
        if re.search(rx, name):
            return layer, kind
    return "ops", "unclassified"


def _as_list(x):
    if x is None:
        return []
    return list(x) if isinstance(x, list) else [x]


def parse_suite(path: str, text: str, cfg: dict) -> Suite:
    data = yamlio.loads(text) or {}
    on = data.get("on", data.get(True))
    if isinstance(on, str):
        on = {on: None}
    elif isinstance(on, list):
        on = {k: None for k in on}
    on = on or {}
    pr_event = next((e for e in ("pull_request", "pull_request_target") if e in on), None)
    cfg_pr = on.get(pr_event) if pr_event else None
    paths = _as_list(cfg_pr.get("paths")) if isinstance(cfg_pr, dict) else []
    ignore = _as_list(cfg_pr.get("paths-ignore")) if isinstance(cfg_pr, dict) else []
    crons = [s["cron"] for s in _as_list(on.get("schedule")) if isinstance(s, dict) and "cron" in s]
    name = suite_name(path)
    layer, kind = classify(name, cfg)
    return Suite(path=path, name=name, display=str(data.get("name", name)), layer=layer, kind=kind,
                 events=list(on), pr_event=pr_event, paths=[str(p) for p in paths],
                 paths_ignore=[str(p) for p in ignore], crons=crons, text=text,
                 gated_by_triage="internal-triage-skip" in text)


def load_registry(repo: str, ref: str, cfg: dict) -> dict:
    """name -> Suite for every workflow file at `ref`."""
    reg = {}
    for path in gitutil.ls_tree(repo, ref):
        if re.match(r"^\.github/workflows/[^/]+\.ya?ml$", path):
            try:
                s = parse_suite(path, gitutil.show(repo, ref, path), cfg)
            except Exception:
                continue
            reg[s.name] = s
    return reg
