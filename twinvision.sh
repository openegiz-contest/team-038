#!/usr/bin/env bash
#
# TwinVision Copper Pit commands for hosts without `make`. Needs only bash and
# Docker Compose; each command does the same as the make target in brackets.
#
#   ./twinvision.sh up        platform + TwinVision (make twinvision)
#   ./twinvision.sh smoke     TwinVision end-to-end check (make twinvision-smoke)
#   ./twinvision.sh platform-smoke   the platform's own check (make smoke)
#   ./twinvision.sh test      unit tests in the image (make twinvision-test)
#   ./twinvision.sh twins     list twins from Ditto, check against SCHEMA.md (make twinvision-twins)
#   ./twinvision.sh urls      print URLs and logins (make twinvision-urls)
#   ./twinvision.sh ps | logs [service] | stop | down | clean
#
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

BASE=(docker compose -f deploy/compose/docker-compose.yml)
TV=(-f twinvision/compose.yml)
MINE=()
[ -f deploy/compose/.example-mine ] && MINE=(-f examples/mine/compose.yml)
ALL=("${BASE[@]}" ${MINE[@]+"${MINE[@]}"})
[ -f deploy/compose/.twinvision ] && ALL+=("${TV[@]}")

platform_up() {
  bash scripts/compose-env.sh
  bash scripts/compose-preflight.sh
  "${ALL[@]}" up -d --build --wait --wait-timeout 900
  # --wait does not wait for one-shot jobs: wait for the init job to exit, then check how.
  if [ "$(docker wait "$("${ALL[@]}" ps -a -q init)")" != 0 ]; then
    echo "ERROR: the init job (policy + Ditto connections) failed:"; "${ALL[@]}" logs init; exit 1
  fi
  bash scripts/compose-wait-ready.sh
  bash scripts/compose-urls.sh
}

case "${1:-up}" in
  up)
    platform_up
    "${BASE[@]}" ${MINE[@]+"${MINE[@]}"} "${TV[@]}" up -d --build --wait --wait-timeout 600
    touch deploy/compose/.twinvision
    bash twinvision/scripts/urls.sh ;;
  smoke) bash twinvision/scripts/smoke.sh ;;
  platform-smoke) bash scripts/compose-smoke.sh ;;
  test)
    "${BASE[@]}" "${TV[@]}" build twinvision-setup
    docker run --rm --user nobody -e PYTHONDONTWRITEBYTECODE=1 twinvision/app:local \
      python -m pytest -q -p no:cacheprovider /app/twinvision/tests ;;
  twins)
    "${BASE[@]}" "${TV[@]}" run --rm --no-deps twinvision-setup python -m twinvision.setup.check_twins ;;
  urls) bash twinvision/scripts/urls.sh ;;
  ps) "${ALL[@]}" ps -a ;;
  logs) "${ALL[@]}" logs -f --tail=100 "${@:2}" ;;
  stop) "${BASE[@]}" "${TV[@]}" stop twinvision-sim ;;
  down) "${ALL[@]}" down ;;
  start) platform_up ;;
  clean)
    "${ALL[@]}" down -v --remove-orphans
    rm -f deploy/compose/.env deploy/compose/.example-mine deploy/compose/.twinvision ;;
  *) sed -n '3,12p' "$0"; exit 2 ;;
esac
