#!/usr/bin/env node
// mock-nora.mjs — a development-only stand-in for Nora's read-only web API
// (nora/web/*.py, registered by nora/web/api.py onto GET /app/v1/*), so the
// portal (this repo's Next.js app) can be built and screenshot-tested
// without a live Nora + Pulse + Life360 + Honcho stack behind it.
//
// Zero npm dependencies on purpose: only node:http, node:fs, node:path and
// node:url. `identity.dev.toml` is read with a tiny hand-rolled parser
// (below) rather than the real `smol-toml` package the portal itself uses
// (src/server/allowlist.ts) — this file is not allowed to reach into
// node_modules, so it re-derives just the subset of TOML this one file
// needs: tables, array-of-tables, quoted strings, comments.
//
// Every value in here is INVENTED. Nothing was copied from a real
// deployment, a real person, or real Pulse/Life360 data — see each
// section's own comment for how its numbers were chosen.
//
// -------------------------------------------------------------------------
// Contract with the rest of this file, and with dev/mock-nora.test.ts:
//
//   export function handle(pathname, searchParams, now) -> { status, body }
//
// `handle` is pure and synchronous: it never touches the filesystem or the
// network itself (everything it reads was loaded once, at module import
// time, below), and every time-relative value it produces is computed from
// the `now` (epoch ms) it is given rather than from `Date.now()` directly —
// so a test can hand it a fixed instant and get a fully reproducible
// response. The HTTP server at the bottom of this file is a thin wrapper:
// it parses the request URL, calls `handle`, adds the artificial latency,
// and writes the JSON response.
//
// Env knobs (all optional):
//   MOCK_PORT      — TCP port to listen on (default 9199).
//   MOCK_LATENCY_MS — mean artificial response latency in ms (default 250),
//                     ±40% jitter so loading states are visible and vary.
//   MOCK_FAIL      — comma list of route families to answer 503 on:
//                     status, memory, doctrine, skills, tools, persons,
//                     games, presence, events, documents.
//   MOCK_EMPTY     — comma list of the same families to answer with
//                     genuinely empty data (empty arrays, zero counts)
//                     instead of their normal generated content.

import { createServer } from "node:http";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// dev/ -> app/ -> lamprey/ (the monorepo root that also holds nora/).
const REPO_ROOT = path.resolve(__dirname, "..", "..");

// ==========================================================================
// 0. Config
// ==========================================================================

const PORT = Number(process.env.MOCK_PORT ?? 9199);
const LATENCY_MS = Number(process.env.MOCK_LATENCY_MS ?? 250);

function familySet(envVar) {
  return new Set(
    (process.env[envVar] ?? "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  );
}
// Read once per process at import time — the harness sets the env var
// before starting the server; changing it requires a restart, same as any
// other config in this file. `handle()` still reads through these sets on
// every call rather than baking a decision in, so a test can mutate
// `process.env.MOCK_FAIL`/`MOCK_EMPTY` between calls (see the test file).
function isFailing(family) {
  return familySet("MOCK_FAIL").has(family);
}
function isEmptied(family) {
  return familySet("MOCK_EMPTY").has(family);
}

const ERR_NOT_FOUND = { error: "not found" };
const ERR_UNAVAILABLE = { error: "unavailable" };

// ==========================================================================
// 1. Deterministic PRNG — same data every process start, so a screenshot
//    diff is never noise from this file. Only the artificial network
//    latency (bottom of file) uses Math.random(); everything that becomes
//    response DATA is seeded.
// ==========================================================================

function hashSeed(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/** mulberry32, seeded from a name so every call site gets its own
 * independent-looking but fully reproducible stream. */
function makeRng(seedStr) {
  let a = hashSeed(seedStr) || 1;
  return function rng() {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** A named PRNG stream plus the handful of shapes this file draws from it. */
function rngStream(seedStr) {
  const rng = makeRng(seedStr);
  return {
    float: (min = 0, max = 1) => min + rng() * (max - min),
    int: (min, max) => Math.floor(min + rng() * (max - min + 1)),
    pick: (arr) => arr[Math.floor(rng() * arr.length)],
    bool: (p = 0.5) => rng() < p,
  };
}

/** A short, stable, UUID-v4-shaped string from a seed — good enough for a
 * mock id field; never used as a real UUID (no version/variant guarantees
 * beyond looking like one). */
function fakeUuid(seedStr) {
  const rng = makeRng(seedStr);
  const hex = () => Math.floor(rng() * 16).toString(16);
  const group = (n) => Array.from({ length: n }, hex).join("");
  return `${group(8)}-${group(4)}-4${group(3)}-8${group(3)}-${group(12)}`;
}

// ==========================================================================
// 2. Time helpers — Hong Kong observes no DST, so "+08:00" is always
//    correct (matches nora.agent.tools's own `_HKT` fixed-offset comment).
// ==========================================================================

const HOUR_MS = 3_600_000;
const DAY_MS = 24 * HOUR_MS;
const HK_OFFSET_MS = 8 * HOUR_MS;

function hkIso(ms) {
  const shifted = new Date(Math.round(ms) + HK_OFFSET_MS);
  const pad = (n) => String(n).padStart(2, "0");
  const Y = shifted.getUTCFullYear();
  const Mo = pad(shifted.getUTCMonth() + 1);
  const D = pad(shifted.getUTCDate());
  const h = pad(shifted.getUTCHours());
  const m = pad(shifted.getUTCMinutes());
  const s = pad(shifted.getUTCSeconds());
  return `${Y}-${Mo}-${D}T${h}:${m}:${s}+08:00`;
}

function hkDate(ms) {
  return hkIso(ms).slice(0, 10);
}

/** The next occurrence of `hour` (HKT, 24h) that is at least `minDays` away
 * on `weekday` (0=Sunday .. 6=Saturday), as an epoch-ms instant. Used for
 * "next Sunday 19:00", "next Saturday", etc. */
function nextHkWeekdayAt(now, weekday, hour, minute = 0) {
  const shifted = new Date(now + HK_OFFSET_MS);
  const todayWeekday = shifted.getUTCDay();
  let addDays = (weekday - todayWeekday + 7) % 7;
  if (addDays === 0) addDays = 7; // "next Sunday" always means a future one
  const base = new Date(now + HK_OFFSET_MS);
  base.setUTCHours(hour, minute, 0, 0);
  base.setUTCDate(base.getUTCDate() + addDays);
  return base.getTime() - HK_OFFSET_MS;
}

function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, n));
}

// ==========================================================================
// 3. Tiny frontmatter + TOML readers (no npm packages — see file header).
// ==========================================================================

/** Splits a `---\n...\n---\n<body>` file and parses the frontmatter as a
 * small, known subset of YAML: single-line `key: value` scalars and folded
 * block scalars (`key: >-` followed by indented lines, joined with
 * spaces) — exactly what nora/doctrine's and nora/skills' real frontmatter
 * uses. Not a general YAML parser; it only has to agree with those files. */
function readFrontmatterFile(absPath) {
  const raw = readFileSync(absPath, "utf8");
  const m = raw.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/);
  if (!m) return null;
  const [, fmText, bodyText] = m;
  return { meta: parseFrontmatter(fmText), body: bodyText.trim() };
}

function parseFrontmatter(text) {
  const meta = {};
  let key = null;
  let buf = [];
  const flush = () => {
    if (key) meta[key] = buf.join(" ").trim();
    key = null;
    buf = [];
  };
  for (const line of text.split(/\r?\n/)) {
    const top = /^\S/.test(line) ? line.match(/^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$/) : null;
    if (top) {
      flush();
      const [, k, rest] = top;
      const trimmed = rest.trim();
      if (trimmed === "" || /^[>|][-+]?$/.test(trimmed)) {
        key = k; // folded/literal block scalar starts on the following lines
      } else {
        meta[k] = stripQuotes(trimmed);
      }
    } else if (key) {
      buf.push(line.trim());
    }
  }
  flush();
  return meta;
}

function stripQuotes(s) {
  if ((s.startsWith('"') && s.endsWith('"')) || (s.startsWith("'") && s.endsWith("'"))) {
    return s.slice(1, -1);
  }
  return s;
}

/** Enough TOML to read identity.dev.toml: `[table.path]`,
 * `[[array.of.tables]]`, `# comments`, and `key = "quoted string"`. The
 * file itself must stay valid, full TOML — src/server/allowlist.ts parses
 * the very same file with the real `smol-toml` package. */
function parseMiniToml(text) {
  const root = {};
  let current = root;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const arrayHeader = line.match(/^\[\[([\w.]+)\]\]$/);
    const tableHeader = line.match(/^\[([\w.]+)\]$/);
    const kv = line.match(/^([\w-]+)\s*=\s*"((?:[^"\\]|\\.)*)"$/);
    if (arrayHeader) {
      current = pushArrayTable(root, arrayHeader[1].split("."));
    } else if (tableHeader) {
      current = ensureTable(root, tableHeader[1].split("."));
    } else if (kv) {
      current[kv[1]] = kv[2];
    }
  }
  return root;
}

function ensureTable(root, keyPath) {
  let node = root;
  for (const key of keyPath) {
    if (!(key in node)) node[key] = {};
    node = node[key];
  }
  return node;
}

function pushArrayTable(root, keyPath) {
  let node = root;
  for (const key of keyPath.slice(0, -1)) {
    if (!(key in node)) node[key] = {};
    node = node[key];
  }
  const last = keyPath[keyPath.length - 1];
  if (!Array.isArray(node[last])) node[last] = [];
  const entry = {};
  node[last].push(entry);
  return entry;
}

// ==========================================================================
// 4. Identity roster — read from identity.dev.toml, this file's one
//    external data source (deliberately: one place to edit the cast).
// ==========================================================================

const identityToml = parseMiniToml(readFileSync(path.join(__dirname, "identity.dev.toml"), "utf8"));

const ROSTER = Object.entries(identityToml.persons ?? {}).map(([personId, p]) => ({
  personId,
  displayName: p.name ?? personId,
  discordUserId: p.discord?.[0]?.user_id ?? null,
}));
const ROSTER_BY_ID = new Map(ROSTER.map((p) => [p.personId, p]));
const ROSTER_SORTED = [...ROSTER].sort((a, b) => a.displayName.localeCompare(b.displayName));

// The presence circle (nora/web/presence.py's `Circle.people`): everyone
// except Sam, who has no Discord account and is deliberately outside it —
// the roster's "known person, but not a circle member" case.
const CIRCLE_IDS = ["miles", "ken", "mei", "jo", "ada"];

// miles: abiotic + bigwalk; ken: abiotic; jo: abiotic + bigwalk; everyone
// else (mei, ada, sam) plays neither — task's own spec for /persons/{id}.
const PERSON_GAME_PROFILES = {
  miles: [
    { serverId: "abiotic", displayName: "Abiotic Factor", game: null },
    { serverId: "bigwalk", displayName: "The Big Walk", game: "big-walk" },
  ],
  ken: [{ serverId: "abiotic", displayName: "Abiotic Factor", game: null }],
  jo: [
    { serverId: "abiotic", displayName: "Abiotic Factor", game: null },
    { serverId: "bigwalk", displayName: "The Big Walk", game: "big-walk" },
  ],
};

// ==========================================================================
// 5. Doctrine — read from the repo's real nora/doctrine/*.md at startup
//    (module import = "server start"); a short invented body when a file
//    can't be found. Section order is fixed by this list, not by each
//    file's own `order:` field — see the module docstring in nora/doctrine
//    /loader.py for how the real deployment actually orders/filters these;
//    this mock exists to exercise the portal's doctrine page, not to
//    reproduce that gate.
// ==========================================================================

function loadDoctrineSection(relPath, fallback) {
  const abs = path.join(REPO_ROOT, "nora", "doctrine", relPath);
  try {
    if (existsSync(abs)) {
      const parsed = readFrontmatterFile(abs);
      if (parsed && parsed.meta.name && parsed.meta.description && parsed.meta.version) {
        return {
          name: parsed.meta.name,
          description: parsed.meta.description,
          version: Number(parsed.meta.version),
          body: parsed.body,
        };
      }
    }
  } catch {
    // Fall through to the invented fallback — a doctrine file that cannot
    // be read must never take the mock down (matches R-27's "skip it"
    // posture the real loaders hold for a malformed file).
  }
  return fallback;
}

const DOCTRINE = [
  loadDoctrineSection("core/identity.md", {
    name: "identity",
    description: "Who Nora is and where she comes from — dev fallback body.",
    version: 1,
    body: "# Who you are\n\nYou are Nora, a household AI assistant. This is a placeholder body: the real nora/doctrine/core/identity.md could not be read.",
  }),
  loadDoctrineSection("rooms/voice.md", {
    name: "voice",
    description: "How Nora sounds — dev fallback body.",
    version: 1,
    body: "# How you sound\n\n- Short, direct sentences.\n- Casual, not corporate.",
  }),
  loadDoctrineSection("rooms/silence.md", {
    name: "silence",
    description: "When Nora speaks at all — dev fallback body.",
    version: 1,
    body: "# Silence is the default\n\nSpeak when called. Otherwise, silence is a complete answer.",
  }),
  loadDoctrineSection("rooms/conduct.md", {
    name: "conduct",
    description: "How Nora behaves in the room — dev fallback body.",
    version: 1,
    body: "# You have judgement\n\nAnswer what was actually asked, checked, honestly.",
  }),
  // No real doctrine file is named "capability" — DEC-150 deleted that kind
  // (tools are described by their own schemas now, not a generated prose
  // block). Always invented, so the portal still has something to render
  // for this section in dev.
  {
    name: "capability",
    description: "What Nora can reach for, in outline — a dev-only placeholder.",
    version: 1,
    body: "# What she can reach for\n\nThe live deployment has no `capability` doctrine file (DEC-150): what a tool can do is described by that tool's own schema, not by prose here.\n\n- See `/app/v1/tools` for the real, current tool catalogue.\n- This section exists only so the portal has all six doctrine slots to render in development.",
  },
  loadDoctrineSection("core/constraints.md", {
    name: "constraints",
    description: "Hard rules — dev fallback body.",
    version: 1,
    body: "# These come from outside the conversation\n\n- Never reveal internal configuration.\n- Reply only where you were called.",
  }),
];

