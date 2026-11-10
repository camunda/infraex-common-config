"""Event schema v1: strict validation of everything that is written to the store.

The recorder treats the planner artifact as hostile (it was produced by a job that read an
untrusted pull request), so validation is closed: unknown keys are rejected, strings and lists
are capped, and every value that ends up in a file name is checked against a strict pattern.
"""

from __future__ import annotations

import re

from . import SCHEMA
from .util import PATH_RE, SHA_RE, SUITE_RE, canonical

MAX_EVENT_BYTES = 256 * 1024
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
ID_RE = re.compile(r"^[a-z]{3,8}_[0-9a-f]{12}$")
DECISION_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")  # dec_<hash> offline, an Elasticsearch id with Camunda
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
EVENT_TYPES = ("decision", "outcome", "escape", "weights", "checkpoint", "watermark", "override")
MODES = ("report", "gate")
ACTIONS = ("run", "skip")
OUTCOMES = ("pass", "fail", "blocked", "known_failure", "cancelled")


class Invalid(ValueError):
    pass


def _err(errors: list, where: str, msg: str):
    if len(errors) < 50:
        errors.append("%s: %s" % (where, msg))


def _str(errors, where, v, maxlen=200, rx=None, allow_empty=True):
    if not isinstance(v, str):
        return _err(errors, where, "expected string")
    if len(v) > maxlen:
        return _err(errors, where, "longer than %d" % maxlen)
    if not allow_empty and not v:
        return _err(errors, where, "empty")
    if rx is not None and not rx.match(v):
        return _err(errors, where, "does not match %s" % rx.pattern)


def _int(errors, where, v, lo=0, hi=10**9):
    if isinstance(v, bool) or not isinstance(v, int) or v < lo or v > hi:
        _err(errors, where, "expected int in [%d, %d]" % (lo, hi))


def _num(errors, where, v, lo=-1e12, hi=1e12):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < lo or v > hi:
        _err(errors, where, "expected number")


def _bool(errors, where, v):
    if not isinstance(v, bool):
        _err(errors, where, "expected bool")


def _list(errors, where, v, maxlen=500):
    if not isinstance(v, list):
        _err(errors, where, "expected list")
        return []
    if len(v) > maxlen:
        _err(errors, where, "more than %d items" % maxlen)
        return v[:maxlen]
    return v


def _dict(errors, where, v, keys=None, required=None, maxlen=500):
    if not isinstance(v, dict):
        _err(errors, where, "expected object")
        return {}
    if len(v) > maxlen:
        _err(errors, where, "more than %d keys" % maxlen)
    if keys is not None:
        for k in v:
            if k not in keys:
                _err(errors, where, "unknown key %r" % k)
    for k in (required if required is not None else keys or ()):
        if k not in v:
            _err(errors, where, "missing key %r" % k)
    return v


def _strs(errors, where, v, maxlen=100, itemlen=300, rx=None):
    for i, x in enumerate(_list(errors, where, v, maxlen)):
        _str(errors, "%s[%d]" % (where, i), x, itemlen, rx)


def _opt(v):
    return v is None


def _common(e, errors, typ):
    _dict(errors, "event", e)
    if e.get("schema") != SCHEMA:
        _err(errors, "schema", "expected %s" % SCHEMA)
    if e.get("type") != typ:
        _err(errors, "type", "expected %s" % typ)
    _str(errors, "id", e.get("id"), 40, ID_RE)
    _str(errors, "ts", e.get("ts"), 20, TS_RE)
    _str(errors, "repo", e.get("repo"), 200, REPO_RE)


DECISION_KEYS = {
    "schema", "type", "id", "ts", "repo", "pr", "head_sha", "base_sha", "merge_base", "event", "actor",
    "head_repo_is_fork", "mode", "planner_version", "rules_version", "weights_version", "registry_ref",
    "plumbing_change", "judge_changed", "changed_files", "impacted", "rules_fired", "memory_inputs",
    "rotation", "decision", "overrides", "safety_net", "labels_hash", "labels",
}


