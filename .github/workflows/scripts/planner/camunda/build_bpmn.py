#!/usr/bin/env python3
"""Generate the planner's BPMN processes (Camunda 8 / Zeebe, executable) with a diagram.

  ci-test-planner.bpmn   facts in -> memory (Elasticsearch) -> DMN rules -> optional AI advisor (add only)
                         -> decision record in Elasticsearch -> plan out
  ci-test-record.bpmn    a batch of outcome/escape documents -> Elasticsearch (idempotent ids)
  ci-test-learn.bpmn     weekly timer: statistics from Elasticsearch -> DMN weights -> weights document

usage: build_bpmn.py <out dir>

The Elasticsearch REST bodies come from queries.py, the same definitions the README documents.
Connector inputs follow the REST connector (io.camunda:http-json:1) as documented in the Camunda docs
(components/best-practices/development/testing-process-definitions.md shows the XML). The AI Agent task
inputs could not be checked against its element template (the template is not in the docs): see README.
"""
import os
import sys
from xml.sax.saxutils import escape

import queries as Q

NS = ('xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" '
      'xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" '
      'xmlns:zeebe="http://camunda.org/schema/zeebe/1.0" xmlns:modeler="http://camunda.org/schema/modeler/1.0" '
      'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"')


def q(s):
    return escape(s, {'"': "&quot;", "\n": "&#10;"})