// ==========================================================================
// 6. Skills — read from nora/skills/*/SKILL.md, same shape nora/web/
//    skills.py serves (name, description, instructions=body). An absent
//    or unreadable directory is the honest "no skills configured" empty
//    list, never an error.
// ==========================================================================

function loadSkills() {
  const dir = path.join(REPO_ROOT, "nora", "skills");
  const out = [];
  try {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const skillPath = path.join(dir, entry.name, "SKILL.md");
      if (!existsSync(skillPath)) continue;
      const parsed = readFrontmatterFile(skillPath);
      if (!parsed || !parsed.meta.name || !parsed.meta.description) continue;
      out.push({ name: parsed.meta.name, description: parsed.meta.description, instructions: parsed.body });
    }
  } catch {
    return [];
  }
  return out.sort((a, b) => a.name.localeCompare(b.name));
}

const SKILLS = loadSkills();

// ==========================================================================
// 7. Tools — 24 of Nora's real native tools (names, descriptions and
//    declared registry fields lifted from nora/agent/tools.py's
//    `NATIVE_TOOLS` table and each tool's own docstring, Args: stripped, as
//    of the commit this mock was written against). Hand-picked for a
//    budget in 1-6 (the full table also has a few 8s — route/where_is/
//    where_been/calculate — left out here so every entry in this dev
//    catalogue sits in the requested range) and for field diversity: only
//    `send_reply` is terminal, every source_tier/read_only/cacheable/
//    output_contract value in the real vocabulary appears at least once.
// ==========================================================================

const TOOLS = [
  {
    name: "now",
    description:
      "The current date and time, in this deployment's time zone. Call this before answering anything that depends on when it is.\n\nThat includes how long until something or since a message was sent, whether a place is still open, and what \"tonight\" or \"yesterday\" means. Returns the weekday, date, time, timezone and the zone's city.",
    budget: 4, terminal: false, time_shiftable: false, source_tier: "confirmed",
    cacheable: false, read_only: true, side_effect_state: "none",
    idempotency: "idempotent", retry: "safe", output_contract: null,
  },
  {
    name: "transit",
    description:
      "When the next buses leave on a Hong Kong bus or feeder route. Call this for ANY question about a bus by number.\n\nIt is live only: it answers about right now, never about a bus later today. Never state a bus time that did not come from here.\n\nReturns `status`: ok, nothing_found or unavailable (plus `near.status` when you passed `near`), with a `note` on how to report it.",
    budget: 4, terminal: false, time_shiftable: false, source_tier: "confirmed",
    cacheable: true, read_only: true, side_effect_state: "none",
    idempotency: "idempotent", retry: "safe", output_contract: "three_state",
  },
  {
    name: "place_info",
    description:
      "How good, how pricey, and whether-open a place is, from Google. Call this for anything Google Maps knows.\n\nFor a rating, use this: it returns Google's own number.\n\nReturns `status`: ok, ambiguous, not_found, unavailable or refused, with a `note` on how to report it.",
    budget: 4, terminal: false, time_shiftable: null, source_tier: "confirmed",
    cacheable: true, read_only: true, side_effect_state: "none",
    idempotency: "idempotent", retry: "safe", output_contract: "three_state",
  },
  {
    name: "saved_places",
    description:
      "The names this household has saved for places, for THIS conversation. Call this the moment someone routes to, or asks WHERE, a place by a PERSONAL name.\n\nA personal name is one Google doesn't know: home, someone's home, work, school. Asked where someone works or lives, use this, not `about` or `search_memory`.\n\nReturns `status`: ok or unavailable, with a `note` on how to report it.",
    budget: 1, terminal: false, time_shiftable: null, source_tier: "remembered",
    cacheable: false, read_only: true, side_effect_state: "none",
    idempotency: "idempotent", retry: "safe", output_contract: "three_state",
  },
  {
    name: "online_now",
    description:
      "Live digital activity for the people in this room. Call this when a question or a decision turns on whether someone is actually present or playing right now.\n\n`what`: online (Steam and Discord presence), playing (which game) or voice (which Discord voice channel).\n\nReturns `status`: ok, nothing_found or unavailable, with a `note` on how to report it.",
    budget: 4, terminal: false, time_shiftable: false, source_tier: "confirmed",
    cacheable: false, read_only: true, side_effect_state: "none",
    idempotency: "idempotent", retry: "safe", output_contract: "three_state",
  },
  {
    name: "who_said",
    description:
      "Check who said something, in the messages you can see right now. Call this BEFORE you put words in somebody's mouth.\n\nIt searches only what you were already shown, where the line usually is. For older lines, `load_transcript` reads further back in this conversation and `search_memory` further still. Give it the words as written, not a paraphrase.\n\nReturns `status`: ok (`said_by`, oldest first), nothing_found or unavailable, with a `note` on how to report the last two.",
    budget: 4, terminal: false, time_shiftable: null, source_tier: "remembered",
    cacheable: false, read_only: true, side_effect_state: "none",
    idempotency: "idempotent", retry: "safe", output_contract: "three_state",
  },
  {
    name: "load_transcript",
    description:
      "Read older messages from this conversation. Only call this if you need to look further back than what you were shown.\n\nYou already have the most recent messages. Returns `messages`, oldest first, each with `sender`, `body` and `when` it was sent.",
    budget: 4, terminal: false, time_shiftable: null, source_tier: "remembered",
    cacheable: false, read_only: true, side_effect_state: "none",
    idempotency: "idempotent", retry: "safe", output_contract: "messages",
  },
  {
    name: "room_list",
    description:
      "List this group's rooms on each network, with each one's connection and activity. Call this when someone asks which groups you are in or whether a network is working.\n\nPass a room's `room` value to room_read. Other groups are never listed.",
    budget: 2, terminal: false, time_shiftable: false, source_tier: "confirmed",
    cacheable: false, read_only: true, side_effect_state: "none",
    idempotency: "idempotent", retry: "safe", output_contract: "three_state",
  },
  {
    name: "lookup_person",
    description:
      "Work out who a name or nickname in this conversation refers to. Call this when a message uses a name you do not recognise.\n\nCall it when knowing who it is changes your answer. It knows only people in THIS conversation, so do not call it for a name you were already given (the person who tagged you) or for someone plainly outside the chat, such as a singer or a company.\n\nReturns `status`: ok, not_found, ambiguous or unavailable, with a `note` on how to report it.",
    budget: 4, terminal: false, time_shiftable: null, source_tier: "remembered",
    cacheable: false, read_only: true, side_effect_state: "none",
    idempotency: "idempotent", retry: "safe", output_contract: "three_state",
  },
  {
    name: "ask_memory",
    description:
      "Work out an answer to a specific QUESTION about someone, not a profile dump. Reach for it when a profile lookup is not enough.\n\nIt reasons over everything you have gathered about the person, where `about` returns the stored conclusions: use it for a judgement that has to be pieced together. It is slow (several seconds or more), so call it only when `about` would not answer. It knows only people in THIS conversation.\n\nSHOULD: write `query` yourself, as the question you need answered, not the raw tag message.\n\nReturns `status`: ok (`answer`), nothing_known, unavailable, not_found or ambiguous, with a `note` on how to report it.",
    budget: 4, terminal: false, time_shiftable: null, source_tier: "remembered",
    cacheable: false, read_only: true, side_effect_state: "none",
    idempotency: "idempotent", retry: "safe", output_contract: "three_state",
  },
  {
    name: "search_memory",
    description:
      "Find where something was actually SAID in this group's history, in the exact words. Use it to recall a specific thing somebody said a long time ago.\n\nIt reaches past `load_transcript`, and returns words, not a profile (`about`) or a judgement (`ask_memory`).\n\nReturns `status`: ok, nothing_found, withheld or unavailable, with a `note`.",
    budget: 4, terminal: false, time_shiftable: null, source_tier: "remembered",
    cacheable: false, read_only: true, side_effect_state: "none",
    idempotency: "idempotent", retry: "safe", output_contract: "three_state",
  },
  {
    name: "media_fetch",
    description:
      "Look at an image or video and say what it shows. Call this when answering means knowing what is IN a picture or a video.\n\nPass exactly one: `code`, from an `[image ...]` or `[video ...]` marker in view here, or `url`, an http(s) link to a picture or video found elsewhere. It cannot open a file; a voice note gives back what was said.\n\nLeave `question` empty for the stored reading (what it shows, all its text), free and instant. Ask one only when that reading cannot answer it; each costs a fresh look.\n\nReturns `status`: ok, nothing_found, ambiguous, not_an_image or unavailable, with a `note` on how to report it.",
    budget: 4, terminal: false, time_shiftable: null, source_tier: null,
    cacheable: false, read_only: true, side_effect_state: "none",
    idempotency: "idempotent", retry: "safe", output_contract: "three_state",
  },
  {
    name: "generate_image",
    description:
      "Make an image from a prompt, optionally from photos in this conversation, and post it to this chat. Call this when somebody asks you to make, draw or edit a picture.\n\nMUST: load the image-generation skill before calling this tool. It holds the canonical reference and the appearance rules, which this tool does not repeat.\n\n`reference_images` takes the marker codes of photos, at most 5 in one call. With neither `size` nor `aspect` the shape is the provider's choice.\n\nMUST: the prompt goes to an outside service, so it never carries a name, phone number, address or other identifier.",
    budget: 2, terminal: false, time_shiftable: null, source_tier: null,
    cacheable: false, read_only: false, side_effect_state: "unknown",
    idempotency: "non_idempotent", retry: "never", output_contract: "three_state",
  },
  {
    name: "web_search",
    description:
      "Look something up on the web. Call this when the answer is not in the conversation and no other tool covers it. Returns ranked links with a snippet each.\n\nOther tools come first: `transit`, `route`, `weather`, `about` for a person you know, `place_info` for a shop's hours or rating, and `parking_status` or `find_parking` for car park vacancy.\n\nNever search a post's ID (a tweet or video id, a status number): the engine cannot resolve one. Read the post with `x_read`, or ask for the link.\n\nReturns `status`: ok, nothing_found or unavailable, with a `note` on how to report it.",
    budget: 4, terminal: false, time_shiftable: null, source_tier: "web",
    cacheable: true, read_only: true, side_effect_state: "none",
    idempotency: "idempotent", retry: "safe", output_contract: "three_state",
  },
  {
    name: "web_fetch",
    description:
      "Open a specific web page and read what's actually on it. Call this when somebody shares a LINK and asks about it. Returns the page's text.\n\nAlso when a `web_search` snippet is not enough; with no link in hand, use `web_search`.\n\nReturns `status`: ok, nothing_found or unavailable, with a `note` on how to report it.",
    budget: 2, terminal: false, time_shiftable: null, source_tier: "web",
    cacheable: true, read_only: true, side_effect_state: "none",
    idempotency: "idempotent", retry: "safe", output_contract: "three_state",
  },
  {
    name: "weather",
    description:
      "The weather in Hong Kong: current conditions, any warnings, the outlook. Call this for anything about HK weather.\n\nOne call gives temperatures across HK, humidity, any warnings in force and today's outlook. Set `forecast=True` when they ask about the days ahead.\n\nReturns `status`: ok or unavailable, with a `note` on how to report it.",
    budget: 2, terminal: false, time_shiftable: null, source_tier: "confirmed",
    cacheable: true, read_only: true, side_effect_state: "none",
    idempotency: "idempotent", retry: "safe", output_contract: "three_state",
  },
  {
    name: "play_music",
    description:
      "Play a search term or a YouTube link in the asker's Discord voice channel. Call this when someone asks you to play music.\n\nThe asker must already be in a voice channel for a first track. `query` is a search term (the top YouTube match plays) or a URL. Only a real playlist URL queues several tracks.\n\nReturns `status`: ok, nothing_found or unavailable, with a `note` on how to report it.",
    budget: 2, terminal: false, time_shiftable: null, source_tier: null,
    cacheable: false, read_only: false, side_effect_state: "unknown",
    idempotency: "non_idempotent", retry: "never", output_contract: "three_state",
  },
  {
    name: "music_status",
    description:
      "Read what is playing in this Discord voice channel. Call this when somebody asks what song is on.\n\nReturns `status`: ok (`title` is the track playing now), nothing_found or unavailable, with a `note` when it is not ok.",
    budget: 2, terminal: false, time_shiftable: null, source_tier: "confirmed",
    cacheable: true, read_only: true, side_effect_state: "none",
    idempotency: "idempotent", retry: "safe", output_contract: "three_state",
  },
  {
    name: "remind",
    description:
      "Hold something for later and say it back into this conversation when the time comes, once or on a repeat. Call this when somebody asks you to remember a thing for them.\n\n`text` is their own words, not a script: when it comes due you get the turn back and answer it as a fresh request.\n\n`when` is one exact moment, ISO-8601 with an offset; for a repeat it is the first time it goes off.\n\nReturns `status`: ok, refused or unavailable, with a `note` on how to report it.",
    budget: 2, terminal: false, time_shiftable: null, source_tier: null,
    cacheable: false, read_only: false, side_effect_state: "unknown",
    idempotency: "non_idempotent", retry: "never", output_contract: "three_state",
  },
  {
    name: "create_event",
    description:
      "Propose a NEW gathering in this conversation, returning its id. Call this when the room wants to arrange something that is not open yet.\n\nYou MUST call list_events first: if an event is already open (proposed), update it with update_event instead of creating a second one.\n\nReturns `status`: ok, refused or unavailable, with a `note` when it is not ok.",
    budget: 3, terminal: false, time_shiftable: null, source_tier: null,
    cacheable: false, read_only: false, side_effect_state: "unknown",
    idempotency: "non_idempotent", retry: "never", output_contract: "three_state",
  },
  {
    name: "list_events",
    description:
      "List the gatherings proposed or confirmed in THIS conversation. Call this before create_event, so an open event gets updated instead of copied.\n\nReturns `status`: ok (each event's id, title, when, venue and status), refused or unavailable, with a `note` when it is not ok.",
    budget: 1, terminal: false, time_shiftable: null, source_tier: null,
    cacheable: false, read_only: true, side_effect_state: "none",
    idempotency: "idempotent", retry: "safe", output_contract: "three_state",
  },
  {
    name: "note_create",
    description:
      "Start carrying an open question that nothing here settles yet. Call this when the room is chewing on something that outlives this conversation: an undecided thing nobody has settled.\n\nA note is tracked uncertainty, never a settled fact or a description of a person. Check <notes> first: if a note already covers this, read and update it instead.\n\nIt MUST have a `closing_condition` and an anchor, or it is refused.\n\nReturns `status`: ok, refused or unavailable, with a `note` on how to report the last two.",
    budget: 1, terminal: false, time_shiftable: null, source_tier: null,
    cacheable: false, read_only: false, side_effect_state: "confirmed",
    idempotency: "non_idempotent", retry: "never", output_contract: "three_state",
  },
  {
    name: "remember",
    description:
      "Record a settled fact about the person speaking. Call this when somebody tells you something about themselves that is now just TRUE.\n\nThe trigger word is «記低». It writes to your long-term memory of THAT person, which `about` and `ask_memory` read back; nothing else can write a person's settled fact.\n\nReturns `status`: ok, refused or unavailable, with a `note` on how to report it.",
    budget: 1, terminal: false, time_shiftable: null, source_tier: null,
    cacheable: false, read_only: false, side_effect_state: "confirmed",
    idempotency: "non_idempotent", retry: "never", output_contract: "three_state",
  },
  {
    name: "send_reply",
    description:
      "Send your reply in words. Call this when your reply is text; a reaction GIF goes through `send_gif`.\n\nThis and `send_gif` are the ONLY way a reply reaches the room. You MUST call one of them whenever you reply. It always goes to the conversation you were tagged in; you do not choose where.\n\nReturns one of three words: `sent` (it reached the room), `held_for_review` (recorded, held by a review mode or cooldown), or `refused` (a guard stopped it and it will not go).",
    budget: 3, terminal: true, time_shiftable: null, source_tier: null,
    cacheable: false, read_only: false, side_effect_state: "unknown",
    idempotency: "non_idempotent", retry: "never", output_contract: null,
  },
];

