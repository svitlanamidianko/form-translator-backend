"""GET /forms + the forms-tab column mapping in SheetsService"""

import json

from fakes import FakeSheetsAPI, make_sheets_service
from sheet_fixture import FORMS_HEADER, LEVELS, make_tabs
import sheets_service as sheets_module


def get_forms(client):
    response = client.get("/forms")
    assert response.status_code == 200, response.get_data(as_text=True)
    return response, response.get_json()


def test_forms_keeps_sheet_order_and_adds_order_field(client):
    response, data = get_forms(client)
    names = list(data["forms"].keys())

    # Sheet order, NOT alphabetical (Flask used to sort keys)
    assert names[:6] == ["ELI5", "teen", "college student", "grad student", "expert (PhD)", "tech bro startup"]
    raw = response.get_data(as_text=True)
    assert raw.index('"teen"') < raw.index('"college student"')

    orders = [form["order"] for form in data["forms"].values()]
    assert orders == sorted(orders)
    assert data["forms"]["ELI5"]["order"] == 0
    assert data["count"] == len(data["forms"])
    assert data["source"] == "Google Sheet"
    assert "timestamp" in data


def test_forms_maps_real_columns(client):
    _, data = get_forms(client)
    forms = data["forms"]

    # C = Ukrainian name -> description "<name> - <C>", E = group
    assert forms["ELI5"] == {
        "description": "ELI5 - поясни як п’ятирічній дитині",
        "category": LEVELS,  # her "| wired" note is kept as-is
        "userSubmitted": False,
        "order": 0,
    }
    assert forms["christian"]["category"] == "religion"
    assert forms["corporate"]["category"] is None
    assert forms["tree"]["description"] == "tree"  # no Ukrainian name -> just the name

    # F = TRUE marks visitor-added forms (column E is still the group)
    assert forms["child"]["userSubmitted"] is True
    assert forms["ayn rand"]["userSubmitted"] is True
    assert forms["ayn rand"]["category"] == "person"

    # Old rows with TRUE in column E: user-submitted, and TRUE is NOT a group
    assert forms["business"] == {"description": "business", "category": None, "userSubmitted": True,
                                 "order": forms["business"]["order"]}
    assert forms["calm"]["userSubmitted"] is True

    # Her own rows without anything in E/F
    assert forms["haiku"]["userSubmitted"] is False
    assert forms["haiku"]["category"] is None


def test_forms_groups_in_first_appearance_order(client):
    _, data = get_forms(client)
    assert data["groups"] == [LEVELS, "philosophical", "religion", "person", "aletheia", "political ideology"]
    assert "TRUE" not in data["groups"]
    assert "hidden" not in data["groups"]
    categories = {form["category"] for form in data["forms"].values()} - {None}
    assert categories == set(data["groups"])


def test_forms_deduplicates_and_excludes_reserved_and_hidden(client):
    _, data = get_forms(client)
    forms = data["forms"]
    names = list(forms.keys())

    # Duplicates are shown once; her own row wins over a visitor duplicate
    assert names.count("gen z") == 1
    assert forms["gen z"]["userSubmitted"] is False
    assert forms["gen z"]["description"] == "gen z - покоління Z"
    assert names.count("baby talk") == 1
    assert forms["baby talk"]["userSubmitted"] is False

    # Only visitor rows -> the first one is kept (ids 1 and 2 were both "PHILOSOPHY")
    assert names.count("PHILOSOPHY") == 1
    assert forms["PHILOSOPHY"]["userSubmitted"] is True

    # Trailing space trimmed
    assert "socialism" in forms
    assert "socialism " not in forms
    assert forms["socialism"]["description"] == "socialism - соціалізм"

    # Reserved names and hidden forms are not listed
    assert "neutral" not in forms
    assert "spam test" not in forms


def test_forms_reserved_names_case_insensitive_and_group_spelling(client, sheet_api):
    sheet_api.tabs["forms"] += [
        ["60", "Detect", "", "", "", "TRUE"],
        ["61", " CUSTOM ", "", "", "", "TRUE"],
        ["62", "sufi", "", "", "Religion ", ""],
        ["63", "Gen  Z", "", "", "", "TRUE"],
        ["64", "", "", "", "religion", ""],
        ["65", "secret", "", "", "HIDDEN", ""],
    ]
    _, data = get_forms(client)
    forms = data["forms"]

    assert "Detect" not in forms and "CUSTOM" not in forms and "" not in forms
    assert "secret" not in forms
    # Same group with different spelling -> first spelling wins, no duplicate group
    assert forms["sufi"]["category"] == "religion"
    assert data["groups"].count("religion") == 1
    assert "Gen  Z" not in forms


