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
                 "Boomers": "men", "Wallabies": "men", "Kangaroos": "men",
                 "Opals": "women", "Wallaroos": "women", "Jillaroos": "women",
                 "ATP": "men", "AFL": "men", "NRL": "men", "Australia men": "men",
                 "WNBA": "women", "WNBL": "women", "Matildas": "women",
                 "WTA": "women", "AFLW": "women", "NRLW": "women", "Australia women": "women"}
SPARK_DAYS = 30


def slug(name):
    """'Ezi Magbegor' -> 'ezi-magbegor': each player's page address on the site."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def num(v, cast=int):
    """A number from the history file ("8.0" and "8" both read fine); blank -> None."""
    if v in (None, ""):
        return None
    try:
        return cast(v)
    except ValueError:
        x = float(v)
        return round(x) if cast is int else cast(x)


# National teams count only for players in that team's most recent official
# tournament squad, listed in data/national_squads.csv (team, competition, date,
# name, club). Teams with no squad listed there fall back to data/athletes.csv.
NATIONAL_TEAMS = {"Matildas", "Socceroos", "Australia men", "Australia women", "Boomers", "Opals",
                  "Wallabies", "Wallaroos", "Kangaroos", "Jillaroos"}


def plain_name(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", "", s).strip()


def load_squads():
    """{team: {"competition": ..., "date": ..., "names": set of plain names}} for each team's latest squad."""
    path = DATA / "national_squads.csv"
    if not path.exists():
        return {}
    latest = {}
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        t = r["team"].strip()
        if r["date"] > latest.get(t, ("", ""))[0]:
            latest[t] = (r["date"], r["competition"].strip())
    squads = {t: {"date": d, "competition": c, "names": set()} for t, (d, c) in latest.items()}
    for r in rows:
        t = r["team"].strip()
        if r["date"] == squads[t]["date"]:
            squads[t]["names"].add(plain_name(r["name"]))
    return squads


def in_squad(name, names):
    """Exact name, or same surname with a shortened first name (Steph / Stephanie)."""
    n = plain_name(name)
    if n in names:
        return True
    if not n:
        return False
    first, last = n.split()[0], n.split()[-1]
    return any(k.split()[-1] == last and (k.split()[0].startswith(first) or first.startswith(k.split()[0]))
               for k in names if k)


def memberships_for(name, memberships, squads):
    """Leagues and teams, with national teams only while the player is in the latest squad."""
    teams, leagues, national = [], [], {}
    for m in memberships:
        lg = m.get("league", "")
        if lg in NATIONAL_TEAMS and lg in squads and not in_squad(name, squads[lg]["names"]):
            teams.append(["", m["team"], m["position"]])     # keep their club; no national badge
            continue
        teams.append([lg, m["team"], m["position"]])
        leagues.append(lg)
        if lg in squads:
            national[lg] = squads[lg]["competition"]
    for team, sq in squads.items():
        if team not in leagues and in_squad(name, sq["names"]):
            teams.append([team, "", ""])
            leagues.append(team)
            national[team] = sq["competition"]
    return {"teams": teams, "leagues": leagues, "national": national}


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


def growth_fields(rows):
    """Followers, changes (1 day, 7/30/365 days, this week) and engagement from someone's history rows."""
    latest = rows[-1]
    last_day = date.fromisoformat(latest["date"])
    followers = num(latest["followers"])
    out = {"as_of": latest["date"], "followers": followers, "posts": num(latest["posts"]),
           "engagement_rate": num(latest["engagement_rate"], float), "posts_last_30d": num(latest["posts_last_30d"]),
           "avg_likes": num(latest["avg_likes"]), "avg_comments": num(latest["avg_comments"], float),
           "tracked_since": rows[0]["date"]}
    for key, days in PERIODS.items():
        then = value_on_or_before(rows, (last_day - timedelta(days=days)).isoformat())
        ok = then and followers is not None
        out[f"change_{key}"] = followers - then if ok else None
        out[f"change_{key}_pct"] = round(100 * (followers - then) / then, 2) if ok else None
    prev = rows[-2] if len(rows) > 1 else None
    if prev:
        gap, prev_f = (last_day - date.fromisoformat(prev["date"])).days, num(prev["followers"])
        if prev_f and followers is not None and 0 < gap <= 3:
            out["change_1d"] = followers - prev_f
            out["change_1d_pct"] = round(100 * (followers - prev_f) / prev_f, 3)
    week_ago = (last_day - timedelta(days=7)).isoformat()
    base = next((r for r in reversed(rows) if r["date"] <= week_ago), rows[0])
    win_days, base_f = (last_day - date.fromisoformat(base["date"])).days, num(base["followers"])
    if win_days > 0 and base_f and followers is not None:
        out.update(growth=followers - base_f, growth_pct=round(100 * (followers - base_f) / base_f, 2), growth_days=win_days)
    return out


