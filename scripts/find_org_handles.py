"""Prints Instagram handles Wikidata knows for Australian clubs, leagues and national teams
(run by hand from the 'Find official account handles' workflow, to check data/organisations.csv)."""
import requests

Q = """
SELECT DISTINCT ?teamLabel ?ig ?leagueLabel WHERE {
  VALUES ?ll { "Australian Football League"@en "National Rugby League"@en "National Basketball League"@en
               "A-League Men"@en "AFL Women's"@en "Women's National Basketball League"@en "NRL Women's Premiership"@en
               "A-League Women"@en "Big Bash League"@en "Women's Big Bash League"@en }
  ?league rdfs:label ?ll .
  ?team wdt:P118 ?league ; wdt:P2003 ?ig .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
} ORDER BY ?leagueLabel ?teamLabel
"""
Q2 = """
SELECT DISTINCT ?itemLabel ?ig WHERE {
  VALUES ?l { "Football Australia"@en "Australia national rugby league team"@en "Australia women's national rugby league team"@en
              "Australia men's national basketball team"@en "Australia women's national basketball team"@en "Basketball Australia"@en
              "AFL Women's"@en "Melbourne United"@en "New Zealand Breakers"@en "South East Melbourne Phoenix"@en "Tasmania JackJumpers"@en
              "Carlton Football Club"@en "Collingwood Football Club"@en "Fremantle Football Club"@en "Gold Coast Suns"@en
              "North Melbourne Football Club"@en "Port Adelaide Football Club"@en "Canterbury-Bankstown Bulldogs"@en "Melbourne Storm"@en
              "Newcastle Knights"@en "St. George Illawarra Dragons"@en "Wests Tigers"@en "Canberra Raiders"@en "Parramatta Eels"@en
              "Central Coast Mariners FC"@en "Macarthur FC"@en "Melbourne Victory FC"@en "Newcastle Jets FC"@en "Perth Glory FC"@en
              "Sydney FC"@en "Wellington Phoenix FC"@en "Western Sydney Wanderers FC"@en "Cricket Australia"@en "Tennis Australia"@en }
  ?item rdfs:label ?l ; wdt:P2003 ?ig .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
"""
for q in (Q, Q2):
    r = requests.get("https://query.wikidata.org/sparql", params={"query": q, "format": "json"},
                     headers={"User-Agent": "Socialete/1.0 (github.com/YosemiteDan)"}, timeout=120)
    for b in r.json()["results"]["bindings"]:
        print(" | ".join(b[k]["value"] for k in b))
    print("-----")