// ==========================================================================
// 8. /status
// ==========================================================================

const START_TIME = Date.now();
const UPTIME_BASELINE_SECONDS = 3.2 * 24 * 3600;

function buildStatus(now) {
  if (isEmptied("status")) {
    return {
      uptime_seconds: UPTIME_BASELINE_SECONDS + (now - START_TIME) / 1000,
      store: { reachable: true },
      honcho: { reachable: true },
      reminders: { count: 0, next_due_at: null, upcoming: [] },
    };
  }
  const offsets = [3 * HOUR_MS, DAY_MS, 2 * DAY_MS + 10 * HOUR_MS, 5 * DAY_MS];
  const platforms = ["signal", "discord", "whatsapp", "signal"];
  const upcoming = offsets.map((offsetMs, i) => ({
    due_at: hkIso(now + offsetMs),
    platform: platforms[i],
    conversation_id: `grp_${fakeUuid(`status-conv-${i}`).slice(0, 12)}`,
  }));
  return {
    uptime_seconds: UPTIME_BASELINE_SECONDS + (now - START_TIME) / 1000,
    store: { reachable: true },
    honcho: { reachable: true },
    reminders: { count: upcoming.length, next_due_at: upcoming[0].due_at, upcoming },
  };
}

// ==========================================================================
// 9. /memory — at most one entry, for the requested personId only.
// ==========================================================================

const MEMORY_IDENTITY_TEMPLATES = {
  miles: ["Name: Miles", "Goes by Miles", "Speaks Cantonese and English", "Likes hotpot more than anyone else in the room", "Plays Abiotic Factor and The Big Walk"],
  ken: ["Name: Ken", "Goes by Ken", "Speaks Cantonese and English", "Drives most days", "Plays Abiotic Factor"],
  mei: ["Name: Mei", "Goes by Mei", "Speaks Cantonese and English", "Lives near Sha Tin"],
  jo: ["Name: Jo", "Goes by Jo", "Speaks Cantonese and English", "Plays Abiotic Factor and The Big Walk"],
  ada: ["Name: Ada", "Goes by Ada", "Speaks Cantonese and English", "Works in Kwun Tong"],
  sam: ["Name: Sam", "Goes by Sam", "Speaks English"],
};

// Short, bland, invented episode fragments — mixed English/Cantonese, in
// the style the task asked for. Picked per person from a shared pool by a
// seeded shuffle, so the set is stable per person but not identical across
// people.
const EPISODE_POOL = [
  "asked where to get hotpot in Yuen Long",
  "問邊間茶記早餐好食",
  "asked about bus times to Sha Tin",
  "mentioned a new pair of headphones",
  "asked how to get to Central after 11pm",
  "話想去行山但唔知邊條路線",
  "asked for a good ramen place open late",
  "mentioned their phone battery draining fast",
  "asked whether it will rain this weekend",
  "問邊個牌子嘅風扇好",
  "asked for help splitting a dinner bill",
  "mentioned finishing a work project",
  "asked what time the gym closes",
  "話想換部新手機",
  "asked about a parking space near Mong Kok",
  "mentioned watching a movie over the weekend",
  "asked for a recipe for claypot rice",
  "問邊度有得買到二手單車",
  "asked whether the MTR was delayed this morning",
  "mentioned a birthday coming up soon",
];

function episodesFor(personId, now) {
  const { pick } = rngStream(`memory-episodes-${personId}`);
  const shuffled = [...EPISODE_POOL];
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(rngStream(`memory-shuffle-${personId}-${i}`).float() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }
  const dayOffsets = [1, 2, 3, 5, 7, 9, 11, 14, 17, 20, 24, 28, 33, 40];
  return dayOffsets.map((d, i) => `${hkDate(now - d * DAY_MS)} — ${shuffled[i % shuffled.length]}`);
}

function buildMemory(now, personId) {
  if (!personId) return [];
  const ref = ROSTER_BY_ID.get(personId);
  if (!ref) return [];
  if (isEmptied("memory")) {
    return [{ person_id: personId, display_name: ref.displayName, reachable: true, identity: [], episodes: [] }];
  }
  return [
    {
      person_id: personId,
      display_name: ref.displayName,
      reachable: true,
      identity: MEMORY_IDENTITY_TEMPLATES[personId] ?? [`Name: ${ref.displayName}`],
      episodes: episodesFor(personId, now),
    },
  ];
}

// ==========================================================================
// 10. /persons, /persons/{id}
// ==========================================================================

function buildRoster() {
  if (isEmptied("persons")) return [];
  return ROSTER_SORTED.map((p) => ({ personId: p.personId, displayName: p.displayName }));
}

function buildPersonProfile(personId) {
  const ref = ROSTER_BY_ID.get(personId);
  if (!ref) return null;
  const gameProfiles = isEmptied("persons") ? [] : PERSON_GAME_PROFILES[personId] ?? [];
  return { personId: ref.personId, displayName: ref.displayName, gameProfiles };
}

// ==========================================================================
// 11. /events, /events/{id} — participant-scoped gatherings.
// ==========================================================================

const EVENTS = [
  {
    id: fakeUuid("evt-hotpot"),
    title: "Hotpot",
    whenMs: (now) => nextHkWeekdayAt(now, 0, 19, 0), // next Sunday 19:00 HKT
    venue: {
      text: null, name: "Yuen Long Hotpot House", address: "Yuen Long, Hong Kong",
      rating: 4.3, price_level: "$$",
      maps_url: "https://www.google.com/maps/search/?api=1&query=Yuen+Long+Hotpot+House",
    },
    status: "confirmed", created_by: "miles", createdOffsetDays: 4,
    participants: ["miles", "ken", "mei", "jo"],
  },
  {
    id: fakeUuid("evt-hike"),
    title: "Lion Rock hike",
    whenMs: (now) => nextHkWeekdayAt(now, 6, 9, 0), // next Saturday 09:00 HKT
    venue: { text: "Lion Rock Country Park trailhead", name: null, address: null, rating: null, price_level: "", maps_url: null },
    status: "proposed", created_by: "ken", createdOffsetDays: 1,
    participants: ["ken", "jo", "ada"],
  },
  {
    id: fakeUuid("evt-birthday"),
    title: "Mei's birthday dinner",
    whenMs: (now) => now + 9 * DAY_MS,
    venue: {
      text: null, name: "Causeway Bay Grill", address: "Causeway Bay, Hong Kong",
      rating: null, price_level: "$$$",
      maps_url: "https://www.google.com/maps/search/?api=1&query=Causeway+Bay+Grill",
    },
    status: "confirmed", created_by: "mei", createdOffsetDays: 6,
    participants: ["miles", "mei", "ada", "sam"],
  },
  {
    id: fakeUuid("evt-bbq"),
    title: "Beach BBQ",
    whenMs: (now) => now - 2 * DAY_MS,
    venue: {
      text: null, name: "Fung Ha Wan BBQ site", address: "Sai Kung, Hong Kong",
      rating: 3.9, price_level: "$$", maps_url: "https://www.google.com/maps/search/?api=1&query=Fung+Ha+Wan+BBQ+site",
    },
    status: "cancelled", created_by: "jo", createdOffsetDays: 10,
    participants: ["miles", "ken", "jo"],
  },
  {
    id: fakeUuid("evt-movie"),
    title: "Movie night",
    whenMs: (now) => now - 10 * DAY_MS,
    venue: {
      text: null, name: "MCL Cinema", address: "Telford Plaza, Kowloon Bay, Hong Kong",
      rating: 4.0, price_level: "$$", maps_url: "https://www.google.com/maps/search/?api=1&query=MCL+Cinema+Telford+Plaza",
    },
    status: "confirmed", created_by: "ken", createdOffsetDays: 16,
    participants: ["miles", "ken", "mei"],
  },
  {
    id: fakeUuid("evt-boardgames"),
    title: "Board game night",
    whenMs: (now) => now - 20 * DAY_MS,
    venue: { text: null, name: "Ada's place", address: "Kwun Tong, Hong Kong", rating: null, price_level: "", maps_url: null },
    status: "confirmed", created_by: "ada", createdOffsetDays: 24,
    participants: ["jo", "ada", "miles"],
  },
];

function gatheringDict(event, now) {
  return {
    id: event.id,
    title: event.title,
    when: hkIso(event.whenMs(now)),
    venue: event.venue,
    status: event.status,
    created_by: event.created_by,
    created_at: hkIso(event.whenMs(now) - event.createdOffsetDays * DAY_MS),
  };
}

function buildEventsList(now, personId) {
  if (isEmptied("events")) return [];
  return EVENTS.filter((e) => e.participants.includes(personId)).map((e) => gatheringDict(e, now));
}

function buildEventDetail(now, personId, id) {
  const event = EVENTS.find((e) => e.id === id);
  if (!event || !event.participants.includes(personId)) return null;
  const rsvpResponses = ["yes", "yes", "maybe", "no"];
  const rsvps = event.participants
    .filter((p) => p !== event.created_by)
    .map((p, i) => ({ person_id: p, response: rsvpResponses[i % rsvpResponses.length] }));
  return { ...gatheringDict(event, now), rsvps };
}

// ==========================================================================
// 12. Presence — positions, saved places, history, analytics.
// ==========================================================================

// Static per-person presence config. Coordinates are plausible Hong Kong
// points, hand-placed and rounded to a few decimal places — not read from
// any real Life360 export.
const PRESENCE_STATIC = {
  miles: {
    lat: 22.3075, lng: 114.2596, address: "Tong Ming Street, Tseung Kwan O, Hong Kong",
    wireState: "live", battery: 64,
  },
  mei: {
    lat: 22.3771, lng: 114.1974, address: "Sha Tin, Hong Kong",
    wireState: "stale", battery: 12, staleAgeMs: 2 * HOUR_MS,
  },
  jo: {
    lat: 22.4106, lng: 114.1245, address: "Nathan Road, Mong Kok, Hong Kong",
    wireState: "sharing_off", battery: 45, staleAgeMs: 3 * DAY_MS,
  },
  ada: {
    lat: 22.3141, lng: 114.226, address: "Hoi Yhys Road, Kwun Tong, Hong Kong",
    wireState: "live", battery: 88,
  },
};

// Ken's driving loop: ten hand-placed control points tracing a rough
// Tuen Mun Road -> Tjnw Highway -> Route 9 circuit, linearly interpolated
// into ~40 waypoints so the rendered path looks road-like rather than a
// straight bounce between ten dots.
const KEN_CONTROL_POINTS = [
  { lat: 22.4106, lng: 114.0228, label: "Yuen Long, Hong Kong" },
  { lat: 22.4155, lng: 113.9805, label: "Tuen Mun, Hong Kong" },
  { lat: 22.393, lng: 113.9945, label: "Tuen Mun Road, Hong Kong" },
  { lat: 22.372, lng: 114.0655, label: "Tuen Mun Road, Kwai Chung, Hong Kong" },
  { lat: 22.357, lng: 114.12, label: "Kwai Chung, Hong Kong" },
  { lat: 22.382, lng: 114.188, label: "Sha Tin, Hong Kong" },
  { lat: 22.427, lng: 114.193, label: "Tjnw Highway, Hong Kong" },
  { lat: 22.389, lng: 114.035, label: "Fanling, Hong Kong" },
  { lat: 22.5, lng: 114.127, label: "Sheung Shui, Hong Kong" },
  { lat: 22.47, lng: 114.07, label: "Route 9, Yuen Long, Hong Kong" },
];
const KEN_ROUTE = (() => {
  const segments = 4; // 10 control points x 4 -> 40 waypoints
  const pts = [];
  for (let i = 0; i < KEN_CONTROL_POINTS.length; i++) {
    const a = KEN_CONTROL_POINTS[i];
    const b = KEN_CONTROL_POINTS[(i + 1) % KEN_CONTROL_POINTS.length];
    for (let s = 0; s < segments; s++) {
      const t = s / segments;
      pts.push({ lat: a.lat + (b.lat - a.lat) * t, lng: a.lng + (b.lng - a.lng) * t, label: a.label });
    }
  }
  return pts;
})();
const KEN_LOOP_MS = 20 * 60 * 1000;

