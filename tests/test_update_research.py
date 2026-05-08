"""Tests for scripts/update_research.py.

Focus on the high-blast-radius paths: HTML escaping (XSS surface, since the
output is published to GitHub Pages), JSON fence stripping, the 24-hour
cutoff filter, and graceful handling of feed errors.
"""
import sys
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests

import update_research as ur


# ── Helpers ───────────────────────────────────────────────────────────────────

def _claude_response(text):
    """Build a MagicMock matching anthropic's response shape."""
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    return resp


def _arxiv_xml(entries):
    """Render a minimal arXiv-style Atom feed from a list of dicts."""
    items = "".join(
        f"""
  <entry>
    <id>{e['id']}</id>
    <title>{e['title']}</title>
    <summary>{e['summary']}</summary>
    <published>{e['published']}</published>
    <author><name>{e.get('author', 'A. Researcher')}</name></author>
  </entry>"""
        for e in entries
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">{items}
</feed>"""


# ── select_and_analyze ────────────────────────────────────────────────────────

def test_select_and_analyze_returns_empty_when_no_input():
    assert ur.select_and_analyze([], []) == []


def test_select_and_analyze_strips_json_code_fence():
    payload = '```json\n[{"title": "x", "impact_score": 7}]\n```'
    with patch.object(ur.client, "messages") as mock_messages:
        mock_messages.create.return_value = _claude_response(payload)
        result = ur.select_and_analyze([{"source": "s", "title": "t",
                                         "published": "p", "link": "l",
                                         "summary": "abstract"}], [])
    assert result == [{"title": "x", "impact_score": 7}]


def test_select_and_analyze_strips_plain_code_fence():
    payload = '```\n[{"a": 1}]\n```'
    with patch.object(ur.client, "messages") as mock_messages:
        mock_messages.create.return_value = _claude_response(payload)
        result = ur.select_and_analyze([{"source": "s", "title": "t",
                                         "published": "p", "link": "l",
                                         "summary": ""}], [])
    assert result == [{"a": 1}]


def test_select_and_analyze_handles_invalid_json():
    with patch.object(ur.client, "messages") as mock_messages:
        mock_messages.create.return_value = _claude_response("not json at all")
        result = ur.select_and_analyze([{"source": "s", "title": "t",
                                         "published": "p", "link": "l",
                                         "summary": ""}], [])
    assert result == []


def test_select_and_analyze_includes_all_items_in_prompt():
    captured = {}

    def fake_create(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return _claude_response("[]")

    with patch.object(ur.client, "messages") as mock_messages:
        mock_messages.create.side_effect = fake_create
        ur.select_and_analyze(
            [{"source": "arxiv", "title": "Paper One", "published": "p",
              "link": "l", "summary": "s"}],
            [{"source": "blog", "title": "Post Two", "published": "p",
              "link": "l", "summary": "s"}],
        )

    assert "Paper One" in captured["prompt"]
    assert "Post Two" in captured["prompt"]


# ── HTML escaping (sig_chip / paper_card) ────────────────────────────────────

def test_sig_chip_escapes_html_in_reason():
    out = ur.sig_chip("NVDA", "up", "<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


def test_sig_chip_escapes_ampersand_in_ticker():
    out = ur.sig_chip("A&B", "down", "ok")
    assert "A&amp;B" in out
    assert "A&B<" not in out  # raw ampersand must not survive next to a tag


def test_sig_chip_direction_classes():
    assert "sig-up" in ur.sig_chip("X", "up", "r")
    assert "▲" in ur.sig_chip("X", "up", "r")
    assert "sig-down" in ur.sig_chip("X", "down", "r")
    assert "▼" in ur.sig_chip("X", "down", "r")


def test_paper_card_escapes_title():
    paper = {
        "title": '<img src=x onerror="alert(1)">',
        "source": "arXiv", "link": "https://example.com",
        "published": "May 8, 2026", "eli5": "", "technical": "",
        "market_narrative": "", "stock_implications": [],
        "impact_score": 5, "impact_label": "📊 Notable",
    }
    out = ur.paper_card(paper, 0)
    assert "<img src=x" not in out
    assert "&lt;img" in out


def test_paper_card_handles_missing_optional_fields():
    # Minimum viable paper — no authors, no impact_label, no implications.
    paper = {"title": "T", "source": "S", "link": "https://x",
             "published": "d", "eli5": "p1\n\np2", "technical": "t",
             "market_narrative": "m"}
    out = ur.paper_card(paper, 0)
    # Should still render and apply defaults.
    assert "📊 Notable" in out  # default impact label
    assert "5/10" in out         # default impact score
    assert "paper-authors" not in out


def test_paper_card_emits_animatable_impact_bar():
    paper = {"title": "T", "source": "S", "link": "https://x",
             "published": "d", "eli5": "", "technical": "",
             "market_narrative": "", "stock_implications": [],
             "impact_score": 8}
    out = ur.paper_card(paper, 0)
    assert 'data-target="80"' in out
    # No inline width — the JS observer fills it on scroll-in.
    assert 'style="width:80%"' not in out


def test_generate_html_marks_count_as_counter():
    out = ur.generate_html([], "2026-05-08", "Friday, May 8, 2026")
    assert 'data-counter' in out
    assert 'data-target="0"' in out


def test_paper_card_splits_eli5_paragraphs():
    paper = {"title": "T", "source": "S", "link": "https://x",
             "published": "d", "eli5": "para one\n\npara two\n\npara three",
             "technical": "", "market_narrative": "",
             "stock_implications": []}
    out = ur.paper_card(paper, 0)
    assert out.count("<p>para one</p>") == 1
    assert out.count("<p>para two</p>") == 1
    assert out.count("<p>para three</p>") == 1


# ── fetch_arxiv_papers ────────────────────────────────────────────────────────

def _patch_arxiv_response(text, status=200):
    """Return a context manager that patches requests.get on the module."""
    fake = MagicMock()
    fake.text = text
    fake.status_code = status
    fake.raise_for_status = MagicMock()
    return patch.object(ur.requests, "get", return_value=fake)


def test_fetch_arxiv_filters_entries_older_than_cutoff():
    recent = (ur.TODAY - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale = (ur.TODAY - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    xml = _arxiv_xml([
        {"id": "http://arxiv.org/abs/1", "title": "Fresh",
         "summary": "abs", "published": recent},
        {"id": "http://arxiv.org/abs/2", "title": "Stale",
         "summary": "abs", "published": stale},
    ])

    # Only patch one category to keep the test focused.
    with patch.object(ur, "ARXIV_CATEGORIES", ["cs.AI"]), \
         _patch_arxiv_response(xml):
        papers = ur.fetch_arxiv_papers()

    titles = [p["title"] for p in papers]
    assert "Fresh" in titles
    assert "Stale" not in titles


def test_fetch_arxiv_handles_request_error():
    with patch.object(ur, "ARXIV_CATEGORIES", ["cs.AI"]), \
         patch.object(ur.requests, "get",
                      side_effect=requests.RequestException("boom")):
        papers = ur.fetch_arxiv_papers()
    assert papers == []


def test_fetch_arxiv_handles_malformed_xml():
    with patch.object(ur, "ARXIV_CATEGORIES", ["cs.AI"]), \
         _patch_arxiv_response("<not-xml"):
        papers = ur.fetch_arxiv_papers()
    assert papers == []


def test_fetch_arxiv_truncates_long_summaries():
    recent = (ur.TODAY - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    xml = _arxiv_xml([{
        "id": "http://arxiv.org/abs/1", "title": "T",
        "summary": "x" * 5000, "published": recent,
    }])
    with patch.object(ur, "ARXIV_CATEGORIES", ["cs.AI"]), \
         _patch_arxiv_response(xml):
        papers = ur.fetch_arxiv_papers()
    assert len(papers) == 1
    assert len(papers[0]["summary"]) <= 800


# ── fetch_blog_posts ──────────────────────────────────────────────────────────

def _make_feed_entry(title, summary, hours_ago):
    when = ur.TODAY - timedelta(hours=hours_ago)
    return {
        "title": title,
        "summary": summary,
        "link": "https://example.com/post",
        "published_parsed": when.timetuple(),
    }


def test_fetch_blog_posts_strips_html_from_summary():
    entry = _make_feed_entry("Post", "<p>hello <b>world</b></p>", hours_ago=1)
    fake_feed = types.SimpleNamespace(entries=[
        types.SimpleNamespace(**entry, get=lambda k, d="": entry.get(k, d))
    ])

    # Entry needs `.get` to behave like a feedparser FeedParserDict.
    class E(dict):
        def get(self, k, default=""):
            return super().get(k, default)
    e = E(entry)
    fake_feed = types.SimpleNamespace(entries=[e])

    with patch.object(ur, "BLOG_FEEDS", [("Test", "http://x")]), \
         patch.object(ur.feedparser, "parse", return_value=fake_feed):
        posts = ur.fetch_blog_posts()

    assert len(posts) == 1
    assert "<" not in posts[0]["summary"]
    assert "hello world" in posts[0]["summary"]


def test_fetch_blog_posts_filters_old_entries():
    class E(dict):
        def get(self, k, default=""):
            return super().get(k, default)

    fresh = E(_make_feed_entry("Fresh", "s", hours_ago=1))
    stale = E(_make_feed_entry("Stale", "s", hours_ago=72))
    fake_feed = types.SimpleNamespace(entries=[fresh, stale])

    with patch.object(ur, "BLOG_FEEDS", [("Test", "http://x")]), \
         patch.object(ur.feedparser, "parse", return_value=fake_feed):
        posts = ur.fetch_blog_posts()

    titles = [p["title"] for p in posts]
    assert "Fresh" in titles
    assert "Stale" not in titles


def test_fetch_blog_posts_handles_feed_exception():
    with patch.object(ur, "BLOG_FEEDS", [("Test", "http://x")]), \
         patch.object(ur.feedparser, "parse",
                      side_effect=RuntimeError("network down")):
        posts = ur.fetch_blog_posts()
    assert posts == []


# ── generate_html ─────────────────────────────────────────────────────────────

def test_generate_html_renders_count_and_date():
    papers = [{
        "title": "T", "source": "S", "link": "https://x",
        "published": "May 8, 2026", "eli5": "", "technical": "",
        "market_narrative": "", "stock_implications": [],
        "impact_score": 5,
    }]
    out = ur.generate_html(papers, "2026-05-08", "Friday, May 8, 2026")
    assert "Friday, May 8, 2026" in out
    assert ">1<" in out  # the {count} stat
    assert "<!DOCTYPE html>" in out


def test_generate_html_handles_empty_paper_list():
    out = ur.generate_html([], "2026-05-08", "Friday, May 8, 2026")
    assert "<!DOCTYPE html>" in out
    assert ">0<" in out  # the {count} stat shows zero
    assert "Friday, May 8, 2026" in out
