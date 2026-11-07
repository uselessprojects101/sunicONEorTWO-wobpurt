#!/usr/bin/env bash
# Builds the web version into ./dist
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v emcc >/dev/null 2>&1; then
  [ -f "$HOME/emsdk/emsdk_env.sh" ] && source "$HOME/emsdk/emsdk_env.sh" >/dev/null 2>&1 || true
fi
command -v emcc >/dev/null 2>&1 || { echo "emcc not found. Run: source ~/emsdk/emsdk_env.sh"; exit 1; }
[ -f engine/CMakeLists.txt ] || { echo "engine/ folder is missing"; exit 1; }

python3 tools/setup_web.py

emcmake cmake -S engine -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DRETRO_OUTPUT_NAME=index \
  -DRETRO_MOD_LOADER=OFF \
  -DRETRO_NETWORKING=ON \
  -DRETRO_USE_HW_RENDER=ON \
  -DRETRO_DISABLE_PLUS=ON
cmake --build build -j"$(nproc)"

rm -rf dist && mkdir -p dist
cp build/index.html build/index.js build/index.wasm dist/
touch dist/.nojekyll

# GitHub Pages serves the docs/ folder
rm -rf docs && cp -r dist docs

echo
echo "Build finished. To test:"
echo "  cd dist && python3 -m http.server 8000"
echo "then open http://localhost:8000 in your browser."
