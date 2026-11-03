"""
OpenAI helpers for Form Translator Backend

Everything that talks to OpenAI lives here, so the routes stay readable:
- get_openai_client(): one shared client (official openai 2.x SDK)
- chat_completion(): the ONE helper all text calls go through (translate, detect, grouping)
- edit_image(): photo -> photo "translation" via the images edit endpoint
- describe_openai_error(): turns an OpenAI exception into a human readable reason
- parse_json_object(): robust JSON parsing for model answers (handles ```json fences)

Which models we use is configured with environment variables (see env.example):
- OPENAI_MODEL              text model            (default: gpt-5.6-luna)
- OPENAI_REASONING_EFFORT   none|low|medium|...   (default: low; empty = don't send it)
- OPENAI_IMAGE_MODEL        image model           (default: gpt-image-2.5-flare)
- OPENAI_IMAGE_QUALITY      low|medium|high|auto  (default: low)
- OPENAI_BASE_URL           another OpenAI-compatible API, e.g. https://openrouter.ai/api/v1
                            (default: OpenAI itself)
- OPENAI_IMAGE_API          edits|chat            (default: edits)
                            "chat" makes photos with a chat model that can answer with images,
                            which is how OpenRouter does it (e.g. OPENAI_IMAGE_MODEL=google/gemini-3.1-flash-image)
"""

import base64
import io
import json
import os
import re
import threading

import openai
from openai import OpenAI

DEFAULT_TEXT_MODEL = "gpt-5.6-luna"
DEFAULT_REASONING_EFFORT = "low"
DEFAULT_IMAGE_MODEL = "gpt-image-2.5-flare"
DEFAULT_IMAGE_QUALITY = "low"

# Reasoning models count their hidden "thinking" tokens inside max_completion_tokens,
# so we give them some headroom (an answer of ~500 words still fits comfortably)
DEFAULT_MAX_COMPLETION_TOKENS = 2000

CHAT_TIMEOUT_SECONDS = 60.0
IMAGE_TIMEOUT_SECONDS = 120.0

# Shared client - created the first time we need it
_client = None
_client_lock = threading.Lock()

# Models that told us "I don't support reasoning_effort" - we stop sending it to them
_models_without_reasoning_effort = set()

# A unique "nothing passed" marker (so callers can explicitly pass reasoning_effort="")
_USE_ENV = object()


class OpenAIEmptyResponseError(Exception):
    """Raised when OpenAI answers with no text at all (e.g. all tokens went into reasoning)"""


# ---------------------------------------------------------------------------
# Configuration (read from env every time, so tests / flyctl secrets just work)
# ---------------------------------------------------------------------------

def get_text_model() -> str:
    return (os.environ.get("OPENAI_MODEL") or "").strip() or DEFAULT_TEXT_MODEL


def get_reasoning_effort() -> str:
    """
    Empty string (or "off") means: don't send reasoning_effort at all (for non-reasoning models).
    "off" exists because some hosts (like `flyctl secrets set`) don't like empty values.
    """
    value = os.environ.get("OPENAI_REASONING_EFFORT")
    if value is None:
        return DEFAULT_REASONING_EFFORT
    value = value.strip()
    if value.lower() in ("off", "omit"):
        return ""
    return value


def get_image_model() -> str:
    return (os.environ.get("OPENAI_IMAGE_MODEL") or "").strip() or DEFAULT_IMAGE_MODEL


def get_image_quality() -> str:
    return (os.environ.get("OPENAI_IMAGE_QUALITY") or "").strip() or DEFAULT_IMAGE_QUALITY


def get_base_url():
    """None means the normal OpenAI API"""
    return (os.environ.get("OPENAI_BASE_URL") or "").strip() or None


