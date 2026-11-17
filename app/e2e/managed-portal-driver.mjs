// Generated browser actuator for the managed-portal pytest journey
// (tests/integration/test_managed_portal_browser.py). It is NOT a Playwright
// test: pytest owns every assertion; this process only exposes a real
// Chromium over newline-delimited JSON on stdio — one command line in, one
// result line out — so the pytest loop can interleave browser steps with its
// own SQL/Ory/HTTP reads against the same disposable services.
//
// Commands (each a JSON object on one line, every reply echoes `id`):
//   new        {name, headers?, noSw?}               -> {}
//   goto       {name, url, timeout?}                 -> {status, url, title}
//   fill       {name, label, value}                  -> {}
//   click_text {name, text, exact?}                  -> {}
//   click_role {name, role, text, exact?}            -> {}
//   submit_form{name, selector, field?, value?}      -> {}
//   wait_url   {name, regex, timeout?}               -> {matched, url}
//   wait_text  {name, text, timeout?}                -> {visible}
//   visible    {name, text, exact?}                  -> {visible}
//   url        {name}                                -> {url}
//   eval       {name, expression}                    -> {value}
//   html       {name}                                -> {html}
//   api        {name, method, url, body?, headers?}  -> {status, body}
//   cookies    {name}                                -> {cookies:[{name,domain,path}]}
//   responses  {name}                                -> {responses:[{url,status,body?}]}
//   pageerrors {name}                                -> {errors:[string]}
//   screenshot {name, path}                          -> {}
//   shutdown   {}                                    -> {}

import { createInterface } from "node:readline";
import { chromium } from "playwright-core";

const CAP = 200_000;

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.NORA_CHROMIUM_PATH || undefined,
});

const contexts = new Map();

function write(id, payload) {
  process.stdout.write(`${JSON.stringify({ id, ...payload })}\n`);
}

function fail(id, error) {
  write(id, { ok: false, error: String(error?.message ?? error) });
}

async function open(name, headers, noSw) {
  if (contexts.has(name)) throw new Error(`context ${name} already open`);
  const context = await browser.newContext({
    extraHTTPHeaders: headers ?? {},
    viewport: { width: 1280, height: 900 },
    ...(noSw ? { serviceWorkers: "block" } : {}),
  });
  const page = await context.newPage();
  const entry = { context, page, responses: [], errors: [] };
  page.on("pageerror", (e) => entry.errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error" && !/GL Driver Message|WebGL/.test(m.text())) {
      entry.errors.push(`console: ${m.text()}`);
    }
  });
  context.on("response", async (res) => {
    try {
      const headers = res.headers();
      const record = { url: res.url(), status: res.status(),
        method: res.request().method() };
      if (headers["x-action-redirect"]) record.actionRedirect = headers["x-action-redirect"];
      if (headers.location) record.location = headers.location;
      const type = headers["content-type"] ?? "";
      // json for API evidence; x-component/text/plain carry the server-action
      // result or redirect payload the client acted on.
      if (type.includes("json") || type.includes("x-component")
          || type.includes("text/plain")) {
        const body = await res.text();
        record.body = body.length > CAP ? body.slice(0, CAP) : body;
      }
      entry.responses.push(record);
    } catch {
      // A raced/closed target's unreadable body is not evidence either way.
    }
  });
  contexts.set(name, entry);
}

function ctx(name) {
  const entry = contexts.get(name);
  if (!entry) throw new Error(`no context named ${name}`);
  return entry;
}

