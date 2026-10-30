"""
One-off helper: builds data/candidates.csv, a list of ACTIVE Australian athletes
across all sports who have an Instagram account recorded on Wikidata (the free
database behind Wikipedia), biggest names first.

Nothing here changes the site. It's a shortlist to choose from; copy the rows you
want into data/athletes.csv. Run it from GitHub: Actions -> "Find candidate athletes".

How "biggest names" is measured: views of their English Wikipedia article over the
last 60 days, i.e. who people are reading about right now. (Counting Wikipedia
language editions instead would favour soccer players and Olympians, whose
articles are translated widely, over AFL and NRL stars.)

How "active" is decided. Wikidata often doesn't record when a career ends, so
"no end date" isn't enough: every athlete needs positive evidence of being active:
  - a team spell that ended within the last year, or
  - a current team they joined in the last 4 years, or
  - a current team and aged 32 or under, or
  - a major event (Olympics, world championships, tours...) since 3 years ago.
Plus: no date of death, no recorded career end, aged MAX_AGE or under.
Athletes without evidence go to data/candidates_unconfirmed.csv rather than being
dropped, in case they're active but Wikidata is out of date.
"""

import csv
import json
import time
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "candidates.csv"
OUT_UNCONFIRMED = ROOT / "data" / "candidates_unconfirmed.csv"
ATHLETES = ROOT / "data" / "athletes.csv"
ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "AthleteSocialRankings/1.0 (github.com; one-off candidate list)"

MAX_AGE = 40           # older athletes are assumed retired
MIN_ARTICLES = 2       # leave out people with only one Wikipedia article
TOP_N = 1500           # rows kept in the spreadsheet
TEAM_BATCH = 150       # people per team-membership query


def sparql(query, tries=3):
    for attempt in range(tries):
        try:
            r = requests.post(ENDPOINT, data={"query": query}, timeout=120,
                              headers={"Accept": "application/sparql-results+json",
                                       "User-Agent": USER_AGENT})
            if r.status_code == 429:
                time.sleep(30)
                continue
            r.raise_for_status()
            return r.json()["results"]["bindings"]
        except requests.RequestException as e:
            print(f"  Wikidata query failed ({e}); retrying")
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("Wikidata didn't answer after several tries")


def val(b, k):
    return b[k]["value"] if k in b else ""


def base_people():
    """Australians (citizens or representing Australia) with a sport and an Instagram handle."""
    born_after = date(date.today().year - MAX_AGE, 1, 1).isoformat()
    q = f"""
    SELECT ?p ?name ?ig ?born ?links ?article
           (GROUP_CONCAT(DISTINCT ?sportName; separator="|") AS ?sports) WHERE {{
      {{ ?p wdt:P27 wd:Q408 }} UNION {{ ?p wdt:P1532 wd:Q408 }}
      ?p wdt:P31 wd:Q5 ; wdt:P641 ?sport ; wdt:P2003 ?ig ; wikibase:sitelinks ?links .
      FILTER(?links >= {MIN_ARTICLES})
      FILTER NOT EXISTS {{ ?p wdt:P570 [] }}          # no date of death
      FILTER NOT EXISTS {{ ?p wdt:P2032 [] }}         # no recorded career end
      OPTIONAL {{ ?p wdt:P569 ?born }}
      OPTIONAL {{ ?article schema:about ?p ; schema:isPartOf <https://en.wikipedia.org/> }}
      FILTER(!BOUND(?born) || ?born >= "{born_after}"^^xsd:dateTime)
      ?p rdfs:label ?name . FILTER(LANG(?name) = "en")
      ?sport rdfs:label ?sportName . FILTER(LANG(?sportName) = "en")
    }}
    GROUP BY ?p ?name ?ig ?born ?links ?article
    """
    people = {}
    for b in sparql(q):
        qid = val(b, "p").rsplit("/", 1)[-1]
        p = people.setdefault(qid, {
            "qid": qid, "name": val(b, "name"), "instagram": val(b, "ig").lstrip("@").lower(),
            "born": val(b, "born")[:10], "articles": int(val(b, "links") or 0),
            "sports": set(), "article": val(b, "article"),
        })
        p["sports"].update(s for s in val(b, "sports").split("|") if s)
    return people


