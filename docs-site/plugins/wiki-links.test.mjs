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
  for (const target of ['/wiki/architecture/memory/#retrieval', '/wiki/architecture/ambient-cognition/', 'https://example.com/page.md', '../../README.md']) {
    assert.ok(result.code.includes(`href="${target}"`), target);
  }
});

test('generated indexes cannot produce broken public links', async () => {
  const renderer = await createMarkdownProcessor({ remarkPlugins: [wikiLinks] });
  await assert.rejects(renderer.render('[index](../architecture/index.md)', {
    fileURL: new URL('../../openwiki/concepts/continuity.md', import.meta.url),
  }), /Link to a factual wiki page/);
});
