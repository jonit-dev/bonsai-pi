#!/usr/bin/env bash
# Check that everything bonsai-pi needs is here, and print the exact command for anything that
# is not. Changes nothing; running it twice is the same as running it once.
set -uo pipefail

HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
MIN_NODE="22.19.0"
MODEL="${BONSAI_MODEL:-$HOME/projects/bonsai2-cuda/models/Ternary-Bonsai-2-27B-PTQ1_0.gguf}"
FORK="${FORK:-$HOME/projects/bonsai2-cuda/fork}"
CLI="$HERE/packages/coding-agent/dist/bundle/cli.js"

missing=0
ok()   { printf '  ok    %s\n' "$1"; }
bad()  { printf '  MISS  %s\n        fix: %s\n' "$1" "$2"; missing=$((missing + 1)); }

echo "bonsai-pi prerequisites"
echo

# node 22.19+ runs the bundle. The launcher can also find one under nvm, so a too-old node on
# PATH is worth reporting but is not fatal.
node_bin="$(command -v node || true)"
if [ -n "$node_bin" ] && [ "$(printf '%s\n%s\n' "$MIN_NODE" "$("$node_bin" -p 'process.versions.node' 2>/dev/null)" | sort -V | head -1)" = "$MIN_NODE" ]; then
  ok "node $("$node_bin" -p 'process.versions.node') ($node_bin)"
elif ls "$HOME"/.nvm/versions/node/*/bin/node >/dev/null 2>&1; then
  ok "node on PATH is $("$node_bin" -p 'process.versions.node' 2>/dev/null || echo absent), but the launcher will find a newer one under ~/.nvm"
else
  bad "node >= $MIN_NODE" "install one (nvm install 22), or set BONSAI_NODE=/path/to/node"
fi

if command -v python3 >/dev/null 2>&1; then
  ok "python3 ($(command -v python3))"
else
  bad "python3" "needed to read the loaded window from the server's /props"
fi

if command -v curl >/dev/null 2>&1; then
  ok "curl"
else
  bad "curl" "install curl; the launcher probes /health with it"
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  free="$(nvidia-smi --query-gpu=memory.total,memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 | awk -F', *' '{print $1-$2}')"
  ok "nvidia-smi (${free:-?} MiB free)"
else
  echo "  note  no nvidia-smi: the VRAM guard is skipped, which is fine on a remote host"
fi

echo
if [ -f "$CLI" ]; then
  ok "pi bundle ($CLI)"
else
  bad "pi bundle" "cd $HERE && npm ci && npm run build"
fi

if [ -x "$FORK/build/bin/llama-server" ]; then
  ok "llama-server ($FORK/build/bin/llama-server)"
else
  bad "PrismML llama-server" "stock llama.cpp and Ollama cannot read PTQ1_0; build the fork:
        git clone --depth 1 --branch prism-b10685-7dffb15 https://github.com/PrismML-Eng/llama.cpp $FORK
        cmake -B $FORK/build -G Ninja -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON -DGGML_NATIVE=OFF \\
          -DCMAKE_CUDA_ARCHITECTURES=75-real -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-15 \\
          -DCUDAToolkit_ROOT=/opt/cuda -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF
        cmake --build $FORK/build -j 16 --target llama-server"
fi

if [ -f "$MODEL" ]; then
  ok "model $MODEL ($(( $(stat -c %s "$MODEL") / 1048576 )) MiB)"
else
  bad "model" "download prism-ml/Ternary-Bonsai-2-27B-gguf (PTQ1_0) to $MODEL, or set BONSAI_MODEL"
fi

echo
if [ "$missing" -eq 0 ]; then
  echo "everything is here. start with:  bin/bonsai-pi"
else
  echo "$missing thing(s) missing; the fix command is printed above each one."
fi
exit "$missing"