function kenPositionAt(now) {
  const frac = ((now % KEN_LOOP_MS) + KEN_LOOP_MS) % KEN_LOOP_MS / KEN_LOOP_MS;
  const idxFloat = frac * KEN_ROUTE.length;
  const i0 = Math.floor(idxFloat) % KEN_ROUTE.length;
  const i1 = (i0 + 1) % KEN_ROUTE.length;
  const t = idxFloat - Math.floor(idxFloat);
  const a = KEN_ROUTE[i0];
  const b = KEN_ROUTE[i1];
  return {
    lat: a.lat + (b.lat - a.lat) * t,
    lng: a.lng + (b.lng - a.lng) * t,
    address: a.label,
  };
}

function kenSpeedKmh(now) {
  return 45 + 35 * (0.5 + 0.5 * Math.sin((now / KEN_LOOP_MS) * 2 * Math.PI * 3));
}

function buildPresenceList(now) {
  if (isEmptied("presence")) return [];
  return CIRCLE_IDS.map((personId) => {
    const ref = ROSTER_BY_ID.get(personId);
    if (personId === "ken") {
      const pos = kenPositionAt(now);
      return {
        personId, displayName: ref.displayName,
        current: {
          lat: pos.lat, lng: pos.lng, ts: now,
          ageSeconds: 2 + ((now % 4000) / 1000),
          wireState: "live", batteryPercent: 31, isDriving: true,
          speedKmh: kenSpeedKmh(now), address: pos.address,
        },
      };
    }
    const s = PRESENCE_STATIC[personId];
    const ageSeconds = s.staleAgeMs ? (s.staleAgeMs + (now % 60_000)) / 1000 : 30 + (now % 60_000) / 1000;
    return {
      personId, displayName: ref.displayName,
      current: {
        lat: s.lat, lng: s.lng, ts: now - (s.staleAgeMs ?? 45_000),
        ageSeconds, wireState: s.wireState, batteryPercent: s.battery,
        isDriving: false, address: s.address,
      },
    };
  });
}

const SAVED_PLACES = [
  { name: "Home", lat: 22.3075, lng: 114.2596 },
  { name: "Office", lat: 22.2825, lng: 114.1585 },
  { name: "Gym", lat: 22.4106, lng: 114.1245 },
  { name: "Mum's", lat: 22.3771, lng: 114.1974 },
];

function buildSavedPlaces() {
  return isEmptied("presence") ? [] : SAVED_PLACES;
}

function buildPresenceHistory(now, personId, hours) {
  const clamped = clamp(hours, 1, 720);
  const from = now - clamped * HOUR_MS;
  if (isEmptied("presence") || !CIRCLE_IDS.includes(personId)) {
    return { hours: clamped, from, to: now, points: [] };
  }
  const points = [];
  const stepMs = Math.max(HOUR_MS / 6, (clamped * HOUR_MS) / 120); // <= ~120 stay points before the drive tail
  if (personId === "ken") {
    // A day of stays plus the live drive, ending at the current position.
    for (let t = from; t < now - KEN_LOOP_MS; t += stepMs) {
      points.push({ lat: PRESENCE_STATIC.miles.lat, lng: PRESENCE_STATIC.miles.lng, ts: t });
    }
    const driveStart = Math.max(from, now - KEN_LOOP_MS);
    for (let t = driveStart; t <= now; t += KEN_LOOP_MS / 30) {
      const pos = kenPositionAt(t);
      points.push({ lat: pos.lat, lng: pos.lng, ts: t });
    }
  } else {
    const s = PRESENCE_STATIC[personId];
    const { float } = rngStream(`history-${personId}`);
    for (let t = from; t <= now; t += stepMs) {
      points.push({ lat: s.lat + float(-0.002, 0.002), lng: s.lng + float(-0.002, 0.002), ts: t });
    }
  }
  return { hours: clamped, from, to: now, points: points.slice(-600) };
}

// The rollup's real vocabulary (nora/analytics/rollup.py): home, work, and
// every other clustered stay "unlabeled". A label is a saved place's name
// when one covers the cluster, else null.
const ANALYTICS_LABELS = ["Gym", "Mum's", "Yuen Long MTR", "Tai Chi market", null, null, null];

function buildDerivedPlacesForSubject(subjectId, now) {
  const { float, int, pick, bool } = rngStream(`derived-places-${subjectId}`);
  const s = subjectId === "ken" ? { lat: KEN_CONTROL_POINTS[0].lat, lng: KEN_CONTROL_POINTS[0].lng } : PRESENCE_STATIC[subjectId] ?? { lat: 22.3, lng: 114.15 };
  const count = int(2, 5);
  const places = [];
  for (let i = 0; i < count; i++) {
    const kind = i === 0 ? "home" : i === 1 ? "work" : "unlabeled";
    const lastSeenTs = now - int(0, 5) * DAY_MS;
    // Home sits where the person usually is; the rest spread around it.
    const spread = i === 0 ? 0.0004 : 0.05;
    places.push({
      kind,
      label: kind === "unlabeled" ? pick(ANALYTICS_LABELS) : null,
      lat: s.lat + float(-spread, spread), lng: s.lng + float(-spread, spread),
      radiusM: int(60, 250), confidence: Number(float(0.4, 0.98).toFixed(2)),
      firstSeenTs: now - int(60, 120) * DAY_MS, lastSeenTs,
      stayCount: int(5, 200), totalDwellMs: int(5, 200) * int(10, 90) * 60_000,
    });
  }
  return places;
}

function buildDerivedPlaces(now) {
  if (isEmptied("presence")) return { subjects: [] };
  return { subjects: CIRCLE_IDS.map((id) => ({ subjectId: id, places: buildDerivedPlacesForSubject(id, now) })) };
}

function rhythmWeight(hourOfWeek) {
  const day = Math.floor(hourOfWeek / 24); // 0=Monday
  const hour = hourOfWeek % 24;
  const isWeekend = day >= 5;
  let w = 0.05;
  if (!isWeekend && (hour === 8 || hour === 9)) w = 0.9;
  else if (!isWeekend && (hour === 18 || hour === 19)) w = 0.85;
  else if (isWeekend && hour >= 13 && hour <= 18) w = 0.6;
  else if (hour >= 0 && hour <= 6) w = 0.3; // home overnight
  return w;
}

function buildRhythm(now, subjectId, days) {
  if (isEmptied("presence") || !subjectId || !CIRCLE_IDS.includes(subjectId)) {
    return { subjectId, bins: [], windowDays: days };
  }
  const { float } = rngStream(`rhythm-${subjectId}`);
  const bins = [];
  for (let h = 0; h < 168; h++) {
    const w = rhythmWeight(h) * float(0.85, 1.15);
    bins.push({ hourOfWeek: h, stayCount: Math.round(w * 6), totalDwellMs: Math.round(w * 6 * float(8, 40) * 60_000) });
  }
  return { subjectId, bins, windowDays: days };
}

const CORRIDOR_TEMPLATES = [
  ["Yuen Long, Hong Kong", "Tuen Mun Road, Hong Kong", "Kwai Chung, Hong Kong"],
  ["Sha Tin, Hong Kong", "Tjnw Highway, Hong Kong", "Fanling, Hong Kong"],
  ["Mong Kok, Hong Kong", "Nathan Road, Hong Kong", "Tsim Sha Tsui, Hong Kong"],
  ["Kwun Tong, Hong Kong", "Kowloon Bay, Hong Kong"],
];

function buildRoutesForSubject(subjectId, now) {
  const { int, float, bool } = rngStream(`routes-${subjectId}`);
  const corridors = CORRIDOR_TEMPLATES.map((sequence, i) => ({
    id: i + 1,
    sampleCount: int(4, 40),
    firstSeenTs: now - int(60, 90) * DAY_MS,
    lastSeenTs: now - int(0, 5) * DAY_MS,
    sequence,
  }));
  const tripCount = int(12, 25);
  const trips = [];
  for (let i = 0; i < tripCount; i++) {
    const startedTs = now - int(0, 90) * DAY_MS - int(0, 23) * HOUR_MS;
    const distanceM = int(1500, 25000);
    const avgSpeedKmh = int(15, 60);
    const durationMs = (distanceM / 1000 / avgSpeedKmh) * HOUR_MS;
    const hasCorridor = bool(0.7);
    const corridor = hasCorridor ? corridors[int(0, corridors.length - 1)] : null;
    trips.push({
      id: i + 1,
      startedTs, endedTs: startedTs + durationMs,
      distanceM, avgSpeedKmh, fixCount: int(5, 80),
      corridorId: corridor ? corridor.id : null,
      corridorSampleCount: corridor ? corridor.sampleCount : null,
    });
  }
  trips.sort((a, b) => a.startedTs - b.startedTs);
  return { trips, corridors };
}

function buildRoutes(now, subjectId, days) {
  if (isEmptied("presence") || !subjectId || !CIRCLE_IDS.includes(subjectId)) {
    return { subjectId, trips: [], corridors: [] };
  }
  const since = now - days * DAY_MS;
  const { trips, corridors } = buildRoutesForSubject(subjectId, now);
  return { subjectId, trips: trips.filter((t) => t.startedTs >= since), corridors };
}

// ==========================================================================
// 13. Games — Pulse's REST surface, thin-projected through Nora
//     (nora/web/games.py). Three servers: abiotic (container, online),
//     bigwalk (local-save), palworld (container, offline).
// ==========================================================================

const GAME_SERVER_SUMMARY = {
  abiotic: { id: "abiotic", displayName: "Abiotic Factor", color: "#5b8a72", maxPlayers: 6 },
  bigwalk: { id: "bigwalk", displayName: "The Big Walk", color: "#b07a4a" },
  palworld: { id: "palworld", displayName: "Palworld", color: "#6a7fb0", maxPlayers: 8 },
};

function nref(id, en, zhHant, known = true) {
  return { id, en: known ? en : id, zhHant: known ? zhHant : id, known };
}

// abiotic's known player roster: 5 people, matching /persons/{id}'s
// gameProfiles (miles/ken/jo are linked identity.toml people; tao/vic are
// invented friends with no portal identity).
const ABIOTIC_PLAYERS = [
  { personId: "ken", playerName: "Ken", steamId: "74822881012340001", online: true, hasSave: true },
  { personId: "jo", playerName: "Jo", steamId: "74822881012340002", online: true, hasSave: true },
  { personId: "miles", playerName: "Miles", steamId: "74822881012340003", online: false, hasSave: true },
  { personId: null, playerName: "Tao", steamId: "74822881012340004", online: false, hasSave: false },
  { personId: null, playerName: "Vic", steamId: "74822881012340005", online: false, hasSave: false },
];
function abioticPlayerKey(p) {
  return `steam_${p.steamId}`;
}

function abioticCpuPercent(now) {
  return 55 + 15 * Math.sin(now / (7 * 60_000)) + 3 * Math.sin(now / (53_000));
}
function abioticMemoryBytes(now) {
  const gib = 1024 ** 3;
  return Math.round((5.8 + 0.3 * Math.sin(now / (11 * 60_000))) * gib);
}

function containerMetrics({ serverId, now, online, cpuPercent, memoryBytes, playerCount, image }) {
  const uptimeSeconds = UPTIME_BASELINE_SECONDS + (now - START_TIME) / 1000;
  return {
    serverId, timestamp: now, online, status: online ? "running" : "exited",
    cpuPercent: Math.max(0, Number(cpuPercent.toFixed(1))),
    memoryBytes, memoryLimitBytes: 16 * 1024 ** 3,
    networkRxBytes: Math.round(uptimeSeconds * 8_200), networkTxBytes: Math.round(uptimeSeconds * 3_100),
    networkRxBps: online ? 6_500 : 0, networkTxBps: online ? 2_400 : 0,
    blockReadBytes: Math.round(uptimeSeconds * 512), blockWriteBytes: Math.round(uptimeSeconds * 1_024),
    pids: online ? 34 : 0, restartCount: 2, playerCount,
    startedAt: hkIso(now - UPTIME_BASELINE_SECONDS * 1000),
    image,
  };
}

function buildGamesServers(now) {
  if (isEmptied("games")) return [];
  const abioticOnline = ABIOTIC_PLAYERS.filter((p) => p.online).length;
  return [
    {
      ...GAME_SERVER_SUMMARY.abiotic,
      current: containerMetrics({
        serverId: "abiotic", now, online: true, cpuPercent: abioticCpuPercent(now),
        memoryBytes: abioticMemoryBytes(now), playerCount: abioticOnline,
        image: "ghcr.io/example/abiotic-factor-server:latest",
      }),
    },
    { ...GAME_SERVER_SUMMARY.bigwalk, current: null },
    {
      ...GAME_SERVER_SUMMARY.palworld,
      current: containerMetrics({
        serverId: "palworld", now, online: false, cpuPercent: 0,
        memoryBytes: Math.round(1.2 * 1024 ** 3), playerCount: 0,
        image: "ghcr.io/example/palworld-server:latest",
      }),
    },
  ];
}

function buildServerStatus(now, id) {
  if (id === "abiotic") {
    const online = ABIOTIC_PLAYERS.filter((p) => p.online);
    return {
      server: GAME_SERVER_SUMMARY.abiotic,
      current: containerMetrics({
        serverId: "abiotic", now, online: true, cpuPercent: abioticCpuPercent(now),
        memoryBytes: abioticMemoryBytes(now), playerCount: online.length,
        image: "ghcr.io/example/abiotic-factor-server:latest",
      }),
      activePlayers: online.map((p) => p.playerName),
      activeProfiles: online.map((p) => ({ playerKey: abioticPlayerKey(p), playerName: p.playerName })),
    };
  }
  if (id === "palworld") {
    return {
      server: GAME_SERVER_SUMMARY.palworld,
      current: containerMetrics({
        serverId: "palworld", now, online: false, cpuPercent: 0,
        memoryBytes: Math.round(1.2 * 1024 ** 3), playerCount: 0,
        image: "ghcr.io/example/palworld-server:latest",
      }),
      activePlayers: [], activeProfiles: [],
    };
  }
  if (id === "bigwalk") {
    return {
      server: GAME_SERVER_SUMMARY.bigwalk, game: "big-walk",
      lastUploadedAt: now - 6 * HOUR_MS, lastPlayedAt: now - 7 * HOUR_MS,
      totalPlaytimeSeconds: 186_000, activePlayers: [], activeProfiles: [],
    };
  }
  return null;
}

