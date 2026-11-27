import argparse
import json
import os
import shutil
import tempfile
import unittest
from datetime import date

from .. import camunda, checkrun, collect, config, gate, known_failures, publish, schema
from ..gh import Gh
from .fakegh import FakeGh
from . import fixtures as fx_mod
from .helpers import args as dargs
from .test_learning import outcome

NOW = "2026-11-10T10:00:00Z"
SHA = "a" * 40


def sample_facts(fx=None, head=None):
    from ..decide import facts_run
    fx = fx or fx_mod.make_repo()
    head = head or fx_mod.commit_change(fx, "p", {"aws/modules/aurora/variables.tf": lambda o: o.replace('"17.9"', '"17.10"')})
    ns = dargs(fx, head, slug="o/r", mode="gate")
    return facts_run(ns), fx, head


def process_variables(facts, picked="a_tests.yml"):
    return {"decisionId": "AbC123", "recorded": True, "merged": {
        "final": {"mode": "gate", "rotation": {"picked": picked}, "rulesFired": [{"rule": "R2", "subject": "m", "reason": "x", "evidence": []}],
                  "decision": [{"suite": "aws_modules_eks_rds_os_create_destruct_tests.yml", "action": "skip", "enforced": True, "layer": "L4", "reasons": ["path filter matched, no rule needs this suite"]},
                               {"suite": "aws_modules_eks_rds_os_tests.yml", "action": "run", "enforced": False, "layer": "L4", "reasons": ["R2 owner suite builds module m"]}]},
        "advisor": {"enabled": False, "added": [], "rejected": []}}}


class FakeHttp:
    def __init__(self, token_status=200, create_status=200, create_body=None, raise_exc=None):
        self.calls = []
        self.token_status, self.create_status, self.create_body, self.raise_exc = token_status, create_status, create_body, raise_exc

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body, timeout))
        if self.raise_exc:
            raise self.raise_exc
        if url.endswith("/token"):
            return self.token_status, json.dumps({"access_token": "tok"}) if self.token_status == 200 else "denied"
        return self.create_status, json.dumps(self.create_body) if self.create_body is not None else "{}"


def client(http):
    return camunda.Client("https://c.example/v2", "https://idp.example/token", "id", "secret", "zeebe.camunda.io", http=http)


class CamundaClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facts, cls.fx, cls.head = sample_facts()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fx["repo"], ignore_errors=True)

    def test_awaits_the_process_and_builds_a_valid_plan(self):
        http = FakeHttp(create_body={"variables": process_variables(self.facts)})
        plan = camunda.decide(self.facts, client(http), False, "1-1", 30)
        self.assertEqual(plan["source"], "camunda")
        self.assertEqual(plan["decision_id"], "AbC123")
        self.assertEqual(schema.validate_plan(plan), [])
        self.assertTrue(plan["decision"]["aws_modules_eks_rds_os_create_destruct_tests.yml"]["enforced"])
        method, url, headers, body, timeout = http.calls[-1]
        sent = json.loads(body)
        self.assertEqual(url, "https://c.example/v2/process-instances")
        self.assertEqual(sent["processDefinitionId"], "ci-test-planner")
        self.assertTrue(sent["awaitCompletion"])
        self.assertEqual(sent["requestTimeout"], 30000)
        self.assertIn("merged", sent["fetchVariables"])
        self.assertEqual(sent["variables"]["advisorEnabled"], False)
        self.assertIn("1-1", sent["businessId"])
        self.assertEqual(headers["Authorization"], "Bearer tok")
        self.assertGreater(timeout, 30, "the HTTP timeout sits above the server-side wait")
        tok = http.calls[0]
        self.assertIn(b"grant_type=client_credentials", tok[3])
        self.assertIn(b"audience=zeebe.camunda.io", tok[3])

    def test_every_failure_fails_open(self):
        cases = {
            "token denied": FakeHttp(token_status=401),
            "gateway timeout": FakeHttp(create_status=504),
            "backpressure": FakeHttp(create_status=503),
            "forbidden": FakeHttp(create_status=403),
            "not json": FakeHttp(create_body=None),
            "no plan": FakeHttp(create_body={"variables": {}}),
            "bad plan": FakeHttp(create_body={"variables": {"merged": {"final": {"mode": "gate", "decision": [{"suite": "../x", "action": "run", "enforced": True, "layer": "L4", "reasons": []}]}}}}),
            "network": FakeHttp(raise_exc=OSError("connection reset")),
            "timeout": FakeHttp(raise_exc=TimeoutError("timed out")),
        }
        for name, http in cases.items():
            plan = camunda.decide(self.facts, client(http), False, "1-1", 1)
            self.assertEqual(plan["source"], "fail-open", name)
            self.assertEqual(plan["decision"], {}, name)
            self.assertEqual(schema.validate_plan(plan), [], name)
            self.assertFalse(gate.decide_skip(plan, "aws_modules_eks_rds_os_create_destruct_tests.yml")[0], name)

    def test_unconfigured_or_insecure_configuration_fails_open(self):
        plan = camunda.decide(self.facts, None, False, "1-1")  # no client, environment empty
        self.assertEqual(plan["source"], "fail-open")
        self.assertIn("not configured", plan["reason"])
        with self.assertRaises(camunda.CamundaError):
            camunda.Client.from_env({"CAMUNDA_BASE_URL": "http://plain", "CAMUNDA_TOKEN_URL": "https://x", "CAMUNDA_CLIENT_ID": "a", "CAMUNDA_CLIENT_SECRET": "b"})

    def test_await_seconds_is_clamped(self):
        for text, want in (("30", 30.0), (" 12 ", 12.0), ("0", 1.0), ("600", 60.0), ("", 30.0), ("abc", 30.0), ("nan", 30.0), ("7.5", 7.5)):
            self.assertEqual(camunda.await_seconds(text), want, text)

    def test_invalid_facts_are_never_sent(self):
        bad = json.loads(json.dumps(self.facts))
        bad["suites"]["../../x"] = bad["suites"][next(iter(bad["suites"]))]
        http = FakeHttp(create_body={"variables": process_variables(self.facts)})
        plan = camunda.decide(bad, client(http), False, "1-1")
        self.assertEqual(plan["source"], "fail-open")
        self.assertEqual(http.calls, [], "hostile facts must not reach Camunda")


class GateAndPublishTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facts, cls.fx, cls.head = sample_facts()
        cls.plan = camunda.plan_from_result(cls.facts, process_variables(cls.facts))
        cls.tmp = tempfile.mkdtemp()
        cls.path = os.path.join(cls.tmp, "plan.json")
        json.dump(cls.plan, open(cls.path, "w"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fx["repo"], ignore_errors=True)

    def pargs(self, **kw):
        ns = dict(plan=self.path, repo="o/r", pr=str(self.facts["pr"]), head_sha=self.head, summary_file="", no_comment=False, no_status=False)
        ns.update(kw)
        return argparse.Namespace(**ns)

    def test_publish_creates_check_run_status_and_a_sticky_comment(self):
        fake = FakeGh()
        self.assertEqual(publish.run(self.pargs(), gh=Gh(fake)), 0)
        self.assertEqual(len(fake.check_runs), 1)
        self.assertEqual(fake.check_runs[0]["name"], "planner plan")
        self.assertEqual(fake.check_runs[0]["conclusion"], "neutral")
        self.assertEqual(fake.statuses[0]["context"], "planner / decision")
        publish.run(self.pargs(), gh=Gh(fake))
        self.assertEqual(len(fake.comments), 1, "the comment is edited, not duplicated")
        self.assertIn("<!-- planner-decision -->", fake.comments[0]["body"])

    def test_publish_rejects_a_plan_for_another_pr_sha_or_repo(self):
        for kw in ({"pr": "99"}, {"head_sha": "f" * 40}, {"repo": "o/other"}):
            fake = FakeGh()
            self.assertEqual(publish.run(self.pargs(**kw), gh=Gh(fake)), 2)
            self.assertEqual(fake.check_runs, [])

    def test_gate_reads_the_newest_plan_and_skips_only_enforced_skips(self):
        fake = FakeGh()
        publish.run(self.pargs(), gh=Gh(fake))
        plan = checkrun.newest(Gh(fake), "o/r", self.head)
        self.assertIsNotNone(plan)
        name = "aws_modules_eks_rds_os_create_destruct_tests.yml"
        self.assertTrue(gate.decide_skip(plan, name, "gate")[0])
        self.assertFalse(gate.decide_skip(plan, "aws_modules_eks_rds_os_tests.yml", "gate")[0])
        self.assertFalse(gate.decide_skip(plan, "unknown.yml", "gate")[0])
        self.assertFalse(gate.decide_skip(None, name, "gate")[0])
        self.assertFalse(gate.decide_skip(dict(plan, mode="report"), name, "gate")[0])
        self.assertFalse(gate.decide_skip(plan, name)[0], "the gate step defaults to report")

    def test_report_mode_never_skips(self):
        """The burn-in guarantee: in report mode no layer can turn a plan into a skip."""
        name = "aws_modules_eks_rds_os_create_destruct_tests.yml"
        # the gate step's own mode: a gate plan with an enforced skip still runs
        for mode in ("report", "", "Gate", "anything"):
            skip, reason = gate.decide_skip(self.plan, name, mode)
            self.assertFalse(skip, mode)
            self.assertIn("report mode", reason)
            self.assertIn("says skip", reason)
        # the process cannot upgrade report facts to a gate plan
        facts = dict(self.facts, requested_mode="report")
        plan = camunda.plan_from_result(facts, process_variables(facts))
        self.assertEqual(plan["mode"], "report")
        self.assertFalse(any(d["enforced"] for d in plan["decision"].values()))
        self.assertFalse(gate.decide_skip(plan, name, "gate")[0])
        # empty or unknown requested mode means report in the facts
        from ..decide import facts_run
        for m in ("", "burn-in"):
            self.assertEqual(facts_run(dargs(self.fx, self.head, slug="o/r", mode=m))["requested_mode"], "report")

    def test_gate_ignores_plans_not_made_by_github_actions_or_for_another_sha(self):
        fake = FakeGh()
        cr = {"name": "planner plan", "head_sha": self.head, "status": "completed", "conclusion": "neutral",
              "output": {"title": "x", "summary": "x", "text": json.dumps(self.plan)}, "_app": "evil-app"}
        fake.call = None
        Gh(fake).api("POST", "repos/o/r/check-runs", cr)
        self.assertIsNone(checkrun.newest(Gh(fake), "o/r", self.head))
        other = dict(self.plan, head_sha="c" * 40)
        Gh(fake).api("POST", "repos/o/r/check-runs", {"name": "planner plan", "head_sha": self.head, "status": "completed", "conclusion": "neutral",
                                                       "output": {"title": "x", "summary": "x", "text": json.dumps(other)}})
        self.assertIsNone(checkrun.newest(Gh(fake), "o/r", self.head), "a plan for another commit is not trusted")
        Gh(fake).api("POST", "repos/o/r/check-runs", {"name": "planner plan", "head_sha": self.head, "status": "completed", "conclusion": "neutral",
                                                       "output": {"title": "x", "summary": "x", "text": "not json"}})
        self.assertIsNone(checkrun.newest(Gh(fake), "o/r", self.head))

    def test_gate_polls_then_fails_open(self):
        fake = FakeGh()
        t = [0.0]
        sleeps = []
        got = gate.find_plan(Gh(fake), "o/r", self.head, 30, 10, sleep=lambda s: (sleeps.append(s), t.__setitem__(0, t[0] + s)), clock=lambda: t[0])
        self.assertIsNone(got)
        self.assertEqual(len(sleeps), 3)

    def test_gate_run_outputs_and_ignores_other_events(self):
        out = os.path.join(self.tmp, "gh_output")
        os.environ["GITHUB_OUTPUT"] = out
        try:
            fake = FakeGh()
            publish.run(self.pargs(), gh=Gh(fake))
            ns = argparse.Namespace(repo="o/r", event="pull_request", fork="false", pr=str(self.facts["pr"]), head_sha=self.head,
                                    suite="aws_modules_eks_rds_os_create_destruct_tests.yml", timeout="0", interval="1", mode="gate")
            gate.run(ns, gh=Gh(fake))
            ns.mode = "report"
            gate.run(ns, gh=Gh(fake))
            ns.mode, ns.event = "gate", "schedule"
            gate.run(ns, gh=Gh(fake))
            ns.event, ns.fork = "pull_request", "true"
            gate.run(ns, gh=Gh(fake))
            self.assertEqual(open(out).read().split(), ["should_skip=true", "should_skip=false", "should_skip=false", "should_skip=false"])
        finally:
            os.environ.pop("GITHUB_OUTPUT", None)

    def test_fail_open_summary_says_nothing_is_skipped(self):
        fo = camunda.fail_open(self.facts, "camunda not reachable")
        self.assertIn("Nothing is skipped", checkrun.plan_summary(fo))


class KnownFailuresTest(unittest.TestCase):
    TEXT = """
entries:
  - id: kf-capacity
    class: blocked
    pattern: "(?i)capacity slot|quota"
    suites: ["aws_*_tests.yml"]
  - id: kf-flaky-dns
    class: known_failure
    pattern: "(?i)dns"
    issue: https://github.com/camunda/camunda-deployment-references/issues/1
    expires: 2026-12-01
  - id: kf-old
    class: known_failure
    pattern: "(?i)legacy"
    issue: https://github.com/x/y/issues/2
    expires: 2026-01-01
  - id: bad
    class: nonsense
    pattern: x
"""

    def test_classification(self):
        kf = known_failures.load(self.TEXT)
        self.assertEqual([e["id"] for e in kf], ["kf-capacity", "kf-flaky-dns", "kf-old"])
        today = date(2026, 11, 10)
        self.assertEqual(known_failures.classify(kf, "aws_eks_tests.yml", ["Acquire capacity slot"], today)[:2], ("blocked", "kf-capacity"))
        self.assertEqual(known_failures.classify(kf, "local_kind_tests.yml", ["Acquire capacity slot"], today)[0], "fail")  # suite glob
        c = known_failures.classify(kf, "aws_eks_tests.yml", ["resolve DNS name"], today)
        self.assertEqual(c[:2], ("known_failure", "kf-flaky-dns"))
        self.assertIn("issues/1", c[2])
        self.assertEqual(known_failures.classify(kf, "aws_eks_tests.yml", ["legacy thing"], today)[0], "fail")  # expired
        self.assertEqual(known_failures.classify(kf, "aws_eks_tests.yml", ["Terratest assertion failed"], today)[0], "fail")
        self.assertEqual(known_failures.load("not: [valid"[:0]), [])


class CollectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = fx_mod.make_repo()
        fx_mod.write(cls.fx["repo"], ".github/planner-known-failures.yaml", KnownFailuresTest.TEXT)
        fx_mod.git(cls.fx["repo"], "add", "-A")
        fx_mod.git(cls.fx["repo"], "commit", "-q", "-m", "chore: catalogue")
        fx_mod.git(cls.fx["repo"], "checkout", "-q", "-B", "main")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fx["repo"], ignore_errors=True)

    def run_payload(self, rid, sha, conclusion="success", event="schedule", branch="main", attempt=1, ended="2026-11-10T09:30:00Z", minutes=120, pr=None):
        from datetime import datetime, timedelta
        end = datetime.strptime(ended, "%Y-%m-%dT%H:%M:%SZ")
        return {"id": rid, "status": "completed", "conclusion": conclusion, "event": event, "head_branch": branch, "head_sha": sha, "run_attempt": attempt,
                "run_started_at": (end - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ"), "updated_at": ended,
                "html_url": "https://github.com/o/r/actions/runs/%d" % rid, "pull_requests": [{"number": pr}] if pr else []}

    def cargs(self, **kw):
        ns = dict(repo="o/r", git_dir=self.fx["repo"], config_path=".github/planner-config.json", known_failures=".github/planner-known-failures.yaml",
                  since_hours="6", max_docs="400", default_branch="main", dry_run=False, now=NOW)
        ns.update(kw)
        return argparse.Namespace(**ns)

    class Sent:
        def __init__(self, fail=None):
            self.sent, self.fail = [], fail

        def create_instance(self, process_id, variables, await_ms=0, fetch=None, business_id=None):
            if self.fail:
                raise self.fail
            self.sent.append((process_id, variables))
            return {}

    def test_outcomes_are_classified_and_sent_in_one_batch(self):
        fake = FakeGh()
        fake.runs["aws_eks_single_region_tests.yml"] = [
            self.run_payload(1, "1" * 40), self.run_payload(2, "2" * 40, "failure", ended="2026-11-10T09:40:00Z"),
            self.run_payload(3, "3" * 40, "failure", ended="2026-11-10T09:50:00Z"), self.run_payload(4, "4" * 40, attempt=2, ended="2026-11-10T09:55:00Z")]
        fake.jobs[2] = [{"name": "Create cluster", "conclusion": "failure", "steps": [{"name": "Acquire capacity slot", "conclusion": "failure"}]}]
        fake.jobs[3] = [{"name": "Terratest", "conclusion": "failure", "steps": [{"name": "Run tests", "conclusion": "failure"}]}]
        sent = self.Sent()
        collect.run(self.cargs(), gh=Gh(fake), client=sent)
        self.assertEqual(len(sent.sent), 1)
        process_id, variables = sent.sent[0]
        self.assertEqual(process_id, "ci-test-record")
        docs = variables["docs"]
        by_run = {d["body"]["run_id"]: d for d in docs if d["index"] == "ci-planner-outcomes"}
        self.assertEqual(by_run[1]["body"]["outcome"], "pass")
        self.assertEqual(by_run[2]["body"]["outcome"], "blocked")
        self.assertEqual(by_run[2]["body"]["known_failure_id"], "kf-capacity")
        self.assertEqual(by_run[3]["body"]["outcome"], "fail")
        self.assertTrue(by_run[1]["body"]["full"])
        self.assertEqual(len({d["id"] for d in docs}), len(docs), "document ids are stable and unique")
        again = self.Sent()
        collect.run(self.cargs(), gh=Gh(fake), client=again)
        self.assertEqual([d["id"] for d in again.sent[0][1]["docs"]], [d["id"] for d in docs], "a re-run sends the same ids")

    def test_pr_runs_carry_the_modules_of_the_published_plan(self):
        facts, fx, head = sample_facts()
        try:
            plan = camunda.plan_from_result(facts, process_variables(facts))
            fake = FakeGh()
            publish.run(argparse.Namespace(plan=self._write(plan), repo="o/r", pr=str(facts["pr"]), head_sha=head, summary_file="", no_comment=True, no_status=True), gh=Gh(fake))
            fake.runs["aws_openshift_rosa_hcp_dual_region_tests.yml"] = [self.run_payload(9, head, event="pull_request", branch="feat/x", pr=facts["pr"])]
            sent = self.Sent()
            collect.run(self.cargs(), gh=Gh(fake), client=sent)
            doc = [d for d in sent.sent[0][1]["docs"] if d["body"]["run_id"] == 9][0]["body"]
            self.assertEqual(doc["decision_id"], "AbC123")
            self.assertEqual(doc["modules"], plan["modules"])
        finally:
            shutil.rmtree(fx["repo"], ignore_errors=True)

    def _write(self, plan):
        p = os.path.join(tempfile.mkdtemp(), "p.json")
        json.dump(plan, open(p, "w"))
        return p

    def test_camunda_down_is_not_an_error(self):
        fake = FakeGh()
        fake.runs["aws_eks_single_region_tests.yml"] = [self.run_payload(1, "1" * 40)]
        self.assertEqual(collect.run(self.cargs(), gh=Gh(fake), client=self.Sent(fail=camunda.CamundaError("503"))), 0)

    def test_dry_run_sends_nothing(self):
        fake = FakeGh()
        fake.runs["aws_eks_single_region_tests.yml"] = [self.run_payload(1, "1" * 40)]
        sent = self.Sent()
        collect.run(self.cargs(dry_run=True), gh=Gh(fake), client=sent)
        self.assertEqual(sent.sent, [])


if __name__ == "__main__":
    unittest.main()
