// Build-time Content-Security-Policy hashes for the static site and the wiki.
//
// 'unsafe-inline' in script-src lets ANY injected inline script run, which is what a CSP
// exists to stop. Both sites are static: every inline <script> is known at build time, so its
// SHA-256 can be listed in the header instead. This reads the built HTML, hashes each inline
// executable script, and writes a Caddyfile whose @@CSP_SCRIPT_HASHES@@ token is replaced by
// the list. Run it in the image build, after both sites are built:
//
//   node scripts/csp-hashes.mjs <Caddyfile> <out> <dist-dir> [<dist-dir> ...]
//
// It FAILS (exit 1) rather than guess when the build holds something a hash cannot cover: an
// inline event handler (onclick=...) or a javascript: URL. The OpenWiki graph export is skipped
// because it serves its own, deliberately wider policy (see the Caddyfile).
import { createHash } from 'node:crypto';
import { readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

export const TOKEN = '@@CSP_SCRIPT_HASHES@@';
const SKIP_DIRS = new Set(['graph', 'node_modules']);
// JSON-LD, import maps and speculation rules are data blocks, not executed scripts.
const DATA_TYPES = /type\s*=\s*["']?(?:application\/(?:ld\+)?json|importmap|speculationrules)/i;

export function htmlFiles(dir) {
  const found = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      if (!SKIP_DIRS.has(name)) found.push(...htmlFiles(path));
    } else if (name.endsWith('.html')) {
      found.push(path);
    }
  }
  return found;
}

export function inspect(html) {
  const hashes = new Set();
  for (const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
    const [, attrs, body] = match;
    if (/\bsrc\s*=/i.test(attrs) || DATA_TYPES.test(attrs)) continue;
    hashes.add(`'sha256-${createHash('sha256').update(body, 'utf8').digest('base64')}'`);
  }
  const hazards = [];
  if (/\son[a-z]+\s*=\s*["']/i.test(html.replace(/<script[\s\S]*?<\/script>/gi, ''))) {
    hazards.push('an inline event handler attribute');
  }
  if (/\b(?:href|src|action)\s*=\s*["']\s*javascript:/i.test(html)) {
    hazards.push('a javascript: URL');
  }
  return { hashes, hazards };
}

export function collect(dirs) {
  const all = new Set();
  const problems = [];
  for (const dir of dirs) {
    for (const file of htmlFiles(dir)) {
      const { hashes, hazards } = inspect(readFileSync(file, 'utf8'));
      hashes.forEach((h) => all.add(h));
      hazards.forEach((h) => problems.push(`${file}: ${h}`));
    }
  }
  return { hashes: [...all].sort(), problems };
}

export function render(caddyfile, hashes) {
  if (!caddyfile.includes(TOKEN)) throw new Error(`the Caddyfile has no ${TOKEN} to replace`);
  const out = caddyfile.split(TOKEN).join(hashes.join(' '));
  if (out.includes('@@')) throw new Error('an unreplaced @@ token is left in the Caddyfile');
  return out;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const [caddyfile, out, ...dirs] = process.argv.slice(2);
  if (!caddyfile || !out || dirs.length === 0) {
    console.error('usage: csp-hashes.mjs <Caddyfile> <out> <dist-dir> [<dist-dir> ...]');
    process.exit(2);
  }
  const { hashes, problems } = collect(dirs);
  if (problems.length) {
    console.error('Cannot cover these with a hash; remove them or widen the policy on purpose:');
    problems.forEach((p) => console.error('  ' + p));
    process.exit(1);
  }
  writeFileSync(out, render(readFileSync(caddyfile, 'utf8'), hashes));
  console.log(`${hashes.length} inline script hash(es) written to ${out}`);
}
