#!/usr/bin/env node
// Evaluate the planner DMN locally with feelin (a JavaScript FEEL implementation), for conformance
// tests against the Python policy. It is NOT the Zeebe DMN engine: dialect differences are possible,
// so the DMN must still be deployed and exercised on a real cluster (see camunda/README.md).
//
// usage: node eval_dmn.js <file.dmn> <input.json|-> [decision]   input = {"facts": {...}, "memory": {...}} or a list of them
const fs = require('fs');
const path = require('path');
const req = name => require(require.resolve(name, { paths: [__dirname] }));
const feelin = req('feelin');
const { DmnModdle } = req('dmn-moddle');

async function load(file) {
  const { rootElement, warnings } = await new DmnModdle().fromXML(fs.readFileSync(file, 'utf8'));
  return { def: rootElement, warnings };
}

function topo(decisions) {
  const byId = Object.fromEntries(decisions.map(d => [d.id, d]));
  const seen = new Set(); const out = [];
  const visit = d => {
    if (seen.has(d.id)) return; seen.add(d.id);
    for (const r of d.informationRequirement || []) if (r.requiredDecision) visit(byId[r.requiredDecision.href.slice(1)]);
    out.push(d);
  };
  decisions.forEach(visit);
  return out;
}

function evalExpr(text, ctx, warns, where) {
  const r = feelin.evaluate(text, ctx);
  for (const w of r.warnings || []) warns.push({ where, type: w.type, message: w.message });
  return r.value;
}

function makeBkm(bkm, env, warns) {
  const fd = bkm.encapsulatedLogic;
  const params = (fd.formalParameter || []).map(p => p.name);
  const body = fd.body;
  return (...args) => {
    const ctx = Object.assign({}, env);
    params.forEach((p, i) => { ctx[p] = args[i]; });
    if (body.$type === 'dmn:LiteralExpression') return evalExpr(body.text, ctx, warns, 'bkm:' + bkm.name);
    // decision table
    const hit = body.hitPolicy || 'UNIQUE';
    const results = [];
    for (const rule of body.rule || []) {
      let ok = true;
      (body.input || []).forEach((inp, i) => {
        const entry = (rule.inputEntry[i].text || '').trim();
        if (!entry || entry === '-') return;
        const value = evalExpr(inp.inputExpression.text, ctx, warns, 'bkm:' + bkm.name + ' input ' + i);
        const t = feelin.unaryTest(entry, Object.assign({}, ctx, { '?': value }));
        for (const w of t.warnings || []) warns.push({ where: 'bkm:' + bkm.name + ' entry', type: w.type, message: w.message });
        if (t.value !== true) ok = false;
      });
      if (!ok) continue;
      const out = {};
      (body.output || []).forEach((o, i) => { out[o.name] = evalExpr(rule.outputEntry[i].text, ctx, warns, 'bkm:' + bkm.name + ' output ' + o.name); });
      results.push(out);
      if (hit === 'FIRST') break;
    }
    return hit === 'COLLECT' ? results : (results[0] === undefined ? null : results[0]);
  };
}

async function run(file, input, loaded) {
  const { def } = loaded || await load(file);
  const warns = [];
  const elems = def.drgElement;
  const env = Object.assign({}, input);
  for (const e of elems.filter(x => x.$type === 'dmn:BusinessKnowledgeModel')) env[e.name] = makeBkm(e, env, warns);
  const results = {};
  for (const d of topo(elems.filter(x => x.$type === 'dmn:Decision'))) {
    const v = evalExpr(d.decisionLogic.text, env, warns, 'decision:' + d.name);
    env[d.name] = v; results[d.name] = v;
  }
  return { results, warnings: warns };
}

module.exports = { run };

if (require.main === module) {
  const [file, inp, want] = process.argv.slice(2);
  const input = JSON.parse(fs.readFileSync(inp === '-' ? 0 : inp, 'utf8'));
  (async () => {
    const loaded = await load(file);
    if (Array.isArray(input)) {  // batch: one result per input, only the wanted decision
      const out = [];
      for (const i of input) { const r = await run(file, i, loaded); out.push({ value: r.results[want || 'plan'], warnings: r.warnings }); }
      console.log(JSON.stringify(out));
    } else {
      const r = await run(file, input, loaded);
      console.log(JSON.stringify(want ? { [want]: r.results[want], warnings: r.warnings } : r, null, 1));
    }
  })().catch(e => { console.error(e); process.exit(1); });
}
