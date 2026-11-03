"""POST /translate-image: validation, size limits, rate limits, OpenAI calls"""

import base64
import datetime
import io
import struct
import zlib

import pytest
from PIL import Image

import image_service
from fakes import make_openai_error


def make_image(fmt="JPEG", size=(300, 200), color=(10, 120, 200), orientation=None, mode="RGB"):
    image = Image.new(mode, size, color if mode == "RGB" else color + (128,))
    output = io.BytesIO()
    kwargs = {}
    if orientation:
        exif = Image.Exif()
        exif[0x0112] = orientation  # EXIF "Orientation" tag (6 = phone held upright)
        kwargs["exif"] = exif.tobytes()
    image.save(output, format=fmt, **kwargs)
    return output.getvalue()


def png_header_only(width, height):
    """A tiny PNG file that CLAIMS to be width x height (like a "decompression bomb" - never decoded in the tests)"""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(b"\x00" * 64)) + chunk(b"IEND", b""))


def post_image(client, data=None, filename="photo.jpg", target="poetic", source=None, headers=None, **extra):
    form = dict(extra)
    if data is not None:
        form["image"] = (io.BytesIO(data), filename)
    if target is not None:
        form["targetForm"] = target
    if source is not None:
        form["sourceForm"] = source
    return client.post("/translate-image", data=form, content_type="multipart/form-data", headers=headers or {})


def test_happy_path_returns_base64_jpeg_and_caption(client, fake_openai, sheet_api, sync_background):
    response = post_image(client, make_image(), target="poetic", source="science")
    assert response.status_code == 200, response.get_data(as_text=True)
    data = response.get_json()

    assert data["mimeType"] == "image/jpeg"
    assert data["targetForm"] == "poetic"
    assert "timestamp" in data
    assert data["caption"] == 'A photo, lovingly retold in the voice of poetic 🌿'
    returned = base64.b64decode(data["imageBase64"])
    assert returned[:3] == b"\xff\xd8\xff"
    Image.open(io.BytesIO(returned)).verify()

    # The image edit call
    params = fake_openai.image_calls[-1]
    assert params["model"] == "gpt-image-2.5-flare"
    assert params["quality"] == "low"
    assert params["size"] == "1536x1024"  # landscape photo
    assert params["output_format"] == "jpeg"
    filename, sent_bytes, mime = params["image"]
    assert mime == "image/jpeg" and sent_bytes[:3] == b"\xff\xd8\xff"
    assert '"poetic"' in params["prompt"]
    assert "Image-rich, metaphor-forward lines" in params["prompt"]  # her column D description
    assert '"science"' in params["prompt"]

    # The caption call: text model + the photo as vision input
    caption_call = fake_openai.chat_calls[-1]
    assert caption_call["model"] == "gpt-5.6-luna"
    parts = caption_call["messages"][-1]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    # Interest recorded as "image_converted"
    assert sheet_api.tabs["interest_registered"][-1][1] == "image_converted"


def test_image_settings_from_env(client, fake_openai, monkeypatch):
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", "gpt-image-test")
    monkeypatch.setenv("OPENAI_IMAGE_QUALITY", "medium")
    assert post_image(client, make_image()).status_code == 200
    assert fake_openai.image_calls[-1]["model"] == "gpt-image-test"
    assert fake_openai.image_calls[-1]["quality"] == "medium"


@pytest.mark.parametrize("image_kwargs, expected_size", [
    (dict(size=(300, 200)), "1536x1024"),
    (dict(size=(200, 300)), "1024x1536"),
    (dict(size=(300, 290)), "1024x1024"),
    # Stored sideways with an EXIF rotation tag (how phones save portrait photos)
    (dict(size=(300, 200), orientation=6), "1024x1536"),
    (dict(fmt="PNG", size=(400, 200), mode="RGBA"), "1536x1024"),
    (dict(fmt="WEBP", size=(200, 400)), "1024x1536"),
])
def test_output_size_follows_orientation(client, fake_openai, image_kwargs, expected_size):
    response = post_image(client, make_image(**image_kwargs), filename="whatever.bin")
    assert response.status_code == 200, response.get_data(as_text=True)
    assert fake_openai.image_calls[-1]["size"] == expected_size


def test_custom_target_form_works_without_description(client, fake_openai):
    response = post_image(client, make_image(), target="  van  gogh ")
    assert response.status_code == 200
    assert response.get_json()["targetForm"] == "van gogh"
    assert "About this form" not in fake_openai.image_calls[-1]["prompt"]


def test_non_image_bytes_rejected_even_with_jpg_name(client, fake_openai):
    response = post_image(client, b"this is definitely not a photo" * 10, filename="cat.jpg")
    assert response.status_code == 400
    assert "JPEG, PNG or WEBP" in response.get_json()["error"]
    assert fake_openai.image_calls == []


