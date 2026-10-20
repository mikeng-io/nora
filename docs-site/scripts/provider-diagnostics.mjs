// Optional HTTP response diagnostics. Never serialize requests, text, tool
// arguments, headers, URLs, arbitrary metadata or errors. Preserve the response.
if (process.env.OPENWIKI_PROVIDER_DIAGNOSTICS === 'true') {
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
        summary.generations = Array.isArray(body.choices) ? body.choices.map(({ message, finish_reason }) => ({
          finishReason: ['stop', 'length', 'tool_calls', 'content_filter', 'function_call']
            .includes(finish_reason) ? finish_reason : 'unknown',
          hasContent: typeof message?.content === 'string'
            ? message.content.length > 0 : Array.isArray(message?.content) && message.content.length > 0,
          toolCalls: Array.isArray(message?.tool_calls) ? message.tool_calls.length : 0,
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