function buildGraph(now, id, hours) {
  if (id === "bigwalk") return { hours, from: now - hours * HOUR_MS, to: now, data: [] };
  const points = Math.min(hours, 168);
  const { float } = rngStream(`graph-${id}`);
  const data = [];
  for (let i = points; i >= 1; i--) {
    const t = now - i * HOUR_MS;
    const hourOfDay = new Date(t + HK_OFFSET_MS).getUTCHours();
    const online = id === "palworld" ? 0 : 1;
    const eveningPeak = id === "palworld" ? 0 : hourOfDay >= 18 && hourOfDay <= 23 ? 2 : hourOfDay >= 0 && hourOfDay <= 7 ? 0 : 1;
    const cpuAvg = id === "palworld" ? 0 : 40 + eveningPeak * 10 + float(-5, 5);
    data.push({
      timestamp: t, online,
      cpuAvg: Math.max(0, Number(cpuAvg.toFixed(1))), cpuMax: Math.max(0, Number((cpuAvg + float(2, 10)).toFixed(1))),
      memoryAvg: id === "palworld" ? 0 : abioticMemoryBytes(t), memoryMax: id === "palworld" ? 0 : abioticMemoryBytes(t) + 40_000_000,
      memoryLimit: 16 * 1024 ** 3,
      networkRxBpsAvg: online ? 5_000 + float(0, 3000) : 0, networkTxBpsAvg: online ? 2_000 + float(0, 1500) : 0,
      playerAvg: id === "palworld" ? 0 : Math.min(2, eveningPeak), playerMax: id === "palworld" ? 0 : 2,
    });
  }
  return { hours, from: now - hours * HOUR_MS, to: now, data };
}

function buildWeekly(now, id) {
  const uniquePlayers = id === "abiotic" ? 5 : id === "palworld" ? 0 : 2;
  return {
    weekStart: now - 7 * DAY_MS, weekEnd: now,
    uniquePlayers, sessions: uniquePlayers * 3, playtimeMs: uniquePlayers * 3 * 90 * 60_000,
    deaths: id === "abiotic" ? 6 : 0, peakPlayers: id === "abiotic" ? 2 : id === "palworld" ? 0 : 2,
    availabilityPercent: id === "palworld" ? 0 : 99.4,
    activityCoverageStartedAt: now - 30 * DAY_MS,
  };
}

// -------------------------------------------------------------- world/save

const ABIOTIC_ITEMS = [nref("scrap_metal", "Scrap Metal", "廢金屬"), nref("purified_water", "Purified Water", "淨化水"), nref("canned_beans", "Canned Beans", "罐頭豆"), nref("duct_tape", "Duct Tape", "萬能膠布"), nref("unk_misc_47", "unk_misc_47", "unk_misc_47", false)];
const ABIOTIC_STRUCTURES = [nref("workbench", "Workbench", "工作台"), nref("bed", "Bed", "床"), nref("generator", "Generator", "發電機")];
const ABIOTIC_SECTORS = [
  { ...nref("sector_4", "Sector 4 - Storage", "4號區 - 儲存室"), sectorId: "sector_4", structures: 6, storedItems: 210 },
  { ...nref("sector_7", "Sector 7 - Labs", "7號區 - 實驗室"), sectorId: "sector_7", structures: 4, storedItems: 88 },
];
const ABIOTIC_TIMELINE = [
  { ...nref("power_restored", "Backup power restored", "備用電源已恢復"), day: 12 },
  { ...nref("elevator_unlocked", "Elevator unlocked", "電梯已解鎖"), day: 18 },
  { ...nref("sector_7_found", "Sector 7 discovered", "發現7號區"), day: 26 },
];

function buildAbioticWorld(now) {
  return {
    savedAt: now - 40 * 60_000,
    metadata: { minutesPassed: 26_340, day: 27, storyProgress: "sector_7" },
    stockpile: {
      items: ABIOTIC_ITEMS.map((it, i) => ({ ...it, count: [420, 96, 58, 33, 4][i] })),
      structures: ABIOTIC_STRUCTURES.map((s, i) => ({ ...s, count: [3, 2, 2][i] })),
      sectors: ABIOTIC_SECTORS,
      totalItems: 611, totalStructures: 7, distinctItems: ABIOTIC_ITEMS.length,
    },
    timeline: ABIOTIC_TIMELINE,
  };
}

function buildPalworldWorld(now) {
  return {
    savedAt: now - 3 * DAY_MS,
    metadata: { minutesPassed: 0, day: 0, storyProgress: "paused" },
    stockpile: { items: [], structures: [], sectors: [], totalItems: 0, totalStructures: 0, distinctItems: 0 },
    timeline: [],
  };
}

const BIGWALK_AREAS = ["forest", "coast", "town", "mountain"];
function bigwalkRecord(values) {
  return Object.fromEntries(BIGWALK_AREAS.map((area, i) => [area, values[i] ?? 0]));
}

function buildBigwalkWorld(now) {
  const players = [
    { steamId: ABIOTIC_PLAYERS.find((p) => p.personId === "miles").steamId, head: 92, torso: 88, legs: 95, walkingProven: true, linkedPerson: { personId: "miles", displayName: "Miles" } },
    { steamId: ABIOTIC_PLAYERS.find((p) => p.personId === "jo").steamId, head: 80, torso: 76, legs: 84, walkingProven: true, linkedPerson: { personId: "jo", displayName: "Jo" } },
  ];
  return {
    game: "big-walk",
    savedAt: now - 6 * HOUR_MS,
    slotName: "Slot 1", filenameUid: "bw-save-0001",
    worldSeed: 837291, lastPlayedAt: now - 7 * HOUR_MS, totalPlaytimeSeconds: 186_000,
    skipAidsActive: false,
    players,
    completions: {
      gourds: bigwalkRecord([12, 9, 15, 6]),
      bigKeys: bigwalkRecord([1, 1, 1, 0]),
      fmStations: bigwalkRecord([3, 2, 4, 1]),
      lookoutLights: bigwalkRecord([2, 1, 2, 1]),
      namedDoors: bigwalkRecord([1, 0, 1, 0]),
    },
    uuidFlags: { "flag-intro-seen": 1, "flag-met-ranger": 1, "flag-secret-cave": 0 },
    notes: { "coast-tide": "check tide chart before the coast run", "town-shop": "shop restocks at dawn" },
    inventory: ["Walking Stick", "Rain Poncho", "Trail Map", "Thermos", "Spare Boots", "Harmonica"],
  };
}

function buildProgress(now) {
  const world = buildBigwalkWorld(now);
  const sum = (rec) => Object.values(rec).reduce((a, b) => a + b, 0);
  return {
    game: "big-walk", slotName: world.slotName, lastPlayedAt: world.lastPlayedAt,
    totalPlaytimeSeconds: world.totalPlaytimeSeconds, totalPlaytimeHours: Number((world.totalPlaytimeSeconds / 3600).toFixed(1)),
    playerCount: world.players.length, inventoryCount: world.inventory.length,
    completionCounts: {
      gourds: sum(world.completions.gourds), bigKeys: sum(world.completions.bigKeys),
      fmStations: sum(world.completions.fmStations), lookoutLights: sum(world.completions.lookoutLights),
      namedDoors: sum(world.completions.namedDoors), uuidFlags: Object.values(world.uuidFlags).filter(Boolean).length,
    },
  };
}

function buildSaveHistory(now, hours) {
  const clamped = clamp(hours, 1, 24 * 90);
  const from = now - clamped * HOUR_MS;
  const points = [];
  const n = Math.min(15, Math.max(2, Math.floor(clamped / 24)));
  for (let i = 0; i < n; i++) {
    const uploadedAt = from + ((i + 1) / n) * (now - from);
    points.push({
      uploadedAt, hash: fakeUuid(`save-${i}`).slice(0, 12),
      totalPlaytimeSeconds: 120_000 + i * 4_500,
      completedGourds: 20 + i, completedBigKeys: 1 + Math.floor(i / 5),
      completedFmStations: 6 + Math.floor(i / 2), completedLookoutLights: 3 + Math.floor(i / 3),
      completedNamedDoors: 1 + Math.floor(i / 6), completedUuidFlags: 2 + Math.floor(i / 4),
      playerCount: 2, inventoryCount: 5 + (i % 3),
    });
  }
  return { hours: clamped, from, to: now, data: points };
}

// -------------------------------------------------------------- insights

const ABIOTIC_DEATH_CAUSES = [
  { cause: "Starvation", causeZhHant: "饑餓致死" },
  { cause: "Mutant Attack", causeZhHant: "變異生物襲擊" },
  { cause: "Fall Damage", causeZhHant: "墜落傷害" },
  { cause: "Radiation", causeZhHant: "輻射" },
];
const ABIOTIC_KILL_TARGETS = [nref("mutated_rat", "Mutated Rat", "變異老鼠"), nref("sewer_crawler", "Sewer Crawler", "下水道爬行者"), nref("rogue_drone", "Rogue Drone", "失控無人機")];
const ABIOTIC_SKILLS = [
  { index: 0, en: "Melee Combat", zhHant: "近戰格鬥" }, { index: 1, en: "Crafting", zhHant: "手工藝" },
  { index: 2, en: "Stealth", zhHant: "潛行" }, { index: 3, en: "First Aid", zhHant: "急救" }, { index: 4, en: "Cooking", zhHant: "烹飪" },
];

function buildInsights(now, id, days) {
  if (id !== "abiotic") {
    return { days, from: now - days * DAY_MS, leaderboard: [], deathCauses: [], heatmap: [], coplay: [], killLeaderboard: [], learnerLeaderboard: [] };
  }
  const { int } = rngStream(`insights-${id}`);
  const leaderboard = ABIOTIC_PLAYERS.map((p) => {
    const deaths = int(0, 4);
    return {
      playerKey: abioticPlayerKey(p), playerName: p.playerName,
      sessions: int(4, 40), playtimeMs: int(4, 40) * int(30, 120) * 60_000,
      longestSessionMs: int(60, 240) * 60_000, deaths, deathsPerHour: Number((deaths / int(5, 40)).toFixed(2)),
    };
  });
  const heatmap = [];
  for (let weekday = 0; weekday < 7; weekday++) {
    for (let hour = 0; hour < 24; hour++) {
      const evening = hour >= 18 && hour <= 23 ? 1.6 : 1;
      heatmap.push({ weekday, hour, playerHours: Number((0.05 * evening * (weekday === 0 || weekday === 6 ? 1.4 : 1)).toFixed(2)) });
    }
  }
  const coplay = [
    { aKey: abioticPlayerKey(ABIOTIC_PLAYERS[0]), aName: "Ken", bKey: abioticPlayerKey(ABIOTIC_PLAYERS[1]), bName: "Jo", overlapMs: 5_400_000 },
    { aKey: abioticPlayerKey(ABIOTIC_PLAYERS[0]), aName: "Ken", bKey: abioticPlayerKey(ABIOTIC_PLAYERS[2]), bName: "Miles", overlapMs: 2_100_000 },
  ];
  const killLeaderboard = ABIOTIC_PLAYERS.slice(0, 3).map((p, i) => ({
    playerKey: abioticPlayerKey(p), playerName: p.playerName, totalKills: [42, 31, 18][i],
    kills: ABIOTIC_KILL_TARGETS.map((t, j) => ({ ...t, count: [20, 14, 8][j] - i * 2 })),
  }));
  const learnerLeaderboard = ABIOTIC_PLAYERS.slice(0, 3).map((p, i) => ({
    playerKey: abioticPlayerKey(p), playerName: p.playerName, xp: [8200, 6100, 4300][i],
    topSkill: { index: ABIOTIC_SKILLS[i].index, xp: [3200, 2100, 1400][i], en: ABIOTIC_SKILLS[i].en, zhHant: ABIOTIC_SKILLS[i].zhHant },
  }));
  return {
    days, from: now - days * DAY_MS, leaderboard,
    deathCauses: ABIOTIC_DEATH_CAUSES.map((c, i) => ({ ...c, count: [5, 3, 2, 1][i] })),
    heatmap, coplay, killLeaderboard, learnerLeaderboard,
  };
}

// -------------------------------------------------------------- players

function playerSummary(p, now) {
  const { int } = rngStream(`player-summary-${p.steamId}`);
  const sessionCount = int(3, 60);
  const deaths = int(0, 5);
  return {
    serverId: "abiotic", playerKey: abioticPlayerKey(p), playerId: p.steamId, playerName: p.playerName,
    online: p.online, sessionCount, totalPlaytimeMs: sessionCount * int(30, 150) * 60_000,
    longestSessionMs: int(60, 300) * 60_000, deaths,
    topDeathCause: deaths > 0 ? { cause: ABIOTIC_DEATH_CAUSES[int(0, ABIOTIC_DEATH_CAUSES.length - 1)].cause, count: int(1, deaths) } : null,
    firstSeenAt: now - int(30, 90) * DAY_MS, lastSeenAt: p.online ? now : now - int(1, 10) * DAY_MS,
  };
}

function buildPlayersPage(now, page, pageSize, online) {
  if (isEmptied("games")) return { data: [], pagination: { page, pageSize, total: 0, hasMore: false } };
  let players = ABIOTIC_PLAYERS;
  if (online !== null) players = players.filter((p) => p.online === online);
  const summaries = players.map((p) => playerSummary(p, now));
  const total = summaries.length;
  const start = (page - 1) * pageSize;
  const data = summaries.slice(start, start + pageSize);
  return { data, pagination: { page, pageSize, total, hasMore: start + pageSize < total } };
}

