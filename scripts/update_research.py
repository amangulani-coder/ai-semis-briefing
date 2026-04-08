#!/usr/bin/env python3
"""
AI Research Paper Scraper & Explainer
Runs daily via GitHub Actions. Fetches the most impactful AI/chip research
papers and white papers from the last 24 hours, then uses Claude to generate
plain-English breakdowns with stock market implications.

Requires: ANTHROPIC_API_KEY environment variable
"""

import os
import json
import re
import sys
import textwrap
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html import escape

import anthropic
import feedparser
import requests

# ── Config ────────────────────────────────────────────────────────────────────

TODAY = datetime.now(timezone.utc)
CUTOFF = TODAY - timedelta(hours=24)

ARXIV_CATEGORIES = [
    "cs.AI",   # Artificial Intelligence
    "cs.LG",   # Machine Learning
    "cs.AR",   # Hardware Architecture
    "cs.CL",   # Computation and Language
    "cs.CV",   # Computer Vision
]

BLOG_FEEDS = [
    ("Google AI Blog",      "https://blog.google/technology/ai/rss/"),
    ("Google DeepMind",     "https://deepmind.google/blog/rss.xml"),
    ("OpenAI Blog",         "https://openai.com/blog/rss.xml"),
    ("Meta AI",             "https://ai.meta.com/blog/rss/"),
    ("Microsoft Research",  "https://www.microsoft.com/en-us/research/blog/feed/"),
    ("Hugging Face",        "https://huggingface.co/blog/feed.xml"),
    ("Mistral",             "https://mistral.ai/news/rss.xml"),
    ("Together AI",         "https://www.together.ai/blog/rss.xml"),
]

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


# ── Fetching ──────────────────────────────────────────────────────────────────

def fetch_arxiv_papers():
    """Fetch recent papers from arXiv via the API."""
    papers = []
    for cat in ARXIV_CATEGORIES:
        try:
            url = (
                f"http://export.arxiv.org/api/query"
                f"?search_query=cat:{cat}"
                f"&start=0&max_results=15"
                f"&sortBy=submittedDate&sortOrder=descending"
            )
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            ns = {"a": "http://www.w3.org/2005/Atom"}
            for entry in root.findall("a:entry", ns):
                published_str = entry.findtext("a:published", default="", namespaces=ns)
                try:
                    pub_date = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
                except Exception:
                    continue
                if pub_date < CUTOFF:
                    continue
                title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip().replace("\n", " ")
                summary = (entry.findtext("a:summary", default="", namespaces=ns) or "").strip().replace("\n", " ")
                link = entry.findtext("a:id", default="", namespaces=ns)
                authors = [a.findtext("a:name", default="", namespaces=ns) for a in entry.findall("a:author", ns)]
                papers.append({
                    "title": title,
                    "summary": summary[:800],
                    "link": link,
                    "source": f"arXiv / {cat}",
                    "authors": ", ".join(authors[:3]),
                    "published": pub_date.strftime("%B %d, %Y"),
                    "type": "paper",
                })
        except Exception as e:
            print(f"arXiv {cat} fetch error: {e}", file=sys.stderr)
    return papers


def fetch_blog_posts():
    """Fetch recent posts from AI company blogs via RSS."""
    posts = []
    for name, feed_url in BLOG_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:8]:
                # Parse date
                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                if pub:
                    pub_date = datetime(*pub[:6], tzinfo=timezone.utc)
                    if pub_date < CUTOFF:
                        continue
                    published_str = pub_date.strftime("%B %d, %Y")
                else:
                    published_str = TODAY.strftime("%B %d, %Y")

                summary = entry.get("summary", "") or ""
                # Strip HTML tags from summary
                summary = re.sub(r"<[^>]+>", "", summary)[:800]

                posts.append({
                    "title": entry.get("title", "").strip(),
                    "summary": summary.strip(),
                    "link": entry.get("link", ""),
                    "source": name,
                    "authors": "",
                    "published": published_str,
                    "type": "blog",
                })
        except Exception as e:
            print(f"{name} feed error: {e}", file=sys.stderr)
    return posts


