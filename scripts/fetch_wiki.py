"""Run by hand: saves the HTML of Wikipedia squad pages to data/wiki/ for parsing."""
import json, sys, requests
from pathlib import Path
out = Path(__file__).resolve().parent.parent / "data" / "wiki"; out.mkdir(parents=True, exist_ok=True)
S = requests.Session(); S.headers["User-Agent"] = "SocialeteRankings/1.0 (github.com; squad lists)"
for q in sys.argv[1:]:
    r = S.get("https://en.wikipedia.org/w/api.php", params={"action": "query", "list": "search", "srsearch": q, "format": "json", "srlimit": 3}, timeout=60).json()
    hits = [h["title"] for h in r["query"]["search"]]
    print(q, "->", hits)
    if not hits: continue
    t = hits[0]
    p = S.get("https://en.wikipedia.org/w/api.php", params={"action": "parse", "page": t, "prop": "text", "format": "json", "redirects": 1}, timeout=60).json()
    html = p.get("parse", {}).get("text", {}).get("*", "")
    (out / (q.replace(" ", "_") + ".html")).write_text(f"<!-- {t} -->\n" + html)
    print("  saved", len(html))
