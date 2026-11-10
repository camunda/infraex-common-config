#!/usr/bin/env python3
"""Generate camunda/ci-test-planner.dmn (DMN 1.3, executable on a Camunda 8.10 cluster).

The decision policy R1-R12 of the planner as DMN: a decision table per signal (a business knowledge
model) and FEEL literal expressions for aggregation, rotation, overrides and the decision list.
Reference implementation in Python: .github/workflows/scripts/planner/policy.py (a conformance test
runs both on the same inputs).

usage: build_dmn.py <out.dmn>
"""
import sys
from xml.sax.saxutils import escape

NS = ('xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" xmlns:dmndi="https://www.omg.org/spec/DMN/20191111/DMNDI/" '
      'xmlns:dc="http://www.omg.org/spec/DMN/20180521/DC/" xmlns:modeler="http://camunda.org/schema/modeler/1.0" '
      'xmlns:camunda="http://camunda.org/schema/1.0/dmn"')

# --------------------------------------------------------------------------------------------
# Business knowledge models (functions)

# signalAction(signal): one decision table row per rule, COLLECT. Outputs:
#   run/runReason, rotate/rotModules/rotReason, fireRule/fireSubject/fireReason/fireEvidence
TABLE_INPUTS = [
    ("signal type", 'signal.type'),
    ("significant", 'get value(signal, "significant")'),
    ("interface files changed", 'get value(signal, "iface_files_changed")'),
    ("has owner suite", 'count(get value(signal, "owner_suites")) > 0'),
    ("pin only", 'get value(signal, "pin_only")'),
]
TABLE_OUTPUTS = ["run", "runReason", "rotate", "rotModules", "rotReason", "fireRule", "fireSubject", "fireReason", "fireEvidence"]
NULL = "null"
E = "[]"
ROWS = [
    # type, significant, iface changed, has owner, pin only | outputs
    ('R2 owner suite', ['"module"', "", "", "", ""],
     ["signal.owner_suites", '"R2 owner suite builds module " + signal.module', E, E, NULL, NULL, NULL, NULL, E]),
    ('R2 consumer golden plans', ['"module"', "", "true", "", ""],
     ["signal.golden_suites", '"R2 interface files of " + signal.module + " changed: consumer golden plan (offline)"', E, E, NULL, NULL, NULL, NULL, E]),
    ('R9 significant interface change', ['"module"', "true", "", "", ""],
     [E, NULL, "signal.consumer_suites", "[signal.module]", '"R9 significant interface change in " + signal.module',
      '"R9"', "signal.module", '"removed or renamed output, removed variable, new required variable or changed type"', "signal.iface_evidence"]),
    ('R3 module without an owner suite', ['"module"', "false", "", "false", ""],
     [E, NULL, "signal.consumer_suites", "[signal.module]", '"R3 " + signal.module + " has no module-level suite"',
      '"R3"', "signal.module", '"no module-level suite: one consumer is rotated in"', E]),
    ('R2 module change, owner suite exists', ['"module"', "false", "", "true", ""],
     [E, NULL, E, E, NULL, '"R2"', "signal.module",
      '"module change, interface not significant; owner suite " + string join(signal.owner_suites, ", ")', E]),
    ('R8 engine major', ['"engine_major"', "", "", "", ""],
     ["signal.owner_suites",
      '"R8 engine major " + signal.dep + " " + get value(signal, "from") + "->" + get value(signal, "to") + ": module owner suite"',
      "signal.candidate_suites", "signal.modules",
      '"R8 engine major " + signal.dep + " " + get value(signal, "from") + "->" + get value(signal, "to")',
      '"R8"', 'signal.dep + " " + get value(signal, "from") + " -> " + get value(signal, "to")',
      '"annotated version pin changed its major in " + string(signal.files_n) + " file(s)"', "signal.files"]),
    ('R10 pin-only change in an RA root', ['"ra_root"', "", "", "", "true"],
     [E, NULL, E, E, NULL, '"R10"', "signal.ra",
      '"only version pins, goldens or docs changed (" + string(signal.files_n) + " files): no own from-scratch run"', E]),
    ('R4 RA root changed', ['"ra_root"', "", "", "", "false"],
     ["signal.suites", '"R4 RA root " + signal.ra + " changed beyond version pins"', E, E, NULL,
      '"R4"', "signal.ra", '"RA root changed: its suites run"', E]),
    ('R5 shared action', ['"shared_action"', "", "", "", ""],
     [E, NULL, "signal.suites", E, '"R5 shared action " + signal.name', '"R5"', "signal.name",
      '"shared action change: one suite that uses it is rotated in (" + string(count(signal.suites)) + " use it)"', E]),
    ('R5 tool versions', ['"tool_versions"', "", "", "", ""],
     [E, NULL, "signal.suites", E, '"R5 .tool-versions"', '"R5"', '".tool-versions"', '"toolchain pin change: one suite is rotated in"', E]),
    ('R5 the changed workflow itself', ['"workflow_changed"', "", "", "", ""],
     ["[signal.suite]", '"R5 the changed workflow itself"', E, E, NULL, NULL, NULL, NULL, E]),
]

