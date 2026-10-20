import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { isRepositorySource } from '../plugins/wiki-links.mjs';

// OpenWiki 0.7.2 intercepts .md links, but leaves generated directory links
// relative to /wiki/graph/. Adapt only the exported copy, never managed pages.
const file = process.argv[2] ?? new URL('../dist/graph/graph.json', import.meta.url);
const graph = JSON.parse(await readFile(file, 'utf8'));
const nodes = new Map(graph.nodes.map((node) => [node.id, node]));
for (const node of graph.nodes) {
  const page = fileURLToPath(new URL(`../../openwiki/${node.id}.md`, import.meta.url));
  node.body = node.body.replace(/\[([^\]]*)\]\(([^)\s]+)\)/g, (match, label, href) =>
    isRepositorySource(href, page) ? label : match);
  node.body = node.body.replace(/\]\(([^)\s]+\/)\)/g, (match, href) => {
    if (/^(?:[a-z]+:|\/)/i.test(href)) return match;
    const target = path.posix.normalize(path.posix.join(path.posix.dirname(node.id), href, 'index'));
    const destination = nodes.get(target);
    if (!destination) throw new Error(`Missing graph directory index: ${target}`);
    if (!node.links.includes(target)) node.links.push(target);
    if (!destination.backlinks.includes(node.id)) destination.backlinks.push(node.id);
    if (!graph.edges.some((edge) => edge.source === node.id && edge.target === target)) {
      graph.edges.push({ source: node.id, target });
    }
    return `](${href}index.md)`;
  });
  node.size = node.body.length;
}
await writeFile(file, JSON.stringify(graph, null, 2) + '\n');
