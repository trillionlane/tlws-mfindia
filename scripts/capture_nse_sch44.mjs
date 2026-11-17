#!/usr/bin/env node
// Run with the approved TLAM checkout and its TypeScript loader. No local auth
// implementation, direct HTTP client, database access, or automatic retries.
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

const ENDPOINT = '/nsemfdesk/api/v2/reports/MASTER_DOWNLOAD';
const UAT_ORIGIN = 'https://nseinvestuat.nseindia.com';
const MAX_BYTES = 64 * 1024 * 1024;
const SAFE_FAILURES = new Set([
  'DNS_FAILURE', 'TCP_FAILURE', 'TLS_FAILURE', 'CERTIFICATE_FAILURE',
  'AKAMAI_BLOCKED', 'NSE_EDGE_ACCESS_RESPONSE', 'NSE_HTTP_403_CAUSE_UNCONFIRMED',
  'SESSION_BOOTSTRAP_FAILED', 'COOKIE_REQUIRED', 'REFERER_REQUIRED', 'SESSION_EXPIRED',
  'AUTH_REJECTED', 'PROVIDER_ROLE_FORBIDDEN', 'INVALID_MEMBER_CODE', 'INVALID_API_KEY',
  'INVALID_API_SECRET', 'INVALID_LOGIN', 'REQUEST_FORMAT_INVALID', 'REQUEST_TIMEOUT',
  'REQUEST_CANCELLED', 'RATE_LIMITED', 'PROVIDER_5XX', 'NSE_APPLICATION_ERROR',
  'SCHEMA_VALIDATION_FAILED', 'READ_ONLY_API_FAILED', 'INVALID_CONFIG',
]);
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');
const status = (value) => Number.isInteger(value) && value >= 100 && value <= 599 ? value : null;
const writePrivate = (path, bytes) => writeFile(path, bytes, { mode: 0o600, flag: 'wx' });
const json = (record) => JSON.stringify(record) + '\n';

function sourceFile(disposition) {
  const unidentified = (filename_status) => ({ filename: null, export_date: null, filename_status });
  if (typeof disposition !== 'string' || !disposition.trim()) return unidentified('HEADER_MISSING');
  const [kind, ...parameters] = disposition.split(';').map((value) => value.trim());
  const names = parameters.filter((parameter) => /^filename(?:\*|\s*=)/i.test(parameter));
  if (!/^(?:attachment|inline)$/i.test(kind) || names.length !== 1) {
    return unidentified('FILENAME_UNRECOGNIZED');
  }
  const match = /^filename\s*=\s*(?:"(NSE_NSEINVEST_ALL_\d{8}\.txt)"|(NSE_NSEINVEST_ALL_\d{8}\.txt))$/i.exec(names[0]);
  if (!match) return unidentified('FILENAME_UNRECOGNIZED');
  const filename = match[1] ?? match[2];
  const [, day, month, year] = /_(\d{2})(\d{2})(\d{4})\.txt$/i.exec(filename);
  const iso = `${year}-${month}-${day}`;
  const parsed = new Date(iso);
  const dateValid = Number.isFinite(+parsed) && parsed.toISOString().slice(0, 10) === iso
    && Date.parse(iso + 'T00:00:00+05:30') <= Date.now();
  return { filename, export_date: dateValid ? iso : null,
    filename_status: dateValid ? 'DATED_FILENAME' : 'EXPORT_DATE_INVALID' };
}

function observeFile(body) {
  const text = new TextDecoder('utf-8', { fatal: true }).decode(body);
  const lines = text.replace(/\r\n/g, '\n').replace(/\n$/, '').split('\n');
  const [serial, scheme, ...tail] = lines[0].split('|');
  if (lines.length < 2 || lines.some((line) => !line || line.includes('\r'))
      || !['UNIQUE SR NO', 'UNIQUE NO'].includes(serial) || scheme !== 'SCHEME CODE') {
    throw new Error('REFERENCE_LAYOUT_UNRECOGNIZED');
  }
  return { header_columns: tail.length + 2, row_count: lines.length - 1 };
}

