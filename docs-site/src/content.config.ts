import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { docsSchema } from '@astrojs/starlight/schema';

export const collections = {
  docs: defineCollection({
    loader: glob({
      base: '../openwiki',
      pattern: ['**/*.md', '!**/index.md', '!INSTRUCTIONS.md', '!log.md'],
    }),
    schema: docsSchema(),
  }),
};
