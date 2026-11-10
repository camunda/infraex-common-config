"""The memory and learner DMN must compute what the Python reference computes.

Elasticsearch is emulated: the aggregation responses the process would receive are built from the
same documents the Python memory is built from. Real Elasticsearch percentiles are approximate, so the
median is compared with a tolerance.
"""

import copy
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta

from .. import config, learn, memory, schema
from ..util import event_id
from .test_dmn_conformance import HERE, NODE, READY

CFG = config.load(None)
NOW = "2026-11-02T20:00:00Z"


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def outcome(suite, ended, minutes=120, conclusion="success", event="schedule", branch="main", rid=1, modules=(), flake=False):
    end = datetime.strptime(ended, "%Y-%m-%dT%H:%M:%SZ")
    e = {"schema": "planner.event/1", "type": "outcome", "ts": ended, "repo": "o/r", "decision_id": None, "pr": 0, "head_sha": "a" * 40,
         "suite": suite, "run_id": rid, "attempt": 1, "event": event, "branch": branch, "conclusion": conclusion,
         "started": iso(end - timedelta(minutes=minutes)), "completed": ended, "minutes": float(minutes), "url": "", "flake_hint": flake}
    e["id"] = event_id("out", {"run": rid, "attempt": 1})
    return e


def es_outcomes(events, default_branch="main"):
    by = {}
    for e in events:
        by.setdefault(e["suite"], []).append(e)
    buckets = []
    for suite, es in sorted(by.items()):
        full = [e for e in es if e["conclusion"] == "success" and e["minutes"] >= 10]
        last = [e for e in full if e["event"] in ("schedule", "workflow_dispatch", "push") and e["branch"] == default_branch]
        med = sorted(e["minutes"] for e in full)
        median = None
        if med:
            n = len(med)
            median = med[n // 2] if n % 2 else (med[n // 2 - 1] + med[n // 2]) / 2
        buckets.append({"key": suite, "doc_count": len(es),
                        "last_full": {"doc_count": len(last), "at": ({"value": 1.0, "value_as_string": max(e["completed"] for e in last)} if last else {"value": None})},
                        "cost": {"doc_count": len(full), "median": {"values": {"50.0": median}}}})
    return {"aggregations": {"by_suite": {"buckets": buckets}}}


def es_escapes(events):
    docs = []
    for e in events:
        for c in e["candidates"]:
            docs.append({"pair": "%s|%s" % (c["module"], e["suite"]), "share": c["share"], "ts": e["ts"], "escape_id": e["id"]})
    by = {}
    for d in docs:
        by.setdefault(d["pair"], []).append(d)
    buckets = [{"key": p, "mass": {"value": sum(x["share"] for x in ds)}, "last": {"value_as_string": max(x["ts"] for x in ds)},
                "ids": {"buckets": [{"key": k} for k in sorted({x["escape_id"] for x in ds})]}} for p, ds in sorted(by.items())]
    return {"aggregations": {"pairs": {"buckets": buckets}}}


def node_eval(dmn, inputs, want):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "in.json")
    with open(p, "w") as fh:
        json.dump(inputs, fh)
    r = subprocess.run([NODE, os.path.join(HERE, "eval_dmn.js"), os.path.join(HERE, dmn), p, want], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout)


