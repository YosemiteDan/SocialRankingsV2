"""
Weekly: fills in each athlete's height, birthdate and birthplace from Wikidata
(the free database behind Wikipedia), for the "Player details" panel on profiles.

Only fills gaps: anything already in data/details.json (e.g. from the NBA's
official stats) is kept. Athletes are matched by name; when Wikidata has
several people with the same name, the one whose Instagram handle matches
ours wins, otherwise the one who plays our sport.

Runs as part of the daily job but only does the work once a week, or when
new athletes are added.
"""

import csv
import json
import time
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
ATHLETES = ROOT / "data" / "athletes.csv"
DETAILS = ROOT / "data" / "details.json"
CHECKED = ROOT / "data" / "wikidata_checked.json"
ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "SocialeteRankings/1.0 (github.com; weekly athlete details)"
REFRESH_DAYS = 7
BATCH = 60

# Our sport names -> words that appear in Wikidata's sport labels.
SPORT_WORDS = {"Basketball": ["basketball"], "Soccer": ["association football", "soccer"],
               "American football": ["american football"], "Tennis": ["tennis"],
               "AFL": ["australian rules"], "Rugby league": ["rugby league"], "Cricket": ["cricket"]}


def sparql(q):
    for attempt in range(3):
        try:
            r = requests.post(ENDPOINT, data={"query": q}, timeout=90,
                              headers={"Accept": "application/sparql-results+json", "User-Agent": USER_AGENT})
            if r.status_code == 429:
                time.sleep(30); continue
            r.raise_for_status()
            return r.json()["results"]["bindings"]
        except requests.RequestException as e:
            print(f"  Wikidata query failed ({e}); retrying")
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("Wikidata didn't answer")


def lookup(names):
    """{name: [candidate, ...]} where a candidate has qid, ig, sports, height_cm, birthdate, birthplace."""
    values = " ".join(json.dumps(n) + "@en" for n in names)
    q = f"""
    SELECT ?name ?p ?ig ?h ?born ?bpl (GROUP_CONCAT(DISTINCT ?sl; separator="|") AS ?sports) WHERE {{
      VALUES ?name {{ {values} }}
      ?p rdfs:label ?name ; wdt:P31 wd:Q5 .
      FILTER EXISTS {{ ?p wdt:P641|wdt:P54 [] }}
      OPTIONAL {{ ?p p:P2048/psn:P2048/wikibase:quantityAmount ?h }}
      OPTIONAL {{ ?p wdt:P569 ?born }}
      OPTIONAL {{ ?p wdt:P2003 ?ig }}
      OPTIONAL {{ ?p wdt:P19 ?bp . ?bp rdfs:label ?bpl . FILTER(LANG(?bpl) = "en") }}
      OPTIONAL {{ ?p wdt:P641 ?s . ?s rdfs:label ?sl . FILTER(LANG(?sl) = "en") }}
    }} GROUP BY ?name ?p ?ig ?h ?born ?bpl"""
    out = {}
    for b in sparql(q):
        v = lambda k: b.get(k, {}).get("value", "")
        qid = v("p").rsplit("/", 1)[-1]
        cands = out.setdefault(v("name"), {})
        c = cands.setdefault(qid, {"qid": qid, "ig": set(), "sports": set(), "height_cm": None,
                                    "birthdate": "", "birthplace": ""})
        if v("ig"):
            c["ig"].add(v("ig").lower().lstrip("@"))
        c["sports"].update(s.lower() for s in v("sports").split("|") if s)
        if v("h"):
            try:
                cm = round(float(v("h")) * 100)       # Wikidata's normalised height is in metres
                if 140 <= cm <= 235:
                    c["height_cm"] = c["height_cm"] or cm
            except ValueError:
                pass
        if v("born") and not v("born").startswith("-"):
            c["birthdate"] = c["birthdate"] or v("born")[:10]
        c["birthplace"] = c["birthplace"] or v("bpl")
    return {n: list(c.values()) for n, c in out.items()}


def choose(cands, handle, sport):
    if not cands:
        return None
    by_ig = [c for c in cands if handle and handle.lower() in c["ig"]]
    if by_ig:
        return by_ig[0]
    words = SPORT_WORDS.get(sport, [])
    by_sport = [c for c in cands if any(w in s for s in c["sports"] for w in words)]
    if len(by_sport) == 1:
        return by_sport[0]
    return cands[0] if len(cands) == 1 and not cands[0]["sports"] else None


def main():
    with ATHLETES.open(newline="") as f:
        rows = list(csv.DictReader(f))
    people = {}
    for r in rows:                                   # first row is their main sport and handle
        people.setdefault(r["name"], {"handle": (r.get("instagram") or "").strip().lstrip("@"), "sport": r["sport"]})
    checked = json.loads(CHECKED.read_text()) if CHECKED.exists() else {}
    last = checked.get("_last_full")
    stale = not last or (date.today() - date.fromisoformat(last)).days >= REFRESH_DAYS
    todo = sorted(people) if stale else sorted(n for n in people if n not in checked)
    if not todo:
        print("Wikidata details are up to date.")
        return
    print(f"Looking up {len(todo)} athletes on Wikidata")
    details = json.loads(DETAILS.read_text()) if DETAILS.exists() else {}
    found = 0
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        try:
            res = lookup(batch)
        except RuntimeError as e:
            print(f"  {e}; the rest wait until next time")
            break
        for n in batch:
            c = choose(res.get(n, []), people[n]["handle"], people[n]["sport"])
            checked[n] = date.today().isoformat()
            if not c:
                continue
            d = dict(details.get(n) or {})
            added = False
            for k in ("height_cm", "birthdate", "birthplace"):
                if c[k] and not d.get(k):
                    d[k] = c[k]; added = True
            if added:
                details[n] = d
                d.setdefault("source", "Wikidata")
                d["wikidata"] = c["qid"]
                found += 1
        time.sleep(1)
    if stale:
        checked["_last_full"] = date.today().isoformat()
    DETAILS.write_text(json.dumps(details, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    CHECKED.write_text(json.dumps(checked, indent=1, sort_keys=True) + "\n")
    have = sum(1 for n in people if (details.get(n) or {}).get("height_cm"))
    print(f"  Added details for {found} athletes; {have} of {len(people)} now have a height.")


if __name__ == "__main__":
    main()