def test_other_image_types_rejected(client):
    gif = make_image(fmt="GIF")
    response = post_image(client, gif, filename="cat.gif")
    assert response.status_code == 400


def test_broken_image_with_valid_magic_bytes_rejected(client, fake_openai):
    response = post_image(client, b"\xff\xd8\xff\xe0" + b"garbage" * 50)
    assert response.status_code == 400
    assert "couldn't read this image" in response.get_json()["error"]
    assert fake_openai.image_calls == []


def test_missing_fields(client):
    assert post_image(client, None).status_code == 400
    response = post_image(client, make_image(), target=None)
    assert response.status_code == 400
    assert "targetForm" in response.get_json()["error"]
    assert post_image(client, make_image(), target="detect").status_code == 400
    assert post_image(client, b"").status_code == 400


def test_too_large_image(client, fake_openai):
    big = b"\xff\xd8\xff" + b"\0" * image_service.MAX_IMAGE_BYTES
    response = post_image(client, big)
    assert response.status_code == 413
    assert "10 MB" in response.get_json()["error"]
    assert fake_openai.image_calls == []


def test_request_over_flask_limit_is_413_json(client):
    huge = b"\xff\xd8\xff" + b"\0" * (12 * 1024 * 1024)
    response = post_image(client, huge)
    assert response.status_code == 413
    assert "error" in response.get_json()


def test_per_client_rate_limit(client, fake_openai, monkeypatch):
    monkeypatch.setenv("IMAGE_RATE_LIMIT_PER_HOUR", "2")
    photo = make_image()
    headers = {"Fly-Client-IP": "203.0.113.7"}
    assert post_image(client, photo, headers=headers).status_code == 200
    assert post_image(client, photo, headers=headers).status_code == 200

    response = post_image(client, photo, headers=headers)
    assert response.status_code == 429
    data = response.get_json()
    assert "2 photo translations per hour" in data["error"]
    assert 0 < data["retryAfterSeconds"] <= 3601
    assert len(fake_openai.image_calls) == 2

    # Someone else is not affected
    assert post_image(client, photo, headers={"Fly-Client-IP": "198.51.100.1"}).status_code == 200
    # X-Forwarded-For first hop is used when there's no Fly header
    assert post_image(client, photo, headers={"X-Forwarded-For": "192.0.2.5, 10.0.0.1"}).status_code == 200


def test_daily_limit(client, monkeypatch):
    monkeypatch.setenv("IMAGE_DAILY_LIMIT", "2")
    photo = make_image()
    assert post_image(client, photo, headers={"Fly-Client-IP": "1.1.1.1"}).status_code == 200
    assert post_image(client, photo, headers={"Fly-Client-IP": "2.2.2.2"}).status_code == 200
    response = post_image(client, photo, headers={"Fly-Client-IP": "3.3.3.3"})
    assert response.status_code == 429
    assert 0 < response.get_json()["retryAfterSeconds"] <= 86401


def test_daily_limit_zero_pauses_feature(client, monkeypatch):
    monkeypatch.setenv("IMAGE_DAILY_LIMIT", "0")
    assert post_image(client, make_image()).status_code == 429


def test_invalid_uploads_do_not_use_up_the_limit(client, monkeypatch):
    monkeypatch.setenv("IMAGE_RATE_LIMIT_PER_HOUR", "1")
    for _ in range(3):
        assert post_image(client, b"not an image").status_code == 400
    assert post_image(client, make_image()).status_code == 200


def test_openai_failure_returns_reason_and_gives_slot_back(client, fake_openai, monkeypatch):
    monkeypatch.setenv("IMAGE_RATE_LIMIT_PER_HOUR", "1")
    fake_openai.image_errors.append(make_openai_error(500, "The image service is having a bad day"))
    response = post_image(client, make_image())
    assert response.status_code == 500
    error = response.get_json()["error"]
    assert error.startswith("Image translation failed: ")
    assert "The image service is having a bad day" in error

    # The failed try didn't count
    assert post_image(client, make_image()).status_code == 200


def test_moderation_block_is_a_400_with_reason(client, fake_openai):
    fake_openai.image_errors.append(make_openai_error(
        400, "Your request was rejected as a result of our safety system.", code="moderation_blocked"))
    response = post_image(client, make_image())
    assert response.status_code == 400
    assert "safety system" in response.get_json()["error"]


def test_caption_failure_still_returns_image(client, fake_openai):
    fake_openai.chat_errors.append(make_openai_error(429, "Rate limit reached for gpt-5.6-luna"))
    response = post_image(client, make_image())
    assert response.status_code == 200
    data = response.get_json()
    assert data["caption"] == ""
    assert data["imageBase64"]


