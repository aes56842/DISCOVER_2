#!/usr/bin/env python3
"""
Updates the "Featured" section on discover-media.html with the latest
YouTube video, podcast episode, and Substack post.

Run manually:      python3 scripts/update_featured.py
Run automatically:  via .github/workflows/update-featured.yml (hourly)

--------------------------------------------------------------------------
EDIT THESE THREE CONSTANTS WHEN A SOURCE CHANGES
--------------------------------------------------------------------------
"""

import html
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

# ── CONFIG — edit these when a source changes ────────────────────────────
YOUTUBE_CHANNEL_ID = "UCyIXWT1VEbeD9kfMpJA1ARw"

# Apple Podcasts ID for the show. Used only to look up the real RSS feed
# (Spotify for Podcasters / Anchor) — no login required, this is a public API.
APPLE_PODCAST_ID = "1853966405"

# TODO: replace with Julie's real Substack publication once she creates one.
# Format is always https://<publication>.substack.com/feed
SUBSTACK_FEED_URL = "https://lizw6866gmailcom.substack.com/feed"

HTML_FILE = "discover-media.html"
# ───────────────────────────────────────────────────────────────────────

UA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers=UA_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_json(url: str) -> dict:
    import json
    return json.loads(fetch(url))


def fetch_feed_via_proxy(feed_url: str) -> dict | None:
    """
    Fallback for feeds that block direct requests from cloud/datacenter IPs
    (Substack does this fairly aggressively). Routes the fetch through
    rss2json.com, a free public RSS-reading service, which fetches from
    its own servers and is often not on the same blocklist.
    Returns the first item as a plain dict, or None if it also fails.
    """
    import json
    import urllib.parse

    proxy_url = "https://api.rss2json.com/v1/api.json?rss_url=" + urllib.parse.quote(feed_url, safe="")
    try:
        data = json.loads(fetch(proxy_url, timeout=20))
    except Exception as e:
        print(f"  (proxy fallback also failed: {e})", file=sys.stderr)
        return None
    if data.get("status") != "ok" or not data.get("items"):
        return None
    return data["items"][0]


def relative_time(dt: datetime) -> str:
    """Turn a datetime into 'New today' / '3 days ago' / 'on Jan 4' style text."""
    now = datetime.now(timezone.utc)
    delta = now - dt.astimezone(timezone.utc)
    days = delta.days
    if days <= 0:
        return "New today"
    if days == 1:
        return "1 day ago"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        weeks = days // 7
        return f"{weeks} week{'s' if weeks != 1 else ''} ago"
    return f"on {dt.strftime('%b %-d, %Y')}"


# ── SOURCE 1: YouTube ─────────────────────────────────────────────────────
def get_latest_youtube():
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={YOUTUBE_CHANNEL_ID}"
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "media": "http://search.yahoo.com/mrss/",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }
    root = ET.fromstring(fetch(url))
    entry = root.find("atom:entry", ns)
    if entry is None:
        return None

    video_id = entry.find("yt:videoId", ns).text
    title = entry.find("atom:title", ns).text
    published = entry.find("atom:published", ns).text
    dt = datetime.fromisoformat(published.replace("Z", "+00:00"))

    author_el = entry.find("atom:author/atom:name", ns)
    author = author_el.text if author_el is not None else "DISCoVER"

    thumb_el = entry.find("media:group/media:thumbnail", ns)
    thumb = thumb_el.get("url") if thumb_el is not None else f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

    return {
        "href": f"https://www.youtube.com/watch?v={video_id}",
        "title": title,
        "author": author,
        "thumb": thumb,
        "dt": dt,
    }


# ── SOURCE 2: Podcast (via Apple lookup → real RSS feed) ──────────────────
def get_latest_podcast():
    lookup = fetch_json(f"https://itunes.apple.com/lookup?id={APPLE_PODCAST_ID}")
    results = lookup.get("results", [])
    if not results:
        return None
    feed_url = results[0].get("feedUrl")
    if not feed_url:
        return None

    root = ET.fromstring(fetch(feed_url))
    item = root.find("./channel/item")
    if item is None:
        return None

    title = item.findtext("title")
    link = item.findtext("link") or feed_url
    pub_date = item.findtext("pubDate")
    dt = parsedate_to_datetime(pub_date)

    itunes_ns = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
    duration = item.findtext(f"{itunes_ns}duration")
    duration_txt = None
    if duration:
        # duration may be "HH:MM:SS", "MM:SS", or plain seconds
        if ":" in duration:
            parts = [int(p) for p in duration.split(":")]
            mins = parts[-2] if len(parts) >= 2 else 0
        else:
            mins = int(duration) // 60
        if mins:
            duration_txt = f"{mins} min"

    return {"href": link, "title": title, "duration": duration_txt, "dt": dt}


