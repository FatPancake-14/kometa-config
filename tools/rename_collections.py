"""Rename Plex collections in place, so a Kometa row can change its name without leaving the old one behind.

Kometa finds a collection by its name. Rename a row in the YAML alone and Kometa makes a new collection,
while the old one stays in Plex. Run this before Kometa instead: it gives the existing collection the new
name (in every library), and Kometa then carries on with it: new poster, new filter, same row.

Safe to run again: libraries with no collection of the old name are skipped, and so is any library that
already has one with the new name (it says so; nothing is merged or deleted).

usage: python3 rename_collections.py /path/to/config.yml "Old name=New name" [...] [--dry]
"""
import json
import sys
import urllib.parse
import urllib.request

import yaml


def main():
    dry = "--dry" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--dry"]
    if len(args) < 2 or not all("=" in a for a in args[1:]):
        sys.exit(__doc__)
    cfg = yaml.safe_load(open(args[0]))["plex"]
    base, token = cfg["url"].rstrip("/"), cfg["token"]
    pairs = [a.split("=", 1) for a in args[1:]]

    def plex(path, method="GET"):
        sep = "&" if "?" in path else "?"
        req = urllib.request.Request(f"{base}{path}{sep}X-Plex-Token={token}", method=method,
                                     headers={"Accept": "application/json"})
        body = urllib.request.urlopen(req, timeout=60).read()
        return json.loads(body)["MediaContainer"] if method == "GET" and body else None

    for lib in plex("/library/sections").get("Directory", []):
        if lib["type"] not in ("movie", "show"):
            continue
        cols = plex(f"/library/sections/{lib['key']}/collections").get("Metadata", [])
        titles = {c["title"]: c for c in cols}
        for old, new in pairs:
            if old not in titles:
                continue
            if new in titles:
                print(f"{lib['title']}: has both '{old}' and '{new}'; left alone")
                continue
            q = urllib.parse.urlencode({"type": 18, "id": titles[old]["ratingKey"],
                                        "title.value": new, "title.locked": 1})
            if not dry:
                plex(f"/library/sections/{lib['key']}/all?{q}", "PUT")
            print(f"{'[dry] ' if dry else ''}{lib['title']}: '{old}' renamed '{new}'")


if __name__ == "__main__":
    main()
