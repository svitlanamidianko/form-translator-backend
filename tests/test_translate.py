"""POST /translate, POST /detect-form, custom forms + auto-grouping, and the OpenAI helper"""

import threading

import pytest

import forms_catalog
import openai_service
from fakes import completion, make_openai_error
from routes import api_routes


def form_rows(sheet_api):
    return sheet_api.tabs["forms"][1:]


def rows_named(sheet_api, name):
    return [row for row in form_rows(sheet_api) if str(row[1]).strip().casefold() == name.casefold()]


def translate(client, source="corporate", target="poetic", text="We need to align on deliverables."):
    return client.post("/translate", json={"sourceForm": source, "targetForm": target, "sourceText": text})


# ---------------------------------------------------------------------------
# /translate
# ---------------------------------------------------------------------------

def test_translate_happy_path(client, sheet_api, fake_openai):
    response = translate(client)
    assert response.status_code == 200, response.get_data(as_text=True)
    data = response.get_json()
    assert data["translatedText"].startswith("✨ [poetic]")
    assert data["sourceForm"] == "corporate"
    assert data["targetForm"] == "poetic"
    assert data["sourceText"] == "We need to align on deliverables."

    # One chat call with the new-style parameters
    params = fake_openai.chat_calls[-1]
    assert params["model"] == "gpt-5.6-luna"
    assert params["reasoning_effort"] == "low"
    assert params["max_completion_tokens"] >= 2000
    assert "temperature" not in params and "max_tokens" not in params

    # Her English description (column D) goes into the prompt
    prompt = params["messages"][-1]["content"]
    assert "poetic - Image-rich, metaphor-forward lines" in prompt
    assert "corporate - Professional, neutral, de-risked" in prompt

    # Logged to history, no new forms written
    history_rows = sheet_api.tabs["history"]
    assert history_rows[-1][2] == "corporate" and history_rows[-1][5] == "poetic"
    assert [call for call in sheet_api.writes("append") if call[1].startswith("forms")] == []


def test_translate_uses_model_and_effort_from_env(client, fake_openai, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "medium")
    assert translate(client).status_code == 200
    assert fake_openai.chat_calls[-1]["model"] == "gpt-5.6-terra"
    assert fake_openai.chat_calls[-1]["reasoning_effort"] == "medium"

    # Empty string (or "off") = don't send reasoning_effort at all (for non-reasoning models)
    for value in ("", "off"):
        monkeypatch.setenv("OPENAI_REASONING_EFFORT", value)
        assert translate(client).status_code == 200
        assert "reasoning_effort" not in fake_openai.chat_calls[-1]


def test_translate_surfaces_real_openai_error(client, fake_openai):
    fake_openai.chat_errors.append(make_openai_error(
        404, "The model `gpt-3.5-turbo` has been deprecated and is no longer available.", code="model_not_found"))
    response = translate(client)
    assert response.status_code == 500
    error = response.get_json()["error"]
    assert error.startswith("Translation failed: ")
    assert "OpenAI API error 404" in error
    assert "gpt-3.5-turbo` has been deprecated" in error


