#!/usr/bin/env bash
# One command to get HoloGuide ready for recording the demo video.
#
#   bash scripts/run_video_demo.sh           # check everything, warm up Gemma, open the dashboard
#   bash scripts/run_video_demo.sh --check   # only run the checks and warm-up, don't open the dashboard
#
# Works offline. It never downloads or pulls a model.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODEL="gemma3:4b"
ENDPOINT="http://localhost:11434"
PY="$ROOT/.venv/bin/python"
CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

ok()   { printf '  \033[32m✔\033[0m %s\n' "$1"; }
fail() { printf '  \033[31m✘ %s\033[0m\n' "$1"; exit 1; }

echo "HoloGuide video demo: getting ready"

# 1. Python environment
[[ -x "$PY" ]] || fail "No .venv found. Run: python3.10 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt"
ok "Python environment found"

# 2. Ollama running (start it only if it isn't already)
if ! curl -sf "$ENDPOINT/api/tags" >/dev/null; then
  command -v ollama >/dev/null || fail "Ollama is not installed."
  echo "  … starting Ollama"
  nohup ollama serve >/tmp/hologuide-ollama.log 2>&1 &
  for _ in $(seq 1 20); do curl -sf "$ENDPOINT/api/tags" >/dev/null && break; sleep 0.5; done
  curl -sf "$ENDPOINT/api/tags" >/dev/null || fail "Ollama did not start. See /tmp/hologuide-ollama.log"
fi
ok "Ollama is running at $ENDPOINT"

# 3. Model already on disk (no download)
curl -sf "$ENDPOINT/api/tags" | grep -q "\"$MODEL\"" \
  || fail "$MODEL is not installed. While online, run: ollama pull $MODEL"
ok "$MODEL is installed"

# 4. Warm up: load Gemma onto the GPU and keep it loaded for 60 min,
#    so the first Inspect click in the video isn't slow.
echo "  … loading $MODEL onto the GPU (first time takes ~10 s)"
curl -sf "$ENDPOINT/api/generate" \
  -d "{\"model\":\"$MODEL\",\"prompt\":\"ready\",\"stream\":false,\"keep_alive\":\"60m\",\"options\":{\"num_predict\":1}}" \
  >/dev/null || fail "Warm-up request to Gemma failed."
ok "Gemma is loaded and warm"
ollama ps 2>/dev/null | sed 's/^/     /' || true

if [[ $CHECK_ONLY -eq 1 ]]; then
  echo; echo "All checks passed. Run without --check to open the dashboard."; exit 0
fi

# 5. Recording cheat sheet (full script: claude-responses/07-demo-script.md)
cat <<'EOF'

──────────────────────────── WHAT TO RECORD ────────────────────────────
 Before you start: turn Wi-Fi OFF. In the browser set Operation = Real,
 then click "Start selected mode / reset session". The banner must be BLUE.
 Use "Start selected mode / reset session" to reset between scenes.

 1 Offline      Show Wi-Fi off + blue banner + "model: gemma3:4b, localhost"
 2 Normal build For base_plate, gasket, cover, bolts:
                Part -> Place part -> Refresh camera -> Inspect   (4 passes)
 3 Rule guard   Reset. Place "cover" first -> red error.
                Place "wrong_cover" -> red error. (AI never asked)
 4 AI catches   Reset. Scenario launcher: missing_gasket -> Load scenario
                -> click "Play next scenario step" until completed
                (step 2 is BLOCKED: no orange gasket in the picture)
 5 Override     Reset. Place base_plate -> Inspect (pass).
                Type Operator (op-07) + Reason (gasket torn, removed).
                Place gasket -> Remove part (gasket) -> Inspect
                -> BLOCKED "Missing required part: Gasket".
                Change Reason (gasket pre-installed on cover, checked by hand),
                tick "Approve current step" -> Submit override.
                Scroll to Session audit to show the logged override.

 DON'T record the "Run scripted mock demo" button or a yellow Mock banner.
 Stop the dashboard with Ctrl+C when you're done.
─────────────────────────────────────────────────────────────────────────

EOF

# 6. Real backend: PyBullet simulator + Gemma-first inspector (Gemma decides; OpenCV can only send a disagreement to review)
export HOLOGUIDE_BACKEND_FACTORY=src.simulation.backend:create_session
export HOLOGUIDE_INSPECTOR_FACTORY=src.vision.factories:gemma_first
export HOLOGUIDE_MODEL_NAME="$MODEL"
export HOLOGUIDE_MODEL_ENDPOINT="$ENDPOINT"
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false   # no telemetry, keeps it offline

exec "$PY" -m streamlit run src/dashboard/app.py
