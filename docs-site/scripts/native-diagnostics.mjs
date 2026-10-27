// Project only fixed native event metadata. Never serialize the event itself:
// tool input, text, debug detail, IDs and page paths contain private context.
export function createNativeDiagnostics() {
  const enabled = process.env.OPENWIKI_PROVIDER_DIAGNOSTICS === 'true';
  const started = performance.now();
  let stage = 'run';
  let toolStarts = 0;
  let toolEnds = 0;
  const emit = record => {
    if (!enabled) return;
    try {
      console.error('OpenWiki native diagnostics:', JSON.stringify({
        ...record, elapsedMs: Math.round(performance.now() - started), toolStarts, toolEnds,
      }));
    } catch { /* Diagnostics must not change authored output or exceptions. */ }
  };
  emit({ stage, status: 'start' });
  return {
    onEvent(event) {
      if (!enabled) return;
      if (event.type === 'repository_progress'
          && ['planning', 'replanning', 'generating', 'finalizing', 'noop'].includes(event.stage)) {
        if (stage !== event.stage) emit({ stage, status: 'end' });
        stage = event.stage;
        const counts = {};
        for (const key of ['pageIndex', 'pageCount', 'completedCount']) {
          if (Number.isSafeInteger(event[key]) && event[key] >= 0) counts[key] = event[key];
        }
        emit({ stage, status: 'start', ...counts });
      } else if (['tool_start', 'tool_end'].includes(event.type)) {
        const tool = ['submit_plan', 'submit_page', 'inspect_claims', 'ls', 'read_file',
          'glob', 'grep', 'write_file', 'edit_file'].includes(event.name) ? event.name : 'other';
        if (event.type === 'tool_start') toolStarts++;
        else toolEnds++;
        emit({ stage, tool, status: event.type === 'tool_start' ? 'start'
          : event.status === 'error' ? 'error' : 'end' });
      }
    },
    finish(failed = false) { emit({ stage, status: failed ? 'error' : 'end' }); },
  };
}