function abioticSave(p, now) {
  const { int, float } = rngStream(`save-${p.steamId}`);
  const jobs = [{ en: "Security Officer", zhHant: "保安人員" }, { en: "Research Assistant", zhHant: "研究助理" }, { en: "Janitor", zhHant: "清潔工" }];
  const traits = [
    { id: "night_owl", en: "Night Owl", zhHant: "夜貓子", negative: false },
    { id: "clumsy", en: "Clumsy", zhHant: "論盡", negative: true },
    { id: "iron_stomach", en: "Iron Stomach", zhHant: "鐵胃", negative: false },
  ];
  // A fraction, 0..1: the old portal rendered it through formatPercent.
  const equipment = [
    { ...nref("makeshift_spear", "Makeshift Spear", "自製長槍"), durability: int(8, 100) / 100 },
    { ...nref("hazmat_suit", "Hazmat Suit", "防化服"), durability: int(8, 100) / 100 },
    { ...nref("flashlight", "Flashlight", "電筒"), durability: int(8, 100) / 100 },
  ];
  const skills = ABIOTIC_SKILLS.map((s) => {
    const xp = int(50, 4000);
    const level = Math.max(1, Math.floor(xp / 500));
    const xpForLevel = 500;
    const xpIntoLevel = xp % xpForLevel;
    return { index: s.index, xp, level, progress: Number((xpIntoLevel / xpForLevel).toFixed(2)), xpIntoLevel, xpForLevel, en: s.en, zhHant: s.zhHant };
  });
  const killCounts = ABIOTIC_KILL_TARGETS.map((t) => ({ ...t, count: int(0, 30) }));
  return {
    savedAt: now - int(10, 600) * 60_000,
    job: jobs[int(0, jobs.length - 1)],
    traits: traits.filter(() => float() > 0.4),
    equipment, skills, killCounts,
    limbHealth: { head: int(60, 100), torso: int(60, 100), leftArm: int(60, 100), rightArm: int(60, 100), leftLeg: int(60, 100), rightLeg: int(60, 100) },
    survival: { hunger: int(40, 100), thirst: int(40, 100), sanity: int(40, 100), fatigue: int(0, 60), continence: int(40, 100), radiation: int(0, 20), bodyTemperature: int(36, 38) },
    money: int(0, 500),
    buffs: [],
    location: ABIOTIC_SECTORS[int(0, ABIOTIC_SECTORS.length - 1)],
    collections: { crafted: int(10, 300), pickedUp: int(50, 900), fish: int(0, 40) },
  };
}

function vitalsHistory(p, now) {
  const { int } = rngStream(`vitals-${p.steamId}`);
  const points = [];
  for (let i = 0; i < 40; i++) {
    const savedAt = now - (40 - i) * 45 * 60_000;
    const point = { savedAt, money: int(0, 500) };
    if (i !== 5) {
      point.limbHealth = { head: int(60, 100), torso: int(60, 100), leftArm: int(60, 100), rightArm: int(60, 100), leftLeg: int(60, 100), rightLeg: int(60, 100) };
    }
    if (i !== 12) {
      point.survival = { hunger: int(40, 100), thirst: int(40, 100), sanity: int(40, 100), fatigue: int(0, 60), continence: int(40, 100), radiation: int(0, 20), bodyTemperature: int(36, 38) };
    }
    points.push(point);
  }
  return points;
}

function statEvents(p, now) {
  const { int, pick } = rngStream(`statevents-${p.steamId}`);
  const kinds = ["kill", "craft", "levelup"];
  return Array.from({ length: 12 }, (_, i) => ({
    kind: pick(kinds), subject: ABIOTIC_KILL_TARGETS[int(0, ABIOTIC_KILL_TARGETS.length - 1)],
    delta: int(1, 5), occurredAt: now - (12 - i) * 3 * HOUR_MS,
  }));
}

function skillProgress(p, now) {
  const { int } = rngStream(`skillprogress-${p.steamId}`);
  return {
    days: 7, totalXp: int(1000, 9000),
    skills: ABIOTIC_SKILLS.map((s) => ({ index: s.index, xp: int(50, 1500), en: s.en, zhHant: s.zhHant })),
  };
}

function buildPlayerDetail(now, playerKey) {
  if (isEmptied("games")) return null;
  const p = ABIOTIC_PLAYERS.find((x) => abioticPlayerKey(x) === playerKey);
  if (!p) return null;
  const detail = { ...playerSummary(p, now), deathCauses: ABIOTIC_DEATH_CAUSES.slice(0, 2).map((c, i) => ({ ...c, count: [2, 1][i] })) };
  if (p.hasSave) {
    detail.save = abioticSave(p, now);
    detail.vitalsHistory = vitalsHistory(p, now);
    detail.statEvents = statEvents(p, now);
    detail.skillProgress = skillProgress(p, now);
  }
  if (p.personId) detail.linkedPerson = { personId: p.personId, displayName: ROSTER_BY_ID.get(p.personId).displayName };
  return detail;
}

// -------------------------------------------------------------- activity/sessions

const ACTIVITY_WORLD_EVENTS = [
  { message: "Backup power restored", messageZhHant: "備用電源已恢復" },
  { message: "A supply drop was found", messageZhHant: "發現一個補給箱" },
];

const BIGWALK_EVENTS = [
  ["Found a gourd in the forest.", "喺森林搵到一個葫蘆。"],
  ["Lit a lookout light on the coast.", "喺海邊點著咗一盞瞭望燈。"],
  ["Tuned in an FM station in town.", "喺市鎮收到一個 FM 台。"],
  ["Opened a named door on the mountain.", "喺山上打開咗一道有名嘅門。"],
  ["Picked up a big key.", "執到一條大鎖匙。"],
];

function buildBigwalkActivity(now, page, pageSize) {
  const { int, pick } = rngStream("bigwalk-activity");
  const all = Array.from({ length: 18 }, (_, i) => {
    const [message, messageZhHant] = pick(BIGWALK_EVENTS);
    const occurredAt = now - (i * 9 + int(1, 8)) * HOUR_MS;
    return { id: 9000 + i, serverId: "bigwalk", occurredAt, observedAt: occurredAt + 60_000, type: "world.event", message, messageZhHant };
  });
  const start = (page - 1) * pageSize;
  return { data: all.slice(start, start + pageSize), pagination: { page, pageSize, total: all.length, hasMore: start + pageSize < all.length } };
}

function buildActivityPage(now, page, pageSize) {
  if (isEmptied("games")) return { data: [], pagination: { page, pageSize, total: 0, hasMore: false } };
  const { pick, int } = rngStream("activity-abiotic");
  const total = 46;
  let cursor = now;
  const all = Array.from({ length: total }, (_, i) => {
    if (i > 0) cursor -= int(20, 90) * 60_000; // strictly decreasing -> newest first
    const occurredAt = cursor;
    const roll = i % 4;
    if (roll === 3) {
      const evt = pick(ACTIVITY_WORLD_EVENTS);
      return { id: total - i, serverId: "abiotic", occurredAt, observedAt: occurredAt + 1500, type: "world.event", message: evt.message, messageZhHant: evt.messageZhHant };
    }
    const p = ABIOTIC_PLAYERS[i % ABIOTIC_PLAYERS.length];
    if (roll === 2) {
      const cause = ABIOTIC_DEATH_CAUSES[i % ABIOTIC_DEATH_CAUSES.length];
      return {
        id: total - i, serverId: "abiotic", occurredAt, observedAt: occurredAt + 1200, type: "player.died",
        playerId: p.steamId, playerName: p.playerName, deathCause: cause.cause, deathCauseZhHant: cause.causeZhHant,
        message: `${p.playerName} died`, messageZhHant: `${p.playerName} 死亡`,
      };
    }
    return {
      id: total - i, serverId: "abiotic", occurredAt, observedAt: occurredAt + 800,
      type: roll === 0 ? "player.joined" : "player.left", playerId: p.steamId, playerName: p.playerName,
    };
  });
  const start = (page - 1) * pageSize;
  const data = all.slice(start, start + pageSize);
  return { data, pagination: { page, pageSize, total, hasMore: start + pageSize < total } };
}

function buildSessionsPage(now, page, pageSize, playerKeyFilter) {
  if (isEmptied("games")) return { data: [], pagination: { page, pageSize, total: 0, hasMore: false } };
  let idCounter = 1000;
  const sessions = [];
  for (const p of ABIOTIC_PLAYERS) {
    const { int } = rngStream(`sessions-${p.steamId}`);
    const closed = int(3, 6);
    let cursor = now - int(1, 20) * DAY_MS;
    for (let i = 0; i < closed; i++) {
      const joinedAt = cursor;
      const durationMs = int(20, 180) * 60_000;
      sessions.push({
        id: idCounter++, serverId: "abiotic", playerKey: abioticPlayerKey(p), playerId: p.steamId, playerName: p.playerName,
        joinedAt, leftAt: joinedAt + durationMs, closedReason: "left", durationMs,
      });
      cursor = joinedAt + durationMs + int(1, 8) * HOUR_MS;
    }
    if (p.online) {
      sessions.push({
        id: idCounter++, serverId: "abiotic", playerKey: abioticPlayerKey(p), playerId: p.steamId, playerName: p.playerName,
        joinedAt: now - int(10, 90) * 60_000, leftAt: null, closedReason: null, durationMs: null,
      });
    }
  }
  sessions.sort((a, b) => b.joinedAt - a.joinedAt);
  const filtered = playerKeyFilter ? sessions.filter((s) => s.playerKey === playerKeyFilter) : sessions;
  const total = filtered.length;
  const start = (page - 1) * pageSize;
  const data = filtered.slice(start, start + pageSize);
  return { data, pagination: { page, pageSize, total, hasMore: start + pageSize < total } };
}

// ==========================================================================
// 14. Router
// ==========================================================================

function compileRoute(pattern) {
  const keys = [];
  const regexStr = pattern
    .split("/")
    .map((seg) => {
      if (seg.startsWith(":")) {
        keys.push(seg.slice(1));
        return "([^/]+)";
      }
      return seg.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    })
    .join("/");
  return { regex: new RegExp(`^${regexStr}$`), keys };
}

const ROUTE_TABLE = [];
function get(pattern, fn) {
  ROUTE_TABLE.push({ ...compileRoute(pattern), fn });
}

function intParam(searchParams, name, fallback) {
  const raw = searchParams.get(name);
  if (raw === null) return { value: fallback, error: false };
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) ? { value: n, error: false } : { value: fallback, error: true };
}

function boolParam(searchParams, name) {
  const raw = searchParams.get(name);
  if (raw === null) return { value: null, error: false };
  if (raw === "true") return { value: true, error: false };
  if (raw === "false") return { value: false, error: false };
  return { value: null, error: true };
}

// -------------------------------------------------------------- nora herself

get("/app/v1/status", (params, sp, now) => {
  if (isFailing("status")) return { status: 503, body: ERR_UNAVAILABLE };
  return { status: 200, body: buildStatus(now) };
});

get("/app/v1/memory", (params, sp, now) => {
  if (isFailing("memory")) return { status: 503, body: ERR_UNAVAILABLE };
  return { status: 200, body: buildMemory(now, sp.get("personId")) };
});

get("/app/v1/doctrine", () => {
  if (isFailing("doctrine")) return { status: 503, body: ERR_UNAVAILABLE };
  return { status: 200, body: isEmptied("doctrine") ? [] : DOCTRINE };
});

get("/app/v1/skills", () => {
  if (isFailing("skills")) return { status: 503, body: ERR_UNAVAILABLE };
  return { status: 200, body: isEmptied("skills") ? [] : SKILLS };
});

get("/app/v1/tools", () => {
  if (isFailing("tools")) return { status: 503, body: ERR_UNAVAILABLE };
  return { status: 200, body: isEmptied("tools") ? [] : TOOLS };
});

// -------------------------------------------------------------- persons

get("/app/v1/persons", () => {
  if (isFailing("persons")) return { status: 503, body: ERR_UNAVAILABLE };
  return { status: 200, body: buildRoster() };
});

get("/app/v1/persons/:personId", (params) => {
  if (isFailing("persons")) return { status: 503, body: ERR_UNAVAILABLE };
  const body = buildPersonProfile(params.personId);
  if (body === null) return { status: 404, body: ERR_NOT_FOUND };
  return { status: 200, body };
});

// -------------------------------------------------------------- events

get("/app/v1/events", (params, sp, now) => {
  if (isFailing("events")) return { status: 503, body: ERR_UNAVAILABLE };
  const personId = sp.get("personId");
  if (!personId || !ROSTER_BY_ID.has(personId)) {
    return { status: 400, body: { error: "personId required" } };
  }
  return { status: 200, body: buildEventsList(now, personId) };
});

get("/app/v1/events/:id", (params, sp, now) => {
  if (isFailing("events")) return { status: 503, body: ERR_UNAVAILABLE };
  const personId = sp.get("personId");
  if (!personId || !ROSTER_BY_ID.has(personId)) {
    return { status: 400, body: { error: "personId required" } };
  }
  // Like nora/web/api.py events_detail: an id that is not a UUID is a 400.
  if (!/^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32})$/i.test(params.id)) {
    return { status: 400, body: { status: "refused", note: "bad id" } };
  }
  const body = buildEventDetail(now, personId, params.id);
  if (body === null) return { status: 404, body: ERR_NOT_FOUND };
  return { status: 200, body };
});

// ------------------------------------------------------------ documents
//
// nora/web/documents.py: what the viewer created plus what a room that names them
// made. Three fixtures for Miles (one revised, one whose stored spec this build refuses)
// and one only Mei can read, so a hidden document answers exactly like an unknown id.
// The picture is one real 1x1 PNG served under a fixed sha256.

