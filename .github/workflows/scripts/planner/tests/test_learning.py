import copy
import json
import os
import shutil
import tempfile
import unittest

from .. import audit, collect, config, learn, memory, schema, store
from ..util import event_id
from . import fixtures as fx_mod
from .helpers import decision

NOW = "2026-11-02T20:00:00Z"
CFG = config.load(None)


def outcome(suite, ended, conclusion="success", minutes=120, event="schedule", branch="main", rid=1, attempt=1, pr=0,
            decision_id=None, sha="a" * 40, flake=False):
    from datetime import datetime, timedelta
    end = datetime.strptime(ended, "%Y-%m-%dT%H:%M:%SZ")
    run = {"id": rid, "status": "completed", "conclusion": conclusion, "event": event, "head_branch": branch, "head_sha": sha,
           "run_attempt": attempt, "run_started_at": (end - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "updated_at": ended, "html_url": "https://github.com/o/r/actions/runs/%d" % rid,
           "pull_requests": [{"number": pr}] if pr else []}
    o = collect.outcome_from_run("o/r", run, suite, decision_id, flake, ended)
    assert schema.validate(o) == [], schema.validate(o)
    return o


class MemoryTest(unittest.TestCase):
    def test_all_and_module_coverage_and_cost(self):
        dec = {"id": "dec_0123456789ab", "type": "decision", "ts": NOW, "impacted": {"modules": ["aws/modules/aurora"]}}
        ev = [outcome("a_tests.yml", "2026-10-28T05:00:00Z", rid=1),
              outcome("a_tests.yml", "2026-10-31T05:00:00Z", rid=2, event="pull_request", branch="feat", pr=9, decision_id=dec["id"]),
              outcome("a_tests.yml", "2026-11-01T05:00:00Z", rid=3, minutes=3),  # label-skipped: not a full run
              dec]
        mem = memory.build(ev, CFG, NOW)
        self.assertAlmostEqual(memory.age_days(mem, "a_tests.yml", None), 5.6, 1)
        self.assertAlmostEqual(memory.age_days(mem, "a_tests.yml", "aws/modules/aurora"), 2.6, 1)  # PR run covers that module
        self.assertIsNone(memory.age_days(mem, "never.yml", None))
        self.assertEqual(mem["suites"]["a_tests.yml"]["median_minutes"], 120.0)

    def test_weights_pin(self):
        w1 = {"id": "wgt_aaaaaaaaaaaa", "type": "weights", "ts": "2026-10-01T00:00:00Z", "pairs": {"m|s": {"weight": 2.0}}, "suites": {}}
        w2 = {"id": "wgt_bbbbbbbbbbbb", "type": "weights", "ts": "2026-10-20T00:00:00Z", "pairs": {"m|s": {"weight": 3.0}}, "suites": {}}
        self.assertEqual(memory.weight(memory.build([w1, w2], CFG, NOW), "m", "s"), 3.0)
        self.assertEqual(memory.weight(memory.build([w1, w2], CFG, NOW, pin="wgt_aaaaaaaaaaaa"), "m", "s"), 2.0)


class EscapeTest(unittest.TestCase):
    def setUp(self):
        self.dec = {"id": "dec_0123456789ab", "pr": 5, "decision": {"x_tests.yml": {"action": "skip"}},
                    "impacted": {"modules": ["aws/modules/aurora"], "consumers": {"aws/modules/aurora": ["x_tests.yml"]}}}
        self.merged = lambda good, bad: [(5, "c" * 40)]

    def run_seq(self, concl, attempts=(1, 1, 1)):
        outs = [outcome("x_tests.yml", "2026-10-%02dT05:00:00Z" % (20 + i), conclusion=c, rid=i + 1, attempt=attempts[i], sha=("%040x" % (i + 1)))
                for i, c in enumerate(concl)]
        return collect.attribute_escapes("o/r", outs, {5: self.dec}, self.merged, CFG, NOW)

    def test_single_failure_is_not_an_escape(self):
        self.assertEqual(self.run_seq(["success", "failure", "success"]), [])

    def test_two_consecutive_failures_are_confirmed_and_attributed(self):
        esc = self.run_seq(["success", "failure", "failure"])
        self.assertEqual(len(esc), 1)
        self.assertEqual(esc[0]["candidates"][0]["module"], "aws/modules/aurora")
        self.assertEqual(esc[0]["candidates"][0]["share"], 1.0)
        self.assertEqual(schema.validate(esc[0]), [])

    def test_failure_on_a_second_attempt_is_confirmed(self):
        esc = self.run_seq(["success", "failure"], attempts=(1, 2))
        self.assertEqual(len(esc), 1)

    def test_no_escape_when_the_suite_ran_or_does_not_consume_the_module(self):
        self.dec["decision"]["x_tests.yml"]["action"] = "run"
        self.assertEqual(self.run_seq(["success", "failure", "failure"]), [])
        self.dec["decision"]["x_tests.yml"]["action"] = "skip"
        self.dec["impacted"]["consumers"] = {"aws/modules/aurora": ["other.yml"]}
        self.assertEqual(self.run_seq(["success", "failure", "failure"]), [])

    def test_share_is_split_and_capped(self):
        decs = {}
        for pr in range(1, 9):
            decs[pr] = dict(self.dec, id="dec_%012x" % pr, pr=pr)
        self.merged = lambda good, bad: [(pr, "c" * 40) for pr in range(1, 9)]
        outs = [outcome("x_tests.yml", "2026-10-%02dT05:00:00Z" % (20 + i), conclusion=c, rid=i + 1, sha=("%040x" % (i + 1)))
                for i, c in enumerate(["success", "failure", "failure"])]
        esc = collect.attribute_escapes("o/r", outs, decs, self.merged, CFG, NOW)[0]
        self.assertEqual(len(esc["candidates"]), 5)
        self.assertAlmostEqual(sum(c["share"] for c in esc["candidates"]), 1.0, 2)
        self.assertLess(esc["confidence"], 1.0)


class LearnTest(unittest.TestCase):
    def events(self):
        dec = {"id": "dec_0123456789ab", "type": "decision", "ts": "2026-10-25T00:00:00Z", "pr": 5, "repo": "o/r",
               "impacted": {"modules": ["aws/modules/aurora"], "consumers": {}}}
        ev = [dec]
        for i in range(10):
            ev.append(outcome("x_tests.yml", "2026-10-%02dT05:00:00Z" % (10 + i), conclusion="failure" if i == 3 else "success",
                              rid=100 + i, event="pull_request", branch="f", pr=5, decision_id=dec["id"], sha=("%040x" % (100 + i))))
        return ev

    def test_weights_have_evidence_and_are_bounded(self):
        w = learn.compute(self.events(), CFG, NOW, None, "o/r")
        self.assertEqual(schema.validate(w), [])
        p = w["pairs"]["aws/modules/aurora|x_tests.yml"]
        self.assertGreater(p["risk"], 0.05)
        self.assertTrue(p["evidence"])
        self.assertLessEqual(p["weight"], 1.5, "first step is bounded")
        w2 = learn.compute(self.events(), CFG, NOW, w, "o/r")
        self.assertGreaterEqual(w2["pairs"]["aws/modules/aurora|x_tests.yml"]["weight"], p["weight"])
        self.assertEqual(w2["prev"], w["id"])
        self.assertIn("pair weights changed", w["diff"])

    def test_flakes_are_not_failures(self):
        ev = self.events()
        for e in ev:
            if e["type"] == "outcome" and e["conclusion"] == "failure":
                e["flake_hint"] = True
        w = learn.compute(ev, CFG, NOW, None, "o/r")
        self.assertEqual(w["pairs"]["aws/modules/aurora|x_tests.yml"]["fail"], 0.0)

    def test_old_events_decay_and_leave_the_window(self):
        ev = self.events()
        w_now = learn.compute(ev, CFG, NOW, None, "o/r")
        w_later = learn.compute(ev, CFG, "2027-06-01T00:00:00Z", None, "o/r")
        self.assertEqual(w_later["pairs"], {})
        self.assertTrue(w_now["pairs"])


class LoopTest(unittest.TestCase):
    """An escape recorded in the store changes the next decision for the same diff."""

    @classmethod
    def setUpClass(cls):
        cls.fx = fx_mod.make_repo()
        cls.head = fx_mod.commit_change(cls.fx, "rename", {"aws/modules/aurora/outputs.tf": lambda o: o.replace("aurora_endpoint", "aurora_writer_endpoint")})
        cls.patch = fx_mod.commit_change(cls.fx, "patch", {"aws/modules/aurora/variables.tf": lambda o: o.replace('"17.9"', '"17.10"')})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fx["repo"], ignore_errors=True)

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.ds = store.DirStore(self.dir)
        for i, (suite, ended) in enumerate([("aws_eks_single_region_tests.yml", "2026-10-28T02:00:00Z"),
                                            ("aws_ecs_single_region_fargate_tests.yml", "2026-10-28T20:00:00Z"),
                                            ("aws_ecs_dual_region_fargate_tests.yml", "2026-11-01T20:00:00Z")]):
            o = outcome(suite, ended, rid=i + 1, sha=("%040x" % (i + 1)))
            self.ds.write(store.path_for(o), o)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def rotation(self, head=None):
        d = decision(self.fx, head or self.head, store_dir=self.dir)
        return d

    def test_escape_changes_the_next_decision(self):
        d1 = self.rotation()
        self.assertEqual(d1["rotation"]["picked"], "aws_eks_single_region_tests.yml")  # oldest last full success
        # a PR that skipped ecs-single touched aurora; the next two scheduled runs of ecs-single fail
        skipped = decision(self.fx, self.patch, store_dir=self.dir, now="2026-10-30T00:00:00Z", pr=5)
        skipped["decision"]["aws_ecs_single_region_fargate_tests.yml"] = {"action": "skip", "enforced": False, "layer": "L4", "reasons": ["x"]}
        skipped["impacted"]["consumers"]["aws/modules/aurora"] = ["aws_ecs_single_region_fargate_tests.yml", "aws_modules_eks_rds_os_tests.yml"]
        self.ds.write(store.path_for(skipped), skipped)
        outs = [outcome("aws_ecs_single_region_fargate_tests.yml", "2026-10-29T20:00:00Z", rid=11, sha="1" * 40),
                outcome("aws_ecs_single_region_fargate_tests.yml", "2026-10-31T20:00:00Z", conclusion="failure", rid=12, sha="2" * 40),
                outcome("aws_ecs_single_region_fargate_tests.yml", "2026-11-01T20:00:00Z", conclusion="failure", rid=13, sha="3" * 40)]
        esc = collect.attribute_escapes("o/r", outs, {5: skipped}, lambda g, b: [(5, "c" * 40)], CFG, "2026-11-02T00:00:00Z")
        self.assertEqual(len(esc), 1)
        self.ds.write(store.path_for(esc[0]), esc[0])
        w = learn.compute(self.ds.events(), CFG, NOW, None, "o/r")
        self.ds.write(store.path_for(w), w)
        pair = w["pairs"]["aws/modules/aurora|aws_ecs_single_region_fargate_tests.yml"]
        self.assertGreater(pair["weight"], 1.0)
        self.assertIn(esc[0]["id"], pair["evidence"], "every learned number traces to records")
        d2 = self.rotation()
        self.assertEqual(d2["rotation"]["picked"], "aws_ecs_single_region_fargate_tests.yml")
        self.assertEqual(d2["weights_version"], w["id"])
        self.assertNotEqual(d1["rotation"]["picked"], d2["rotation"]["picked"])
        # the circuit breaker needs two escapes: add a second one and the suite becomes mandatory
        esc2 = copy.deepcopy(esc[0])
        esc2["id"] = "esc_ffffffffffff"
        esc2["ts"] = "2026-11-02T01:00:00Z"
        self.ds.write(store.path_for(esc2), esc2)
        d3 = decision(self.fx, self.patch, store_dir=self.dir)
        self.assertEqual(d3["decision"]["aws_ecs_single_region_fargate_tests.yml"]["action"] if "aws_ecs_single_region_fargate_tests.yml" in d3["decision"] else "run", "run")
        self.assertIn("R7b", {r["rule"] for r in d3["rules_fired"]})

    def test_audit_queries_and_verify_detects_tampering(self):
        d = decision(self.fx, self.head, store_dir=self.dir)
        self.ds.write(store.path_for(d), d)
        ev = self.ds.events()
        self.assertEqual(audit.verify(ev), [])
        self.assertEqual(len(audit.select(ev, pr=7, types={"decision"})), 1)
        self.assertEqual(len(audit.select(ev, module="aurora", types={"decision"})), 1)
        self.assertEqual(len(audit.select(ev, suite="eks_single", types={"outcome"})), 1)
        rel = [e for e in ev if e["type"] == "decision"][0]["_path"]
        path = os.path.join(self.dir, rel)
        tampered = json.load(open(path))
        tampered["mode"] = "report"
        json.dump(tampered, open(path, "w"))
        problems = audit.verify(self.ds.events())
        self.assertTrue(any("does not match its content" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