def test_interest_tracking_failure_does_not_fail_request(client, sheet_api, sync_background):
    sheet_api.fail_ranges.add("interest_registered!A1:C1")
    response = post_image(client, make_image())
    assert response.status_code == 200


def test_rate_limiter_window_and_midnight_reset(monkeypatch):
    # 2026-10-29 23:59:00 UTC
    now = [1793318340.0]
    limiter = image_service.ImageRateLimiter(clock=lambda: now[0])
    monkeypatch.setenv("IMAGE_RATE_LIMIT_PER_HOUR", "1")
    monkeypatch.setenv("IMAGE_DAILY_LIMIT", "2")

    # Rolling hour per client
    assert limiter.try_acquire("a").allowed
    blocked = limiter.try_acquire("a")
    assert not blocked.allowed and blocked.retry_after_seconds == 3601

    # Site-wide daily limit, resets at UTC midnight
    assert limiter.try_acquire("b").allowed
    daily = limiter.try_acquire("c")
    assert not daily.allowed and daily.retry_after_seconds == 61
    now[0] += 61
    assert limiter.try_acquire("c").allowed

    # After an hour, "a" may go again
    now[0] += 3600
    assert limiter.try_acquire("a").allowed


def test_client_ip_priority():
    assert image_service.get_client_ip({"Fly-Client-IP": "1.1.1.1", "X-Forwarded-For": "2.2.2.2"}, "3.3.3.3") == "1.1.1.1"
    assert image_service.get_client_ip({"X-Forwarded-For": " 2.2.2.2 , 9.9.9.9"}, "3.3.3.3") == "2.2.2.2"
    assert image_service.get_client_ip({}, "3.3.3.3") == "3.3.3.3"


# --- Review fixes: memory, cost protection, daily limit after restarts --------

def test_decompression_bomb_is_refused_before_decoding(client, fake_openai, monkeypatch):
    # 9400 x 9400 RGBA would need ~1 GB of memory to decode; the file itself is tiny
    bomb = png_header_only(9400, 9400)
    assert len(bomb) < 200
    response = post_image(client, bomb, filename="cute.png")
    assert response.status_code == 400
    error = response.get_json()["error"]
    assert "too many pixels" in error and "9400x9400" in error and "32 megapixels" in error
    assert fake_openai.image_calls == []


@pytest.mark.parametrize("mime, size, allowed", [
    ("image/jpeg", (8160, 6120), True),     # 50 MP phone photo
    ("image/jpeg", (9000, 6000), False),    # 54 MP
    ("image/png", (6500, 4900), True),      # 32 MP
    ("image/png", (8000, 5000), False),     # 40 MP
    ("image/webp", (4000, 4000), True),     # 16 MP
    ("image/webp", (5000, 4000), False),    # 20 MP (WEBP needs the most memory to open)
])
def test_pixel_limit_depends_on_file_type(mime, size, allowed, monkeypatch):
    class ClaimsToBeThisBig:
        """Stands in for an opened (not yet decoded) image of this size"""
        def __init__(self, *args, **kwargs):
            self.size = size

        def draft(self, *args):
            pass

        def load(self):
            raise RuntimeError("decoding started")  # reaching this = the size check let it through

    monkeypatch.setattr(Image, "open", ClaimsToBeThisBig)
    with pytest.raises(image_service.InvalidImageError) as error:
        image_service.prepare_image(b"whatever", mime)
    if allowed:
        assert "couldn't read this image" in str(error.value)
    else:
        assert "too many pixels" in str(error.value)


def test_blocked_client_is_refused_before_the_photo_is_decoded(client, fake_openai, monkeypatch):
    monkeypatch.setenv("IMAGE_RATE_LIMIT_PER_HOUR", "0")
    decoded = []
    monkeypatch.setattr(image_service, "prepare_image", lambda *args: decoded.append(args))
    response = post_image(client, png_header_only(9400, 9400))
    assert response.status_code == 429
    assert "retryAfterSeconds" in response.get_json()
    assert decoded == []


def test_refused_photo_uses_up_the_slot(client, fake_openai, monkeypatch):
    # A safety block is about the visitor's photo -> it counts, so retrying can't bypass the limits
    monkeypatch.setenv("IMAGE_RATE_LIMIT_PER_HOUR", "1")
    for _ in range(3):
        fake_openai.image_errors.append(make_openai_error(
            400, "Your request was rejected as a result of our safety system.", code="moderation_blocked"))
    assert post_image(client, make_image()).status_code == 400
    assert post_image(client, make_image()).status_code == 429
    assert post_image(client, make_image()).status_code == 429
    assert len(fake_openai.image_calls) == 1


