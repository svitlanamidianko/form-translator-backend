"""
In-memory fakes used by the tests (no network, no credentials):

- FakeSheetsAPI: pretends to be the googleapiclient "service" object, so the REAL SheetsService
  code runs against an in-memory spreadsheet (this is like a tiny fake database in Python tests)
- FakeOpenAI: pretends to be the OpenAI client (chat.completions.create + images.edit)
- make_openai_error(): builds real openai exceptions (e.g. a 404 "model does not exist")
"""

import base64
import io
import json
import re
import threading
import time
from types import SimpleNamespace

import httpx
import openai

from sheets_service import SheetsService


# ---------------------------------------------------------------------------
# Google Sheets
# ---------------------------------------------------------------------------

def _column_index(letters: str) -> int:
    index = 0
    for letter in letters.upper():
        index = index * 26 + (ord(letter) - ord("A") + 1)
    return index - 1


def _parse_range(a1: str):
    """'forms!A1:F1' -> ('forms', col1, row1, col2, row2); rows/cols 0-based, None = open-ended"""
    sheet, _, cells = a1.partition("!")
    parts = cells.split(":")

    def parse_cell(text):
        match = re.fullmatch(r"([A-Za-z]*)(\d*)", text)
        letters, digits = match.group(1), match.group(2)
        col = _column_index(letters) if letters else None
        row = int(digits) - 1 if digits else None
        return col, row

    c1, r1 = parse_cell(parts[0])
    c2, r2 = parse_cell(parts[1]) if len(parts) > 1 else (c1, r1)
    return sheet, c1, r1, c2, r2


def _formatted(value):
    """What the real API returns by default (FORMATTED_VALUE): everything as text, booleans as TRUE/FALSE"""
    if value is True:
        return "TRUE"
    if value is False:
        return "FALSE"
    if value is None:
        return ""
    return str(value)


class _Request:
    def __init__(self, func):
        self._func = func

    def execute(self):
        return self._func()


class _Values:
    def __init__(self, api):
        self._api = api

    def get(self, spreadsheetId, range, **kwargs):
        return _Request(lambda: self._api._get(range))

    def append(self, spreadsheetId, range, valueInputOption=None, insertDataOption=None, body=None, **kwargs):
        return _Request(lambda: self._api._append(range, body["values"]))

    def update(self, spreadsheetId, range, valueInputOption=None, body=None, **kwargs):
        return _Request(lambda: self._api._update(range, body["values"]))

    def batchUpdate(self, spreadsheetId, body, **kwargs):
        def run():
            total = 0
            for item in body["data"]:
                self._api._update(item["range"], item["values"])
                total += sum(len(row) for row in item["values"])
            return {"totalUpdatedCells": total}
        return _Request(run)


class _Spreadsheets:
    def __init__(self, api):
        self._api = api

    def values(self):
        return _Values(self._api)


class FakeSheetsAPI:
    """
    In-memory spreadsheet. tabs = {"forms": [[header...], [row...], ...], ...}
    Records every call in .calls as (kind, range, values).
    Set .fail_ranges = {"forms!A:A"} to make reads of that range raise.
    Set .read_delay = 0.05 to slow reads down (for concurrency tests).
    """

    def __init__(self, tabs=None):
        self.tabs = {name: [list(row) for row in rows] for name, rows in (tabs or {}).items()}
        self.calls = []
        self.fail_ranges = set()
        self.read_delay = 0
        self._lock = threading.Lock()

    def spreadsheets(self):
        return _Spreadsheets(self)

    # -- helpers -------------------------------------------------------------
    def _tab(self, name):
        return self.tabs.setdefault(name, [])

    def writes(self, kind=None):
        return [call for call in self.calls if call[0] != "get" and (kind is None or call[0] == kind)]

    def _get(self, a1):
        if self.read_delay:
            time.sleep(self.read_delay)
        with self._lock:
            self.calls.append(("get", a1, None))
            if a1 in self.fail_ranges:
                raise RuntimeError(f"Simulated Google Sheets failure reading {a1}")

            sheet, c1, r1, c2, r2 = _parse_range(a1)
            rows = self._tab(sheet)
            start_row = r1 or 0
            end_row = (r2 + 1) if r2 is not None else len(rows)
            start_col = c1 or 0

            result = []
            for row in rows[start_row:end_row]:
                end_col = (c2 + 1) if c2 is not None else len(row)
                cells = [_formatted(value) for value in row[start_col:end_col]]
                while cells and cells[-1] == "":
                    cells.pop()
                result.append(cells)
            while result and not result[-1]:
                result.pop()

            return {"values": result} if result else {}

    def _write_cells(self, rows, start_row, start_col, values):
        for row_offset, row_values in enumerate(values):
            row_index = start_row + row_offset
            while len(rows) <= row_index:
                rows.append([])
            row = rows[row_index]
            for col_offset, value in enumerate(row_values):
                col_index = start_col + col_offset
                while len(row) <= col_index:
                    row.append("")
                row[col_index] = value

    def _append(self, a1, values):
        with self._lock:
            self.calls.append(("append", a1, [list(v) for v in values]))
            sheet, c1, _, _, _ = _parse_range(a1)
            rows = self._tab(sheet)
            last = -1
            for index, row in enumerate(rows):
                if any(_formatted(cell) != "" for cell in row):
                    last = index
            self._write_cells(rows, last + 1, c1 or 0, values)
            return {"updates": {"updatedRange": f"{sheet}!A{last + 2}"}}

    def _update(self, a1, values):
        with self._lock:
            self.calls.append(("update", a1, [list(v) for v in values]))
            sheet, c1, r1, _, _ = _parse_range(a1)
            self._write_cells(self._tab(sheet), r1 or 0, c1 or 0, values)
            return {"updatedCells": sum(len(v) for v in values)}


