"""
Night-time helper: finds Instagram handles for the club players in data/new_players.csv
(from Wikipedia's current squad lists) and adds the clear matches to data/athletes.csv.

For each player it tries Wikidata's recorded handle, then the usual patterns
(firstnamelastname, firstname.lastname, firstname_lastname), checking each through
Meta. A handle is only added automatically when the account's name contains the
player's first and last name. Near misses go to data/new_player_handles.csv for review.

Runs in the evening, when the daily collection isn't using Meta's hourly allowance,
and picks up where it left off each night.
"""
import csv, json, os, re, sys, time, unicodedata
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent.parent
NEW, STATE, ATHLETES = ROOT / "data" / "new_players.csv", ROOT / "data" / "new_player_handles.csv", ROOT / "data" / "athletes.csv"
GRAPH = "https://graph.facebook.com/v25.0"
LIMIT = int(os.environ.get("LOOKUP_LIMIT", "185"))
STATE_COLS = ["name", "status", "handle", "ig_name", "followers", "tried"]


def plain(t):
    return re.sub(r"[^a-z ]", "", unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()).split()


def ig_account(token):
    fixed = os.environ.get("META_IG_USER_ID", "").strip()
    if fixed:
        return fixed
    for path in ("me", "me/accounts"):
        r = requests.get(f"{GRAPH}/{path}", params={"fields": "instagram_business_account", "access_token": token}, timeout=30).json()
        items = r.get("data", [r])
        for it in items:
            if (it.get("instagram_business_account") or {}).get("id"):
                return it["instagram_business_account"]["id"]
    sys.exit("No Instagram account found for the token")


def wikidata_handles(names):
    out = {}
    for i in range(0, len(names), 80):
        values = " ".join(json.dumps(n) + "@en" for n in names[i:i + 80])
        q = f'SELECT ?name ?ig WHERE {{ VALUES ?name {{ {values} }} ?p rdfs:label ?name ; wdt:P31 wd:Q5 ; wdt:P2003 ?ig . }}'
        try:
            r = requests.post("https://query.wikidata.org/sparql", data={"query": q}, timeout=90,
                              headers={"Accept": "application/sparql-results+json", "User-Agent": "SocialeteRankings/1.0 (github.com)"})
            for b in r.json()["results"]["bindings"]:
                out.setdefault(b["name"]["value"], b["ig"]["value"].lstrip("@").lower())
        except Exception as e:  # noqa: BLE001
            print("Wikidata failed:", e)
        time.sleep(2)
    return out


def main():
    token = os.environ["META_ACCESS_TOKEN"].strip()
    ig = ig_account(token)
    players = list(csv.DictReader(NEW.open()))
    state = {r["name"]: r for r in csv.DictReader(STATE.open())} if STATE.exists() else {}
    tracked = {r["name"].lower() for r in csv.DictReader(ATHLETES.open())}
    todo = [p for p in players if p["name"] not in state and p["name"].lower() not in tracked]
    wd = wikidata_handles([p["name"] for p in todo]) if todo else {}
    lookups, added, stop = 0, [], False
    for p in todo:
        w = plain(p["name"])
        if len(w) < 2:
            state[p["name"]] = {"name": p["name"], "status": "skipped", "tried": ""}; continue
        f, l = w[0], w[-1]
        guesses = list(dict.fromkeys(([wd[p["name"]]] if p["name"] in wd else []) + [f + l, f + "." + l, f + "_" + l]))
        result = {"name": p["name"], "status": "none", "tried": "|".join(guesses)}
        for h in guesses:
            if lookups >= LIMIT:
                stop = True; break
            lookups += 1
            time.sleep(1.5)
            r = requests.get(f"{GRAPH}/{ig}", timeout=30, params={"access_token": token,
                             "fields": f"business_discovery.username({h}){{username,name,followers_count}}"}).json()
            err = (r.get("error") or {})
            if err.get("code") in (4, 17, 32, 613) or "limit" in err.get("message", "").lower():
                print("Meta limit reached; continuing next run"); stop = True; break
            d = r.get("business_discovery")
            if not d:
                continue
            name_words = plain(d.get("name"))
            joined = "".join(name_words)
            if f in name_words + [joined[:len(f)]] and l in joined:
                result.update(status="found", handle=h, ig_name=d.get("name") or "", followers=d.get("followers_count") or "")
                added.append({"name": p["name"], "sport": p["sport"], "league": p["league"], "team": p["team"], "position": "",
                              "import": "", "instagram": h, "handle_checked": "yes"})
                break
            if l in joined or f + l in "".join(plain(h)):
                result.update(status="check", handle=h, ig_name=d.get("name") or "", followers=d.get("followers_count") or "")
        if stop and result["status"] == "none":
            break                    # finish this player next run
        state[p["name"]] = result
    with STATE.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=STATE_COLS, lineterminator="\n", extrasaction="ignore")
        w.writeheader(); w.writerows(state.values())
    if added:
        rows = list(csv.DictReader(ATHLETES.open()))
        cols = list(rows[0].keys())
        with ATHLETES.open("a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
            for a in added:
                w.writerow({c: a.get(c, "") for c in cols})
    left = sum(1 for p in players if p["name"] not in state and p["name"].lower() not in tracked)
    print(f"{lookups} lookups: {len(added)} players added; {sum(1 for r in state.values() if r['status'] == 'check')} to review; {left} still to check")


if __name__ == "__main__":
    main()
