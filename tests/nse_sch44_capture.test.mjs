import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { captureSchemeMaster } from '../scripts/capture_nse_sch44.mjs';

const source = await readFile(new URL('./fixtures/nse01/sch44_synthetic.psv', import.meta.url));
const digest = createHash('sha256').update(source).digest('hex');
const revision = 'a'.repeat(40);

async function capture(t, download) {
  const parent = await mkdtemp(join(tmpdir(), 'nse-sch-unit-'));
  t.after(() => rm(parent, { recursive: true, force: true }));
  const dir = join(parent, 'once');
  let calls = 0;
  const result = await captureSchemeMaster({
    outputDir: dir, connectorRevision: revision, download: (...args) => {
      calls++;
      return download(...args);
    },
  });
  assert.equal(calls, 1);
  await assert.rejects(captureSchemeMaster({
    outputDir: dir, connectorRevision: revision,
    download: () => assert.fail('consumed output must stop before sending'),
  }), { code: 'EEXIST' });
  return { dir, result };
}

function syntheticReply(stage, observe, exchange = {}) {
  stage({ phase: 'PROVIDER_REQUEST', stage: 'TRANSPORT_STARTED' });
  observe({ complete: true, representation: 'DECODED', credentialsExcluded: false,
    httpStatus: 200, postInvoked: true, receivedPrefixSha256: digest, ...exchange });
  return { body: Buffer.from(source), httpStatus: 200,
    contentDisposition: 'attachment; filename="NSE_NSEINVEST_ALL_18092026.txt"' };
}

test('one SCH capture retains exact bytes in a private directory and leaves scope open', async (t) => {
  const { dir, result } = await capture(t, syntheticReply);
  assert.deepEqual(await readFile(join(dir, 'source.txt')), source);
  assert.equal((await stat(dir)).mode & 0o777, 0o700);
  assert.equal((await stat(join(dir, 'source.txt'))).mode & 0o777, 0o600);
  assert.equal(result.sha256, digest);
  assert.equal(result.byte_length, source.length);
  assert.equal(result.export_date, '2026-09-18');
  assert.equal(result.header_columns, 44);
  assert.equal(result.row_count, 5);
  assert.equal(result.source_scope, 'UNVERIFIED');
  assert.equal(result.publication, 'NOT_ATTEMPTED');
});

test('unauthorized replies retain safe status without provider text or auth values', async (t) => {
  const { dir, result } = await capture(t, (stage, observe) => {
    stage({ phase: 'PROVIDER_REQUEST', stage: 'TRANSPORT_STARTED' });
    observe({ complete: true, httpStatus: 403, postInvoked: true });
    throw Object.assign(new Error('SYNTHETIC_SECRET'), { classification: 'PROVIDER_ROLE_FORBIDDEN', httpStatus: 403 });
  });
  assert.equal(result.reason, 'PROVIDER_ROLE_FORBIDDEN');
  assert.equal(result.http_status, 403);
  assert.equal(result.provider_transport_started, true);
  assert.equal((await readFile(join(dir, 'receipt.json'), 'utf8')).includes('SYNTHETIC_SECRET'), false);
  assert.deepEqual((await readdir(dir)).sort(), ['attempt.json', 'receipt.json']);
});

for (const exchange of [{ complete: false }, { credentialsExcluded: true }, { receivedPrefixSha256: '0'.repeat(64) }]) {
  test('untrusted bodies never enter the archive', async (t) => {
    const { dir, result } = await capture(t, (stage, observe) => syntheticReply(stage, observe, exchange));
    assert.equal(result.reason, 'UNSAFE_OR_INCOMPLETE_RESPONSE');
    assert.deepEqual((await readdir(dir)).sort(), ['attempt.json', 'receipt.json']);
  });
}

test('a missing filename does not turn the retrieval timestamp into an export date', async (t) => {
  const { dir, result } = await capture(t, (stage, observe) => ({
    ...syntheticReply(stage, observe), contentDisposition: null,
  }));
  assert.equal(result.outcome, 'RETAINED_EXPORT_DATE_UNVERIFIED');
  assert.equal(result.filename_status, 'HEADER_MISSING');
  assert.equal(result.export_date, null);
  assert.deepEqual(await readFile(join(dir, 'source.txt')), source);
});