# ── Claude analysis ───────────────────────────────────────────────────────────

def select_and_analyze(papers, posts):
    """
    Ask Claude to:
    1. Select the 3-5 most impactful items for an AI/chip investor audience
    2. For each, write a full breakdown: ELI5, technical detail, market implications
    Return structured JSON.
    """
    all_items = papers + posts
    if not all_items:
        return []

    items_text = "\n\n".join(
        f"[{i+1}] SOURCE: {item['source']}\n"
        f"TITLE: {item['title']}\n"
        f"DATE: {item['published']}\n"
        f"LINK: {item['link']}\n"
        f"ABSTRACT/SUMMARY: {item['summary']}"
        for i, item in enumerate(all_items)
    )

    prompt = f"""You are an expert analyst covering AI research, semiconductor technology, and capital markets.
Today's date: {TODAY.strftime('%A, %B %d, %Y')}.

Below are AI research papers and company blog posts published in the last 24 hours.
Your job:

1. SELECT the 3–5 most impactful items for an investor who follows AI models and semiconductor stocks (NVDA, AMD, TSM, INTC, AVGO, ASML, MU, MRVL, AMAT, LRCX, GOOGL, MSFT, META, AMZN, AAPL).

   Prioritize items that:
   - Announce a new AI model, architecture breakthrough, or efficiency gain
   - Directly affect chip demand, memory (HBM/DRAM) requirements, or foundry capacity
   - Come from Google, OpenAI, Anthropic, Meta, Microsoft, xAI, or major research labs
   - Have clear stock market implications (positive or negative)

2. For each selected item, produce a thorough analysis with these sections:
   - "title": the paper/post title
   - "source": the source name
   - "link": the URL
   - "published": the date string
   - "type": "paper" or "blog"
   - "eli5": Plain English explanation (3–4 paragraphs). Imagine explaining this to a smart person who has never studied AI or engineering. Use analogies. No jargon without explanation. Cover: what problem did they solve? How? Why does it matter?
   - "technical": Deeper breakdown (2–3 paragraphs) for someone who wants more detail. Explain the key innovation, how it differs from prior approaches, and what the benchmarks/results show.
   - "market_narrative": 2 paragraphs explaining the broader market context — why this matters NOW, what trend it fits into, what it signals about the direction of the industry.
   - "stock_implications": A list of 4–8 stock implications. Each is an object with:
     - "ticker": e.g. "MU" or "NVDA"
     - "direction": "up" or "down"
     - "reason": 1–2 sentence explanation of why this news is bullish or bearish for this specific stock
   - "impact_score": integer 1–10 rating of how significant this is for markets (10 = paradigm shift, 1 = minor)
   - "impact_label": one of "🔥 Breakthrough", "⚡ Significant", "📊 Notable", "🔍 Watch List"

Return ONLY a valid JSON array. No markdown, no code fences, just the raw JSON array.

PAPERS AND POSTS TO ANALYZE:
{items_text}
"""

    print("Calling Claude to analyze papers...", file=sys.stderr)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    # Strip markdown fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}\nRaw: {raw[:500]}", file=sys.stderr)
        return []


# ── HTML generation ───────────────────────────────────────────────────────────

def sig_chip(ticker, direction, reason):
    cls = "sig-up" if direction == "up" else "sig-down"
    arrow = "▲" if direction == "up" else "▼"
    return f'<div class="stock-impl {cls}"><div class="stock-impl-header"><span class="impl-arrow">{arrow}</span><span class="impl-ticker">{escape(ticker)}</span></div><p class="impl-reason">{escape(reason)}</p></div>'


