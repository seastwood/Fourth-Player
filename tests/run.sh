#!/bin/sh
# Run every suite. Each one is an ordinary script; none of them touches the
# network, a GPU or a real /dev/uinput, so this is safe on any machine.
set -e
cd "$(dirname "$0")"
failed=0
for suite in test_*.py; do
  echo
  echo "=== $suite ==="
  python3 "$suite" || failed=$((failed + 1))
done
echo
if [ "$failed" -eq 0 ]; then
  echo "all suites passed"
else
  echo "$failed suite(s) FAILED"
fi
exit "$failed"