def _decision(e, errors):
    _common(e, errors, "decision")
    _dict(errors, "decision", e, keys=DECISION_KEYS, required=DECISION_KEYS - {"labels"})
    _int(errors, "pr", e.get("pr"), 0, 10**7)
    for k in ("head_sha", "base_sha", "merge_base", "registry_ref"):
        _str(errors, k, e.get(k), 40, SHA_RE)
    _str(errors, "event", e.get("event"), 40, re.compile(r"^[a-z_]{1,40}$"))
    _str(errors, "actor", e.get("actor"), 100, re.compile(r"^[A-Za-z0-9_.\[\]-]{0,100}$"))
    _bool(errors, "head_repo_is_fork", e.get("head_repo_is_fork"))
    if e.get("mode") not in MODES:
        _err(errors, "mode", "expected one of %s" % (MODES,))
    for k in ("planner_version", "rules_version", "weights_version", "labels_hash"):
        _str(errors, k, e.get(k), 80, re.compile(r"^[A-Za-z0-9_.:+-]{1,80}$"))
    _bool(errors, "plumbing_change", e.get("plumbing_change"))
    _bool(errors, "judge_changed", e.get("judge_changed"))
    cf = _dict(errors, "changed_files", e.get("changed_files"), keys={"count", "sha256", "paths", "truncated"})
    _int(errors, "changed_files.count", cf.get("count"))
    _str(errors, "changed_files.sha256", cf.get("sha256"), 64, re.compile(r"^[0-9a-f]{64}$"))
    _strs(errors, "changed_files.paths", cf.get("paths"), 200, 300, PATH_RE)
    _bool(errors, "changed_files.truncated", cf.get("truncated"))
    imp = _dict(errors, "impacted", e.get("impacted"), keys={"modules", "ras", "suites_path_triggered", "engine_pins", "interface", "consumers"})
    _strs(errors, "impacted.modules", imp.get("modules"), 200, 300, PATH_RE)
    _strs(errors, "impacted.ras", imp.get("ras"), 200, 300, PATH_RE)
    _strs(errors, "impacted.suites_path_triggered", imp.get("suites_path_triggered"), 200, 100, SUITE_RE)
    cons = _dict(errors, "impacted.consumers", imp.get("consumers"), maxlen=200)
    for k, v in cons.items():
        _str(errors, "consumers key", k, 300, PATH_RE)
        _strs(errors, "consumers[%s]" % k, v, 100, 100, SUITE_RE)
    for i, p in enumerate(_list(errors, "impacted.engine_pins", imp.get("engine_pins"), 100)):
        d = _dict(errors, "engine_pin[%d]" % i, p, keys={"dep", "from", "to", "major"})
        _str(errors, "engine_pin.dep", d.get("dep"), 100)
        _str(errors, "engine_pin.from", d.get("from"), 50)
        _str(errors, "engine_pin.to", d.get("to"), 50)
        _bool(errors, "engine_pin.major", d.get("major"))
    for i, p in enumerate(_list(errors, "impacted.interface", imp.get("interface"), 100)):
        d = _dict(errors, "interface[%d]" % i, p, maxlen=20)
        _str(errors, "interface.module", d.get("module"), 300, PATH_RE)
        _bool(errors, "interface.significant", d.get("significant"))
        for k in ("removed_outputs", "added_outputs", "removed_variables", "new_required_variables", "changed_variable_types", "changed_defaults"):
            _strs(errors, "interface." + k, d.get(k, []), 100, 100)
        _bool(errors, "interface.possible_output_rename", d.get("possible_output_rename", False))
    for i, r in enumerate(_list(errors, "rules_fired", e.get("rules_fired"), 200)):
        d = _dict(errors, "rules_fired[%d]" % i, r, keys={"rule", "subject", "reason", "evidence"})
        _str(errors, "rule", d.get("rule"), 10, re.compile(r"^R\d{1,2}[a-z]?$"))
        _str(errors, "subject", d.get("subject"), 300)
        _str(errors, "reason", d.get("reason"), 500)
        _strs(errors, "evidence", d.get("evidence"), 20, 300)
    for i, m in enumerate(_list(errors, "memory_inputs", e.get("memory_inputs"), 200)):
        d = _dict(errors, "memory_inputs[%d]" % i, m, keys={"suite", "source", "last_full_success_at", "age_days", "weight"})
        _str(errors, "memory.suite", d.get("suite"), 100, SUITE_RE)
        _str(errors, "memory.source", d.get("source"), 30, re.compile(r"^[a-z-]{1,30}$"))
        if d.get("last_full_success_at") is not None:
            _str(errors, "memory.last", d.get("last_full_success_at"), 20, TS_RE)
        if d.get("age_days") is not None:
            _num(errors, "memory.age", d.get("age_days"), 0, 10**5)
        _num(errors, "memory.weight", d.get("weight"), 0, 100)
    rot = e.get("rotation")
    if rot is not None:
        d = _dict(errors, "rotation", rot, keys={"reasons", "candidates", "picked"})
        _strs(errors, "rotation.reasons", d.get("reasons"), 50, 300)
        _str(errors, "rotation.picked", d.get("picked"), 100, SUITE_RE)
        for i, c in enumerate(_list(errors, "rotation.candidates", d.get("candidates"), 100)):
            cd = _dict(errors, "candidate[%d]" % i, c, keys={"suite", "age_days", "weight", "score", "cost_min"})
            _str(errors, "candidate.suite", cd.get("suite"), 100, SUITE_RE)
            for k in ("age_days", "cost_min"):
                if cd.get(k) is not None:
                    _num(errors, "candidate." + k, cd.get(k), 0, 10**6)
            _num(errors, "candidate.weight", cd.get("weight"), 0, 100)
            _num(errors, "candidate.score", cd.get("score"), 0, 10**9)
    dec = _dict(errors, "decision", e.get("decision"), maxlen=300)
    for name, v in dec.items():
        _str(errors, "decision key", name, 100, SUITE_RE)
        d = _dict(errors, "decision[%s]" % name, v, keys={"action", "enforced", "layer", "reasons", "scenario"}, required={"action", "enforced", "layer", "reasons"})
        if d.get("action") not in ACTIONS:
            _err(errors, "decision.action", "expected run|skip")
        _bool(errors, "decision.enforced", d.get("enforced"))
        _str(errors, "decision.layer", d.get("layer"), 20, re.compile(r"^[A-Za-z0-9]{1,20}$"))
        _strs(errors, "decision.reasons", d.get("reasons"), 20, 300)
    ov = _dict(errors, "overrides", e.get("overrides"), keys={"labels", "applied", "conflicts"})
    for k in ("labels", "applied"):
        _strs(errors, "overrides." + k, ov.get(k), 100, 100, re.compile(r"^[A-Za-z0-9_.:-]{1,100}$"))
    _strs(errors, "overrides.conflicts", ov.get("conflicts"), 100, 200)
    sn = _dict(errors, "safety_net", e.get("safety_net"), maxlen=300)
    for name, v in sn.items():
        _str(errors, "safety_net key", name, 100, SUITE_RE)
        d = _dict(errors, "safety_net[%s]" % name, v, keys={"next_scheduled", "last_full_success_at"})
        _str(errors, "safety_net.next", d.get("next_scheduled"), 100)
        if d.get("last_full_success_at") is not None:
            _str(errors, "safety_net.last", d.get("last_full_success_at"), 20, TS_RE)
    if "labels" in e:
        _strs(errors, "labels", e.get("labels"), 100, 100, re.compile(r"^[A-Za-z0-9_.:-]{1,100}$"))