def paper_card(paper, idx):
    # Impact badge color
    impact = paper.get("impact_label", "📊 Notable")
    impact_cls = {
        "🔥 Breakthrough": "tag-break",
        "⚡ Significant": "tag-chip",
        "📊 Notable": "tag-ai",
        "🔍 Watch List": "tag-trade",
    }.get(impact, "tag-ai")

    score = paper.get("impact_score", 5)
    score_pct = score * 10

    stock_html = "\n".join(
        sig_chip(s["ticker"], s["direction"], s["reason"])
        for s in paper.get("stock_implications", [])
    )

    # ELI5 paragraphs
    eli5_paras = "\n".join(
        f"<p>{escape(p.strip())}</p>"
        for p in paper.get("eli5", "").split("\n\n")
        if p.strip()
    )

    # Technical paragraphs
    tech_paras = "\n".join(
        f"<p>{escape(p.strip())}</p>"
        for p in paper.get("technical", "").split("\n\n")
        if p.strip()
    )

    # Market narrative paragraphs
    market_paras = "\n".join(
        f"<p>{escape(p.strip())}</p>"
        for p in paper.get("market_narrative", "").split("\n\n")
        if p.strip()
    )

    source_badge = f'<span class="top-story-tag {impact_cls}">{escape(impact)}</span>'
    authors_html = f'<span class="paper-authors">{escape(paper.get("authors", ""))}</span>' if paper.get("authors") else ""

    return f"""
<article class="research-card reveal" style="--d:{idx * 0.08:.2f}s">
  <div class="research-card-header">
    <div class="research-meta">
      <div class="card-tag">{source_badge}</div>
      <div class="research-source-row">
        <span class="research-source">{escape(paper.get('source', ''))}</span>
        <span class="research-dot">·</span>
        <span class="research-date">{escape(paper.get('published', ''))}</span>
        {authors_html}
      </div>
    </div>
    <div class="impact-meter">
      <div class="impact-label">Impact</div>
      <div class="impact-bar-wrap"><div class="impact-bar" style="width:{score_pct}%"></div></div>
      <div class="impact-score">{score}/10</div>
    </div>
  </div>

  <a class="research-title" href="{escape(paper.get('link', '#'))}" target="_blank" rel="noopener">{escape(paper.get('title', ''))}</a>

  <div class="research-section">
    <div class="research-section-label">💡 What it actually means</div>
    <div class="research-section-body eli5-body">
      {eli5_paras}
    </div>
  </div>

  <div class="research-section">
    <div class="research-section-label">⚙️ Technical breakdown</div>
    <div class="research-section-body">
      {tech_paras}
    </div>
  </div>

  <div class="research-section">
    <div class="research-section-label">📈 Market context</div>
    <div class="research-section-body">
      {market_paras}
    </div>
  </div>

  <div class="research-section">
    <div class="research-section-label">💼 Stock implications</div>
    <div class="stock-impl-grid">
      {stock_html}
    </div>
  </div>
</article>
"""


def generate_html(papers, date_str, display_date):
    cards_html = "\n".join(paper_card(p, i) for i, p in enumerate(papers))
    count = len(papers)
    top_paper = papers[0] if papers else {}
    hero_sub = f"Today: {escape(top_paper.get('title', '')[:80])}..." if top_paper else "No papers found today."

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Research · {display_date}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">
<link rel="stylesheet" href="briefing-style.css">
<style>
/* ── Research-specific styles ── */
.research-card {{
  background: var(--glass);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 32px;
  margin-bottom: 24px;
  transition: border-color 0.3s, box-shadow 0.3s;
  position: relative;
  overflow: hidden;
}}
.research-card::before {{
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent, rgba(79,142,247,0.5), transparent);
  opacity: 0; transition: opacity 0.3s;
}}
.research-card:hover {{ border-color: var(--border-b); box-shadow: 0 20px 60px rgba(0,0,0,0.4); }}
.research-card:hover::before {{ opacity: 1; }}

