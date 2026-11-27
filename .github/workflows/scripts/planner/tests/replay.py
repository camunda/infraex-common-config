"""Replay real past pull requests of camunda-deployment-references through the planner.

usage: python3 -m planner.tests.replay <repo clone> <runs.jsonl> <out dir>

The suite registry is read at each PR's own base commit (so suites that did not exist then are
not considered), memory comes from the sampled Actions history (scheduled/dispatch runs only,
as it would after PRs stop running every suite). Slow: old blobs are fetched on demand.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .. import decide, render, schema, store
from .. import gitutil

PRS = [
    ("e127b109", "Aurora engine major 17.9 -> 18.3 (renovate pins in 6 files)"),
    ("e271f7af", "ECS dual-region root change plus aurora-global variables"),
    ("af690ea9", "eks-cluster minor-grouped bump with .tool-versions"),
    ("62ef7c68", "rosa-hcp provider skip plus shared cleanup action"),
    ("449b5d42", "grouped Renovate minor update, 81 files"),
]


def run(repo: str, runs_jsonl: str, out: str, prs=PRS, now: str = "2026-11-02T20:00:00Z") -> list:
    os.makedirs(out, exist_ok=True)
    with open(runs_jsonl) as fh:
        rows = [json.loads(l) for l in fh if l.strip()]
    runs_path = os.path.join(out, "runs-fallback.json")
    with open(runs_path, "w") as fh:
        json.dump([r for r in rows if r.get("event") != "pull_request"], fh)
    results = []
    for short, title in prs:
        head = gitutil.head_sha(repo, short)
        base = gitutil.head_sha(repo, short + "^")
        ns = argparse.Namespace(
            repo=repo, slug="camunda/camunda-deployment-references", pr=0, head_sha=head, base_sha=base, event="pull_request",
            actor="replay", fork="false", labels="[]", mode="gate", base_branch="main", default_branch="main",
            gate_base_branches="main", gate_suites="", weights_pin="", config_path=".github/planner-config.json",
            store_dir="", runs_json=runs_path, planner_version="replay", now=now)
        d = decide.run(ns)
        ds = store.DirStore(os.path.join(out, "store"))
        ds.write(store.path_for(d), d)
        with open(os.path.join(out, "%s.md" % short), "w") as fh:
            fh.write("# Replay of %s: %s\n\n" % (short, title) + render.summary_md(d))
        trig = d["impacted"]["suites_path_triggered"]
        expensive = [n for n in trig if d["decision"].get(n, {}).get("layer") in ("L3", "L4")]
        ran = [n for n, v in d["decision"].items() if v["action"] == "run" and v["layer"] in ("L3", "L4")]
        results.append({"pr": short, "title": title, "changed": d["changed_files"]["count"], "path_triggered_cloud": len(expensive),
                        "planned_cloud": len(ran), "rules": sorted({r["rule"] for r in d["rules_fired"]}),
                        "rotation": (d["rotation"] or {}).get("picked"), "planned": sorted(ran), "decision_id": d["id"]})
    with open(os.path.join(out, "summary.json"), "w") as fh:
        json.dump(results, fh, indent=1)
    return results


if __name__ == "__main__":
    res = run(sys.argv[1], sys.argv[2], sys.argv[3])
    for r in res:
        print("%s: %d files, cloud suites path-triggered %d -> planned %d, rules %s, rotation %s" % (
            r["pr"], r["changed"], r["path_triggered_cloud"], r["planned_cloud"], ",".join(r["rules"]), r["rotation"]))