export async function captureSchemeMaster({ outputDir, connectorRevision, download }) {
  await mkdir(outputDir, { mode: 0o700 });
  const provenance = {
    format: 'nse01-sch-api-capture-v1', environment: 'UAT', method: 'POST',
    endpoint: ENDPOINT, file_type: 'SCH', report_type: 'CONSOLIDATED_SCHEME_MASTER',
    source_scope: 'UNVERIFIED', connector_revision: connectorRevision,
  };
  await writePrivate(join(outputDir, 'attempt.json'), json(provenance));

  let exchange;
  let providerTransportStarted = false;
  const observeStage = ({ phase, stage }) => {
    if (phase === 'PROVIDER_REQUEST' && stage === 'TRANSPORT_STARTED') providerTransportStarted = true;
  };
  const observeExchange = (result) => {
    exchange = {
      complete: result.complete === true, representation: result.representation,
      credentialsExcluded: result.credentialsExcluded !== false,
      httpStatus: status(result.httpStatus), postInvoked: result.postInvoked === true,
      sha256: result.receivedPrefixSha256,
    };
  };

  let response;
  let receipt;
  try {
    response = await download(observeStage, observeExchange);
  } catch (error) {
    receipt = {
      outcome: 'DOWNLOAD_FAILED',
      reason: SAFE_FAILURES.has(error?.classification) ? error.classification : 'CONNECTOR_FAILED',
      http_status: status(error?.httpStatus) ?? exchange?.httpStatus ?? null,
    };
  }
  if (!receipt) {
    const body = response?.body;
    const httpStatus = status(response?.httpStatus);
    if (httpStatus !== 200 || !providerTransportStarted || !exchange?.postInvoked || !exchange.complete || exchange.representation !== 'DECODED'
        || exchange.credentialsExcluded || !Buffer.isBuffer(body) || !body.length
        || body.length > MAX_BYTES || hash(body) !== exchange.sha256) {
      receipt = { outcome: 'DOWNLOAD_FAILED', reason: 'UNSAFE_OR_INCOMPLETE_RESPONSE', http_status: httpStatus };
    } else {
      let shape;
      try {
        shape = observeFile(body);
      } catch {
        receipt = { outcome: 'DOWNLOAD_FAILED', reason: 'REFERENCE_LAYOUT_UNRECOGNIZED', http_status: httpStatus };
      }
      if (shape) {
        const file = sourceFile(response.contentDisposition);
        await writePrivate(join(outputDir, 'source.txt'), body);
        receipt = {
          ...file, ...shape, http_status: httpStatus, sha256: hash(body), byte_length: body.length,
          outcome: file.export_date ? 'RETAINED_FOR_OFFLINE_VALIDATION' : 'RETAINED_EXPORT_DATE_UNVERIFIED',
        };
      }
    }
  }
  if (Buffer.isBuffer(response?.body)) response.body.fill(0);
  const result = {
    ...provenance, ...receipt, provider_transport_started: providerTransportStarted,
    post_invoked: exchange?.postInvoked ?? null, observed_at_utc: new Date().toISOString(),
    publication: 'NOT_ATTEMPTED',
  };
  await writePrivate(join(outputDir, 'receipt.json'), json(result));
  return result;
}

async function main() {
  const { values } = parseArgs({ options: {
    'connector-root': { type: 'string' },
    'connector-revision': { type: 'string' },
    'output-dir': { type: 'string' },
  } });
  if (!values['connector-root'] || !values['output-dir'] ||
      !/^[0-9a-f]{40}$/.test(values['connector-revision'] ?? '')) throw new Error('INVALID_ARGUMENTS');

  const root = resolve(values['connector-root']);
  const revision = values['connector-revision'];
  const git = (...args) => execFileSync('git', args, { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
  if (git('rev-parse', 'HEAD') !== revision || git('status', '--porcelain')) throw new Error('CONNECTOR_CHECKOUT_DIFFERS');

  const load = (name) => import(pathToFileURL(join(root, 'src/server/connectors/nse', name)).href);
  const runtime = await load('nse-runtime-configuration.ts');
  const config = runtime.runtimeNseConfiguration();
  if (![UAT_ORIGIN, UAT_ORIGIN + '/'].includes(config.baseUrl)) throw new Error('UNAPPROVED_NSE_ENVIRONMENT');
  const { nseInvestTransport } = await load('nse-transport.ts');
  const result = await captureSchemeMaster({
    outputDir: resolve(values['output-dir']), connectorRevision: revision,
    download: (stage, exchange) =>
      nseInvestTransport.downloadMasterOnce(runtime.NSE_RUNTIME_INSTANCE_ID, 'SCH', null, stage, exchange),
  });
  console.log(json(result).trimEnd());
  if (result.outcome !== 'RETAINED_FOR_OFFLINE_VALIDATION'
      && result.outcome !== 'RETAINED_EXPORT_DATE_UNVERIFIED') {
    process.exitCode = 1;
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(() => {
    console.error('NSE_SCH_CAPTURE_SETUP_OR_STORAGE_FAILED');
    process.exitCode = 1;
  });
}
