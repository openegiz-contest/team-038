#!/usr/bin/env bash
#
# Prints twinvision/presentation/deck.html to presentation.pdf in the repository root.
# Needs Google Chrome or Chromium (set CHROME to its path if it is not found).
# The screenshots in img/ are taken from the running stand.
#
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
CHROME="${CHROME:-}"
for c in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" google-chrome chromium chromium-browser; do
  [ -n "$CHROME" ] && break
  if command -v "$c" >/dev/null 2>&1 || [ -x "$c" ]; then CHROME="$c"; fi
done
[ -n "$CHROME" ] || { echo "Chrome or Chromium not found; set CHROME=/path/to/chrome" >&2; exit 1; }
"$CHROME" --headless=new --disable-gpu --no-pdf-header-footer --print-to-pdf="$ROOT/presentation.pdf" \
  "file://$HERE/deck.html" 2>/dev/null
echo "wrote $ROOT/presentation.pdf"