@unittest.skipUnless(READY, "needs node and `npm ci` in planner/camunda")
class MemoryDmnTest(unittest.TestCase):
    def test_memory_from_elasticsearch_matches_python(self):
        outs = [outcome("a_tests.yml", "2026-10-28T05:00:00Z", 120, rid=1), outcome("a_tests.yml", "2026-10-31T05:00:00Z", 100, rid=2),
                outcome("a_tests.yml", "2026-11-01T05:00:00Z", 3, rid=3),  # label-skipped: not a full run
                outcome("b_tests.yml", "2026-10-20T05:00:00Z", 200, rid=4, event="pull_request", branch="feat"),
                outcome("c_tests.yml", "2026-10-30T05:00:00Z", 90, rid=5, conclusion="failure")]
        esc = []
        for i, (mod, ts) in enumerate([("aws/modules/aurora", "2026-10-25T00:00:00Z"), ("aws/modules/aurora", "2026-10-29T00:00:00Z"),
                                       ("aws/modules/vpn", "2026-07-01T00:00:00Z")]):
            esc.append({"schema": "planner.event/1", "type": "escape", "id": "esc_%012x" % (i + 1), "ts": ts, "repo": "o/r", "suite": "a_tests.yml",
                        "candidates": [{"decision_id": "dec_0123456789ab", "pr": 5, "module": mod, "share": 1.0}], "confidence": 1.0, "confirmed_by": "x",
                        "failing_run": {"id": 1, "sha": "a" * 40, "at": ts}, "good_run": {"id": 1, "sha": "a" * 40, "at": ts}})
        weights = {"schema": "planner.event/1", "type": "weights", "id": "wgt_0123456789ab", "ts": "2026-10-30T00:00:00Z", "repo": "o/r", "window_days": 180,
                   "pairs": {"aws/modules/aurora|a_tests.yml": {"risk": 0.2, "weight": 1.7, "n_run": 3, "fail": 1, "escape": 1, "evidence": []}},
                   "suites": {}, "prev": None, "diff": "", "params": {}}
        py_rich = memory.build(outs + esc + [weights], CFG, NOW)
        facts = {"signals": [{"type": "module", "module": m} for m in ("aws/modules/aurora", "aws/modules/vpn")],
                 "suites": {n: {} for n in ("a_tests.yml", "b_tests.yml", "c_tests.yml")}}
        py = memory.compact(py_rich, facts)
        weights_response = {"hits": {"hits": [{"_source": {"weights_id": weights["id"], "pairs": [{"pair": k, "weight": v["weight"]} for k, v in weights["pairs"].items()]}}]}}
        inp = {"outcomesResponse": es_outcomes(outs), "escapesResponse": es_escapes([e for e in esc if (datetime.strptime(NOW, "%Y-%m-%dT%H:%M:%SZ") - datetime.strptime(e["ts"], "%Y-%m-%dT%H:%M:%SZ")).days <= 90]),
               "weightsResponse": weights_response, "nowIso": NOW,
               "params": {"breaker_escapes": CFG["learn"]["breaker_escapes"], "breaker_clean_days": CFG["learn"]["breaker_clean_days"]}}
        res = node_eval("ci-test-memory.dmn", inp, "memory")
        self.assertEqual(res["warnings"], [])
        dm = res["memory"]
        self.assertEqual(dm["weights_version"], py["weights_version"])
        self.assertEqual(dm["weights"], py["weights"])
        for n in ("a_tests.yml", "b_tests.yml", "c_tests.yml"):
            want, got = py["suites"][n], dm["suites"].get(n, {})
            if want["age_all"] is None:
                self.assertIsNone(got.get("age_all"), n)
            else:
                self.assertAlmostEqual(got["age_all"], want["age_all"], places=2, msg=n)
            self.assertEqual(got.get("last_full_success_at"), want["last_full_success_at"] if want["age_all"] is not None else None, n)
            if want["cost_min"] is not None:
                self.assertAlmostEqual(got["cost_min"], want["cost_min"], places=1)
        # breaker: aurora has two escapes (mass 2) and the last one is within 30 days; vpn is old
        self.assertEqual(sorted(dm["breaker"]), sorted(py["breaker"]))
        self.assertEqual(sorted(dm["breaker"]), ["aws/modules/aurora|a_tests.yml"])
        self.assertEqual(dm["breaker"]["aws/modules/aurora|a_tests.yml"]["evidence"], ["esc_000000000001", "esc_000000000002"])

    def test_empty_elasticsearch_gives_empty_memory(self):
        inp = {"outcomesResponse": None, "escapesResponse": None, "weightsResponse": None, "nowIso": NOW,
               "params": {"breaker_escapes": 2, "breaker_clean_days": 30}}
        res = node_eval("ci-test-memory.dmn", inp, "memory")
        self.assertEqual(res["memory"], {"now": NOW, "weights_version": "none", "suites": {}, "weights": {}, "breaker": {}})


@unittest.skipUnless(READY, "needs node and `npm ci` in planner/camunda")
class LearnDmnTest(unittest.TestCase):
    def test_weights_match_the_python_learner(self):
        cfg = copy.deepcopy(CFG)
        cfg["learn"]["half_life_days"] = 1e9  # the DMN has no time decay: compare the formula on equal counts
        dec = {"id": "dec_0123456789ab", "type": "decision", "ts": "2026-10-25T00:00:00Z", "pr": 5, "repo": "o/r",
               "impacted": {"modules": ["aws/modules/aurora", "aws/modules/vpn"], "consumers": {}}}
        ev = [dec]
        for i in range(12):
            o = outcome("x_tests.yml", "2026-10-%02dT05:00:00Z" % (10 + i), conclusion="failure" if i in (3, 7) else "success", rid=100 + i, event="pull_request", branch="f")
            o["decision_id"] = dec["id"]
            ev.append(o)
        ev.append({"schema": "planner.event/1", "type": "escape", "id": "esc_000000000001", "ts": "2026-10-28T00:00:00Z", "repo": "o/r", "suite": "x_tests.yml",
                   "candidates": [{"decision_id": dec["id"], "pr": 5, "module": "aws/modules/aurora", "share": 1.0}], "confidence": 1.0, "confirmed_by": "x",
                   "failing_run": {"id": 1, "sha": "a" * 40, "at": "2026-10-28T00:00:00Z"}, "good_run": {"id": 1, "sha": "a" * 40, "at": "2026-10-28T00:00:00Z"}})
        prev = {"aws/modules/aurora|x_tests.yml": 1.2}
        prev_event = {"id": "wgt_0123456789ab", "pairs": {k: {"weight": v} for k, v in prev.items()}}
        py = learn.compute(ev, cfg, NOW, prev_event, "o/r")["pairs"]
        stats = [{"module": k.split("|")[0], "suite": k.split("|")[1], "n_run": round(v["n_run"]), "fail": round(v["fail"]), "escape": round(v["escape"])} for k, v in py.items()]
        lc = CFG["learn"]
        params = {k: lc[k] for k in ("prior_a", "prior_b", "escape_factor", "weight_slope", "weight_min", "weight_max", "max_step")}
        res = node_eval("ci-test-learn.dmn", {"stats": stats, "prev": prev, "params": params}, "learned")
        self.assertEqual(res["warnings"], [])
        got = {r["pair"]: r for r in res["learned"]}
        self.assertEqual(sorted(got), sorted(py))
        for k, v in py.items():
            self.assertAlmostEqual(got[k]["weight"], v["weight"], places=2, msg=k)
            self.assertAlmostEqual(got[k]["risk"], v["risk"], places=3, msg=k)
        self.assertGreater(got["aws/modules/aurora|x_tests.yml"]["weight"], 1.2)
        self.assertLessEqual(got["aws/modules/aurora|x_tests.yml"]["weight"], 1.2 + CFG["learn"]["max_step"] + 1e-9)


if __name__ == "__main__":
    unittest.main()
