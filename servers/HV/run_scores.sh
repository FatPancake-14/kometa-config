#!/bin/bash
# Nightly MDBList-average scores for HV, before Kometa (which waits on the same lock).
cd /home/ubuntu/kometa-scores
# UK broadcaster streaming charts (iPlayer, ITVX, Channel 4, NOW) into Kometa collection files; takes seconds.
/usr/bin/python3 /home/ubuntu/kometa-tools/uk_charts.py /home/ubuntu/PMM/HV/main/config.yml >> /home/ubuntu/kometa-tools/logs/uk_charts.log 2>&1
exec /usr/bin/flock -w 3600 /tmp/kometa-hv.lock /usr/bin/python3 sync_scores.py /home/ubuntu/PMM/HV/main/config.yml \
  "Movies 4K" "Movies 4Kmini" "TV Shows 4K" \
  "Movies" "TV Shows" "Movies International" "TV Shows International" \
  "Kids Movies" "Kids TV Shows" \
  "Anime Movies (Dual-Audio)" "Anime Movies (Subs)" "Anime TV (Dubs)" "Anime TV (Subs)" "$@"