# ── SOURCE 3: Substack ─────────────────────────────────────────────────────
def parse_flexible_date(raw: str) -> datetime:
    """Handles both RFC-822 (real RSS) and rss2json's 'YYYY-MM-DD HH:MM:SS' style dates."""
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)


def get_latest_substack():
    try:
        root = ET.fromstring(fetch(SUBSTACK_FEED_URL))
        item = root.find("./channel/item")
        if item is None:
            return None
        title = item.findtext("title")
        link = item.findtext("link")
        dt = parse_flexible_date(item.findtext("pubDate"))
        description = item.findtext("description") or ""
    except Exception as e:
        print(f"Substack direct fetch failed ({e}) — trying proxy fallback…", file=sys.stderr)
        item = fetch_feed_via_proxy(SUBSTACK_FEED_URL)
        if not item:
            raise RuntimeError("direct fetch and proxy fallback both failed")
        title = item.get("title")
        link = item.get("link")
        dt = parse_flexible_date(item.get("pubDate"))
        description = item.get("description") or item.get("content") or ""

    word_count = len(re.sub(r"<[^>]+>", " ", description).split())
    read_mins = max(1, round(word_count / 200)) if word_count else None

    return {"href": link, "title": title, "read_mins": read_mins, "dt": dt}


# ── HTML block builders ─────────────────────────────────────────────────
def build_youtube_block(v: dict) -> str:
    title_esc = html.escape(v["title"])
    author_esc = html.escape(v["author"])
    data_title = html.escape(f'{v["title"]} {v["author"]}')
    return f'''<!-- AUTO-FEATURED:YOUTUBE:START (do not hand-edit between these markers — the hourly script overwrites it) -->
      <a href="{v['href']}" class="video-card featured-item" data-topic="explainer" data-title="{data_title}" style="display:block;text-decoration:none;color:inherit;">
        <div class="thumb">
          <img src="{v['thumb']}" alt="{title_esc}" onerror="this.remove()">
        </div>
        <div class="play-btn"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></div>
        <div class="video-meta">
          <span class="format-badge video"><span class="dot"></span>VIDEO · YOUTUBE</span>
          <h3>{title_esc}</h3>
          <div class="sub">{author_esc} <span class="duration-chip">{relative_time(v['dt'])}</span></div>
        </div>
      </a>
      <!-- AUTO-FEATURED:YOUTUBE:END -->'''


def build_podcast_block(p: dict) -> str:
    title_esc = html.escape(p["title"])
    data_title = html.escape(p["title"])
    sub_bits = ["Spotify"]
    if p.get("duration"):
        sub_bits.append(p["duration"])
    sub_bits.append(relative_time(p["dt"]))
    sub = " · ".join(sub_bits)
    return f'''<!-- AUTO-FEATURED:PODCAST:START (do not hand-edit between these markers — the hourly script overwrites it) -->
        <a href="{p['href']}" class="aside-item featured-item" data-title="{data_title}" style="text-decoration:none;color:inherit;">
          <div class="aside-thumb">
            <svg viewBox="0 0 24 24" fill="none" stroke="#C43D4E" stroke-width="1.6"><path d="M12 1a4 4 0 0 0-4 4v6a4 4 0 0 0 8 0V5a4 4 0 0 0-4-4z"/><path d="M19 10v1a7 7 0 0 1-14 0v-1M12 18v4"/></svg>
          </div>
          <div class="aside-body">
            <h4>{title_esc}</h4>
            <div class="sub">{sub}</div>
          </div>
        </a>
        <!-- AUTO-FEATURED:PODCAST:END -->'''


def build_substack_block(s: dict) -> str:
    title_esc = html.escape(s["title"])
    data_title = html.escape(s["title"])
    sub_bits = ["Substack"]
    if s.get("read_mins"):
        sub_bits.append(f"{s['read_mins']} min read")
    sub_bits.append(relative_time(s["dt"]))
    sub = " · ".join(sub_bits)
    return f'''<!-- AUTO-FEATURED:SUBSTACK:START (do not hand-edit between these markers — the hourly script overwrites it) -->
        <a href="{s['href']}" class="aside-item featured-item" data-title="{data_title}" style="text-decoration:none;color:inherit;">
          <div class="aside-thumb">
            <svg viewBox="0 0 24 24" fill="none" stroke="#6B74E0" stroke-width="1.6"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
          </div>
          <div class="aside-body">
            <h4>{title_esc}</h4>
            <div class="sub">{sub}</div>
          </div>
        </a>
        <!-- AUTO-FEATURED:SUBSTACK:END -->'''