def team_memberships(qids):
    """{qid: [(team name, start year, end year)]} for sports teams."""
    out = {}
    for i in range(0, len(qids), TEAM_BATCH):
        batch = " ".join(f"wd:{q}" for q in qids[i:i + TEAM_BATCH])
        q = f"""
        SELECT ?p ?teamName ?start ?end WHERE {{
          VALUES ?p {{ {batch} }}
          ?p p:P54 ?st . ?st ps:P54 ?team .
          OPTIONAL {{ ?st pq:P580 ?start }}
          OPTIONAL {{ ?st pq:P582 ?end }}
          ?team rdfs:label ?teamName . FILTER(LANG(?teamName) = "en")
        }}"""
        for b in sparql(q):
            qid = val(b, "p").rsplit("/", 1)[-1]
            out.setdefault(qid, []).append((val(b, "teamName"), val(b, "start")[:4], val(b, "end")[:4]))
        time.sleep(1)
    return out


def events(qids):
    """{qid: [(event name, year)]} for events they took part in (P1344)."""
    out = {}
    for i in range(0, len(qids), TEAM_BATCH):
        batch = " ".join(f"wd:{q}" for q in qids[i:i + TEAM_BATCH])
        q = f"""
        SELECT ?p ?evName ?when WHERE {{
          VALUES ?p {{ {batch} }}
          ?p wdt:P1344 ?ev .
          OPTIONAL {{ ?ev wdt:P585 ?d1 }} OPTIONAL {{ ?ev wdt:P580 ?d2 }}
          BIND(COALESCE(?d1, ?d2) AS ?when)
          ?ev rdfs:label ?evName . FILTER(LANG(?evName) = "en")
        }}"""
        for b in sparql(q):
            qid = val(b, "p").rsplit("/", 1)[-1]
            name = val(b, "evName")
            year = val(b, "when")[:4]
            if not year.isdigit():                       # e.g. "2024 Summer Olympics"
                year = next((w for w in name.split() if w[:4].isdigit() and len(w) >= 4), "")[:4]
            out.setdefault(qid, []).append((name, int(year) if year.isdigit() else 0))
        time.sleep(1)
    return out


NOT_SPORT = ("eurovision", "song contest", "election", "music", "award")


def pageviews(article_urls):
    """{article url: views of that English Wikipedia article over the last ~60 days}.
    Uses Wikipedia's own API, 50 articles per request."""
    titles = {u.rsplit("/wiki/", 1)[-1].replace("_", " "): u for u in article_urls if u}
    titles = {requests.utils.unquote(t): u for t, u in titles.items()}
    out = {}
    names = sorted(titles)
    for i in range(0, len(names), 50):
        batch = names[i:i + 50]
        params = {"action": "query", "prop": "pageviews", "titles": "|".join(batch),
                  "format": "json", "formatversion": "2", "redirects": "1"}
        cont = {}
        q = {"normalized": [], "redirects": [], "pages": {}}
        for _ in range(20):                      # Wikipedia returns views a few pages at a time
            data = None
            for attempt in range(3):
                try:
                    r = requests.get("https://en.wikipedia.org/w/api.php", params={**params, **cont},
                                     timeout=60, headers={"User-Agent": USER_AGENT})
                    r.raise_for_status()
                    data = r.json()
                    break
                except (requests.RequestException, ValueError) as e:
                    print(f"  Page-view lookup failed ({e}); retrying")
                    time.sleep(5 * (attempt + 1))
            if data is None:
                break
            part = data.get("query", {})
            for k in ("normalized", "redirects"):
                for m in part.get(k, []):
                    if m not in q[k]:
                        q[k].append(m)
            for page in part.get("pages", []):
                pv = page.get("pageviews")
                if pv or page.get("title") not in q["pages"]:
                    q["pages"][page.get("title", "")] = pv or q["pages"].get(page.get("title", ""))
            if "continue" not in data:
                break
            cont = data["continue"]
            time.sleep(0.5)
        back = {}
        for m in q["normalized"] + q["redirects"]:
            back[m["to"]] = back.get(m["from"], m["from"])
        for t, pv in q["pages"].items():
            views = sum(v or 0 for v in (pv or {}).values())
            original = back.get(t, t)
            original = back.get(original, original)
            if original in titles:
                out[titles[original]] = views
        time.sleep(1)
    return out


