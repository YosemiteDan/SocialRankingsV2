"""
Daily: YouTube numbers for athletes and official accounts that have a channel.

1. Finds channels (weekly) from Wikidata's "YouTube channel ID" (P2397): athletes are
   matched by name (and our sport / Instagram handle), official accounts and creators
   by their Instagram handle. Results go in data/youtube_channels.csv, where channels
   can also be added or corrected by hand (source = manual is never overwritten).
2. Collects each channel's subscribers, views and video count, and its latest videos
   with views, likes and comments, through the official YouTube Data API v3.

Needs the YOUTUBE_API_KEY secret. Uses well under 100 of the 10,000 free daily units.
YouTube API Services: https://www.youtube.com/t/terms
"""

import csv
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CHANNELS = DATA / "youtube_channels.csv"
HISTORY = DATA / "youtube_history.csv"
PROFILES = DATA / "youtube_profiles.json"
CHECKED = DATA / "youtube_checked.json"
API = "https://www.googleapis.com/youtube/v3"
WIKIDATA = "https://query.wikidata.org/sparql"
UA = "SocialeteRankings/1.0 (github.com; youtube channel lookup)"
LATEST = 6
REFRESH_DAYS = 7
SPORT_WORDS = {"Basketball": ["basketball"], "Soccer": ["association football", "soccer"],
               "American football": ["american football"], "Tennis": ["tennis"],
               "AFL": ["australian rules"], "Rugby league": ["rugby league"], "Cricket": ["cricket"]}
HIST_COLS = ["date", "channel_id", "subscribers", "views", "videos", "hidden"]


def read_csv(path):
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def sparql(q):
    for attempt in range(3):
        try:
            r = requests.post(WIKIDATA, data={"query": q}, timeout=90,
                              headers={"Accept": "application/sparql-results+json", "User-Agent": UA})
            if r.status_code == 429:
                time.sleep(30); continue
            r.raise_for_status()
            return r.json()["results"]["bindings"]
        except requests.RequestException as e:
            print(f"  Wikidata query failed ({e}); retrying")
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("Wikidata didn't answer")


import re
import unicodedata


def plain(t):
    return re.sub(r"[^a-z ]", "", unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower())


def handle_lookup(rows, people, key):
    """Athletes often use the same handle on YouTube as on Instagram: try @<instagram handle>
    and keep the channel only if its name includes the athlete's surname."""
    tried = 0
    for name, p in people.items():
        k = "athlete:" + name
        if rows.get(k) or not p["ig"]:
            continue
        tried += 1
        try:
            items = yt("channels", key, part="snippet,statistics", forHandle="@" + p["ig"]).get("items", [])
        except Exception:  # noqa: BLE001
            continue
        if not items:
            continue
        c = items[0]
        title, surname = plain(c["snippet"].get("title")), plain(name).split()[-1]
        if surname and surname in title.replace(" ", "") and int(c.get("statistics", {}).get("videoCount", 0)) > 0:
            rows[k] = {"key": k, "name": name, "channel_id": c["id"], "source": "handle"}
            print(f"  YouTube @{p['ig']} -> {c['snippet'].get('title')}")
    return tried


GENERIC = {"the", "fc", "football", "club", "afc", "and", "official", "australia", "australian", "women", "womens", "aflw", "nrlw", "league"}


def account_handle_lookup(rows, key):
    """Clubs, leagues and federations: try their Instagram handle on YouTube, and keep the
    channel only if its name shares a distinctive word with the account's name."""
    accounts = read_csv(DATA / "organisations.csv") + read_csv(DATA / "creators.csv")
    tried = 0
    for o in accounts:
        handles = [h.strip().lstrip("@").lower() for h in o.get("instagram", "").split("|") if h.strip()]
        if not handles or any(rows.get("account:" + h) for h in handles):
            continue
        words = [w for w in plain(o["name"]).split() if len(w) >= 3 and w not in GENERIC] or plain(o["name"]).split()
        for h in handles:
            tried += 1
            try:
                items = yt("channels", key, part="snippet,statistics", forHandle="@" + h).get("items", [])
            except Exception:  # noqa: BLE001
                continue
            if not items:
                continue
            c = items[0]
            title = plain(c["snippet"].get("title")).replace(" ", "")
            if any(w in title for w in words) and int(c.get("statistics", {}).get("videoCount", 0)) > 0:
                rows["account:" + handles[0]] = {"key": "account:" + handles[0], "name": o["name"], "channel_id": c["id"], "source": "handle"}
                print(f"  YouTube @{h} -> {c['snippet'].get('title')}")
                break
    return tried


