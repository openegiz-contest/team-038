#!/usr/bin/env bash
# Print where the TwinVision Copper Pit can be seen and how to log in.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
set -a; . "$ROOT/deploy/compose/.env"; set +a
H="${OPENEGIZ_PUBLIC_HOST:-localhost}"
cat <<TXT

TwinVision Copper Pit is running.

  Live mine map       http://$H:${TWINVISION_PORT:-8095}/          (what-if buttons, live vs baseline KPIs)
  Control API         http://$H:${TWINVISION_PORT:-8095}/docs
  Grafana dashboards  http://$H:$GRAFANA_PORT/d/twinvision-overview   admin / $GRAFANA_ADMIN_PASSWORD
                      (Dashboards -> TwinVision)
  Ditto twins         http://$H:$DITTO_PORT/api/2/search/things?filter=like(thingId,%22org.twinvision.copper:*%22)
                      ditto / $DITTO_PASSWORD
  Twin hierarchy      Grafana -> OpenEgiz app -> Twins

Credentials live in deploy/compose/.env. Check everything with: ./twinvision.sh smoke   (or: make twinvision-smoke)
TXT
