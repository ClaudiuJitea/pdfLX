#!/bin/sh
# Run the test suite on an off-screen GTK Broadway display so no windows
# appear on the desktop. Extra arguments are passed to unittest.
# A few layout tests in test_gtk_review measure real monitor-sized windows
# and fail on Broadway with or without local changes.
set -e
root=$(cd "$(dirname "$0")/.." && pwd)
python=${PYTHON:-python3}
display=:$((40 + $$ % 50))
gtk4-broadwayd "$display" >/dev/null 2>&1 &
daemon=$!
trap 'kill $daemon 2>/dev/null' EXIT INT TERM
sleep 1
cd "$root"
GDK_BACKEND=broadway BROADWAY_DISPLAY=$display PYTHONPATH="$root:$root/tests" \
    "$python" -m unittest ${@:-discover -s tests -t tests -q}
