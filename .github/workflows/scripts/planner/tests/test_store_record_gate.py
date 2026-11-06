import json
import os
import tempfile
import unittest

from .. import gate, record, schema, store
from ..gh import Gh, GhError
from .fakegh import FakeGh
from . import fixtures as fx_mod
from .helpers import args, decision


def mk_store(fake, retries=4):
    sleeps = []
    return store.GhStore(Gh(fake), "o/r", "ci-decisions", retries=retries, sleep=sleeps.append, rng=lambda: 0.5), sleeps


class StoreTest(unittest.TestCase):
    def test_put_get_list_and_idempotency(self):
        fake = FakeGh()
        st, _ = mk_store(fake)
        r = st.put("events/2026/11/06/x.json", {"a": 1}, "m")
        self.assertTrue(r["created"])
        self.assertEqual(json.loads(st.get("events/2026/11/06/x.json")), {"a": 1})
        self.assertEqual(st.list_dir("events/2026/11/06"), ["x.json"])
        r = st.put("events/2026/11/06/x.json", {"a": 1}, "m")  # a retried write that already landed
        self.assertFalse(r["created"])
        with self.assertRaises(store.StoreConflict):
            st.put("events/2026/11/06/x.json", {"a": 2}, "m")  # never overwrite

    def test_concurrent_writer_conflict_is_retried_with_backoff(self):
        fake = FakeGh()
        fake.fail = [(409, "is at abc but expected def"), (409, "is at abc but expected def")]
        st, sleeps = mk_store(fake)
        r = st.put("events/2026/11/06/y.json", {"b": 1}, "m")
        self.assertTrue(r["created"])
        self.assertEqual(len(sleeps), 2)
        self.assertTrue(all(s > 0 for s in sleeps))

    def test_gives_up_after_the_retries(self):
        fake = FakeGh()
        fake.fail = [(409, "conflict")] * 10
        st, _ = mk_store(fake, retries=2)
        with self.assertRaises(GhError):
            st.put("events/2026/11/06/z.json", {"b": 1}, "m")

    def test_missing_branch_is_created_as_an_orphan_once(self):
        fake = FakeGh(branch_exists=False)
        st, _ = mk_store(fake)
        st.put("events/2026/11/06/n.json", {"n": 1}, "m")
        self.assertTrue(fake.branch)
        self.assertIn(("POST", "repos/o/r/git/refs"), fake.calls)

    def test_paths_are_deterministic_and_sorted(self):
        p = store.decision_path(7, "a" * 40, "2026-11-06T09:41:12Z", "deadbeefcafebabe")
        self.assertEqual(p, "decisions/pr/7/%s/20261106T094112Z-deadbeef.json" % ("a" * 40))
        self.assertEqual(store.event_path("2026-11-06T09:41:12Z", "outcome", "out_0123456789ab"),
                         "events/2026/11/06/20261106T094112Z-outcome-out_0123456789ab.json")


class RecordGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = fx_mod.make_repo()
        cls.head = fx_mod.commit_change(cls.fx, "c1", {"aws/modules/aurora/variables.tf": lambda o: o.replace('"17.9"', '"17.10"')})
        cls.dec = decision(cls.fx, cls.head, slug="o/r")
        cls.tmp = tempfile.mkdtemp()
        cls.art = os.path.join(cls.tmp, "decision.json")
        with open(cls.art, "w") as fh:
            json.dump(cls.dec, fh)

    def rec_args(self, **kw):
        import argparse
        ns = dict(artifact=self.art, repo="o/r", branch="ci-decisions", pr="7", head_sha=self.head, summary_file="",
                  no_comment=False, no_status=False)
        ns.update(kw)
        return argparse.Namespace(**ns)

    def test_record_stores_comments_and_sets_a_status(self):
        fake = FakeGh()
        self.assertEqual(record.run(self.rec_args(), gh=Gh(fake)), 0)
        self.assertEqual(len([f for f in fake.files if f.startswith("decisions/pr/7/%s/" % self.head)]), 1)
        self.assertEqual(len(fake.comments), 1)
        self.assertIn("<!-- planner-decision -->", fake.comments[0]["body"])
        self.assertEqual(fake.statuses[0]["context"], "planner / decision")
        record.run(self.rec_args(), gh=Gh(fake))  # second run edits the sticky comment, stores nothing new
        self.assertEqual(len(fake.comments), 1)
        self.assertEqual(len(fake.files), 1)

    def test_record_rejects_an_artifact_for_another_pr_or_sha_or_repo(self):
        for kw in ({"pr": "8"}, {"head_sha": "f" * 40}, {"repo": "o/other"}):
            fake = FakeGh()
            self.assertEqual(record.run(self.rec_args(**kw), gh=Gh(fake)), 2)
            self.assertEqual(fake.files, {})

    def test_record_rejects_a_hostile_artifact(self):
        bad = json.loads(json.dumps(self.dec))
        bad["decision"]["../../etc/passwd"] = bad["decision"][next(iter(bad["decision"]))]
        p = os.path.join(self.tmp, "bad.json")
        with open(p, "w") as fh:
            json.dump(bad, fh)
        fake = FakeGh()
        self.assertEqual(record.run(self.rec_args(artifact=p), gh=Gh(fake)), 2)
        self.assertEqual(fake.files, {})
        bad = json.loads(json.dumps(self.dec))
        bad["extra"] = "x"
        with open(p, "w") as fh:
            json.dump(bad, fh)
        self.assertEqual(record.run(self.rec_args(artifact=p), gh=Gh(fake)), 2)

    def test_fork_decision_is_not_commented(self):
        fork = decision(self.fx, self.head, slug="o/r", fork="true")
        p = os.path.join(self.tmp, "fork.json")
        with open(p, "w") as fh:
            json.dump(fork, fh)
        fake = FakeGh()
        record.run(self.rec_args(artifact=p), gh=Gh(fake))
        self.assertEqual(fake.comments, [])

    def test_gate_skips_only_enforced_skip_in_gate_mode(self):
        d = self.dec
        name = "aws_modules_eks_rds_os_create_destruct_tests.yml"
        self.assertEqual(d["decision"][name]["action"], "skip")
        self.assertEqual(gate.decide_skip(d, name)[0], True)
        self.assertEqual(gate.decide_skip(d, "aws_modules_eks_rds_os_tests.yml")[0], False)
        self.assertEqual(gate.decide_skip(d, "aws_ecs_dual_region_fargate_tests.yml")[0], False)  # not enforced
        self.assertEqual(gate.decide_skip(d, "unknown.yml")[0], False)
        self.assertEqual(gate.decide_skip(None, name)[0], False)  # fail open
        rep = dict(d, mode="report")
        self.assertEqual(gate.decide_skip(rep, name)[0], False)

    def test_gate_polls_then_fails_open_on_timeout(self):
        fake = FakeGh()
        st, _ = mk_store(fake)
        t = [0.0]
        sleeps = []
        got = gate.find_decision(st, 7, self.head, 30, 10, sleep=lambda s: (sleeps.append(s), t.__setitem__(0, t[0] + s)), clock=lambda: t[0])
        self.assertIsNone(got)
        self.assertEqual(len(sleeps), 3)

    def test_gate_reads_the_newest_decision(self):
        fake = FakeGh()
        record.run(self.rec_args(), gh=Gh(fake))
        newer = decision(self.fx, self.head, slug="o/r", now="2026-11-02T21:00:00Z", labels=json.dumps(["planner:run-all"]))
        fake.files["decisions/pr/7/%s/%s" % (self.head, "99999999T000000Z-aaaaaaaa.json")] = json.dumps(newer).encode()
        st, _ = mk_store(fake)
        got = gate.find_decision(st, 7, self.head, 0, 1)
        self.assertEqual(got["id"], newer["id"])

    def test_gate_run_writes_the_output_and_ignores_non_pr_events(self):
        import argparse
        out = os.path.join(self.tmp, "gh_output")
        os.environ["GITHUB_OUTPUT"] = out
        try:
            fake = FakeGh()
            record.run(self.rec_args(), gh=Gh(fake))
            ns = argparse.Namespace(repo="o/r", branch="ci-decisions", event="pull_request", fork="false", pr="7",
                                    head_sha=self.head, suite="aws_modules_eks_rds_os_create_destruct_tests.yml", timeout="0", interval="1")
            gate.run(ns, gh=Gh(fake))
            ns.event = "schedule"
            gate.run(ns, gh=Gh(fake))
            lines = open(out).read().split()
            self.assertEqual(lines, ["should_skip=true", "should_skip=false"])
        finally:
            os.environ.pop("GITHUB_OUTPUT", None)


if __name__ == "__main__":
    unittest.main()
