from pathlib import Path


def test_run_bat_starts_only_the_web_ui_without_arguments_or_command_branches():
    script = (Path(__file__).parents[1] / "run.bat").read_text(encoding="utf-8")

    assert 'set "VENV=%~dp0.venv"' in script
    assert '%PYTHON% -m venv "%VENV%"' in script
    assert '"%VENV%\\Scripts\\python.exe" -m pip install -e ".[dev,laya,web]"' in script
    assert '"%VENV%\\Scripts\\python.exe" -m web.app' in script
    assert "%~1" not in script
    assert "library-finder.exe" not in script
    assert ":directory" not in script
    assert "library-source.txt" not in script