class Proc:
    def __init__(self, pid, name, doc):
        self.pid, self.name, self.doc = pid, name, doc
        self.nodes = []  # dicts: id,kind,name,x,y,w,h,xml(inner),attrs,parent
        self.flows = []
        self.attached = []
        self.n = 0

    def add(self, nid, kind, name, x, y, inner="", attrs="", parent=None, w=None, h=None, doc=None):
        size = {"event": (36, 36), "gateway": (50, 50), "task": (130, 80), "sub": (w or 600, h or 220)}
        k = "event" if kind in ("startEvent", "endEvent", "boundaryEvent") else "gateway" if kind.endswith("Gateway") else "sub" if kind == "subProcess" else "task"
        dw, dh = size[k]
        self.nodes.append({"id": nid, "kind": kind, "name": name, "x": x, "y": y, "w": w or dw, "h": h or dh, "inner": inner,
                           "attrs": attrs, "parent": parent, "doc": doc})
        return nid

    def flow(self, a, b, name="", cond=None, default=False, via=None):
        fid = "f_%s_%s" % (a, b)
        self.flows.append({"id": fid, "a": a, "b": b, "name": name, "cond": cond, "default": default, "via": via or []})
        return fid

    def node(self, nid):
        return next(n for n in self.nodes if n["id"] == nid)

    def _abs(self, n):
        x, y = n["x"], n["y"]
        p = n["parent"]
        while p:
            pn = self.node(p)
            x, y = x + pn["x"], y + pn["y"]
            p = pn["parent"]
        return x, y

    def _center(self, n):
        x, y = self._abs(n)
        return x + n["w"] / 2, y + n["h"] / 2

    def _scope_xml(self, parent, indent):
        out = []
        ins = [n for n in self.nodes if n["parent"] == parent]
        pad = " " * indent
        for n in ins:
            if n["kind"] == "boundaryEvent":
                continue
            out.append(self._node_xml(n, pad))
        for n in ins:
            if n["kind"] == "boundaryEvent":
                out.append(self._node_xml(n, pad))
        for f in self.flows:
            if self.node(f["a"])["parent"] == parent:
                attrs = ' name="%s"' % q(f["name"]) if f["name"] else ""
                if f["cond"] or f["default"]:
                    pass
                body = ""
                if f["cond"]:
                    body = '<bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">%s</bpmn:conditionExpression>' % q(f["cond"])
                out.append('%s<bpmn:sequenceFlow id="%s" sourceRef="%s" targetRef="%s"%s>%s</bpmn:sequenceFlow>' % (pad, f["id"], f["a"], f["b"], attrs, body))
        return out

    def _node_xml(self, n, pad):
        tag = "bpmn:" + n["kind"]
        name = ' name="%s"' % q(n["name"]) if n["name"] else ""
        doc = '<bpmn:documentation>%s</bpmn:documentation>' % q(n["doc"]) if n["doc"] else ""
        default = ""
        for f in self.flows:
            if f["a"] == n["id"] and f["default"]:
                default = ' default="%s"' % f["id"]
        inc = "".join("<bpmn:incoming>%s</bpmn:incoming>" % f["id"] for f in self.flows if f["b"] == n["id"])
        outg = "".join("<bpmn:outgoing>%s</bpmn:outgoing>" % f["id"] for f in self.flows if f["a"] == n["id"])
        if n["kind"] == "subProcess":
            inner = "\n".join(self._scope_xml(n["id"], len(pad) + 2))
            return '%s<%s id="%s"%s%s>\n%s%s%s\n%s\n%s</%s>' % (pad, tag, n["id"], name, n["attrs"], pad + "  ", doc, "", inner, pad, tag) if False else \
                '%s<%s id="%s"%s%s>%s%s%s%s\n%s\n%s</%s>' % (pad, tag, n["id"], name, n["attrs"], doc, inc, outg, n["inner"], inner, pad, tag)
        return '%s<%s id="%s"%s%s%s>%s%s%s%s</%s>' % (pad, tag, n["id"], name, n["attrs"], default, doc, n["inner"], inc, outg, tag)

    def xml(self):
        out = ['<?xml version="1.0" encoding="UTF-8"?>',
               '<bpmn:definitions %s id="Definitions_%s" targetNamespace="http://bpmn.io/schema/bpmn" exporter="build_bpmn.py" exporterVersion="1" '
               'modeler:executionPlatform="Camunda Cloud" modeler:executionPlatformVersion="8.10.0">' % (NS, self.pid),
               '  <bpmn:process id="%s" name="%s" isExecutable="true">' % (self.pid, q(self.name))]
        if self.doc:
            out.append('    <bpmn:documentation>%s</bpmn:documentation>' % q(self.doc))
        out += self._scope_xml(None, 4)
        out.append('  </bpmn:process>')
        out.append('  <bpmndi:BPMNDiagram id="Diagram_%s">' % self.pid)
        out.append('    <bpmndi:BPMNPlane id="Plane_%s" bpmnElement="%s">' % (self.pid, self.pid))
        for n in self.nodes:
            x, y = self._abs(n)
            exp = ' isExpanded="true"' if n["kind"] == "subProcess" else ""
            out.append('      <bpmndi:BPMNShape id="%s_di" bpmnElement="%s"%s><dc:Bounds x="%d" y="%d" width="%d" height="%d" />' % (
                n["id"], n["id"], exp, x, y, n["w"], n["h"]))
            if n["name"] and n["kind"] in ("startEvent", "endEvent", "boundaryEvent") or n["kind"].endswith("Gateway") and n["name"]:
                out.append('        <bpmndi:BPMNLabel><dc:Bounds x="%d" y="%d" width="90" height="27" /></bpmndi:BPMNLabel>' % (x - 27, y + n["h"] + 4))
            out.append('      </bpmndi:BPMNShape>')
        for f in self.flows:
            a, b = self.node(f["a"]), self.node(f["b"])
            ax, ay = self._center(a)
            bx, by = self._center(b)
            sx = self._abs(a)[0] + a["w"]
            ex = self._abs(b)[0]
            pts = [(sx, ay)]
            if a["kind"] == "boundaryEvent":
                pts = [(ax, self._abs(a)[1] + a["h"])]
            for p in f["via"]:
                pts.append(p)
            pts.append((ex, by))
            if a["kind"] != "boundaryEvent" and not f["via"] and abs(ay - by) > 1:
                midx = (sx + ex) / 2
                pts = [(sx, ay), (midx, ay), (midx, by), (ex, by)]
            if a["kind"] == "boundaryEvent" and not f["via"]:
                pts = [(ax, self._abs(a)[1] + a["h"]), (ax, by), (ex, by)]
            out.append('      <bpmndi:BPMNEdge id="%s_di" bpmnElement="%s">' % (f["id"], f["id"]))
            for px, py in pts:
                out.append('        <di:waypoint x="%d" y="%d" />' % (px, py))
            out.append('      </bpmndi:BPMNEdge>')
        out.append('    </bpmndi:BPMNPlane>')
        out.append('  </bpmndi:BPMNDiagram>')
        out.append('</bpmn:definitions>')
        return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------------------------
# element helpers

