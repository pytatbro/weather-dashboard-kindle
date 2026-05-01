#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate

LOG="/home/tuan/weather-dashboard/logs/cron.log"
URL="http://localhost:5000/generate"
MAX_RETRIES=3

for i in $(seq 1 $MAX_RETRIES); do
    RESULT=$(curl -s "$URL")
    echo "$(date '+[%d/%b/%Y:%H:%M:%S %z]') (attempt $i) $RESULT" >> "$LOG"
    echo "$RESULT" | grep -q '"status":"ok"' && break
    [ $i -lt $MAX_RETRIES ] && sleep 10
done