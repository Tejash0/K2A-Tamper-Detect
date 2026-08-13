#!/usr/bin/env bash
# Bring the whole K2A stack up or down with one command.
#
#   ./dev.sh up       start everything, deploy, wire the addresses, health-check
#   ./dev.sh down     stop everything
#   ./dev.sh status    what is listening, and is it healthy
#   ./dev.sh logs <chain|ai|backend|frontend>
#   ./dev.sh restart   down then up
#
# Code changes are picked up without a restart: the AI service runs uvicorn
# --reload, the backend runs node --watch, and Vite does HMR. Only a change to
# contracts/ needs `./dev.sh restart`, because the chain must be redeployed.

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
ROOT=$PWD
LOGS=$ROOT/.dev/logs
mkdir -p "$LOGS"

NODE_BIN=""
CHAIN_PORT=8545
AI_PORT=8000
BACKEND_PORT=5000
FRONTEND_PORT=5173

# Hardhat's deterministic Account #0. Local test chain only; never a real key.
DEFAULT_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80

c_ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
c_bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; }
c_info() { printf '  \033[2m%s\033[0m\n' "$1"; }
step()   { printf '\n\033[1m%s\033[0m\n' "$1"; }

port_pid() { lsof -ti:"$1" -sTCP:LISTEN 2>/dev/null | head -1; }

wait_for() { # wait_for <name> <url> <seconds>
  local name=$1 url=$2 secs=${3:-60} i
  for ((i = 0; i < secs * 2; i++)); do
    curl -sf -m 2 "$url" >/dev/null 2>&1 && { c_ok "$name ready"; return 0; }
    sleep 0.5
  done
  c_bad "$name did not come up in ${secs}s"
  return 1
}

venv_python() {
  if [[ -x $ROOT/ai-service/.venv/bin/python ]]; then
    echo "$ROOT/ai-service/.venv/bin/python"
  else
    return 1
  fi
}

cmd_down() {
  step "Stopping services"
  local any=0
  for p in $FRONTEND_PORT $BACKEND_PORT $AI_PORT $CHAIN_PORT; do
    local pid
    pid=$(port_pid "$p")
    if [[ -n $pid ]]; then
      kill "$pid" 2>/dev/null && c_ok "stopped :$p (pid $pid)" && any=1
    fi
  done
  sleep 2
  # Anything that ignored SIGTERM.
  for p in $FRONTEND_PORT $BACKEND_PORT $AI_PORT $CHAIN_PORT; do
    local pid
    pid=$(port_pid "$p")
    [[ -n $pid ]] && kill -9 "$pid" 2>/dev/null && c_info "force-killed :$p"
  done
  [[ $any -eq 0 ]] && c_info "nothing was running"
  return 0
}

