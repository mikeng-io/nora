import path from 'node:path';
import { readdir, readFile, stat } from 'node:fs/promises';

// Check rendered output, including Starlight navigation and copied graph assets.
const root = new URL('../dist/', import.meta.url);
let checked = 0;
for (const name of await readdir(root, { recursive: true })) {
  if (!name.endsWith('.html')) continue;
  const html = await readFile(new URL(name, root), 'utf8');
  for (const [, raw] of html.matchAll(/(?:href|src)="([^"]+)"/g)) {
    const url = new URL(raw.replaceAll('&amp;', '&'), 'https://wiki.invalid/wiki/' + name);
    if (url.origin !== 'https://wiki.invalid' || !url.pathname.startsWith('/wiki/')) continue;
    const relative = decodeURIComponent(url.pathname.slice('/wiki/'.length));
    const target = new URL(relative || './', root);
    const found = await stat(target).catch(() => null);
    if (!found || (found.isDirectory() && !(await stat(new URL('index.html', target.href.endsWith('/') ? target : target.href + '/')).catch(() => null)))) {
      throw new Error(`${name}: broken static link ${raw}`);
    }
    checked++;
  }
}
console.log(`Verified ${checked} local wiki links and assets.`);

// Graph Markdown is rendered in the browser, outside Astro's HTML link pass.
const graph = JSON.parse(await readFile(new URL('graph/graph.json', root), 'utf8'));
const ids = new Set(graph.nodes.map((node) => node.id));
for (const node of graph.nodes) {
  for (const [, href] of node.body.matchAll(/\]\(([^)\s]+)\)/g)) {
    if (/^(?:[a-z]+:|\/|#)/i.test(href)) continue;
    const clean = href.split('#')[0];
    if (clean.endsWith('/')) throw new Error(`${node.id}: unresolved graph directory link ${href}`);
    if (!clean.endsWith('.md')) continue;
    const target = path.posix.normalize(path.posix.join(path.posix.dirname(node.id), clean)).replace(/\.md$/, '');
    if (!ids.has(target)) throw new Error(`${node.id}: unresolved graph page ${href}`);
  }
}
console.log('Verified graph reader navigation targets.');
