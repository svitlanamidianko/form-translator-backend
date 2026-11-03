"""
The REAL openai SDK, but with a fake HTTP layer (httpx.MockTransport) - no network.
Checks that our calls produce the request bodies OpenAI expects, and that real
HTTP errors become readable messages.
"""

import base64
import json

import httpx
import pytest
from openai import OpenAI

import openai_service


def make_client(handler):
    return OpenAI(
        api_key="sk-test-not-real",
        base_url="https://api.openai.test/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )


def chat_response(text):
    return {
        "id": "chatcmpl-1", "object": "chat.completion", "created": 0, "model": "gpt-5.6-luna",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": text}}],
    }


def test_chat_request_body():
    seen = []

    def handler(request):
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json=chat_response("hello there"))

    openai_service.set_openai_client(make_client(handler))
    assert openai_service.chat_completion([{"role": "user", "content": "hi"}]) == "hello there"

    path, body = seen[0]
    assert path == "/v1/chat/completions"
    assert body == {
        "model": "gpt-5.6-luna",
        "messages": [{"role": "user", "content": "hi"}],
        "max_completion_tokens": 2000,
        "reasoning_effort": "low",
    }


def test_real_400_about_reasoning_effort_is_retried_without_it():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(400, json={"error": {
                "message": "Unrecognized request argument supplied: reasoning_effort",
                "type": "invalid_request_error", "param": None, "code": None}})
        return httpx.Response(200, json=chat_response("ok"))

    openai_service.set_openai_client(make_client(handler))
    assert openai_service.chat_completion([{"role": "user", "content": "hi"}], model="gpt-4o-mini") == "ok"
    assert "reasoning_effort" in bodies[0] and "reasoning_effort" not in bodies[1]


def test_real_404_becomes_readable_message():
    def handler(request):
        return httpx.Response(404, json={"error": {
            "message": "The model `gpt-3.5-turbo` does not exist or you do not have access to it.",
            "type": "invalid_request_error", "param": None, "code": "model_not_found"}})

    openai_service.set_openai_client(make_client(handler))
    with pytest.raises(Exception) as error:
        openai_service.chat_completion([{"role": "user", "content": "hi"}], model="gpt-3.5-turbo")
    assert openai_service.describe_openai_error(error.value) == (
        "OpenAI API error 404: The model `gpt-3.5-turbo` does not exist or you do not have access to it.")


def test_connection_error_is_readable():
    def handler(request):
        raise httpx.ConnectError("name resolution failed")

    openai_service.set_openai_client(make_client(handler))
    with pytest.raises(Exception) as error:
        openai_service.chat_completion([{"role": "user", "content": "hi"}])
    assert openai_service.describe_openai_error(error.value).startswith("Could not reach the OpenAI API")


def test_image_edit_multipart_request():
    seen = []
    fake_jpeg = b"\xff\xd8\xff\xe0fake-jpeg-bytes"

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"created": 0, "data": [{"b64_json": base64.b64encode(b"result").decode()}]})

    openai_service.set_openai_client(make_client(handler))
    result = openai_service.edit_image(fake_jpeg, "make it poetic", "1536x1024")
    assert base64.b64decode(result) == b"result"

    request = seen[0]
    assert request.url.path == "/v1/images/edits"
    assert request.headers["content-type"].startswith("multipart/form-data")
    body = request.content
    for field, value in (("model", b"gpt-image-2.5-flare"), ("prompt", b"make it poetic"),
                         ("size", b"1536x1024"), ("quality", b"low"), ("output_format", b"jpeg"),
                         ("output_compression", b"85")):
        assert b'name="' + field.encode() + b'"\r\n\r\n' + value in body, field
    assert b'filename="photo.jpg"' in body
    assert b"Content-Type: image/jpeg" in body
    assert fake_jpeg in body