def replace_block(content: str, marker: str, new_block: str) -> str:
    pattern = re.compile(
        rf"<!-- AUTO-FEATURED:{marker}:START.*?AUTO-FEATURED:{marker}:END -->",
        re.DOTALL,
    )
    if not pattern.search(content):
        print(f"WARNING: markers for {marker} not found — skipping.", file=sys.stderr)
        return content
    return pattern.sub(new_block.replace("\\", "\\\\"), content, count=1)


def main():
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    try:
        yt = get_latest_youtube()
        if yt:
            content = replace_block(content, "YOUTUBE", build_youtube_block(yt))
    except Exception as e:
        print(f"YouTube fetch failed: {e}", file=sys.stderr)

    try:
        pod = get_latest_podcast()
        if pod:
            content = replace_block(content, "PODCAST", build_podcast_block(pod))
    except Exception as e:
        print(f"Podcast fetch failed: {e}", file=sys.stderr)

    try:
        sub = get_latest_substack()
        if sub:
            content = replace_block(content, "SUBSTACK", build_substack_block(sub))
    except Exception as e:
        print(f"Substack fetch failed: {e}", file=sys.stderr)

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print("Done.")


if __name__ == "__main__":
    main()


def fetch(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_json(url: str) -> dict:
    import json
    return json.loads(fetch(url))


def relative_time(dt: datetime) -> str:
    """Turn a datetime into 'New today' / '3 days ago' / 'on Jan 4' style text."""
    now = datetime.now(timezone.utc)
    delta = now - dt.astimezone(timezone.utc)
    days = delta.days
    if days <= 0:
        return "New today"
    if days == 1:
        return "1 day ago"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        weeks = days // 7
        return f"{weeks} week{'s' if weeks != 1 else ''} ago"
    return f"on {dt.strftime('%b %-d, %Y')}"


# ── SOURCE 1: YouTube ─────────────────────────────────────────────────────
def get_latest_youtube():
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={YOUTUBE_CHANNEL_ID}"
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "media": "http://search.yahoo.com/mrss/",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }
    root = ET.fromstring(fetch(url))
    entry = root.find("atom:entry", ns)
    if entry is None:
        return None

    video_id = entry.find("yt:videoId", ns).text
    title = entry.find("atom:title", ns).text
    published = entry.find("atom:published", ns).text
    dt = datetime.fromisoformat(published.replace("Z", "+00:00"))

    author_el = entry.find("atom:author/atom:name", ns)
    author = author_el.text if author_el is not None else "DISCoVER"

    thumb_el = entry.find("media:group/media:thumbnail", ns)
    thumb = thumb_el.get("url") if thumb_el is not None else f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

    return {
        "href": f"https://www.youtube.com/watch?v={video_id}",
        "title": title,
        "author": author,
        "thumb": thumb,
        "dt": dt,
    }


# ── SOURCE 2: Podcast (via Apple lookup → real RSS feed) ──────────────────
def get_latest_podcast():
    lookup = fetch_json(f"https://itunes.apple.com/lookup?id={APPLE_PODCAST_ID}")
    results = lookup.get("results", [])
    if not results:
        return None
    feed_url = results[0].get("feedUrl")
    if not feed_url:
        return None

    root = ET.fromstring(fetch(feed_url))
    item = root.find("./channel/item")
    if item is None:
        return None

    title = item.findtext("title")
    link = item.findtext("link") or feed_url
    pub_date = item.findtext("pubDate")
    dt = parsedate_to_datetime(pub_date)

    itunes_ns = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
    duration = item.findtext(f"{itunes_ns}duration")
    duration_txt = None
    if duration:
        # duration may be "HH:MM:SS", "MM:SS", or plain seconds
        if ":" in duration:
            parts = [int(p) for p in duration.split(":")]
            mins = parts[-2] if len(parts) >= 2 else 0
        else:
            mins = int(duration) // 60
        if mins:
            duration_txt = f"{mins} min"

    return {"href": link, "title": title, "duration": duration_txt, "dt": dt}


# ── SOURCE 3: Substack ─────────────────────────────────────────────────────
def get_latest_substack():
    root = ET.fromstring(fetch(SUBSTACK_FEED_URL))
    item = root.find("./channel/item")
    if item is None:
        return None

    title = item.findtext("title")
    link = item.findtext("link")
    pub_date = item.findtext("pubDate")
    dt = parsedate_to_datetime(pub_date)

    description = item.findtext("description") or ""
    word_count = len(re.sub(r"<[^>]+>", " ", description).split())
    read_mins = max(1, round(word_count / 200)) if word_count else None

    return {"href": link, "title": title, "read_mins": read_mins, "dt": dt}


