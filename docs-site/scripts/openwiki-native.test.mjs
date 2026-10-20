import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, readFile, writeFile, rm, mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { BaseChatModel } from '@langchain/core/language_models/chat_models';
import { AIMessage } from '@langchain/core/messages';

// These regressions use native stores and a scripted in-process model. No
// provider client is constructed; tracing and outbound fetch are disabled.
process.env.LANGCHAIN_TRACING_V2 = 'false';
process.env.LANGSMITH_TRACING = 'false';
globalThis.fetch = () => { throw new Error('Network is forbidden in the native regression'); };
const native = '../node_modules/openwiki/dist/';
const {
  beginRepositoryRun, submitRepositoryPlan, nextRepositoryPage,
  submitRepositoryPage, finishRepositoryRun,
} = await import(`${native}generation/repository-run.js`);
const { readRepositoryRunState } = await import(`${native}generation/run-state.js`);
const { readRepositoryPageManifest } = await import(`${native}generation/page-manifest.js`);
const { reconcilePageClaims } = await import(`${native}generation/page-jobs.js`);
const { ClaimsStore } = await import(`${native}claims/brains/code/store.js`);
const { runNativeRepositoryGeneration } = await import(`${native}agent/repository-runner.js`);
const { OpenWikiLocalShellBackend } = await import(`${native}agent/docs-only-backend.js`);
const { OPENWIKI_PRODUCER_ACTOR } = await import(`${native}version.js`);
const actor = { producerActor: OPENWIKI_PRODUCER_ACTOR, metadataModel: 'offline-fixture' };
const pageText = `---
type: guide
title: Synthetic fixture
description: A synthetic component for native lifecycle tests.
tags: [fixture]
---

DRAFT documents a synthetic component.
`;
const claim = {
  statement: 'README describes a synthetic component.',
  evidence: [{ resource: 'repo://README.md#L1-L2' }],
};

