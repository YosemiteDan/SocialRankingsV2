"""Check whether a name is free as a domain and an Instagram handle.
Run from GitHub: Actions -> "Check a name". Results show as notes on the run."""
import json, os, socket, sys
import requests

name = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
UA = {"User-Agent": "Mozilla/5.0 (name check)"}
def note(msg): print(f"::notice title=Name check::{msg}"); print(msg)

def rdap(domain):
    tld = domain.rsplit(".", 1)[-1]
    url = {"com": f"https://rdap.verisign.com/com/v1/domain/{domain}",
           "net": f"https://rdap.verisign.com/net/v1/domain/{domain}",
           "au": f"https://rdap.cctld.au/rdap/domain/{domain}"}.get(tld, f"https://rdap.org/domain/{domain}")
    try:
        r = requests.get(url, headers=UA, timeout=30, allow_redirects=True)
    except requests.RequestException as e:
        return f"couldn't check ({e.__class__.__name__})"
    if r.status_code == 404:
        return "AVAILABLE (not registered)"
    if r.ok:
        d = r.json()
        ev = {e.get("eventAction"): e.get("eventDate", "")[:10] for e in d.get("events", [])}
        reg = next((v.get("vcardArray", [None, []])[1] for v in d.get("entities", []) if "registrar" in v.get("roles", [])), [])
        regname = next((x[3] for x in reg if x and x[0] == "fn"), "")
        return f"TAKEN – registered {ev.get('registration','?')}, expires {ev.get('expiration','?')}{', registrar ' + regname if regname else ''}; status {', '.join(d.get('status', []))}"
    return f"couldn't check (HTTP {r.status_code})"

for dom in [f"{name}.com", f"{name}.com.au", f"{name}.au", f"{name}.co", f"{name}.io", f"{name}.app"]:
    try:
        ip = socket.gethostbyname(dom)
    except OSError:
        ip = None
    note(f"{dom}: {rdap(dom)}{' · website answering at ' + ip if ip else ''}")

# Instagram
def ig(handle):
    out = []
    try:
        r = requests.get("https://www.instagram.com/api/v1/users/web_profile_info/",
                         params={"username": handle}, timeout=30,
                         headers={**UA, "x-ig-app-id": "936619743392459"})
        if r.status_code == 404:
            out.append("Instagram says no such account (looks AVAILABLE)")
        elif r.ok and r.json().get("data", {}).get("user"):
            u = r.json()["data"]["user"]
            out.append(f"TAKEN – '{u.get('full_name')}', {u.get('edge_followed_by', {}).get('count')} followers, "
                       f"{u.get('edge_owner_to_timeline_media', {}).get('count')} posts, private={u.get('is_private')}")
        else:
            out.append(f"Instagram web check inconclusive (HTTP {r.status_code})")
    except requests.RequestException as e:
        out.append(f"Instagram web check failed ({e.__class__.__name__})")
    token, ig_id = os.environ.get("META_ACCESS_TOKEN", ""), os.environ.get("META_IG_USER_ID", "")
    if token:
        try:
            if not ig_id:
                me = requests.get("https://graph.facebook.com/v25.0/me", params={"fields": "instagram_business_account", "access_token": token}, timeout=30).json()
                ig_id = (me.get("instagram_business_account") or {}).get("id", "")
            if ig_id:
                r = requests.get(f"https://graph.facebook.com/v25.0/{ig_id}", timeout=30, params={
                    "fields": f"business_discovery.username({handle}){{username,name,followers_count}}", "access_token": token}).json()
                if "business_discovery" in r:
                    b = r["business_discovery"]; out.append(f"Meta: TAKEN by a business/creator account '{b.get('name')}' ({b.get('followers_count')} followers)")
                else:
                    out.append("Meta: no business/creator account with that name (a personal account could still have it)")
        except Exception as e:  # noqa: BLE001
            out.append(f"Meta check failed ({e.__class__.__name__})")
    return "; ".join(out)

for h in [name, f"{name}.au", f"{name}au", f"{name}_"]:
    note(f"Instagram @{h}: {ig(h)}")