# ── HTML block builders ─────────────────────────────────────────────────
def build_youtube_block(v: dict) -> str:
    title_esc = html.escape(v["title"])
    author_esc = html.escape(v["author"])
    data_title = html.escape(f'{v["title"]} {v["author"]}')
    return f'''<!-- AUTO-FEATURED:YOUTUBE:START (do not hand-edit between these markers — the hourly script overwrites it) -->
      <a href="{v['href']}" class="video-card featured-item" data-topic="explainer" data-title="{data_title}" style="display:block;text-decoration:none;color:inherit;">
        <div class="thumb">
          <img src="{v['thumb']}" alt="{title_esc}" onerror="this.remove()">
        </div>
        <div class="play-btn"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></div>
        <div class="video-meta">
          <span class="format-badge video"><span class="dot"></span>VIDEO · YOUTUBE</span>
          <h3>{title_esc}</h3>
          <div class="sub">{author_esc} <span class="duration-chip">{relative_time(v['dt'])}</span></div>
        </div>
      </a>
      <!-- AUTO-FEATURED:YOUTUBE:END -->'''


def build_podcast_block(p: dict) -> str:
    title_esc = html.escape(p["title"])
    data_title = html.escape(p["title"])
    sub_bits = ["Spotify"]
    if p.get("duration"):
        sub_bits.append(p["duration"])
    sub_bits.append(relative_time(p["dt"]))
    sub = " · ".join(sub_bits)
    return f'''<!-- AUTO-FEATURED:PODCAST:START (do not hand-edit between these markers — the hourly script overwrites it) -->
        <a href="{p['href']}" class="aside-item featured-item" data-title="{data_title}" style="text-decoration:none;color:inherit;">
          <div class="aside-thumb">
            <svg viewBox="0 0 24 24" fill="none" stroke="#C43D4E" stroke-width="1.6"><path d="M12 1a4 4 0 0 0-4 4v6a4 4 0 0 0 8 0V5a4 4 0 0 0-4-4z"/><path d="M19 10v1a7 7 0 0 1-14 0v-1M12 18v4"/></svg>
          </div>
          <div class="aside-body">
            <h4>{title_esc}</h4>
            <div class="sub">{sub}</div>
          </div>
        </a>
        <!-- AUTO-FEATURED:PODCAST:END -->'''


def build_substack_block(s: dict) -> str:
    title_esc = html.escape(s["title"])
    data_title = html.escape(s["title"])
    sub_bits = ["Substack"]
    if s.get("read_mins"):
        sub_bits.append(f"{s['read_mins']} min read")
    sub_bits.append(relative_time(s["dt"]))
    sub = " · ".join(sub_bits)
    return f'''<!-- AUTO-FEATURED:SUBSTACK:START (do not hand-edit between these markers — the hourly script overwrites it) -->
        <a href="{s['href']}" class="aside-item featured-item" data-title="{data_title}" style="text-decoration:none;color:inherit;">
          <div class="aside-thumb">
            <svg viewBox="0 0 24 24" fill="none" stroke="#6B74E0" stroke-width="1.6"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
          </div>
          <div class="aside-body">
            <h4>{title_esc}</h4>
            <div class="sub">{sub}</div>
          </div>
        </a>
        <!-- AUTO-FEATURED:SUBSTACK:END -->'''


def replace_block(content: str, marker: str, new_block: str) -> str:
    pattern = re.compile(
        rf"<!-- AUTO-FEATURED:{marker}:START.*?AUTO-FEATURED:{marker}:END -->",
        re.DOTALL,
    )
    if not pattern.search(content):
        print(f"WARNING: markers for {marker} not found — skipping.", file=sys.stderr)
        return content
    return pattern.sub(new_block.replace("\\", "\\\\"), content, count=1)


def main():
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    try:
        yt = get_latest_youtube()
        if yt:
            content = replace_block(content, "YOUTUBE", build_youtube_block(yt))
    except Exception as e:
        print(f"YouTube fetch failed: {e}", file=sys.stderr)

    try:
        pod = get_latest_podcast()
        if pod:
            content = replace_block(content, "PODCAST", build_podcast_block(pod))
    except Exception as e:
        print(f"Podcast fetch failed: {e}", file=sys.stderr)

    try:
        sub = get_latest_substack()
        if sub:
            content = replace_block(content, "SUBSTACK", build_substack_block(sub))
    except Exception as e:
        print(f"Substack fetch failed: {e}", file=sys.stderr)

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print("Done.")


if __name__ == "__main__":
    main()