def test_forms_returns_real_reason_when_sheet_unreadable(client, sheet_api):
    sheet_api.fail_ranges.add("forms!A:F")
    response = client.get("/forms")
    assert response.status_code == 500
    assert "Simulated Google Sheets failure" in response.get_json()["error"]


def test_parse_form_row_handles_short_rows_and_booleans():
    row = sheets_module.parse_form_row(["7", "x"], sheet_row=9)
    assert row == {"form_id": "7", "form_name": "x", "ukrainian_name": "", "description": "",
                   "group": "", "user_submitted": False, "row_number": 9}

    assert sheets_module.parse_form_row(["1", "a", "", "", True], 2)["user_submitted"] is True
    assert sheets_module.parse_form_row(["1", "a", "", "", "FALSE"], 2)["group"] == ""
    assert sheets_module.parse_form_row(["1", "a", "", "", "religion", "yes"], 2)["user_submitted"] is True
    assert sheets_module.parse_form_row(["1", "a", "", "", "  political   ideology "], 2)["group"] == "political ideology"


# ---------------------------------------------------------------------------
# SheetsService writes (real code, in-memory spreadsheet)
# ---------------------------------------------------------------------------

def test_existing_header_row_is_never_rewritten(sheet_api):
    service = make_sheets_service(sheet_api)
    assert service.add_custom_form("brand new form", group="religion") is True

    assert sheet_api.tabs["forms"][0] == FORMS_HEADER
    assert not [call for call in sheet_api.writes("update") if call[1].startswith("forms!A1")]


def test_headers_written_only_into_empty_sheet():
    api = FakeSheetsAPI({"forms": []})
    service = make_sheets_service(api)
    assert service.add_custom_form("first form") is True

    assert api.tabs["forms"][0] == sheets_module.DEFAULT_FORMS_HEADERS
    assert api.tabs["forms"][1] == [1, "first form", "", "", "", True]


def test_custom_form_written_with_real_layout(sheet_api):
    service = make_sheets_service(sheet_api)
    assert service.add_custom_form("  sufi   mystic ", group="religion") is True

    kind, a1, values = sheet_api.writes("append")[-1]
    assert a1 == "forms!A:F"
    # id | name | "" | "" | group (E) | TRUE (F); next id = highest id + 1
    assert values == [[60, "sufi mystic", "", "", "religion", True]]
    assert sheet_api.tabs["forms"][-1] == [60, "sufi mystic", "", "", "religion", True]


def test_custom_form_duplicate_check_ignores_case_and_spaces(sheet_api):
    service = make_sheets_service(sheet_api)
    before = len(sheet_api.tabs["forms"])
    for name in ("Gen Z ", "GEN  z", "socialism", "Socialism  ", "philosophy"):
        assert service.add_custom_form(name) is True
    assert len(sheet_api.tabs["forms"]) == before
    assert sheet_api.writes("append") == []


def test_custom_form_not_added_when_sheet_cannot_be_read(sheet_api):
    service = make_sheets_service(sheet_api)
    sheet_api.fail_ranges.add("forms!A:F")
    assert service.add_custom_form("something new") is False
    assert sheet_api.writes("append") == []


def test_next_form_id_does_not_fall_back_to_1(sheet_api):
    service = make_sheets_service(sheet_api)
    assert service._get_next_form_id() == 60

    sheet_api.fail_ranges.add("forms!A:A")
    fallback = service._get_next_form_id()
    assert fallback > 1_000_000_000  # timestamp-based, can't collide with 1, 2, ...


def test_update_form_groups_only_touches_column_e(sheet_api):
    service = make_sheets_service(sheet_api)
    before = [list(row) for row in sheet_api.tabs["forms"]]

    assert service.update_form_groups({7: "workplace", 9: "voices & moods"}) == 2

    after = sheet_api.tabs["forms"]
    for index, (old, new) in enumerate(zip(before, after)):
        sheet_row = index + 1
        if sheet_row in (7, 9):
            assert new[4] == ("workplace" if sheet_row == 7 else "voices & moods")
            assert new[:4] == old[:4]
        else:
            assert new == old
    assert [call[1] for call in sheet_api.writes("update")] == ["forms!E7", "forms!E9"]


def test_forms_list_endpoint_still_works(client):
    response = client.get("/forms/list?include_user_submitted=true")
    assert response.status_code == 200
    data = response.get_json()
    assert data["count"] == len(make_tabs()["forms"]) - 1
    first = data["forms"][0]
    assert first["form_name"] == "ELI5" and first["group"] == LEVELS
    json.dumps(data)  # serializable
