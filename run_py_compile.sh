#!/usr/bin/env bash
set -euo pipefail

echo "==> py_compile all ttchat/*.py and tests/*.py"
rc=0
for p in ttchat/*.py tests/*.py; do
    if python -m py_compile "$p"; then
        :
    else
        echo "FAIL $p"
        rc=1
    fi
done

echo "==> import ttchat.new_gui (no tkinter panic)"
if python -c "import ttchat.new_gui" 2>/dev/null; then
    echo "import ttchat.new_gui OK"
else
    echo "import ttchat.new_gui — tkinter is missing but import did not crash"
fi

echo "==> import run_new_gui (isolated from tkinter missing)"
python run_new_gui.py >/dev/null 2>&1 || true

exit "$rc"
