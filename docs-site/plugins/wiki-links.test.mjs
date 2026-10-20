import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createMarkdownProcessor } from '@astrojs/markdown-remark';
import { wikiLinks } from './wiki-links.mjs';

test('repository Markdown links become public directory routes', async () => {
  const renderer = await createMarkdownProcessor({ remarkPlugins: [wikiLinks] });
  const result = await renderer.render([
    '[memory](../architecture/memory.md#retrieval)',
    '[reference][cognition]',
    '[external](https://example.com/page.md)',
    '[source](../../README.md)',
    '', '[cognition]: ../architecture/ambient-cognition.md',
  ].join('\n'), { fileURL: new URL('../../openwiki/concepts/continuity.md', import.meta.url) });
  for (const target of ['/wiki/architecture/memory/#retrieval', '/wiki/architecture/ambient-cognition/', 'https://example.com/page.md']) {
    assert.ok(result.code.includes(`href="${target}"`), target);
  }
  assert.ok(!result.code.includes('href="../../README.md"'));
  assert.ok(result.code.includes('source'));
});

test('private repository references retain their labels without broken public links', async () => {
  const renderer = await createMarkdownProcessor({ remarkPlugins: [wikiLinks] });
  const result = await renderer.render([
    '[`pipeline.py`](../../nora/ingest/pipeline.py#L303-L406)',
    '[config][source]',
    '[wiki][page]',
    '', '[source]: ../../config.example.toml',
    '[page]: ../concepts/memory-and-record.md',
  ].join('\n'), { fileURL: new URL('../../openwiki/workflows/ingestion.md', import.meta.url) });
  assert.ok(result.code.includes('<code>pipeline.py</code>'));
  assert.ok(result.code.includes('config'));
  assert.ok(!result.code.includes('../../nora/'));
  assert.ok(!result.code.includes('../../config.example.toml'));
  assert.ok(result.code.includes('href="/wiki/concepts/memory-and-record/"'));
});

test('generated indexes cannot produce broken public links', async () => {
  const renderer = await createMarkdownProcessor({ remarkPlugins: [wikiLinks] });
  await assert.rejects(renderer.render('[index](../architecture/index.md)', {
    fileURL: new URL('../../openwiki/concepts/continuity.md', import.meta.url),
  }), /Link to a factual wiki page/);
});