def get_image_api() -> str:
    return "chat" if (os.environ.get("OPENAI_IMAGE_API") or "").strip().lower() == "chat" else "edits"


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def get_openai_client():
    """Get the shared OpenAI client, creating it if needed"""
    global _client

    if _client is None:
        with _client_lock:
            if _client is None:
                api_key = os.environ.get('OPENAI_API_KEY')
                if not api_key:
                    raise ValueError("OPENAI_API_KEY environment variable is not set")

                # Text calls rarely take more than a few seconds; image edits get their own longer timeout below
                _client = OpenAI(api_key=api_key, base_url=get_base_url(), timeout=CHAT_TIMEOUT_SECONDS, max_retries=2)
                print(f"✅ OpenAI client initialized (api: {get_base_url() or 'OpenAI'}, text model: {get_text_model()}, "
                      f"image model: {get_image_model()} via {get_image_api()})")

    return _client


def set_openai_client(client):
    """Swap in a different client (used by tests and the local fake server)"""
    global _client
    _client = client
    _models_without_reasoning_effort.clear()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

def describe_openai_error(error: Exception) -> str:
    """
    Turn any exception from an OpenAI call into a short, human readable reason.
    Example: "OpenAI API error 404: The model `gpt-3.5-turbo` does not exist or you do not have access to it."
    """
    if isinstance(error, openai.APIStatusError):
        message = None
        body = error.body
        if isinstance(body, dict):
            message = body.get("message")
        elif isinstance(body, str) and body.strip():
            message = body.strip()
        return f"OpenAI API error {error.status_code}: {message or error.message}"

    if isinstance(error, openai.APITimeoutError):
        return "OpenAI API timed out. Please try again."

    if isinstance(error, openai.APIConnectionError):
        return f"Could not reach the OpenAI API: {error}"

    return str(error) or error.__class__.__name__


def is_service_side_error(error: Exception) -> bool:
    """
    True when a failed OpenAI call was NOT caused by what the visitor sent:
    OpenAI is down / unreachable / timed out (5xx, connection errors), we are rate limited (429),
    or our own key / model settings are wrong (401, 403, 404).
    False for answers about the request itself, e.g. a 400 safety-system (moderation) block.
    """
    if isinstance(error, openai.APIConnectionError):  # also covers timeouts
        return True
    status = getattr(error, "status_code", None)
    return isinstance(status, int) and (status >= 500 or status in (401, 403, 404, 429))


def _mentions_reasoning_effort(error: Exception) -> bool:
    text = describe_openai_error(error) + " " + str(error)
    if isinstance(error, openai.APIStatusError) and error.param:
        text += " " + str(error.param)
    return "reasoning_effort" in text or "reasoning.effort" in text


# ---------------------------------------------------------------------------
# Chat (text) calls
# ---------------------------------------------------------------------------

def chat_completion(messages, max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS,
                    model: str = None, reasoning_effort=_USE_ENV) -> str:
    """
    Send a chat request and return the answer text.
    This is like a tiny wrapper function you'd write in any Python project so that
    every call uses the same model / settings.

    Args:
        messages: list of {"role": ..., "content": ...} dicts
        max_completion_tokens: upper limit for the answer (includes reasoning tokens)
        model: override the model (default: OPENAI_MODEL env var)
        reasoning_effort: override the effort (default: OPENAI_REASONING_EFFORT env var, "" = don't send)

    Returns:
        str: the model's answer, stripped

    Raises:
        openai.OpenAIError (use describe_openai_error() to show it) or OpenAIEmptyResponseError
    """
    client = get_openai_client()
    model = model or get_text_model()
    effort = get_reasoning_effort() if reasoning_effort is _USE_ENV else (reasoning_effort or "")

    params = {
        "model": model,
        "messages": messages,
        # NOTE: no temperature - reasoning models reject it
        "max_completion_tokens": max_completion_tokens,
    }
    if effort and model not in _models_without_reasoning_effort:
        params["reasoning_effort"] = effort

    try:
        response = client.chat.completions.create(**params)
    except openai.BadRequestError as e:
        # Some models don't know reasoning_effort -> try once more without it
        if "reasoning_effort" in params and _mentions_reasoning_effort(e):
            print(f"⚠️ Model '{model}' rejected reasoning_effort='{effort}', retrying without it ({describe_openai_error(e)})")
            _models_without_reasoning_effort.add(model)
            params.pop("reasoning_effort")
            response = client.chat.completions.create(**params)
        else:
            raise

    choice = response.choices[0]
    text = (choice.message.content or "").strip()
    if not text:
        raise OpenAIEmptyResponseError(
            f"OpenAI returned an empty answer (finish_reason: {choice.finish_reason}, model: {model})"
        )
    return text


