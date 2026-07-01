#!/usr/bin/env bash
# Build tagged slide PDF locally (MacTeX + LuaLaTeX + ltx-talk).
set -euo pipefail

TEXBIN="/Library/TeX/texbin"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STEM="${1:-slides1fall25}"
WORKDIR="${2:-$SCRIPT_DIR/../905_materials}"
TEX="${STEM}_slides.tex"
PDF="${STEM}_slides.pdf"

if [[ ! -x "$TEXBIN/lualatex" ]]; then
  echo "MacTeX not found. Install from https://www.tug.org/mactex/mactex-download.html"
  exit 1
fi

export PATH="$TEXBIN:$PATH"
cd "$WORKDIR"

if [[ ! -f "$TEX" ]]; then
  echo "Missing $WORKDIR/$TEX — run convert_swp_slides first."
  exit 1
fi

echo "Compiling $TEX with LuaLaTeX..."
lualatex -interaction=nonstopmode "$TEX" >/dev/null || true
lualatex -interaction=nonstopmode "$TEX" >/dev/null || true

if [[ -f "$PDF" ]]; then
  echo "Done: $WORKDIR/$PDF"
else
  echo "Build failed. Try: tlmgr update --self --all"
  exit 1
fi
