import { defineConfig } from 'astro/config';
import { unified } from '@astrojs/markdown-remark';
import starlight from '@astrojs/starlight';
import { wikiLinks } from './plugins/wiki-links.mjs';

export default defineConfig({
  site: process.env.SITE_ORIGIN ?? 'https://nora.67.zone',
  base: '/wiki',
  trailingSlash: 'always',
  markdown: { processor: unified({ remarkPlugins: [wikiLinks] }) },
  integrations: [starlight({
    title: 'Nora · Wiki',
    favicon: new URL('/favicon.ico', process.env.SITE_ORIGIN ?? 'https://nora.67.zone').href,
    description: 'How Nora carries context, thinks between interactions and participates over time.',
    customCss: ['./src/styles/nora.css'],
    sidebar: [
      { label: 'Start here', slug: 'quickstart' },
      { label: 'Concepts', items: [{ slug: 'concepts/continuity' }] },
      { label: 'Architecture', items: [{ slug: 'architecture/ambient-cognition' }, { slug: 'architecture/memory' }] },
      { label: 'Working with Nora', items: [{ slug: 'workflows/computer' }] },
      { label: 'Operating and contributing', items: [{ slug: 'operations/configuration' }, { slug: 'operations/contributing' }] },
      { label: 'Explore the graph', link: '/graph/' },
      { label: 'About Nora', link: new URL('/', process.env.SITE_ORIGIN ?? 'https://nora.67.zone').href },
    ],
  })],
});