def make_sheets_service(api: FakeSheetsAPI, sheet_id: str = "fake-sheet-id") -> SheetsService:
    """A REAL SheetsService, but talking to the in-memory FakeSheetsAPI (skips Google login)"""
    service = SheetsService.__new__(SheetsService)
    service.credentials_file = None
    service.sheet_id = sheet_id
    service.service = api
    return service


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

_ERROR_CLASSES = {
    400: openai.BadRequestError,
    401: openai.AuthenticationError,
    403: openai.PermissionDeniedError,
    404: openai.NotFoundError,
    429: openai.RateLimitError,
}


def make_openai_error(status: int, message: str, param=None, code=None):
    """Build the same exception the real SDK raises for an HTTP error from OpenAI"""
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status, request=request)
    body = {"message": message, "type": "invalid_request_error", "param": param, "code": code}
    error_class = _ERROR_CLASSES.get(status, openai.InternalServerError if status >= 500 else openai.APIStatusError)
    return error_class(f"Error code: {status} - {{'error': {body}}}", response=response, body=body)


def _completion(text, finish_reason="stop"):
    message = SimpleNamespace(content=text, role="assistant")
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)])


def _message_text(content) -> str:
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content or ""


def default_chat_handler(params):
    """Deterministic, playful fake answers for each kind of prompt the backend sends"""
    messages = params["messages"]
    system = _message_text(messages[0]["content"]) if messages and messages[0]["role"] == "system" else ""
    last = messages[-1]["content"]

    # Photo caption (vision input = list content with an image_url part)
    if isinstance(last, list):
        form = re.search(r"written in the form (\"[^\"]*\")", _message_text(last))
        form_name = json.loads(form.group(1)) if form else "this form"
        return _completion(f"A photo, lovingly retold in the voice of {form_name} 🌿")

    user = _message_text(last)

    # Form detection
    if "Form Detection expert" in system:
        text = user.split("Text to analyze:", 1)[-1].lower()
        detected = "gen z" if any(word in text for word in ("lol", "fr ", "no cap", "slay")) else "poetic"
        answer = {
            "detectedForm": detected,
            "reasoning": f"This form leans on the idioms of {detected}. It wraps meaning in that ontology's vocabulary.",
            "isCustomForm": False,
            "alternativeForms": ["silly"],
        }
        return _completion("```json\n" + json.dumps(answer, ensure_ascii=False) + "\n```")

    # Semantic group picker for new visitor forms
    if "sort the forms" in system:
        name_match = re.search(r"New form name: (\".*?\")", user)
        name = json.loads(name_match.group(1)).lower() if name_match else ""
        groups = re.findall(r"^- (.+)$", user, re.MULTILINE)
        for group in groups:
            for word in re.findall(r"\w+", group.lower()):
                if len(word) > 3 and word in name:
                    return _completion(json.dumps({"group": group}))
        return _completion(json.dumps({"group": ""}))

    # Translation
    target = re.search(r"into (.+?) \(", user)
    target_name = target.group(1) if target else "the target form"
    text = user.split("Text:", 1)[-1].strip() if "Text:" in user else user[-200:]
    return _completion(f"✨ [{target_name}] {text} ✨")


def _fake_jpeg_from(image_bytes: bytes) -> bytes:
    """Re-encode the uploaded photo with a warm tint (or return a tiny JPEG if Pillow can't read it)"""
    try:
        from PIL import Image, ImageOps

        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        tinted = ImageOps.colorize(ImageOps.grayscale(image), black="#2b1b3d", white="#ffd9a0")
        output = io.BytesIO()
        tinted.save(output, format="JPEG", quality=80)
        return output.getvalue()
    except Exception:
        from PIL import Image

        output = io.BytesIO()
        Image.new("RGB", (8, 8), (255, 200, 120)).save(output, format="JPEG")
        return output.getvalue()


class _FakeChatCompletions:
    def __init__(self, owner):
        self._owner = owner

    def create(self, **params):
        owner = self._owner
        with owner.lock:
            owner.chat_calls.append(params)
            planned_error = owner.chat_errors.pop(0) if owner.chat_errors else None
        if planned_error is not None:
            raise planned_error
        return owner.chat_handler(params)


class _FakeImages:
    def __init__(self, owner):
        self._owner = owner

    def edit(self, **params):
        owner = self._owner
        with owner.lock:
            owner.image_calls.append(params)
            planned_error = owner.image_errors.pop(0) if owner.image_errors else None
        if planned_error is not None:
            raise planned_error
        image = params["image"]
        image_bytes = image[1] if isinstance(image, tuple) else image.read()
        b64 = base64.b64encode(_fake_jpeg_from(image_bytes)).decode("ascii")
        return SimpleNamespace(data=[SimpleNamespace(b64_json=b64)])


class FakeOpenAI:
    """
    Stand-in for openai.OpenAI(). Inspect .chat_calls / .image_calls in tests.
    Queue failures with .chat_errors.append(make_openai_error(...)) (used once each, in order),
    or replace .chat_handler with your own function(params) -> completion.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.chat_calls = []
        self.image_calls = []
        self.chat_errors = []
        self.image_errors = []
        self.chat_handler = default_chat_handler
        self.chat = SimpleNamespace(completions=_FakeChatCompletions(self))
        self.images = _FakeImages(self)


def completion(text, finish_reason="stop"):
    """Build a chat completion object (for custom chat handlers in tests)"""
    return _completion(text, finish_reason)