def rest(method, url, body=None, result_var=None, result_expr="= response.body", error_expr=True, retries=3, timeout=15, name_hint=""):
    """REST connector extension elements."""
    inputs = [("noAuth", "authentication.type"), (method, "method"), (url, "url"),
              ("= " + Q.HEADERS, "headers")]
    if body is not None:
        inputs.append(("= " + body, "body"))
    inputs += [(str(timeout), "connectionTimeoutInSeconds"), (str(timeout), "readTimeoutInSeconds")]
    io = "".join('<zeebe:input source="%s" target="%s" />' % (q(s), t) for s, t in inputs)
    hdr = []
    if result_var:
        hdr.append(("resultVariable", result_var))
        hdr.append(("resultExpression", result_expr))
    if error_expr:
        hdr.append(("errorExpression", '= if response.status >= 400 then bpmnError("ES_" + string(response.status), "Elasticsearch answered " + string(response.status)) else null'))
    hdr.append(("retryBackoff", "PT2S"))
    th = "".join('<zeebe:header key="%s" value="%s" />' % (k, q(v)) for k, v in hdr)
    return ('<bpmn:extensionElements><zeebe:taskDefinition type="io.camunda:http-json:1" retries="%d" /><zeebe:ioMapping>%s</zeebe:ioMapping>'
            '<zeebe:taskHeaders>%s</zeebe:taskHeaders></bpmn:extensionElements>' % (retries, io, th))


REST_ATTR = ' zeebe:modelerTemplate="io.camunda.connectors.HttpJson.v2"'


def script(expr, var):
    return '<bpmn:extensionElements><zeebe:script expression="%s" resultVariable="%s" /></bpmn:extensionElements>' % (q(expr), var)


def brt(decision, var, inputs=()):
    io_ = "".join('<zeebe:input source="%s" target="%s" />' % (q(s), t) for s, t in inputs)
    io_ = "<zeebe:ioMapping>%s</zeebe:ioMapping>" % io_ if io_ else ""
    return '<bpmn:extensionElements><zeebe:calledDecision decisionId="%s" resultVariable="%s" bindingType="latest" />%s</bpmn:extensionElements>' % (decision, var, io_)


def io(inputs):
    return "<bpmn:extensionElements><zeebe:ioMapping>%s</zeebe:ioMapping></bpmn:extensionElements>" % "".join(
        '<zeebe:input source="%s" target="%s" />' % (q(s), t) for s, t in inputs)


def error_boundary(p, bid, host, name, x, y, parent=None):
    p.add(bid, "boundaryEvent", name, x, y, inner='<bpmn:errorEventDefinition id="%s_def" />' % bid,
          attrs=' cancelActivity="true" attachedToRef="%s"' % host, parent=parent)