const handlers = {
  new: async (c) => {
    await open(c.name, c.headers, c.noSw);
    return {};
  },
  goto: async (c) => {
    const res = await ctx(c.name).page.goto(c.url, {
      waitUntil: "domcontentloaded",
      timeout: c.timeout ?? 60_000,
    });
    return { status: res?.status() ?? null, url: ctx(c.name).page.url(),
      title: await ctx(c.name).page.title() };
  },
  fill: async (c) => {
    await ctx(c.name).page.getByLabel(c.label).fill(c.value);
    return {};
  },
  click_text: async (c) => {
    await ctx(c.name).page.getByText(c.text, { exact: c.exact ?? true }).first().click();
    return {};
  },
  click_role: async (c) => {
    await ctx(c.name).page.getByRole(c.role, { name: c.text, exact: c.exact ?? true }).click();
    return {};
  },
  // Native (MPA) form submit: injects the submitter's name/value (form.submit()
  // carries no submitter), then posts without firing the submit event. Next 16
  // production builds intermittently never commit useActionState transitions
  // (vercel/next.js#97990), leaving hydrated clicks dead; the native POST runs
  // the identical server action and redirect, the no-JS path real browsers
  // take before hydration.
  submit_form: async (c) => {
    await ctx(c.name).page.evaluate(({ sel, fields }) => {
      const form = document.querySelector(sel);
      if (!form) throw new Error(`no form matching ${sel}`);
      // A dispatched-but-never-committed action still resets uncontrolled
      // fields, so every leg re-supplies its values here, not just `intent`.
      for (const [k, v] of Object.entries(fields)) {
        const radio = form.querySelector(
          `input[type=radio][name="${k}"][value="${v}"]`);
        if (radio) { radio.checked = true; continue; }
        let el = form.elements.namedItem(k);
        // A submit button can share the field's name ("intent"); a list or a
        // button must not shadow the hidden input the post needs.
        if (el instanceof RadioNodeList ||
            (el && !("value" in el)) ||
            (el && /^(submit|button|reset|image)$/.test(el.type))) {
          el = null;
        }
        if (el) { el.disabled = false; el.value = v; continue; }
        const input = document.createElement("input");
        input.type = "hidden"; input.name = k; input.value = v;
        form.appendChild(input);
      }
      HTMLFormElement.prototype.submit.call(form);
    }, {
      sel: c.selector,
      fields: c.fields ?? (c.field ? { [c.field]: c.value } : {}),
    });
    return {};
  },
  wait_url: async (c) => {
    try {
      // "commit" resolves on client-side router navigations too; the default
      // "load" never fires for a pushState switch like the post-sign-in redirect.
      await ctx(c.name).page.waitForURL(new RegExp(c.regex), {
        waitUntil: "commit",
        timeout: c.timeout ?? 30_000,
      });
      return { matched: true, url: ctx(c.name).page.url() };
    } catch {
      return { matched: false, url: ctx(c.name).page.url() };
    }
  },
  wait_text: async (c) => {
    try {
      // filter() narrows the matched set itself; a hidden live-region copy of
      // the same string must not shadow the rendered one.
      await ctx(c.name).page.getByText(c.text, { exact: false })
        .filter({ visible: true }).first()
        .waitFor({ state: "visible", timeout: c.timeout ?? 30_000 });
      return { visible: true };
    } catch {
      return { visible: false };
    }
  },
  visible: async (c) => {
    const loc = ctx(c.name).page.getByText(c.text, { exact: c.exact ?? false })
      .filter({ visible: true }).first();
    return { visible: await loc.isVisible().catch(() => false) };
  },
  url: async (c) => ({ url: ctx(c.name).page.url() }),
  eval: async (c) => ({
    value: await ctx(c.name).page.evaluate(c.expression).catch((e) => `ERR ${e}`),
  }),
  html: async (c) => ({ html: await ctx(c.name).page.content() }),
  api: async (c) => {
    const res = await ctx(c.name).context.request.fetch(c.url, {
      method: c.method ?? "GET",
      data: c.body,
      headers: c.headers,
      timeout: c.timeout ?? 30_000,
    });
    return { status: res.status(), body: await res.text() };
  },
  cookies: async (c) => ({
    cookies: (await ctx(c.name).context.cookies())
      .map(({ name, domain, path }) => ({ name, domain, path })),
  }),
  responses: async (c) => ({ responses: ctx(c.name).responses }),
  pageerrors: async (c) => ({ errors: ctx(c.name).errors }),
  screenshot: async (c) => {
    await ctx(c.name).page.screenshot({ path: c.path, fullPage: true });
    return {};
  },
  shutdown: async () => {
    setTimeout(() => process.exit(0), 50);
    return {};
  },
};

write(null, { ok: true, ready: true });

// Commands run strictly in arrival order: a later step (click, goto) depends
// on earlier state (the context being open, the page having navigated).
let chain = Promise.resolve();
createInterface({ input: process.stdin }).on("line", (line) => {
  chain = chain.then(async () => {
    let cmd;
    try {
      cmd = JSON.parse(line);
    } catch {
      return fail(null, "unparsable command line");
    }
    const handler = handlers[cmd.cmd];
    if (!handler) return fail(cmd.id, `unknown command ${cmd.cmd}`);
    try {
      write(cmd.id, { ok: true, result: await handler(cmd) });
    } catch (error) {
      fail(cmd.id, error);
    }
  });
});
