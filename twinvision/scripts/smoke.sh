#!/usr/bin/env bash
#
# End-to-end check of the TwinVision Copper Pit on a running stack:
# twins in Ditto, simulator telemetry through MQTT -> Ditto -> Telegraf ->
# InfluxDB for the live and the baseline copy, mass balance, dashboards.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/deploy/compose/docker-compose.yml" -f "$ROOT/twinvision/compose.yml")
# shellcheck disable=SC1091
set -a; . "$ROOT/deploy/compose/.env"; set +a

HOST=127.0.0.1
DITTO="http://$HOST:$DITTO_PORT"
GRAFANA="http://$HOST:$GRAFANA_PORT"
NS="org.twinvision.copper"
EXPECTED_TWINS=69
FAILED=0

pass() { printf '  \033[32mok\033[0m    %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAILED=1; }
retry() {   # retry NAME TRIES CMD...: pass once CMD succeeds, 3 s apart
  local name="$1" tries="$2"; shift 2
  for _ in $(seq 1 "$tries"); do
    if "$@" >/dev/null 2>&1; then pass "$name"; return; fi
    sleep 3
  done
  fail "$name"
}
dget() { curl -sf -u "ditto:$DITTO_PASSWORD" "$DITTO/api/2/$1"; }
feature() {  # feature THING FEATURE -> value
  dget "things/$NS:$1/features/$2/properties/value"
}
flux() {
  "${COMPOSE[@]}" exec -T influxdb influx query --org opentwins --token "$INFLUXDB_TOKEN" --raw "$1" 2>/dev/null
}

echo "Containers"
setup_exit=$("${COMPOSE[@]}" ps -a --format '{{.Service}} {{.ExitCode}}' | awk '$1=="twinvision-setup"{print $2}')
if [ "$setup_exit" = "0" ]; then pass "twinvision-setup created the twins"; else fail "twinvision-setup exit code: ${setup_exit:-not run}"; fi
retry "twinvision-sim healthy" 20 sh -c "${COMPOSE[*]} ps --format '{{.Service}} {{.Health}}' | grep -q '^twinvision-sim healthy'"

echo "Twins in Ditto"
count_twins() {
  local n
  n=$(dget "search/things/count?filter=like(thingId,%22$NS:*%22)")
  [ "$n" -ge "$EXPECTED_TWINS" ]
}
retry "$EXPECTED_TWINS twins (live + baseline) indexed" 20 count_twins
for t in mine pit crusher-01 stockpile-cos plant-feed truck-01 ex-01 road-s04 blocks-north dispatcher scenario-controller \
         mine-baseline truck-01-baseline crusher-01-baseline; do
  retry "twin $t exists" 5 dget "things/$NS:$t"
done
retry "truck-01 is a child of the pit (_parents)" 5 sh -c \
  "curl -sf -u ditto:$DITTO_PASSWORD '$DITTO/api/2/things/$NS:truck-01/attributes/_parents' | grep -q '$NS:pit'"

echo "Simulation"
advancing() {   # advancing THING: its sim_time_h grows
  local a b
  a=$(feature "$1" sim_time_h); sleep 5; b=$(feature "$1" sim_time_h)
  awk -v a="$a" -v b="$b" 'BEGIN{exit !(b > a)}'
}
retry "live mine twin advances (sim_time_h)" 5 advancing mine
retry "baseline mine twin advances" 5 advancing mine-baseline
for copy in mine mine-baseline; do
  r=$(feature "$copy" mass_balance_residual_t || echo nan)
  if awk -v r="$r" 'BEGIN{exit !(r < 0.001 && r > -0.001)}'; then pass "$copy mass balance residual $r t"; else fail "$copy mass balance residual: $r"; fi
done
moving() {
  local a b
  a=$(dget "things/$NS:truck-01/features/x/properties/value")$(dget "things/$NS:truck-02/features/x/properties/value")
  sleep 5
  b=$(dget "things/$NS:truck-01/features/x/properties/value")$(dget "things/$NS:truck-02/features/x/properties/value")
  [ "$a" != "$b" ]
}
retry "trucks move (x changes)" 6 moving

echo "Telemetry in InfluxDB"
has_points() {  # has_points THING: telemetry stored in the last 2 minutes
  flux "from(bucket:\"default\") |> range(start:-2m) |> filter(fn:(r) => r.thingId == \"$NS:$1\") |> count() |> limit(n:1)" \
    | grep -qE ',[1-9][0-9]*,'
}
for thing in mine mine-baseline truck-03 crusher-01; do
  retry "points for $thing in the last 2 min" 20 has_points "$thing"
done

echo "Grafana"
retry "TwinVision dashboard provisioned" 10 curl -sf -u "admin:$GRAFANA_ADMIN_PASSWORD" "$GRAFANA/api/dashboards/uid/twinvision-overview"

echo
if [ $FAILED = 0 ]; then echo "TwinVision smoke test passed."; else echo "TwinVision smoke test FAILED."; exit 1; fi