def active_evidence(age, team_spells, event_list, this_year):
    """A short reason they look active, or "" if there's no evidence."""
    for t, s, e in team_spells:
        if e.isdigit() and int(e) >= this_year - 1:
            return f"played for {t} until {e}"
    for t, s, e in sorted(team_spells, key=lambda x: x[1], reverse=True):
        if not e and s.isdigit() and int(s) >= this_year - 4:
            return f"at {t} since {s}"
    if age != "" and age <= 32 and any(not e for t, s, e in team_spells):
        return f"at {next(t for t, s, e in team_spells if not e)}"
    recent = sorted((y, n) for n, y in event_list
                    if y >= this_year - 3 and not any(w in n.lower() for w in NOT_SPORT))
    if recent:
        return f"competed at {recent[-1][1]}"
    return ""


def main():
    this_year = date.today().year
    print("Finding Australian athletes with Instagram on Wikidata...")
    people = base_people()
    print(f"  {len(people)} found before the active-player check")

    teams = team_memberships(sorted(people))
    evs = events(sorted(people))
    tracked_names, tracked_handles = set(), set()
    if ATHLETES.exists():
        with ATHLETES.open(newline="") as f:
            for r in csv.DictReader(f):
                tracked_names.add(r["name"].lower())
                tracked_handles.add((r.get("instagram") or "").lower())

    rows, unconfirmed, retired = [], [], 0
    for qid, p in people.items():
        ts = teams.get(qid, [])
        current = [t for t, s, e in ts if not e]
        recent_end = max((int(e) for t, s, e in ts if e.isdigit()), default=0)
        if ts and not current and recent_end < this_year - 1:
            retired += 1          # every team they played for has ended: treat as retired
            continue
        # Prefer the most recently started current team.
        current_sorted = sorted(((s or "0", t) for t, s, e in ts if not e), reverse=True)
        age = this_year - int(p["born"][:4]) if p["born"][:4].isdigit() else ""
        evidence = active_evidence(age, ts, evs.get(qid, []), this_year)
        (rows if evidence else unconfirmed).append({
            "name": p["name"],
            "sport": " / ".join(sorted(p["sports"])),
            "current_team": current_sorted[0][1] if current_sorted else "",
            "other_current_teams": "; ".join(t for _, t in current_sorted[1:4]),
            "born": p["born"],
            "age": age,
            "active_because": evidence,
            "instagram": p["instagram"],
            "wikipedia_views_60d": 0,
            "wikipedia_articles": p["articles"],
            "already_tracked": "yes" if (p["name"].lower() in tracked_names or p["instagram"] in tracked_handles) else "",
            "wikidata": f"https://www.wikidata.org/wiki/{qid}",
            "_article": p["article"],
        })

    print(f"  Checking Wikipedia page views for {len(rows) + len(unconfirmed)} athletes...")
    views = pageviews([r["_article"] for r in rows + unconfirmed])
    for r in rows + unconfirmed:
        r["wikipedia_views_60d"] = views.get(r.pop("_article"), 0)
    for out_rows, path in ((rows, OUT), (unconfirmed, OUT_UNCONFIRMED)):
        out_rows.sort(key=lambda r: (-r["wikipedia_views_60d"], -r["wikipedia_articles"]))
        del out_rows[TOP_N:]
        with path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out_rows[0]) if out_rows else ["name"], lineterminator="\n")
            w.writeheader()
            w.writerows(out_rows)

    by_sport = {}
    for r in rows:
        for s in r["sport"].split(" / "):
            by_sport[s] = by_sport.get(s, 0) + 1
    print(f"  {retired} left out as retired; {len(rows)} active athletes saved to data/candidates.csv; "
          f"{len(unconfirmed)} with no evidence either way saved to data/candidates_unconfirmed.csv")
    print("  By sport:", json.dumps(dict(sorted(by_sport.items(), key=lambda x: -x[1])[:25])))


if __name__ == "__main__":
    main()
