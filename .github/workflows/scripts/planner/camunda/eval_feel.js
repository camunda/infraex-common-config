#!/usr/bin/env node
// Evaluate FEEL expressions with feelin. input: [{"expr": "...", "vars": {...}}, ...] -> [{"value":..., "warnings":[...]}]
const fs = require('fs');
const req = name => require(require.resolve(name, { paths: [__dirname] }));
const feelin = req('feelin');
const input = JSON.parse(fs.readFileSync(process.argv[2] === '-' || !process.argv[2] ? 0 : process.argv[2], 'utf8'));
console.log(JSON.stringify(input.map(i => {
  try { const r = feelin.evaluate(i.expr.replace(/^=\s*/, ''), i.vars || {}); return { value: r.value, warnings: (r.warnings || []).map(w => w.type + ': ' + w.message) }; }
  catch (e) { return { error: e.message }; }
})));
