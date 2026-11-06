"""
The Google Sheets client must work when two threads use it at the same moment
(e.g. /translate reads the prompt while a background job saves a visitor's new form).

This test uses the REAL googleapiclient code (built by sheets_service.build_sheets_api) talking to a
tiny fake Google Sheets server on this computer - no internet, no credentials.
"""

import http.server
import json
import socketserver
import threading
import time
import urllib.parse

import pytest
from google.auth.credentials import AnonymousCredentials

from sheets_service import SheetsService, build_sheets_api

TABS = {
    "forms": [["id", "form name", "form description", "category", "user_submitted", "user_submitted"],
              ["1", "PHILOSOPHY", "", "", "TRUE"], ["7", "gen z", "", "", "person"]],
    "prompt": [["id", "prompt"], ["1", "Translate {source_text} from {source_form} to {target_form}"]],
}


class _SlowFakeSheets(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive, like Google

    def do_GET(self):
        time.sleep(0.1)  # slow enough that the two threads' requests overlap
        a1 = urllib.parse.unquote(self.path.split("/values/")[1].split("?")[0])
        body = json.dumps({"range": a1, "values": TABS[a1.split("!")[0]]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


@pytest.fixture
def real_client_service():
    server = _Server(("127.0.0.1", 0), _SlowFakeSheets)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    service = SheetsService.__new__(SheetsService)  # skip the Google login
    service.sheet_id = "fake-sheet-id"
    service.service = build_sheets_api(
        AnonymousCredentials(),
        client_options={"api_endpoint": f"http://127.0.0.1:{server.server_address[1]}"},
    )
    yield service
    server.shutdown()


def test_two_threads_get_their_own_answers(real_client_service):
    for _ in range(3):
        results = {}

        def background_job():
            rows = real_client_service.get_all_forms("forms", include_user_submitted=True, raise_errors=True)
            results["forms"] = [row["form_name"] for row in rows]

        thread = threading.Thread(target=background_job)
        thread.start()
        results["prompt"] = real_client_service.get_prompt_from_sheet("prompt", "1")
        thread.join(timeout=10)

        assert results == {
            "prompt": "Translate {source_text} from {source_form} to {target_form}",
            "forms": ["PHILOSOPHY", "gen z"],
        }
