import argparse
import json
import os
import shutil
import tempfile
import unittest

from .. import cli, collect, runs, store
from ..gh import Gh
from .fakegh import FakeGh
from . import fixtures as fx_mod


def run_payload(rid, sha, conclusion="success", event="schedule", branch="main", attempt=1, ended="2026-11-01T05:00:00Z", minutes=120):
    from datetime import datetime, timedelta
    end = datetime.strptime(ended, "%Y-%m-%dT%H:%M:%SZ")
    return {"id": rid, "status": "completed", "conclusion": conclusion, "event": event, "head_branch": branch, "head_sha": sha,
            "run_attempt": attempt, "run_started_at": (end - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "updated_at": ended, "html_url": "https://github.com/o/r/actions/runs/%d" % rid, "pull_requests": []}


class CollectRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = fx_mod.make_repo()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fx["repo"], ignore_errors=True)

    def ns(self, store_dir):
        return argparse.Namespace(repo="o/r", git_dir=self.fx["repo"], store_dir=store_dir, branch="ci-decisions",
                                  config_path=".github/planner-config.json", backfill_days="30", max_events="50", now="2026-11-02T00:00:00Z")

    def test_collect_is_idempotent_and_writes_a_watermark(self):
        fake = FakeGh()
        fake.runs["aws_eks_single_region_tests.yml"] = [run_payload(1, "1" * 40), run_payload(2, "2" * 40, conclusion="failure", ended="2026-11-01T09:00:00Z")]
        fake.runs["aws_ecs_single_region_fargate_tests.yml"] = [run_payload(3, "3" * 40, attempt=2, ended="2026-11-01T11:00:00Z")]
        d = tempfile.mkdtemp()
        try:
            collect.run(self.ns(d), gh=Gh(fake))
            outs = [k for k in fake.files if "-outcome-" in k]
            self.assertEqual(len(outs), 3)
            self.assertTrue(any("-watermark-" in k for k in fake.files))
            # a fresh clone of the branch: nothing new to write except a newer watermark
            for k, v in fake.files.items():
                p = os.path.join(d, k)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                open(p, "wb").write(v)
            before = len(fake.files)
            later = self.ns(d)
            later.now = "2026-11-02T01:00:00Z"
            collect.run(later, gh=Gh(fake))
            self.assertEqual(len([k for k in fake.files if "-outcome-" in k]), 3)
            self.assertEqual(len(fake.files) - before, 1)
            ev = [json.loads(v) for k, v in fake.files.items() if "-outcome-" in k]
            attempt2 = [e for e in ev if e["run_id"] == 3][0]
            self.assertEqual(attempt2["attempt"], 2)
            self.assertTrue(attempt2["flake_hint"], "green on attempt 2 after a red attempt 1")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_runs_command_lists_successful_scheduled_runs_of_expensive_suites(self):
        fake = FakeGh()
        fake.runs["aws_eks_single_region_tests.yml"] = [run_payload(1, "1" * 40)]
        out = os.path.join(tempfile.mkdtemp(), "runs.json")
        ns = argparse.Namespace(repo="o/r", git_dir=self.fx["repo"], base_sha=self.fx["base"], default_branch="main",
                                config_path=".github/planner-config.json", per_page="20", out=out)
        runs.run(ns, gh=Gh(fake))
        data = json.load(open(out))
        self.assertTrue(data)
        self.assertEqual({r["path"] for r in data}, {".github/workflows/aws_eks_single_region_tests.yml"})

    def test_learn_command_writes_weights_and_a_checkpoint(self):
        fake = FakeGh()
        d = tempfile.mkdtemp()
        try:
            ds = store.DirStore(d)
            o = collect.outcome_from_run("o/r", run_payload(1, "1" * 40, ended="2026-10-30T05:00:00Z"), "x_tests.yml", None, False, "2026-10-30T05:00:00Z")
            ds.write(store.path_for(o), o)
            ns = argparse.Namespace(repo="o/r", git_dir="", store_dir=d, branch="ci-decisions", config_path=".github/planner-config.json",
                                    summary_file="", dry_run=False, now="2026-11-02T00:00:00Z", gh_runner=fake)
            cli._learn(ns)
            names = sorted(fake.files)
            self.assertTrue(any("-weights-" in n for n in names))
            self.assertTrue(any("-checkpoint-" in n for n in names))
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