BKMS = {
    "suiteAge": (["memory", "suite", "module"], """{s: get value(memory.suites, suite),
 a: if s = null then null else get value(s, "age_all"),
 b: if s = null or module = null then null else get value(get value(s, "age_modules"), module),
 r: if a = null then b else if b = null then a else min([a, b])}.r"""),
    "pairWeight": (["memory", "module", "suite"], """if module = null then 1 else {w: get value(memory.weights, module + "|" + suite),
 r: if w = null then 1 else w}.r"""),
    "scoreEntries": (["memory", "mods", "c"], """for m in mods return {a: suiteAge(memory, c, m),
 eff: if a = null then 100000 else a,
 w: pairWeight(memory, m, c),
 score: eff * w}"""),
}

# --------------------------------------------------------------------------------------------
# Decisions: name -> (requires, expression)
DECISIONS = [
    ("rows", ["facts"], ["signalAction"], "flatten(for s in facts.signals return signalAction(s))"),
    ("runAdds", ["rows"], [], "flatten(for r in rows return for n in r.run return {suite: n, reason: r.runReason})"),
    ("rotCandidates", ["rows"], [], "distinct values(flatten(for r in rows return r.rotate))"),
    ("rotModules", ["rows"], [], "sort(distinct values(flatten(for r in rows return r.rotModules)), function(a, b) a < b)"),
    ("rotReasons", ["rows"], [], "distinct values(for r in rows[item.rotReason != null] return r.rotReason)"),
    ("rotCands", ["facts", "rotCandidates"], [], """{names: rotCandidates[get value(facts.suites, item) != null],
 prim: names[get value(facts.suites, item).primary = true],
 r: if count(prim) > 0 then prim else names}.r"""),
    ("rotationRows", ["memory", "rotCands", "rotModules"], ["suiteAge", "pairWeight", "scoreEntries"],
     """for c in rotCands return {suite: c,
 scored: scoreEntries(memory, if count(rotModules) = 0 then [null] else rotModules, c),
 best: max(for e in scored return e.score),
 score: best,
 chosen: scored[item.score = best][-1],
 cost: {s: get value(memory.suites, c), r: if s = null then null else get value(s, "cost_min")}.r,
 costKey: if cost = null then 1000000000 else cost}"""),
    ("rotSorted", ["rotationRows"], [],
     "sort(rotationRows, function(a, b) a.score > b.score or (a.score = b.score and (a.costKey < b.costKey or (a.costKey = b.costKey and a.suite < b.suite))))"),
    ("rotPick", ["facts", "rotSorted"], [],
     """if count(rotSorted) = 0 then null else {top: rotSorted[1],
 near: rotSorted[item.score >= top.score * (1 - facts.tie_percent / 100)],
 ordered: sort(near, function(a, b) a.costKey < b.costKey or (a.costKey = b.costKey and (a.score > b.score or (a.score = b.score and a.suite < b.suite)))),
 r: if top.score > 0 then ordered[1] else top}.r"""),
    ("rotation", ["rotPick", "rotSorted", "rotReasons"], [],
     """if rotPick = null then null else {reasons: rotReasons,
 candidates: for r in rotSorted return {suite: r.suite, age_days: r.chosen.a, weight: r.chosen.w, score: r.score, cost_min: r.cost},
 picked: rotPick.suite}"""),
    ("mandatory", ["facts", "memory"], [],
     """flatten(for sig in facts.signals[item.type = "module"] return for n in sig.consumer_suites return
 if get value(memory.breaker, sig.module + "|" + n) = null then [] else [{suite: n, reason: "R7b circuit breaker on " + sig.module,
 pair: sig.module + "|" + n, evidence: get value(get value(memory.breaker, sig.module + "|" + n), "evidence")}])"""),
    ("triggered", ["facts"], [],
     "sort(for e in get entries(facts.suites)[item.value.path_triggered = true] return e.key, function(a, b) a < b)"),
    ("triggeredExpensive", ["facts", "triggered"], [],
     "triggered[list contains(facts.expensive_layers, get value(facts.suites, item).layer) = true]"),
    ("cheap", ["facts", "triggered"], [],
     """for s in triggered[get value(facts.suites, item).layer = "L0" or get value(facts.suites, item).layer = "L1" or get value(facts.suites, item).layer = "L2"]
 return {suite: s, reason: "R1 cheap layer"}"""),
    ("judgeEntries", ["facts", "triggeredExpensive"], [],
     """if facts.judge_changed then for s in triggeredExpensive return {suite: s, reason: "R11 the planner configuration or caller changed: nothing is narrowed"} else []"""),
    ("runAllEntries", ["facts", "triggeredExpensive"], [],
     """if facts.labels.run_all then for s in triggeredExpensive return {suite: s, reason: "R11 label planner:run-all: path-triggered set restored"} else []"""),
    ("runLabelEntries", ["facts"], [],
     """for n in facts.labels.run return {suite: n, reason: "R11 label run_" + substring(n, 1, string length(n) - 4)}"""),
    ("runEntries", ["cheap", "runAdds", "rotation", "rotPick", "rotReasons", "mandatory", "judgeEntries", "runAllEntries", "runLabelEntries"], [],
     """concatenate(cheap, concatenate(runAdds, concatenate(
 if rotPick = null then [] else [{suite: rotPick.suite, reason: "R7 rotation (" + string join(rotReasons, "; ") + "): highest staleness x weight"}],
 concatenate(for m in mandatory return {suite: m.suite, reason: m.reason},
 concatenate(judgeEntries, concatenate(runAllEntries, runLabelEntries))))))"""),
    ("runSet", ["facts", "runEntries"], [],
     """{names: distinct values(for e in runEntries return e.suite),
 r: names[list contains(facts.labels.skip, item) = false and (facts.labels.skip_all = false or get value(facts.suites, item).layer = "L0")]}.r"""),
    ("modeInfo", ["facts"], [],
     """if facts.head_repo_is_fork then {mode: "report", reason: "fork pull request"}
 else if not(list contains(facts.gate_base_branches, facts.base_branch)) then {mode: "report", reason: "base branch " + facts.base_branch + " is not gated"}
 else {mode: if facts.requested_mode = "gate" then "gate" else "report", reason: null}"""),
    ("decisionList", ["facts", "triggered", "runEntries", "runSet", "modeInfo"], [],
     """concatenate(
 for s in triggered[get value(facts.suites, item).layer != "ops"] return {suite: s,
   action: if list contains(runSet, s) then "run" else "skip",
   enforced: modeInfo.mode = "gate" and list contains(facts.gate_suites, s) and list contains(facts.expensive_layers, get value(facts.suites, s).layer),
   layer: get value(facts.suites, s).layer,
   reasons: if list contains(runSet, s) then distinct values(for e in runEntries[item.suite = s] return e.reason) else ["path filter matched, no rule needs this suite"]},
 concatenate(
 for s in runSet[get value(facts.suites, item).path_triggered = false] return {suite: s, action: "run", enforced: false,
   layer: get value(facts.suites, s).layer,
   reasons: concatenate(distinct values(for e in runEntries[item.suite = s] return e.reason), ["not path-triggered: this suite will only start if its paths: filter is widened"])},
 for n in facts.new_workflows return {suite: n, action: "run", enforced: false, layer: "unknown", reasons: ["new workflow, not in the base registry"]}))"""),
    ("rulesFired", ["facts", "rows", "mandatory", "modeInfo"], [],
     """concatenate(
 if facts.docs_only then [{rule: "R6", subject: "docs only", reason: "only documentation files changed: cheap layers only", evidence: []}] else [],
 concatenate(for r in rows[item.fireRule != null] return {rule: r.fireRule, subject: r.fireSubject, reason: r.fireReason, evidence: r.fireEvidence},
 concatenate(for n in facts.new_workflows return {rule: "R5", subject: n, reason: "workflow not in the base registry: not narrowed", evidence: []},
 concatenate(for m in mandatory return {rule: "R7b", subject: m.pair, reason: "confirmed escapes within the window: suite is mandatory for this module", evidence: m.evidence},
 concatenate(if facts.judge_changed then [{rule: "R11", subject: "judge", reason: "the planner config or its caller changed in this PR: the path-triggered set runs", evidence: []}] else [],
 if modeInfo.reason = null then [] else [{rule: "R12", subject: "mode", reason: "gating is off for this PR: " + modeInfo.reason + " (decision recorded as report)", evidence: []}])))))"""),
    ("overrides", ["facts"], [],
     """{labels: facts.labels.seen,
 applied: concatenate(if facts.labels.run_all then ["planner:run-all"] else [],
   concatenate(for n in facts.labels.run return "run_" + substring(n, 1, string length(n) - 4),
   concatenate(for n in facts.labels.skip return "skip_" + substring(n, 1, string length(n) - 4),
   if facts.labels.skip_all then ["skip_all"] else []))),
 conflicts: for n in facts.labels.skip[list contains(facts.labels.run, item) = true] return "skip_" + substring(n, 1, string length(n) - 4) + " with run_" + substring(n, 1, string length(n) - 4) + ": skip wins"}"""),
    ("safetyNet", ["facts", "memory", "decisionList"], [],
     """for d in decisionList[item.action = "skip" and list contains(facts.expensive_layers, item.layer) = true] return {suite: d.suite,
   next_scheduled: get value(facts.suites, d.suite).next_scheduled,
   last_full_success_at: {s: get value(memory.suites, d.suite), r: if s = null then null else get value(s, "last_full_success_at")}.r}"""),
    ("plan", ["facts", "memory", "modeInfo", "decisionList", "rotation", "rulesFired", "overrides", "safetyNet"], [],
     """{mode: modeInfo.mode, decision: decisionList, rotation: rotation, rulesFired: rulesFired, overrides: overrides, safetyNet: safetyNet,
 weightsVersion: memory.weights_version, rulesVersion: "1.0.0"}"""),
]


