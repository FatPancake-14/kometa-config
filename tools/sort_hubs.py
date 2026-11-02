"""Put each library's Recommended rows in sort-title order, below Plex's own rows.

Kometa's `auto_sort_hubs` moves the collection rows above Plex's built-in rows. Plex shows Continue
Watching where its built-in rows are, so that pushes Continue Watching to the bottom of the Recommended
tab. This sorts the collection rows the same way (by collection sort title) but places them after Plex's
built-in rows, which keeps Continue Watching at the top. Run nightly after Kometa (instead of auto_sort_hubs).

usage: python3 sort_hubs.py /path/to/config.yml [--dry] [Library ...]   (default: every library)
"""
import json
import sys
import urllib.parse
import urllib.request

import yaml

args = [a for a in sys.argv[1:] if a != "--dry"]
if not args:
    sys.exit(__doc__)
DRY = "--dry" in sys.argv
cfg = yaml.safe_load(open(args[0]))
ONLY = args[1:]
U, T = cfg["plex"]["url"].rstrip("/"), cfg["plex"]["token"]


def plex(path, method="GET"):
    r = urllib.request.Request(f"{U}{path}{'&' if '?' in path else '?'}X-Plex-Token={T}",
                               headers={"Accept": "application/json"}, method=method)
    body = urllib.request.urlopen(r, timeout=60).read()
    return json.loads(body) if body and method == "GET" else None


for d in plex("/library/sections")["MediaContainer"]["Directory"]:
    if ONLY and d["title"] not in ONLY:
        continue
    k = d["key"]
    sorts = {str(c["ratingKey"]): c.get("titleSort") or c["title"]
             for c in plex(f"/library/sections/{k}/collections")["MediaContainer"].get("Metadata", [])}
    hubs = plex(f"/hubs/sections/{k}/manage")["MediaContainer"].get("Hub", [])
    custom = [h for h in hubs if h.get("identifier", "").startswith("custom.collection.")]
    builtin = [h for h in hubs if not h.get("identifier", "").startswith("custom.collection.")]
    if not custom:
        continue
    want = builtin + sorted(custom, key=lambda h: (sorts.get(h["identifier"].split(".")[-1]) or h["title"], h["title"]))
    if [h["identifier"] for h in want] == [h["identifier"] for h in hubs]:
        print(f"{d['title']}: already in order")
        continue
    print(f"{d['title']}: {'would sort' if DRY else 'sorting'} {len(custom)} rows -> "
          + " | ".join(h["title"] for h in want if h.get("promotedToRecommended")))
    if DRY:
        continue
    prev = builtin[-1]["identifier"] if builtin else None  # the collection rows go after Plex's own rows
    for h in want[len(builtin):]:
        q = f"?after={urllib.parse.quote(prev)}" if prev else ""
        plex(f"/hubs/sections/{k}/manage/{urllib.parse.quote(h['identifier'])}/move{q}", "PUT")
        prev = h["identifier"]