.research-card-header {{
  display: flex; align-items: flex-start; justify-content: space-between;
  gap: 20px; margin-bottom: 16px; flex-wrap: wrap;
}}
.research-meta {{ flex: 1; }}
.research-source-row {{
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin-top: 6px;
}}
.research-source {{ font-size: 12px; font-weight: 600; color: var(--blue); }}
.research-dot {{ color: var(--text-3); font-size: 12px; }}
.research-date {{ font-size: 12px; color: var(--text-3); }}
.paper-authors {{ font-size: 11px; color: var(--text-3); }}

.impact-meter {{
  display: flex; align-items: center; gap: 8px; flex-shrink: 0;
}}
.impact-label {{ font-size: 10px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-3); }}
.impact-bar-wrap {{
  width: 80px; height: 4px; background: rgba(255,255,255,0.08);
  border-radius: 2px; overflow: hidden;
}}
.impact-bar {{ height: 100%; background: linear-gradient(90deg, var(--blue), var(--green)); border-radius: 2px; }}
.impact-score {{ font-size: 11px; font-weight: 700; color: var(--text-2); min-width: 28px; text-align: right; }}

.research-title {{
  font-size: 20px; font-weight: 700; letter-spacing: -0.4px;
  line-height: 1.35; color: var(--text);
  text-decoration: none; display: block;
  margin-bottom: 24px;
  transition: color 0.2s;
}}
.research-title:hover {{ color: var(--blue); }}

.research-section {{ margin-bottom: 24px; }}
.research-section:last-child {{ margin-bottom: 0; }}
.research-section-label {{
  font-size: 11px; font-weight: 700; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--text-3);
  margin-bottom: 12px;
  display: flex; align-items: center; gap: 6px;
}}
.research-section-body {{ display: flex; flex-direction: column; gap: 12px; }}
.research-section-body p {{
  font-size: 14.5px; color: var(--text-2); line-height: 1.75;
}}
.eli5-body p {{
  font-size: 15px; color: var(--text); line-height: 1.8;
}}
.eli5-body p:first-child {{
  font-size: 15.5px; font-weight: 500; color: var(--text);
}}

.stock-impl-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 10px;
}}
.stock-impl {{
  border-radius: var(--radius-sm); padding: 12px 14px;
  border: 1px solid transparent;
  display: flex; flex-direction: column; gap: 4px;
}}
.stock-impl.sig-up {{ background: var(--green-dim); border-color: rgba(52,217,128,0.2); }}
.stock-impl.sig-down {{ background: var(--red-dim); border-color: rgba(247,82,82,0.2); }}
.stock-impl-header {{ display: flex; align-items: center; gap: 8px; }}
.impl-arrow {{ font-size: 12px; font-weight: 800; }}
.sig-up .impl-arrow {{ color: var(--green); }}
.sig-down .impl-arrow {{ color: var(--red); }}
.impl-ticker {{ font-size: 13px; font-weight: 800; letter-spacing: 0.03em; }}
.sig-up .impl-ticker {{ color: var(--green); }}
.sig-down .impl-ticker {{ color: var(--red); }}
.impl-reason {{ font-size: 12px; color: var(--text-2); line-height: 1.5; }}

.research-card + .research-card {{ border-top: none; }}

.research-divider {{
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--border), transparent);
  margin: 8px 0 32px;
}}

@media (max-width: 640px) {{
  .research-card {{ padding: 20px; }}
  .research-card-header {{ flex-direction: column; }}
  .stock-impl-grid {{ grid-template-columns: 1fr; }}
  .research-title {{ font-size: 17px; }}
}}
</style>
</head>
<body>

<!-- NAV -->
<nav id="site-nav">
  <div class="nav-inner">
    <a class="nav-brand" href="briefing.html">
      <span class="nav-brand-dot"></span>
      AI &amp; Chips Daily
    </a>
    <span class="nav-date">{display_date}</span>
    <button class="nav-smh-btn" onclick="(window.parent !== window ? window.parent.switchTab('smh') : window.location.href='smh.html')">
      📈 SMH Tracker
    </button>
  </div>
</nav>

