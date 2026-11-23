"""AI News Hub — a tiny, dependency-free news aggregator for AI.

Run:  py server.py        then open http://localhost:8795
Uses only the Python standard library.
"""

from __future__ import annotations

import gzip
import html
import json
import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
CACHE_FILE = ROOT / "data" / "cache.json"
PORT = int(__import__("os").environ.get("PORT", 8795))
CACHE_TTL = 20 * 60  # seconds
MAX_PER_FEED = 30
USER_AGENT = "Mozilla/5.0 (compatible; AINewsHub/1.0; personal RSS reader)"

# category: labs | news | practical | community | research
FEEDS = [
    # --- Official AI labs & big tech
    {"name": "OpenAI", "url": "https://openai.com/news/rss.xml", "category": "labs"},
    {"name": "Google DeepMind", "url": "https://deepmind.google/blog/rss.xml", "category": "labs"},
    {"name": "Google AI", "url": "https://blog.google/technology/ai/rss/", "category": "labs"},
    {"name": "Microsoft AI", "url": "https://www.microsoft.com/en-us/ai/blog/feed/", "category": "labs"},
    {"name": "NVIDIA", "url": "https://blogs.nvidia.com/feed/", "category": "labs"},
    {"name": "Hugging Face", "url": "https://huggingface.co/blog/feed.xml", "category": "labs"},
    # --- News outlets
    {"name": "The Verge", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "category": "news"},
    {"name": "TechCrunch", "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "category": "news"},
    {"name": "MIT Tech Review", "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed", "category": "news"},
    {"name": "Ars Technica", "url": "https://arstechnica.com/ai/feed/", "category": "news"},
    {"name": "VentureBeat", "url": "https://venturebeat.com/category/ai/feed/", "category": "news"},
    # --- Practical / how-to writers
    {"name": "Simon Willison", "url": "https://simonwillison.net/atom/everything/", "category": "practical"},
    {"name": "One Useful Thing", "url": "https://www.oneusefulthing.org/feed", "category": "practical"},
    {"name": "Latent Space", "url": "https://www.latent.space/feed", "category": "practical"},
    {"name": "Ben's Bites", "url": "https://www.bensbites.com/feed", "category": "practical"},
    # --- Community
    {"name": "Hacker News", "url": "https://hnrss.org/newest?q=AI&points=150", "category": "community"},
]

# Words that make an item useful as a personal "lifehack" (practical, actionable).
LIFEHACK_PATTERNS = [
    r"\bhow to\b", r"\bhow i\b", r"\btips?\b", r"\btricks?\b", r"\bguide\b", r"\btutorial\b",
    r"\bprompt(s|ing)?\b", r"\bworkflow", r"\bproductiv", r"\bautomat", r"\buse cases?\b",
    r"\bcheat ?sheet\b", r"\bstep[- ]by[- ]step\b", r"\bbest practices\b", r"\bhands[- ]on\b",
    r"\bbeginner", r"\blearn(ing)? to\b", r"\bfree tool", r"\bnew feature",
    r"\bnow lets you\b", r"\byou can now\b", r"\bways to\b", r"\bsave time\b",
    r"\bnotebooklm\b", r"\bvibe cod", r"\bclaude code\b", r"\bcustom gpt", r"\btry\b",
]
LIFEHACK_RE = re.compile("|".join(LIFEHACK_PATTERNS), re.I)

TOPIC_RULES = {
    "Models": r"\b(gpt|claude|gemini|llama|mistral|qwen|deepseek|grok|model|llm|reasoning)\b",
    "Tools & Apps": r"\b(app|tool|feature|chatgpt|copilot|notebooklm|cursor|extension|plugin|launch)",
    "Agents": r"\bagent",
    "Coding": r"\b(code|coding|developer|programming|github|api|sdk)\b",
    "Images & Video": r"\b(image|video|sora|veo|midjourney|diffusion|voice|audio|music)\b",
    "Policy & Safety": r"\b(regulat|law|policy|safety|copyright|lawsuit|eu ai act|ethic|alignment)",
    "Business": r"\b(funding|raises|valuation|acquir|revenue|ipo|startup|billion|deal|layoff)",
    "Research": r"\b(paper|research|benchmark|study|arxiv|dataset|scientist)",
    "Hardware": r"\b(chip|gpu|nvidia|tpu|data ?cent|compute|semiconductor)",
}
TOPIC_RES = {k: re.compile(v, re.I) for k, v in TOPIC_RULES.items()}

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "media": "http://search.yahoo.com/mrss/",
}

_lock = threading.Lock()
_cache: dict = {"fetched_at": 0, "items": [], "sources": []}


