"""One-off first fill of MDBList-average user ratings for a movie library, without Kometa.

Why: Kometa's mass_metadata_update reloads every item from Plex in batches of 100 before it
writes. On a server where a cold film read takes several seconds, behind a proxy with a short
timeout, those batches time out and the whole library's operations abort. This script never
reads items one by one. It takes the IDs from a single library listing, asks MDBList for the
averages in batches of 100 (one API request per batch), and writes them with Plex's batch edit.

It writes exactly what Kometa's `ratings: user: [mdb_average, ...]` writes (score_average x 0.1,
one decimal, field locked), so Kometa's scheduled runs take over afterwards with no changes.
Only films with no user rating are touched; films without an average are left alone.

usage: python3 fill_scores.py /path/to/config.yml "<Library>" [--dry]
Reads plex url/token and mdblist apikey from that config.yml.
"""
import json
import sys
import time
import urllib.parse
import urllib.request

import yaml

if len(sys.argv) < 3:
    sys.exit(__doc__)
cfg = yaml.safe_load(open(sys.argv[1]))
lib, dry = sys.argv[2], "--dry" in sys.argv
U, T = cfg["plex"]["url"].rstrip("/"), cfg["plex"]["token"]
MK = cfg["mdblist"]["apikey"]
UA = {"User-Agent": "kometa-config-fill/1.0", "Accept": "application/json"}


def http(url, method="GET", body=None, timeout=120, tries=6):
    for attempt in range(tries):
        try:
            data = json.dumps(body).encode() if body is not None else None
            headers = dict(UA, **({"Content-Type": "application/json"} if data else {}))
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            return urllib.request.urlopen(req, timeout=timeout).read()
        except Exception as e:  # retry anything: proxies time out, APIs hiccup
            err = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"{method} {url.split('?')[0]} failed: {err}")


def plex(path, method="GET", timeout=120):
    return http(f"{U}{path}{'&' if '?' in path else '?'}X-Plex-Token={T}", method, timeout=timeout)


sections = {d["title"]: d["key"] for d in json.loads(plex("/library/sections"))["MediaContainer"]["Directory"]}
key = sections[lib]
items = json.loads(plex(f"/library/sections/{key}/all?type=1&includeGuids=1", timeout=900))["MediaContainer"].get("Metadata", [])

todo = {}  # tmdb id -> [ratingKey, ...]
for it in items:
    if it.get("userRating") is not None:
        continue
    tmdb = [g["id"][7:] for g in it.get("Guid", []) if g["id"].startswith("tmdb://")]
    if tmdb and tmdb[0].isdigit():
        todo.setdefault(int(tmdb[0]), []).append(it["ratingKey"])
print(f"{lib}: {len(items)} films, {sum(map(len, todo.values()))} unscored with a TMDb ID", flush=True)

ids, found = list(todo), {}
for i in range(0, len(ids), 100):
    res = json.loads(http(f"https://api.mdblist.com/tmdb/movie/?apikey={MK}", "POST", {"ids": ids[i:i + 100]}))
    for d in res if isinstance(res, list) else []:
        tid = (d.get("ids") or {}).get("tmdb") or d.get("tmdbid") or d.get("id")
        avg = d.get("score_average")
        if str(tid).isdigit() and isinstance(avg, (int, float)) and 0 < avg <= 100:
            found[int(tid)] = f"{avg * 0.1:.1f}"
    time.sleep(0.25)

groups = {}  # "7.8" -> [ratingKey, ...]
for tid, value in found.items():
    groups.setdefault(value, []).extend(todo[tid])
print(f"MDBList: {len(found)} of {len(ids)} have an average; {sum(map(len, groups.values()))} films to write", flush=True)
if dry:
    sys.exit()

for value, keys in sorted(groups.items()):
    for j in range(0, len(keys), 100):
        q = urllib.parse.urlencode({"type": 1, "id": ",".join(keys[j:j + 100]), "userRating.value": value, "userRating.locked": 1})
        plex(f"/library/sections/{key}/all?{q}", "PUT")

items = json.loads(plex(f"/library/sections/{key}/all?type=1", timeout=900))["MediaContainer"].get("Metadata", [])
print(f"{lib}: {sum(1 for it in items if it.get('userRating') is not None)} of {len(items)} films now scored", flush=True)