def _outcome(e, errors):
    keys = {"schema", "type", "id", "ts", "repo", "decision_id", "pr", "head_sha", "suite", "run_id", "attempt",
            "event", "branch", "conclusion", "started", "completed", "minutes", "url", "flake_hint",
            "outcome", "reason", "known_failure_id", "modules", "full"}
    _common(e, errors, "outcome")
    _dict(errors, "outcome", e, keys=keys, required=keys - {"outcome", "reason", "known_failure_id", "modules", "full"})
    if "outcome" in e and e["outcome"] not in OUTCOMES:
        _err(errors, "outcome.outcome", "expected one of %s" % (OUTCOMES,))
    if "reason" in e:
        _str(errors, "outcome.reason", e.get("reason"), 200)
    if "known_failure_id" in e:
        _str(errors, "outcome.known_failure_id", e.get("known_failure_id"), 100, re.compile(r"^[A-Za-z0-9_.-]{1,100}$"))
    if "modules" in e:
        _strs(errors, "outcome.modules", e.get("modules"), 100, 300, PATH_RE)
    if "full" in e:
        _bool(errors, "outcome.full", e.get("full"))
    if e.get("decision_id") is not None:
        _str(errors, "decision_id", e.get("decision_id"), 200, DECISION_ID_RE)
    _int(errors, "pr", e.get("pr"), 0, 10**7)
    _str(errors, "head_sha", e.get("head_sha"), 40, SHA_RE)
    _str(errors, "suite", e.get("suite"), 100, SUITE_RE)
    _int(errors, "run_id", e.get("run_id"), 0, 10**15)
    _int(errors, "attempt", e.get("attempt"), 1, 1000)
    _str(errors, "event", e.get("event"), 40, re.compile(r"^[a-z_]{1,40}$"))
    _str(errors, "branch", e.get("branch"), 200)
    _str(errors, "conclusion", e.get("conclusion"), 30, re.compile(r"^[a-z_]{1,30}$"))
    _str(errors, "started", e.get("started"), 20, TS_RE)
    _str(errors, "completed", e.get("completed"), 20, TS_RE)
    _num(errors, "minutes", e.get("minutes"), 0, 10**5)
    _str(errors, "url", e.get("url"), 300)
    _bool(errors, "flake_hint", e.get("flake_hint"))


