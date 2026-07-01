#!/usr/bin/env bash
# Build tagged homework PDF locally (MacTeX + LuaLaTeX + tagged article).
set -euo pipefail

TEXBIN="/Library/TeX/texbin"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STEM="${1:-hw1fall25}"
WORKDIR="${2:-$SCRIPT_DIR/../905_homework}"
OUTDIR="${3:-$SCRIPT_DIR/../canvas_pdfs}"
TEX="${STEM}_hw.tex"
PDF="${STEM}_hw.pdf"

if [[ ! -x "$TEXBIN/lualatex" ]]; then
  echo "MacTeX not found. Install from https://www.tug.org/mactex/mactex-download.html"
  exit 1
fi

mkdir -p "$OUTDIR"
OUTDIR="$(cd "$OUTDIR" && pwd)"

export PATH="$TEXBIN:$PATH"
cd "$WORKDIR"

if [[ ! -f "$TEX" ]]; then
  echo "Missing $WORKDIR/$TEX — run convert_swp_hw first."
  exit 1
fi

echo "Compiling $TEX with LuaLaTeX..."
lualatex -interaction=nonstopmode -output-directory="$OUTDIR" "$TEX" >/dev/null || true
lualatex -interaction=nonstopmode -output-directory="$OUTDIR" "$TEX" >/dev/null || true

if [[ -f "$OUTDIR/$PDF" ]]; then
  echo "Done: $OUTDIR/$PDF"
else
  echo "Build failed. Try: tlmgr update --self --all"
  exit 1
fi
