import ast
import inspect
from pathlib import Path

from ri import cli, db


def test_cli_main_never_shadows_module_level_imports():
    """Regression: a function-local `import os` made `os` unbound earlier in main() and crashed `serve` on Render."""
    mod = ast.parse(Path(cli.__file__).read_text())
    top = {a.asname or a.name.split(".")[0] for n in mod.body if isinstance(n, ast.Import) for a in n.names}
    main = next(n for n in mod.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    local = {a.asname or a.name.split(".")[0] for n in ast.walk(main) if isinstance(n, ast.Import) for a in n.names}
    assert not (top & local), f"main() re-imports module-level names: {top & local}"


def test_pooler_safe_connections_disable_prepared_statements():
    src = inspect.getsource(db)
    assert src.count("prepare_threshold") >= 2          # direct connections AND the pool (Supabase poolers share server connections)


def test_serve_port_comes_from_environment(monkeypatch):
    assert 'os.environ.get("PORT"' in Path(cli.__file__).read_text()