@pytest.mark.parametrize("error", [
    make_openai_error(429, "Rate limit reached for gpt-image-2.5-flare"),
    make_openai_error(404, "The model `gpt-image-old` does not exist"),
    make_openai_error(503, "The server is overloaded"),
])
def test_openai_side_failures_give_the_slot_back(client, fake_openai, monkeypatch, error):
    monkeypatch.setenv("IMAGE_RATE_LIMIT_PER_HOUR", "1")
    fake_openai.image_errors.append(error)
    assert post_image(client, make_image()).status_code == 500
    assert post_image(client, make_image()).status_code == 200


def test_form_names_have_a_length_limit(client, fake_openai):
    response = post_image(client, make_image(), target="x" * 201)
    assert response.status_code == 400
    assert "200 characters" in response.get_json()["error"]
    assert post_image(client, make_image(), target="poetic", source="y" * 201).status_code == 400
    assert fake_openai.image_calls == []
    assert post_image(client, make_image(), target="x" * 200).status_code == 200


def _interest_rows(sheet_api, *rows):
    tab = sheet_api.tabs["interest_registered"]
    for index, (what, when) in enumerate(rows, start=len(tab)):
        tab.append([str(index), what, when])


def test_daily_limit_counts_earlier_conversions_after_a_restart(client, sheet_api, monkeypatch):
    # The server was restarted (memory wiped), but the sheet remembers 2 conversions today
    monkeypatch.setenv("IMAGE_DAILY_LIMIT", "3")
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    yesterday = now_utc - datetime.timedelta(days=1)
    _interest_rows(
        sheet_api,
        ("image_converted", now_utc.isoformat()),
        ("image_converted", now_utc.astimezone().replace(tzinfo=None).isoformat()),  # what the app writes
        ("image_converted", yesterday.isoformat()),     # yesterday: doesn't count
        ("images", now_utc.isoformat()),                # a click on the images tab: doesn't count
    )
    assert post_image(client, make_image(), headers={"Fly-Client-IP": "1.1.1.1"}).status_code == 200
    response = post_image(client, make_image(), headers={"Fly-Client-IP": "2.2.2.2"})
    assert response.status_code == 429
    assert "today's limit" in response.get_json()["error"]


def test_daily_count_is_loaded_once(client, monkeypatch):
    from routes import api_routes
    loads = []
    real_loader = api_routes._count_images_converted_today
    monkeypatch.setattr(api_routes, "_count_images_converted_today", lambda day: loads.append(day) or real_loader(day))
    for _ in range(3):
        assert post_image(client, make_image()).status_code == 200
    assert len(loads) == 1


def test_daily_count_loading_failure_does_not_fail_request(client, sheet_api):
    sheet_api.fail_ranges.add("interest_registered!A:C")
    assert post_image(client, make_image()).status_code == 200


def test_seed_daily_count_takes_the_bigger_number_and_reloads_each_day(monkeypatch):
    now = [1793318340.0]  # 2026-10-29 23:59:00 UTC
    limiter = image_service.ImageRateLimiter(clock=lambda: now[0])
    monkeypatch.setenv("IMAGE_DAILY_LIMIT", "5")
    monkeypatch.setenv("IMAGE_RATE_LIMIT_PER_HOUR", "100")
    loads = []

    def load(day):
        loads.append(day)
        return 4

    limiter.seed_daily_count(load)
    limiter.seed_daily_count(load)
    assert loads == [datetime.date(2026, 10, 29)]
    assert limiter.try_acquire("a").allowed
    assert not limiter.try_acquire("b").allowed  # 4 earlier + 1 = 5

    now[0] += 120  # past UTC midnight -> new day, loaded again
    limiter.seed_daily_count(lambda day: loads.append(day) or 0)
    assert loads[-1] == datetime.date(2026, 10, 30)
    assert limiter.try_acquire("b").allowed

    # A failing loader is ignored (counting from memory)
    failing = image_service.ImageRateLimiter(clock=lambda: now[0])
    failing.seed_daily_count(lambda day: 1 / 0)
    assert failing.try_acquire("c").allowed


def test_count_on_utc_day():
    day = datetime.date(2026, 10, 30)
    stamps = [
        "2026-10-30T00:00:01+00:00",
        "2026-10-30T23:59:59Z",
        "2026-10-30T01:30:00+03:00",   # = 2026-10-29 22:30 UTC
        "2026-10-29T23:59:59+00:00",
        "not a date",
        "",
    ]
    assert image_service.count_on_utc_day(stamps, day) == 2


def test_rate_limit_check_does_not_count(monkeypatch):
    limiter = image_service.ImageRateLimiter()
    monkeypatch.setenv("IMAGE_RATE_LIMIT_PER_HOUR", "1")
    for _ in range(5):
        assert limiter.check("a").allowed
    assert limiter.try_acquire("a").allowed
    assert not limiter.check("a").allowed