def test_translate_missing_api_key_is_explained(client, monkeypatch):
    openai_service.set_openai_client(None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    response = translate(client)
    assert response.status_code == 500
    assert "OPENAI_API_KEY environment variable is not set" in response.get_json()["error"]


def test_translate_validation_errors(client):
    assert client.post("/translate", json={"sourceForm": "a", "targetForm": "b"}).status_code == 400
    response = client.post("/translate", data="{not json", content_type="application/json")
    assert response.status_code == 400
    assert response.get_json()["error"] == "No data provided"


def test_translate_same_form_returns_text(client, fake_openai):
    response = translate(client, source="Poetic ", target="poetic", text="hello")
    assert response.status_code == 200
    assert response.get_json()["translatedText"] == "hello"
    assert fake_openai.chat_calls == []


def test_empty_answer_is_an_error_with_reason(client, fake_openai):
    fake_openai.chat_handler = lambda params: completion("", finish_reason="length")
    response = translate(client)
    assert response.status_code == 500
    assert "empty answer (finish_reason: length" in response.get_json()["error"]


# ---------------------------------------------------------------------------
# reasoning_effort fallback
# ---------------------------------------------------------------------------

def test_reasoning_effort_rejected_retries_without_it(client, fake_openai):
    fake_openai.chat_errors.append(make_openai_error(
        400, "Unsupported parameter: 'reasoning_effort' is not supported with this model.",
        param="reasoning_effort", code="unsupported_parameter"))

    response = translate(client)
    assert response.status_code == 200, response.get_data(as_text=True)
    first, second = fake_openai.chat_calls[-2:]
    assert first["reasoning_effort"] == "low"
    assert "reasoning_effort" not in second

    # Remembered: the next call doesn't even try
    assert translate(client).status_code == 200
    assert "reasoning_effort" not in fake_openai.chat_calls[-1]


def test_other_bad_requests_are_not_retried(fake_openai):
    fake_openai.chat_errors.append(make_openai_error(400, "messages: too long"))
    with pytest.raises(Exception) as error:
        openai_service.chat_completion([{"role": "user", "content": "hi"}])
    assert "too long" in openai_service.describe_openai_error(error.value)
    assert len(fake_openai.chat_calls) == 1


# ---------------------------------------------------------------------------
# Custom forms: duplicate-safe + automatic semantic group
# ---------------------------------------------------------------------------

def test_existing_forms_never_duplicated(client, sheet_api, sync_background):
    before = len(sheet_api.tabs["forms"])
    for target in ("Gen Z ", "gen  z", "socialism", "SOCIALISM ", "Philosophy", "spam test"):
        assert translate(client, target=target).status_code == 200
    assert len(sheet_api.tabs["forms"]) == before


def test_new_custom_form_gets_group_and_real_layout(client, sheet_api, fake_openai, sync_background):
    original = fake_openai.chat_handler

    def handler(params):
        system = params["messages"][0]["content"]
        if "sort the forms" in system:
            return completion('```json\n{"group": "Religion"}\n```')
        return original(params)

    fake_openai.chat_handler = handler
    response = translate(client, target="  Sufi   mystic ")
    assert response.status_code == 200

    rows = rows_named(sheet_api, "sufi mystic")
    assert rows == [[60, "Sufi mystic", "", "", "religion", True]]

    # The picker was given her existing groups and the new name
    picker_call = [call for call in fake_openai.chat_calls if "sort the forms" in call["messages"][0]["content"]][0]
    picker_prompt = picker_call["messages"][-1]["content"]
    assert '"Sufi mystic"' in picker_prompt
    assert "- political ideology" in picker_prompt and "- hidden" not in picker_prompt


@pytest.mark.parametrize("answer, expected_group", [
    ('{"group": "a brand new group"}', ""),        # invented groups are not accepted
    ('{"group": ""}', ""),
    ('{"group": "HIDDEN"}', "hidden"),              # moderation
    ("no json here", ""),
])
def test_group_picker_answers(client, sheet_api, fake_openai, sync_background, answer, expected_group):
    original = fake_openai.chat_handler
    fake_openai.chat_handler = lambda params: (
        completion(answer) if "sort the forms" in params["messages"][0]["content"] else original(params))

    assert translate(client, target="pirate speak").status_code == 200
    assert rows_named(sheet_api, "pirate speak") == [[60, "pirate speak", "", "", expected_group, True]]


def test_group_picker_failure_writes_empty_group(client, sheet_api, fake_openai, sync_background):
    original = fake_openai.chat_handler

    def handler(params):
        if "sort the forms" in params["messages"][0]["content"]:
            raise make_openai_error(500, "server exploded")
        return original(params)

    fake_openai.chat_handler = handler
    assert translate(client, target="pirate speak").status_code == 200
    assert rows_named(sheet_api, "pirate speak") == [[60, "pirate speak", "", "", "", True]]


def test_reserved_and_huge_names_are_not_saved(client, sheet_api, sync_background):
    before = len(sheet_api.tabs["forms"])
    assert translate(client, source="custom", target="Detect").status_code == 200
    assert translate(client, target="x" * 200).status_code == 200
    assert len(sheet_api.tabs["forms"]) == before


def test_custom_form_saved_in_background_without_blocking(client, sheet_api, fake_openai):
    """The response doesn't wait for the group picker / sheet write"""
    release = threading.Event()
    original = fake_openai.chat_handler

    def slow_picker(params):
        if "sort the forms" in params["messages"][0]["content"]:
            release.wait(5)
            return completion('{"group": ""}')
        return original(params)

    fake_openai.chat_handler = slow_picker
    response = translate(client, target="slow new form")
    assert response.status_code == 200
    assert rows_named(sheet_api, "slow new form") == []  # not written yet

    release.set()
    for _ in range(100):
        if rows_named(sheet_api, "slow new form"):
            break
        threading.Event().wait(0.02)
    assert len(rows_named(sheet_api, "slow new form")) == 1


def test_concurrent_requests_create_one_row(client, sheet_api, fake_openai):
    sheet_api.read_delay = 0.02
    errors = []

    def worker(name):
        try:
            api_routes.queue_custom_form(name)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(name,))
               for name in ["Moon Speak", "moon speak ", "MOON  SPEAK", "moon speak"] * 3]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    for _ in range(200):
        if not api_routes._pending_custom_forms:
            break
        threading.Event().wait(0.02)

    assert errors == []
    assert len(rows_named(sheet_api, "moon speak")) == 1
    # Afterwards it is found in the sheet, so another request adds nothing
    api_routes.queue_custom_form("Moon speak")
    for _ in range(200):
        if not api_routes._pending_custom_forms:
            break
        threading.Event().wait(0.02)
    assert len(rows_named(sheet_api, "moon speak")) == 1


