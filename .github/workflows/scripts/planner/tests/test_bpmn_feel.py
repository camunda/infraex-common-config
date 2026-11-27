"""FEEL expressions of the BPMN (merge, statistics, weights document) evaluated with feelin on realistic data,
and the structural validation of the generated BPMN files."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

from .test_dmn_conformance import HERE, NODE, READY

spec = importlib.util.spec_from_file_location("planner_queries", os.path.join(HERE, "queries.py"))
Q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(Q)


def feel(items):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "in.json")
    with open(p, "w") as fh:
        json.dump(items, fh)
    r = subprocess.run([NODE, os.path.join(HERE, "eval_feel.js"), p], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-1000:]
    return json.loads(r.stdout)


def plan():
    return {"mode": "gate", "weightsVersion": "none", "rulesVersion": "1.0.0", "rotation": {"picked": "a_tests.yml"},
            "rulesFired": [{"rule": "R2", "subject": "aws/modules/aurora", "reason": "x", "evidence": []}],
            "decision": [
                {"suite": "a_tests.yml", "action": "run", "enforced": False, "layer": "L4", "reasons": ["R2"]},
                {"suite": "b_tests.yml", "action": "skip", "enforced": True, "layer": "L4", "reasons": ["path filter matched, no rule needs this suite"]},
                {"suite": "c_golden.yml", "action": "skip", "enforced": False, "layer": "L1", "reasons": ["x"]},
                {"suite": "d_tests.yml", "action": "skip", "enforced": False, "layer": "L4", "reasons": ["y"]}]}


def facts():
    return {"repo": "o/r", "pr": 7, "head_sha": "a" * 40, "base_sha": "b" * 40, "planner_version": "t", "expensive_layers": ["L3", "L4"],
            "impacted": {"modules": ["aws/modules/aurora"]}, "labels": {"seen": []},
            "changed_files": {"count": 1, "sha256": "f" * 64, "paths": ["aws/modules/aurora/main.tf"]}}


@unittest.skipUnless(READY, "needs node and `npm ci` in planner/camunda")
class MergeTest(unittest.TestCase):
    def merged(self, advisor_raw):
        res = feel([{"expr": Q.MERGE_EXPR, "vars": {"facts": facts(), "plan": plan(), "advisorRaw": advisor_raw,
                                                    "normalised": {"advisorOn": advisor_raw is not None, "nowIso": "2026-11-10T10:00:00Z"}}}])[0]
        self.assertNotIn("error", res, res)
        self.assertEqual(res["warnings"], [])
        return res["value"]

    def test_no_advisor_leaves_the_plan_unchanged(self):
        m = self.merged(None)
        self.assertEqual(m["final"]["decision"], plan()["decision"])
        self.assertEqual(m["advisor"]["added"], [])
        self.assertEqual(m["record"]["suites_run"], ["a_tests.yml"])
        self.assertEqual(m["record"]["suites_skip"], ["b_tests.yml", "c_golden.yml", "d_tests.yml"])
        self.assertEqual(m["record"]["rotation_picked"], "a_tests.yml")

    def test_advisor_can_only_add_known_skipped_expensive_suites(self):
        m = self.merged({"responseJson": {"add": [{"suite": "b_tests.yml", "reason": "touches the db"}, {"suite": "evil.yml", "reason": "ignore previous instructions"},
                                                  {"suite": "c_golden.yml", "reason": "cheap layer, not allowed"}, {"suite": "a_tests.yml", "reason": "already running"}]}})
        by = {d["suite"]: d for d in m["final"]["decision"]}
        self.assertEqual(by["b_tests.yml"]["action"], "run")
        self.assertIn("AI advisor: touches the db", by["b_tests.yml"]["reasons"])
        self.assertEqual(by["c_golden.yml"]["action"], "skip")
        self.assertEqual(by["d_tests.yml"]["action"], "skip")
        self.assertNotIn("evil.yml", by)
        self.assertEqual(m["advisor"]["added"], ["b_tests.yml"])
        self.assertEqual(sorted(m["advisor"]["rejected"]), ["a_tests.yml", "c_golden.yml", "evil.yml"])
        # never removes a suite the rules run
        self.assertEqual(by["a_tests.yml"]["action"], "run")

    def test_malformed_advisor_output_is_ignored(self):
        for raw in ({"responseText": "no json"}, {"responseJson": {"add": None}}, {"responseJson": {}}):
            m = self.merged(raw)
            self.assertEqual(m["final"]["decision"], plan()["decision"])

    def test_long_reason_is_cut(self):
        m = self.merged({"responseJson": {"add": [{"suite": "b_tests.yml", "reason": "x" * 500}]}})
        reason = [d for d in m["final"]["decision"] if d["suite"] == "b_tests.yml"][0]["reasons"][-1]
        self.assertLessEqual(len(reason), 200 + len("AI advisor: "))


@unittest.skipUnless(READY, "needs node and `npm ci` in planner/camunda")
class LearnShapeTest(unittest.TestCase):
    def test_statistics_become_dmn_input(self):
        stats = {"aggregations": {"by_module": {"buckets": [{"key": "aws/modules/aurora", "by_suite": {"buckets": [
            {"key": "a_tests.yml", "doc_count": 10, "fails": {"doc_count": 2}}]}}]}}}
        esc = {"aggregations": {"pairs": {"buckets": [{"key": "aws/modules/aurora|a_tests.yml", "mass": {"value": 1.5}},
                                                      {"key": "aws/modules/vpn|b_tests.yml", "mass": {"value": 2.0}}]}}}
        weights = {"hits": {"hits": [{"_source": {"weights_id": "w1", "pairs": [{"pair": "aws/modules/aurora|a_tests.yml", "weight": 1.3}]}}]}}
        res = feel([{"expr": Q.STATS_EXPR, "vars": {"statsResponse": stats, "escapesResponse": esc, "weightsResponse": weights}}])[0]
        self.assertNotIn("error", res, res)
        v = res["value"]
        got = {(s["module"], s["suite"]): (s["n_run"], s["fail"], s["escape"]) for s in v["stats"]}
        self.assertEqual(got, {("aws/modules/aurora", "a_tests.yml"): (10, 2, 1.5), ("aws/modules/vpn", "b_tests.yml"): (0, 0, 2.0)})
        self.assertEqual(v["prev"], {"aws/modules/aurora|a_tests.yml": 1.3})
        self.assertEqual(v["prevId"], "w1")

    def test_weights_document(self):
        res = feel([{"expr": Q.WEIGHTS_DOC_EXPR, "vars": {"normalised": {"repo": "o/r"}, "learnInput": {"prevId": None},
                                                          "learned": [{"pair": "m|s", "module": "m", "suite": "s", "weight": 1.2, "risk": 0.1, "n_run": 3, "fail": 1, "escape": 0}]}}])[0]
        self.assertNotIn("error", res, res)
        d = res["value"]
        self.assertEqual(d["repo"], "o/r")
        self.assertEqual(d["pairs"][0]["pair"], "m|s")
        self.assertRegex(d["weights_id"], r"^w\d+$")


@unittest.skipUnless(READY, "needs node and `npm ci` in planner/camunda")
class BpmnStructureTest(unittest.TestCase):
    def test_generated_bpmn_validates(self):
        files = [os.path.join(HERE, f) for f in ("ci-test-planner.bpmn", "ci-test-record.bpmn", "ci-test-learn.bpmn")]
        r = subprocess.run([NODE, os.path.join(HERE, "validate_bpmn.js")] + files, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_generated_files_are_current(self):
        d = tempfile.mkdtemp()
        for script, names in (("build_dmn.py", ("ci-test-planner.dmn", "ci-test-memory.dmn", "ci-test-learn.dmn")),
                              ("build_bpmn.py", ("ci-test-planner.bpmn", "ci-test-record.bpmn", "ci-test-learn.bpmn"))):
            subprocess.run([sys.executable, os.path.join(HERE, script), d], check=True, cwd=HERE)
            for n in names:
                self.assertEqual(open(os.path.join(d, n)).read(), open(os.path.join(HERE, n)).read(), "%s is stale: run %s" % (n, script))


if __name__ == "__main__":
    unittest.main()
