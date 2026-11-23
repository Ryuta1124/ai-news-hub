"""Fetch all feeds once and write static/news.json for the hosted (static) site.

Run by GitHub Actions every 3 hours; also works locally:  py build_static.py
"""

import json
from pathlib import Path

import server

OUT = Path(__file__).resolve().parent / "static" / "news.json"

# Seed the cache with the previous build so temporarily failing sources keep their stories.
if OUT.exists():
    try:
        server._cache = json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        pass

snapshot = server.refresh()
OUT.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

ok = sum(1 for s in snapshot["sources"] if not s["error"])
print(f"Wrote {len(snapshot['items'])} stories from {ok}/{len(snapshot['sources'])} sources to {OUT}")
for s in snapshot["sources"]:
    if s["error"]:
        print(f"  ! {s['name']}: {s['error']}")
