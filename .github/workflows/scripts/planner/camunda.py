"""Client for the Camunda 8 Orchestration Cluster REST API (stdlib only).

Used by the GitHub job that holds the Camunda credentials. Everything fails open: any error, timeout
or malformed answer yields a "fail-open" plan, and the suites run as the path filters decide.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from . import schema
from .util import now_iso

PROCESS_ID = "ci-test-planner"
RECORD_PROCESS_ID = "ci-test-record"


class CamundaError(RuntimeError):
    pass


def _urlopen(method: str, url: str, headers: dict, body: bytes | None, timeout: float):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (https URL from configuration)
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


class Client:
    def __init__(self, base_url: str, token_url: str, client_id: str, client_secret: str, audience: str = "", scope: str = "",
                 http=None, timeout: float = 30.0):
        self.base = base_url.rstrip("/")
        self.token_url, self.client_id, self.client_secret = token_url, client_id, client_secret
        self.audience, self.scope = audience, scope
        self.http = http or _urlopen
        self.timeout = timeout

    @classmethod
    def from_env(cls, env=None, http=None, timeout: float = 30.0):
        env = os.environ if env is None else env
        need = ("CAMUNDA_BASE_URL", "CAMUNDA_TOKEN_URL", "CAMUNDA_CLIENT_ID", "CAMUNDA_CLIENT_SECRET")
        missing = [k for k in need if not env.get(k)]
        if missing:
            raise CamundaError("not configured: %s is empty" % ", ".join(missing))
        base = env["CAMUNDA_BASE_URL"].rstrip("/")
        for k in ("CAMUNDA_BASE_URL", "CAMUNDA_TOKEN_URL"):
            if not env[k].startswith("https://"):
                raise CamundaError("%s must be an https URL" % k)
        return cls(base, env["CAMUNDA_TOKEN_URL"], env["CAMUNDA_CLIENT_ID"], env["CAMUNDA_CLIENT_SECRET"],
                   env.get("CAMUNDA_AUDIENCE", ""), env.get("CAMUNDA_SCOPE", ""), http=http, timeout=timeout)

    def token(self) -> str:
        form = {"grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self.client_secret}
        if self.audience:
            form["audience"] = self.audience
        if self.scope:
            form["scope"] = self.scope
        status, text = self.http("POST", self.token_url, {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                                 urllib.parse.urlencode(form).encode(), self.timeout)
        if status != 200:
            raise CamundaError("token endpoint answered %d" % status)
        try:
            return json.loads(text)["access_token"]
        except (ValueError, KeyError):
            raise CamundaError("token endpoint answered without an access token")

    def create_instance(self, process_id: str, variables: dict, await_ms: int = 0, fetch: list | None = None, business_id: str | None = None) -> dict:
        body = {"processDefinitionId": process_id, "variables": variables}
        if await_ms:
            body.update({"awaitCompletion": True, "requestTimeout": int(await_ms), "fetchVariables": fetch or []})
        if business_id:
            body["businessId"] = business_id
        # the HTTP timeout sits a little above the server-side wait, so the server answers (504) first
        timeout = (await_ms / 1000.0 + 5.0) if await_ms else self.timeout
        status, text = self.http("POST", self.base + "/process-instances", {"Authorization": "Bearer " + self.token(),
                                 "Content-Type": "application/json", "Accept": "application/json"}, json.dumps(body).encode(), timeout)
        if status != 200:
            raise CamundaError("create process instance answered %d (%s)" % (status, text[:120].replace("\n", " ")))
        try:
            return json.loads(text)
        except ValueError:
            raise CamundaError("create process instance answered with invalid JSON")


def fail_open(facts: dict, reason: str) -> dict:
    """The plan used when Camunda cannot answer: nothing is skipped, the suites run as the path filters decide."""
    return {"schema": schema.PLAN_SCHEMA, "source": "fail-open", "reason": reason[:300], "decision_id": None, "repo": facts["repo"],
            "pr": facts["pr"], "head_sha": facts["head_sha"], "mode": "report", "generated_at": now_iso(), "decision": {},
            "rotation_picked": None, "modules": facts["impacted"]["modules"][:200], "consumers": facts["impacted"]["consumers"],
            "rules_fired": [], "advisor_added": [], "recorded": False}


def plan_from_result(facts: dict, variables: dict) -> dict:
    """Turn the process result (variable `merged`, `decisionId`, `recorded`) into a planner.plan/1 document."""
    merged = variables.get("merged")
    if not isinstance(merged, dict) or not isinstance(merged.get("final"), dict):
        raise CamundaError("the process answered without a plan")
    final = merged["final"]
    decision = {}
    for d in final.get("decision") or []:
        decision[d["suite"]] = {"action": d["action"], "enforced": bool(d["enforced"]), "layer": d["layer"], "reasons": [str(r)[:300] for r in d["reasons"]][:20]}
    rot = final.get("rotation")
    plan = {"schema": schema.PLAN_SCHEMA, "source": "camunda", "reason": "decided by the Camunda process", "decision_id": variables.get("decisionId"),
            "repo": facts["repo"], "pr": facts["pr"], "head_sha": facts["head_sha"], "mode": final.get("mode", "report"), "generated_at": now_iso(),
            "decision": decision, "rotation_picked": rot.get("picked") if isinstance(rot, dict) else None,
            "modules": facts["impacted"]["modules"][:200], "consumers": facts["impacted"]["consumers"],
            "rules_fired": [{"rule": str(r["rule"]), "subject": str(r["subject"])[:300], "reason": str(r["reason"])[:500]} for r in final.get("rulesFired") or []][:100],
            "advisor_added": list((merged.get("advisor") or {}).get("added") or [])[:20], "recorded": bool(variables.get("recorded") not in (None, False))}
    errs = schema.validate_plan(plan)
    if errs:
        raise CamundaError("the plan is invalid: %s" % errs[0])
    return plan


def decide(facts: dict, client: Client | None, advisor: bool, run_ref: str, await_s: float = 30.0) -> dict:
    """Ask the process. Never raises: returns a fail-open plan on any problem."""
    errs = schema.validate_facts(facts)
    if errs:
        return fail_open(facts, "facts rejected: %s" % errs[0])
    try:
        c = client or Client.from_env()
        res = c.create_instance(PROCESS_ID, {"facts": facts, "advisorEnabled": bool(advisor)}, int(await_s * 1000),
                                ["merged", "decisionId", "recorded"], "%s#%d@%s/%s" % (facts["repo"], facts["pr"], facts["head_sha"][:12], run_ref)[:200])
        return plan_from_result(facts, res.get("variables") or {})
    except CamundaError as e:
        return fail_open(facts, str(e))
    except (OSError, ValueError, KeyError, TypeError) as e:  # network, timeout, malformed answer
        return fail_open(facts, "%s: %s" % (type(e).__name__, str(e)[:150]))
