run_pytest.sh
#!/usr/bin/env bash
set -euo pipefail

echo "==> pytest (unittest, without tkinter)"
python -m pytest tests -q 2>&1 | tail -n 40 || true

echo "==> check static structure of ttchat.new_gui"
python -c "
import ast, pathlib
p = pathlib.Path('ttchat/new_gui.py')
tree = ast.parse(p.read_text(encoding='utf-8'))
classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
need = {'App', 'StatusChip', 'Checkbox', 'RoundedCanvas', 'run_gui', '_enable_dpi_awareness'}
missing = need - classes - funcs
if missing:
    print('Missing symbols in new_gui:', sorted(missing))
    raise SystemExit(1)
print('static structure OK:', sorted(need & (classes | funcs)))
"
