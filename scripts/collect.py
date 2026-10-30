"""
Daily collector: looks up each athlete's Instagram account and saves today's
numbers to data/history.csv, plus their name, bio and photo.

You normally don't run this yourself - GitHub runs it every morning.

Where the numbers come from:
  1. Meta's official Business Discovery API - the main source. Free and
     permitted. Needs the META_ACCESS_TOKEN secret (see README). Works for
     Creator and Business accounts, which most pro athletes use.
  2. Apify - optional backup for athletes on personal accounts, which Meta's
     API can't see. Used only if an APIFY_TOKEN secret is set.

Safety checks:
  - The name on the Instagram account must match the athlete, so a wrong
    handle can't put someone else's numbers on the site. If you've checked
    a handle yourself, set handle_checked to "yes" in data/athletes.csv to
    skip this check.
  - Blank or failing handles are looked up on Wikidata (the free database
    behind Wikipedia) and filled in automatically for the next run.
"""

import csv
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
ATHLETES_FILE = ROOT / "data" / "athletes.csv"
HISTORY_FILE = ROOT / "data" / "history.csv"
LAST_RUN_FILE = ROOT / "data" / "last_run.json"
# Accounts Meta couldn't see (usually personal accounts), with the date last checked.
# They're re-checked weekly rather than daily, to save Meta's daily allowance.
NOT_VISIBLE_FILE = ROOT / "data" / "not_visible.json"
RECHECK_DAYS = 7
PROFILES_FILE = ROOT / "data" / "profiles.json"
IMG_DIR = ROOT / "site" / "img"
PHOTO_REFRESH_DAYS = 7

GRAPH = "https://graph.facebook.com/v25.0"
SECONDS_BETWEEN_LOOKUPS = 2   # stays well inside Meta's hourly limits

HISTORY_COLUMNS = [
    "date", "instagram", "followers", "following", "posts",
    "avg_likes", "avg_comments", "engagement_rate", "posts_last_30d", "source",
]
USER_AGENT = "AussieHoopsSocial/1.0 (github.com; daily follower rankings)"


class TokenProblem(Exception):
    """The Meta access token is missing, wrong or expired - nothing will work."""


class RateLimited(Exception):
    """Meta says we've made too many requests - stop for today."""


class NotVisible(Exception):
    """Meta can't see this account: wrong handle, or a personal account."""


# ---------------------------------------------------------------- helpers

