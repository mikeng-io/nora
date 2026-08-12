"""`render_markdown` — GFM markdown to self-contained HTML, code blocks
syntax-highlighted via Pygments at render time (inline CSS, no client JS).
The second of the three deterministic skills (this plan's Architecture note).
"""

from __future__ import annotations

from markdown_it import MarkdownIt
from mdit_py_plugins.front_matter import front_matter_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.util import ClassNotFound

# `nowrap=True` -- Pygments returns just the inline `<span>`-tagged tokens,
# no `<pre>`/`<div>` wrapper of its own. This matters for a reason that only
# shows up at runtime, not at import time: markdown-it-py's fence renderer
# skips wrapping the `highlight` callback's return value in ITS OWN
# `<pre><code>...</code></pre>` only when that return value ALREADY starts
# with `"<pre"`. An earlier draft of this module used `nowrap=False`, whose
# output starts with `"<div"` -- so markdown-it-py wrapped it a SECOND time,
# producing `<pre><code><div class="highlight"><pre>...</pre></div>
# </code></pre>`, a div nested inside code nested inside pre. Confirmed by an
# independent subagent review that actually ran this module's fence-rendering
# path end to end, not just read the code. Building the single `<pre>`
# wrapper explicitly below is what satisfies markdown-it-py's skip-check.
_FORMATTER = HtmlFormatter(nowrap=True, cssclass="highlight")
_STYLE_BLOCK = f"<style>{_FORMATTER.get_style_defs('.highlight')}</style>"

_md = MarkdownIt("gfm-like").use(front_matter_plugin)


def _highlight_code(code: str, lang: str, _attrs: str) -> str:
    try:
        lexer = get_lexer_by_name(lang) if lang else guess_lexer(code)
    except ClassNotFound:
        lexer = get_lexer_by_name("text")
    highlighted = highlight(code, lexer, _FORMATTER)
    return f'<pre class="highlight"><code>{highlighted}</code></pre>'


_md.options["highlight"] = _highlight_code


def render_markdown(*, text: str) -> str:
    body = _md.render(text)
    return _STYLE_BLOCK + body
