"""The cluster-side bootstrap scripts (camunda/bootstrap/), run against a stub `curl` that records each request."""

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

BOOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "camunda", "bootstrap")

STUB = r"""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
body = None
if "--data-binary" in args and args[args.index("--data-binary") + 1] == "@-":
    body = sys.stdin.read()
elif "--data-binary" in args:
    body = open(args[args.index("--data-binary") + 1][1:]).read()
hdr = args[args.index("-H") + 1][1:] if "-H" in args else None
headers = open(hdr).read() if hdr else ""
with open(os.environ["STUB_LOG"], "a") as fh:
    fh.write(json.dumps({"args": args, "body": body, "headers": headers}) + "\n")
url = [a for a in args if a.startswith("https://")][0]
if "-w" in args:
    print("200" if url.endswith(os.environ.get("STUB_EXISTING_ALIAS", "-none-")) else "404", end="")
elif url.endswith("/_security/api_key"):
    print(json.dumps({"id": "k", "encoded": "ENCODEDKEY"}))
elif url.endswith("/authorizations"):
    print(json.dumps({"authorizationKey": "22517998136%05d" % len(open(os.environ["STUB_LOG"]).readlines())}))
else:
    print("{}")
"""


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "needs bash and jq")
class BootstrapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "bin"))
        stub = os.path.join(self.tmp, "bin", "curl")
        with open(stub, "w") as fh:
            fh.write(STUB)
        os.chmod(stub, 0o755)
        self.log = os.path.join(self.tmp, "log")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_script(self, name, **env):
        e = {"PATH": os.path.join(self.tmp, "bin") + os.pathsep + os.environ["PATH"], "STUB_LOG": self.log}
        e.update(env)
        p = subprocess.run([os.path.join(BOOT, name)], env=e, capture_output=True, text=True, timeout=60)
        calls = [json.loads(x) for x in open(self.log)] if os.path.exists(self.log) else []
        return p, calls

    def test_es_bootstrap(self):
        key_file = os.path.join(self.tmp, "key")
        p, calls = self.run_script("es-bootstrap.sh", ES_URL="https://es.example.test:9200/", ES_ADMIN_API_KEY="ADMINKEY",
                                   REPLICAS="0", KEY_FILE=key_file, STUB_EXISTING_ALIAS="ci-planner-weights")
        self.assertEqual(p.returncode, 0, p.stderr)
        urls = [[a for a in c["args"] if a.startswith("https://")][0] for c in calls]
        self.assertEqual(urls[0], "https://es.example.test:9200/_ilm/policy/ci-planner-retention")
        created = [u.rsplit("/", 1)[1] for c, u in zip(calls, urls) if "PUT" in c["args"] and u.endswith("-000001")]
        self.assertEqual(created, ["ci-planner-decisions-000001", "ci-planner-outcomes-000001", "ci-planner-escapes-000001"],
                         "an existing alias is left alone")
        first = json.loads([c for c in calls if "ci-planner-decisions-000001" in " ".join(c["args"])][0]["body"])
        self.assertEqual(first["aliases"], {"ci-planner-decisions": {"is_write_index": True}})
        self.assertEqual(first["settings"]["number_of_replicas"], 0)
        self.assertEqual(first["settings"]["index.lifecycle.rollover_alias"], "ci-planner-decisions")
        key_req = json.loads(calls[-1]["body"])
        self.assertEqual(key_req["role_descriptors"]["ci_planner"]["indices"][0]["names"], ["ci-planner-*"])
        self.assertEqual(open(key_file).read(), "ENCODEDKEY", "no trailing newline in the secret")
        self.assertEqual(stat.S_IMODE(os.stat(key_file).st_mode), 0o600)
        for c in calls:
            self.assertNotIn("ADMINKEY", " ".join(c["args"]), "the admin key never is a command line argument")
            self.assertIn("Authorization: ApiKey ADMINKEY", c["headers"])
        self.assertNotIn("ENCODEDKEY", p.stdout + p.stderr)
        self.assertNotIn("ADMINKEY", p.stdout + p.stderr)

    def test_es_bootstrap_refuses_plain_http(self):
        p, calls = self.run_script("es-bootstrap.sh", ES_URL="http://es.example.test:9200", ES_ADMIN_API_KEY="x")
        self.assertEqual((p.returncode, calls), (2, []))

    def test_authorizations(self):
        tok = os.path.join(self.tmp, "token")
        with open(tok, "w") as fh:
            fh.write("ADMINTOKEN\n")
        p, calls = self.run_script("authorizations.sh", CAMUNDA_BASE_URL="https://oc.example.test/v2/", ADMIN_TOKEN_FILE=tok,
                                   PLANNER_CLIENT_ID="ci-planner", RECORDER_CLIENT_ID="ci-recorder", DEPLOYER_CLIENT_ID="ci-deployer")
        self.assertEqual(p.returncode, 0, p.stderr)
        bodies = [json.loads(c["body"]) for c in calls]
        self.assertEqual([(b["ownerId"], b["ownerType"], b["resourceType"], b["resourceId"], b["permissionTypes"]) for b in bodies], [
            ("ci-planner", "CLIENT", "PROCESS_DEFINITION", "ci-test-planner", ["CREATE_PROCESS_INSTANCE"]),
            ("ci-recorder", "CLIENT", "PROCESS_DEFINITION", "ci-test-record", ["CREATE_PROCESS_INSTANCE"]),
            ("ci-deployer", "CLIENT", "RESOURCE", "*", ["CREATE"])])
        self.assertTrue(all("https://oc.example.test/v2/authorizations" in c["args"] for c in calls))
        self.assertTrue(all("Authorization: Bearer ADMINTOKEN\n" in c["headers"] for c in calls))
        self.assertNotIn("ADMINTOKEN", p.stdout + p.stderr)
        self.assertEqual(len(p.stdout.splitlines()), 3)

    def test_authorizations_skip_unset_clients(self):
        tok = os.path.join(self.tmp, "token")
        open(tok, "w").write("t")
        p, calls = self.run_script("authorizations.sh", CAMUNDA_BASE_URL="https://oc.example.test/v2", ADMIN_TOKEN_FILE=tok, PLANNER_CLIENT_ID="ci-planner")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