# ---------------------------------------------------------------------------------------------
def planner():
    p = Proc("ci-test-planner", "CI test planner",
             "Decides which CI suites a pull request needs. Input: `facts` (planner.facts/1, validated by the GitHub job), "
             "optional `advisorEnabled`. Output: `finalPlan`, `decisionId`, `recorded`, `advisor`. Rules are DMN (ci-test-planner.dmn), "
             "memory is read from Elasticsearch, the AI advisor can only add suites. See camunda/README.md.")
    p.add("start", "startEvent", "PR facts received", 150, 160)
    p.add("normalise", "scriptTask", "Normalise inputs", 230, 120,
          inner=script('{advisorOn: if is defined(advisorEnabled) then advisorEnabled = true else false, nowIso: string(now()), '
                       'params: {breaker_escapes: %s, breaker_clean_days: %s}}' % (Q.BREAKER_ESCAPES, Q.BREAKER_CLEAN_DAYS), "normalised"),
          doc="advisorEnabled defaults to false until a model provider and its data policy are agreed.")
    # load memory subprocess
    p.add("loadMemory", "subProcess", "Load memory (Elasticsearch)", 400, 70, w=860, h=190,
          doc="Reads the last full run of every suite, the newest weights and the escapes of the last 90 days. Any HTTP error leaves the memory empty (rotation then prefers never-run suites).")
    p.add("m_start", "startEvent", "", 20, 78, parent="loadMemory")
    p.add("m_outcomes", "serviceTask", "Last runs per suite", 80, 40, parent="loadMemory", attrs=REST_ATTR,
          inner=rest("POST", Q.URL_OUTCOMES, Q.outcomes_body(), "outcomesResponse"))
    p.add("m_weights", "serviceTask", "Newest weights", 240, 40, parent="loadMemory", attrs=REST_ATTR,
          inner=rest("POST", Q.URL_WEIGHTS, Q.weights_body(), "weightsResponse"))
    p.add("m_escapes", "serviceTask", "Recent escapes", 400, 40, parent="loadMemory", attrs=REST_ATTR,
          inner=rest("POST", Q.URL_ESCAPES, Q.escapes_body(), "escapesResponse"))
    p.add("m_build", "businessRuleTask", "Build compact memory (DMN)", 560, 40, parent="loadMemory", inner=brt("memory", "memory", [("= normalised.nowIso", "nowIso"), ("= normalised.params", "params")]),
          doc="ci-test-memory.dmn, decision `memory`. Inputs: outcomesResponse, escapesResponse, weightsResponse, nowIso, params.")
    p.add("m_end", "endEvent", "", 730, 78, parent="loadMemory")
    for a, b in (("m_start", "m_outcomes"), ("m_outcomes", "m_weights"), ("m_weights", "m_escapes"), ("m_escapes", "m_build"), ("m_build", "m_end")):
        p.flow(a, b)
    error_boundary(p, "memError", "loadMemory", "Elasticsearch unavailable", 600, 242)
    p.add("emptyMemory", "scriptTask", "Use empty memory", 560, 310,
          inner=script('{now: nowIso, weights_version: "none", suites: {}, weights: {}, breaker: {}}', "memory"))
    p.add("rules", "businessRuleTask", "Apply decision tables R1-R12 (DMN)", 1320, 110, inner=brt("plan", "plan"),
          doc="ci-test-planner.dmn, decision `plan`. Inputs: facts, memory. Output: mode, decision[], rotation, rulesFired, overrides, safetyNet.")
    p.add("advGw", "exclusiveGateway", "AI advisor enabled?", 1500, 125)
    p.add("advisor", "serviceTask", "AI advisor: suggest extra suites", 1600, 20, attrs=' zeebe:modelerTemplate="io.camunda.connectors.agenticai.ai-agent-task.v2"',
          inner=Q.ai_agent_task(), doc="AI Agent Task (v2) without tools: one call, JSON answer. It can only add suites; see the merge step. Input names must be re-checked against the element template in Desktop Modeler.")
    error_boundary(p, "advError", "advisor", "Advisor failed", 1650, 102)
    p.add("advFailed", "scriptTask", "Advisor skipped", 1640, 250, inner=script("null", "advisorRaw"))
    p.add("noAdvisor", "scriptTask", "No advisor", 1600, 160 + 70 - 20 + 90, inner=script("null", "advisorRaw"))
    p.add("merge", "scriptTask", "Merge advisor output (add only) and build the record", 1800, 110, inner=script(Q.MERGE_EXPR, "merged"),
          doc="Allow-list: only path-triggered suites of the expensive layers that the rules skipped can be added. Output: finalPlan, advisor, record.")
    p.add("record", "serviceTask", "Record the decision (Elasticsearch)", 1980, 110, attrs=REST_ATTR,
          inner=rest("POST", Q.URL_DECISIONS, "merged.record", "recorded", "= {decisionId: response.body._id, recorded: true}"),
          doc="One document per decision in ci-planner-decisions (the audit trail). Camunda keeps the process and decision instances too, for as long as retention allows.")
    error_boundary(p, "recError", "record", "Not recorded", 2030, 192)
    p.add("notRecorded", "scriptTask", "Mark as not recorded", 2010, 250, inner=script("{decisionId: null, recorded: false}", "recorded"))
    p.add("join", "exclusiveGateway", "", 2160, 125)
    p.add("end", "endEvent", "Plan returned", 2250, 128)
    p.flow("start", "normalise")
    p.flow("normalise", "loadMemory", via=[(310, 160)])
    p.flow("loadMemory", "rules", via=[])
    p.flow("memError", "emptyMemory")
    p.flow("emptyMemory", "rules", via=[(1260 + 20, 350), (1300, 150)])
    p.flow("rules", "advGw")
    p.flow("advGw", "advisor", name="yes", cond="= " + Q.ADVISOR_GATE, via=[(1525, 60)])
    p.flow("advGw", "noAdvisor", name="no", default=True, via=[(1525, 370)])
    p.flow("advisor", "merge", via=[(1780, 60), (1780, 150)])
    p.flow("advError", "advFailed")
    p.flow("advFailed", "merge", via=[(1780, 290), (1780, 150)])
    p.flow("noAdvisor", "merge", via=[(1780, 370), (1780, 150)])
    p.flow("merge", "record")
    p.flow("record", "join")
    p.flow("recError", "notRecorded")
    p.flow("notRecorded", "join", via=[(2185, 290)])
    p.flow("join", "end")
    return p


