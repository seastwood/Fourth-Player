#!/bin/sh
# Run every suite. Each one is an ordinary script; none of them touches the
# network, a GPU or a real /dev/uinput, so this is safe on any machine.
set -e
cd "$(dirname "$0")"
failed=0
skipped=0
skiplist=""
for suite in test_*.py; do
  echo
  echo "=== $suite ==="
  out=$(python3 "$suite" 2>&1) || failed=$((failed + 1))
  echo "$out"
  # A suite that skips exits 0, so it counts as a pass and disappears into a
  # wall of green. Four browser suites rotted that way: they had stopped
  # running at all on every machine here, because none of them has node, and
  # "all suites passed" was true and useless. Skips are counted and named.
  case "$out" in
    *SKIPPED*) skipped=$((skipped + 1)); skiplist="$skiplist $suite" ;;
  esac
done
echo
if [ "$skipped" -ne 0 ]; then
  echo "$skipped suite(s) SKIPPED -- not run here, so not proven here:"
  for suite in $skiplist; do echo "    $suite"; done
  echo "  (node runs most browser halves; a headless Chrome runs the four"
  echo "   that load the real page; python3-evdev and python3-websockets run"
  echo "   the host ones. Install what is missing to actually test them.)"
fi
if [ "$failed" -eq 0 ]; then
  echo "all suites that ran passed"
else
  echo "$failed suite(s) FAILED"
fi
exit "$failed"
