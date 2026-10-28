import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import { createNativeDiagnostics } from './native-diagnostics.mjs';

test('native print failure emits stage and tool metadata immediately without private event content', () => {
  const result = spawnSync(process.execPath, ['--input-type=module', '--eval', `
    import assert from 'node:assert/strict';
    import { createServer } from 'node:http';
    import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
    import { tmpdir } from 'node:os';
    import { join } from 'node:path';
    import { execFileSync } from 'node:child_process';
    const hidden = 'private-native-event-sentinel';
    const root = mkdtempSync(join(tmpdir(), 'openwiki-print-'));
    execFileSync('git', ['init', '-q', root]);
    writeFileSync(join(root, 'README.md'), hidden);
    process.env.OPENWIKI_CONFIG_DIR = join(root, 'config');
    const lines = [];
    const log = console.error;
    console.error = (...args) => { lines.push(args.join(' ')); log(...args); };
    let requests = 0;
    const server = createServer((request, response) => {
      request.resume();
      request.on('end', () => {
        requests++;
        if (requests === 2) {
          // Positive control before the terminal provider response: neither the
          // stage nor completed tool event may be waiting in print's buffer.
          assert.ok(lines.some(line => line.includes('"stage":"planning"')));
          assert.ok(lines.some(line => line.includes('"tool":"read_file","status":"end"')));
        }
        const message = requests === 1 ? { role: 'assistant', content: null, tool_calls: [{
          id: hidden, type: 'function', function: { name: 'read_file',
            arguments: JSON.stringify({ file_path: '/README.md' }) },
        }] } : { role: 'assistant', content: hidden };
        response.setHeader('content-type', 'application/json');
        response.end(JSON.stringify({ id: 'synthetic-' + requests, object: 'chat.completion',
          created: 0, model: 'probe', choices: [{ index: 0, message,
            finish_reason: requests === 1 ? 'tool_calls' : 'stop' }] }));
      });
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    process.env.OPENAI_COMPATIBLE_BASE_URL = 'http://127.0.0.1:' + server.address().port + '/v1';
    process.env.OPENAI_COMPATIBLE_API_KEY = hidden;
    process.env.OPENWIKI_PROVIDER = 'openai-compatible';
    process.env.OPENWIKI_MODEL_ID = 'probe';
    const fetch = globalThis.fetch;
    globalThis.fetch = (input, init) => {
      const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
      assert.equal(url.origin, process.env.OPENAI_COMPATIBLE_BASE_URL.replace('/v1', ''));
      return fetch(input, init);
    };
    const { runPrintCommand } = await import(${JSON.stringify(new URL('../node_modules/openwiki/dist/cli/runners.js', import.meta.url).href)});
    process.chdir(root);
    try {
      await runPrintCommand({ mode: 'code', command: 'init', language: 'en', modelId: 'probe', userMessage: hidden });
      assert.equal(process.exitCode, 1);
      assert.equal(requests, 2);
      process.exitCode = 0;
    } finally {
      await new Promise(resolve => server.close(resolve));
      rmSync(root, { recursive: true, force: true });
    }
  `], {
    cwd: new URL('../../', import.meta.url), encoding: 'utf8', timeout: 30000,
    env: { ...process.env, NODE_OPTIONS: '', OPENWIKI_PROVIDER_DIAGNOSTICS: 'true',
      OPENWIKI_DEBUG: '0', OPENWIKI_TELEMETRY_DISABLED: '1',
      LANGCHAIN_TRACING_V2: 'false', LANGSMITH_TRACING: 'false' },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stderr, /Repository planning worker exited without submit_plan/);
  const lines = result.stderr.split('\n').filter(line => line.startsWith('OpenWiki native diagnostics: '));
  assert.ok(lines.some(line => line.includes('"status":"error"')));
  for (const line of lines) {
    const record = JSON.parse(line.slice('OpenWiki native diagnostics: '.length));
    assert.ok(Number.isInteger(record.elapsedMs));
    assert.ok(Number.isInteger(record.toolStarts));
    assert.ok(Number.isInteger(record.toolEnds));
  }
  assert.equal(result.stdout, '');
  assert.ok(!result.stderr.includes('private-native-event-sentinel'));
  assert.ok(!result.stderr.includes('/README.md'));
});

test('native projection excludes debug text, arguments, results and unknown names; logger cannot replace a throwable', () => {
  const previous = process.env.OPENWIKI_PROVIDER_DIAGNOSTICS;
  const log = console.error;
  const lines = [];
  process.env.OPENWIKI_PROVIDER_DIAGNOSTICS = 'true';
  console.error = (...args) => lines.push(args.join(' '));
  try {
    const diagnostics = createNativeDiagnostics();
    const hidden = 'private-native-projection-sentinel';
    diagnostics.onEvent({ type: 'debug', message: hidden });
    diagnostics.onEvent({ type: 'repository_progress', stage: hidden });
    diagnostics.onEvent({ type: 'tool_start', name: hidden, input: hidden, id: hidden });
    diagnostics.onEvent({ type: 'tool_end', name: 'read_file', status: 'error', result: hidden });
    assert.ok(lines.some(line => line.includes('"tool":"other"')));
    assert.ok(!lines.join('').includes(hidden));
    const original = new Error(hidden);
    console.error = () => { throw new Error('logger failed'); };
    assert.throws(() => { try { throw original; } finally { diagnostics.finish(true); } }, error => error === original);
  } finally {
    console.error = log;
    if (previous === undefined) delete process.env.OPENWIKI_PROVIDER_DIAGNOSTICS;
    else process.env.OPENWIKI_PROVIDER_DIAGNOSTICS = previous;
  }
});
