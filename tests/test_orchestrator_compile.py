import py_compile
from pathlib import Path


def test_orchestrator_compiles():
    py_compile.compile(str(Path(__file__).parents[1] / "orchestrator_bot.py"), doraise=True)