def _escape(e, errors):
    keys = {"schema", "type", "id", "ts", "repo", "suite", "failing_run", "good_run", "candidates", "confidence", "confirmed_by"}
    _common(e, errors, "escape")
    _dict(errors, "escape", e, keys=keys)
    _str(errors, "suite", e.get("suite"), 100, SUITE_RE)
    for k in ("failing_run", "good_run"):
        d = _dict(errors, k, e.get(k), keys={"id", "sha", "at"})
        _int(errors, k + ".id", d.get("id"), 0, 10**15)
        _str(errors, k + ".sha", d.get("sha"), 40, SHA_RE)
        _str(errors, k + ".at", d.get("at"), 20, TS_RE)
    for i, c in enumerate(_list(errors, "candidates", e.get("candidates"), 20)):
        d = _dict(errors, "candidates[%d]" % i, c, keys={"decision_id", "pr", "module", "share"})
        _str(errors, "candidate.decision_id", d.get("decision_id"), 200, DECISION_ID_RE)
        _int(errors, "candidate.pr", d.get("pr"), 0, 10**7)
        _str(errors, "candidate.module", d.get("module"), 300, PATH_RE)
        _num(errors, "candidate.share", d.get("share"), 0, 1)
    _num(errors, "confidence", e.get("confidence"), 0, 1)
    _str(errors, "confirmed_by", e.get("confirmed_by"), 100)