def test_two_new_forms_at_once_get_different_ids(client, sheet_api):
    sheet_api.read_delay = 0.01
    api_routes.queue_custom_form("first new")
    api_routes.queue_custom_form("second new")
    for _ in range(300):
        if not api_routes._pending_custom_forms:
            break
        threading.Event().wait(0.02)
    ids = [rows_named(sheet_api, "first new")[0][0], rows_named(sheet_api, "second new")[0][0]]
    assert sorted(ids) == [60, 61]


def test_hidden_form_still_translates(client, fake_openai):
    response = translate(client, target="spam test")
    assert response.status_code == 200
    assert "[spam test]" in response.get_json()["translatedText"]


# ---------------------------------------------------------------------------
# /detect-form
# ---------------------------------------------------------------------------

def test_detect_form_parses_code_fenced_json(client, fake_openai, sheet_api, sync_background):
    fake_openai.chat_handler = lambda params: completion(
        'Sure! Here you go:\n```json\n{"detectedForm": "Gen Z", "reasoning": "This form uses slang. It is online.",'
        ' "isCustomForm": true, "alternativeForms": ["teen"]}\n```\nHope that helps!')
    response = client.post("/detect-form", json={"text": "no cap this slaps fr"})
    assert response.status_code == 200, response.get_data(as_text=True)
    data = response.get_json()
    # Matched to the existing form, with the sheet's exact spelling
    assert data == {"detectedForm": "gen z", "reasoning": "This form uses slang. It is online.",
                    "isCustomForm": False, "alternativeForms": ["teen"]}

    # Hidden + reserved forms aren't offered to the detector, visitor forms are
    prompt = fake_openai.chat_calls[-1]["messages"][-1]["content"]
    assert "- ELI5" in prompt and "- calm" in prompt
    assert "- spam test" not in prompt and "- neutral" not in prompt
    assert sheet_api.writes("append") == []


def test_detect_form_neutral_is_never_saved(client, fake_openai, sheet_api, sync_background):
    fake_openai.chat_handler = lambda params: completion(
        '{"detectedForm": "Neutral", "reasoning": "No framework.", "isCustomForm": true, "alternativeForms": []}')
    response = client.post("/detect-form", json={"text": "The meeting is at 3."})
    assert response.status_code == 200
    assert response.get_json()["isCustomForm"] is True
    assert [call for call in sheet_api.writes("append") if call[1].startswith("forms")] == []


def test_detect_form_unparseable_answer_falls_back_to_neutral(client, fake_openai, sheet_api, sync_background):
    fake_openai.chat_handler = lambda params: completion("I think it's poetic, honestly.")
    response = client.post("/detect-form", json={"text": "roses are red"})
    assert response.status_code == 200
    assert response.get_json()["detectedForm"] == "neutral"
    assert sheet_api.writes("append") == []


def test_detect_form_new_custom_form_is_saved_once(client, fake_openai, sheet_api, sync_background):
    original = fake_openai.chat_handler
    fake_openai.chat_handler = lambda params: (
        completion('{"detectedForm": "pirate speak ", "reasoning": "Arr.", "isCustomForm": true, "alternativeForms": []}')
        if "Form Detection" in params["messages"][0]["content"] else original(params))

    for _ in range(2):
        response = client.post("/detect-form", json={"text": "Ahoy matey"})
        assert response.status_code == 200
        assert response.get_json()["detectedForm"] == "pirate speak"
    assert len(rows_named(sheet_api, "pirate speak")) == 1


def test_detect_form_openai_error_has_reason(client, fake_openai):
    fake_openai.chat_errors.append(make_openai_error(401, "Incorrect API key provided: sk-abc***"))
    response = client.post("/detect-form", json={"text": "hello"})
    assert response.status_code == 500
    assert "Incorrect API key provided" in response.get_json()["error"]


def test_detect_form_validation(client):
    assert client.post("/detect-form", json={}).status_code == 400
    assert client.post("/detect-form", json={"text": "   "}).status_code == 400


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('```\n{"a": {"b": [1, 2]}}\n```', {"a": {"b": [1, 2]}}),
    ('Here is the JSON: {"a": "x}y"} and some more text {"b": 2}', {"a": "x}y"}),
    ('```JSON\n {"detectedForm": "poetic"} \n```', {"detectedForm": "poetic"}),
])
def test_parse_json_object(text, expected):
    assert openai_service.parse_json_object(text) == expected


def test_parse_json_object_rejects_non_objects():
    with pytest.raises(ValueError):
        openai_service.parse_json_object("[1, 2, 3]")
    with pytest.raises(ValueError):
        openai_service.parse_json_object("nothing to see")


def test_prompt_description_prefers_english_description():
    row = {"description": "Warm and kind.", "ukrainian_name": "добрий"}
    assert forms_catalog.prompt_description(" kind ", row) == "kind - Warm and kind."
    assert forms_catalog.prompt_description("kind", {"description": "", "ukrainian_name": "добрий"}) == "kind - добрий"
    assert forms_catalog.prompt_description("new thing", None) == "new thing"
