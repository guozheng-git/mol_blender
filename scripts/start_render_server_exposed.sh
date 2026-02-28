#!/usr/bin/env bash
set -euo pipefail

# Starts the Blender render server and optionally exposes it through
# an SSH reverse tunnel.
#
# Default server command:
#   pixi run python -m blender_render.render_server \
#     --host 0.0.0.0 \
#     --port 8765 \
#     --blender-exe /home/magnusb/Downloads/blender-5.0.1-linux-x64/blender
#
# Optional tunnel example:
#   ./scripts/start_render_server_exposed.sh \
#     --relay-target user@public-host \
#     --relay-port 8765
#
# Then clients connect to: http://public-host:8765

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8765}"
BLENDER_EXE="${BLENDER_EXE:-/home/magnusb/Downloads/blender-5.0.1-linux-x64/blender}"
SERVER_ROOT="${SERVER_ROOT:-}"
VERBOSE="${VERBOSE:-1}"

RELAY_TARGET="${RELAY_TARGET:-}"
RELAY_PORT="${RELAY_PORT:-}"
START_TUNNEL="${START_TUNNEL:-auto}" # auto|yes|no
CLIENT_HOST_OVERRIDE="${CLIENT_HOST_OVERRIDE:-}"

SERVER_PID=""
TUNNEL_PID=""

usage() {
  cat <<'EOF'
Usage: start_render_server_exposed.sh [options]

Server options:
  --host <ip>               Server bind host (default: 0.0.0.0)
  --port <port>             Server bind port (default: 8765)
  --blender-exe <path>      Blender binary path
  --server-root <path>      Optional server runtime root
  --verbose <0|1>           Server verbose mode (default: 1)

Exposure options:
  --relay-target <user@host>  Enable reverse SSH tunnel target host
  --relay-port <port>         Remote exposed port (default: same as --port)
  --start-tunnel <auto|yes|no>
      auto: start tunnel only if --relay-target is set
      yes : require and start tunnel
      no  : do not start tunnel

Client convenience:
  --client-host <host>        Override auto-detected client host in printed command

Examples:
  ./scripts/start_render_server_exposed.sh
  ./scripts/start_render_server_exposed.sh --port 8766
  ./scripts/start_render_server_exposed.sh --relay-target user@10.0.0.5 --relay-port 8766
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      HOST="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --blender-exe)
      BLENDER_EXE="$2"
      shift 2
      ;;
    --server-root)
      SERVER_ROOT="$2"
      shift 2
      ;;
    --verbose)
      VERBOSE="$2"
      shift 2
      ;;
    --relay-target)
      RELAY_TARGET="$2"
      shift 2
      ;;
    --relay-port)
      RELAY_PORT="$2"
      shift 2
      ;;
    --start-tunnel)
      START_TUNNEL="$2"
      shift 2
      ;;
    --client-host)
      CLIENT_HOST_OVERRIDE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "${RELAY_PORT}" ]]; then
  RELAY_PORT="${PORT}"
fi

if [[ ! -x "${BLENDER_EXE}" ]]; then
  echo "Blender executable not found or not executable: ${BLENDER_EXE}" >&2
  exit 1
fi

if ! command -v pixi >/dev/null 2>&1; then
  echo "pixi command not found in PATH." >&2
  exit 1
fi

if [[ "${START_TUNNEL}" == "yes" || "${START_TUNNEL}" == "auto" ]]; then
  if [[ -n "${RELAY_TARGET}" ]] && ! command -v ssh >/dev/null 2>&1; then
    echo "ssh command not found in PATH." >&2
    exit 1
  fi
fi

cleanup() {
  set +e
  if [[ -n "${TUNNEL_PID}" ]] && kill -0 "${TUNNEL_PID}" >/dev/null 2>&1; then
    kill "${TUNNEL_PID}" >/dev/null 2>&1 || true
    wait "${TUNNEL_PID}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${SERVER_PID}" ]] && kill -0 "${SERVER_PID}" >/dev/null 2>&1; then
    kill "${SERVER_PID}" >/dev/null 2>&1 || true
    wait "${SERVER_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