def _weights(e, errors):
    keys = {"schema", "type", "id", "ts", "repo", "window_days", "pairs", "suites", "prev", "diff", "params"}
    _common(e, errors, "weights")
    _dict(errors, "weights", e, keys=keys)
    _int(errors, "window_days", e.get("window_days"), 1, 3650)
    pairs = _dict(errors, "pairs", e.get("pairs"), maxlen=5000)
    for k, v in pairs.items():
        _str(errors, "pair key", k, 400)
        d = _dict(errors, "pair", v, keys={"risk", "weight", "n_run", "fail", "escape", "evidence"})
        for f in ("risk", "weight", "n_run", "fail", "escape"):
            _num(errors, "pair." + f, d.get(f), 0, 1e9)
        _strs(errors, "pair.evidence", d.get("evidence"), 50, 40, ID_RE)
    suites = _dict(errors, "suites", e.get("suites"), maxlen=500)
    for k, v in suites.items():
        _str(errors, "suite key", k, 100, SUITE_RE)
        d = _dict(errors, "suite", v, keys={"flake", "cost_min", "runs", "evidence"})
        for f in ("flake", "cost_min", "runs"):
            if d.get(f) is not None:
                _num(errors, "suite." + f, d.get(f), 0, 1e9)
        _strs(errors, "suite.evidence", d.get("evidence"), 50, 40, ID_RE)
    if e.get("prev") is not None:
        _str(errors, "prev", e.get("prev"), 40, ID_RE)
    _str(errors, "diff", e.get("diff"), 20000)
    _dict(errors, "params", e.get("params"), maxlen=60)


def _checkpoint(e, errors):
    _common(e, errors, "checkpoint")
    _dict(errors, "checkpoint", e, keys={"schema", "type", "id", "ts", "repo", "week", "files", "sha256", "head_commit"})
    _str(errors, "week", e.get("week"), 10, re.compile(r"^\d{4}-W\d{2}$"))
    _int(errors, "files", e.get("files"))
    _str(errors, "sha256", e.get("sha256"), 64, re.compile(r"^[0-9a-f]{64}$"))
    _str(errors, "head_commit", e.get("head_commit"), 40, SHA_RE)


def _watermark(e, errors):
    _common(e, errors, "watermark")
    _dict(errors, "watermark", e, keys={"schema", "type", "id", "ts", "repo", "since"})
    _str(errors, "since", e.get("since"), 20, TS_RE)


def _override(e, errors):
    _common(e, errors, "override")
    _dict(errors, "override", e, keys={"schema", "type", "id", "ts", "repo", "decision_id", "who", "labels"})
    _str(errors, "decision_id", e.get("decision_id"), 40, ID_RE)
    _str(errors, "who", e.get("who"), 100)
    _strs(errors, "labels", e.get("labels"), 50, 100)


_CHECKS = {"decision": _decision, "outcome": _outcome, "escape": _escape, "weights": _weights,
           "checkpoint": _checkpoint, "watermark": _watermark, "override": _override}


def validate(event) -> list:
    """Return a list of error strings (empty = valid)."""
    errors: list = []
    if not isinstance(event, dict):
        return ["event: expected object"]
    typ = event.get("type")
    if typ not in _CHECKS:
        return ["type: unknown event type %r" % (typ,)]
    try:
        size = len(canonical(event))
    except (TypeError, ValueError):
        return ["event: not JSON serialisable"]
    if size > MAX_EVENT_BYTES:
        return ["event: larger than %d bytes" % MAX_EVENT_BYTES]
    _CHECKS[typ](event, errors)
    return errors


def require_valid(event) -> None:
    errs = validate(event)
    if errs:
        raise Invalid("; ".join(errs[:10]))


# ---------------------------------------------------------------------------------------------
# planner.facts/1: what the GitHub job sends to the Camunda process. It is derived from an
# untrusted pull request, so the job that holds the Camunda secret validates it first.

