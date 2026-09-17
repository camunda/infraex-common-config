// Exercise Renovate itself, not a second implementation of its matching rules.
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const renovate = dirname(require.resolve('renovate/package.json'));
const load = (path) => import(pathToFileURL(resolve(renovate, 'dist', path)));
const { applyPackageRules } = await load('util/package-rules/index.js');
const { mergeChildConfig } = await load('config/utils.js');
const { checkMinimumReleaseAge } = await load('util/minimum-release-age.js');
const { extractPackageFile } = await load('modules/manager/terraform/extract.js');
const { GlobalConfig } = await load('config/global.js');
GlobalConfig.set({ localDir: process.cwd() });
const JSON5 = require(require.resolve('json5', { paths: [renovate] }));
const base = JSON5.parse(readFileSync('default.json5', 'utf8'));
const overlay = existsSync('reviewed-automerge.json5')
  ? JSON5.parse(readFileSync('reviewed-automerge.json5', 'utf8')) : {};
const config = mergeChildConfig({ automerge: false, ...base }, overlay);
const candidate = {
  manager: 'terraform', datasource: 'terraform-provider', depType: 'required_provider',
  depName: 'aws', packageName: 'hashicorp/aws', packageFile: 'aws/main.tf',
  updateType: 'patch', currentValue: '6.0.0', newValue: '6.0.1',
};
const policy = (dependency = {}, source = config) => applyPackageRules(
  mergeChildConfig(mergeChildConfig(source, source[dependency.updateType ?? 'patch']),
    { ...candidate, ...dependency }), 'update-type');
let checks = 0;
function equal(actual, expected, message) {
  assert.deepEqual(actual, expected, message);
  checks++;
  console.log(`PASS: ${message}`);
}

const eligible = await policy();
equal(eligible.automerge, true, 'allow reviewed AWS provider patch');
const extracted = await extractPackageFile(`terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "6.0.0" }
  }
}`, 'aws/main.tf', {});
assert.ok(extracted?.deps.length, 'Renovate extracts the provider fixture');
equal((await policy(extracted.deps[0])).automerge, true, 'real Terraform extraction reaches opt-in rule');
equal((await policy({ packageName: 'hashicorp/azurerm' })).automerge, true,
  'allow reviewed Azure provider patch');
equal(eligible.groupName, null, 'eligible patch is not in the shared patch group');
equal(eligible.platformAutomerge, false, 'Renovate checks statuses instead of platform enqueue');
equal(eligible.ignoreTests, false, 'never ignore tests');
equal(eligible.automergeType, 'pr', 'never merge directly to a branch');
equal(eligible.rebaseWhen, 'behind-base-branch', 'rebase stale candidates');
equal(eligible.internalChecksFilter, 'strict', 'filter pending releases');
equal(eligible.minimumReleaseAge, '7 days', 'retain seven-day observation window');
for (const [timestamp, pending, name] of [
  [new Date(Date.now() - 8 * 86400000).toISOString(), false, 'old release eligible'],
  [new Date().toISOString(), true, 'new release blocked'],
  [undefined, true, 'missing timestamp blocked'],
]) {
  equal(checkMinimumReleaseAge(eligible, timestamp).isPending, pending, name);
}
for (const updateType of ['major', 'minor', 'pin', 'pinDigest', 'digest', 'lockFileMaintenance']) {
  equal((await policy({ updateType })).automerge, false, `${updateType} stays manual`);
}
for (const manager of ['github-actions', 'pre-commit', 'asdf', 'custom.regex', 'npm', 'future-manager']) {
  equal((await policy({ manager })).automerge, false, `${manager} stays manual`);
}
for (const packageName of ['evil/aws', 'hashicorp/aws-evil', 'hashicorp/random', '$(touch injected)']) {
  equal((await policy({ packageName })).automerge, false, `unlisted package ${packageName} stays manual`);
}
for (const packageFile of ['.github/main.tf', 'nested/.github/main.tf', 'scripts/main.tf', 'nested/run.sh']) {
  equal((await policy({ packageFile })).automerge, false, `protected path ${packageFile} stays manual`);
}
equal((await policy({ datasource: 'github-tags' })).automerge, false, 'other datasource stays manual');
equal((await policy({ isVulnerabilityAlert: true })).automerge, false, 'security remediation stays manual');
equal((await policy({}, { automerge: false, ...base })).automerge, false, 'default stays manual');
equal((await policy({}, { automerge: false, ...base })).groupName, 'patch-grouped', 'default grouping unchanged');
equal(base.extends.includes(':automergeDisabled'), true, 'default retains disabled preset');
equal(JSON5.parse(readFileSync('.github/renovate.json5', 'utf8')).extends,
  ['github>camunda/infraex-common-config:default.json5'], 'local adoption remains disabled');
console.log(`${checks} policy checks passed using Renovate ${require(resolve(renovate, 'package.json')).version}`);
