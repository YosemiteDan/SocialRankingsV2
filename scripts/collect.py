"""
Daily collector: looks up each athlete's public Instagram profile and saves
today's numbers to data/history.csv.

You normally don't run this yourself - GitHub runs it every morning.

Where the numbers come from (picked automatically):
  1. Apify  - used if an APIFY_TOKEN secret is set. Paid, but reliable.
  2. Instagram directly - the default. Free, but Instagram may block it.

Optional: an IG_SESSIONID secret makes the direct method look like a
logged-in visitor, which gets blocked less often (see README for the risks).
"""

import csv
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
ATHLETES_FILE = ROOT / "data" / "athletes.csv"
HISTORY_FILE = ROOT / "data" / "history.csv"
LAST_RUN_FILE = ROOT / "data" / "last_run.json"

HISTORY_COLUMNS = [
    "date", "instagram", "followers", "following", "posts",
    "avg_likes", "avg_comments", "engagement_rate", "posts_last_30d", "source",
]

# Pause between profiles, in seconds. Slow on purpose: fast = blocked.
MIN_WAIT, MAX_WAIT = 8, 20

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-AU,en;q=0.9",
    # The ID Instagram's own website sends with this request.
    "X-IG-App-ID": "936619743392459",
}


class Blocked(Exception):
    """Instagram has started refusing requests - stop for today."""


# ---------------------------------------------------------------- helpers

def parse_count(text):
    """Turn '1.2M', '15,300' or '987K' into a whole number."""
    text = text.strip().replace(",", "")
    mult = 1
    if text[-1:].upper() in ("K", "M", "B"):
        mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[text[-1].upper()]
        text = text[:-1]
    return int(round(float(text) * mult))


def engagement_from_posts(posts, followers, now_ts):
    """posts: list of dicts with likes, comments, timestamp (unix seconds)."""
    if not posts:
        return None, None, None, None
    likes = [p["likes"] for p in posts if p.get("likes") is not None and p["likes"] >= 0]
    comments = [p["comments"] for p in posts if p.get("comments") is not None]
    avg_likes = round(sum(likes) / len(likes)) if likes else None
    avg_comments = round(sum(comments) / len(comments)) if comments else None
    rate = None
    if followers and avg_likes is not None:
        rate = round(100 * (avg_likes + (avg_comments or 0)) / followers, 3)
    recent = sum(1 for p in posts if p.get("timestamp") and now_ts - p["timestamp"] <= 30 * 86400)
    return avg_likes, avg_comments, rate, recent


def row(handle, followers, following, posts, eng, source):
    avg_likes, avg_comments, rate, recent = eng
    return {
        "instagram": handle, "followers": followers, "following": following,
        "posts": posts, "avg_likes": avg_likes, "avg_comments": avg_comments,
        "engagement_rate": rate, "posts_last_30d": recent, "source": source,
    }


# ------------------------------------------------ method 1: Instagram itself

def make_session():
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    sessionid = os.environ.get("IG_SESSIONID", "").strip()
    if sessionid:
        s.cookies.set("sessionid", sessionid, domain=".instagram.com")
    return s


def parse_profile_json(handle, payload, now_ts):
    user = (payload.get("data") or {}).get("user")
    if not user:
        return None
    followers = user["edge_followed_by"]["count"]
    timeline = user.get("edge_owner_to_timeline_media") or {}
    posts = []
    for edge in timeline.get("edges", []):
        n = edge.get("node", {})
        likes = (n.get("edge_liked_by") or n.get("edge_media_preview_like") or {}).get("count")
        posts.append({
            "likes": likes,
            "comments": (n.get("edge_media_to_comment") or {}).get("count"),
            "timestamp": n.get("taken_at_timestamp"),
        })
    return row(handle, followers, user["edge_follow"]["count"], timeline.get("count"),
               engagement_from_posts(posts, followers, now_ts), "instagram-json")


def parse_profile_html(handle, html):
    """Backup: the page's preview text reads '1.2M Followers, 500 Following, 300 Posts'."""
    m = re.search(
        r'content="([\d.,]+[KMB]?) Followers, ([\d.,]+[KMB]?) Following, ([\d.,]+[KMB]?) Posts',
        html, re.IGNORECASE)
    if not m:
        return None
    f, g, p = (parse_count(x) for x in m.groups())
    return row(handle, f, g, p, (None, None, None, None), "instagram-html")