const DOC_PICTURE_SHA = "b".repeat(64);
const DOC_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==",
  "base64",
);
const DOC_SPEC = (title, extra = []) => ({
  version: 1,
  root: {
    type: "Page",
    props: { title, subtitle: "Made for the group chat" },
    children: [
      {
        type: "Section",
        props: { title: "Notes" },
        children: [{ type: "Markdown", props: { text: "Three nights, one train, no rush." } }],
      },
      ...extra,
    ],
  },
});
const DOCUMENTS = [
  {
    id: "5b6f0c3a-2d41-4e8b-9c77-1a2b3c4d5e01",
    title: "Lisbon in December",
    revision: 3,
    platform: "signal",
    createdAt: "2026-09-28T09:00:00Z",
    updatedAt: "2026-09-30T18:30:00Z",
    createdBy: { personId: "ken", name: "Ken" },
    readers: ["miles", "ken", "mei"],
    media: [DOC_PICTURE_SHA],
    document: DOC_SPEC("Lisbon in December", [
      { type: "Image", props: { media: DOC_PICTURE_SHA, alt: "The hotel door", caption: "Where we are staying" } },
      // A scroll region that owns its keys in the present view (design/artifact §34).
      { type: "Code", props: { language: "text", text: "Train 07:10 Lisbon Santa Apolonia" } },
    ]),
  },
  {
    id: "5b6f0c3a-2d41-4e8b-9c77-1a2b3c4d5e02",
    title: "Kitchen rota",
    revision: 1,
    platform: "discord",
    createdAt: "2026-09-25T10:00:00Z",
    updatedAt: "2026-09-25T10:00:00Z",
    createdBy: { personId: "miles", name: "Miles" },
    readers: ["miles"],
    media: [],
    // nora/web/documents.py `roomLabel`: the operator's label, only for a viewer in that room.
    roomLabel: "Flat chat",
    text: "Who does the dishes on which night",
    document: DOC_SPEC("Kitchen rota"),
  },
  {
    id: "5b6f0c3a-2d41-4e8b-9c77-1a2b3c4d5e03",
    title: "From a newer catalog",
    revision: 1,
    platform: "signal",
    createdAt: "2026-09-20T10:00:00Z",
    updatedAt: "2026-09-20T10:00:00Z",
    createdBy: { personId: "miles", name: "Miles" },
    readers: ["miles"],
    media: [],
    document: { version: 9, root: { type: "Page", props: { title: "From a newer catalog" } } },
  },
  {
    id: "5b6f0c3a-2d41-4e8b-9c77-1a2b3c4d5e04",
    title: "Mei's surprise",
    revision: 1,
    platform: "signal",
    createdAt: "2026-09-19T10:00:00Z",
    updatedAt: "2026-09-19T10:00:00Z",
    createdBy: { personId: "mei", name: "Mei" },
    readers: ["mei"],
    media: [],
    document: DOC_SPEC("Mei's surprise"),
  },
  // A distinct handle (9e1d2c3b4a5f) so a link's 12-hex handle resolves to exactly one
  // document Miles may read. The four above all share the handle 5b6f0c3a2d41, so that
  // handle is the collision case: among what Miles reads it names three documents.
  {
    id: "9e1d2c3b-4a5f-4617-8899-aabbccddeeff",
    title: "Saturday packing list",
    revision: 1,
    platform: "signal",
    createdAt: "2026-09-10T10:00:00Z",
    updatedAt: "2026-09-10T10:00:00Z",
    createdBy: { personId: "miles", name: "Miles" },
    readers: ["miles"],
    // A picture and a diagram, so a link's handle is shown to reach both (the picture by
    // the document's UUID) and a dark page prints its diagram redrawn light.
    media: [DOC_PICTURE_SHA],
    document: DOC_SPEC("Saturday packing list", [
      { type: "Image", props: { media: DOC_PICTURE_SHA, alt: "The bag", caption: "Packed the night before" } },
      { type: "Mermaid", props: { source: "flowchart LR\n  A[Pack] --> B[Go]", caption: "Pack, then go" } },
    ]),
  },
];
// nora/web/documents.py: a document's lifecycle state, the latest change wins and none
// is `active`. A deleted document is in no list and every read of it is a 404.
const DOC_STATES = new Map();
const docState = (d) => DOC_STATES.get(d.id) ?? "active";
/** Every document back to `active` (tests share this module's state). */
export const resetDocumentStates = () => DOC_STATES.clear();
// A restore (below) makes a new current revision; the list and the detail read it. The
// search text is never sent (nora/web/documents.py).
const docHead = (d) => DOC_RESTORES.get(d.id)?.at(-1) ?? null;
const docSummary = ({ readers: _r, media: _m, document: _d, text: _t, ...rest }) => {
  const head = docHead(rest);
  const moved = head ? { revision: head.revision, title: head.title, updatedAt: head.recordedAt } : {};
  return { roomLabel: "", ...rest, ...moved, state: docState(rest) };
};
const docSpec = (d) => docHead(d)?.document ?? d.document;
const DOC_HANDLE = /^[0-9a-f]{12}$/;
const DOC_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function documentViewer(sp) {
  const personId = sp.get("personId");
  return personId && ROSTER_BY_ID.has(personId) ? personId : null;
}

get("/app/v1/documents", (params, sp) => {
  if (isFailing("documents")) return { status: 503, body: ERR_UNAVAILABLE };
  const personId = documentViewer(sp);
  if (!personId) return { status: 400, body: { error: "personId required" } };
  const { value: page, error: pageErr } = intParam(sp, "page", 1);
  const { value: pageSize, error: sizeErr } = intParam(sp, "pageSize", 20);
  if (pageErr) return { status: 400, body: { error: "invalid page" } };
  if (sizeErr) return { status: 400, body: { error: "invalid pageSize" } };
  const listed = sp.get("state") ?? "active";
  if (listed !== "active" && listed !== "archived") return { status: 400, body: { error: "invalid state" } };
  // nora/web/api.py: `q` is a case-insensitive substring of the title or text, collapsed and
  // trimmed, and one over 200 characters is a 400 (never cut).
  const rawQuery = sp.get("q") ?? "";
  if (rawQuery.length > 200) return { status: 400, body: { error: "invalid q" } };
  const needle = rawQuery.split(/\s+/).filter(Boolean).join(" ").toLowerCase();
  const matches = (d) => !needle || d.title.toLowerCase().includes(needle) || (d.text ?? "").toLowerCase().includes(needle);
  const mine = isEmptied("documents")
    ? []
    : DOCUMENTS.filter((d) => d.readers.includes(personId) && docState(d) === listed && matches(d)).sort((a, b) =>
        b.updatedAt.localeCompare(a.updatedAt),
      );
  const size = Math.min(Math.max(1, pageSize), 50);
  const at = Math.max(1, page);
  return {
    status: 200,
    body: {
      documents: mine.slice((at - 1) * size, at * size).map(docSummary),
      page: at,
      pageSize: size,
      total: mine.length,
      state: listed,
      query: needle,
    },
  };
});

get("/app/v1/documents/:id", (params, sp) => {
  if (isFailing("documents")) return { status: 503, body: ERR_UNAVAILABLE };
  const personId = documentViewer(sp);
  if (!personId) return { status: 400, body: { error: "personId required" } };
  // Nora's rule (nora/web/api.py documents_detail): a 12-hex handle is matched by id
  // prefix among the documents the viewer may read ONLY, and zero or several matches are
  // the same 404 as an unreadable document.
  if (DOC_HANDLE.test(params.id)) {
    const readable = DOCUMENTS.filter(
      (d) =>
        d.readers.includes(personId) && docState(d) !== "deleted" && d.id.replaceAll("-", "").startsWith(params.id),
    );
    if (readable.length !== 1) return { status: 404, body: { status: "not_found" } };
    const [only] = readable;
    return { status: 200, body: { ...docSummary(only), document: docSpec(only), media: only.media } };
  }
  if (!DOC_UUID.test(params.id)) return { status: 400, body: { status: "refused", note: "bad id" } };
  const doc = DOCUMENTS.find((d) => d.id === params.id);
  // Unreadable is unknown: never a distinct "forbidden". Deleted is unknown too.
  if (!doc || !doc.readers.includes(personId) || docState(doc) === "deleted") {
    return { status: 404, body: { status: "not_found" } };
  }
  return { status: 200, body: { ...docSummary(doc), document: docSpec(doc), media: doc.media } };
});

get("/app/v1/documents/:id/media/:sha", (params, sp) => {
  if (isFailing("documents")) return { status: 503, body: ERR_UNAVAILABLE };
  const personId = documentViewer(sp);
  if (!personId) return { status: 400, body: { error: "personId required" } };
  if (!DOC_UUID.test(params.id)) return { status: 400, body: { status: "refused", note: "bad id" } };
  const doc = DOCUMENTS.find((d) => d.id === params.id);
  if (!doc || !doc.readers.includes(personId) || docState(doc) === "deleted" || !doc.media.includes(params.sha)) {
    return { status: 404, body: { status: "not_found" } };
  }
  return { status: 200, bytes: DOC_PNG, contentType: "image/png" };
});

// nora/web/document_history.py: every revision is a full snapshot, read through the
// document's own gate. A fixture's earlier revisions are its current spec with an earlier
// note, written by Nora; a restore appends one more, by the person, and never rewrites.
const DOC_RESTORES = new Map();
/** Every restore undone (tests share this module's state). */
export const resetDocumentRestores = () => DOC_RESTORES.clear();

function docRevisions(d) {
  const base = Array.from({ length: d.revision }, (_, i) => {
    const n = i + 1;
    const current = n === d.revision;
    const day = Date.parse(d.createdAt) + i * 86_400_000;
    return {
      revision: n,
      title: d.title,
      sha256: current ? "f".repeat(64) : String(n).repeat(64),
      recordedAt: current ? d.updatedAt : new Date(day).toISOString().replace(".000Z", "Z"),
      writtenBy: { kind: "nora" },
      document: current
        ? d.document
        : {
            ...d.document,
            root: {
              ...d.document.root,
              children: [
                { type: "Section", props: { title: "Notes" }, children: [{ type: "Markdown", props: { text: `Draft ${n}.` } }] },
                ...(d.document.root?.children ?? []).slice(1),
              ],
            },
          },
      media: d.media,
    };
  });
  return [...base, ...(DOC_RESTORES.get(d.id) ?? [])];
}

function readableDocument(params, sp) {
  const personId = documentViewer(sp);
  if (!personId) return { error: { status: 400, body: { error: "personId required" } } };
  if (!DOC_UUID.test(params.id)) return { error: { status: 400, body: { status: "refused", note: "bad id" } } };
  const doc = DOCUMENTS.find((d) => d.id === params.id);
  if (!doc || !doc.readers.includes(personId) || docState(doc) === "deleted") {
    return { error: { status: 404, body: { status: "not_found" } } };
  }
  return { doc, personId };
}

get("/app/v1/documents/:id/revisions", (params, sp) => {
  if (isFailing("documents")) return { status: 503, body: ERR_UNAVAILABLE };
  const { doc, error } = readableDocument(params, sp);
  if (error) return error;
  const all = docRevisions(doc);
  return {
    status: 200,
    body: {
      id: doc.id,
      title: all.at(-1).title,
      revision: all.length,
      state: docState(doc),
      revisions: all.map(({ document: _d, media: _m, ...r }) => r).reverse(),
    },
  };
});

get("/app/v1/documents/:id/revisions/:n", (params, sp) => {
  if (isFailing("documents")) return { status: 503, body: ERR_UNAVAILABLE };
  const { doc, error } = readableDocument(params, sp);
  if (error) return error;
  if (!/^[1-9][0-9]*$/.test(params.n)) return { status: 400, body: { status: "refused", note: "bad id" } };
  const all = docRevisions(doc);
  const found = all[Number(params.n) - 1];
  if (!found) return { status: 404, body: { status: "not_found" } };
  return { status: 200, body: { id: doc.id, currentRevision: all.length, state: docState(doc), ...found } };
});

// POST /app/v1/documents/:id/revisions/:n/restore (nora/web/document_history.py).
export function handleDocumentRestore(pathname, sp, payload) {
  const m = /^\/app\/v1\/documents\/([^/]+)\/revisions\/([^/]+)\/restore$/.exec(pathname);
  if (!m) return null;
  if (isFailing("documents")) return { status: 503, body: ERR_UNAVAILABLE };
  const { doc, personId, error } = readableDocument({ id: m[1] }, sp);
  if (error) return error;
  const all = docRevisions(doc);
  const old = all[Number(m[2]) - 1];
  if (!/^[1-9][0-9]*$/.test(m[2]) || !old) return { status: 404, body: { status: "not_found" } };
  const base = payload?.baseRevision;
  if (!Number.isInteger(base) || base < 1) return { status: 400, body: { status: "refused" } };
  if (old.revision === all.length) return { status: 400, body: { status: "refused" } };
  if (docState(doc) !== "active") return { status: 409, body: { status: "not_active" } };
  if (base !== all.length) return { status: 409, body: { status: "conflict" } };
  const person = ROSTER_BY_ID.get(personId);
  const restored = {
    ...old,
    revision: all.length + 1,
    recordedAt: new Date().toISOString(),
    writtenBy: { kind: "person", personId, name: person?.displayName ?? personId },
  };
  DOC_RESTORES.set(doc.id, [...(DOC_RESTORES.get(doc.id) ?? []), restored]);
  return { status: 200, body: { status: "ok", revision: restored.revision } };
}

// -------------------------------------------------------------- presence

get("/app/v1/presence", (params, sp, now) => {
  if (isFailing("presence")) return { status: 503, body: ERR_UNAVAILABLE };
  return { status: 200, body: buildPresenceList(now) };
});

get("/app/v1/presence/places", () => {
  if (isFailing("presence")) return { status: 503, body: ERR_UNAVAILABLE };
  return { status: 200, body: buildSavedPlaces() };
});

get("/app/v1/presence/analytics/places", (params, sp, now) => {
  if (isFailing("presence")) return { status: 503, body: ERR_UNAVAILABLE };
  return { status: 200, body: buildDerivedPlaces(now) };
});

get("/app/v1/presence/analytics/rhythm", (params, sp, now) => {
  if (isFailing("presence")) return { status: 503, body: ERR_UNAVAILABLE };
  const { value: days, error } = intParam(sp, "days", 30);
  if (error || days <= 0) return { status: 400, body: { error: "invalid days" } };
  return { status: 200, body: buildRhythm(now, sp.get("subjectId"), Math.min(days, 90)) };
});