def find_channels(today, key=""):
    """Weekly: fill in channel IDs from Wikidata, then by trying athletes' Instagram handles on
    YouTube. Rows with source "manual" (a channel added by hand) or "none" (no channel, or a
    wrong match to ignore) are never changed."""
    checked = json.loads(CHECKED.read_text()) if CHECKED.exists() else {}
    if checked.get("last") and (datetime.fromisoformat(today) - datetime.fromisoformat(checked["last"])).days < REFRESH_DAYS:
        return
    rows = {r["key"]: r for r in read_csv(CHANNELS)}
    athletes = read_csv(DATA / "athletes.csv")
    people = {}
    for a in athletes:
        p = people.setdefault(a["name"], {"sport": a.get("sport", ""), "ig": ""})
        if a.get("instagram", "").strip():
            p["ig"] = a["instagram"].strip().lstrip("@").lower()
    names = [n for n in people if rows.get("athlete:" + n, {}).get("source") not in ("manual", "none")]
    found = 0
    for i in range(0, len(names), 60):
        batch = names[i:i + 60]
        values = " ".join(json.dumps(n) + "@en" for n in batch)
        res = sparql(f"""SELECT ?name ?p ?yt ?ig (GROUP_CONCAT(DISTINCT ?sl; separator="|") AS ?sports) WHERE {{
          VALUES ?name {{ {values} }} ?p rdfs:label ?name ; wdt:P31 wd:Q5 ; wdt:P2397 ?yt .
          OPTIONAL {{ ?p wdt:P2003 ?ig }}
          OPTIONAL {{ ?p wdt:P641 ?s . ?s rdfs:label ?sl . FILTER(LANG(?sl) = "en") }}
        }} GROUP BY ?name ?p ?yt ?ig""")
        by = {}
        for b in res:
            by.setdefault(b["name"]["value"], []).append(b)
        for name, cands in by.items():
            me = people[name]
            def score(b):
                ig = b.get("ig", {}).get("value", "").lower().lstrip("@")
                sports = b.get("sports", {}).get("value", "").lower()
                return (2 if me["ig"] and ig == me["ig"] else 0) + (1 if any(w in sports for w in SPORT_WORDS.get(me["sport"], [])) else 0)
            best = max(cands, key=score)
            if score(best) == 0:
                continue        # can't confirm it's our athlete (same name, different person)
            rows["athlete:" + name] = {"key": "athlete:" + name, "name": name, "channel_id": best["yt"]["value"], "source": "wikidata"}
            found += 1
        time.sleep(2)
    # Official accounts and creators: matched by their Instagram handle on Wikidata.
    accounts = read_csv(DATA / "organisations.csv") + read_csv(DATA / "creators.csv")
    handles = {}
    for o in accounts:
        for h in o.get("instagram", "").split("|"):
            h = h.strip().lstrip("@").lower()
            if h and rows.get("account:" + h, {}).get("source") not in ("manual", "none"):
                handles[h] = o["name"]
    hl = list(handles)
    for i in range(0, len(hl), 80):
        values = " ".join(json.dumps(h) for h in hl[i:i + 80])
        for b in sparql(f"""SELECT ?ig ?yt WHERE {{ VALUES ?ig {{ {values} }} ?p wdt:P2003 ?ig ; wdt:P2397 ?yt . }}"""):
            h = b["ig"]["value"].lower()
            rows["account:" + h] = {"key": "account:" + h, "name": handles.get(h, h), "channel_id": b["yt"]["value"], "source": "wikidata"}
            found += 1
        time.sleep(2)
    if key:
        print(f"Tried {handle_lookup(rows, people, key)} athletes' and {account_handle_lookup(rows, key)} accounts' Instagram handles on YouTube")
    with CHANNELS.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["key", "name", "channel_id", "source"], lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(rows.values(), key=lambda r: r["key"]))
    CHECKED.write_text(json.dumps({"last": today}))
    print(f"Wikidata: {found} channels found ({len(rows)} listed)")


