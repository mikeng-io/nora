import pytest

from page_shell import page_shell


def test_the_shell_wraps_every_section():
    html = page_shell(title="Test", sections=["<p>one</p>", "<p>two</p>"])
    assert "<p>one</p>" in html
    assert "<p>two</p>" in html


def test_the_shell_includes_a_viewport_meta_tag():
    html = page_shell(title="Test", sections=["<p>x</p>"])
    assert 'name="viewport"' in html


def test_the_shell_defaults_to_true_neutral_grayscale():
    html = page_shell(title="Test", sections=["<p>x</p>"])
    # True neutral means R=G=B in the base token values -- check the shell's
    # own CSS custom properties directly rather than rendering and sampling
    # pixels, which this test has no browser to do.
    assert "--ink: #141414" in html or "--ink:#141414" in html


def test_an_accent_color_is_applied_when_given():
    html = page_shell(title="Test", sections=["<p>x</p>"], accent="#2f6f62")
    assert "#2f6f62" in html
    # Not just present SOMEWHERE in the string -- `--accent` must actually be
    # consumed by a rule that renders, not merely defined and left unread
    # (the bug this test was strengthened to catch: the CSS variable existed
    # but no selector referenced it).
    assert "color: var(--accent)" in html
    assert "h1 {" in html


def test_no_accent_means_no_accent_variable_override():
    html = page_shell(title="Test", sections=["<p>x</p>"])
    assert "#2f6f62" not in html


def test_the_dark_theme_blocks_also_define_a_fallback_accent():
    # `h1` reads `color: var(--accent)`, and the default (no accent= given)
    # must stay legible against `--bg` in EVERY theme. An earlier draft
    # bound `--accent` only on bare `:root` (light) and left both dark
    # blocks without their own `--accent` redefinition -- so a dark-mode
    # h1 fell through to `--shell-accent`'s default of `#141414`, painted
    # on a `#141414` background: invisible. Both dark blocks must carry
    # their own fallback (matching --ink's dark value), not just the
    # light one.
    html = page_shell(title="Test", sections=["<p>x</p>"])
    dark_media_block = html.split(
        "@media (prefers-color-scheme: dark)"
    )[1].split("}\n}")[0]
    assert "--accent: var(--shell-accent, #ececec)" in dark_media_block
    dark_theme_block = html.split(':root[data-theme="dark"]')[1].split("}")[0]
    assert "--accent: var(--shell-accent, #ececec)" in dark_theme_block


def test_a_non_hex_color_accent_is_rejected():
    with pytest.raises(ValueError):
        page_shell(
            title="Test",
            sections=["<p>x</p>"],
            accent="red; } </style><script>alert(1)</script>",
        )
