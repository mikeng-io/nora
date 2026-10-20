import { StructuredTool, ToolInputParsingException } from '@langchain/core/tools';

// Optional HTTP response diagnostics. Never serialize requests, text, tool
// arguments, headers, URLs, arbitrary metadata or errors. Preserve the response.
if (process.env.OPENWIKI_PROVIDER_DIAGNOSTICS === 'true') {
  const completionIds = new Set();
  const toolCallIds = new Set();
  const invoke = StructuredTool.prototype.invoke;
  StructuredTool.prototype.invoke = async function (...args) {
    try {
      return await invoke.apply(this, args);
    } catch (error) {
      const tool = ['submit_plan', 'submit_page', 'inspect_claims', 'ls', 'read_file',
        'glob', 'grep', 'write_file', 'edit_file'].includes(this.name) ? this.name : 'other';
      const kind = error instanceof ToolInputParsingException ? 'ToolInputParsingException'
        : ['ZodError', 'TypeError', 'Error',
        'GraphRecursionError', 'InvalidUpdateError'].includes(error?.name) ? error.name : 'other';
      console.error('OpenWiki tool diagnostics:', JSON.stringify({ tool, kind }));
      throw error;
    }
  };
  const fetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const response = await fetch(input, init);
    const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
    if (!url.pathname.endsWith('/chat/completions')) return response;
    const json = response.headers.get('content-type')?.includes('application/json');
    const summary = { status: response.status, format: json ? 'json' : 'other' };
    if (json) {
      try {
        const body = await response.clone().json();
        summary.duplicateCompletionId = typeof body.id === 'string' && completionIds.has(body.id);
        if (typeof body.id === 'string') completionIds.add(body.id);
        summary.generations = Array.isArray(body.choices) ? body.choices.map(({ message, finish_reason }) => ({
          finishReason: ['stop', 'length', 'tool_calls', 'content_filter', 'function_call']
            .includes(finish_reason) ? finish_reason : 'unknown',
          hasContent: typeof message?.content === 'string'
            ? message.content.length > 0 : Array.isArray(message?.content) && message.content.length > 0,
          toolCalls: Array.isArray(message?.tool_calls) ? message.tool_calls.length : 0,
          argumentJsonErrors: (message?.tool_calls ?? []).filter(call => {
            try { JSON.parse(call.function?.arguments); return false; } catch { return true; }
          }).length,
          duplicateToolCallIds: (message?.tool_calls ?? []).filter(call => {
            if (typeof call.id !== 'string') return false;
            const duplicate = toolCallIds.has(call.id);
            toolCallIds.add(call.id);
            return duplicate;
          }).length,
          hasReasoning: Boolean(message?.reasoning_content),
        })) : [];
      } catch {
        summary.format = 'unreadable-json';
      }
    }
    console.error('OpenWiki provider diagnostics:', JSON.stringify(summary));
    return response;
  };
}