async function fixture(t, pages = ['a', 'b', 'quickstart']) {
  const root = await mkdtemp(path.join(tmpdir(), 'openwiki-native-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  // An unborn repository exercises the real source snapshot without commits.
  execFileSync('git', ['init', '-q', root]);
  await writeFile(path.join(root, 'README.md'), '# Fixture\nA synthetic component.\n');
  const begin = () => beginRepositoryRun({ root, mode: 'init', language: 'en', actor });
  const { run } = await begin();
  await submitRepositoryPlan(run, { pages: pages.map(name => ({
    path: `/openwiki/${name}.md`, title: name, purpose: `Describe ${name}.`,
    seedPaths: ['README.md'],
  })) });
  return { root, run, begin, store: new ClaimsStore(root) };
}

async function completeNext(run, claims = [claim]) {
  const { job } = await nextRepositoryPage(run);
  assert.equal((await run.backend.write(job.path, pageText)).error, undefined);
  await submitRepositoryPage(run, { jobId: job.id, claims });
  return job;
}

test('native resume reopens only changed completed Markdown and requires explicit Claims', async t => {
  const { root, run, begin, store } = await fixture(t);
  const a = await completeNext(run);
  const b = await completeNext(run);
  const original = structuredClone(run.state);
  const oldA = await store.loadPage(a.path);
  const bMarkdown = await store.readMarkdown(b.path);
  const bSidecar = await readFile(store.sidecarPath(b.path), 'utf8');
  const coverage = await readRepositoryPageManifest(root);
  const lateMarkdown = (await store.readMarkdown(a.path)).replace('DRAFT', 'LATE EDIT');
  await writeFile(path.join(root, a.path.slice(1)), lateMarkdown);

  const { run: resumed, view } = await begin();
  assert.equal(view.resumed, true);
  assert.deepEqual(resumed.state, {
    ...original,
    plan: { ...original.plan, pages: original.plan.pages.map(job =>
      job.id === a.id ? { ...job, status: 'pending' } : job) },
  }, 'only the changed job is reopened; run ID, source checkpoint and queue survive');
  assert.deepEqual(await readRepositoryRunState(root), resumed.state);
  assert.equal(await store.readMarkdown(a.path), lateMarkdown);
  assert.deepEqual(await store.loadPage(a.path), oldA, 'resume must not refresh the hash');
  assert.deepEqual(await readRepositoryPageManifest(root), coverage);
  const next = await nextRepositoryPage(resumed);
  assert.equal(next.job.id, a.id);
  assert.deepEqual(next.job.claimsRequiringAttention.map(c => c.id), oldA.claims.map(c => c.id));
  await assert.rejects(submitRepositoryPage(resumed, { jobId: a.id }), /explicit/);
  assert.deepEqual(await store.loadPage(a.path), oldA, 'empty submission cannot bless drift');
  await submitRepositoryPage(resumed, {
    jobId: a.id, confirmedClaimIds: oldA.claims.map(c => c.id),
  });
  const verifiedA = await store.loadPage(a.path);
  assert.deepEqual(verifiedA.claims, oldA.claims, 'original Claim IDs and evidence survive');
  assert.equal(verifiedA.pageVersion, await store.hashPage(a.path));
  assert.equal(await store.readMarkdown(b.path), bMarkdown);
  assert.equal(await readFile(store.sidecarPath(b.path), 'utf8'), bSidecar);
  assert.equal((await nextRepositoryPage(resumed)).job.id, original.plan.pages[2].id);
  await completeNext(resumed);
  assert.equal((await nextRepositoryPage(resumed)).status, 'complete');
  await finishRepositoryRun(resumed);
  assert.equal(await readRepositoryRunState(root), null);
  assert.equal(JSON.parse(await readFile(path.join(root, 'openwiki/.last-update.json'))).status, 'complete');
});

test('native resume keeps malformed Claims, missing Markdown and I/O failures fatal', async t => {
  for (const corruption of ['json', 'verification', 'empty-claims', 'duplicate-id', 'missing-sidecar', 'missing-markdown', 'io']) {
    await t.test(corruption, async t => {
      const { root, run, begin, store } = await fixture(t);
      const job = await completeNext(run);
      const state = await readFile(path.join(root, 'openwiki/.run.json'), 'utf8');
      const sidecar = store.sidecarPath(job.path);
      const persisted = await store.loadPage(job.path);
      await writeFile(path.join(root, job.path.slice(1)), pageText + '\nLate edit.\n');
      if (corruption === 'json') await writeFile(sidecar, '{');
      if (corruption === 'verification') {
        delete persisted.verification;
        await writeFile(sidecar, JSON.stringify(persisted));
      }
      if (corruption === 'duplicate-id') {
        persisted.claims.push(persisted.claims[0]);
        await writeFile(sidecar, JSON.stringify(persisted));
      }
      if (corruption === 'empty-claims') {
        persisted.claims = [];
        await writeFile(sidecar, JSON.stringify(persisted));
      }
      if (corruption === 'missing-sidecar') await rm(sidecar);
      if (corruption === 'missing-markdown') await rm(path.join(root, job.path.slice(1)));
      if (corruption === 'io') {
        await rm(sidecar);
        await mkdir(sidecar);
      }
      await assert.rejects(begin, /Claims|claims|sidecar|regular file|Invalid|hash/);
      assert.equal(await readFile(path.join(root, 'openwiki/.run.json'), 'utf8'), state);
    });
  }
});

test('native drift outside a resumed plan blocks publication across retries until every Claim is reconciled', async t => {
  const { root, run, store } = await fixture(t, ['a', 'quickstart']);
  const a = await completeNext(run, [claim, {
    ...claim, statement: 'README identifies the fixture.',
  }, {
    ...claim, statement: 'README describes the fixture component.',
  }]);
  await completeNext(run);
  await finishRepositoryRun(run);
  const beginUpdate = () => beginRepositoryRun({ root, mode: 'update', force: true, language: 'en', actor });
  const { run: updating } = await beginUpdate();
  await submitRepositoryPlan(updating, { pages: [{
    path: '/openwiki/quickstart.md', title: 'quickstart', purpose: 'Update quickstart.',
    seedPaths: ['README.md'],
  }] });
  const original = structuredClone(updating.state);
  const before = await store.loadPage(a.path);
  const edited = (await store.readMarkdown(a.path)) + '\nUnreviewed appended prose.\n';
  await writeFile(path.join(root, a.path.slice(1)), edited);
  let { run: resumed } = await beginUpdate();
  assert.deepEqual(resumed.state, original, 'out-of-plan drift must not replace the existing queue');
  assert.equal(await store.readMarkdown(a.path), edited);
  const job = (await nextRepositoryPage(resumed)).job;
  assert.equal(job.path, '/openwiki/quickstart.md');
  await submitRepositoryPage(resumed, { jobId: job.id });
  assert.deepEqual(await store.loadPage(a.path), before, 'another page submission cannot refresh unresolved drift');
  const completeQueue = structuredClone(resumed.state);
  const publish = () => execFileSync(process.execPath, [
    new URL('./check-completion.mjs', import.meta.url).pathname, path.join(root, 'openwiki'),
  ], { encoding: 'utf8', stdio: 'pipe' });
  for (let retry = 0; retry < 2; retry++) {
    await assert.rejects(finishRepositoryRun(resumed), /Claims.*(Markdown bytes|projected)/);
    assert.equal(JSON.parse(await readFile(path.join(root, 'openwiki/.last-update.json'))).status, 'interrupted');
    assert.throws(publish, /OpenWiki did not complete every planned page/);
    assert.deepEqual(await store.loadPage(a.path), before, 'failed finish retains the original Claim hash and IDs');
    ({ run: resumed } = await beginUpdate());
    assert.deepEqual(resumed.state, completeQueue, 'retry preserves completed jobs, source checkpoint and run ID');
    assert.deepEqual(resumed.claimsRuntime.session.inspectClaims(a.path)
      .filter(c => c.issue).map(c => c.id), before.claims.map(c => c.id));
  }
  // Use the same native reconciliation seam as submit_page: this page has no
  // job in the immutable saved plan. No sidecar or run metadata is fabricated.
  const session = resumed.claimsRuntime.session;
  const [confirmed, revised, retracted] = before.claims;
  await assert.rejects(reconcilePageClaims(session, a.path, {}), /explicit/);
  await assert.rejects(reconcilePageClaims(session, a.path, {
    confirmedClaimIds: [confirmed.id],
  }), /explicit/);
  const revision = { ...claim, id: revised.id, statement: 'README introduces a synthetic component.' };
  await reconcilePageClaims(session, a.path, {
    confirmedClaimIds: [confirmed.id], claims: [revision], retractedClaimIds: [retracted.id],
  });
  await finishRepositoryRun(resumed);
  const after = await store.loadPage(a.path);
  assert.deepEqual(after.claims.map(c => c.id), [confirmed.id, revised.id]);
  assert.equal(after.claims[1].statement, revision.statement);
  assert.equal(after.pageVersion, await store.hashPage(a.path));
  assert.equal(await readRepositoryRunState(root), null);
  assert.match(publish(), /completion metadata verified/);
});

test('native backend preserves read/write admission order including queued uploads', async t => {
  for (const scope of ['default', 'docs-only', 'worker']) {
    await t.test(scope, async t => {
      const { root } = await fixture(t, ['quickstart']);
      const page = '/openwiki/quickstart.md';
      const backend = new OpenWikiLocalShellBackend({
        rootDir: root, virtualMode: true, docsOnly: scope !== 'default',
        ...(scope === 'worker' ? { writableWikiPages: [page] } : {}),
      });
      assert.equal((await backend.write(page, 'old')).error, undefined);
      const writing = backend.write(page, 'new');
      const reading = backend.readRaw(page);
      const formatted = backend.read(page);
      assert.equal((await writing).error, undefined);
      assert.equal((await reading).data.content, 'new');
      assert.match((await formatted).content, /new/);

      // Queue several real operations without yielding. Reads must preserve
      // both directions of admission, including an upload behind an edit.
      const before = backend.readRaw(page);
      const editing = backend.edit(page, 'new', 'edited', false);
      const uploading = backend.uploadFiles([[page, new TextEncoder().encode('uploaded')]]);
      const after = backend.readRaw(page);
      assert.equal((await before).data.content, 'new');
      assert.equal((await editing).error, undefined);
      assert.equal((await uploading)[0].error, null);
      assert.equal((await after).data.content, 'uploaded');
      const deleting = backend.delete(page);
      const missing = assert.rejects(backend.readRaw(page), { code: 'ENOENT' });
      assert.equal((await deleting).error, undefined);
      await missing;

      if (scope !== 'worker') {
        // Ordinary lifecycle backends retain the module-wide path queue too.
        const other = new OpenWikiLocalShellBackend({ rootDir: root, virtualMode: true });
        assert.equal((await backend.write(page, 'cross-before')).error, undefined);
        const first = backend.edit(page, 'cross-before', 'cross-edited', false);
        const upload = backend.uploadFiles([[page, new TextEncoder().encode('cross-backend')]]);
        const second = other.readRaw(page);
        assert.equal((await first).error, undefined);
        assert.equal((await upload)[0].error, null);
        assert.equal((await second).data.content, 'cross-backend');
      }
    });
  }
});

class ScriptedModel extends BaseChatModel {
  constructor(reply) { super({}); this.reply = reply; this.calls = 0; }
  _llmType() { return 'offline-scripted'; }
  bindTools() { return this; }
  async _generate(messages) {
    const message = this.reply(++this.calls, messages);
    return { generations: [{ message, text: '' }] };
  }
}

function tools(...calls) {
  return new AIMessage({
    content: '',
    tool_calls: calls.map(([name, args], i) => ({ id: `call-${i}`, name, args, type: 'tool_call' })),
  });
}

test('native worker stops at successful submit and freezes writes in the same tool batch', async t => {
  for (const mode of ['later-turn', 'same-batch', 'write-submit-write']) {
    await t.test(mode, async t => {
      const { root, store } = await fixture(t, ['quickstart']);
      const page = '/openwiki/quickstart.md';
      const edit = (before, after) => ['edit_file', { file_path: page, old_string: before, new_string: after }];
      let sawRejection = false;
      const model = new ScriptedModel((call, messages) => {
        if (call === 1) return tools(['write_file', { file_path: page, content: pageText }]);
        if (call === 2) return tools(['submit_page', {}]); // Recoverable rejection.
        if (call === 3) {
          sawRejection = messages.some(m => m.getType() === 'tool' && String(m.content).includes('"rejected"'));
          return tools(edit('DRAFT', 'REVISED'));
        }
        if (call === 4) {
          const submit = ['submit_page', { claims: [claim] }];
          if (mode === 'write-submit-write') {
            return tools(edit('REVISED', 'ACCEPTED'), submit, edit('ACCEPTED', 'CORRUPTED'));
          }
          if (mode === 'same-batch') return tools(submit, edit('REVISED', 'CORRUPTED'));
          return tools(submit);
        }
        if (call === 5) return tools(edit('REVISED', 'CORRUPTED'));
        return new AIMessage('Finished.');
      });
      await runNativeRepositoryGeneration({
        root, mode: 'init', language: 'en', model, modelId: actor.metadataModel,
        pageConcurrency: 1, workerStartStaggerMs: 0,
      });
      const markdown = await store.readMarkdown(page);
      assert.doesNotMatch(markdown, /CORRUPTED/, 'post-submit writes changed completed bytes');
      assert.match(markdown, mode === 'write-submit-write' ? /ACCEPTED/ : /REVISED/);
      assert.equal(sawRejection, true, 'rejected submission must allow repair and retry');
      assert.equal(model.calls, 4, 'successful submit must end before another model call');
      assert.equal((await store.loadPage(page)).pageVersion, await store.hashPage(page));
      assert.equal(await readRepositoryRunState(root), null);
    });
  }
});

test('worker write revocation covers queued write, edit, delete and upload', async t => {
  const { root } = await fixture(t, ['quickstart']);
  const page = '/openwiki/quickstart.md';
  const backend = new OpenWikiLocalShellBackend({
    rootDir: root, virtualMode: true, docsOnly: true, writableWikiPages: [page],
  });
  const [uploaded] = await backend.uploadFiles([[page, new TextEncoder().encode('uploaded')]]);
  assert.equal(uploaded.error, null);
  assert.equal((await backend.delete(page)).error, undefined);
  assert.equal((await backend.write(page, 'before')).error, undefined);
  const earlierEdit = backend.edit(page, 'before', 'accepted', false);
  let release;
  const held = new Promise(resolve => { release = resolve; });
  const submission = backend.withWriteLock(async () => {
    assert.equal(await readFile(path.join(root, page.slice(1)), 'utf8'), 'accepted');
    await held;
    backend.writableWikiPages.clear();
  });
  const queued = [
    backend.write(page, 'after'),
    backend.edit(page, 'accepted', 'after', false),
    backend.delete(page),
    backend.uploadFiles([[page, new TextEncoder().encode('after')]]),
  ];
  release();
  assert.equal((await earlierEdit).error, undefined);
  await submission;
  const results = await Promise.all(queued);
  for (const result of results.slice(0, 3)) assert.match(result.error, /worker may not modify/);
  assert.equal(results[3][0].error, 'permission_denied');
  assert.equal(await readFile(path.join(root, page.slice(1)), 'utf8'), 'accepted');
});