FACTS_SCHEMA = "planner.facts/1"
SIGNAL_TYPES = ("module", "engine_major", "ra_root", "shared_action", "tool_versions", "workflow_changed")
SIGNAL_KEYS = {
    "module": {"type", "module", "owner_suites", "consumer_suites", "golden_suites", "iface_files_changed", "significant", "iface_evidence"},
    "engine_major": {"type", "dep", "from", "to", "files", "files_n", "modules", "owner_suites", "candidate_suites"},
    "ra_root": {"type", "ra", "pin_only", "files_n", "suites"},
    "shared_action": {"type", "name", "suites"},
    "tool_versions": {"type", "suites"},
    "workflow_changed": {"type", "suite"},
}
FACTS_KEYS = {
    "schema", "repo", "pr", "head_sha", "base_sha", "merge_base", "event", "actor", "head_repo_is_fork", "base_branch",
    "default_branch", "registry_ref", "planner_version", "requested_mode", "gate_base_branches", "gate_suites",
    "expensive_layers", "tie_percent", "labels", "docs_only", "judge_changed", "plumbing_change", "new_workflows",
    "changed_files", "suites", "signals", "impacted", "labels_hash",
}
MAX_FACTS_BYTES = 200 * 1024
BRANCH_RE = re.compile(r"^[A-Za-z0-9_./-]{1,100}$")


