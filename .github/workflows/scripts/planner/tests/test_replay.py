import os
import tempfile
import unittest

from . import replay

REPO = os.environ.get("PLANNER_REPLAY_REPO", "/work/repos/camunda-deployment-references")
RUNS = os.environ.get("PLANNER_REPLAY_RUNS", "/work/notes/smx-qa-proposal/baseline/data/runs-camunda-deployment-references.jsonl")


@unittest.skipUnless(os.environ.get("PLANNER_REPLAY") == "1" and os.path.isdir(REPO) and os.path.exists(RUNS),
                     "set PLANNER_REPLAY=1 (slow, needs the clone and sampled runs)")
class ReplayTest(unittest.TestCase):
    def test_four_real_prs_produce_valid_decisions_with_fewer_cloud_suites(self):
        with tempfile.TemporaryDirectory() as out:
            res = {r["pr"]: r for r in replay.run(REPO, RUNS, out, prs=replay.PRS[:4])}
        self.assertIn("R8", res["e127b109"]["rules"])
        self.assertLess(res["e127b109"]["planned_cloud"], res["e127b109"]["path_triggered_cloud"])
        self.assertIn("R4", res["e271f7af"]["rules"])
        self.assertTrue(res["af690ea9"]["rotation"])
        self.assertIn("R5", res["62ef7c68"]["rules"])
        for r in res.values():
            self.assertLessEqual(r["planned_cloud"], r["path_triggered_cloud"] + 1)


if __name__ == "__main__":
    unittest.main()
