"""`nora.domain.linkedin_links`: which LinkedIn links name a person, a company
or a post, and that nothing else is read as one."""

from __future__ import annotations

import pytest

from nora.domain.linkedin_links import (
    LinkedInLink,
    is_handle,
    is_linkedin_host,
    parse_link,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.linkedin.com/in/williamhgates/", LinkedInLink("person", "williamhgates")),
        ("https://linkedin.com/in/williamhgates", LinkedInLink("person", "williamhgates")),
        ("https://hk.linkedin.com/in/satya-nadella-1152401b2",
         LinkedInLink("person", "satya-nadella-1152401b2")),
        ("http://www.linkedin.com/in/williamhgates/?trk=share#x",
         LinkedInLink("person", "williamhgates")),
        # A person's sub-page still names the person.
        ("https://www.linkedin.com/in/williamhgates/recent-activity/all/",
         LinkedInLink("person", "williamhgates")),
        ("https://www.linkedin.com/company/nvidia/", LinkedInLink("company", "nvidia")),
        ("https://www.linkedin.com/company/nvidia/about/", LinkedInLink("company", "nvidia")),
        # A post is recognized, only so the tool can say it cannot read one.
        ("https://www.linkedin.com/posts/williamhgates_ai-activity-123-abc/",
         LinkedInLink("post")),
        ("https://www.linkedin.com/feed/update/urn:li:activity:123/", LinkedInLink("post")),
        ("https://www.linkedin.com/pulse/some-article-title/", LinkedInLink("post")),
    ],
)
def test_a_person_company_or_post_link_is_recognized(url, expected):
    assert parse_link(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        # Not LinkedIn, or LinkedIn spelled as a substring or a suffix.
        "https://linkedin.com.evil.example/in/williamhgates/",
        "https://evillinkedin.com/in/williamhgates/",
        "https://www.linkedin.co/in/williamhgates/",
        "https://a.b.linkedin.com/in/williamhgates/",
        "https://abc.linkedin.com/in/williamhgates/",
        "https://x.com/williamhgates",
        # A malformed origin.
        "https://user:pw@www.linkedin.com/in/williamhgates/",
        "https://www.linkedin.com:8443/in/williamhgates/",
        "ftp://www.linkedin.com/in/williamhgates/",
        # Pages that name no person, company or post.
        "https://www.linkedin.com/",
        "https://www.linkedin.com/feed/",
        "https://www.linkedin.com/jobs/view/123/",
        "https://www.linkedin.com/school/hku/",
        "https://www.linkedin.com/in/",
        # A segment outside its charset.
        "https://www.linkedin.com/in/ab/",
        "https://www.linkedin.com/in/%E5%BC%A0%E4%B8%89/",
        "https://www.linkedin.com/in/william.gates/",
        "https://www.linkedin.com/company/nvidia%20corp/",
        # A leading dash would read as a command-line flag downstream.
        "https://www.linkedin.com/company/-f/",
        "https://www.linkedin.com/company/--evil/",
        "https://www.linkedin.com/in/-abc/",
        # Not a link at all.
        "",
        None,
        7,
    ],
)
def test_anything_else_is_refused(url):
    assert parse_link(url) is None


def test_the_handle_charset():
    assert is_handle("williamhgates")
    assert is_handle("satya-nadella-1152401b2")
    assert not is_handle("ab")
    assert not is_handle("william gates")
    assert not is_handle("a" * 101)
    assert not is_handle(None)


def test_is_linkedin_host():
    assert is_linkedin_host("https://www.linkedin.com/jobs/")
    assert is_linkedin_host("https://hk.linkedin.com/")
    assert not is_linkedin_host("https://x.com/nasa")
