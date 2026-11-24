// Copy the report answer's schema out of the service API document.
//
// The outbox validates the hub's answer to a `report` against the contract,
// but this image holds only this directory, so it ships a generated subset of
// docs/service-api/openapi.json: VoiceReportEnvelope and every schema it
// references, unchanged.
//
// usage: node scripts/report_schema.mjs           # rewrite src/report_schema.json
//        node scripts/report_schema.mjs --check   # exit 1 when it has drifted

import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const DOCUMENT = new URL('../../../docs/service-api/openapi.json', import.meta.url);
export const TARGET = new URL('../src/report_schema.json', import.meta.url);
const ROOT = 'VoiceReportEnvelope';
const PREFIX = '#/components/schemas/';

function refs(node) {
  if (Array.isArray(node)) return node.flatMap(refs);
  if (node && typeof node === 'object') {
    const own = typeof node.$ref === 'string' ? [node.$ref.slice(PREFIX.length)] : [];
    return own.concat(Object.values(node).flatMap(refs));
  }
  return [];
}

function sorted(node) {
  if (Array.isArray(node)) return node.map(sorted);
  if (node && typeof node === 'object') {
    return Object.fromEntries(Object.keys(node).sort().map((key) => [key, sorted(node[key])]));
  }
  return node;
}

/** The subset, serialized exactly as the shipped file holds it. */
export function extract(document) {
  const { schemas } = document.components;
  const wanted = new Set();
  const todo = [ROOT];
  while (todo.length) {
    const name = todo.pop();
    if (!wanted.has(name)) {
      wanted.add(name);
      todo.push(...refs(schemas[name]));
    }
  }
  const subset = { components: { schemas: Object.fromEntries([...wanted].map((name) => [name, schemas[name]])) } };
  return `${JSON.stringify(sorted(subset), null, 2)}\n`;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const expected = extract(JSON.parse(readFileSync(DOCUMENT, 'utf8')));
  if (process.argv.includes('--check')) {
    if (!existsSync(TARGET) || readFileSync(TARGET, 'utf8') !== expected) {
      console.error('report_schema.json is stale: run node scripts/report_schema.mjs');
      process.exit(1);
    }
  } else {
    writeFileSync(TARGET, expected);
  }
}