def validate_facts(facts) -> list:
    errors: list = []
    if not isinstance(facts, dict):
        return ["facts: expected object"]
    try:
        if len(canonical(facts)) > MAX_FACTS_BYTES:
            return ["facts: larger than %d bytes" % MAX_FACTS_BYTES]
    except (TypeError, ValueError):
        return ["facts: not JSON serialisable"]
    _dict(errors, "facts", facts, keys=FACTS_KEYS, required=FACTS_KEYS - {"labels_hash"})
    if facts.get("schema") != FACTS_SCHEMA:
        _err(errors, "schema", "expected %s" % FACTS_SCHEMA)
    _str(errors, "repo", facts.get("repo"), 200, REPO_RE)
    _int(errors, "pr", facts.get("pr"), 0, 10**7)
    for k in ("head_sha", "base_sha", "merge_base", "registry_ref"):
        _str(errors, k, facts.get(k), 40, SHA_RE)
    _str(errors, "event", facts.get("event"), 40, re.compile(r"^[a-z_]{1,40}$"))
    _str(errors, "actor", facts.get("actor"), 100, re.compile(r"^[A-Za-z0-9_.\[\]-]{0,100}$"))
    _bool(errors, "head_repo_is_fork", facts.get("head_repo_is_fork"))
    for k in ("base_branch", "default_branch"):
        _str(errors, k, facts.get(k), 100, BRANCH_RE)
    _str(errors, "planner_version", facts.get("planner_version"), 80, re.compile(r"^[A-Za-z0-9_.:+-]{1,80}$"))
    _str(errors, "requested_mode", facts.get("requested_mode"), 20, re.compile(r"^[a-z]{0,20}$"))
    _strs(errors, "gate_base_branches", facts.get("gate_base_branches"), 20, 100, BRANCH_RE)
    _strs(errors, "gate_suites", facts.get("gate_suites"), 100, 100, SUITE_RE)
    _strs(errors, "expensive_layers", facts.get("expensive_layers"), 10, 20, re.compile(r"^[A-Za-z0-9]{1,20}$"))
    _num(errors, "tie_percent", facts.get("tie_percent"), 0, 100)
    lab = _dict(errors, "labels", facts.get("labels"), keys={"skip_all", "skip", "run", "run_all", "seen"})
    _bool(errors, "labels.skip_all", lab.get("skip_all"))
    _bool(errors, "labels.run_all", lab.get("run_all"))
    for k in ("skip", "run"):
        _strs(errors, "labels." + k, lab.get(k), 100, 100, SUITE_RE)
    _strs(errors, "labels.seen", lab.get("seen"), 100, 100, re.compile(r"^[A-Za-z0-9_.:-]{1,100}$"))
    for k in ("docs_only", "judge_changed", "plumbing_change"):
        _bool(errors, k, facts.get(k))
    _strs(errors, "new_workflows", facts.get("new_workflows"), 50, 100, SUITE_RE)
    cf = _dict(errors, "changed_files", facts.get("changed_files"), keys={"count", "sha256", "paths", "truncated"})
    _int(errors, "changed_files.count", cf.get("count"))
    _str(errors, "changed_files.sha256", cf.get("sha256"), 64, re.compile(r"^[0-9a-f]{64}$"))
    _strs(errors, "changed_files.paths", cf.get("paths"), 200, 300, PATH_RE)
    _bool(errors, "changed_files.truncated", cf.get("truncated"))
    su = _dict(errors, "suites", facts.get("suites"), maxlen=300)
    for name, v in su.items():
        _str(errors, "suites key", name, 100, SUITE_RE)
        d = _dict(errors, "suites[%s]" % name, v, keys={"layer", "kind", "path_triggered", "primary", "next_scheduled"})
        _str(errors, "suite.layer", d.get("layer"), 20, re.compile(r"^[A-Za-z0-9]{1,20}$"))
        _str(errors, "suite.kind", d.get("kind"), 100)
        _bool(errors, "suite.path_triggered", d.get("path_triggered"))
        _bool(errors, "suite.primary", d.get("primary"))
        _str(errors, "suite.next_scheduled", d.get("next_scheduled"), 100)
    for i, sig in enumerate(_list(errors, "signals", facts.get("signals"), 200)):
        if not isinstance(sig, dict) or sig.get("type") not in SIGNAL_TYPES:
            _err(errors, "signals[%d]" % i, "unknown signal type")
            continue
        keys = SIGNAL_KEYS[sig["type"]]
        _dict(errors, "signals[%d]" % i, sig, keys=keys)
        for k in keys:
            v = sig.get(k)
            if k in ("type",):
                continue
            if k in ("module", "ra"):
                _str(errors, k, v, 300, PATH_RE)
            elif k in ("dep", "from", "to", "name", "suite"):
                _str(errors, k, v, 100)
            elif k in ("iface_files_changed", "significant", "pin_only"):
                _bool(errors, k, v)
            elif k == "files_n":
                _int(errors, k, v)
            elif k in ("files", "iface_evidence"):
                _strs(errors, k, v, 20, 300)
            elif k == "modules":
                _strs(errors, k, v, 100, 300, PATH_RE)
            else:
                _strs(errors, k, v, 100, 100, SUITE_RE)
    imp = _dict(errors, "impacted", facts.get("impacted"), keys={"modules", "ras", "suites_path_triggered", "engine_pins", "interface", "consumers"})
    _strs(errors, "impacted.modules", imp.get("modules"), 200, 300, PATH_RE)
    _strs(errors, "impacted.ras", imp.get("ras"), 200, 300, PATH_RE)
    _strs(errors, "impacted.suites_path_triggered", imp.get("suites_path_triggered"), 200, 100, SUITE_RE)
    for p in _list(errors, "impacted.engine_pins", imp.get("engine_pins"), 100):
        d = _dict(errors, "engine_pin", p, keys={"dep", "from", "to", "major"})
        _str(errors, "pin.dep", d.get("dep"), 100)
        _str(errors, "pin.from", d.get("from"), 50)
        _str(errors, "pin.to", d.get("to"), 50)
        _bool(errors, "pin.major", d.get("major"))
    for p in _list(errors, "impacted.interface", imp.get("interface"), 100):
        d = _dict(errors, "interface", p, maxlen=20)
        _str(errors, "interface.module", d.get("module"), 300, PATH_RE)
        _bool(errors, "interface.significant", d.get("significant"))
    cons = _dict(errors, "impacted.consumers", imp.get("consumers"), maxlen=200)
    for k, v in cons.items():
        _str(errors, "consumers key", k, 300, PATH_RE)
        _strs(errors, "consumers[%s]" % k, v, 100, 100, SUITE_RE)
    if "labels_hash" in facts:
        _str(errors, "labels_hash", facts.get("labels_hash"), 80, re.compile(r"^[A-Za-z0-9_.:+-]{1,80}$"))
    return errors


