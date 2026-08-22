#!/usr/bin/env bash
# Brings up the nine-container stack, narrating every step.
#
# This is the first thing a student sees in a fresh Codespace, so it is written
# to be read rather than to be terse. The first run takes minutes -- it pulls six
# images and builds three services -- and a silent wait that long is
# indistinguishable from a hang. Every phase therefore prints the command it is
# about to run and keeps printing while it works.
#
# Two things invoke this: the folderOpen task in .vscode/tasks.json (visible, in
# the Terminal panel) and postStartCommand (guaranteed to run, but its output is
# surfaced nowhere in the UI). Whichever arrives first does the work; the other
# follows its log, so the student sees one coherent narration either way.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

LOG=/tmp/tt-stack-start.log
LOCK=/tmp/tt-stack-start.lock
# Overridable: a slow machine or a throttled network can legitimately need
# longer, and the test harness needs shorter.
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-600}

if [ -t 1 ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'
  RED=$'\033[31m'; CYAN=$'\033[36m'; OFF=$'\033[0m'
else
  BOLD=''; DIM=''; GREEN=''; YELLOW=''; RED=''; CYAN=''; OFF=''
fi

STEP=0
TOTAL_STEPS=4

fmt_dur() {
  local s=$1
  if [ "$s" -lt 60 ]; then printf '%ds' "$s"; else printf '%dm %02ds' "$((s / 60))" "$((s % 60))"; fi
}

banner() {
  printf '%s\n' "${CYAN}${BOLD}================================================================${OFF}"
  printf '%s\n' "${BOLD}  TripleTen - Autonomous Incident Defense${OFF}"
  printf '%s\n' "${BOLD}  Starting the 9-container stack${OFF}"
  printf '%s\n' "${CYAN}${BOLD}================================================================${OFF}"
  printf '%s\n' "  ${DIM}Full log: ${LOG}${OFF}"
  printf '\n'
}

# Printed only once we know there is real work to do. Promising a student 4-7
# minutes and then finishing in two seconds because the stack was already up
# teaches them to distrust the next estimate.
timing_note() {
  printf '\n%s\n' "  A first run pulls 6 images and builds 3 services."
  printf '%s\n' "  Budget ${BOLD}4-7 minutes${OFF}. Nothing is stuck - every step prints below."
}

# Announce the command before running it. These commands are the lesson as much
# as the stack is, and they are what a student will type by hand next time.
announce() {
  STEP=$((STEP + 1))
  printf '\n%s\n' "${BOLD}>> STEP ${STEP}/${TOTAL_STEPS}  $1${OFF}"
  printf '%s\n' "   ${DIM}\$ $2${OFF}"
}

ok()   { printf '%s\n' "   ${GREEN}OK${OFF}  $1 ${DIM}($(fmt_dur "$2"))${OFF}"; }
warn() { printf '%s\n' "   ${YELLOW}--${OFF}  $1"; }
fail() { printf '%s\n' "   ${RED}XX${OFF}  $1"; }

# `docker compose up --wait` waits for exactly this, but prints nothing while it
# does, which is the problem this script exists to solve. Polling ourselves costs
# a loop and buys a per-service progress table.
#
# A service with no healthcheck has no .State.Health, so fall back to its
# lifecycle status instead of letting the Go template error out.
health_lines() {
  local ids
  ids=$(docker compose ps -q 2>/dev/null)
  [ -z "$ids" ] && return 1
  # shellcheck disable=SC2086  # word splitting is how docker inspect takes ids
  docker inspect \
    --format '{{.Name}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
    $ids 2>/dev/null | sed 's|^/||'
}

wait_for_health() {
  local expected=$1 start=$SECONDS last_ready=-1 last_print=0
  local elapsed lines ready pending now
  while :; do
    elapsed=$((SECONDS - start))
    lines=$(health_lines) || lines=''
    ready=$(printf '%s\n' "$lines" | grep -cE '\|(healthy|running)$')
    pending=$(printf '%s\n' "$lines" | grep -vE '\|(healthy|running)$' | grep '|' | cut -d'|' -f1 | tr '\n' ' ')

    if [ "$ready" -ge "$expected" ]; then
      ok "all ${expected} containers healthy" "$elapsed"
      return 0
    fi

    # Print on every state change, plus a 15s heartbeat, so the display keeps
    # moving even during the long stretches where nothing flips.
    now=$SECONDS
    if [ "$ready" != "$last_ready" ] || [ $((now - last_print)) -ge 15 ]; then
      printf '%s\n' "   ${DIM}[$(fmt_dur "$elapsed")]${OFF}  healthy ${GREEN}${ready}${OFF}/${expected}  ${DIM}not ready yet: ${pending:-none}${OFF}"
      last_ready=$ready
      last_print=$now
    fi

    if [ "$elapsed" -ge "$HEALTH_TIMEOUT" ]; then
      fail "gave up after $(fmt_dur "$elapsed") with ${ready}/${expected} healthy"
      return 1
    fi
    sleep 3
  done
}

report_failure() {
  printf '\n%s\n' "${RED}${BOLD}The stack did not come up cleanly.${OFF}"
  printf '\n%s\n' "${BOLD}Container state:${OFF}"
  docker compose ps
  printf '\n%s\n' "${BOLD}Last 20 log lines from each container that is not healthy:${OFF}"
  health_lines | grep -vE '\|(healthy|running)$' | grep '|' | cut -d'|' -f1 | while read -r name; do
    [ -z "$name" ] && continue
    printf '\n%s\n' "${YELLOW}--- ${name} ---${OFF}"
    docker logs --tail 20 "$name" 2>&1 || true
  done
  printf '\n%s\n' "${BOLD}What to try next:${OFF}"
  printf '%s\n' "  docker compose logs <service>   ${DIM}# everything one container printed${OFF}"
  printf '%s\n' "  docker compose up -d            ${DIM}# retry; it resumes rather than starting over${OFF}"
  printf '%s\n' "  cat ${LOG}     ${DIM}# this whole run${OFF}"
}

print_urls() {
  printf '\n%s\n' "${GREEN}${BOLD}Stack is up.${OFF} ${BOLD}Open these from the Ports panel:${OFF}"
  printf '%s\n' "  ${BOLD}3000${OFF}   Incident War Room    ${DIM}(opens on its own)${OFF}"
  printf '%s\n' "  ${BOLD}3001${OFF}   Grafana dashboards   ${DIM}(anonymous viewer, no login)${OFF}"
  printf '%s\n' "  ${BOLD}8000${OFF}   Incident Agent API   ${DIM}(/docs for the OpenAPI page)${OFF}"
  printf '%s\n' "  ${BOLD}16686${OFF}  Jaeger traces"
  printf '%s\n' "  ${BOLD}9090${OFF}   Prometheus"
  printf '\n%s\n' "${DIM}Watch it live:  docker compose logs -f${OFF}"
  printf '%s\n'   "${DIM}Shut it down:   docker compose down${OFF}"
}

# ---------------------------------------------------------------------------
# Follower path: another copy holds the lock and is already doing the work.
# Sitting on the lock in silence would reproduce the very "looks stuck" problem
# this script fixes, and running the steps concurrently would have two
# `docker compose up` calls racing to create the project network -- which is how
# you end up with "network ..._tt-network is ambiguous (2 matches found)".
# So: stream the leader's log instead.
# ---------------------------------------------------------------------------
#
# flock is util-linux, so it is absent on macOS and Git Bash. Those hosts only
# ever run this by hand, one copy at a time, so skipping the lock there costs
# nothing -- whereas treating "flock not found" as "lock is held" would send a
# lone runner down the follower path to tail a log nobody is writing.
if command -v flock > /dev/null 2>&1; then
  exec 9>"$LOCK"
  if ! flock -n 9; then
    printf '%s\n' "${BOLD}The Codespace already started this on boot.${OFF} ${DIM}Attaching to it:${OFF}"
    printf '\n'
    tail -n +1 -f "$LOG" 2>/dev/null &
    tail_pid=$!
    flock 9        # blocks until the leader releases
    sleep 1        # let the tail flush the leader's closing lines
    kill "$tail_pid" 2>/dev/null
    wait "$tail_pid" 2>/dev/null
    exit 0
  fi
fi

# ---------------------------------------------------------------------------
# Leader path.
# ---------------------------------------------------------------------------
: > "$LOG"    # truncate, so a follower tailing from line 1 sees only this run
exec > >(tee -a "$LOG") 2>&1

banner

announce "Waiting for the Docker daemon" "docker info"
daemon_start=$SECONDS
daemon_ready=0
for _ in $(seq 1 60); do
  if docker info > /dev/null 2>&1; then daemon_ready=1; break; fi
  printf '%s' "   ."
  sleep 2
done
printf '\n'
if [ "$daemon_ready" != 1 ]; then
  # Under docker-in-docker, dockerd is started by the container entrypoint and
  # races postStartCommand, so this is a real outcome on a cold boot.
  fail "the Docker daemon did not come up within 120s"
  printf '%s\n' "   Once it does, retry with: ${BOLD}bash .devcontainer/start-stack.sh${OFF}"
  exit 1
fi
ok "daemon ready" "$((SECONDS - daemon_start))"

EXPECTED=$(docker compose config --services 2>/dev/null | grep -c .)
[ "${EXPECTED:-0}" -gt 0 ] || EXPECTED=9

# Fast path. The folderOpen task fires on every window reload, not just on a
# cold boot, and re-pulling six images to tell a student what is already running
# is a minute of noise for no information.
if [ "$(health_lines | grep -cE '\|(healthy|running)$')" -ge "$EXPECTED" ]; then
  printf '\n%s\n' "${GREEN}All ${EXPECTED} containers are already running.${OFF} ${DIM}Nothing to do.${OFF}"
  print_urls
  exit 0
fi

timing_note

announce "Pulling the stack's prebuilt images" "docker compose pull"
printf '%s\n' "   ${DIM}Postgres+pgvector, Redis, Grafana, Prometheus, Jaeger, LocalStack.${OFF}"
printf '%s\n' "   ${DIM}This is the slow part on a cold Codespace: roughly 1.5 GB.${OFF}"
pull_start=$SECONDS
if ! docker compose pull; then
  warn "pull reported an error; continuing, because cached images may still serve"
fi
ok "images ready" "$((SECONDS - pull_start))"

announce "Building this repo's own 3 services" "docker compose build"
printf '%s\n' "   ${DIM}Incident Agent API, remediation worker, War Room frontend.${OFF}"
build_start=$SECONDS
if ! docker compose build; then
  fail "build failed - the output above says why"
  exit 1
fi
ok "images built" "$((SECONDS - build_start))"

announce "Starting containers and waiting for health checks" "docker compose up -d"
if ! docker compose up -d; then
  report_failure
  exit 1
fi

printf '\n%s\n' "   ${DIM}Container logs follow while health checks settle. Chatter here is normal.${OFF}"
# Deliberately not piped through sed for an indent prefix: with stdout being a
# pipe rather than a TTY, sed block-buffers and the last few KB of log are still
# sitting in the buffer when this gets killed -- so the lines never appear at
# all. Compose already prefixes each line with its service name.
docker compose logs -f --tail 0 --no-color 2>/dev/null &
logs_pid=$!

health_result=0
wait_for_health "$EXPECTED" || health_result=1

kill "$logs_pid" 2>/dev/null
wait "$logs_pid" 2>/dev/null

if [ "$health_result" -eq 0 ]; then
  print_urls
  exit 0
fi

report_failure
exit 1
