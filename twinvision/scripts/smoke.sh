#!/usr/bin/env bash
#
# End-to-end check of the TwinVision Copper Pit on a running stack:
# twins in Ditto, simulator telemetry through MQTT -> Ditto -> Telegraf ->
# InfluxDB for the live and the baseline copy, mass balance, dashboards,
# mass, fuel and energy balances, the twin inventory against SCHEMA.md,
# the web UI, a what-if round trip (KPI delta against the baseline, undo,
# recovery), a decision-module switch round trip and a resync of the live
# copy to the baseline.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/deploy/compose/docker-compose.yml" -f "$ROOT/twinvision/compose.yml")
# shellcheck disable=SC1091
set -a; . "$ROOT/deploy/compose/.env"; set +a

HOST=127.0.0.1
DITTO="http://$HOST:$DITTO_PORT"
GRAFANA="http://$HOST:$GRAFANA_PORT"
WEB="http://$HOST:${TWINVISION_PORT:-8095}"
NS="org.twinvision.copper"
EXPECTED_TWINS=75
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
if "${COMPOSE[@]}" run --rm --no-deps twinvision-setup python -m twinvision.setup.check_twins >/tmp/twinvision-twins.txt 2>&1; then
  pass "twin types, counts, parents and relations match SCHEMA.md"
else
  fail "twin inventory differs from SCHEMA.md:"; grep FAIL /tmp/twinvision-twins.txt | head -10
fi

echo "Simulation"
advancing() {   # advancing THING: its sim_time_h grows
  local a b
  a=$(feature "$1" sim_time_h); sleep 5; b=$(feature "$1" sim_time_h)
  awk -v a="$a" -v b="$b" 'BEGIN{exit !(b > a)}'
}
retry "live mine twin advances (sim_time_h)" 5 advancing mine
retry "baseline mine twin advances" 5 advancing mine-baseline
near_zero() {  # near_zero COPY FEATURE UNIT LIMIT
  local r
  r=$(feature "$1" "$2" || echo nan)
  if awk -v r="$r" -v l="$4" 'BEGIN{exit !(r < l && r > -l)}'; then pass "$1 $2 = $r $3"; else fail "$1 $2: $r $3"; fi
}
for copy in mine mine-baseline; do
  near_zero "$copy" mass_balance_residual_t t 0.001
  near_zero "$copy" fuel_balance_residual_l L 0.01
  near_zero "$copy" energy_balance_residual_kwh kWh 0.01
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

echo "Web UI"
retry "twinvision-web healthy" 20 sh -c "${COMPOSE[*]} ps --format '{{.Service}} {{.Health}}' | grep -q '^twinvision-web healthy'"
retry "map page served at $WEB" 5 sh -c "curl -sf '$WEB/' | grep -q 'TwinVision'"
retry "map geometry from twin attributes (/api/static)" 5 sh -c "curl -sf '$WEB/api/static' | grep -q 'blocks-north'"
retry "live state from Ditto (/api/state)" 10 sh -c "curl -sf '$WEB/api/state' | grep -q '\"ok\":true'"
retry "trend from InfluxDB (/api/trend)" 10 sh -c "curl -sf '$WEB/api/trend?thing=mine&feature=plant_feed_tph&minutes=5' | grep -q '\"live\":\[\['"

echo "What-if round trip (crusher failure, then undo)"
curl -sf -X POST "$WEB/api/scenarios/reset" >/dev/null || fail "reset scenarios"
for m in dispatcher maintenance energy; do
  curl -sf -X POST -H 'Content-Type: application/json' -d '{"enabled":true}' "$WEB/api/modules/$m" >/dev/null || fail "module $m on"