def fetch_direct(session, handle, now_ts):
    url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={handle}"
    r = session.get(url, timeout=30)
    if r.status_code == 404:
        raise LookupError("profile not found - check the handle")
    if r.status_code in (401, 403, 429):
        # Try the plain profile page once before giving up.
        page = session.get(f"https://www.instagram.com/{handle}/", timeout=30)
        result = parse_profile_html(handle, page.text) if page.ok else None
        if result:
            return result
        raise Blocked(f"Instagram answered {r.status_code}")
    r.raise_for_status()
    try:
        result = parse_profile_json(handle, r.json(), now_ts)
    except ValueError:
        result = None
    if result is None:
        raise LookupError("unexpected response - Instagram may have changed its page")
    return result


def collect_direct(handles, now_ts):
    session = make_session()
    results, failures = {}, {}
    for i, handle in enumerate(handles):
        if i:
            time.sleep(random.uniform(MIN_WAIT, MAX_WAIT))
        try:
            results[handle] = fetch_direct(session, handle, now_ts)
            print(f"  ok   @{handle}: {results[handle]['followers']:,} followers")
        except Blocked as e:
            print(f"  STOP @{handle}: {e}. Skipping the rest until tomorrow.")
            for h in handles[i:]:
                failures[h] = f"blocked ({e})"
            break
        except Exception as e:  # noqa: BLE001 - keep going for other players
            print(f"  fail @{handle}: {e}")
            failures[handle] = str(e)
    return results, failures


# ------------------------------------------------------ method 2: Apify

def collect_apify(handles, now_ts, token):
    url = ("https://api.apify.com/v2/acts/apify~instagram-profile-scraper/"
           "run-sync-get-dataset-items")
    r = requests.post(url, json={"usernames": handles}, timeout=600,
                      headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    results = {}
    for item in r.json():
        handle = (item.get("username") or "").lower()
        if not handle or item.get("followersCount") is None:
            continue
        posts = []
        for p in item.get("latestPosts") or []:
            ts = p.get("timestamp")
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            posts.append({"likes": p.get("likesCount"), "comments": p.get("commentsCount"),
                          "timestamp": ts})
        followers = item["followersCount"]
        results[handle] = row(handle, followers, item.get("followsCount"), item.get("postsCount"),
                              engagement_from_posts(posts, followers, now_ts), "apify")
        print(f"  ok   @{handle}: {followers:,} followers")
    failures = {h: "not returned by Apify" for h in handles if h not in results}
    return results, failures


# ------------------------------------------------------------- saving

def save(results, today):
    existing = []
    if HISTORY_FILE.exists():
        with HISTORY_FILE.open(newline="") as f:
            existing = [r for r in csv.DictReader(f)
                        if not (r["date"] == today and r["instagram"] in results)]
    for handle, r in results.items():
        existing.append({"date": today, **r})
    existing.sort(key=lambda r: (r["date"], r["instagram"]))
    with HISTORY_FILE.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_COLUMNS)
        w.writeheader()
        for r in existing:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in HISTORY_COLUMNS})


def main():
    with ATHLETES_FILE.open(newline="") as f:
        handles = [r["instagram"].strip().lstrip("@").lower()
                   for r in csv.DictReader(f) if r["instagram"].strip()]

    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    token = os.environ.get("APIFY_TOKEN", "").strip()
    method = "apify" if token else "instagram"
    print(f"Collecting {len(handles)} profiles via {method} for {today}")

    if token:
        results, failures = collect_apify(handles, now.timestamp(), token)
    else:
        results, failures = collect_direct(handles, now.timestamp())

    if results:
        save(results, today)
    LAST_RUN_FILE.write_text(json.dumps({
        "ran_at": now.isoformat(timespec="seconds"),
        "method": method,
        "succeeded": sorted(results),
        "failed": failures,
    }, indent=2))

    print(f"Done: {len(results)} ok, {len(failures)} failed")
    # Only report the run as failed if nothing at all worked.
    sys.exit(0 if results else 1)


if __name__ == "__main__":
    main()