# ---------------------------------------------------------------------------------------------
# planner.plan/1: the answer the planner publishes as a check run for the suites' gate.

PLAN_SCHEMA = "planner.plan/1"
PLAN_KEYS = {"schema", "source", "reason", "decision_id", "repo", "pr", "head_sha", "mode", "generated_at", "decision", "rotation_picked",
             "modules", "consumers", "rules_fired", "advisor_added", "recorded"}


def validate_plan(plan) -> list:
    errors: list = []
    if not isinstance(plan, dict):
        return ["plan: expected object"]
    try:
        if len(canonical(plan)) > 60000:
            return ["plan: larger than 60000 bytes"]
    except (TypeError, ValueError):
        return ["plan: not JSON serialisable"]
    _dict(errors, "plan", plan, keys=PLAN_KEYS)
    if plan.get("schema") != PLAN_SCHEMA:
        _err(errors, "schema", "expected %s" % PLAN_SCHEMA)
    if plan.get("source") not in ("camunda", "fail-open"):
        _err(errors, "source", "expected camunda or fail-open")
    _str(errors, "reason", plan.get("reason"), 300)
    if plan.get("decision_id") is not None:
        _str(errors, "decision_id", plan.get("decision_id"), 200, re.compile(r"^[A-Za-z0-9_.:-]{1,200}$"))
    _str(errors, "repo", plan.get("repo"), 200, REPO_RE)
    _int(errors, "pr", plan.get("pr"), 0, 10**7)
    _str(errors, "head_sha", plan.get("head_sha"), 40, SHA_RE)
    if plan.get("mode") not in MODES:
        _err(errors, "mode", "expected one of %s" % (MODES,))
    _str(errors, "generated_at", plan.get("generated_at"), 20, TS_RE)
    dec = _dict(errors, "decision", plan.get("decision"), maxlen=300)
    for name, v in dec.items():
        _str(errors, "decision key", name, 100, SUITE_RE)
        d = _dict(errors, "decision[%s]" % name, v, keys={"action", "enforced", "layer", "reasons"})
        if d.get("action") not in ACTIONS:
            _err(errors, "decision.action", "expected run|skip")
        _bool(errors, "decision.enforced", d.get("enforced"))
        _str(errors, "decision.layer", d.get("layer"), 20, re.compile(r"^[A-Za-z0-9]{1,20}$"))
        _strs(errors, "decision.reasons", d.get("reasons"), 20, 300)
    if plan.get("rotation_picked") is not None:
        _str(errors, "rotation_picked", plan.get("rotation_picked"), 100, SUITE_RE)
    _strs(errors, "modules", plan.get("modules"), 200, 300, PATH_RE)
    cons = _dict(errors, "consumers", plan.get("consumers"), maxlen=200)
    for k, v in cons.items():
        _str(errors, "consumers key", k, 300, PATH_RE)
        _strs(errors, "consumers[%s]" % k, v, 100, 100, SUITE_RE)
    for i, r in enumerate(_list(errors, "rules_fired", plan.get("rules_fired"), 200)):
        d = _dict(errors, "rules_fired[%d]" % i, r, keys={"rule", "subject", "reason"})
        _str(errors, "rule", d.get("rule"), 10, re.compile(r"^R\d{1,2}[a-z]?$"))
        _str(errors, "subject", d.get("subject"), 300)
        _str(errors, "reason", d.get("reason"), 500)
    _strs(errors, "advisor_added", plan.get("advisor_added"), 20, 100, SUITE_RE)
    _bool(errors, "recorded", plan.get("recorded"))
    return errors
