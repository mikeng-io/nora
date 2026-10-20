import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../openwiki/', import.meta.url));

// Markdown links stay useful in Git; the public site uses directory routes.
export function wikiLinks() {
  return (tree, file) => {
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
      for (const child of node.children ?? []) visit(child);
    }
    visit(tree);
  };
}
