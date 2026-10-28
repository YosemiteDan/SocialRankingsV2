"""
Turns data/history.csv into site/data.json, which the website reads.
Works out growth over 7 days, 30 days and 1 year for each player.

If there is no real history yet, it uses data/sample_history.csv so the
site has something to show, and the site displays a 'sample data' banner.
"""

import csv
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "site" / "data.json"

PERIODS = {"7d": 7, "30d": 30, "365d": 365}
CHART_DAYS = 90


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

    players = []
    for a in athletes:
        handle = a["instagram"].strip().lstrip("@").lower()
        rows = history.get(handle, [])
        p = {"name": a["name"], "team": a["team"], "position": a["position"],
             "instagram": handle, "followers": None, "history": []}
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
            cutoff = (last_day - timedelta(days=CHART_DAYS)).isoformat()
            p["history"] = [[r["date"], num(r["followers"])] for r in rows if r["date"] >= cutoff]
        players.append(p)

    players.sort(key=lambda p: -(p["followers"] or -1))
    for i, p in enumerate(players, 1):
        p["rank"] = i if p["followers"] is not None else None

    last_run = {}
    if (DATA / "last_run.json").exists() and not sample:
        last_run = json.loads((DATA / "last_run.json").read_text())

    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sample_data": sample,
        "last_run": last_run,
        "players": players,
    }, indent=1))
    print(f"Wrote {OUT.relative_to(ROOT)} ({len(players)} players, sample={sample})")


if __name__ == "__main__":
    main()
