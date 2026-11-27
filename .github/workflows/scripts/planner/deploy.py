"""`planner deploy-models`: the six generated BPMN/DMN files, deployed to an Orchestration Cluster in one request.

Used only by the manual workflow `planner-deploy.yml`, with the deployer client. That client has RESOURCE CREATE,
which is effectively remote code execution on the cluster, so it is never used by the planner or the collector,
and its secret is only sent to the cluster the GitHub environment allows (`PLANNER_DEPLOY_ALLOWED_*`).

`POST /v2/deployments` is atomic: either all six resources are deployed or none. The answer is checked against
the expected process and decision ids; only ids, versions and keys are printed.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
import xml.etree.ElementTree as ET

from .camunda import CamundaError, Client

RESOURCES = ("ci-test-planner.bpmn", "ci-test-record.bpmn", "ci-test-learn.bpmn",
             "ci-test-planner.dmn", "ci-test-memory.dmn", "ci-test-learn.dmn")
PROCESSES = {"ci-test-planner.bpmn": "ci-test-planner", "ci-test-record.bpmn": "ci-test-record", "ci-test-learn.bpmn": "ci-test-learn"}
DRGS = {"ci-test-planner.dmn": "ci_test_planner", "ci-test-memory.dmn": "ci_test_memory", "ci-test-learn.dmn": "ci_test_learn"}
# the decisions the processes call (business rule tasks); the others are helpers inside the same DRGs
CALLED_DECISIONS = {"plan", "memory", "learned"}
MAX_BYTES = 2 * 1024 * 1024
BPMN_NS = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
DMN_NS = "{https://www.omg.org/spec/DMN/20191111/MODEL/}"


def load(directory: str) -> list:
    """[(name, bytes)] of the six files, in a fixed order; raises CamundaError when one is missing or not the expected model."""
    files = []
    for name in RESOURCES:
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            raise CamundaError("missing resource %s" % name)
        with open(path, "rb") as fh:
            data = fh.read()
        if not data or len(data) > MAX_BYTES:
            raise CamundaError("resource %s is empty or larger than %d bytes" % (name, MAX_BYTES))
        try:
            root = ET.fromstring(data)
        except ET.ParseError as e:
            raise CamundaError("resource %s is not XML: %s" % (name, e))
        if name in PROCESSES:
            ids = [p.get("id") for p in root.findall(BPMN_NS + "process")]
            if ids != [PROCESSES[name]]:
                raise CamundaError("resource %s defines processes %s, expected %s" % (name, ids, PROCESSES[name]))
        elif root.tag != DMN_NS + "definitions" or root.get("id") != DRGS[name]:
            raise CamundaError("resource %s is not the DMN definitions %s" % (name, DRGS[name]))
        files.append((name, data))
    return files


def digest(files: list) -> str:
    h = hashlib.sha256()
    for name, data in files:
        h.update(name.encode() + b"\0" + hashlib.sha256(data).hexdigest().encode() + b"\n")
    return h.hexdigest()


def multipart(files: list, tenant_id: str = "", boundary: str | None = None):
    """(content type, body) of a multipart/form-data request: one `resources` part per file, optional `tenantId`."""
    b = boundary or "planner-" + uuid.uuid4().hex
    parts = []
    for name, data in files:
        ctype = "application/xml"
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"resources\"; filename=\"%s\"\r\nContent-Type: %s\r\n\r\n"
                      % (b, name, ctype)).encode() + data + b"\r\n")
    if tenant_id:
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"tenantId\"\r\n\r\n%s\r\n" % (b, tenant_id)).encode())
    parts.append(("--%s--\r\n" % b).encode())
    return "multipart/form-data; boundary=%s" % b, b"".join(parts)


def check_result(res: dict, tenant_id: str = "") -> tuple:
    """(rows, errors) for a DeploymentResult. rows: [(kind, id, version, key, resource)]."""
    errors, rows = [], []
    if not isinstance(res, dict) or not res.get("deploymentKey"):
        return rows, ["the answer has no deploymentKey"]
    if tenant_id and res.get("tenantId") != tenant_id:
        errors.append("deployed to tenant %r, expected %r" % (res.get("tenantId"), tenant_id))
    procs, drgs, decisions, names = {}, {}, set(), set()
    for item in res.get("deployments") or []:
        if not isinstance(item, dict):
            continue
        p, d, r = item.get("processDefinition"), item.get("decisionDefinition"), item.get("decisionRequirements")
        if isinstance(p, dict):
            procs[p.get("processDefinitionId")] = p
            names.add(p.get("resourceName"))
            rows.append(("process", p.get("processDefinitionId"), p.get("processDefinitionVersion"), p.get("processDefinitionKey"), p.get("resourceName")))
        if isinstance(r, dict):
            drgs[r.get("decisionRequirementsId")] = r
            names.add(r.get("resourceName"))
            rows.append(("decisions", r.get("decisionRequirementsId"), r.get("version"), r.get("decisionRequirementsKey"), r.get("resourceName")))
        if isinstance(d, dict):
            decisions.add(d.get("decisionDefinitionId"))
    for want in sorted(set(PROCESSES.values()) - set(procs)):
        errors.append("process %s missing from the answer" % want)
    for want in sorted(set(DRGS.values()) - set(drgs)):
        errors.append("decision requirements %s missing from the answer" % want)
    for want in sorted(CALLED_DECISIONS - decisions):
        errors.append("decision %s missing from the answer" % want)
    for want in sorted(set(RESOURCES) - names):
        errors.append("resource %s missing from the answer" % want)
    for kind, rid, version, key, _ in rows:
        if not isinstance(version, int) or version < 1 or not key:
            errors.append("%s %s has no version or key" % (kind, rid))
    rows.sort(key=lambda r: (r[0] != "process", str(r[1])))
    return rows, errors


def check_target(env) -> None:
    """The deployer secret only goes to the cluster the GitHub environment allows: the inputs must match it."""
    for k in ("CAMUNDA_BASE_URL", "CAMUNDA_TOKEN_URL"):
        allowed = (env.get("PLANNER_DEPLOY_ALLOWED_" + k[len("CAMUNDA_"):]) or "").rstrip("/")
        if not allowed:
            raise CamundaError("PLANNER_DEPLOY_ALLOWED_%s is not set in the deployment environment" % k[len("CAMUNDA_"):])
        if (env.get(k) or "").rstrip("/") != allowed:
            raise CamundaError("%s does not match the URL the deployment environment allows" % k)


def summary_md(rows: list, res: dict, files: list, dry_run: bool) -> str:
    L = ["## Planner models", ""]
    if dry_run:
        L += ["Dry run: nothing was sent. Bundle sha256 `%s`." % digest(files), "", "| Resource | sha256 |", "|---|---|"]
        L += ["| `%s` | `%s` |" % (n, hashlib.sha256(d).hexdigest()[:16]) for n, d in files]
        return "\n".join(L) + "\n"
    L += ["Deployment `%s` (tenant `%s`), bundle sha256 `%s`." % (res.get("deploymentKey"), res.get("tenantId"), digest(files)), "",
          "| Kind | Id | Version | Key | Resource |", "|---|---|---|---|---|"]
    L += ["| %s | `%s` | %s | `%s` | `%s` |" % r for r in rows]
    return "\n".join(L) + "\n"


def run(a, client: Client | None = None, env=None) -> int:
    env = os.environ if env is None else env
    try:
        files = load(a.dir)
        check_target(env)
        for k in ("CAMUNDA_BASE_URL", "CAMUNDA_TOKEN_URL"):
            if not (env.get(k) or "").startswith("https://"):
                raise CamundaError("%s must be an https URL" % k)
        c = None if a.dry_run else (client or Client.from_env(env, timeout=120.0))
    except CamundaError as e:
        print("::error::deploy-models: %s" % e)
        return 2
    print("resources: %s (bundle sha256 %s)" % (", ".join(n for n, _ in files), digest(files)))
    if a.dry_run:
        md = summary_md([], {}, files, True)
        res, rows = {"dryRun": True, "bundle": digest(files)}, []
    else:
        try:
            res = c.deploy(files, a.tenant_id or "")
        except (CamundaError, OSError) as e:  # OSError: network, TLS, timeout
            print("::error::deploy-models: %s" % e)
            return 1
        rows, errors = check_result(res, a.tenant_id or "")
        for e in errors:
            print("::error::deploy-models: %s" % e)
        if errors:
            return 1
        md = summary_md(rows, res, files, False)
    print(md)
    if a.summary_file:
        with open(a.summary_file, "a") as fh:
            fh.write(md)
    if a.out:
        with open(a.out, "w") as fh:
            json.dump({"deploymentKey": res.get("deploymentKey"), "tenantId": res.get("tenantId"), "bundle": digest(files),
                       "dryRun": bool(a.dry_run), "resources": [dict(zip(("kind", "id", "version", "key", "resource"), r)) for r in rows]},
                      fh, indent=1, sort_keys=True)
    return 0
