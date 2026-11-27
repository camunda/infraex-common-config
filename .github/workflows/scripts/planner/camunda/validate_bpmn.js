#!/usr/bin/env node
// Static checks of the generated BPMN: schema (bpmn-moddle with the Zeebe extension), graph sanity,
// diagram completeness, and a FEEL syntax check of every expression with feelin.
// usage: node validate_bpmn.js <file.bpmn>...
const fs = require('fs');
const path = require('path');
const req = name => require(require.resolve(name, { paths: [__dirname] }));
const { BpmnModdle } = req('bpmn-moddle');
const zeebe = JSON.parse(fs.readFileSync(path.join(__dirname, 'node_modules', 'zeebe-bpmn-moddle', 'resources', 'zeebe.json'), 'utf8'));
const feelin = req('feelin');

function walk(el, fn) { fn(el); for (const k of ['flowElements', 'artifacts']) for (const c of el[k] || []) walk(c, fn); }

(async () => {
  let failed = 0;
  for (const file of process.argv.slice(2)) {
    const errs = [];
    const { rootElement, warnings } = await new BpmnModdle({ zeebe }).fromXML(fs.readFileSync(file, 'utf8'));
    for (const w of warnings) errs.push('moddle: ' + w.message);
    const proc = rootElement.rootElements.find(r => r.$type === 'bpmn:Process');
    if (!proc || !proc.isExecutable) errs.push('no executable process');
    const ids = new Set(); const nodes = {}; const flows = [];
    walk(proc, el => {
      if (el.id) { if (ids.has(el.id)) errs.push('duplicate id ' + el.id); ids.add(el.id); }
      if (el.$type === 'bpmn:SequenceFlow') flows.push(el); else if (el.$type !== 'bpmn:Process') nodes[el.id] = el;
    });
    for (const f of flows) {
      if (!nodes[f.sourceRef.id] || !nodes[f.targetRef.id]) errs.push('flow ' + f.id + ' dangling');
    }
    for (const n of Object.values(nodes)) {
      if (n.$type === 'bpmn:StartEvent' && !(n.outgoing || []).length) errs.push('start without outgoing ' + n.id);
      if (!['bpmn:StartEvent', 'bpmn:BoundaryEvent'].includes(n.$type) && !(n.incoming || []).length) errs.push('no incoming ' + n.id);
      if (n.$type !== 'bpmn:EndEvent' && !(n.outgoing || []).length) errs.push('no outgoing ' + n.id);
      if (n.$type === 'bpmn:BoundaryEvent' && !n.attachedToRef) errs.push('boundary not attached ' + n.id);
      if (n.$type === 'bpmn:ExclusiveGateway' && (n.outgoing || []).length > 1 && !n.default) {
        const unc = n.outgoing.filter(f => !f.conditionExpression);
        if (unc.length) errs.push('gateway ' + n.id + ' has unconditional outgoing flows without a default');
      }
    }
    // diagram: every node and flow has a shape/edge
    const di = rootElement.diagrams[0].plane.planeElement.map(e => e.bpmnElement.id);
    for (const id of [...Object.keys(nodes), ...flows.map(f => f.id)]) if (!di.includes(id)) errs.push('no diagram element for ' + id);
    // FEEL: every expression
    const exprs = [];
    walk(proc, el => {
      const ext = (el.extensionElements && el.extensionElements.values) || [];
      for (const v of ext) {
        if (v.$type === 'zeebe:Script') exprs.push([el.id + ' script', v.expression]);
        if (v.$type === 'zeebe:IoMapping') {
          for (const i of v.inputParameters || []) if ((i.source || '').startsWith('=')) exprs.push([el.id + ' input ' + i.target, i.source]);
          for (const o of v.outputParameters || []) if ((o.source || '').startsWith('=')) exprs.push([el.id + ' output ' + o.target, o.source]);
        }
        if (v.$type === 'zeebe:TaskHeaders') for (const h of v.values || []) if ((h.value || '').startsWith('=')) exprs.push([el.id + ' header ' + h.key, h.value]);
        if (v.$type === 'zeebe:LoopCharacteristics' && (v.inputCollection || '').startsWith('=')) exprs.push([el.id + ' loop', v.inputCollection]);
      }
      if (el.$type === 'bpmn:SequenceFlow' && el.conditionExpression) exprs.push([el.id + ' condition', el.conditionExpression.body]);
      if (el.$type === 'bpmn:ServiceTask' && el.extensionElements) { /* connectors covered above */ }
    });
    for (const f of flows) if (f.conditionExpression) exprs.push([f.id + ' condition', f.conditionExpression.body]);
    for (const [where, text] of exprs) {
      const body = text.replace(/^=\s*/, '');
      try { feelin.evaluate(body, {}); } catch (e) { errs.push('FEEL ' + where + ': ' + e.message.slice(0, 120)); }
    }
    console.log('%s: %d elements, %d flows, %d FEEL expressions, %d problem(s)', path.basename(file), Object.keys(nodes).length, flows.length, exprs.length, errs.length);
    for (const e of errs) console.log('  - ' + e);
    failed += errs.length;
  }
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