def x(s):
    return escape(s, {'"': "&quot;"})


def _table_bkm(out):
    out.append('  <businessKnowledgeModel id="bkm_signalAction" name="signalAction">')
    out.append('    <variable id="bkm_signalAction_v" name="signalAction" />')
    out.append('    <encapsulatedLogic id="bkm_signalAction_f">')
    out.append('      <formalParameter id="bkm_signalAction_p" name="signal" typeRef="Any" />')
    out.append('      <decisionTable id="dt_signalAction" hitPolicy="COLLECT">')
    for i, (label, expr) in enumerate(TABLE_INPUTS):
        out.append('        <input id="dt_in_%d" label="%s"><inputExpression id="dt_in_%d_e" typeRef="Any"><text>%s</text></inputExpression></input>' % (i, x(label), i, x(expr)))
    for i, name in enumerate(TABLE_OUTPUTS):
        out.append('        <output id="dt_out_%d" name="%s" typeRef="Any" />' % (i, name))
    for r, (label, ins, outs) in enumerate(ROWS):
        out.append('        <rule id="dt_rule_%d">' % r)
        out.append('          <description>%s</description>' % x(label))
        for i, t in enumerate(ins):
            out.append('          <inputEntry id="dt_r%d_i%d"><text>%s</text></inputEntry>' % (r, i, x(t)))
        for i, t in enumerate(outs):
            out.append('          <outputEntry id="dt_r%d_o%d"><text>%s</text></outputEntry>' % (r, i, x(t)))
        out.append('        </rule>')
    out.append('      </decisionTable>')
    out.append('    </encapsulatedLogic>')
    out.append('  </businessKnowledgeModel>')


