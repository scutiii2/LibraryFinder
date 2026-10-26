from pathlib import Path

import os

import pytest


@pytest.fixture(autouse=True)
def isolated_service_env(monkeypatch):
    """Keep the real .env and service variables from leaking into, or out of, any test."""
    clean = {key: value for key, value in os.environ.items()
             if key not in ("MINICPM_URL", "LAYA_URL", "MINICPM_TOKEN", "LAYA_TOKEN")}
    monkeypatch.setattr(os, "environ", clean)


@pytest.fixture
def library_root(tmp_path: Path) -> Path:
    root = tmp_path / "libs"
    (root / "drivers").mkdir(parents=True)
    (root / "utils").mkdir()
    (root / "__init__.py").write_text("")
    (root / "drivers" / "__init__.py").write_text("")
    (root / "drivers" / "base.py").write_text("class BaseDriver:\n    pass\n")
    (root / "drivers" / "sample.py").write_text(
        "from selenium import webdriver\n"
        "from .base import BaseDriver\n\n"
        "class SampleDriver(BaseDriver):\n"
        "    def click(self, locator: str) -> None:\n"
        "        \"\"\"Click a locator.\"\"\"\n"
        "        self._click(locator)\n\n"
        "    def _click(self, locator: str) -> None:\n"
        "        self.driver.find_element(locator).click()\n"
    )
    (root / "utils" / "settings.py").write_text(
        "import os\n"
        "from dotenv import load_dotenv\n\n"
        "def settings():\n"
        "    return os.getenv('SETTING')\n"
    )
    (root / "broken.py").write_text("def nope(:\n")
    return root
