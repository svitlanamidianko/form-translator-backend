"""scripts/suggest_groups.py (dry run by default, writes ONLY column E with --apply)"""

import importlib.util
import json
import os

import pytest

from fakes import completion, make_sheets_service

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("suggest_groups", os.path.join(ROOT, "scripts", "suggest_groups.py"))
suggest_groups = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(suggest_groups)


ANSWER = {
    "assignments": [
        {"form": "corporate", "group": "workplace"},
        {"form": "professional", "group": "Workplace "},
        {"form": "tech bro startup", "group": "workplace"},
        {"form": "gen z", "group": "internet culture"},
        {"form": "brainrot", "group": "internet culture"},
        {"form": "woo woo", "group": "Religion"},          # existing group, other spelling
        {"form": "haiku", "group": "lonely new group"},    # only one member -> dropped
        {"form": "self-hate", "group": "hidden"},          # never proposed by the script
        {"form": "poetic", "group": ""},
    ]
}


@pytest.fixture
def answering(fake_openai):
    fake_openai.chat_handler = lambda params: completion("```json\n" + json.dumps(ANSWER) + "\n```")
    return fake_openai


def column_e(sheet_api, name):
    for row in sheet_api.tabs["forms"][1:]:
        if str(row[1]).strip() == name:
            return row[4] if len(row) > 4 else ""
    raise KeyError(name)


def test_dry_run_writes_nothing(sheet_api, answering, capsys):
    service = make_sheets_service(sheet_api)
    assert suggest_groups.main([], service=service) == 0

    assert sheet_api.writes() == []
    out = capsys.readouterr().out
    assert "Dry run" in out
    assert "workplace" in out and "NEW group" in out
    assert "Skipped (column E still holds the old TRUE flag" in out

    # The AI saw her groups with examples, and only ungrouped forms
    prompt = answering.chat_calls[-1]["messages"][-1]["content"]
    assert '"religion" (e.g. christian, hindu, buddhist' in prompt
    assert '- "corporate": Professional, neutral' in prompt
    assert '- "christian"' not in prompt          # already grouped
    assert '- "neutral"' not in prompt            # reserved
    assert '- "business"' not in prompt           # TRUE in column E -> skipped


def test_apply_writes_only_column_e(sheet_api, answering):
    before = [list(row) for row in sheet_api.tabs["forms"]]
    service = make_sheets_service(sheet_api)
    assert suggest_groups.main(["--apply"], service=service) == 0

    assert column_e(sheet_api, "corporate") == "workplace"
    assert column_e(sheet_api, "professional") == "workplace"
    assert column_e(sheet_api, "tech bro startup") == "workplace"
    assert column_e(sheet_api, "gen z") == "internet culture"
    assert column_e(sheet_api, "woo woo") == "religion"
    assert column_e(sheet_api, "haiku") == ""          # lonely new group dropped
    assert column_e(sheet_api, "self-hate") == ""      # never "hidden"
    assert column_e(sheet_api, "poetic") == ""

    # Nothing but column E changed, and only via single-cell E updates
    def padded(row):
        return list(row) + [""] * (6 - len(row))

    for old, new in zip(before, sheet_api.tabs["forms"]):
        assert padded(new)[:4] == padded(old)[:4]
        assert padded(new)[5:] == padded(old)[5:]
    assert all(call[1].startswith("forms!E") for call in sheet_api.writes())
    assert sheet_api.tabs["forms"][0] == before[0]

    # The old TRUE flag rows were not touched
    business = [row for row in sheet_api.tabs["forms"] if row[1] == "business"][0]
    assert business[4] == "TRUE"


def test_no_new_groups_option(sheet_api, answering):
    service = make_sheets_service(sheet_api)
    assert suggest_groups.main(["--apply", "--no-new-groups"], service=service) == 0
    assert column_e(sheet_api, "corporate") == ""
    assert column_e(sheet_api, "woo woo") == "religion"
    assert "You MAY create" not in answering.chat_calls[-1]["messages"][-1]["content"]


def test_model_and_effort_options(sheet_api, answering):
    service = make_sheets_service(sheet_api)
    suggest_groups.main(["--model", "gpt-5.6-terra", "--effort", "medium"], service=service)
    assert answering.chat_calls[-1]["model"] == "gpt-5.6-terra"
    assert answering.chat_calls[-1]["reasoning_effort"] == "medium"


def test_rows_changed_meanwhile_are_skipped(sheet_api, answering, monkeypatch):
    service = make_sheets_service(sheet_api)
    real_read_rows = suggest_groups.read_rows
    calls = {"n": 0}

    def read_rows_then_she_edits(svc):
        calls["n"] += 1
        rows = real_read_rows(svc)
        if calls["n"] == 1:
            # She types a group for "corporate" by hand while the AI is thinking
            for row in sheet_api.tabs["forms"]:
                if row[1] == "corporate":
                    row.extend([""] * (5 - len(row)))
                    row[4] = "her own choice"
        return rows

    monkeypatch.setattr(suggest_groups, "read_rows", read_rows_then_she_edits)
    suggest_groups.main(["--apply"], service=service)
    assert column_e(sheet_api, "corporate") == "her own choice"
    assert column_e(sheet_api, "professional") == "workplace"