<!-- DATE NAV -->
<div class="date-nav">
  <div class="date-nav-inner">
    <span class="date-nav-label">Edition</span>
    <div class="date-pills">
      <a href="briefing.html" class="date-pill" onclick="if(window.parent!==window){{window.parent.switchTab('briefing');return false;}}">📰 AI Briefing</a>
      <a href="research.html" class="date-pill active">🔬 Research · {display_date}</a>
    </div>
  </div>
</div>

<!-- HERO -->
<section class="hero" style="min-height: 40vh; padding: 80px 40px 60px;">
  <div class="hero-inner">
    <div class="hero-eyebrow">
      <span style="width:6px;height:6px;border-radius:50%;background:currentColor;animation:pulse 2s infinite;display:inline-block"></span>
      Research Desk · {display_date}
    </div>
    <h1 class="hero-h1" style="font-size: clamp(40px,6vw,72px)">AI Research<br><em>Explained</em></h1>
    <p class="hero-sub">The most impactful AI papers and white papers from the last 24 hours — translated into plain English with stock market implications.</p>
    <div class="hero-stats">
      <div class="hero-stat">
        <div class="hero-stat-val">{count}</div>
        <div class="hero-stat-label">Papers Today</div>
      </div>
      <div class="hero-divider"></div>
      <div class="hero-stat">
        <div class="hero-stat-val">24h</div>
        <div class="hero-stat-label">Lookback Window</div>
      </div>
      <div class="hero-divider"></div>
      <div class="hero-stat">
        <div class="hero-stat-val">Auto</div>
        <div class="hero-stat-label">Daily Update</div>
      </div>
    </div>
  </div>
</section>

<!-- PAPERS -->
<section class="page-section" style="padding-top: 40px;">
  <div class="section-wrap">
    <div class="section-header">
      <div class="section-eyebrow reveal">Today's Picks</div>
      <h2 class="section-title reveal" style="--d:0.05s">What researchers<br>published today.</h2>
    </div>
    {cards_html}
  </div>
</section>

<!-- FOOTER -->
<footer class="site-footer">
  <div class="footer-inner">
    <div>
      <div class="footer-brand">AI &amp; Chips Daily — Research Desk</div>
      <div class="footer-sub" style="margin-top:4px">{display_date} · Auto-generated by Claude · Updates daily 8am UTC</div>
    </div>
    <div class="footer-sub">
      <a href="https://amangulani-coder.github.io/ai-semis-briefing/">amangulani-coder.github.io/ai-semis-briefing</a>
    </div>
  </div>
</footer>

<script>
const revealObs = new IntersectionObserver((entries) => {{
  entries.forEach(e => {{ if (e.isIntersecting) {{ e.target.classList.add('in'); revealObs.unobserve(e.target); }} }});
}}, {{ threshold: 0.04, rootMargin: '0px 0px -30px 0px' }});
document.querySelectorAll('.reveal').forEach(el => revealObs.observe(el));
</script>
</body>
</html>
"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Fetching papers for {TODAY.strftime('%Y-%m-%d')}...", file=sys.stderr)

    papers = fetch_arxiv_papers()
    print(f"  arXiv: {len(papers)} papers", file=sys.stderr)

    posts = fetch_blog_posts()
    print(f"  Blogs: {len(posts)} posts", file=sys.stderr)

    if not papers and not posts:
        print("No papers found — keeping existing research.html", file=sys.stderr)
        sys.exit(0)

    selected = select_and_analyze(papers, posts)
    print(f"  Claude selected: {len(selected)} items", file=sys.stderr)

    if not selected:
        print("Claude returned no selections — keeping existing research.html", file=sys.stderr)
        sys.exit(0)

    date_str = TODAY.strftime("%Y-%m-%d")
    display_date = TODAY.strftime("%A, %B %-d, %Y")

    html = generate_html(selected, date_str, display_date)

    output_path = os.path.join(os.path.dirname(__file__), "..", "research.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Written: research.html ({len(html):,} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()