get("/app/v1/presence/analytics/routes", (params, sp, now) => {
  if (isFailing("presence")) return { status: 503, body: ERR_UNAVAILABLE };
  const { value: days, error } = intParam(sp, "days", 30);
  if (error || days <= 0) return { status: 400, body: { error: "invalid days" } };
  return { status: 200, body: buildRoutes(now, sp.get("subjectId"), Math.min(days, 90)) };
});

get("/app/v1/presence/:personId/history", (params, sp, now) => {
  if (isFailing("presence")) return { status: 503, body: ERR_UNAVAILABLE };
  const { value: hours, error } = intParam(sp, "hours", 24);
  if (error || hours <= 0) return { status: 400, body: { error: "invalid hours" } };
  return { status: 200, body: buildPresenceHistory(now, params.personId, hours) };
});

// -------------------------------------------------------------- games

get("/app/v1/games/servers", (params, sp, now) => {
  if (isFailing("games")) return { status: 503, body: ERR_UNAVAILABLE };
  return { status: 200, body: buildGamesServers(now) };
});

const KNOWN_SERVER_IDS = new Set(["abiotic", "bigwalk", "palworld"]);

get("/app/v1/games/servers/:id/status", (params, sp, now) => {
  if (isFailing("games")) return { status: 503, body: ERR_UNAVAILABLE };
  if (!KNOWN_SERVER_IDS.has(params.id)) return { status: 404, body: ERR_NOT_FOUND };
  return { status: 200, body: buildServerStatus(now, params.id) };
});

get("/app/v1/games/servers/:id/graph", (params, sp, now) => {
  if (isFailing("games")) return { status: 503, body: ERR_UNAVAILABLE };
  if (!KNOWN_SERVER_IDS.has(params.id)) return { status: 404, body: ERR_NOT_FOUND };
  const { value: hours, error } = intParam(sp, "hours", 24);
  if (error) return { status: 400, body: { error: "invalid hours" } };
  return { status: 200, body: buildGraph(now, params.id, hours) };
});

get("/app/v1/games/servers/:id/weekly", (params, sp, now) => {
  if (isFailing("games")) return { status: 503, body: ERR_UNAVAILABLE };
  if (params.id === "bigwalk") return { status: 404, body: ERR_NOT_FOUND };
  if (!KNOWN_SERVER_IDS.has(params.id)) return { status: 404, body: ERR_NOT_FOUND };
  return { status: 200, body: buildWeekly(now, params.id) };
});

get("/app/v1/games/servers/:id/world", (params, sp, now) => {
  if (isFailing("games")) return { status: 503, body: ERR_UNAVAILABLE };
  if (params.id === "abiotic") return { status: 200, body: buildAbioticWorld(now) };
  if (params.id === "bigwalk") return { status: 200, body: buildBigwalkWorld(now) };
  if (params.id === "palworld") return { status: 200, body: buildPalworldWorld(now) };
  return { status: 404, body: ERR_NOT_FOUND };
});

get("/app/v1/games/servers/:id/progress", (params, sp, now) => {
  if (isFailing("games")) return { status: 503, body: ERR_UNAVAILABLE };
  if (params.id !== "bigwalk") return { status: 404, body: ERR_NOT_FOUND };
  return { status: 200, body: buildProgress(now) };
});

get("/app/v1/games/servers/:id/save-history", (params, sp, now) => {
  if (isFailing("games")) return { status: 503, body: ERR_UNAVAILABLE };
  if (params.id !== "bigwalk") return { status: 404, body: ERR_NOT_FOUND };
  const { value: hours, error } = intParam(sp, "hours", 24);
  if (error) return { status: 400, body: { error: "invalid hours" } };
  return { status: 200, body: buildSaveHistory(now, hours) };
});

get("/app/v1/games/servers/:id/insights", (params, sp, now) => {
  if (isFailing("games")) return { status: 503, body: ERR_UNAVAILABLE };
  if (params.id === "bigwalk") return { status: 404, body: ERR_NOT_FOUND };
  if (!KNOWN_SERVER_IDS.has(params.id)) return { status: 404, body: ERR_NOT_FOUND };
  const { value: days, error } = intParam(sp, "days", 30);
  if (error) return { status: 400, body: { error: "invalid days" } };
  return { status: 200, body: buildInsights(now, params.id, days) };
});

get("/app/v1/games/servers/:id/players", (params, sp, now) => {
  if (isFailing("games")) return { status: 503, body: ERR_UNAVAILABLE };
  if (params.id === "bigwalk") {
    // Pulse answers a local-save game's container-only lists with a
    // well-formed empty page, not an error (the old portal's game page
    // documents this); the UI hides those tabs for local saves.
    return { status: 200, body: { data: [], pagination: { page: 1, pageSize: 20, total: 0, hasMore: false } } };
  }

  if (!KNOWN_SERVER_IDS.has(params.id)) return { status: 404, body: ERR_NOT_FOUND };
  const { value: page, error: pageErr } = intParam(sp, "page", 1);
  const { value: pageSize, error: sizeErr } = intParam(sp, "pageSize", 20);
  const { value: online, error: onlineErr } = boolParam(sp, "online");
  if (pageErr) return { status: 400, body: { error: "invalid page" } };
  if (sizeErr) return { status: 400, body: { error: "invalid pageSize" } };
  if (onlineErr) return { status: 400, body: { error: "invalid online" } };
  if (params.id === "palworld") return { status: 200, body: { data: [], pagination: { page, pageSize, total: 0, hasMore: false } } };
  return { status: 200, body: buildPlayersPage(now, page, pageSize, online) };
});

get("/app/v1/games/servers/:id/players/:playerKey", (params, sp, now) => {
  if (isFailing("games")) return { status: 503, body: ERR_UNAVAILABLE };
  if (!KNOWN_SERVER_IDS.has(params.id) || params.id === "bigwalk") return { status: 404, body: ERR_NOT_FOUND };
  const body = buildPlayerDetail(now, params.playerKey);
  if (body === null) return { status: 404, body: ERR_NOT_FOUND };
  return { status: 200, body };
});

get("/app/v1/games/servers/:id/activity", (params, sp, now) => {
  if (isFailing("games")) return { status: 503, body: ERR_UNAVAILABLE };
  if (!KNOWN_SERVER_IDS.has(params.id)) return { status: 404, body: ERR_NOT_FOUND };
  const { value: page, error: pageErr } = intParam(sp, "page", 1);
  const { value: pageSize, error: sizeErr } = intParam(sp, "pageSize", 20);
  if (pageErr) return { status: 400, body: { error: "invalid page" } };
  if (sizeErr) return { status: 400, body: { error: "invalid pageSize" } };
  // A local-save game's activity is the events its save diffs produce.
  if (params.id === "bigwalk") return { status: 200, body: buildBigwalkActivity(now, page, pageSize) };
  if (params.id === "palworld") return { status: 200, body: { data: [], pagination: { page, pageSize, total: 0, hasMore: false } } };
  return { status: 200, body: buildActivityPage(now, page, pageSize) };
});

get("/app/v1/games/servers/:id/sessions", (params, sp, now) => {
  if (isFailing("games")) return { status: 503, body: ERR_UNAVAILABLE };
  if (params.id === "bigwalk") {
    // Pulse answers a local-save game's container-only lists with a
    // well-formed empty page, not an error (the old portal's game page
    // documents this); the UI hides those tabs for local saves.
    return { status: 200, body: { data: [], pagination: { page: 1, pageSize: 20, total: 0, hasMore: false } } };
  }

  if (!KNOWN_SERVER_IDS.has(params.id)) return { status: 404, body: ERR_NOT_FOUND };
  const { value: page, error: pageErr } = intParam(sp, "page", 1);
  const { value: pageSize, error: sizeErr } = intParam(sp, "pageSize", 20);
  if (pageErr) return { status: 400, body: { error: "invalid page" } };
  if (sizeErr) return { status: 400, body: { error: "invalid pageSize" } };
  if (params.id === "palworld") return { status: 200, body: { data: [], pagination: { page, pageSize, total: 0, hasMore: false } } };
  return { status: 200, body: buildSessionsPage(now, page, pageSize, sp.get("playerKey")) };
});

// ==========================================================================
// 15. handle() — the pure entry point the HTTP server and the test file
//     both call.
// ==========================================================================

export function handle(pathname, searchParams, now = Date.now()) {
  const cleanPath = pathname.length > 1 && pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
  for (const route of ROUTE_TABLE) {
    const m = route.regex.exec(cleanPath);
    if (!m) continue;
    const params = {};
    route.keys.forEach((key, i) => {
      params[key] = decodeURIComponent(m[i + 1]);
    });
    try {
      return route.fn(params, searchParams, now);
    } catch {
      // A handler must never throw into the HTTP layer — an ERROR is
      // never silently a PASS. Surfaced as 503, same as a configured
      // MOCK_FAIL, rather than an unhandled exception closing the socket.
      return { status: 503, body: ERR_UNAVAILABLE };
    }
  }
  return { status: 404, body: ERR_NOT_FOUND };
}

// ==========================================================================
// 16. HTTP server bootstrap — only when this file is run directly
//     (`node dev/mock-nora.mjs`), never on import (the test file imports
//     `handle` without opening a socket).
// ==========================================================================

function jitteredLatencyMs() {
  const jitter = LATENCY_MS * 0.4;
  return Math.max(0, LATENCY_MS + (Math.random() * 2 - 1) * jitter);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function readJson(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf-8"));
  } catch {
    return null;
  }
}

// POST /app/v1/documents/:id/state (nora/web/api.py documents_state): the viewer who may
// read a document may archive, delete or restore it; a deleted one is a 404 here.
export function handleDocumentState(pathname, sp, payload) {
  const m = /^\/app\/v1\/documents\/([^/]+)\/state$/.exec(pathname);
  if (!m) return { status: 404, body: ERR_NOT_FOUND };
  if (isFailing("documents")) return { status: 503, body: ERR_UNAVAILABLE };
  const personId = documentViewer(sp);
  if (!personId) return { status: 400, body: { error: "personId required" } };
  if (!DOC_UUID.test(m[1])) return { status: 400, body: { status: "refused", note: "bad id" } };
  const state = payload?.state;
  if (!["active", "archived", "deleted"].includes(state)) {
    return { status: 400, body: { status: "refused", note: "bad state" } };
  }
  const doc = DOCUMENTS.find((d) => d.id === m[1]);
  if (!doc || !doc.readers.includes(personId) || docState(doc) === "deleted") {
    return { status: 404, body: { status: "not_found" } };
  }
  DOC_STATES.set(doc.id, state);
  return { status: 200, body: docSummary(doc) };
}

// One-time codes (DEC-287, nora/web/otp.py), dev only. The real routes need a
// bearer token and send a real message; this stands in for both. Like Nora,
// it resolves a phone number on the chosen platform from identity.dev.toml
// (Miles is +852 0000 0001), never a person_id; the code is always 123456 and
// is printed to the console as if it had been sent. An unknown number still
// gets a challenge, as the real API answers, and its code never verifies.
const DEV_CODE = "123456";
const OTP_CHALLENGES = new Map();
let otpIssued = 0;

export function handleOtp(pathname, payload) {
  if (pathname === "/app/v1/otp/request") {
    otpIssued += 1;
    const challengeId = `dev-${otpIssued}`;
    const typed = String(payload?.identifier ?? "").replace(/[\s\-().]/g, "");
    const platform = String(payload?.platform ?? "");
    const personId =
      Object.entries(identityToml.persons ?? {}).find(([, p]) =>
        (p[platform] ?? []).some((block) => block.phone === typed),
      )?.[0] ?? null;
    OTP_CHALLENGES.set(challengeId, { personId, purpose: payload?.purpose ?? "sign_in" });
    if (personId) console.log(`[mock-nora] one-time code for ${personId}: ${DEV_CODE}`);
    return { status: 202, body: { challengeId } };
  }
  if (pathname === "/app/v1/otp/verify") {
    const challenge = OTP_CHALLENGES.get(String(payload?.challengeId ?? ""));
    const purpose = payload?.purpose ?? "sign_in";
    if (challenge?.personId && challenge.purpose === purpose && payload?.code === DEV_CODE) {
      OTP_CHALLENGES.delete(payload.challengeId);
      return { status: 200, body: { personId: challenge.personId } };
    }
    return { status: 401, body: { error: "refused" } };
  }
  return null;
}

function startServer() {
  const server = createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", "http://127.0.0.1");
    // Dev only, not part of Nora's API: put every document back to `active`, so an e2e that
    // deleted one (the portal cannot restore it) leaves the fixtures as it found them.
    if (req.method === "POST" && url.pathname === "/__mock/documents/reset") {
      resetDocumentStates();
      resetDocumentRestores();
      res.writeHead(204);
      res.end();
      return;
    }
    const { status, body, bytes, contentType } =
      req.method === "GET"
        ? handle(url.pathname, url.searchParams, Date.now())
        : req.method === "POST"
          ? await readJson(req).then(
              (payload) =>
                handleOtp(url.pathname, payload) ??
                handleDocumentRestore(url.pathname, url.searchParams, payload) ??
                handleDocumentState(url.pathname, url.searchParams, payload),
            )
          : { status: 404, body: ERR_NOT_FOUND };
    await sleep(jitteredLatencyMs());
    if (bytes) {
      // A picture: the only non-JSON answer this mock gives.
      res.writeHead(status, { "content-type": contentType });
      res.end(bytes);
    } else {
      res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
      res.end(JSON.stringify(body));
    }
    console.log(`[mock-nora] ${req.method} ${url.pathname}${url.search} -> ${status}`);
  });
  server.listen(PORT, "127.0.0.1", () => {
    console.log(`[mock-nora] listening on http://127.0.0.1:${PORT} (app/v1/*)`);
    const failing = [...familySet("MOCK_FAIL")];
    const empty = [...familySet("MOCK_EMPTY")];
    if (failing.length) console.log(`[mock-nora] MOCK_FAIL: ${failing.join(", ")}`);
    if (empty.length) console.log(`[mock-nora] MOCK_EMPTY: ${empty.join(", ")}`);
  });
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) startServer();