REGIONS = {"Australia": "Australia", "New Zealand": "Australia", "USA": "North America", "Canada": "North America",
           "England": "Europe", "UK": "Europe", "International": "Global"}


def post_stats(recent, as_of):
    """Posts in the last 7 / 30 days and the content mix (count and average likes + comments per type)."""
    out = {"posts_7d": None, "posts_30d": None, "mix": {}}
    if not recent or not as_of:
        return out
    t = date.fromisoformat(as_of)
    ages = [(t - date.fromisoformat(m["date"])).days for m in recent if m.get("date")]
    out["posts_7d"] = sum(1 for a in ages if 0 <= a <= 7)
    out["posts_30d"] = sum(1 for a in ages if 0 <= a <= 30)
    for m in recent:
        if isinstance(m.get("likes"), int):
            c = out["mix"].setdefault(m.get("type", "photo"), [0, 0])
            c[0] += 1
            c[1] += m["likes"] + (m.get("comments") or 0)
    out["mix"] = {k: [n, round(tot / n)] for k, (n, tot) in out["mix"].items()}
    return out


YOUTUBE = {}


def build_orgs(history, profiles, filename="organisations.csv"):
    """Official accounts (data/organisations.csv), or creators (data/creators.csv), for the Sports pages."""
    path = DATA / filename
    if not path.exists():
        return []
    out = []
    with path.open(newline="") as f:
        for o in csv.DictReader(f):
            options = [h.strip().lstrip("@").lower() for h in o["instagram"].split("|") if h.strip()]
            if not options:
                continue
            handle = next((h for h in options if history.get(h)), options[0])   # whichever handle was collected
            rows, prof = history.get(handle, []), profiles.get(handle, {})
            item = {"id": slug(o["name"]), "name": o["name"], "instagram": handle, "type": o["type"],
                    "sport": o["sport"], "league": o.get("league") or "", "country": o.get("country") or "Australia",
                    "gender": o.get("gender") or "mixed", "verified": prof.get("is_verified"), "kind": o.get("kind") or "", "parent": o.get("parent") or "", "live": (o.get("live") or "").lower() == "yes",
                    "photo": f"img/{handle}.jpg" if (SITE / "img" / f"{handle}.jpg").exists() else None,
                    "region": REGIONS.get(o.get("country") or "Australia", "Global"), "followers": None}
            if rows:
                item.update(growth_fields(rows))
                item.update(post_stats(prof.get("recent"), item["as_of"]))
                # The account's own page: recent posts and follower history.
                acc = SITE / "accounts"
                acc.mkdir(exist_ok=True)
                (acc / f"{handle}.json").write_text(json.dumps({
                    "recent": prof.get("recent") or [],
                    "history": [[r["date"], num(r["followers"])] for r in rows]}, separators=(",", ":"), ensure_ascii=False))
            keys = ("url", "type", "date", "caption", "likes", "comments", "thumb")
            bp = prof.get("best_post")
            if bp:
                item["best_post"] = {k: bp.get(k) for k in keys}
            item["top_posts"] = [{k: m.get(k) for k in keys} for m in (prof.get("top_posts") or ([bp] if bp else []))]
            y = next((YOUTUBE.get("account:" + h) for h in options if YOUTUBE.get("account:" + h)), None)
            if y:
                item["youtube"] = {k: v for k, v in y.items() if k != "recent"}
                if rows:
                    (SITE / "accounts" / f"{handle}.json").write_text(json.dumps({**json.loads((SITE / "accounts" / f"{handle}.json").read_text()), "youtube": y}, separators=(",", ":"), ensure_ascii=False))
            out.append(item)
    return out


def load_events():
    """Tournament feature widgets (data/events.csv): shown on the Sports pages while they're on."""
    path = DATA / "events.csv"
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f) if (r.get("name") or "").strip()]


