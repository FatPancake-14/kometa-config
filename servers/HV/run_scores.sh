#!/bin/bash
# Nightly MDBList-average scores for HV, before Kometa (which waits on the same lock).
cd /home/ubuntu/scores
exec /usr/bin/flock -w 3600 /tmp/kometa-hv.lock /usr/bin/python3 sync_scores.py /home/ubuntu/PMM/HV/main/config.yml \
  "Movies 4K" "Movies 4Kmini" "TV Shows 4K" \
  "Movies" "TV Shows" "Movies International" "TV Shows International" \
  "Kids Movies" "Kids TV Shows" \
  "Anime Movies (Dual-Audio)" "Anime Movies (Subs)" "Anime TV (Dubs)" "Anime TV (Subs)" "$@"