def yt(path, key, **params):
    r = requests.get(f"{API}/{path}", params={**params, "key": key}, timeout=30)
    if r.status_code == 403:
        raise RuntimeError(f"YouTube refused the request: {r.json().get('error', {}).get('message', r.text[:200])}")
    r.raise_for_status()
    return r.json()


def main():
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("Australia/Sydney")).date().isoformat()
    try:
        find_channels(today, key)
    except Exception as e:  # noqa: BLE001 - lookups can wait a week
        print(f"::warning::Couldn't look up YouTube channels on Wikidata ({e})")
    if not key:
        print("YOUTUBE_API_KEY isn't set; skipping YouTube.")
        return
    # Channels added by hand as an @handle are turned into channel IDs here.
    ch_rows = read_csv(CHANNELS)
    changed = False
    for r in ch_rows:
        if r.get("channel_id", "").startswith("@"):
            items = yt("channels", key, part="id", forHandle=r["channel_id"]).get("items", [])
            if items:
                print(f"  {r['channel_id']} -> {items[0]['id']}")
                r["channel_id"], changed = items[0]["id"], True
    if changed:
        with CHANNELS.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["key", "name", "channel_id", "source"], lineterminator="\n")
            w.writeheader()
            w.writerows(ch_rows)
    channels = sorted({r["channel_id"] for r in ch_rows if r.get("channel_id") and not r["channel_id"].startswith("@") and r.get("source") != "none"})
    history = read_csv(HISTORY)
    if any(r["date"] == today for r in history):
        print("YouTube numbers already collected today.")
        return
    profiles = json.loads(PROFILES.read_text()) if PROFILES.exists() else {}
    new_rows = []
    for i in range(0, len(channels), 50):
        res = yt("channels", key, part="snippet,statistics,contentDetails", id=",".join(channels[i:i + 50]), maxResults=50)
        for c in res.get("items", []):
            st, sn = c.get("statistics", {}), c.get("snippet", {})
            hidden = st.get("hiddenSubscriberCount", False)
            new_rows.append({"date": today, "channel_id": c["id"], "subscribers": "" if hidden else st.get("subscriberCount", ""),
                             "views": st.get("viewCount", ""), "videos": st.get("videoCount", ""), "hidden": "yes" if hidden else ""})
            profiles[c["id"]] = {**profiles.get(c["id"], {}), "title": sn.get("title"), "handle": sn.get("customUrl"),
                                 "thumb": ((sn.get("thumbnails") or {}).get("default") or {}).get("url"),
                                 "uploads": (c.get("contentDetails") or {}).get("relatedPlaylists", {}).get("uploads")}
    # Latest videos (one call per channel for the list, one call per 50 videos for their stats).
    vids = {}
    for cid in channels:
        up = (profiles.get(cid) or {}).get("uploads")
        if not up:
            continue
        try:
            items = yt("playlistItems", key, part="contentDetails", playlistId=up, maxResults=LATEST).get("items", [])
        except requests.HTTPError:
            continue
        vids[cid] = [it["contentDetails"]["videoId"] for it in items]
    all_ids = [v for ids in vids.values() for v in ids]
    stats = {}
    for i in range(0, len(all_ids), 50):
        for v in yt("videos", key, part="snippet,statistics,contentDetails", id=",".join(all_ids[i:i + 50])).get("items", []):
            s, sn = v.get("statistics", {}), v.get("snippet", {})
            stats[v["id"]] = {"id": v["id"], "title": sn.get("title", "")[:120], "date": (sn.get("publishedAt") or "")[:10],
                              "views": int(s["viewCount"]) if "viewCount" in s else None,
                              "likes": int(s["likeCount"]) if "likeCount" in s else None,
                              "comments": int(s["commentCount"]) if "commentCount" in s else None,
                              "short": (v.get("contentDetails") or {}).get("duration", "").startswith("PT") and "M" not in v["contentDetails"]["duration"] and "H" not in v["contentDetails"]["duration"]}
    for cid, ids in vids.items():
        profiles[cid]["recent"] = [stats[v] for v in ids if v in stats]
        profiles[cid]["updated"] = today
    history += new_rows
    with HISTORY.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HIST_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(history, key=lambda r: (r["date"], r["channel_id"])))
    PROFILES.write_text(json.dumps(profiles, indent=1, ensure_ascii=False))
    print(f"YouTube: {len(new_rows)} channels collected, {len(stats)} videos")


if __name__ == "__main__":
    main()
