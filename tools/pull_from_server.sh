#!/usr/bin/env bash
# Copy each server's live Kometa YAML files into servers/<name>/, replacing secrets with <<placeholders>>.
# Run this after changing anything on the server, then review `git diff` and commit.
#
#   KOMETA_HOST=user@host KOMETA_KEY=~/.ssh/key tools/pull_from_server.sh
#
# Only *.yml files are copied (config, collection and overlay files). Kometa's cache, logs, saved original
# posters, fonts and .env files never leave the server.
set -euo pipefail
HOST="${KOMETA_HOST:?set KOMETA_HOST, e.g. ubuntu@my-server}"
KEY="${KOMETA_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_ROOT="${KOMETA_REMOTE_ROOT:-PMM}"      # ~/PMM/<server>/main on the host
SERVERS="${KOMETA_SERVERS:-HV Ely}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"

for s in $SERVERS; do
  dest="$DIR/servers/$s"
  mkdir -p "$dest"
  rsync -a --delete --include='*.yml' --exclude='*' -e "ssh -i $KEY" "$HOST:$REMOTE_ROOT/$s/main/" "$dest/"
  python3 "$DIR/tools/sanitize.py" "$dest"/*.yml
done
echo "Pulled and sanitised: $SERVERS. Review with: git -C \"$DIR\" diff"
