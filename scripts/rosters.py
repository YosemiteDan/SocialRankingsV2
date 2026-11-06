"""Run by hand ("Find club players" workflow): current players at the clubs of the
Australian leagues we cover, from Wikidata, with their Instagram handle where Wikidata
has one. Writes data/roster_candidates.csv for review before anyone is added."""
import csv, json, time
from datetime import date
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "roster_candidates.csv"
LEAGUES = {"Australian Football League": ("AFL", "AFL", "men"), "AFL Women's": ("AFL", "AFLW", "women"),
           "National Rugby League": ("Rugby league", "NRL", "men"), "NRL Women's Premiership": ("Rugby league", "NRLW", "women"),
           "A-League Men": ("Soccer", "A-League", "men"), "A-League Women": ("Soccer", "A-League Women", "women"),
           "National Basketball League": ("Basketball", "NBL", "men"), "Women's National Basketball League": ("Basketball", "WNBL", "women"),
           "Suncorp Super Netball": ("Netball", "Super Netball", "women"), "Super Netball": ("Netball", "Super Netball", "women")}
UA = "SocialeteRankings/1.0 (github.com; club rosters)"

def sparql(q):
    for a in range(4):
        try:
            r = requests.post("https://query.wikidata.org/sparql", data={"query": q}, timeout=120,
                              headers={"Accept": "application/sparql-results+json", "User-Agent": UA})
            if r.status_code == 429: time.sleep(30); continue
            r.raise_for_status(); return r.json()["results"]["bindings"]
        except requests.RequestException as e:
            print("retry", e); time.sleep(15 * (a + 1))
    return []

tracked = {r["name"].lower() for r in csv.DictReader(open(ROOT / "data" / "athletes.csv"))}
rows, seen = [], set()
for label, (sport, league, gender) in LEAGUES.items():
    q = f"""SELECT ?p ?pLabel ?teamLabel ?ig ?born ?start WHERE {{
      ?lg rdfs:label "{label}"@en . ?team wdt:P118 ?lg .
      ?p p:P54 ?st . ?st ps:P54 ?team . FILTER NOT EXISTS {{ ?st pq:P582 [] }}
      OPTIONAL {{ ?st pq:P580 ?start }} OPTIONAL {{ ?p wdt:P2003 ?ig }} OPTIONAL {{ ?p wdt:P569 ?born }}
      FILTER NOT EXISTS {{ ?p wdt:P570 [] }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }} }}"""
    res = sparql(q); kept = 0
    for b in res:
        v = lambda k: b.get(k, {}).get("value", "")
        name, born, start = v("pLabel"), v("born")[:10], v("start")[:10]
        if not name or name.startswith("Q") or (name.lower(), league) in seen: continue
        age = (date.today().year - int(born[:4])) if born else None
        if age and age > 38: continue
        if start and start < "2019": continue          # old spells Wikidata never closed
        if not start and age and age > 33: continue
        seen.add((name.lower(), league)); kept += 1
        rows.append({"name": name, "sport": sport, "league": league, "team": v("teamLabel"), "gender": gender,
                     "instagram": v("ig").lstrip("@").lower(), "born": born, "since": start,
                     "already_tracked": "yes" if name.lower() in tracked else "", "wikidata": v("p")})
    print(f"{label}: {len(res)} rows, kept {kept}")
    time.sleep(3)
rows.sort(key=lambda r: (r["league"], r["team"], r["name"]))
with OUT.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n"); w.writeheader(); w.writerows(rows)
print(len(rows), "players;", sum(1 for r in rows if r["instagram"]), "with Instagram;", sum(1 for r in rows if not r["already_tracked"]), "new")
