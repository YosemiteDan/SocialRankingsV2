"""
Turns the collected numbers into the files the website reads:

  site/data.json              - the rankings: one summary per player
  site/players/<handle>.json  - one file per player for their profile page,
                                with their full history

If there is no real history yet, it uses data/sample_history.csv so the
site has something to show, and the site displays a 'sample data' banner.
"""

import csv
import json
import re
import unicodedata
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SITE = ROOT / "site"

PERIODS = {"7d": 7, "30d": 30, "365d": 365}

# Men's or women's competition. An optional "gender" column in athletes.csv
# (men / women) overrides this for any athlete.
LEAGUE_GENDER = {"NBA": "men", "NBL": "men", "Socceroos": "men", "NFL": "men",
                 "ATP": "men", "AFL": "men", "NRL": "men", "Australia men": "men",
                 "WNBA": "women", "WNBL": "women", "Matildas": "women",
                 "WTA": "women", "AFLW": "women", "NRLW": "women", "Australia women": "women"}
SPARK_DAYS = 30


def slug(name):
    """'Ezi Magbegor' -> 'ezi-magbegor': each player's page address on the site."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def num(v, cast=int):
    return cast(v) if v not in (None, "") else None


def load_history():
    real = DATA / "history.csv"
    path, sample = (real, False) if real.exists() else (DATA / "sample_history.csv", True)
    by_handle = {}
    if path.exists():
        with path.open(newline="") as f:
            for r in csv.DictReader(f):
                by_handle.setdefault(r["instagram"].lower(), []).append(r)
    for rows in by_handle.values():
        rows.sort(key=lambda r: r["date"])
    return by_handle, sample


def value_on_or_before(rows, day):
    """Followers on the latest date that is on or before `day`."""
    best = None
    for r in rows:
        if r["date"] <= day:
            best = r
        else:
            break
    return num(best["followers"]) if best else None


def main():
    with (DATA / "athletes.csv").open(newline="") as f:
        athletes = list(csv.DictReader(f))
    history, sample = load_history()
    profiles = {}
    if (DATA / "profiles.json").exists() and not sample:
        profiles = json.loads((DATA / "profiles.json").read_text())

    details = {}
    if (DATA / "details.json").exists():
        details = json.loads((DATA / "details.json").read_text())

    # One entry per athlete, even if they play in two leagues (e.g. WNBA and WNBL).
    grouped = {}
    for a in athletes:
        grouped.setdefault(a["name"], []).append(a)

    summaries, pages = [], {}
    for name, memberships in grouped.items():
        a = memberships[0]                      # first row = main league and team
        handle = next((m["instagram"].strip().lstrip("@").lower()
                       for m in memberships if m["instagram"].strip()), "")
        key = handle or (a["name"].replace(" ", "").lower() if sample else "")
        rows = history.get(key, []) if key else []
        prof = profiles.get(handle, {})
        photo = SITE / "img" / f"{handle}.jpg"
        p = {
            "name": a["name"], "team": a["team"], "position": a["position"],
            "sport": a.get("sport", ""), "league": a.get("league", ""),
            # Every league and team they're in: [league, team, position]
            "teams": [[m.get("league", ""), m["team"], m["position"]] for m in memberships],
            "leagues": [m.get("league", "") for m in memberships],
            "import": any(m.get("import") == "yes" for m in memberships),
            "gender": (a.get("gender") or "").strip().lower() or LEAGUE_GENDER.get(a.get("league", "")),
            "id": slug(a["name"]),
            "instagram": handle or None,
            "verified": prof.get("is_verified"),
            "photo": f"img/{handle}.jpg" if handle and photo.exists() else None,
            "followers": None, "spark": [],
        }
        if rows:
            latest = rows[-1]
            last_day = date.fromisoformat(latest["date"])
            followers = num(latest["followers"])
            p.update({
                "as_of": latest["date"],
                "followers": followers,
                "following": num(latest["following"]),
                "posts": num(latest["posts"]),
                "avg_likes": num(latest["avg_likes"]),
                "avg_comments": num(latest["avg_comments"]),
                "engagement_rate": num(latest["engagement_rate"], float),
                "posts_last_30d": num(latest["posts_last_30d"]),
            })
            for key, days in PERIODS.items():
                then = value_on_or_before(rows, (last_day - timedelta(days=days)).isoformat())
                if then and followers is not None:
                    p[f"change_{key}"] = followers - then
                    p[f"change_{key}_pct"] = round(100 * (followers - then) / then, 2)
                else:
                    p[f"change_{key}"] = p[f"change_{key}_pct"] = None
            # "This week" growth for the home page. Until there's a week of history,
            # use growth since tracking began, and say how many days that covers.
            week_ago = (last_day - timedelta(days=7)).isoformat()
            base = None
            for r in rows:
                if r["date"] <= week_ago:
                    base = r
            base = base or rows[0]
            base_followers = num(base["followers"])
            win_days = (last_day - date.fromisoformat(base["date"])).days
            if win_days > 0 and base_followers and followers is not None:
                p["growth"] = followers - base_followers
                p["growth_pct"] = round(100 * (followers - base_followers) / base_followers, 2)
                p["growth_days"] = win_days
            cutoff = (last_day - timedelta(days=SPARK_DAYS)).isoformat()
            p["spark"] = [num(r["followers"]) for r in rows if r["date"] >= cutoff]
        summaries.append(p)
        pages[p["id"]] = {
            **{k: v for k, v in p.items() if k != "spark"},
            "full_name": prof.get("full_name"),
            "bio": prof.get("biography"),
            "recent": prof.get("recent") or [],     # latest Instagram posts (links only)
            # Their latest reel, with a preview image saved on the site
            "featured": prof.get("featured") if prof.get("featured") and
                        (SITE / prof["featured"].get("thumb", "-")).exists() else None,
            "details": details.get(a["name"]),
            # [date, followers, posts, engagement rate]
            "history": [[r["date"], num(r["followers"]), num(r["posts"]),
                         num(r["engagement_rate"], float)] for r in rows],
        }

    summaries.sort(key=lambda p: -(p["followers"] or -1))
    for i, p in enumerate(summaries, 1):
        p["rank"] = i if p["followers"] is not None else None
        pages[p["id"]]["rank"] = p["rank"]

    last_run = {}
    if (DATA / "last_run.json").exists() and not sample:
        last_run = json.loads((DATA / "last_run.json").read_text())

    (SITE / "data.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sample_data": sample,
        "last_run": last_run,
        "players": summaries,
    }, indent=1, ensure_ascii=False))

    players_dir = SITE / "players"
    shutil.rmtree(players_dir, ignore_errors=True)
    players_dir.mkdir()
    for pid, d in pages.items():
        (players_dir / f"{pid}.json").write_text(
            json.dumps({**d, "sample_data": sample}, separators=(",", ":"), ensure_ascii=False))

    print(f"Wrote site/data.json and {len(pages)} player files (sample={sample})")


if __name__ == "__main__":
    main()