def load_youtube():
    """{key: summary} for athletes ("athlete:<name>") and accounts ("account:<handle>") with a YouTube channel."""
    ch_path, hist_path, prof_path = DATA / "youtube_channels.csv", DATA / "youtube_history.csv", DATA / "youtube_profiles.json"
    if not ch_path.exists() or not hist_path.exists():
        return {}
    with hist_path.open(newline="") as f:
        hist = {}
        for r in csv.DictReader(f):
            hist.setdefault(r["channel_id"], []).append(r)
    profiles = json.loads(prof_path.read_text()) if prof_path.exists() else {}
    out = {}
    with ch_path.open(newline="") as f:
        for c in csv.DictReader(f):
            rows = sorted(hist.get(c["channel_id"], []), key=lambda r: r["date"])
            if not rows:
                continue
            last, prof = rows[-1], profiles.get(c["channel_id"], {})
            yt = {"channel_id": c["channel_id"], "handle": prof.get("handle"), "title": prof.get("title"),
                  "subscribers": num(last["subscribers"]), "views": num(last["views"]), "videos": num(last["videos"]),
                  "hidden": last.get("hidden") == "yes", "as_of": last["date"], "recent": prof.get("recent") or []}
            week_ago = (date.fromisoformat(last["date"]) - timedelta(days=7)).isoformat()
            base = next((r for r in reversed(rows) if r["date"] <= week_ago), rows[0])
            days = (date.fromisoformat(last["date"]) - date.fromisoformat(base["date"])).days
            if days > 0:
                bv, bs = num(base["views"]), num(base["subscribers"])
                if bv is not None and yt["views"] is not None:
                    yt["views_gain"], yt["gain_days"] = yt["views"] - bv, days
                if bs and yt["subscribers"] is not None:
                    yt["subs_gain"] = yt["subscribers"] - bs
                    yt["subs_gain_pct"] = round(100 * (yt["subscribers"] - bs) / bs, 2)
            out[c["key"]] = yt
    return out


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

    squads = load_squads()
    youtube = load_youtube()
    YOUTUBE.update(youtube)
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
            **memberships_for(a["name"], memberships, squads),
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
                "avg_comments": num(latest["avg_comments"], float),
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
            # "Today": change since the previous collection (within 3 days), for the
            # home page's top profile of the day.
            prev = rows[-2] if len(rows) > 1 else None
            gap = (last_day - date.fromisoformat(prev["date"])).days if prev else 0
            prev_f = num(prev["followers"]) if prev else None
            if prev_f and followers is not None and 0 < gap <= 3:
                p["change_1d"] = followers - prev_f
                p["change_1d_pct"] = round(100 * (followers - prev_f) / prev_f, 3)
            # First day we have numbers for them (for "New on Socialete").
            p["tracked_since"] = rows[0]["date"]
            # Breakouts: today's growth compared with their usual daily growth
            # (median of up to 14 earlier days; needs at least 4 days to compare).
            daily = []
            for prev_r, cur_r in zip(rows[:-1], rows[1:]):
                a_f, b_f = num(prev_r["followers"]), num(cur_r["followers"])
                gap_d = (date.fromisoformat(cur_r["date"]) - date.fromisoformat(prev_r["date"])).days
                if a_f and b_f is not None and gap_d > 0:
                    daily.append(100 * (b_f - a_f) / a_f / gap_d)
            earlier = sorted(daily[:-1][-14:])
            if len(earlier) >= 4 and p.get("change_1d_pct") and p["change_1d_pct"] > 0:
                typical = earlier[len(earlier) // 2]
                p["typical_daily_pct"] = round(typical, 3)
                p["breakout_x"] = round(p["change_1d_pct"] / max(typical, 0.02), 1)
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
        # Height and birthdate in the summary too, for the advanced search.
        det = details.get(a["name"]) or {}
        if det.get("height_cm"):
            p["height_cm"] = det["height_cm"]
        if det.get("birthdate"):
            p["birthdate"] = det["birthdate"]
        y = youtube.get("athlete:" + a["name"])
        if y:
            p["youtube"] = {k: v for k, v in y.items() if k != "recent"}
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
            "youtube": youtube.get("athlete:" + a["name"]),
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

    shutil.rmtree(SITE / "accounts", ignore_errors=True)   # rebuilt by build_orgs below
    (SITE / "data.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sample_data": sample,
        "last_run": last_run,
        "players": summaries,
        "orgs": build_orgs(history, profiles if not sample else {}),
        "creators": build_orgs(history, profiles if not sample else {}, "creators.csv"),
        "events": load_events(),
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
