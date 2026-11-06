"""Memory: what the planner knows about past runs, built from the store (and Actions history).

`build` is a pure function of its inputs, so every number in a decision can be re-derived from
the events listed in `evidence`.
"""

from __future__ import annotations

import statistics

from .util import days_between, parse_iso


def _full(rec: dict, min_minutes: float) -> bool:
    return rec.get("conclusion") == "success" and rec.get("minutes", 0) >= min_minutes


def _newer(a: dict | None, b: dict) -> dict:
    return b if a is None or b["at"] > a["at"] else a


def build(events: list, cfg: dict, now: str, runs: list | None = None, pin: str | None = None,
          default_branch: str = "main") -> dict:
    mcfg, lcfg = cfg["memory"], cfg["learn"]
    min_full = float(mcfg["min_full_minutes"])
    decisions = {e["id"]: e for e in events if e.get("type") == "decision"}
    suites: dict = {}

    def entry(name):
        return suites.setdefault(name, {"all": None, "modules": {}, "last_failure": None, "minutes": [], "runs": 0, "flakes": 0})

    for e in events:
        if e.get("type") != "outcome":
            continue
        s = entry(e["suite"])
        s["runs"] += 1
        if e.get("flake_hint"):
            s["flakes"] += 1
        rec = {"at": e["completed"], "run_id": e["run_id"], "sha": e["head_sha"], "event": e["event"],
               "minutes": e["minutes"], "evidence": e["id"], "source": "events"}
        if e["conclusion"] == "failure":
            s["last_failure"] = _newer(s["last_failure"], rec)
        if _full(e, min_full):
            s["minutes"].append(e["minutes"])
            if e["event"] != "pull_request" and e.get("branch") == default_branch:
                s["all"] = _newer(s["all"], rec)
            elif e["event"] == "pull_request" and e.get("decision_id") in decisions:
                for m in decisions[e["decision_id"]].get("impacted", {}).get("modules", []):
                    s["modules"][m] = _newer(s["modules"].get(m), rec)
    for r in runs or []:
        # Actions API fallback. Cost uses every successful full run; "covers the whole tree"
        # only holds for runs that are not pull requests, on the default branch.
        if r.get("status") != "completed":
            continue
        name = (r.get("path") or "").rsplit("/", 1)[-1]
        if not name:
            continue
        try:
            minutes = (parse_iso(r["updated_at"]) - parse_iso(r.get("run_started_at") or r["created_at"])).total_seconds() / 60
        except (KeyError, ValueError):
            continue
        if r.get("conclusion") == "success" and minutes >= min_full:
            entry(name)["minutes"].append(round(minutes, 1))
        if r.get("event") == "pull_request" or r.get("head_branch") != default_branch:
            continue
        if r.get("conclusion") == "success" and minutes >= min_full:
            s = entry(name)
            s["all"] = _newer(s["all"], {"at": r["updated_at"], "run_id": r["id"], "sha": r.get("head_sha", ""),
                                         "event": r.get("event", ""), "minutes": round(minutes, 1),
                                         "evidence": "run:%s" % r["id"], "source": "actions-api"})
    out_suites = {}
    for name, s in suites.items():
        out_suites[name] = {
            "all": s["all"], "modules": s["modules"], "last_failure": s["last_failure"],
            "median_minutes": round(statistics.median(s["minutes"]), 1) if s["minutes"] else None,
            "flake_rate": round(s["flakes"] / s["runs"], 3) if s["runs"] else None, "runs": s["runs"],
        }
    weights_events = [e for e in events if e.get("type") == "weights"]
    weights = None
    if pin:
        weights = next((w for w in weights_events if w["id"] == pin), None)
    elif weights_events:
        weights = max(weights_events, key=lambda w: (w["ts"], w["id"]))
    # circuit breaker: escape mass per pair inside the window, cleared after clean days
    mass: dict = {}
    for e in events:
        if e.get("type") != "escape":
            continue
        if days_between(now, e["ts"]) > lcfg["breaker_window_days"]:
            continue
        for c in e.get("candidates", []):
            k = "%s|%s" % (c["module"], e["suite"])
            m = mass.setdefault(k, {"mass": 0.0, "last": e["ts"], "evidence": []})
            m["mass"] += c["share"]
            m["last"] = max(m["last"], e["ts"])
            m["evidence"].append(e["id"])
    breaker = {}
    for k, m in mass.items():
        if m["mass"] >= lcfg["breaker_escapes"] - 1e-9 and days_between(now, m["last"]) <= lcfg["breaker_clean_days"]:
            breaker[k] = {"mass": round(m["mass"], 2), "last_escape": m["last"], "evidence": m["evidence"][-10:]}
    return {"schema": "planner.memory/1", "now": now, "suites": out_suites,
            "weights": weights, "breaker": breaker, "decisions": len(decisions)}


def age_days(mem: dict, suite: str, module: str | None):
    """Days since the last full success of `suite` that covers `module` (None = never seen)."""
    s = mem["suites"].get(suite)
    if not s:
        return None
    best = None
    for rec in [s.get("all")] + ([s["modules"].get(module)] if module else []):
        if rec and (best is None or rec["at"] > best["at"]):
            best = rec
    return None if best is None else max(0.0, days_between(mem["now"], best["at"]))


def last_success(mem: dict, suite: str, module: str | None):
    s = mem["suites"].get(suite)
    if not s:
        return None
    best = None
    for rec in [s.get("all")] + ([s["modules"].get(module)] if module else []):
        if rec and (best is None or rec["at"] > best["at"]):
            best = rec
    return best


def weight(mem: dict, module: str | None, suite: str) -> float:
    w = mem.get("weights")
    if not w or not module:
        return 1.0
    return float(w["pairs"].get("%s|%s" % (module, suite), {}).get("weight", 1.0))


def cost(mem: dict, suite: str):
    s = mem["suites"].get(suite) or {}
    c = s.get("median_minutes")
    if c is None and mem.get("weights"):
        c = (mem["weights"]["suites"].get(suite) or {}).get("cost_min")
    return c


def breaker_for(mem: dict, module: str, suite: str):
    return mem.get("breaker", {}).get("%s|%s" % (module, suite))