test('the capture CLI exits zero for both retention outcomes and nonzero for a denial', async (t) => {
  const parent = await mkdtemp(join(tmpdir(), 'nse-sch-cli-'));
  t.after(() => rm(parent, { recursive: true, force: true }));
  const root = join(parent, 'connector');
  const nse = join(root, 'src/server/connectors/nse');
  await mkdir(nse, { recursive: true });
  await writeFile(join(nse, 'nse-runtime-configuration.ts'), `
    export const NSE_RUNTIME_INSTANCE_ID = 'fixture-uat';
    export const runtimeNseConfiguration = () => ({ baseUrl: 'https://nseinvestuat.nseindia.com' });
  `);
  await writeFile(join(nse, 'nse-transport.ts'), `
    export const nseInvestTransport = {
      async downloadMasterOnce(instance, fileType, organization, stage, observe) {
        if (instance !== 'fixture-uat' || fileType !== 'SCH' || organization !== null) throw Error('WRONG_REQUEST');
        stage({ phase: 'PROVIDER_REQUEST', stage: 'TRANSPORT_STARTED' });
        if (process.env.SCH_FIXTURE_MODE === 'denied') {
          observe({ httpStatus: 403, postInvoked: true });
          throw Object.assign(Error('SYNTHETIC_SECRET'), { classification: 'PROVIDER_ROLE_FORBIDDEN', httpStatus: 403 });
        }
        observe({ httpStatus: 200, postInvoked: true, complete: true, representation: 'DECODED',
          credentialsExcluded: false, receivedPrefixSha256: '${digest}' });
        return { body: Buffer.from('${source.toString('base64')}', 'base64'), httpStatus: 200,
          contentDisposition: process.env.SCH_FIXTURE_MODE === 'dated'
            ? 'attachment; filename=NSE_NSEINVEST_ALL_18092026.txt' : null };
      },
    };
  `);
  const git = (...args) => execFileSync('git', args, { cwd: root, encoding: 'utf8' }).trim();
  git('init', '-q');
  git('add', '.');
  git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
    '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'Fixture connector');
  const head = git('rev-parse', 'HEAD');
  const cli = fileURLToPath(new URL('../scripts/capture_nse_sch44.mjs', import.meta.url));
  for (const [mode, outcome, exitCode, exportDate] of [
    ['undated', 'RETAINED_EXPORT_DATE_UNVERIFIED', 0, null],
    ['dated', 'RETAINED_FOR_OFFLINE_VALIDATION', 0, '2026-09-18'],
    ['denied', 'DOWNLOAD_FAILED', 1, undefined],
  ]) {
    await t.test(mode, async () => {
      const dir = join(parent, mode);
      const run = spawnSync(process.execPath, [cli, '--connector-root', root,
        '--connector-revision', head, '--output-dir', dir], {
        encoding: 'utf8', env: { ...process.env, SCH_FIXTURE_MODE: mode },
      });
      assert.equal(run.stderr, '');
      const receipt = JSON.parse(await readFile(join(dir, 'receipt.json'), 'utf8'));
      assert.deepEqual(JSON.parse(run.stdout), receipt);
      assert.equal(receipt.outcome, outcome);
      assert.equal(receipt.export_date, exportDate);
      assert.equal(receipt.source_scope, 'UNVERIFIED');
      assert.equal(receipt.publication, 'NOT_ATTEMPTED');
      assert.equal(run.status, exitCode);
      if (exitCode === 0) {
        assert.deepEqual(await readFile(join(dir, 'source.txt')), source);
      } else {
        assert.equal(receipt.http_status, 403);
        assert.equal(receipt.reason, 'PROVIDER_ROLE_FORBIDDEN');
        assert.deepEqual((await readdir(dir)).sort(), ['attempt.json', 'receipt.json']);
      }
    });
  }
});