def plain(text):
    """Lower-case, accents removed, letters only - for comparing names."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", text.lower())


def name_matches(athlete_name, ig_name, ig_username):
    """True if the athlete's surname appears in the name shown on the Instagram
    account. (The username alone isn't trusted: we often guessed it from the
    athlete's name, so it would always 'match'.) Only if the account shows no
    name at all do we fall back to the username."""
    words = [w for w in athlete_name.split() if plain(w) not in SUFFIXES]
    surname = plain(words[-1]) if words else ""
    shown = plain(ig_name) or plain(ig_username)
    return bool(surname) and surname in shown


SUFFIXES = {"jr", "jnr", "sr", "ii", "iii", "iv"}


def people_from(athletes):
    """One entry per athlete. Someone can have several rows (e.g. WNBA and WNBL);
    they share one Instagram account, taken from whichever row has it."""
    people = {}
    for a in athletes:
        p = people.setdefault(a["name"], {"name": a["name"], "instagram": "", "handle_checked": "no"})
        if not p["instagram"] and clean_handle(a["instagram"]):
            p["instagram"] = clean_handle(a["instagram"])
        if a.get("handle_checked") == "yes":
            p["handle_checked"] = "yes"
        elif a.get("handle_checked") == "wikidata" and p["handle_checked"] == "no":
            p["handle_checked"] = "wikidata"
    return people


def sync_rows(fields, athletes, people):
    """Copy each athlete's handle onto all of their rows, so the file stays consistent."""
    changed = False
    for a in athletes:
        p = people[a["name"]]
        if clean_handle(a["instagram"]) != p["instagram"] or a.get("handle_checked") != p["handle_checked"]:
            a["instagram"], a["handle_checked"] = p["instagram"], p["handle_checked"]
            changed = True
    if changed:
        save_athletes(fields, athletes)


def load_athletes():
    with ATHLETES_FILE.open(newline="") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def save_athletes(fields, rows):
    with ATHLETES_FILE.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def clean_handle(h):
    return (h or "").strip().lstrip("@").lower()


# ---------------------------------------------------------------- Meta API

def graph_get(path, token, **params):
    r = requests.get(f"{GRAPH}/{path}", params={**params, "access_token": token}, timeout=30)
    try:
        body = r.json()
    except ValueError:
        r.raise_for_status()
        raise
    err = body.get("error")
    if err:
        code, sub, msg = err.get("code"), err.get("error_subcode"), err.get("message", "")
        if code == 190 or code == 102:
            raise TokenProblem(f"Meta rejected the access token: {msg}")
        if code in (4, 17, 32, 613) or "limit" in msg.lower():
            raise RateLimited(msg)
        if code in (10, 200):
            raise TokenProblem(
                f"Meta says the app doesn't have permission ({msg}). If your Facebook Page is in a "
                "business portfolio, the token also needs ads_read - see README 'Connecting Meta'.")
        if code in (100, 110) or sub == 2207013:
            raise NotVisible(msg)
        raise RuntimeError(f"Meta error {code}: {msg}")
    return body


def find_ig_account(token):
    """Work out which Instagram account to make lookups from."""
    fixed = os.environ.get("META_IG_USER_ID", "").strip()
    if fixed:
        return fixed
    # A Page token: the page itself is "me".
    try:
        me = graph_get("me", token, fields="name,instagram_business_account")
        if me.get("instagram_business_account"):
            return me["instagram_business_account"]["id"]
        # It's a Page token, but Meta shows no Instagram account linked to the Page.
        raise TokenProblem(
            f"The token works and belongs to the Facebook Page '{me.get('name')}', but Meta can't see "
            "an Instagram professional account linked to that Page. Link them (README step 2), then make "
            "a new token with instagram_basic ticked (steps 4-5).")
    except NotVisible:
        pass
    # A user token: look through the pages this person manages.
    try:
        pages = graph_get("me/accounts", token, fields="name,instagram_business_account")
    except (NotVisible, RuntimeError) as e:
        raise TokenProblem(
            f"Couldn't find your Facebook Page with this token ({e}). Make sure the token is the "
            "Page's access_token from README step 5, or that it has the pages_show_list permission.")
    for page in pages.get("data", []):
        if page.get("instagram_business_account"):
            return page["instagram_business_account"]["id"]
    raise TokenProblem(
        "No Instagram professional account is linked to a Facebook Page this token can see. "
        "Check README step 'Link Instagram to a Facebook Page'.")


def token_expiry(token):
    """When the token stops working, or None if it never expires / can't tell."""
    try:
        data = graph_get("debug_token", token, input_token=token).get("data", {})
        exp = data.get("expires_at") or 0
        return datetime.fromtimestamp(exp, timezone.utc) if exp else None
    except Exception:  # noqa: BLE001 - this check is only a courtesy
        return None


RECENT_POSTS = 10  # latest posts fetched per athlete: the video strip on their profile, and engagement

# Whether Meta lets us fetch recent posts in the same lookup. None = not known yet.
# If asking for posts ever causes errors, the run carries on without them.
MEDIA_STATE = {"ok": None, "images": False}
# like_count is left out by Meta when an athlete hides likes on a post.
POST_FIELDS = "permalink,media_type,media_product_type,timestamp,caption,like_count,comments_count"
IMAGE_FIELDS = ",media_url,thumbnail_url"
HL_DIR = IMG_DIR / "hl"           # featured highlight thumbnails
HL_WIDTH = 440                    # small enough to keep the site (and repository) light


def check_media_allowed(token, ig_id):
    """One test lookup on your own Instagram account: can we fetch recent posts?
    If not, the run still collects follower numbers, just without posts."""
    try:
        me = graph_get(ig_id, token, fields="username")["username"]
    except (NotVisible, RuntimeError, KeyError) as e:
        MEDIA_STATE["ok"] = False
        print(f"  Recent posts not available this run ({e}); collecting follower numbers only.")
        return
    for images in (True, False):
        fields = POST_FIELDS + (IMAGE_FIELDS if images else "")
        try:
            graph_get(ig_id, token, fields=f"business_discovery.username({me}){{media.limit(1){{{fields}}}}}")
            MEDIA_STATE.update(ok=True, images=images)
            return
        except (NotVisible, RuntimeError) as e:
            last = e
    MEDIA_STATE["ok"] = False
    print(f"  Recent posts not available this run ({last}); collecting follower numbers only.")


def recent_media(items):
    """Keep only what the site needs: link, type and date of each recent post."""
    out = []
    for m in items or []:
        if not m.get("permalink"):
            continue
        kind = "reel" if (m.get("media_product_type") == "REELS" or m.get("media_type") == "VIDEO") \
            else "carousel" if m.get("media_type") == "CAROUSEL_ALBUM" else "photo"
        out.append({"url": m["permalink"], "type": kind, "date": (m.get("timestamp") or "")[:10],
                    "caption": (m.get("caption") or "").strip()[:160],
                    "likes": m.get("like_count"), "comments": m.get("comments_count"),
                    "_img": m.get("thumbnail_url") or (m.get("media_url") if kind != "reel" else None)})
    return out


def engagement(followers, recent, today=None):
    """Engagement from the latest posts (phase 2; not shown on the site yet).

    engagement_rate = average of (likes + comments) / followers x 100, over the
    recent posts that show their like count. Swap in your own formula here.
    """
    out = {"avg_likes": None, "avg_comments": None, "engagement_rate": None, "posts_last_30d": None}
    if not recent:
        return out
    liked = [m for m in recent if isinstance(m.get("likes"), int) and m["likes"] >= 0]
    commented = [m for m in recent if isinstance(m.get("comments"), int) and m["comments"] >= 0]
    if liked:
        out["avg_likes"] = round(sum(m["likes"] for m in liked) / len(liked))
    if commented:
        out["avg_comments"] = round(sum(m["comments"] for m in commented) / len(commented), 1)
    if liked and followers:
        out["engagement_rate"] = round(sum((m["likes"] + (m.get("comments") or 0)) / followers * 100
                                           for m in liked) / len(liked), 3)
    today = today or datetime.now(timezone.utc).date()
    dates = [m.get("date") for m in recent if m.get("date")]
    if dates:
        out["posts_last_30d"] = sum(1 for d in dates if (today - datetime.fromisoformat(d).date()).days <= 30)
    return out


def lookup_meta(handle, token, ig_id):
    fields = ("username,name,biography,followers_count,follows_count,"
              "media_count,profile_picture_url")
    media = f",media.limit({RECENT_POSTS}){{{POST_FIELDS}{IMAGE_FIELDS if MEDIA_STATE['images'] else ''}}}"
    body = graph_get(ig_id, token,
                     fields=f"business_discovery.username({handle}){{{fields}{media if MEDIA_STATE['ok'] else ''}}}")
    d = body["business_discovery"]
    recent = recent_media((d.get("media") or {}).get("data")) if MEDIA_STATE["ok"] else None
    return {
        "followers": d.get("followers_count"), "following": d.get("follows_count"),
        "posts": d.get("media_count"), "source": "meta",
        **engagement(d.get("followers_count"), recent),
        "_profile": {"username": d.get("username"), "full_name": d.get("name"),
                     "biography": d.get("biography"), "pic_url": d.get("profile_picture_url"),
                     "recent": recent},
    }


# ---------------------------------------------------------------- Apify backup

def lookup_apify(handles, token):
    r = requests.post(
        "https://api.apify.com/v2/acts/apify~instagram-profile-scraper/run-sync-get-dataset-items",
        json={"usernames": handles}, timeout=600,
        headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    out = {}
    for item in r.json():
        h = clean_handle(item.get("username"))
        if h and item.get("followersCount") is not None:
            recent = [{"url": p["url"],
                       "type": "reel" if p.get("type") == "Video" else
                               "carousel" if p.get("type") == "Sidecar" else "photo",
                       "date": (p.get("timestamp") or "")[:10],
                       "caption": (p.get("caption") or "").strip()[:160],
                       "likes": p.get("likesCount"), "comments": p.get("commentsCount"),
                       "_img": p.get("displayUrl")}
                      for p in (item.get("latestPosts") or [])[:RECENT_POSTS] if p.get("url")]
            out[h] = {
                "followers": item["followersCount"], "following": item.get("followsCount"),
                "posts": item.get("postsCount"), "source": "apify",
                **engagement(item["followersCount"], recent),
                "_profile": {"username": h, "full_name": item.get("fullName"),
                             "biography": item.get("biography"), "is_verified": item.get("verified"),
                             "pic_url": item.get("profilePicUrlHD") or item.get("profilePicUrl"),
                             "recent": recent},
            }
    return out


# ---------------------------------------------------------------- Wikidata

# Wikidata occupations searched for handles: basketball, soccer and American football players.
# To cover another sport, add its occupation here (e.g. "wd:Q12299841" for cricketers).
# Occupations: basketball, soccer, American football, tennis, Australian rules football,
# rugby league, cricket.
WIKIDATA_JOBS = ["wd:Q3665646", "wd:Q937857", "wd:Q19204627", "wd:Q10833314",
                 "wd:Q13414980", "wd:Q14089670", "wd:Q12299841"]


def wikidata_handles(names):
    """{athlete name: instagram handle} for athletes found on Wikidata."""
    if not names:
        return {}
    values = " ".join('"%s"@en' % n.replace('"', "") for n in names)
    query = f"""
      SELECT ?name ?ig WHERE {{
        VALUES ?name {{ {values} }}
        VALUES ?job {{ {" ".join(WIKIDATA_JOBS)} }}
        ?p rdfs:label ?name ; wdt:P106 ?job ; wdt:P2003 ?ig .
      }}"""
    try:
        r = requests.post("https://query.wikidata.org/sparql", data={"query": query},
                          headers={"Accept": "application/sparql-results+json",
                                   "User-Agent": USER_AGENT}, timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  Wikidata lookup skipped: {e}")
        return {}
    found = {}
    for b in r.json()["results"]["bindings"]:
        found.setdefault(b["name"]["value"], set()).add(clean_handle(b["ig"]["value"]))
    # Only use a name if Wikidata gives exactly one handle for it.
    return {n: hs.pop() for n, hs in found.items() if len(hs) == 1}


def fill_handles_from_wikidata(fields, athletes, people, names, changes):
    suggestions = wikidata_handles(names)
    for name in names:
        p, new = people[name], suggestions.get(name)
        if new and new != p["instagram"] and p["handle_checked"] != "yes":
            changes[name] = {"from": p["instagram"] or None, "to": new}
            p["instagram"], p["handle_checked"] = new, "wikidata"
    sync_rows(fields, athletes, people)
    return suggestions


# ---------------------------------------------------------------- saving

def save_history(results, today):
    rows = []
    if HISTORY_FILE.exists():
        with HISTORY_FILE.open(newline="") as f:
            rows = [r for r in csv.DictReader(f)
                    if not (r["date"] == today and r["instagram"] in results)]
    for handle, r in results.items():
        rows.append({"date": today, "instagram": handle, **r})
    rows.sort(key=lambda r: (r["date"], r["instagram"]))
    with HISTORY_FILE.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in HISTORY_COLUMNS})


def save_profiles(results, today):
    """Keep each player's name, bio and photo up to date.

    Instagram photo links expire and won't load on other websites, so photos
    are downloaded into site/img/ and served from your own site, refreshed weekly.
    """
    profiles = json.loads(PROFILES_FILE.read_text()) if PROFILES_FILE.exists() else {}
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    for handle, r in results.items():
        info = r.get("_profile") or {}
        p = profiles.setdefault(handle, {})
        for key in ("full_name", "biography", "is_verified"):
            if info.get(key) is not None:
                p[key] = info[key]
        if info.get("recent") is not None:
            recent = info["recent"]
            p["recent"] = [{k: v for k, v in m.items() if not k.startswith("_")} for m in recent]
            # Featured highlight: keep a small copy of its preview image on your site
            # (Instagram's image links expire). Only re-downloaded when it changes.
            feat = pick_featured(recent)
            old = p.get("featured") or {}
            if feat and feat.get("_img") and feat["url"] != old.get("url"):
                thumb = HL_DIR / f"{handle}.jpg"
                if save_thumbnail(feat["_img"], thumb):
                    p["featured"] = {k: v for k, v in feat.items() if not k.startswith("_")}
                    p["featured"]["thumb"] = f"img/hl/{handle}.jpg"
            elif not feat:
                p.pop("featured", None)
        img = IMG_DIR / f"{handle}.jpg"
        last = p.get("photo_updated", "")
        stale = not img.exists() or not last or \
            (datetime.fromisoformat(today) - datetime.fromisoformat(last)).days >= PHOTO_REFRESH_DAYS
        if info.get("pic_url") and stale:
            try:
                resp = requests.get(info["pic_url"], timeout=30, headers={"User-Agent": USER_AGENT})
                if resp.ok and resp.headers.get("Content-Type", "").startswith("image/"):
                    img.write_bytes(resp.content)
                    p["photo_updated"] = today
            except requests.RequestException as e:
                print(f"  photo for @{handle} not saved: {e}")
    PROFILES_FILE.write_text(json.dumps(profiles, indent=2, ensure_ascii=False))


def save_thumbnail(url, path):
    """Download a post's preview image and shrink it. Returns True if saved."""
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
        if not (resp.ok and resp.headers.get("Content-Type", "").startswith("image/")):
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            from io import BytesIO
            from PIL import Image
            im = Image.open(BytesIO(resp.content)).convert("RGB")
            if im.width > HL_WIDTH:
                im = im.resize((HL_WIDTH, round(im.height * HL_WIDTH / im.width)))
            im.save(path, "JPEG", quality=72, optimize=True, progressive=True)
        except ImportError:
            path.write_bytes(resp.content)
        return True
    except Exception as e:  # noqa: BLE001 - a missing thumbnail never stops the run
        print(f"  thumbnail not saved: {e}")
        return False


def pick_featured(recent):
    """Their latest reel, or their latest post if they have no reels."""
    reels = [m for m in recent if m["type"] == "reel"]
    return (reels or recent or [None])[0]


def write_report(report):
    LAST_RUN_FILE.write_text(json.dumps(report, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------- main

def main():
    now = datetime.now(timezone.utc)
    report = {"ran_at": now.isoformat(timespec="seconds"), "method": "meta",
              "succeeded": [], "failed": {}, "handle_changes": {}, "warnings": []}
    try:
        run(now, report)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 - always leave a report explaining what went wrong
        msg = f"The collector stopped unexpectedly: {type(e).__name__}: {e}"
        print(f"::error::{msg}")
        report["warnings"].append(msg)
        write_report(report)
        raise


def run(now, report):
    today = now.date().isoformat()
    fields, athletes = load_athletes()
    people = people_from(athletes)
    sync_rows(fields, athletes, people)

    # 1. Fill blank handles from Wikidata before looking anyone up.
    blanks = [n for n, p in people.items() if not p["instagram"]]
    if blanks:
        print(f"Looking up {len(blanks)} missing handles on Wikidata")
        fill_handles_from_wikidata(fields, athletes, people, blanks, report["handle_changes"])

    token = os.environ.get("META_ACCESS_TOKEN", "").strip()
    apify = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        msg = "META_ACCESS_TOKEN secret isn't set yet - see README 'Connecting Meta'."
        print(f"::error::{msg}")
        report["warnings"].append(msg)
        write_report(report)
        sys.exit(1)

    try:
        ig_id = find_ig_account(token)
        check_media_allowed(token, ig_id)
        expires = token_expiry(token)
        if expires:
            days = (expires - now).days
            report["token_expires"] = expires.date().isoformat()
            if days < 14:
                msg = f"Your Meta access token expires in {days} days. See README 'Renewing the token'."
                print(f"::warning::{msg}")
                report["warnings"].append(msg)
        else:
            report["token_expires"] = "never"
    except TokenProblem as e:
        print(f"::error::{e}")
        report["warnings"].append(str(e))
        write_report(report)
        sys.exit(1)

    # 2. Look everyone up through Meta.
    results, not_visible = {}, {}
    seen_hidden = json.loads(NOT_VISIBLE_FILE.read_text()) if NOT_VISIBLE_FILE.exists() else {}
    def recently_hidden(h):
        d = seen_hidden.get(h)
        return d and (now.date() - datetime.fromisoformat(d).date()).days < RECHECK_DAYS
    skipped = {p["instagram"]: p for p in people.values() if p["instagram"] and recently_hidden(p["instagram"])}
    not_visible.update(skipped)          # still offered to Apify, if it's set up
    done_today = set()
    if HISTORY_FILE.exists():
        with HISTORY_FILE.open(newline="") as f:
            done_today = {r["instagram"] for r in csv.DictReader(f) if r["date"] == today}
    todo = [p for p in people.values() if p["instagram"] and p["instagram"] not in skipped
            and clean_handle(p["instagram"]) not in done_today]
    if done_today:
        print(f"{len(done_today)} accounts already collected today; collecting the rest")
    if skipped:
        print(f"Skipping {len(skipped)} accounts Meta couldn't see in the last {RECHECK_DAYS} days")
    # Start at a different point each day, so if Meta's limit cuts a run short,
    # it isn't always the same athletes who miss out.
    if todo:
        start = now.toordinal() % len(todo)
        todo = todo[start:] + todo[:start]
    for p in people.values():
        if not p["instagram"]:
            report["failed"][p["name"]] = "no Instagram handle yet - add it to data/athletes.csv"
    print(f"Collecting {len(todo)} profiles via Meta for {today}")
    for i, a in enumerate(todo):
        handle = clean_handle(a["instagram"])
        if i:
            time.sleep(SECONDS_BETWEEN_LOOKUPS)
        try:
            results[handle] = lookup_meta(handle, token, ig_id)
            seen_hidden.pop(handle, None)
            print(f"  ok   @{handle}: {results[handle]['followers']:,} followers")
        except NotVisible as e:
            not_visible[handle] = a
            seen_hidden[handle] = today
            print(f"  --   @{handle}: not visible to Meta ({e})")
        except RateLimited as e:
            print(f"::warning::Meta rate limit reached at @{handle}; the rest wait until tomorrow.")
            for b in todo[i:]:
                report["failed"][b["name"]] = "skipped - Meta rate limit reached"
            break
        except TokenProblem as e:
            print(f"::error::{e}")
            report["warnings"].append(str(e))
            for b in todo[i:]:
                report["failed"][b["name"]] = "skipped - access token problem"
            break
        except Exception as e:  # noqa: BLE001 - keep going for other players
            report["failed"][a["name"]] = str(e)
            print(f"  fail @{handle}: {e}")

    # 3. Personal accounts: try Apify if it's set up.
    if not_visible and apify:
        print(f"Trying {len(not_visible)} accounts through Apify")
        try:
            results.update(lookup_apify(list(not_visible), apify))
        except requests.RequestException as e:
            report["warnings"].append(f"Apify backup failed: {e}")
    for handle, a in not_visible.items():
        if handle not in results:
            report["failed"][a["name"]] = (
                f"@{handle} not found, or it's a personal account Meta can't see "
                f"(checked {seen_hidden.get(handle, today)}; re-checked weekly). "
                "Check the handle" + ("." if apify else ", or add an APIFY_TOKEN secret for personal accounts."))
    # Forget handles that are no longer in the list.
    current = {p["instagram"] for p in people.values() if p["instagram"]}
    NOT_VISIBLE_FILE.write_text(json.dumps({h: d for h, d in sorted(seen_hidden.items()) if h in current}, indent=1))

    # 4. Name check: never publish someone else's numbers.
    by_handle = {p["instagram"]: p for p in people.values() if p["instagram"]}
    for handle in list(results):
        a = by_handle[handle]
        prof = results[handle].get("_profile") or {}
        if a.get("handle_checked") != "yes" and not name_matches(a["name"], prof.get("full_name"), handle):
            report["failed"][a["name"]] = (
                f"@{handle} belongs to '{prof.get('full_name') or 'someone else'}', not {a['name']}. "
                "Fix the handle, or set handle_checked to yes if it really is them.")
            del results[handle]

    # 5. For handles that failed, see if Wikidata knows a better one for next time.
    retry = [n for n, why in report["failed"].items() if why.startswith("@")]
    if retry:
        fill_handles_from_wikidata(fields, athletes, people, retry, report["handle_changes"])

    if results:
        save_history(results, today)
        save_profiles(results, today)
    report["succeeded"] = sorted(by_handle[h]["name"] for h in results)
    write_report(report)
    print(f"Done: {len(results)} ok, {len(report['failed'])} not collected, "
          f"{len(report['handle_changes'])} handles updated from Wikidata")
    sys.exit(0 if results else 1)


if __name__ == "__main__":
    main()