def record_proc():
    p = Proc("ci-test-record", "CI test planner: record outcomes",
             "Writes a batch of documents (outcomes, escapes) to Elasticsearch. Input: `docs` = list of {index, id, body}. Ids are stable, so a document sent twice is written once.")
    p.add("start", "startEvent", "Documents received", 150, 100)
    p.add("write", "serviceTask", "Write document (Elasticsearch)", 260, 78, attrs=REST_ATTR,
          inner=rest("PUT", Q.URL_PUT_DOC, "doc.body", "written", "= {id: response.body._id, result: response.body.result}"))
    # parallel multi-instance
    n = p.node("write")
    n["inner"] += ('<bpmn:multiInstanceLoopCharacteristics><bpmn:extensionElements><zeebe:loopCharacteristics inputCollection="= docs" inputElement="doc" '
                   'outputCollection="results" outputElement="= written" /></bpmn:extensionElements></bpmn:multiInstanceLoopCharacteristics>')
    p.add("end", "endEvent", "Recorded", 450, 100)
    p.flow("start", "write")
    p.flow("write", "end")
    return p


def learn_proc():
    p = Proc("ci-test-learn", "CI test planner: weekly learner",
             "Every Monday 06:00: statistics per module and suite from Elasticsearch -> DMN weights (bounded step) -> weights document. "
             "Rollback: the planner reads the newest weights document, so write the previous one back or delete the newest.")
    p.add("start", "startEvent", "Every Monday 06:00", 150, 100,
          inner='<bpmn:timerEventDefinition id="learn_timer"><bpmn:timeCycle xsi:type="bpmn:tFormalExpression">0 0 6 * * MON</bpmn:timeCycle></bpmn:timerEventDefinition>')
    p.add("configure", "scriptTask", "Configure", 230, 78,
          inner=script('{repo: "camunda/camunda-deployment-references", params: {prior_a: 1, prior_b: 19, escape_factor: 3, weight_slope: 5, weight_min: 1, weight_max: 4, max_step: 0.5}}', "normalised"),
          doc="One repository per learner process in v1. The constants are the defaults of the Python reference learner.")
    p.add("stats", "serviceTask", "Statistics per module and suite", 380, 78, attrs=REST_ATTR,
          inner=rest("POST", Q.URL_OUTCOMES, Q.stats_body("normalised.repo"), "statsResponse"))
    p.add("esc", "serviceTask", "Escapes of the last 180 days", 530, 78, attrs=REST_ATTR,
          inner=rest("POST", Q.URL_ESCAPES, Q.escapes_body(180, "normalised.repo"), "escapesResponse"))
    p.add("prev", "serviceTask", "Newest weights", 680, 78, attrs=REST_ATTR,
          inner=rest("POST", Q.URL_WEIGHTS, Q.weights_body("normalised.repo"), "weightsResponse"))
    p.add("shape", "scriptTask", "Shape statistics", 830, 78, inner=script(Q.STATS_EXPR, "learnInput"))
    p.add("learn", "businessRuleTask", "Compute weights (DMN)", 980, 78,
          inner=brt("learned", "learned", [("= learnInput.stats", "stats"), ("= learnInput.prev", "prev"), ("= normalised.params", "params")]),
          doc="ci-test-learn.dmn, decision `learned`. Inputs: stats, prev, params.")
    p.add("doc", "scriptTask", "Build weights document", 1130, 78, inner=script(Q.WEIGHTS_DOC_EXPR, "weightsDoc"))
    p.add("write", "serviceTask", "Write weights (Elasticsearch)", 1280, 78, attrs=REST_ATTR,
          inner=rest("POST", Q.URL_WEIGHTS_DOC, "weightsDoc", "weightsWritten", "= {id: response.body._id}"))
    p.add("end", "endEvent", "Weights written", 1440, 100)
    for a, b in (("start", "configure"), ("configure", "stats"), ("stats", "esc"), ("esc", "prev"), ("prev", "shape"), ("shape", "learn"),
                 ("learn", "doc"), ("doc", "write"), ("write", "end")):
        p.flow(a, b)
    return p


if __name__ == "__main__":
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    for name, proc in (("ci-test-planner.bpmn", planner()), ("ci-test-record.bpmn", record_proc()), ("ci-test-learn.bpmn", learn_proc())):
        open(os.path.join(out, name), "w").write(proc.xml())
