"""`planner burnin`: the report-mode guarantee checked against what the suites did (fake GitHub API)."""

import argparse
import json
import os
import shutil
import tempfile
import unittest

from .. import burnin, camunda, publish
from ..gh import Gh
from .fakegh import FakeGh
from .test_camunda_flow import process_variables, sample_facts

SKIPPED = "aws_modules_eks_rds_os_create_destruct_tests.yml"  # the plan skips it (enforced in gate mode)
RUN = "aws_modules_eks_rds_os_tests.yml"  # the plan runs it


def gate_log(skip, reason):
    return ("2026-11-26T10:00:00.0000000Z ##[group]Run set -uo pipefail\n"
            "2026-11-26T10:00:00.0000000Z   echo \"::warning::planner-gate failed, running the suite as before\"\n"
            "2026-11-26T10:00:01.0000000Z planner-gate: %s\n2026-11-26T10:00:01.0000000Z should_skip=%s\n" % (reason, skip))


class BurninTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facts, cls.fx, cls.head = sample_facts()
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fx["repo"], ignore_errors=True)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def fake(self, mode, suites):
        """suites: name -> (work job conclusion, run conclusion, planner-gate answer or None for no log)."""
        facts = dict(self.facts, requested_mode=mode)
        plan = camunda.plan_from_result(facts, process_variables(facts))
        path = os.path.join(self.tmp, "plan-%s.json" % mode)
        json.dump(plan, open(path, "w"))
        fake = FakeGh()
        fake.pulls[self.facts["pr"]] = self.head
        publish.run(argparse.Namespace(plan=path, repo="o/r", pr=str(self.facts["pr"]), head_sha=self.head, summary_file="",
                                       no_comment=True, no_status=True), gh=Gh(fake))
        for i, (name, (job, concl, gate)) in enumerate(suites.items()):
            rid = 100 + i
            fake.runs[name] = [{"id": rid, "head_sha": self.head, "status": "completed", "conclusion": concl, "run_attempt": 1, "html_url": "u"}]
            fake.jobs[rid] = [{"id": rid * 10, "name": "triage", "conclusion": "success"}, {"id": rid * 10 + 1, "name": "test (a)", "conclusion": job}]
            if gate is not None:
                fake.logs[rid * 10] = gate_log(*gate)
        return fake

    def run_burnin(self, fake):
        out = os.path.join(self.tmp, "burnin.json")
        rc = burnin.run(argparse.Namespace(repo="o/r", pr=str(self.facts["pr"]), head_sha="", out=out, summary_file=""), gh=Gh(fake))
        return rc, json.load(open(out)) if rc != 2 else None

    def test_report_mode_where_everything_ran_passes(self):
        fake = self.fake("report", {SKIPPED: ("success", "success", ("false", "report mode, nothing is skipped (plan AbC123 says skip)")),
                                    RUN: ("failure", "failure", ("false", "report mode, nothing is skipped (plan AbC123 says run)"))})
        rc, res = self.run_burnin(fake)
        self.assertEqual(rc, 0)
        self.assertEqual(res["mode"], "report")
        row = {r["suite"]: r for r in res["rows"]}
        self.assertEqual(row[SKIPPED]["plan"], "skip")
        self.assertFalse(row[SKIPPED]["enforced"])
        self.assertIn("plan would skip; ran: success", row[SKIPPED]["note"])
        self.assertEqual(row[RUN]["execution"], "ran")

    def test_a_skip_in_report_mode_is_a_violation(self):
        fake = self.fake("report", {SKIPPED: ("skipped", "success", ("true", "plan AbC123: skip (x)"))})
        rc, res = self.run_burnin(fake)
        self.assertEqual(rc, 1)
        self.assertEqual(res["violations"], [SKIPPED])

    def test_unexplained_skip_without_a_gate_log_is_flagged(self):
        fake = self.fake("report", {SKIPPED: ("skipped", "success", None)})
        self.assertEqual(self.run_burnin(fake)[0], 1)
        fake.pull_labels[self.facts["pr"]] = ["skip_aws_modules_eks_rds_os_create_destruct_tests"]
        rc, res = self.run_burnin(fake)
        self.assertEqual(rc, 0, "a skip label explains it")
        self.assertIn("the label", res["rows"][0]["note"] if res["rows"][0]["suite"] == SKIPPED else res["rows"][1]["note"])

    def test_gate_mode_enforced_skip_is_fine_but_skipping_a_run_suite_is_not(self):
        fake = self.fake("gate", {SKIPPED: ("skipped", "success", ("true", "plan AbC123: skip (x)")),
                                  RUN: ("success", "success", ("false", "plan AbC123: run"))})
        self.assertEqual(self.run_burnin(fake)[0], 0)
        fake = self.fake("gate", {RUN: ("skipped", "success", ("true", "plan AbC123: skip (x)"))})
        rc, res = self.run_burnin(fake)
        self.assertEqual((rc, res["violations"]), (1, [RUN]))

    def test_rosa_report_success_after_a_skip_is_not_a_run(self):
        """ROSA dual region: `Report success` runs and succeeds when triage skipped every working job."""
        run = {"status": "completed", "conclusion": "success"}
        skipped = [{"name": n, "conclusion": "skipped"} for n in ("Define Matrix", "Prepare clusters", "Read kube configs from matrix",
                                                                    "Run integration tests", "Retry Tests in case of failure",
                                                                    "Cleanup ROSA clusters", "Report failures")]
        jobs = [{"name": "triage", "conclusion": "success"}] + skipped + [{"name": "Report success", "conclusion": "success"}]
        self.assertEqual(burnin.execution(run, jobs), "skipped")
        jobs[1] = {"name": "Define Matrix", "conclusion": "success"}
        self.assertEqual(burnin.execution(run, jobs), "ran")
        # a job whose name only starts with the letters is still work
        self.assertEqual(burnin.execution(run, [{"name": "reporting-suite", "conclusion": "success"}]), "ran")
        self.assertEqual(burnin.execution(run, [{"name": "report-failure", "conclusion": "success"}]), "skipped")

    def test_rosa_shape_skip_without_gate_answer_is_flagged(self):
        fake = self.fake("report", {SKIPPED: ("skipped", "success", None)})
        rid = 100
        fake.jobs[rid].append({"id": rid * 10 + 2, "name": "Report success", "conclusion": "success"})
        rc, res = self.run_burnin(fake)
        self.assertEqual(rc, 1, "a report job that ran does not hide the skip")
        row = {r["suite"]: r for r in res["rows"]}[SKIPPED]
        self.assertEqual(row["execution"], "skipped")

    def test_gate_answer_parsing(self):
        self.assertEqual(burnin.gate_answer(gate_log("false", "report mode, nothing is skipped (no camunda plan)")),
                         ("false", "report mode, nothing is skipped (no camunda plan)"))
        self.assertEqual(burnin.gate_answer("planner-gate: not a same-repository pull_request run, nothing to gate\nshould_skip=false\n"), (None, None))
        self.assertEqual(burnin.gate_answer(""), (None, None))

    def test_no_plan_is_an_error(self):
        fake = FakeGh()
        fake.pulls[self.facts["pr"]] = self.head
        self.assertEqual(self.run_burnin(fake)[0], 2)


if __name__ == "__main__":
    unittest.main()