cmd_up() {
  # --- preflight -----------------------------------------------------------
  step "Preflight"
  local PY
  if ! PY=$(venv_python); then
    c_bad "ai-service/.venv missing. Create it with:"
    c_info "python3 -m venv ai-service/.venv && ai-service/.venv/bin/pip install -r ai-service/requirements.txt"
    return 1
  fi
  c_ok "python venv"
  [[ -d node_modules ]]          || { c_bad "run: npm install";              return 1; }
  [[ -d backend/node_modules ]]  || { c_bad "run: cd backend && npm install"; return 1; }
  [[ -d frontend/node_modules ]] || { c_bad "run: cd frontend && npm install"; return 1; }
  c_ok "node modules ($(node -v))"

  # better-sqlite3 is a native addon, so its binary is tied to the Node ABI it
  # was built against. Under the wrong Node it fails to load, the backend dies
  # at startup, and Vite's proxy reports that as a 500 from the dashboard.
  #
  # better-sqlite3@11.10.0 predates Node 26 and CANNOT be compiled against it:
  # V8 removed Object::GetPrototype and Context::GetIsolate, so `npm rebuild`
  # fails at the C++ stage. Rebuilding is not a fallback here. The supported
  # runtime is Node 22 (see .nvmrc), whose prebuilt binary ships with the
  # package. So: find a Node that actually works and use it for the backend.
  #
  # NB: require()-ing the package is not enough to detect a bad ABI.
  # better-sqlite3 only dlopen()s its addon when a Database is constructed.
  local probe="const D=require('better-sqlite3'); new D(':memory:').close();"
  NODE_BIN=""
  local candidate
  for candidate in \
      "$(command -v node || true)" \
      "$HOME"/.nvm/versions/node/v22*/bin/node \
      /usr/bin/node; do
    [[ -x $candidate ]] || continue
    if (cd backend && "$candidate" -e "$probe") >/dev/null 2>&1; then
      NODE_BIN=$candidate
      break
    fi
  done
  if [[ -z $NODE_BIN ]]; then
    c_bad "no Node on this machine can load better-sqlite3"
    c_info "this project needs Node 22 (.nvmrc). Install it with:  nvm install 22"
    c_info "Node 26 cannot work: better-sqlite3 11.x does not compile against its V8"
    return 1
  fi
  if [[ $NODE_BIN != "$(command -v node)" ]]; then
    c_ok "better-sqlite3 loads under $("$NODE_BIN" -v) (default node $(node -v) cannot)"
    c_info "using $NODE_BIN for the backend"
  else
    c_ok "better-sqlite3 loads ($("$NODE_BIN" -v))"
  fi

  for p in $CHAIN_PORT $AI_PORT $BACKEND_PORT $FRONTEND_PORT; do
    if [[ -n $(port_pid "$p") ]]; then
      c_bad "port $p already in use. Run ./dev.sh down first."
      return 1
    fi
  done
  c_ok "ports free"

  if [[ ! -f .env ]] || ! grep -q '^DEPLOYER_PRIVATE_KEY=0x' .env 2>/dev/null; then
    echo "DEPLOYER_PRIVATE_KEY=$DEFAULT_KEY" >> .env
    c_info "wrote local test DEPLOYER_PRIVATE_KEY to .env"
  fi

  # --- chain ---------------------------------------------------------------
  step "Chain (:$CHAIN_PORT)"
  npx hardhat node >"$LOGS/chain.log" 2>&1 &
  for ((i = 0; i < 90; i++)); do
    curl -s -m 2 -X POST -H 'Content-Type: application/json' \
      --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
      "http://127.0.0.1:$CHAIN_PORT" 2>/dev/null | grep -q result && break
    sleep 0.5
  done
  [[ -n $(port_pid $CHAIN_PORT) ]] || { c_bad "chain failed, see $LOGS/chain.log"; return 1; }
  c_ok "hardhat node ready"

  # --- deploy --------------------------------------------------------------
  # --reset is required: Ignition caches deployment state, but `hardhat node`
  # starts a brand new chain each time, so without it the old address is reused
  # and points at nothing.
  step "Deploy"
  if ! npx hardhat ignition deploy ./ignition/modules/EvidenceLog.ts \
      --network localhost --reset >"$LOGS/deploy.log" 2>&1; then
    c_bad "deploy failed, see $LOGS/deploy.log"
    return 1
  fi
  local ADDR
  ADDR=$(jq -r '."EvidenceLogModule#EvidenceLog"' \
    ignition/deployments/chain-31337/deployed_addresses.json 2>/dev/null)
  [[ -n $ADDR && $ADDR != null ]] || { c_bad "could not read deployed address"; return 1; }
  c_ok "EvidenceLog at $ADDR"

  # --- backend env ---------------------------------------------------------
  # PRIVATE_KEY must be the deploying account: logEvidence() is onlyOwner.
  local KEY
  KEY=$(grep '^DEPLOYER_PRIVATE_KEY=' .env | tail -1 | cut -d= -f2)
  mkdir -p backend/database
  cat >backend/.env <<EOF
PORT=$BACKEND_PORT
DATABASE_PATH=./database/evidence.db
HARDHAT_NETWORK_URL=http://127.0.0.1:$CHAIN_PORT
CONTRACT_ADDRESS=$ADDR
PRIVATE_KEY=$KEY
CORS_ORIGIN=http://localhost:$FRONTEND_PORT
EOF
  c_ok "backend/.env synced to the new deployment"

  # The chain is brand new, so any cached evidence refers to records that no
  # longer exist on it. Leaving the cache in place is what produces confusing
  # "cache-only" verifications of evidence the chain has never seen.
  if [[ -f backend/database/evidence.db ]]; then
    rm -f backend/database/evidence.db backend/database/evidence.db-shm backend/database/evidence.db-wal
    c_info "cleared stale SQLite cache (chain was reset)"
  fi

  # --- services ------------------------------------------------------------
  step "Services"
  (cd ai-service && "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port $AI_PORT --reload \
    >"$LOGS/ai.log" 2>&1 &)
  (cd backend && "$NODE_BIN" --watch server.js >"$LOGS/backend.log" 2>&1 &)
  (cd frontend && npm run dev >"$LOGS/frontend.log" 2>&1 &)

  wait_for "AI service"  "http://localhost:$AI_PORT/"            120
  wait_for "backend"     "http://localhost:$BACKEND_PORT/api/health" 60
  wait_for "frontend"    "http://localhost:$FRONTEND_PORT/"      60

  cmd_status
}

cmd_status() {
  step "Status"
  local rows=(
    "chain:$CHAIN_PORT"
    "ai:$AI_PORT"
    "backend:$BACKEND_PORT"
    "frontend:$FRONTEND_PORT"
  )
  for r in "${rows[@]}"; do
    local name=${r%%:*} port=${r##*:} pid
    pid=$(port_pid "$port")
    if [[ -n $pid ]]; then c_ok "$(printf '%-9s' "$name") :$port  pid $pid"
    else                   c_bad "$(printf '%-9s' "$name") :$port  not running"; fi
  done

  local health
  health=$(curl -s -m 3 "http://localhost:$BACKEND_PORT/api/health" 2>/dev/null)
  if [[ -n $health ]]; then
    printf '\n'
    c_info "health: $health"
  fi
  printf '\n  Open \033[4mhttp://localhost:%s\033[0m\n' "$FRONTEND_PORT"
  c_info "logs: ./dev.sh logs <chain|ai|backend|frontend>"
}

cmd_logs() {
  local svc=${1:-}
  [[ -z $svc ]] && { echo "usage: ./dev.sh logs <chain|ai|backend|frontend>"; return 1; }
  local f="$LOGS/$svc.log"
  [[ -f $f ]] || { echo "no log at $f"; return 1; }
  tail -f "$f"
}

case "${1:-up}" in
  up)      cmd_up ;;
  down)    cmd_down ;;
  status)  cmd_status ;;
  logs)    cmd_logs "${2:-}" ;;
  restart) cmd_down; sleep 1; cmd_up ;;
  *)       sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
esac
