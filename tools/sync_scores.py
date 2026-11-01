"""Keep Plex user ratings in step with MDBList's average score, for whole libraries, every night.

This replaces Kometa's `mass_metadata_update: ratings: user: [mdb_average, remove]`. It writes the
same value (score_average x 0.1, one decimal, field locked) and clears the score when MDBList has
none, but it never reads items one by one:

- one Plex listing per library (IDs and current scores),
- one MDBList request per 100 titles (movies by TMDb ID, shows by TVDb ID, as Kometa does),
- one Plex batch edit per score value, and only for scores that changed.

A full pass over ~90,000 titles takes minutes, so there is no need for refresh tiers.

Safety:
- A title with no TMDb/TVDb ID is never touched.
- If MDBList or Plex keeps failing, that library is skipped with no changes, and the exit code is 1.
- Clearing scores is skipped (with a warning) when an unusual number of titles lose their score
  at once, e.g. MDBList returning a partial response. See --max-remove (percent, default 2).
- An average of 0 means MDBList has no ratings yet, so the title gets no score. (Kometa writes 0.0;
  those placeholders are cleared, and don't count towards the limit above.)

usage: python3 sync_scores.py /path/to/config.yml "<Library>" ["<Library>" ...] [--dry] [--max-remove 2]
Reads the plex url/token and mdblist apikey from that config.yml.
"""
import json
import sys
import time
import urllib.parse
import urllib.request

import yaml

args = [a for a in sys.argv[1:] if not a.startswith("--")]
if len(args) < 2:
    sys.exit(__doc__)
DRY = "--dry" in sys.argv
MAX_REMOVE_PCT = float(sys.argv[sys.argv.index("--max-remove") + 1]) if "--max-remove" in sys.argv else 2.0
if "--max-remove" in sys.argv:
    args.remove(sys.argv[sys.argv.index("--max-remove") + 1])
cfg = yaml.safe_load(open(args[0]))
LIBRARIES = args[1:]
U, T = cfg["plex"]["url"].rstrip("/"), cfg["plex"]["token"]
MK = cfg["mdblist"]["apikey"]
UA = {"User-Agent": "kometa-config-scores/1.0", "Accept": "application/json"}


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def http(url, method="GET", body=None, timeout=120, tries=6):
    for attempt in range(tries):
        try:
            data = json.dumps(body).encode() if body is not None else None
            headers = dict(UA, **({"Content-Type": "application/json"} if data else {}))
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            return urllib.request.urlopen(req, timeout=timeout).read()
        except Exception as e:  # retry anything: proxies time out, APIs rate-limit
            err = e
            time.sleep(5 * (attempt + 1))
    where = urllib.parse.urlparse(url).path  # no host or keys in logs
    raise RuntimeError(f"{method} {where} failed after {tries} tries: {err}")


def plex(path, method="GET", timeout=120):
    return http(f"{U}{path}{'&' if '?' in path else '?'}X-Plex-Token={T}", method, timeout=timeout)


def listing(key, kind, page=500):
    """All titles in a library, in pages, so no single request outlasts a proxy timeout."""
    out, start = [], 0
    while True:
        mc = json.loads(plex(f"/library/sections/{key}/all?type={1 if kind == 'movie' else 2}&includeGuids=1"
                             f"&X-Plex-Container-Start={start}&X-Plex-Container-Size={page}"))["MediaContainer"]
        got = mc.get("Metadata", [])
        out += got
        start += len(got)
        if len(got) < page:
            total = int(mc.get("totalSize", len(out)))
            if len(out) != total:
                raise RuntimeError(f"listing returned {len(out)} of {total} titles")
            return out


averages = {}  # ("movie", tmdb) / ("show", tvdb) -> "7.8" or None, shared across libraries


def fetch(kind, ids):
    provider, mtype = ("tmdb", "movie") if kind == "movie" else ("tvdb", "show")
    missing = [i for i in dict.fromkeys(ids) if (kind, i) not in averages]
    for n in range(0, len(missing), 100):
        batch = missing[n:n + 100]
        res = json.loads(http(f"https://api.mdblist.com/{provider}/{mtype}/?apikey={MK}", "POST", {"ids": batch}))
        if not isinstance(res, list):
            raise RuntimeError(f"MDBList returned {type(res).__name__}, expected a list")
        got = {}
        for d in res:
            rid = (d.get("ids") or {}).get(provider)
            avg = d.get("score_average")
            if str(rid).isdigit():
                got[int(rid)] = f"{avg * 0.1:.1f}" if isinstance(avg, (int, float)) and 0 < avg <= 100 else None
        for i in batch:
            averages[(kind, i)] = got.get(i)
        time.sleep(0.25)


def sync(lib, key, kind):
    prefix = "tmdb://" if kind == "movie" else "tvdb://"
    items = listing(key, kind)
    have = []
    for it in items:
        ids = [g["id"][7:] for g in it.get("Guid", []) if g["id"].startswith(prefix)]
        if ids and ids[0].isdigit():
            cur = it.get("userRating")
            have.append((it["ratingKey"], int(ids[0]), f"{float(cur):.1f}" if cur is not None else None))
    fetch(kind, [i for _, i, _ in have])
    sets, removes, zeros, same, none = {}, [], [], 0, 0
    for rk, i, cur in have:
        target = averages.get((kind, i))
        if target is None:
            none += 1
            if cur == "0.0":
                zeros.append(rk)  # Kometa writes 0.0 when MDBList has no ratings yet; that isn't a real score
            elif cur is not None:
                removes.append(rk)
        elif target != cur:
            sets.setdefault(target, []).append(rk)
        else:
            same += 1
    n_set = sum(map(len, sets.values()))
    limit = max(25, len(have) * MAX_REMOVE_PCT / 100)
    note = ""
    if len(removes) > limit:  # real scores vanishing in bulk means a bad response, not a real change
        note = f" | WARNING: {len(removes)} scores to clear is over the limit of {int(limit)}; not clearing them"
        removes = []
    removes += zeros
    if not DRY:
        for value, keys in sorted(sets.items()):
            for n in range(0, len(keys), 100):
                q = urllib.parse.urlencode({"type": 1 if kind == "movie" else 2, "id": ",".join(keys[n:n + 100]), "userRating.value": value, "userRating.locked": 1})
                plex(f"/library/sections/{key}/all?{q}", "PUT")
        for n in range(0, len(removes), 100):
            q = urllib.parse.urlencode({"type": 1 if kind == "movie" else 2, "id": ",".join(removes[n:n + 100]), "userRating.value": "", "userRating.locked": 1})
            plex(f"/library/sections/{key}/all?{q}", "PUT")
    log(f"{'[dry] ' if DRY else ''}{lib}: {len(items)} titles, {len(have)} with an ID | set {n_set}, cleared {len(removes)}, "
        f"unchanged {same}, no MDBList average {none}{note}")


def main():
    start = time.time()
    sections = {d["title"]: (d["key"], d["type"]) for d in json.loads(plex("/library/sections"))["MediaContainer"]["Directory"]}
    failed = []
    for lib in LIBRARIES:
        try:
            key, typ = sections[lib]
            if typ not in ("movie", "show"):
                raise RuntimeError(f"is a {typ} library")
            sync(lib, key, typ)
        except Exception as e:
            failed.append(lib)
            log(f"{lib}: SKIPPED, no changes made: {e}")
    log(f"done in {time.time() - start:.0f}s{', FAILED: ' + ', '.join(failed) if failed else ''}")
    sys.exit(1 if failed else 0)


main()
