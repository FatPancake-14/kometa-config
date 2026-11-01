#!/usr/bin/env python3
"""Replace secrets in Kometa YAML files with <<placeholders>>, keeping layout and comments.

    python3 tools/sanitize.py FILE [FILE ...]      # rewrites the files in place

Kometa fills placeholders in from KOMETA_* environment variables, --kometa-* arguments or a .env file next
to config.yml, e.g. `token: <<plex_token>>` is filled from KOMETA_PLEX_TOKEN. Placeholders are named
<section>_<key> after the top-level section they sit in (plex_url, plex_token, tmdb_apikey, mdblist_apikey).

Anything in SECRET_KEYS is replaced wherever it appears, commented out or not, so the files can be shared.
Exits non-zero if anything that still looks secret is left over.
"""
import re
import sys

SECRET_KEYS = {"url", "token", "apikey", "api_key", "client_id", "client_secret", "username", "password", "pin",
               "access_token", "refresh_token", "authorization", "webhook", "webhooks", "error", "run_start", "run_end",
               "changes", "version", "delete"}
# Keys that only count as secrets inside these sections (e.g. 'url' in a collection is a public list URL)
SECTION_ONLY = {"url": {"plex", "radarr", "sonarr", "tautulli", "notifiarr", "gotify", "ntfy", "seerr", "overseerr", "lidarr"},
                "error": {"webhooks"}, "run_start": {"webhooks"}, "run_end": {"webhooks"}, "changes": {"webhooks"},
                "version": {"webhooks"}, "delete": {"webhooks"}, "username": {"anidb", "mal", "myanimelist"},
                "webhook": {"webhooks"}}
LINE = re.compile(r"^(?P<lead>\s*#?\s*)(?P<key>[A-Za-z_]+):(?P<sp>[ \t]+)(?P<val>[^#\n]*?)(?P<tail>\s*(#.*)?)$")
SUSPICIOUS = [re.compile(p) for p in (r"\b(?:\d{1,3}\.){3}\d{1,3}\b", r"plex\.direct", r"X-Plex-Token=", r"\b[a-f0-9]{32}\b",
                                      r"\bgho_[A-Za-z0-9]+", r"\b[A-Za-z0-9_-]{20}\b(?=\s*$)")]


def sanitize_text(text):
    out, section = [], None
    for line in text.split("\n"):
        top = re.match(r"^#?([A-Za-z_]+):\s*(#.*)?$", line)  # commented-out sections count too
        if top:
            section = top.group(1)
        m = LINE.match(line)
        if m and section and not top:
            key, val = m.group("key"), m.group("val").strip()
            allowed = SECTION_ONLY.get(key)
            if key in SECRET_KEYS and val and not val.startswith("<<") and (allowed is None or section in allowed):
                tail = m.group("tail")
                if any(p.search(tail) for p in SUSPICIOUS) or "http" in tail:
                    tail = ""  # a trailing comment holding an old address or key goes too
                line = f"{m.group('lead')}{key}:{m.group('sp')}<<{section}_{key}>>{tail}"
        out.append(line)
    return "\n".join(out)


def leftovers(text):
    hits = []
    for n, line in enumerate(text.split("\n"), 1):
        if "mdblist.com/lists/" in line or "theposterdb.com" in line:
            continue
        for p in SUSPICIOUS:
            if p.search(line):
                hits.append((n, line.strip()))
                break
    return hits


def main(paths):
    bad = False
    for path in paths:
        text = open(path).read()
        clean = sanitize_text(text)
        open(path, "w").write(clean)
        for n, line in leftovers(clean):
            print(f"{path}:{n}: still looks secret: {line[:80]}", file=sys.stderr)
            bad = True
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