done
rising() {   # rising THING FEATURE DIRECTION(1|-1): value moves that way within 15 s
  local a b
  a=$(feature "$1" "$2"); sleep 15; b=$(feature "$1" "$2")
  awk -v a="$a" -v b="$b" -v d="$3" 'BEGIN{exit !((b - a) * d > 0)}'
}
delta() {    # delta FEATURE: live - baseline of a mine KPI
  awk -v a="$(feature mine "$1")" -v b="$(feature mine-baseline "$1")" 'BEGIN{printf "%.1f", a - b}'
}
below_baseline() {   # below_baseline FEATURE MARGIN: live < baseline - MARGIN
  awk -v d="$(delta "$1")" -v m="$2" 'BEGIN{exit !(d < -m)}'
}
recovering() {   # recovering FEATURE: |live - baseline| shrinks within 15 s
  local a b
  a=$(delta "$1"); sleep 15; b=$(delta "$1")
  awk -v a="$a" -v b="$b" 'BEGIN{a = a < 0 ? -a : a; b = b < 0 ? -b : b; exit !(b < a || b < 50)}'
}
if curl -sf -X POST "$WEB/api/scenarios/crusher_failure" >/dev/null; then pass "scenario started through the web API"; else fail "POST /api/scenarios/crusher_failure"; fi
retry "live crusher failed" 15 sh -c "[ \"\$(curl -sf -u ditto:$DITTO_PASSWORD '$DITTO/api/2/things/$NS:crusher-01/features/state/properties/value')\" = '\"failed\"' ]"
retry "baseline crusher unaffected" 3 sh -c "[ \"\$(curl -sf -u ditto:$DITTO_PASSWORD '$DITTO/api/2/things/$NS:crusher-01-baseline/features/state/properties/value')\" != '\"failed\"' ]"
retry "live crusher throughput 0 t/h" 10 sh -c "[ \"\$(curl -sf -u ditto:$DITTO_PASSWORD '$DITTO/api/2/things/$NS:crusher-01/features/throughput_tph/properties/value')\" = 0.0 ] || [ \"\$(curl -sf -u ditto:$DITTO_PASSWORD '$DITTO/api/2/things/$NS:crusher-01/features/throughput_tph/properties/value')\" = 0 ]"
retry "live tph_crushed below baseline by > 500 t/h" 10 below_baseline tph_crushed 500
echo "        delta vs baseline after the failure: $(delta tph_crushed) t/h"
if curl -sf -X POST "$WEB/api/scenarios/undo" >/dev/null; then pass "undo through the web API"; else fail "POST /api/scenarios/undo"; fi
retry "live crusher back in service" 15 sh -c "[ \"\$(curl -sf -u ditto:$DITTO_PASSWORD '$DITTO/api/2/things/$NS:crusher-01/features/state/properties/value')\" != '\"failed\"' ]"
retry "live crushing KPI recovers (tph_crushed)" 4 rising mine tph_crushed 1
retry "gap to baseline closes (tph_crushed)" 8 recovering tph_crushed
echo "        delta vs baseline after undo: $(delta tph_crushed) t/h"

echo "Decision module switch (maintenance off, then undo)"
module_is() {  # module_is TWIN true|false
  [ "$(feature "$1" module_enabled)" = "$2" ]
}
if curl -sf -X POST -H 'Content-Type: application/json' -d '{"enabled":false}' "$WEB/api/modules/maintenance" >/dev/null; then
  pass "maintenance module switched off through the web API"; else fail "POST /api/modules/maintenance"; fi
retry "live maintenance planner reports module off" 10 module_is maintenance false
retry "baseline maintenance planner stays on" 3 module_is maintenance-baseline true
retry "live twin lists the module as off (mine modules_off)" 5 sh -c "curl -sf -u ditto:$DITTO_PASSWORD '$DITTO/api/2/things/$NS:mine/features/modules_off/properties/value' | grep -q maintenance"
if curl -sf -X POST "$WEB/api/scenarios/undo" >/dev/null; then pass "undo through the web API"; else fail "POST /api/scenarios/undo"; fi
retry "live maintenance planner back on" 10 module_is maintenance true

echo "Resync live to baseline"
same_totals() {
  [ "$(feature mine mined_total_t)" = "$(feature mine-baseline mined_total_t)" ]
}
if curl -sf -X POST "$WEB/api/resync" >/dev/null; then pass "resync requested through the web API"; else fail "POST /api/resync"; fi
retry "live and baseline report identical totals (mined_total_t)" 10 same_totals

echo
if [ $FAILED = 0 ]; then echo "TwinVision smoke test passed."; else echo "TwinVision smoke test FAILED."; exit 1; fi
