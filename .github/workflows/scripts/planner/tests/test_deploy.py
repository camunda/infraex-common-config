"""`planner deploy-models`: one atomic multipart deployment of the six files, the target check, the answer check."""

import argparse
import email.parser
import email.policy
import json
import os
import http.server
import shutil
import tempfile
import threading
import unittest

from .. import deploy
from ..camunda import Client, _urlopen

CAMUNDA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "camunda")
ENV = {"CAMUNDA_BASE_URL": "https://oc.example.test/v2", "CAMUNDA_TOKEN_URL": "https://idp.example.test/token",
       "CAMUNDA_CLIENT_ID": "ci-deployer", "CAMUNDA_CLIENT_SECRET": "s3cr3t-value",
       "PLANNER_DEPLOY_ALLOWED_BASE_URL": "https://oc.example.test/v2/", "PLANNER_DEPLOY_ALLOWED_TOKEN_URL": "https://idp.example.test/token"}


def answer(tenant="<default>", drop=None):
    items = []
    for i, (name, pid) in enumerate(sorted(deploy.PROCESSES.items())):
        items.append({"processDefinition": {"processDefinitionId": pid, "processDefinitionVersion": 2, "processDefinitionKey": "2251799813%06d" % i,
                                            "resourceName": name, "tenantId": tenant}, "decisionDefinition": None, "decisionRequirements": None,
                      "form": None, "resource": None})
    for i, (name, did) in enumerate(sorted(deploy.DRGS.items())):
        items.append({"processDefinition": None, "decisionDefinition": None, "form": None, "resource": None,
                      "decisionRequirements": {"decisionRequirementsId": did, "decisionRequirementsName": did, "version": 1,
                                               "decisionRequirementsKey": "2251799814%06d" % i, "resourceName": name, "tenantId": tenant}})
    for i, dec in enumerate(sorted(deploy.CALLED_DECISIONS | {"rows", "suites"})):
        items.append({"processDefinition": None, "decisionRequirements": None, "form": None, "resource": None,
                      "decisionDefinition": {"decisionDefinitionId": dec, "decisionDefinitionKey": "2251799815%06d" % i, "version": 1, "name": dec,
                                             "decisionRequirementsId": "ci_test_planner", "decisionRequirementsKey": "1", "tenantId": tenant}})
    if drop:
        items = [x for x in items if drop not in json.dumps(x)]
    return {"deploymentKey": "2251799813686975", "tenantId": tenant, "deployments": items}


class FakeHttp:
    def __init__(self, status=200, body=None):
        self.status, self.body, self.calls = status, body, []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body))
        if url.endswith("/token"):
            return 200, json.dumps({"access_token": "tok"})
        return self.status, json.dumps(self.body if self.body is not None else answer())


def args(**kw):
    ns = dict(dir=CAMUNDA_DIR, tenant_id="", dry_run=False, out="", summary_file="")
    ns.update(kw)
    return argparse.Namespace(**ns)