def build(defs_id="ci_test_planner", defs_name="CI test planner", inputs=("facts", "memory"), table=True, bkms=None, decisions=None) -> str:
    bkms = BKMS if bkms is None else bkms
    decisions = DECISIONS if decisions is None else decisions
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<definitions %s id="%s" name="%s" namespace="http://camunda.org/schema/1.0/dmn" '
           'exporter="build_dmn.py" exporterVersion="1">' % (NS, defs_id, defs_name)]
    for name in inputs:
        out.append('  <inputData id="in_%s" name="%s"><variable id="in_%s_v" name="%s" typeRef="Any" /></inputData>' % (name, name, name, name))
    if table:
        _table_bkm(out)
    for name, (params, body) in bkms.items():
        out.append('  <businessKnowledgeModel id="bkm_%s" name="%s">' % (name, name))
        out.append('    <variable id="bkm_%s_v" name="%s" />' % (name, name))
        for d in [d for d in bkms if d != name and d + "(" in body]:
            out.append('    <knowledgeRequirement id="bkm_%s_k_%s"><requiredKnowledge href="#bkm_%s" /></knowledgeRequirement>' % (name, d, d))
        out.append('    <encapsulatedLogic id="bkm_%s_f">' % name)
        for p in params:
            out.append('      <formalParameter id="bkm_%s_p_%s" name="%s" typeRef="Any" />' % (name, p, p))
        out.append('      <literalExpression id="bkm_%s_e"><text>%s</text></literalExpression>' % (name, x(body)))
        out.append('    </encapsulatedLogic>')
        out.append('  </businessKnowledgeModel>')
    names = {d[0] for d in decisions}
    all_bkm = set(bkms) | ({"signalAction"} if table else set())
    for name, reqs, kreqs, body in decisions:
        out.append('  <decision id="%s" name="%s">' % (name, name))
        out.append('    <variable id="%s_v" name="%s" typeRef="Any" />' % (name, name))
        for r in reqs:
            if r in names:
                out.append('    <informationRequirement id="%s_r_%s"><requiredDecision href="#%s" /></informationRequirement>' % (name, r, r))
            else:
                out.append('    <informationRequirement id="%s_r_%s"><requiredInput href="#in_%s" /></informationRequirement>' % (name, r, r))
        kn = set(kreqs) | {k for k in all_bkm if k + "(" in body}
        for k in sorted(kn):
            out.append('    <knowledgeRequirement id="%s_k_%s"><requiredKnowledge href="#bkm_%s" /></knowledgeRequirement>' % (name, k, k))
        out.append('    <literalExpression id="%s_e" typeRef="Any"><text>%s</text></literalExpression>' % (name, x(body)))
        out.append('  </decision>')
    out.append('</definitions>')
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------------------------
# Memory: Elasticsearch responses -> the compact memory the planner reads
MEMORY_BKMS = {
    "ageDays": (["nowIso", "ts"], """{d: date and time(nowIso) - date and time(ts),
 r: d.days + d.hours / 24 + d.minutes / 1440 + d.seconds / 86400}.r"""),
}
MEMORY_DECISIONS = [
    ("suiteBuckets", ["outcomesResponse"], [],
     """if outcomesResponse = null or get value(outcomesResponse, "aggregations") = null then [] else outcomesResponse.aggregations.by_suite.buckets"""),
    ("suites", ["suiteBuckets", "nowIso"], ["ageDays"],
     """context merge(for b in suiteBuckets return context put({}, b.key, {
 age_all: if get value(b.last_full.at, "value_as_string") = null then null else ageDays(nowIso, b.last_full.at.value_as_string),
 age_modules: {},
 cost_min: if b.cost.doc_count = 0 then null else get value(b.cost.median.values, "50.0"),
 last_full_success_at: get value(b.last_full.at, "value_as_string"),
 source: "elasticsearch"}))"""),
    ("weightsHit", ["weightsResponse"], [],
     """if weightsResponse = null or get value(weightsResponse, "hits") = null or count(weightsResponse.hits.hits) = 0 then null else weightsResponse.hits.hits[1]._source"""),
    ("weights", ["weightsHit"], [],
     """if weightsHit = null then {} else context merge(for p in weightsHit.pairs return context put({}, p.pair, p.weight))"""),
    ("breaker", ["escapesResponse", "nowIso", "params"], ["ageDays"],
     """if escapesResponse = null or get value(escapesResponse, "aggregations") = null then {} else
 context merge(for b in escapesResponse.aggregations.pairs.buckets[item.mass.value >= params.breaker_escapes and ageDays(nowIso, item.last.value_as_string) <= params.breaker_clean_days] return
 context put({}, b.key, {mass: b.mass.value, evidence: for i in b.ids.buckets return i.key}))"""),
    ("memory", ["suites", "weights", "weightsHit", "breaker", "nowIso"], [],
     """{now: nowIso, weights_version: if weightsHit = null then "none" else weightsHit.weights_id, suites: suites, weights: weights, breaker: breaker}"""),
]