# ----------------------------------------------------------------- helpers
def strip_html(text: str | None, limit: int = 320) -> str:
    if not text:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def first_image(raw: str | None) -> str:
    if not raw:
        return ""
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw, re.I)
    return html.unescape(m.group(1)) if m and m.group(1).startswith("http") else ""


def parse_date(value: str | None) -> float:
    if not value:
        return 0.0
    value = value.strip()
    try:
        return parsedate_to_datetime(value).timestamp()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def text_of(el, path: str) -> str:
    found = el.find(path, NS)
    return (found.text or "").strip() if found is not None and found.text else ""


def classify(title: str, summary: str, category: str) -> tuple[list[str], bool]:
    blob = f"{title} {summary}"
    topics = [name for name, rx in TOPIC_RES.items() if rx.search(blob)][:3]
    lifehack = category == "practical" or bool(LIFEHACK_RE.search(title))
    return topics, lifehack


# ----------------------------------------------------------------- parsing
def parse_feed(xml_bytes: bytes, feed: dict) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    entries = []
    if root.tag.endswith("rss") or root.find("channel") is not None:
        for it in root.iter("item"):
            raw_desc = text_of(it, "content:encoded") or text_of(it, "description")
            img = ""
            for tag in ("media:content", "media:thumbnail", "enclosure"):
                m = it.find(tag, NS)
                if m is not None and m.get("url", "").startswith("http") and \
                        (tag != "enclosure" or m.get("type", "").startswith("image")):
                    img = m.get("url")
                    break
            entries.append({
                "title": text_of(it, "title"),
                "link": text_of(it, "link"),
                "raw": raw_desc,
                "date": parse_date(text_of(it, "pubDate") or text_of(it, "dc:date")),
                "image": img or first_image(raw_desc),
            })
    else:  # Atom
        for it in root.findall("atom:entry", NS):
            link = ""
            for l in it.findall("atom:link", NS):
                if l.get("rel", "alternate") == "alternate":
                    link = l.get("href", "")
                    break
            raw_desc = text_of(it, "atom:summary") or text_of(it, "atom:content")
            entries.append({
                "title": text_of(it, "atom:title"),
                "link": link,
                "raw": raw_desc,
                "date": parse_date(text_of(it, "atom:published") or text_of(it, "atom:updated")),
                "image": first_image(raw_desc),
            })

    items = []
    for e in entries[:MAX_PER_FEED]:
        title = strip_html(e["title"], 240)
        if not title or not e["link"]:
            continue
        summary = strip_html(e["raw"])
        topics, lifehack = classify(title, summary, feed["category"])
        items.append({
            "id": e["link"],
            "title": title,
            "link": e["link"],
            "summary": summary,
            "image": e["image"],
            "date": e["date"],
            "source": feed["name"],
            "category": feed["category"],
            "topics": topics,
            "lifehack": lifehack,
        })
    return items


def fetch_feed(feed: dict) -> tuple[dict, list[dict], str | None]:
    try:
        req = urllib.request.Request(feed["url"], headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        return feed, parse_feed(data, feed), None
    except Exception as exc:  # network / parse errors are reported, not fatal
        return feed, [], f"{type(exc).__name__}: {exc}"[:200]


def refresh() -> dict:
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch_feed, FEEDS))
    seen, items, sources = set(), [], []
    for feed, feed_items, err in results:
        if err:  # keep the last good copy of a temporarily failing source
            feed_items = [i for i in _cache.get("items", []) if i["source"] == feed["name"]]
        sources.append({"name": feed["name"], "category": feed["category"],
                        "count": len(feed_items), "error": err})
        for it in feed_items:
            key = it["link"].split("?")[0].rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            items.append(it)
    items.sort(key=lambda x: x["date"], reverse=True)
    snapshot = {"fetched_at": time.time(), "items": items, "sources": sources}
    try:
        CACHE_FILE.parent.mkdir(exist_ok=True)
        CACHE_FILE.write_text(json.dumps(snapshot), encoding="utf-8")
    except OSError:
        pass
    return snapshot


def get_news(force: bool = False) -> dict:
    global _cache
    with _lock:
        if not _cache["items"] and CACHE_FILE.exists():
            try:
                _cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        if force or time.time() - _cache["fetched_at"] > CACHE_TTL:
            _cache = refresh()
        return _cache


# ----------------------------------------------------------------- server
class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive with explicit Content-Length

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/news":
            force = parse_qs(url.query).get("refresh") == ["1"]
            body = json.dumps(get_news(force)).encode("utf-8")
            gz = "gzip" in self.headers.get("Accept-Encoding", "")
            if gz:
                body = gzip.compress(body)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            if gz:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if url.path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, fmt, *args):
        if args and "/api/" in str(args[0]):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    print(f"AI News Hub running at http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
