"""
Shared pytest setup (pytest loads this file automatically).

Nothing here talks to Google or OpenAI:
- FT_SKIP_SHEETS_INIT=1 stops the app from logging into Google at import time
- the sheets service is a REAL SheetsService wired to an in-memory FakeSheetsAPI
- the OpenAI client is a FakeOpenAI
Run with:  python -m pytest
"""

import os
import sys

# Must be set BEFORE the app (and its routes) are imported
os.environ["FT_SKIP_SHEETS_INIT"] = "1"
os.environ.setdefault("OPENAI_API_KEY", "test-key-not-real")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for path in (ROOT, TESTS):
    if path not in sys.path:
        sys.path.insert(0, path)

import pytest

import app as app_module
import image_service
import openai_service
import sheets_service as sheets_module
from routes import api_routes

from fakes import FakeOpenAI, FakeSheetsAPI, make_sheets_service
from sheet_fixture import make_tabs

ENV_VARS = (
    "OPENAI_MODEL",
    "OPENAI_REASONING_EFFORT",
    "OPENAI_IMAGE_MODEL",
    "OPENAI_IMAGE_QUALITY",
    "IMAGE_RATE_LIMIT_PER_HOUR",
    "IMAGE_DAILY_LIMIT",
)


@pytest.fixture
def sheet_api():
    """The in-memory spreadsheet (real layout fixture)"""
    return FakeSheetsAPI(make_tabs())


@pytest.fixture
def fake_openai():
    return FakeOpenAI()


@pytest.fixture
def sync_background(monkeypatch):
    """Run "background" jobs immediately, so tests can check the sheet right after a request"""
    monkeypatch.setattr(api_routes, "run_in_background", lambda target, *args: target(*args))


@pytest.fixture(autouse=True)
def wired_app(monkeypatch, sheet_api, fake_openai):
    """Wire the app to the fakes and reset all in-memory state between tests"""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setattr(sheets_module, "sheets_service", make_sheets_service(sheet_api))
    monkeypatch.setitem(api_routes._sheets_initialization_status, "initialized", True)
    monkeypatch.setitem(api_routes._sheets_initialization_status, "error", None)

    openai_service.set_openai_client(fake_openai)
    image_service.image_rate_limiter.reset()
    api_routes._pending_custom_forms.clear()

    yield

    openai_service.set_openai_client(None)
    image_service.image_rate_limiter.reset()


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        yield test_client


def form_rows(sheet_api):
    """The data rows of the forms tab (without the header)"""
    return sheet_api.tabs["forms"][1:]