# --------------------------------------------------------------------------------------------
# Learner: per-pair statistics -> weights (bounded step)
LEARN_DECISIONS = [
    ("learned", ["stats", "prev", "params"], [],
     """for s in stats return {pair: s.module + "|" + s.suite, module: s.module, suite: s.suite,
 risk: (s.fail + params.escape_factor * s.escape + params.prior_a) / (s.n_run + s.escape + params.prior_a + params.prior_b),
 target: min([params.weight_max, max([params.weight_min, 1 + params.weight_slope * risk])]),
 old: {v: get value(prev, pair), r: if v = null then 1 else v}.r,
 step: max([0 - params.max_step, min([params.max_step, target - old])]),
 weight: old + step,
 n_run: s.n_run, fail: s.fail, escape: s.escape}"""),
]


if __name__ == "__main__":
    import os
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, "ci-test-planner.dmn"), "w").write(build())
    open(os.path.join(out, "ci-test-memory.dmn"), "w").write(build(
        "ci_test_memory", "CI test planner memory", ("outcomesResponse", "escapesResponse", "weightsResponse", "nowIso", "params"),
        table=False, bkms=MEMORY_BKMS, decisions=MEMORY_DECISIONS))
    open(os.path.join(out, "ci-test-learn.dmn"), "w").write(build(
        "ci_test_learn", "CI test planner learner", ("stats", "prev", "params"), table=False, bkms={}, decisions=LEARN_DECISIONS))
