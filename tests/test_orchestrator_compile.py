import py_compile


def test_orchestrator_compiles():
    py_compile.compile("orchestrator_bot.py", doraise=True)