class DeployTest(unittest.TestCase):
    def client(self, http):
        return Client.from_env(ENV, http=http)

    def test_one_request_with_all_six_resources(self):
        http = FakeHttp()
        tmp = tempfile.mkdtemp()
        try:
            out = os.path.join(tmp, "deployment.json")
            self.assertEqual(deploy.run(args(out=out), client=self.client(http), env=ENV), 0)
            deploys = [c for c in http.calls if c[1].endswith("/deployments")]
            self.assertEqual(len(deploys), 1, "atomic: one deployment request")
            method, url, headers, body = deploys[0]
            self.assertEqual((method, url), ("POST", "https://oc.example.test/v2/deployments"))
            msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
                b"Content-Type: " + headers["Content-Type"].encode() + b"\r\n\r\n" + body)
            parts = [(p.get_param("name", header="content-disposition"), p.get_filename(), p.get_content()) for p in msg.iter_parts()]
            self.assertEqual([p[1] for p in parts], list(deploy.RESOURCES))
            self.assertTrue(all(p[0] == "resources" for p in parts))
            for name in deploy.RESOURCES:
                with open(os.path.join(CAMUNDA_DIR, name), "rb") as fh:
                    self.assertIn(b"\r\n\r\n" + fh.read() + b"\r\n--", body, "%s is sent unchanged" % name)
            summary = json.load(open(out))
            self.assertEqual(summary["deploymentKey"], "2251799813686975")
            self.assertEqual(len(summary["resources"]), 6)
            self.assertNotIn("s3cr3t", open(out).read())
        finally:
            shutil.rmtree(tmp)

    def test_tenant_is_sent_and_checked(self):
        http = FakeHttp(body=answer("ci"))
        self.assertEqual(deploy.run(args(tenant_id="ci"), client=self.client(http), env=ENV), 0)
        self.assertIn(b'name="tenantId"\r\n\r\nci\r\n', http.calls[-1][3])
        self.assertEqual(deploy.run(args(tenant_id="other"), client=self.client(FakeHttp(body=answer("ci"))), env=ENV), 1)

    def test_incomplete_or_failed_answers_fail(self):
        for drop in ("ci-test-record", "ci_test_memory", '"learned"'):
            self.assertEqual(deploy.run(args(), client=self.client(FakeHttp(body=answer(drop=drop))), env=ENV), 1, drop)
        problem = {"type": "about:blank", "title": "INVALID_ARGUMENT", "status": 400, "detail": "Unable to parse model"}
        self.assertEqual(deploy.run(args(), client=self.client(FakeHttp(400, problem)), env=ENV), 1)
        self.assertEqual(deploy.run(args(), client=self.client(FakeHttp(200, {"deployments": []})), env=ENV), 1)

    def test_secret_only_goes_to_the_allowed_cluster(self):
        for k, v in (("CAMUNDA_BASE_URL", "https://evil.example.test/v2"), ("CAMUNDA_TOKEN_URL", "https://evil.example.test/token"),
                     ("PLANNER_DEPLOY_ALLOWED_BASE_URL", ""), ("PLANNER_DEPLOY_ALLOWED_TOKEN_URL", "")):
            http = FakeHttp()
            self.assertEqual(deploy.run(args(), client=self.client(http), env=dict(ENV, **{k: v})), 2, k)
            self.assertEqual(http.calls, [], "nothing is sent, not even the token request")
        insecure = dict(ENV, CAMUNDA_BASE_URL="http://oc.example.test/v2", PLANNER_DEPLOY_ALLOWED_BASE_URL="http://oc.example.test/v2")
        self.assertEqual(deploy.run(args(dry_run=True), env=insecure), 2)

    def test_dry_run_sends_nothing_and_needs_no_secret(self):
        env = {k: v for k, v in ENV.items() if "CLIENT" not in k}
        self.assertEqual(deploy.run(args(dry_run=True), env=env), 0)

    def test_files_are_checked_before_anything_is_sent(self):
        tmp = tempfile.mkdtemp()
        try:
            for n in deploy.RESOURCES:
                shutil.copy(os.path.join(CAMUNDA_DIR, n), tmp)
            os.remove(os.path.join(tmp, "ci-test-learn.dmn"))
            http = FakeHttp()
            self.assertEqual(deploy.run(args(dir=tmp), client=self.client(http), env=ENV), 2)
            shutil.copy(os.path.join(CAMUNDA_DIR, "ci-test-memory.dmn"), os.path.join(tmp, "ci-test-learn.dmn"))
            self.assertEqual(deploy.run(args(dir=tmp), client=self.client(http), env=ENV), 2, "wrong model under a known name")
            self.assertEqual(http.calls, [])
        finally:
            shutil.rmtree(tmp)


class RedirectTest(unittest.TestCase):
    """A 30x from the cluster or the identity provider is an answer: the bearer token never follows it elsewhere."""

    def serve(self, handler):
        srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return "http://127.0.0.1:%d" % srv.server_port

    def test_redirect_is_not_followed(self):
        seen = []

        class Elsewhere(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        elsewhere = self.serve(Elsewhere)

        class Cluster(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(302)
                self.send_header("Location", elsewhere + "/steal")
                self.end_headers()

            def log_message(self, *a):
                pass

        status, _ = _urlopen("POST", self.serve(Cluster) + "/v2/deployments", {"Authorization": "Bearer t0ken"}, b"x", 5.0)
        self.assertEqual(status, 302)
        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()
