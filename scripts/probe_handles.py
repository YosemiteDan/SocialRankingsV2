"""Run by hand: looks up a list of possible Instagram handles through Meta and prints which
exist (name, followers, start of bio). Used to find official accounts before adding them.
Usage: python scripts/probe_handles.py handles.txt   (one handle per line; "# label" lines are printed)"""
import os, sys, time, requests

GRAPH = "https://graph.facebook.com/v25.0"
token, ig = os.environ["META_ACCESS_TOKEN"].strip(), os.environ.get("META_IG_USER_ID", "").strip()
if not ig:
    me = requests.get(f"{GRAPH}/me", params={"fields": "instagram_business_account", "access_token": token}, timeout=30).json()
    ig = (me.get("instagram_business_account") or {}).get("id") or ""
    if not ig:
        acc = requests.get(f"{GRAPH}/me/accounts", params={"fields": "instagram_business_account", "access_token": token}, timeout=30).json()
        ig = next((a["instagram_business_account"]["id"] for a in acc.get("data", []) if a.get("instagram_business_account")), "")
for line in open(sys.argv[1]):
    h = line.strip()
    if not h:
        continue
    if h.startswith("#"):
        print(h); continue
    time.sleep(1.5)
    r = requests.get(f"{GRAPH}/{ig}", timeout=30, params={"access_token": token,
        "fields": f"business_discovery.username({h}){{username,name,followers_count,biography}}"}).json()
    d = r.get("business_discovery")
    if d:
        print(f"FOUND @{h} | {d.get('name')} | {d.get('followers_count')} | {(d.get('biography') or '').replace(chr(10), ' ')[:70]}")
    elif "limit" in str(r).lower():
        print("RATE LIMIT - stopping"); break
