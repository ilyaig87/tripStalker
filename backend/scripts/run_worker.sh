#!/bin/bash
# Runs the TripStalker price-check worker once: checks every active tracked item
# and sends a Telegram alert on any >2% price move (drop or rise).
# Scheduled via crontab; logs to /tmp/tripstalker_worker.log.
cd /Users/ilya/PhpstormProjects/TripStalker/backend || exit 1
.venv/bin/python worker.py
