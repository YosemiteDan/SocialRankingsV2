"""
Weekly: fetches each NBA and WNBA player's details from the leagues' official
stats service (the same one behind nba.com and wnba.com): height, weight,
birthdate, country, college, draft, jersey number and position.

Saved to data/details.json and shown on each player's profile page.

This runs as part of the daily job but only does any work once a week (or when
a new player is added). The NBA's stats service is sometimes slow or refuses
requests from cloud servers; if that happens it just tries again next time,
and the site keeps the details it already has.
"""

import csv
import json
import re
import time
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
ATHLETES_FILE = ROOT / "data" / "athletes.csv"
DETAILS_FILE = ROOT / "data" / "details.json"
REFRESH_DAYS = 7

# The WNBA has its own copy of the same service; try it first for WNBA players.
HOSTS = {"NBA": ["https://stats.nba.com"], "WNBA": ["https://stats.wnba.com", "https://stats.nba.com"]}
LEAGUE_IDS = {"NBA": "00", "WNBA": "10"}
HEADERS = {
    # The stats service only answers requests that look like they come from nba.com.
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-AU,en;q=0.9",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}


def plain(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", text.lower())


def season_for(league, today):
    if league == "WNBA":                       # WNBA seasons run May-October
        return str(today.year if today.month >= 5 else today.year - 1)
    start = today.year if today.month >= 10 else today.year - 1
    return f"{start}-{(start + 1) % 100:02d}"  # e.g. 2026-27


def call(league, endpoint, **params):
    last = None
    for host in HOSTS[league]:
        site = "https://www.wnba.com" if "wnba" in host else "https://www.nba.com"
        headers = {**HEADERS, "Origin": site, "Referer": site + "/"}
        for attempt in range(2):
            try:
                r = requests.get(f"{host}/stats/{endpoint}", params=params, headers=headers, timeout=40)
                r.raise_for_status()
                return r.json()
            except (requests.RequestException, ValueError) as e:
                last = e
                time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"{endpoint} didn't respond: {last}")


def table(payload, name):
    for rs in payload.get("resultSets", []):
        if rs.get("name") == name:
            return [dict(zip(rs["headers"], row)) for row in rs["rowSet"]]
    return []


def find_player(roster, name):
    """Exact name first; otherwise same surname and first initial (so 'Steph'
    finds 'Stephanie'), as long as only one player fits."""
    exact = [pid for n, pid in roster.items() if plain(n) == plain(name)]
    if exact:
        return exact[0]
    first, last = plain(name.split()[0])[:1], plain(name.split()[-1])
    close = [pid for n, pid in roster.items()
             if plain(n.split()[-1]) == last and plain(n)[:1] == first]
    return close[0] if len(close) == 1 else None


def height_cm(h):
    """'6-8' (feet-inches) -> 203."""
    m = re.match(r"^(\d+)-(\d+)$", str(h or ""))
    return round((int(m.group(1)) * 12 + int(m.group(2))) * 2.54) if m else None


def main():
    today = date.today()
    with ATHLETES_FILE.open(newline="") as f:
        athletes = [a for a in csv.DictReader(f) if a.get("league") in LEAGUE_IDS]
    details = json.loads(DETAILS_FILE.read_text()) if DETAILS_FILE.exists() else {}

    def stale(name):
        got = (details.get(name) or {}).get("updated")
        return not got or (today - date.fromisoformat(got)).days >= REFRESH_DAYS
    todo = [a for a in athletes if stale(a["name"])]
    if not todo:
        print("Player details are up to date")
        return

    print(f"Fetching details for {len(todo)} players")
    rosters = {}
    for league in sorted({a["league"] for a in todo}):
        try:
            data = call(league, "commonallplayers", LeagueID=LEAGUE_IDS[league],
                        Season=season_for(league, today), IsOnlyCurrentSeason=1)
            rosters[league] = {p["DISPLAY_FIRST_LAST"]: p["PERSON_ID"]
                               for p in table(data, "CommonAllPlayers")}
            print(f"  {league}: {len(rosters[league])} players on current rosters")
        except RuntimeError as e:
            print(f"::warning::Couldn't get the {league} player list ({e}). Will try again next run.")

    for a in todo:
        ids = rosters.get(a["league"])
        if ids is None:
            continue
        pid = find_player(ids, a["name"])
        if not pid:
            print(f"  {a['name']}: not on a current {a['league']} roster under that name")
            continue
        try:
            info = table(call(a["league"], "commonplayerinfo", PlayerID=pid, LeagueID=LEAGUE_IDS[a["league"]]),
                         "CommonPlayerInfo")[0]
        except (RuntimeError, IndexError) as e:
            print(f"  {a['name']}: details not available ({e})")
            continue
        draft = None
        if str(info.get("DRAFT_YEAR", "")).isdigit():
            draft = f"{info['DRAFT_YEAR']} · round {info.get('DRAFT_ROUND')}, pick {info.get('DRAFT_NUMBER')}"
        elif info.get("DRAFT_YEAR"):
            draft = str(info["DRAFT_YEAR"]).title()          # e.g. "Undrafted"
        birth = (info.get("BIRTHDATE") or "")[:10] or None
        details[a["name"]] = {
            "league_player_id": pid,
            "height": info.get("HEIGHT") or None,
            "height_cm": height_cm(info.get("HEIGHT")),
            "weight_lb": int(info["WEIGHT"]) if str(info.get("WEIGHT", "")).isdigit() else None,
            "birthdate": birth,
            "country": info.get("COUNTRY") or None,
            "college": info.get("SCHOOL") or None,
            "draft": draft,
            "jersey": info.get("JERSEY") or None,
            "position": info.get("POSITION") or None,
            "first_season": info.get("FROM_YEAR") or None,
            "updated": today.isoformat(),
        }
        print(f"  {a['name']}: ok")
        time.sleep(1.5)

    DETAILS_FILE.write_text(json.dumps(details, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
