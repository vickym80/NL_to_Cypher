#!/usr/bin/env bash
# Start the NL2Cypher POC FastAPI app on port 9060 using the project venv.
# Separate from start_backend.sh (port 9050 / graphrag_service) on purpose —
# see docs/implementation_nl2cypher.md.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV="$SCRIPT_DIR/.venv"
if [[ ! -f "$VENV/bin/python" ]]; then
  echo "ERROR: .venv not found. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements/base.txt"
  exit 1
fi

if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
  echo "ERROR: .env not found. Copy .env.example and fill in your API keys."
  exit 1
fi

if ! "$VENV/bin/python" -c "import neo4j_graphrag" 2>/dev/null; then
  echo "ERROR: neo4j-graphrag not installed. Run: .venv/bin/pip install neo4j-graphrag>=1.18.0"
  exit 1
fi

echo "Starting NL2Cypher POC on http://localhost:9060"
echo "  Docs: http://localhost:9060/docs"
echo ""

export PYTHONPATH="$(dirname "$SCRIPT_DIR")"
export KMP_DUPLICATE_LIB_OK=TRUE

"$VENV/bin/uvicorn" NL2Cypher.service:app \
  --host 0.0.0.0 \
  --port 9060 \
  --reload \
  --reload-dir "$SCRIPT_DIR" \
  --reload-dir "$SCRIPT_DIR/config" \
  --log-level info
