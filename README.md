# AI News Hub

A personal, dependency-free AI news reader. It pulls the latest stories from 16 English sources
(AI labs, tech news, hands-on writers, Hacker News) and highlights practical **Lifehacks**.

## Run

Double-click `start.bat`, or:

```
py server.py
```

Then open http://localhost:8795

## Features

- Tabs: All / 💡 Lifehacks / AI Labs / News / Practical / Community / ★ Saved
- Topic chips (Models, Agents, Coding, Tools & Apps, …), search, time range
- ★ Save for later, read tracking, "NEW since last visit" markers (stored in your browser)
- Feeds are cached for 20 minutes (`data/cache.json`); **Refresh** fetches immediately

## Customize

Edit `FEEDS` in `server.py` to add or remove sources, and `LIFEHACK_PATTERNS` to tune what counts
as a lifehack.
