#!/bin/bash
# Wrapper so cron doesn't choke on the % characters in the HF URL.
# Samples the tracked offer's price once and appends it to price_volatility.csv.
# Scheduled via crontab (see install line in price_volatility.py header).
cd /Users/ilya/PhpstormProjects/TripStalker/backend || exit 1

URL='https://www.holidayfinder.co.il/offer/6606726?bc=m4d32h6606726c21o150926i200926st1:withCib:AI&adult=2&child=%5B2%5D&airports%5B%5D=TLV&position=0'

.venv/bin/python scripts/price_volatility.py "$URL" --notify
