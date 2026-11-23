#!/usr/bin/env bash
set -euo pipefail

SRC="${1:-$HOME/sunicONEorTWO-wobpurt-testing}"
DST="${2:-$HOME/sunicONEorTWO-wobpurt}"

[ -d "$SRC/web" ] && [ -d "$DST/web" ] || { echo "both project folders must exist: $SRC and $DST"; exit 1; }

echo "Files that differ between the test project and the live project:"
changed=0
for item in build.sh tools web; do
  if [ -d "$SRC/$item" ]; then
    out=$(diff -rq "$SRC/$item" "$DST/$item" 2>/dev/null || true)
  else
    out=$(diff -q "$SRC/$item" "$DST/$item" 2>/dev/null || true)
  fi
  out=$(printf "%s\n" "$out" | grep -v "^Only in $DST" || true)
  if [ -n "$out" ]; then echo "$out"; changed=1; fi
done
if [ "$changed" = 0 ]; then echo "Nothing to copy."; exit 0; fi

printf "Copy these into the live project (nothing is deleted)? [y/N] "
read -r answer
[ "$answer" = "y" ] || { echo "Cancelled."; exit 0; }

cp "$SRC/build.sh" "$DST/build.sh"
cp -r "$SRC/tools/." "$DST/tools/"
cp -r "$SRC/web/." "$DST/web/"
echo "Copied. Now, in the live project folder: bash build.sh, test, then commit and push."
