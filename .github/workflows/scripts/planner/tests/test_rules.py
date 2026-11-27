import json
import os
import shutil
import tempfile
import unittest

from . import fixtures as fx_mod
from .helpers import decision, run_rec, runs_json

CLOUD = ("L3", "L4")


def cloud_run(d):
    return {n for n, v in d["decision"].items() if v["action"] == "run" and v["layer"] in CLOUD}


def cloud_skip(d):
    return {n for n, v in d["decision"].items() if v["action"] == "skip" and v["layer"] in CLOUD}


class RulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = fx_mod.make_repo()
        cls.tmp = tempfile.mkdtemp(prefix="planner-rules-")
        sub = lambda s, a, b: (lambda old: old.replace(a, b))
        v = "aws/modules/aurora/variables.tf"
        cls.heads = {
            "patch": fx_mod.commit_change(cls.fx, "patch", {v: sub(0, '"17.9"', '"17.10"')}),
            "major": fx_mod.commit_change(cls.fx, "major", {
                v: sub(0, '"17.9"', '"18.0"'),
                "aws/kubernetes/eks-single-region/terraform/cluster/db.tf": sub(0, '"17.9"', '"18.0"')}),
            "rename": fx_mod.commit_change(cls.fx, "rename", {"aws/modules/aurora/outputs.tf": sub(0, "aurora_endpoint", "aurora_writer_endpoint")}),
            "ecs_root": fx_mod.commit_change(cls.fx, "ecs_root", {"aws/containers/ecs-single-region-fargate/terraform/cluster/main.tf": 'resource "a" "c" {}\n'}),
            "vpn": fx_mod.commit_change(cls.fx, "vpn", {"aws/modules/vpn/main.tf": 'resource "x" "v2" {}\n'}),
            "docs": fx_mod.commit_change(cls.fx, "docs", {"README.md": "changed\n"}),
            "action": fx_mod.commit_change(cls.fx, "action", {".github/actions/shared/action.yml": "name: shared2\nruns:\n  using: composite\n  steps: []\n"}),
            "judge": fx_mod.commit_change(cls.fx, "judge", {".github/planner-config.json": '{"gate_suites": []}\n', v: sub(0, '"17.9"', '"17.10"')}),
        }
        cls.runs = runs_json(cls.tmp, [
            run_rec("aws_eks_single_region_tests.yml", ended="2026-10-28T05:00:00Z", rid=1),
            run_rec("aws_ecs_single_region_fargate_tests.yml", ended="2026-10-27T05:00:00Z", rid=2),
            run_rec("aws_modules_eks_rds_os_tests.yml", ended="2026-10-30T05:00:00Z", rid=3),
        ])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fx["repo"], ignore_errors=True)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def d(self, name, **kw):
        kw.setdefault("runs_json", self.runs)
        return decision(self.fx, self.heads[name], **kw)

    def test_patch_runs_only_the_owner_suite(self):
        d = self.d("patch")
        self.assertEqual(cloud_run(d), {"aws_modules_eks_rds_os_tests.yml"})
        self.assertIsNone(d["rotation"])
        self.assertTrue(d["decision"]["aws_modules_eks_rds_os_create_destruct_tests.yml"]["enforced"])
        self.assertEqual(d["decision"]["aws_modules_eks_rds_os_create_destruct_tests.yml"]["action"], "skip")
        self.assertFalse(d["decision"]["aws_ecs_dual_region_fargate_tests.yml"]["enforced"], "not in the gate list")
        # consumer golden plans run because variables.tf changed
        self.assertEqual(d["decision"]["aws_eks_single_region_golden.yml"]["action"], "run")
        self.assertEqual({r["rule"] for r in d["rules_fired"]}, {"R2"})

    def test_engine_major_rotates_one_consumer_and_prefers_the_never_run_suite(self):
        d = self.d("major")
        self.assertIn("R8", {r["rule"] for r in d["rules_fired"]})
        self.assertEqual(d["impacted"]["engine_pins"][0]["dep"], "aurora-postgresql")
        self.assertTrue(d["impacted"]["engine_pins"][0]["major"])
        self.assertEqual(d["rotation"]["picked"], "aws_ecs_dual_region_fargate_tests.yml")
        self.assertEqual(cloud_run(d), {"aws_modules_eks_rds_os_tests.yml", "aws_ecs_dual_region_fargate_tests.yml"})
        # the RA root only changed a pin: no own from-scratch run for it
        self.assertIn("R10", {r["rule"] for r in d["rules_fired"]})
        self.assertNotIn("aws_eks_single_region_tests.yml", cloud_run(d))

    def test_rotation_uses_age_and_goes_to_the_oldest_when_all_have_run(self):
        runs = runs_json(self.tmp, [
            run_rec("aws_eks_single_region_tests.yml", ended="2026-10-28T05:00:00Z", rid=1),
            run_rec("aws_ecs_single_region_fargate_tests.yml", ended="2026-10-20T05:00:00Z", rid=2),
            run_rec("aws_ecs_dual_region_fargate_tests.yml", ended="2026-10-29T05:00:00Z", rid=3),
        ])
        d = self.d("major", runs_json=runs)
        self.assertEqual(d["rotation"]["picked"], "aws_ecs_single_region_fargate_tests.yml")
        ages = {c["suite"]: c["age_days"] for c in d["rotation"]["candidates"]}
        self.assertGreater(ages["aws_ecs_single_region_fargate_tests.yml"], ages["aws_eks_single_region_tests.yml"])

    def test_output_rename_is_a_significant_interface_change(self):
        d = self.d("rename")
        r9 = [r for r in d["rules_fired"] if r["rule"] == "R9"]
        self.assertTrue(r9)
        iface = d["impacted"]["interface"][0]
        self.assertTrue(iface["possible_output_rename"])
        self.assertEqual(iface["removed_outputs"], ["aurora_endpoint"])
        self.assertIn("aws_modules_eks_rds_os_tests.yml", cloud_run(d))
        self.assertIsNotNone(d["rotation"])

    def test_ecs_root_change_runs_that_ras_suite_only(self):
        d = self.d("ecs_root")
        self.assertEqual(cloud_run(d), {"aws_ecs_single_region_fargate_tests.yml"})
        self.assertIsNone(d["rotation"])
        self.assertEqual({r["rule"] for r in d["rules_fired"]}, {"R4"})

    def test_module_without_owner_suite_rotates_a_consumer(self):
        d = self.d("vpn")
        self.assertEqual({r["rule"] for r in d["rules_fired"]}, {"R3"})
        self.assertEqual(d["rotation"]["picked"], "aws_eks_single_region_tests.yml")

    def test_docs_only_runs_cheap_checks(self):
        d = self.d("docs")
        self.assertEqual(cloud_run(d), set())
        self.assertEqual({r["rule"] for r in d["rules_fired"]}, {"R6"})

    def test_shared_action_rotates_one_suite_that_uses_it(self):
        d = self.d("action")
        self.assertTrue(d["plumbing_change"])
        self.assertEqual(d["rotation"]["picked"], "aws_openshift_rosa_hcp_dual_region_tests.yml")

    def test_judge_change_does_not_narrow(self):
        d = self.d("judge")
        self.assertTrue(d["judge_changed"])
        self.assertIn("R11", {r["rule"] for r in d["rules_fired"]})
        self.assertIn("aws_modules_eks_rds_os_create_destruct_tests.yml", cloud_run(d))

    def test_labels_are_optional_escape_hatches(self):
        d = self.d("patch", labels=json.dumps(["planner:run-all"]))
        self.assertIn("aws_modules_eks_rds_os_create_destruct_tests.yml", cloud_run(d))
        self.assertIn("planner:run-all", d["overrides"]["applied"])
        d = self.d("patch", labels=json.dumps(["skip_aws_modules_eks_rds_os_tests"]))
        self.assertEqual(d["decision"]["aws_modules_eks_rds_os_tests.yml"]["action"], "skip")
        d = self.d("patch", labels=json.dumps(["skip_aws_modules_eks_rds_os_tests", "run_aws_modules_eks_rds_os_tests"]))
        self.assertEqual(d["decision"]["aws_modules_eks_rds_os_tests.yml"]["action"], "skip")
        self.assertTrue(d["overrides"]["conflicts"])
        d = self.d("patch", labels=json.dumps(["skip_all"]))
        self.assertEqual(cloud_run(d), set())

    def test_no_label_is_needed_on_the_normal_path(self):
        d = self.d("patch")
        self.assertEqual(d["overrides"]["labels"], [])
        self.assertEqual(d["mode"], "gate")

    def test_fork_and_unknown_base_branch_are_report_only(self):
        d = self.d("patch", fork="true")
        self.assertEqual(d["mode"], "report")
        self.assertFalse(any(v["enforced"] for v in d["decision"].values()))
        d = self.d("patch", base_branch="stable/8.9")
        self.assertEqual(d["mode"], "report")
        d = self.d("patch", mode="bogus")
        self.assertEqual(d["mode"], "report")

    def test_gate_suites_override(self):
        d = self.d("patch", gate_suites="none")
        self.assertFalse(any(v["enforced"] for v in d["decision"].values()))
        d = self.d("patch", gate_suites="aws_ecs_dual_region_fargate_tests.yml")
        self.assertTrue(d["decision"]["aws_ecs_dual_region_fargate_tests.yml"]["enforced"])

    def test_decision_is_deterministic_and_valid(self):
        from .. import schema
        a, b = self.d("major"), self.d("major")
        self.assertEqual(a, b)
        self.assertEqual(schema.validate(a), [])


if __name__ == "__main__":
    unittest.main()
