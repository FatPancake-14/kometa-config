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

Score rows (film libraries): after the scores, the script also picks today's films for the discovery rows
(Rated 80+, Hidden Gems, Under 100 Minutes, Best Documentaries): 50 at random from every film that
qualifies, the same all day and new each night. It tags them with a Plex label named after the row, and
Kometa only shows the rows (config/score_rows_movies.yml: a smart collection per label).
A library gets rows when its Kometa entry loads score_rows_movies.yml; `use_gems: false` or
`use_docs: false` in that file's template_variables switches a row off there. The rules use the full
MDBList record the scores come from (every site's score and vote count, runtime, genres), so they cost no
extra requests. Filmed stage shows, concert films and stand-up specials are left out, using TMDb's tags.

usage: python3 sync_scores.py /path/to/config.yml "<Library>" ["<Library>" ...] [--dry] [--max-remove 2] [--rows-reset]
Reads the plex url/token and the mdblist and tmdb apikeys from that config.yml.
"""
import datetime
import json
import os
import random
import sys
import time
import urllib.parse
import urllib.request

import yaml

args = [a for a in sys.argv[1:] if not a.startswith("--")]
if len(args) < 2:
    sys.exit(__doc__)
DRY = "--dry" in sys.argv
ROWS_RESET = "--rows-reset" in sys.argv  # allow a row's pool to shrink a lot in one go (after changing the rules)
MAX_REMOVE_PCT = float(sys.argv[sys.argv.index("--max-remove") + 1]) if "--max-remove" in sys.argv else 2.0
if "--max-remove" in sys.argv:
    args.remove(sys.argv[sys.argv.index("--max-remove") + 1])
cfg = yaml.safe_load(open(args[0]))
LIBRARIES = args[1:]
U, T = cfg["plex"]["url"].rstrip("/"), cfg["plex"]["token"]
MK = cfg["mdblist"]["apikey"]
TK = cfg["tmdb"]["apikey"]
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
facts = {}     # tmdb -> what the row rules need (films only)
MAJOR = {"imdb": 1000, "letterboxd": 1000, "tomatoes": 20, "popcorn": 250, "metacritic": 7}  # min votes to count


def film_facts(d):
    s = {r["source"]: (r.get("score"), r.get("votes") or 0) for r in d.get("ratings") or [] if r.get("score") is not None}
    genres = {(g.get("title") if isinstance(g, dict) else str(g)).lower() for g in d.get("genres") or []}
    lb = s.get("letterboxd", (None, 0))
    return {
        "avg": d.get("score_average") or 0,
        "imdb_votes": s.get("imdb", (0, 0))[1],
        "lb": lb[0] if lb[1] >= 500 else 0,
        "major": [sc for src, (sc, v) in s.items() if src in MAJOR and v >= MAJOR[src]],
        "runtime": d.get("runtime") or 0,
        "year": int(d.get("year") or 0),
        "doc": "documentary" in genres,
        "stage": str(d.get("title") or "").lower().endswith(": the musical"),  # filmed stage shows TMDb hasn't tagged
    }


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
                if kind == "movie":
                    facts[int(rid)] = film_facts(d)
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
    return [(rk, i) for rk, i, _ in have]


# ---- Score rows ----------------------------------------------------------------------------------------
ROW_FILE = "score_rows_movies.yml"
ROW_MIN_YEAR = 1980  # the user's preference: no older films in these rows (unknown year counts as older)
ROW_SIZE = 50        # today's selection per row: random from everything that qualifies, fixed for the day
OLD_LABELS = ["Rated 80+ · Not Seen Yet", "Rated 80+, Not Seen Yet"]  # earlier row names; removed wherever found
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "rows.json")  # pool sizes, for the guard
# TMDb tags for things that aren't films: filmed theater, filmed play, musical proshot, concert film, stand-up comedy
NOT_FILMS_KEYWORDS = "246884|229756|334841|156205|9716"
not_films = set()


def agree(f, floor):  # at least three sites with enough votes, and none of them below the floor
    return len(f["major"]) >= 3 and min(f["major"]) >= floor


ROWS = {  # row (= Plex label = Kometa collection name): (switch in template_variables, rule). 60+ minutes keeps out shorts and TV specials
    "Rated 80+": (None, lambda f: not f["doc"] and f["avg"] >= 80 and f["imdb_votes"] >= 25000
                                 and f["runtime"] >= 60 and agree(f, 70)),
    "Hidden Gems": ("use_gems", lambda f: not f["doc"] and f["avg"] >= 78 and 2000 <= f["imdb_votes"] <= 25000
                    and f["lb"] >= 74 and f["runtime"] >= 70 and agree(f, 65)),
    "Under 100 Minutes": (None, lambda f: not f["doc"] and f["avg"] >= 75 and 75 <= f["runtime"] <= 99
                          and f["imdb_votes"] >= 10000 and agree(f, 65)),
    "Best Documentaries": ("use_docs", lambda f: f["doc"] and f["avg"] >= 80 and f["imdb_votes"] >= 5000
                           and f["runtime"] >= 60 and agree(f, 70)),
}


def row_settings(lib):
    """The rows a library should have, from its Kometa entry: None when it doesn't load the row file."""
    for entry in (cfg.get("libraries", {}).get(lib) or {}).get("collection_files") or []:
        if isinstance(entry, dict) and str(entry.get("file", "")).endswith(ROW_FILE):
            tv = entry.get("template_variables") or {}
            return {row for row, (switch, _) in ROWS.items() if switch is None or tv.get(switch, True) is not False}
    return None


def load_not_films():
    page = 1
    while True:
        d = json.loads(http(f"https://api.themoviedb.org/3/discover/movie?api_key={TK}&with_keywords={NOT_FILMS_KEYWORDS}&page={page}"))
        not_films.update(m["id"] for m in d["results"])
        if page >= min(d["total_pages"], 500):
            return
        page += 1


def label_members(key, keys_by_title):
    out = {}
    for title, lkey in keys_by_title.items():
        members, start = set(), 0
        while True:
            got = json.loads(plex(f"/library/sections/{key}/all?type=1&label={lkey}"
                                  f"&X-Plex-Container-Start={start}&X-Plex-Container-Size=500"))["MediaContainer"].get("Metadata", [])
            members.update(m["ratingKey"] for m in got)
            start += len(got)
            if len(got) < 500:
                break
        out[title] = members
    return out


def edit_label(key, keys, label, remove=False):
    for n in range(0, len(keys), 100):
        field = {"label[].tag.tag-": label} if remove else {"label[0].tag.tag": label}
        q = urllib.parse.urlencode(dict({"type": 1, "id": ",".join(sorted(keys)[n:n + 100])}, **field))
        plex(f"/library/sections/{key}/all?{q}", "PUT")


def rows(lib, key, films, wanted, pools):
    labels = {d["title"].lower(): d["key"] for d in json.loads(plex(f"/library/sections/{key}/label"))["MediaContainer"].get("Directory", [])}
    names = list(ROWS) + OLD_LABELS
    current = label_members(key, {name: labels[name.lower()] for name in names if name.lower() in labels})
    today = datetime.date.today().isoformat()
    shown = set()  # films already in an earlier row today: the rows avoid repeating each other
    out = []
    for row in names:
        pool = []
        if row in wanted:
            rule = ROWS[row][1]
            pool = sorted(rk for rk, i in films
                          if i in facts and i not in not_films and not facts[i]["stage"]
                          and facts[i]["year"] >= ROW_MIN_YEAR and rule(facts[i]))
        have = current.get(row, set())
        last = pools.get(f"{lib}|{row}", 0)
        if row in wanted and not ROWS_RESET and last >= 40 and len(pool) < last / 2:  # a sudden collapse means bad data
            out.append(f"{row} WARNING: pool shrank from {last} to {len(pool)}; today's films left as they were")
            continue
        # Seeded by date, so a second run on the same day picks the same films. Films already in an earlier
        # row today are only used when a row would otherwise come up short.
        rnd = random.Random(f"{today}|{lib}|{row}")
        fresh = [rk for rk in pool if rk not in shown]
        target = set(rnd.sample(fresh, min(ROW_SIZE, len(fresh))))
        if len(target) < ROW_SIZE:
            rest = [rk for rk in pool if rk in shown]
            target |= set(rnd.sample(rest, min(ROW_SIZE - len(target), len(rest))))
        shown |= target
        add, drop = target - have, have - target
        if not DRY:
            edit_label(key, add, row)
            edit_label(key, drop, row, remove=True)
        if row in wanted:
            pools[f"{lib}|{row}"] = len(pool)
            out.append(f"{row} {len(target)} of {len(pool)} (+{len(add)} -{len(drop)})")
        elif have:
            out.append(f"{row} removed from {len(drop)}")
    log(f"{'[dry] ' if DRY else ''}{lib} rows: " + ", ".join(out))


def main():
    start = time.time()
    sections = {d["title"]: (d["key"], d["type"]) for d in json.loads(plex("/library/sections"))["MediaContainer"]["Directory"]}
    failed, listed = [], {}
    for lib in LIBRARIES:
        try:
            key, typ = sections[lib]
            if typ not in ("movie", "show"):
                raise RuntimeError(f"is a {typ} library")
            listed[lib] = sync(lib, key, typ)
        except Exception as e:
            failed.append(lib)
            log(f"{lib}: SKIPPED, no changes made: {e}")
    with_rows = {lib: row_settings(lib) for lib in listed if sections[lib][1] == "movie"}
    with_rows = {lib: wanted for lib, wanted in with_rows.items() if wanted is not None}
    if with_rows:
        try:
            load_not_films()
        except Exception as e:
            failed.append("score rows")
            log(f"score rows: SKIPPED, no changes made: TMDb: {e}")
            with_rows = {}
    try:
        pools = json.load(open(STATE))
    except (OSError, ValueError):
        pools = {}
    for lib, wanted in with_rows.items():
        try:
            rows(lib, sections[lib][0], listed[lib], wanted, pools)
        except Exception as e:
            failed.append(f"{lib} rows")
            log(f"{lib} rows: SKIPPED: {e}")
    if with_rows and not DRY:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        json.dump(pools, open(STATE, "w"), indent=1, sort_keys=True)
    log(f"done in {time.time() - start:.0f}s{', FAILED: ' + ', '.join(failed) if failed else ''}")
    sys.exit(1 if failed else 0)


main()
