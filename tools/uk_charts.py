"""Write the UK broadcaster streaming charts into Kometa collection files, nightly before Kometa runs.

MDBList has weekly UK charts (JustWatch data) for BBC iPlayer, ITVX, Channel 4 and NOW, but Kometa's
`mdblist_streaming` only accepts the services on its own list, so these four can't be set up like the
Netflix row. This fetches them and writes each chart's TMDb IDs, in chart order, into:

  uk_charts_movies.yml   (load in film libraries)
  uk_charts_tv.yml       (load in TV libraries)

next to config.yml. Kometa then builds "Top on <service>" rows from those IDs, in chart order, showing only
what's in the library, like the other "Top on" rows (config/streaming_collections.yml).

These charts aren't offered on MDBList's website or in its docs, so they're checked before use:
- MDBList answers an unknown service code with the chart for ALL services instead of an error, so a chart
  that matches the all-services chart is rejected.
- A chart that is empty or fails to load is rejected.
A rejected chart keeps last night's titles (read back from the file), so a bad night changes nothing.

usage: python3 uk_charts.py /path/to/config.yml [--dry]
Reads the mdblist apikey from that config.yml. Exit code 1 if any chart was rejected.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import yaml

# Row name, JustWatch code (as MDBList takes it), and the order number for the row's sort title
# (after the rows in streaming_collections.yml, which use 01-06).
SERVICES = [
    ("BBC iPlayer", "bbc", "07"),
    ("ITVX", "itv", "08"),
    ("Channel 4", "al4", "09"),
    ("NOW", "ntv", "10"),
]
FILES = {"movie": "uk_charts_movies.yml", "show": "uk_charts_tv.yml"}

HEADER = """\
# GENERATED every night by kometa-tools/uk_charts.py - don't edit by hand, changes are overwritten.
# Weekly UK streaming charts (MDBList / JustWatch) for services Kometa's mdblist_streaming doesn't cover.
# Each list is the chart's TMDb IDs in chart order; the row shows only what's in the library, in that order.
# (Kometa's `text` builder: it counts as one source, so collection_order: custom keeps the chart order. A list under
# tmdb_movie/tmdb_show counts as one source per ID, and Kometa refuses custom order for those.)
"""


def main():
    args = [a for a in sys.argv[1:] if a != "--dry"]
    if len(args) != 1:
        sys.exit(__doc__)
    dry = "--dry" in sys.argv
    cfg_path = args[0]
    key = yaml.safe_load(open(cfg_path))["mdblist"]["apikey"]
    out_dir = os.path.dirname(os.path.abspath(cfg_path))

    def chart(kind, provider=None):
        params = {"apikey": key, "country": "GB", "period": "weekly"}
        if provider:
            params["provider"] = provider
        url = f"https://api.mdblist.com/justwatch/streaming-charts/{kind}?{urllib.parse.urlencode(params)}"
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "kometa-tools"})
                data = json.loads(urllib.request.urlopen(req, timeout=60).read())
                items = data.get("results", data.get("items", [])) if isinstance(data, dict) else data
                time.sleep(0.5)
                return [((i.get("ids") or {}).get("tmdb"), i.get("title")) for i in items if (i.get("ids") or {}).get("tmdb")]
            except Exception as e:  # noqa: BLE001 - any failure just means "no chart tonight"
                print(f"  {kind} {provider or 'all'}: attempt {attempt + 1} failed: {e}")
                time.sleep(5)
        return None

    print(time.strftime("== %Y-%m-%d %H:%M"))
    failed = False
    for kind, fname in FILES.items():
        path = os.path.join(out_dir, fname)
        previous = {}
        if os.path.exists(path):
            for name, coll in ((yaml.safe_load(open(path)) or {}).get("collections") or {}).items():
                previous[name] = [int(str(v).split(":")[-1]) for v in coll.get("text") or []]
        everything = chart(kind)
        everything_ids = [i for i, _ in everything] if everything else None
        collections = {}
        for service, code, order in SERVICES:
            name = f"Top on {service}"
            got = chart(kind, code)
            ids = [i for i, _ in got] if got else []
            problem = None
            if got is None:
                problem = "didn't load"
            elif not ids:
                problem = "is empty"
            elif everything_ids is None:
                problem = "can't be checked (the all-services chart didn't load)"
            elif ids == everything_ids:
                problem = "is the all-services chart (MDBList didn't recognise the code)"
            if problem:
                failed = True
                ids = previous.get(name, [])
                print(f"{kind:5} {name}: chart {problem}; keeping last night's {len(ids)} titles")
            else:
                print(f"{kind:5} {name}: {len(ids)} titles - " + "; ".join(t for _, t in got[:5]))
            if not ids:
                continue
            collections[name] = {
                "text": [f"tmdb:{i}" for i in ids],
                "collection_order": "custom",
                "sync_mode": "sync",
                "minimum_items": 5,              # a row of one or two titles looks broken; skip (and delete) it
                "delete_below_minimum": True,
                "visible_home": False,
                "visible_shared": False,
                "visible_library": True,
                "sort_title": f"~{order}_{name}",
                "summary": f"What's most watched on {service} in the UK this week, from this library.",
            }
        if not collections:
            print(f"no charts for {fname}; leaving it as it is")
            continue
        text = HEADER + yaml.safe_dump({"collections": collections}, sort_keys=False, allow_unicode=True, width=200)
        if dry:
            print(f"--dry: not writing {path}")
            continue
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
