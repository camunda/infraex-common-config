"""The DMN decision tables and the Python policy must make the same decision.

The DMN is evaluated with feelin (a JavaScript FEEL engine) through camunda/eval_dmn.js. That is not the
Zeebe engine, so this proves the tables and expressions are consistent with the reference policy and free
of FEEL errors, not that a cluster accepts them. Skipped when node or the npm packages are missing
(`cd planner/camunda && npm ci`).
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from .. import config, facts as facts_mod, gitutil, impact as imp, memory, policy, registry
from . import fixtures as fx_mod
from .helpers import run_rec, runs_json

HERE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "camunda")
NODE = shutil.which("node")
READY = NODE and os.path.isdir(os.path.join(HERE, "node_modules", "feelin"))


def build_facts(fx, head, labels=(), mode="gate", fork=False, base_branch="main", gate_suites=None):
    cfg = config.load(None)
    reg = registry.load_registry(fx["repo"], fx["base"], cfg)
    mb = gitutil.merge_base(fx["repo"], fx["base"], head)
    changed = gitutil.changed_files(fx["repo"], fx["base"], head)
    f = facts_mod.build(fx["repo"], "o/r", 7, head, fx["base"], mb, "pull_request", "dev", fork, list(labels), mode, cfg, reg,
                        imp.Tree(fx["repo"], head), imp.Tree(fx["repo"], fx["base"]), changed, base_branch, "main",
                        gate_suites, ["main"], "t")
    f["labels_hash"] = "x"
    return f


def norm_py(d):
    return {
        "mode": d["mode"],
        "decision": {k: (v["action"], v["enforced"], v["layer"], v["reasons"]) for k, v in d["decision"].items()},
        "picked": (d["rotation"] or {}).get("picked"),
        "rot_reasons": (d["rotation"] or {}).get("reasons"),
        "rot_candidates": [c["suite"] for c in (d["rotation"] or {}).get("candidates", [])],
        "fired": sorted((r["rule"], r["subject"]) for r in d["rules_fired"]),
        "applied": d["overrides"]["applied"], "conflicts": d["overrides"]["conflicts"],
        "safety": {k: (v["next_scheduled"], v["last_full_success_at"]) for k, v in d["safety_net"].items()},
    }


def norm_dmn(p):
    return {
        "mode": p["mode"],
        "decision": {d["suite"]: (d["action"], d["enforced"], d["layer"], d["reasons"]) for d in p["decision"]},
        "picked": (p["rotation"] or {}).get("picked"),
        "rot_reasons": (p["rotation"] or {}).get("reasons"),
        "rot_candidates": [c["suite"] for c in (p["rotation"] or {}).get("candidates", [])],
        "fired": sorted((r["rule"], r["subject"]) for r in p["rulesFired"]),
        "applied": p["overrides"]["applied"], "conflicts": p["overrides"]["conflicts"],
        "safety": {s["suite"]: (s["next_scheduled"], s["last_full_success_at"]) for s in p["safetyNet"]},
    }


@unittest.skipUnless(READY, "needs node and `npm ci` in planner/camunda")
class DmnConformanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = fx_mod.make_repo()
        cls.tmp = tempfile.mkdtemp()
        sub = lambda a, b: (lambda old: old.replace(a, b))
        v = "aws/modules/aurora/variables.tf"
        cls.heads = {
            "patch": fx_mod.commit_change(cls.fx, "patch", {v: sub('"17.9"', '"17.10"')}),
            "major": fx_mod.commit_change(cls.fx, "major", {v: sub('"17.9"', '"18.0"'),
                                                             "aws/kubernetes/eks-single-region/terraform/cluster/db.tf": sub('"17.9"', '"18.0"')}),
            "rename": fx_mod.commit_change(cls.fx, "rename", {"aws/modules/aurora/outputs.tf": sub("aurora_endpoint", "aurora_writer_endpoint")}),
            "ecs_root": fx_mod.commit_change(cls.fx, "ecs_root", {"aws/containers/ecs-single-region-fargate/terraform/cluster/main.tf": 'resource "a" "c" {}\n'}),
            "vpn": fx_mod.commit_change(cls.fx, "vpn", {"aws/modules/vpn/main.tf": 'resource "x" "v2" {}\n'}),
            "docs": fx_mod.commit_change(cls.fx, "docs", {"README.md": "changed\n"}),
            "action": fx_mod.commit_change(cls.fx, "action", {".github/actions/shared/action.yml": "name: shared2\nruns:\n  using: composite\n  steps: []\n"}),
            "tool": fx_mod.commit_change(cls.fx, "tool", {".tool-versions": "terraform 1.10.0\n"}),
            "judge": fx_mod.commit_change(cls.fx, "judge", {".github/planner-config.json": '{"gate_suites": []}\n', v: sub('"17.9"', '"17.10"')}),
            "ops": fx_mod.commit_change(cls.fx, "ops", {".github/workflows/internal_global_maintenance.yml": lambda old: old + "# changed\n"}),  # found on a real engine: an ops suite changed itself
            "newwf": fx_mod.commit_change(cls.fx, "newwf", {".github/workflows/aws_new_tests.yml": fx_mod.workflow("New", ["aws/**"])}),
        }

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fx["repo"], ignore_errors=True)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def memories(self, f):
        names = sorted(f["suites"])
        def mem(**kw):
            m = {"now": "2026-11-02T20:00:00Z", "weights_version": "none", "weights": {}, "breaker": {}, "suites": {}}
            for n in names:
                m["suites"][n] = {"age_all": None, "age_modules": {}, "cost_min": None, "last_full_success_at": None, "source": "none"}
            m.update(kw)
            return m
        empty = mem()
        aged = mem()
        for i, n in enumerate(names):
            aged["suites"][n].update({"age_all": 2.0 + i * 1.5, "cost_min": 60.0 + (i % 3) * 30, "last_full_success_at": "2026-10-28T05:00:00Z", "source": "events"})
        aged["suites"][names[0]]["age_modules"] = {"aws/modules/aurora": 0.5}
        weighted = json.loads(json.dumps(aged))
        weighted["weights_version"] = "wgt_aaaaaaaaaaaa"
        weighted["weights"] = {"aws/modules/aurora|%s" % names[-1]: 3.0, "aws/modules/vpn|%s" % names[0]: 2.5}
        broken = json.loads(json.dumps(aged))
        for n in names:
            broken["breaker"]["aws/modules/aurora|%s" % n] = {"mass": 2.0, "evidence": ["esc_0123456789ab"]}
        tie = json.loads(json.dumps(empty))
        for n in names:  # equal ages: the cheaper suite wins the tie
            tie["suites"][n].update({"age_all": 5.0, "cost_min": 100.0 - 10 * names.index(n)})
        return {"empty": empty, "aged": aged, "weighted": weighted, "breaker": broken, "tie": tie}

    def test_policy_and_dmn_agree(self):
        cases, inputs = [], []
        variants = [dict(), dict(labels=["planner:run-all"]), dict(labels=["run_aws_eks_single_region_tests"]),
                    dict(labels=["skip_aws_modules_eks_rds_os_tests"]), dict(labels=["skip_all"]),
                    dict(labels=["skip_aws_modules_eks_rds_os_tests", "run_aws_modules_eks_rds_os_tests"]),
                    dict(fork=True), dict(base_branch="stable/8.9"), dict(mode="report"), dict(mode="bogus"),
                    dict(gate_suites=["aws_ecs_dual_region_fargate_tests.yml", "aws_eks_single_region_tests.yml"])]
        for name, head in self.heads.items():
            for var in variants:
                f = build_facts(self.fx, head, **var)
                for mname, m in self.memories(f).items():
                    cases.append((name, repr(var), mname, f, m))
                    inputs.append({"facts": f, "memory": m})
        path = os.path.join(self.tmp, "in.json")
        with open(path, "w") as fh:
            json.dump(inputs, fh)
        p = subprocess.run([NODE, os.path.join(HERE, "eval_dmn.js"), os.path.join(HERE, "ci-test-planner.dmn"), path, "plan"],
                           capture_output=True, text=True, timeout=600)
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        out = json.loads(p.stdout)
        self.assertEqual(len(out), len(cases))
        bad = []
        for (name, var, mname, f, m), res in zip(cases, out):
            self.assertEqual(res["warnings"], [], "FEEL warnings in %s %s %s: %s" % (name, var, mname, res["warnings"][:2]))
            want, got = norm_py(policy.plan(f, m)), norm_dmn(res["value"])
            if want != got:
                diff = {k: (want[k], got[k]) for k in want if want[k] != got[k]}
                bad.append("%s %s %s: %s" % (name, var, mname, json.dumps(diff)[:600]))
        self.assertEqual(bad, [], "\n".join(bad[:5]) + "\n(%d of %d cases differ)" % (len(bad), len(cases)))


if __name__ == "__main__":
    unittest.main()
