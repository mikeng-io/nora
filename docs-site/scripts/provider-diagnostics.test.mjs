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
      { message: { role: 'assistant', content: null, tool_calls: [{ id: 'probe', type: 'function',
          function: { name: 'submit_plan', arguments: '{' + hidden } }] }, finish_reason: 'tool_calls' },
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
      for (let count = 0; count < 4; count++) results.push(await model.invoke(hidden));
      if (results[1].tool_calls[0].name !== 'submit_plan' || results[2].content !== hidden
          || results[3].invalid_tool_calls.length !== 1)
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
    .map(line => {
      const { generations, duplicateCompletionId } = JSON.parse(line.slice('OpenWiki provider diagnostics: '.length));
      return { ...generations[0], duplicateCompletionId };
    });
  assert.equal(diagnostics.length, 4);
  assert.deepEqual(diagnostics.map(({ finishReason, hasContent, toolCalls }) =>
    [finishReason, hasContent, toolCalls]), [
      ['length', false, 0], ['tool_calls', false, 1], ['unknown', true, 0], ['tool_calls', false, 1],
    ]);
  assert.deepEqual(diagnostics.map(row => row.duplicateCompletionId), [false, true, true, true]);
  assert.deepEqual(diagnostics.map(row => row.argumentJsonErrors), [0, 0, 0, 1]);
  assert.deepEqual(diagnostics.map(row => row.duplicateToolCallIds), [0, 0, 0, 1]);
  assert.equal(result.stdout, '');
  assert.ok(!result.stderr.includes('private-diagnostic-sentinel'));
  assert.ok(!result.stderr.includes('submit_plan'));
});

test('tool diagnostics classify schema and execution failures without exposing error text', () => {
  const result = spawnSync(process.execPath, ['--input-type=module', '--eval', `
    import assert from 'node:assert/strict';
    import { DynamicStructuredTool } from './docs-site/node_modules/@langchain/core/dist/tools/index.js';
    import { z } from './docs-site/node_modules/zod/index.js';
    const hidden = 'private-tool-error-sentinel';
    const schema = z.object({ value: z.string() });
    const schemaTool = new DynamicStructuredTool({ name: 'read_file', description: 'probe', schema,
      func: async () => { throw new Error('Should not execute'); } });
    await assert.rejects(schemaTool.invoke({ value: 0 }), error => error.constructor.name === 'ToolInputParsingException');
    const original = Object.assign(new Error(hidden), { name: hidden });
    const failingTool = new DynamicStructuredTool({ name: hidden, description: 'probe', schema,
      func: async () => { throw original; } });
    await assert.rejects(failingTool.invoke({ value: hidden }), error => error === original);
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
    .filter(line => line.startsWith('OpenWiki tool diagnostics: {'))
    .map(line => JSON.parse(line.slice('OpenWiki tool diagnostics: '.length)));
  assert.deepEqual(diagnostics, [
    { tool: 'read_file', kind: 'ToolInputParsingException' },
    { tool: 'other', kind: 'other' },
  ]);
  assert.equal(result.stdout, '');
  assert.ok(!result.stderr.includes('private-tool-error-sentinel'));
});

for (const scenario of ['throws', 'changes']) {
  test(`an error name getter that ${scenario} cannot leak text or replace the error`, () => {
    const result = spawnSync(process.execPath, ['--input-type=module', '--eval', `
      import assert from 'node:assert/strict';
      import { DynamicStructuredTool } from './docs-site/node_modules/@langchain/core/dist/tools/index.js';
      import { z } from './docs-site/node_modules/zod/index.js';
      const scenario = ${JSON.stringify(scenario)};
      const hidden = 'private-name-getter-sentinel';
      let reads = 0;
      const original = Object.defineProperty({}, 'name', { get() {
        reads++;
        if (scenario === 'throws') throw new Error(hidden);
        return reads === 1 ? 'Error' : hidden;
      } });
      const tool = new DynamicStructuredTool({ name: 'read_file', description: 'probe',
        schema: z.object({ value: z.string() }), func: async () => { throw original; } });
      await assert.rejects(tool.invoke({ value: hidden }), error => error === original);
      if (scenario === 'changes') assert.equal(reads, 1);
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
      .filter(line => line.startsWith('OpenWiki tool diagnostics: {'))
      .map(line => JSON.parse(line.slice('OpenWiki tool diagnostics: '.length)));
    assert.deepEqual(diagnostics, scenario === 'throws' ? [] : [{ tool: 'read_file', kind: 'Error' }]);
    assert.ok(!result.stderr.includes('private-name-getter-sentinel'));
  });
}