detect_primary_ipv4() {
  if command -v ip >/dev/null 2>&1; then
    ip -o -4 route get 1.1.1.1 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i == "src") {print $(i+1); exit}}'
    return 0
  fi
  if command -v hostname >/dev/null 2>&1; then
    hostname -I 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i != "127.0.0.1") {print $i; exit}}'
    return 0
  fi
  return 1
}

server_cmd=(
  pixi run python -m blender_render.render_server
  --host "${HOST}"
  --port "${PORT}"
  --blender-exe "${BLENDER_EXE}"
  --verbose "${VERBOSE}"
)
if [[ -n "${SERVER_ROOT}" ]]; then
  server_cmd+=(--server-root "${SERVER_ROOT}")
fi

echo "Starting render server..."
echo "Command: ${server_cmd[*]}"
"${server_cmd[@]}" &
SERVER_PID=$!

if command -v curl >/dev/null 2>&1; then
  for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi

echo "Server PID: ${SERVER_PID}"
echo "Server endpoint: http://${HOST}:${PORT}"

want_tunnel="no"
case "${START_TUNNEL}" in
  no)
    want_tunnel="no"
    ;;
  yes)
    want_tunnel="yes"
    ;;
  auto)
    if [[ -n "${RELAY_TARGET}" ]]; then
      want_tunnel="yes"
    fi
    ;;
  *)
    echo "Invalid --start-tunnel value: ${START_TUNNEL}" >&2
    exit 1
    ;;
esac

if [[ "${want_tunnel}" == "yes" ]]; then
  if [[ -z "${RELAY_TARGET}" ]]; then
    echo "Tunnel requested but --relay-target is empty." >&2
    exit 1
  fi
  echo "Starting reverse SSH tunnel..."
  tunnel_cmd=(
    ssh -N
    -o ExitOnForwardFailure=yes
    -o ServerAliveInterval=30
    -o ServerAliveCountMax=3
    -o ControlMaster=no
    -o ControlPath=none
    -R "0.0.0.0:${RELAY_PORT}:127.0.0.1:${PORT}"
    "${RELAY_TARGET}"
  )
  echo "Command: ${tunnel_cmd[*]}"
  "${tunnel_cmd[@]}" &
  TUNNEL_PID=$!
  sleep 1
  if ! kill -0 "${TUNNEL_PID}" >/dev/null 2>&1; then
    echo "Tunnel process exited immediately." >&2
    exit 1
  fi
  echo "Tunnel PID: ${TUNNEL_PID}"
  echo "Exposed endpoint: http://${RELAY_TARGET##*@}:${RELAY_PORT}"
fi

CLIENT_HOST=""
CLIENT_PORT="${PORT}"

if [[ -n "${CLIENT_HOST_OVERRIDE}" ]]; then
  CLIENT_HOST="${CLIENT_HOST_OVERRIDE}"
elif [[ "${want_tunnel}" == "yes" ]]; then
  CLIENT_HOST="${RELAY_TARGET##*@}"
  CLIENT_PORT="${RELAY_PORT}"
elif [[ "${HOST}" == "0.0.0.0" || "${HOST}" == "::" ]]; then
  CLIENT_HOST="$(detect_primary_ipv4 || true)"
  if [[ -z "${CLIENT_HOST}" ]]; then
    CLIENT_HOST="$(hostname -f 2>/dev/null || hostname)"
  fi
else
  CLIENT_HOST="${HOST}"
fi

echo "Client connect endpoint: http://${CLIENT_HOST}:${CLIENT_PORT}"
echo "PyMOL command:"
echo "blender_render selection='(all)', preset='preset_1_interface', mode='remote', server_host='${CLIENT_HOST}', server_port=${CLIENT_PORT}"

echo "Press Ctrl+C to stop."
if [[ -n "${TUNNEL_PID}" ]]; then
  wait -n "${SERVER_PID}" "${TUNNEL_PID}"
else
  wait "${SERVER_PID}"
fi
