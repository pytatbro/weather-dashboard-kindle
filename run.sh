#!/bin/bash
cd "$(dirname "$0")"

LOG_DIR="logs"
LOG_FILE="$LOG_DIR/server.log"
CRON_JOB="*/30 * * * * curl -s http://localhost:5000/generate >> /home/tuan/weather-dashboard/logs/cron.log 2>&1"

mkdir -p "$LOG_DIR"

# Cleanup on exit (Ctrl+C or kill)
cleanup() {
    echo ""
    echo "Shutting down..."

    # Kill Gunicorn
    if [ -n "$GUNICORN_PID" ]; then
        kill "$GUNICORN_PID" 2>/dev/null
        echo "Gunicorn stopped."
    fi

    # Remove cron job
    crontab -l 2>/dev/null | grep -v "$CRON_JOB" | crontab -
    echo "Cron job removed."

    exit 0
}
trap cleanup INT TERM

# Add cron job if not already present
if ! crontab -l 2>/dev/null | grep -q "localhost:5000/generate"; then
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    echo "Cron job added."
else
    echo "Cron job already present."
fi

# Activate venv
source venv/bin/activate

# Start Gunicorn (1 worker, logs to terminal + file)
echo "Starting server at http://0.0.0.0:5000"
gunicorn -w 1 -b 0.0.0.0:5000 \
    --access-logfile "$LOG_FILE" \
    --error-logfile "$LOG_FILE" \
    --capture-output \
    --log-level info \
    app:app 2>&1 | tee -a "$LOG_FILE" &
GUNICORN_PID=$!

echo "Server running (PID $GUNICORN_PID). Press Ctrl+C to stop."
echo ""

# Wait forever until Ctrl+C
wait $GUNICORN_PID
