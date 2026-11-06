"""`planner learn`: weekly weights from the event log. Every number carries its evidence ids."""

from __future__ import annotations

import math
import statistics

from . import SCHEMA
from .util import days_between, event_id


def _decay(age_days: float, half_life: float) -> float:
    return 0.5 ** (max(0.0, age_days) / half_life)


def compute(events: list, cfg: dict, now: str, prev: dict | None, repo: str) -> dict:
    lc = cfg["learn"]
    window = lc["window_days"]
    decisions = {e["id"]: e for e in events if e["type"] == "decision"}
    pairs: dict = {}
    suites: dict = {}

    def pair(m, s):
        return pairs.setdefault("%s|%s" % (m, s), {"n_run": 0.0, "fail": 0.0, "escape": 0.0, "evidence": []})

    def suite(s):
        return suites.setdefault(s, {"runs": 0.0, "flakes": 0.0, "minutes": [], "evidence": []})

    for e in events:
        if e["type"] == "outcome":
            age = days_between(now, e["completed"])
            if age > window:
                continue
            d = _decay(age, lc["half_life_days"])
            s = suite(e["suite"])
            s["runs"] += d
            s["evidence"].append(e["id"])
            if e.get("flake_hint"):
                s["flakes"] += d
            if e["conclusion"] == "success" and e["minutes"] >= cfg["memory"]["min_full_minutes"]:
                s["minutes"].append(e["minutes"])
            dec = decisions.get(e.get("decision_id"))
            if dec:
                for m in dec["impacted"]["modules"]:
                    p = pair(m, e["suite"])
                    p["n_run"] += d
                    if e["conclusion"] == "failure" and not e.get("flake_hint"):
                        p["fail"] += d
                    p["evidence"].append(e["id"])
        elif e["type"] == "escape":
            age = days_between(now, e["ts"])
            if age > window:
                continue
            d = _decay(age, lc["half_life_days"])
            for c in e["candidates"]:
                p = pair(c["module"], e["suite"])
                p["escape"] += c["share"] * d
                p["evidence"].append(e["id"])
    prev_pairs = (prev or {}).get("pairs", {})
    out_pairs = {}
    for k, p in sorted(pairs.items()):
        risk = (p["fail"] + lc["escape_factor"] * p["escape"] + lc["prior_a"]) / (p["n_run"] + p["escape"] + lc["prior_a"] + lc["prior_b"])
        target = min(lc["weight_max"], max(lc["weight_min"], 1.0 + lc["weight_slope"] * risk))
        old = float(prev_pairs.get(k, {}).get("weight", 1.0))
        step = max(-lc["max_step"], min(lc["max_step"], target - old))
        out_pairs[k] = {"risk": round(risk, 4), "weight": round(old + step, 3), "n_run": round(p["n_run"], 2),
                        "fail": round(p["fail"], 2), "escape": round(p["escape"], 2), "evidence": p["evidence"][-20:]}
    out_suites = {}
    for k, s in sorted(suites.items()):
        out_suites[k] = {"flake": round(s["flakes"] / s["runs"], 4) if s["runs"] else None,
                         "cost_min": round(statistics.median(s["minutes"]), 1) if s["minutes"] else None,
                         "runs": round(s["runs"], 2), "evidence": s["evidence"][-20:]}
    lines = ["| Pair (module|suite) | Weight | Risk | Runs | Fails | Escapes |", "|---|---|---|---|---|---|"]
    changed = 0
    for k, v in out_pairs.items():
        old = float(prev_pairs.get(k, {}).get("weight", 1.0))
        if abs(v["weight"] - old) > 1e-9 or k not in prev_pairs:
            changed += 1
            lines.append("| `%s` | %.2f -> %.2f | %.3f | %.1f | %.1f | %.1f |" % (k, old, v["weight"], v["risk"], v["n_run"], v["fail"], v["escape"]))
    diff = "%d pair weights changed (of %d).\n\n%s" % (changed, len(out_pairs), "\n".join(lines) if changed else "No change.")
    body = {"schema": SCHEMA, "type": "weights", "ts": now, "repo": repo, "window_days": window, "pairs": out_pairs,
            "suites": out_suites, "prev": (prev or {}).get("id"), "diff": diff[:19000], "params": dict(lc)}
    body["id"] = event_id("wgt", {k: v for k, v in body.items() if k != "id"})
    return body