# ---------------------------------------------------------------------------
# Image edits
# ---------------------------------------------------------------------------

def edit_image(image_bytes: bytes, prompt: str, size: str, quality: str = None, model: str = None,
               filename: str = "photo.jpg", mime_type: str = "image/jpeg") -> str:
    """
    Re-draw a photo following the prompt (OpenAI images edit endpoint).

    Returns:
        str: the new image as base64-encoded JPEG
    """
    if get_image_api() == "chat":
        return _edit_image_with_chat(image_bytes, prompt, model=model, mime_type=mime_type)

    client = get_openai_client()
    result = client.images.edit(
        model=model or get_image_model(),
        image=(filename, image_bytes, mime_type),
        prompt=prompt,
        size=size,
        quality=quality or get_image_quality(),
        output_format="jpeg",
        output_compression=85,
        timeout=IMAGE_TIMEOUT_SECONDS,
    )

    data = getattr(result, "data", None) or []
    image_b64 = getattr(data[0], "b64_json", None) if data else None
    if not image_b64:
        raise OpenAIEmptyResponseError("OpenAI returned no image data")
    return image_b64


def _edit_image_with_chat(image_bytes: bytes, prompt: str, model: str = None, mime_type: str = "image/jpeg") -> str:
    """
    Same as edit_image(), but through a chat model that answers with an image
    (OpenRouter style: the picture comes back in message.images as a data URL).
    """
    client = get_openai_client()
    photo_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    response = client.chat.completions.create(
        model=model or get_image_model(),
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt + "\nKeep the photo's framing and aspect ratio. Answer with the edited image."},
                {"type": "image_url", "image_url": {"url": photo_url}},
            ],
        }],
        extra_body={"modalities": ["image", "text"]},
        timeout=IMAGE_TIMEOUT_SECONDS,
    )

    message = response.choices[0].message
    for image in getattr(message, "images", None) or []:
        url = (image.get("image_url") or {}).get("url", "") if isinstance(image, dict) else ""
        if url.startswith("data:") and "," in url:
            return _to_jpeg_base64(base64.b64decode(url.split(",", 1)[1]))
    raise OpenAIEmptyResponseError(f"The image model returned no image (model: {model or get_image_model()})")


def _to_jpeg_base64(image_bytes: bytes) -> str:
    """Chat image models often answer with PNG; the frontend expects JPEG"""
    from PIL import Image  # only needed here

    with Image.open(io.BytesIO(image_bytes)) as img:
        out = io.BytesIO()
        img.convert("RGB").save(out, format="JPEG", quality=85)
    return base64.b64encode(out.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------
# JSON answers
# ---------------------------------------------------------------------------

def parse_json_object(text: str) -> dict:
    """
    Parse a JSON object out of a model answer.
    Handles answers wrapped in ```json ... ``` fences and extra words around the JSON.

    Raises:
        ValueError if no JSON object can be found
    """
    if text is None:
        raise ValueError("Empty answer")

    cleaned = text.strip()

    # Remove markdown code fences like ```json ... ```
    fence = re.search(r"```(?:json|JSON)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Fall back to the first {...} object found in the text
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", cleaned):
        try:
            parsed, _ = decoder.raw_decode(cleaned[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise ValueError(f"No JSON object found in answer: {text[:200]}")
