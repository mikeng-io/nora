import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

test('native OpenWiki completes despite repeated Ollama completion IDs', () => {
  const result = spawnSync(process.execPath, ['--input-type=module', '--eval', `
    import assert from 'node:assert/strict';
    import { createServer } from 'node:http';
    import { gzipSync } from 'node:zlib';
    import { mkdtempSync, writeFileSync, readFileSync, existsSync, rmSync } from 'node:fs';
    import { tmpdir } from 'node:os';
    import { join } from 'node:path';
    import { execFileSync } from 'node:child_process';
    import { createModel } from './docs-site/node_modules/openwiki/dist/agent/index.js';
    import { runNativeRepositoryGeneration } from './docs-site/node_modules/openwiki/dist/agent/repository-runner.js';
    const root = mkdtempSync(join(tmpdir(), 'openwiki-identity-test-'));
    execFileSync('git', ['init', '-q', root]);
    writeFileSync(join(root, 'README.md'), '# Synthetic component\\nA minimal documentation fixture.\\n');
    execFileSync('git', ['-C', root, 'add', 'README.md']);
    execFileSync('git', ['-C', root, '-c', 'user.name=Probe', '-c', 'user.email=probe@example.invalid',
      'commit', '-qm', 'docs: add synthetic fixture']);
    const page = '---\\ntype: guide\\ntitle: Synthetic component\\ndescription: A synthetic documentation fixture.\\ntags: [probe]\\n---\\n\\nThis is a minimal documentation fixture.\\n';
    const actions = [
      ['read_file', { file_path: '/README.md' }],
      ['read_file', { file_path: '/README.md' }],
      ['submit_plan', { pages: [{ path: '/openwiki/quickstart.md', title: 'Synthetic component',
        purpose: 'Explain the synthetic component.', seedPaths: ['README.md'] }] }],
      null,
      ['write_file', { file_path: '/openwiki/quickstart.md', content: page }],
      ['submit_page', { claims: [{ statement: 'README describes a minimal documentation fixture.',
        evidence: [{ resource: 'repo://README.md#L1-L2' }] }] }],
      null,
    ];
    let requests = 0;
    const server = createServer((request, response) => {
      const chunks = [];
      request.on('data', chunk => chunks.push(chunk));
      request.on('end', () => {
        let body;
        try { body = JSON.parse(Buffer.concat(chunks).toString()); } catch { body = null; }
        const action = actions[requests];
        if (request.method !== 'POST' || request.url !== '/v1/chat/completions'
            || request.headers.authorization !== 'Bearer synthetic-key'
            || body?.model !== 'probe' || !Array.isArray(body.messages) || body.messages.length === 0
            || (action && !body.tools?.some(tool => tool.function?.name === action[0]))) {
          response.statusCode = 400;
          response.setHeader('content-type', 'application/json');
          response.end(JSON.stringify({ error: { message: 'Invalid synthetic API request' } }));
          return;
        }
        requests++;
        const message = action ? { role: 'assistant', content: null, tool_calls: [{
          id: 'synthetic-call-' + requests, type: 'function',
          function: { name: action[0], arguments: JSON.stringify(action[1]) },
        }] } : { role: 'assistant', content: 'Submitted.' };
        response.setHeader('content-type', 'application/json');
        response.setHeader('content-encoding', 'gzip');
        response.setHeader('x-request-id', 'synthetic-trace');
        response.end(gzipSync(JSON.stringify({ id: 'reused-provider-completion',
          object: 'chat.completion', created: 0, model: 'probe',
          choices: [{ index: 0, message, finish_reason: action ? 'tool_calls' : 'stop' }] })));
      });
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const fetch = globalThis.fetch;
    // Route the real provider URL to this synthetic HTTP server before loading
    // the adapter. The test cannot contact a model provider.
    globalThis.fetch = (input, init) => {
      const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
      assert.ok(['ollama.com', 'other.example.invalid'].includes(url.hostname));
      return fetch('http://127.0.0.1:' + server.address().port + url.pathname, init);
    };
    await import('./docs-site/scripts/provider-compat.mjs');
    process.env.OPENAI_COMPATIBLE_BASE_URL = 'https://ollama.com/v1';
    process.env.OPENAI_COMPATIBLE_API_KEY = 'synthetic-key';
    try {
      await runNativeRepositoryGeneration({ root, mode: 'init', force: true, language: 'en',
        modelId: 'probe', model: createModel('openai-compatible', 'probe', 0, 2048), pageConcurrency: 1 });
      const metadata = JSON.parse(readFileSync(join(root, 'openwiki/.last-update.json'), 'utf8'));
      assert.equal(metadata.status, 'complete');
      assert.equal(existsSync(join(root, 'openwiki/.run.json')), false);
      assert.match(readFileSync(join(root, 'openwiki/quickstart.md'), 'utf8'), /minimal documentation fixture/);
      assert.ok(requests >= 7);
      const probe = { method: 'POST', headers: { 'content-type': 'application/json',
        authorization: 'Bearer synthetic-key' }, body: JSON.stringify({ model: 'probe',
        messages: [{ role: 'user', content: 'Complete the synthetic probe.' }] }) };
      const response = await globalThis.fetch('https://ollama.com/v1/chat/completions', probe);
      assert.equal(response.headers.get('x-request-id'), 'synthetic-trace');
      assert.equal(response.headers.get('content-encoding'), null);
      assert.equal(response.headers.get('content-length'), null);
      assert.notEqual((await response.json()).id, 'reused-provider-completion');
      const other = await globalThis.fetch('https://other.example.invalid/v1/chat/completions', probe);
      assert.equal((await other.json()).id, 'reused-provider-completion');
    } finally {
      await new Promise(resolve => server.close(resolve));
      rmSync(root, { recursive: true, force: true });
    }
  `], {
    cwd: new URL('../../', import.meta.url),
    env: { ...process.env, OPENWIKI_PROVIDER_DIAGNOSTICS: 'false', LANGCHAIN_TRACING_V2: 'false' },
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    timeout: 30000,
  });
  assert.equal(result.status, 0, result.stderr);
});
