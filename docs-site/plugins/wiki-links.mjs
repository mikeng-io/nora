import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../openwiki/', import.meta.url));

export function isRepositorySource(url, filePath) {
  return url && !/^(?:[a-z]+:|\/|#)/i.test(url) &&
    path.relative(root, path.resolve(path.dirname(filePath), url.split('#', 1)[0])).startsWith('..');
}

// Markdown links stay useful in Git; the public site uses directory routes.
export function wikiLinks() {
  return (tree, file) => {
    const isSource = (url) => isRepositorySource(url, file.path);
    const sourceDefinitions = new Set();
    function collect(node) {
      if (node.type === 'definition' && isSource(node.url)) sourceDefinitions.add(node.identifier);
      for (const child of node.children ?? []) collect(child);
    }
    collect(tree);
    function visit(node) {
      if ((node.type === 'link' || node.type === 'definition') && node.url &&
          !/^(?:[a-z]+:|\/|#)/i.test(node.url)) {
        const [target, anchor] = node.url.split('#', 2);
        if (target.endsWith('.md')) {
          const relative = path.relative(root, path.resolve(path.dirname(file.path), target));
          if (!relative.startsWith('..')) {
            if (/(?:^|\/)index\.md$/.test(relative)) {
              throw new Error('Link to a factual wiki page instead of a generated index: ' + node.url);
            }
            node.url = '/wiki/' + relative.replace(/index\.md$/, '').replace(/\.md$/, '/') +
              (anchor ? `#${anchor}` : '');
          }
        }
      }
      if (node.children) {
        // The repository stays private. Keep source names readable without
        // sending public readers to nonexistent source routes.
        node.children = node.children.flatMap(child => {
          if ((child.type === 'link' && isSource(child.url)) ||
              (child.type === 'linkReference' && sourceDefinitions.has(child.identifier))) {
            return child.children;
          }
          if (child.type === 'definition' && sourceDefinitions.has(child.identifier)) return [];
          visit(child);
          return [child];
        });
      }
    }
    visit(tree);
  };
}
