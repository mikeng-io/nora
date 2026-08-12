from skills.markdown.render import render_markdown


def test_plain_prose_becomes_a_paragraph():
    result = render_markdown(text="Hello, world.")
    assert "<p>Hello, world.</p>" in result


def test_a_fenced_code_block_is_syntax_highlighted():
    """Deliberately stricter than checking for the mere PRESENCE of `<pre`/
    `<div` — that weaker assertion is exactly what let a real double-wrapping
    bug (`<pre><code><div class="highlight"><pre>...` — Pygments' own
    `nowrap=False` output getting wrapped a second time by markdown-it-py's
    fence renderer) pass an earlier draft of this test undetected. Counting
    exactly one `<pre` confirms the structure is actually well-formed, not
    just that SOME wrapper tag showed up somewhere in the string."""
    result = render_markdown(text="```python\ndef f():\n    return 1\n```")
    assert "highlight" in result  # Pygments' own wrapper class
    assert result.count("<pre") == 1
    assert "<div" not in result


def test_gfm_tables_render():
    result = render_markdown(text="| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<table>" in result


def test_no_external_script_or_link_tags_are_emitted():
    result = render_markdown(text="# Title\n\nSome *text* with `code`.")
    assert "<script" not in result
    assert "cdn." not in result


def test_raw_html_embedded_in_the_source_does_not_pass_through_unescaped():
    """MarkdownIt("gfm-like") defaults to `html: True`, which lets HTML
    embedded in the markdown SOURCE (a `<script>` tag, say) pass straight
    through into the output unescaped -- breaking this plan's own Global
    Constraint 1 (no untrusted script in a self-contained artifact). markdown
    is LLM-authored input here, not trusted markup."""
    result = render_markdown(text="# Title\n\n<script>alert(1)</script>\n\nSome text.")
    assert "<script>alert(1)</script>" not in result
