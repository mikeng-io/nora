import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

test('real model requests expose response structure while suppressing content and unknown metadata', () => {
  const result = spawnSync(process.execPath, ['--input-type=module', '--eval', `
    import { createServer } from 'node:http';
    import { ChatOpenAI } from './docs-site/node_modules/@langchain/openai/dist/index.js';
    const hidden = 'private-diagnostic-sentinel';
    const replies = [
      { message: { role: 'assistant', content: null, reasoning_content: hidden }, finish_reason: 'length' },
      { message: { role: 'assistant', content: null, tool_calls: [{ id: 'probe', type: 'function',
          function: { name: 'submit_plan', arguments: JSON.stringify({ value: hidden }) } }] }, finish_reason: 'tool_calls' },
      { message: { role: 'assistant', content: hidden }, finish_reason: hidden },
    ];
    const server = createServer((request, response) => {
      request.resume();
      request.on('end', () => {
        response.setHeader('content-type', 'application/json');
        response.end(JSON.stringify({ id: 'probe', object: 'chat.completion', created: 0, model: 'probe',
          choices: [{ index: 0, ...replies.shift() }] }));
      });
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    try {
      const model = new ChatOpenAI({ model: 'probe', apiKey: hidden, maxRetries: 0,
        configuration: { baseURL: 'http://127.0.0.1:' + server.address().port + '/v1' } });
      const results = [];
      for (let count = 0; count < 3; count++) results.push(await model.invoke(hidden));
      if (results[1].tool_calls[0].name !== 'submit_plan' || results[2].content !== hidden)
        throw new Error('Diagnostics changed the provider response');
    } finally { await new Promise(resolve => server.close(resolve)); }
  `], {
    cwd: new URL('../../', import.meta.url),
    env: { ...process.env,
      NODE_OPTIONS: '--import=./docs-site/scripts/provider-diagnostics.mjs',
      OPENWIKI_PROVIDER_DIAGNOSTICS: 'true' },
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  assert.equal(result.status, 0, result.stderr);
  const diagnostics = result.stderr.split('\n')
    .filter(line => line.startsWith('OpenWiki provider diagnostics: {'))
    .map(line => JSON.parse(line.slice('OpenWiki provider diagnostics: '.length)).generations[0]);
  assert.equal(diagnostics.length, 3);
  assert.deepEqual(diagnostics.map(({ finishReason, hasContent, toolCalls }) =>
    [finishReason, hasContent, toolCalls]), [
      ['length', false, 0], ['tool_calls', false, 1], ['unknown', true, 0],
    ]);
  assert.equal(result.stdout, '');
  assert.ok(!result.stderr.includes('private-diagnostic-sentinel'));
  assert.ok(!result.stderr.includes('submit_plan'));
});
