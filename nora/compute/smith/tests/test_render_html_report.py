from skills.html_report.render import render_html_report


def test_it_delegates_to_the_page_shell():
    html = render_html_report(title="Weekly Recap", sections=["<p>hello</p>"])
    assert "<p>hello</p>" in html
    assert "Weekly Recap" in html
    assert "<!doctype html>" in html.lower()
