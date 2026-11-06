"""
Image helpers for photo "translation" (POST /translate-image)

- detect_image_mime(): checks the first bytes of the file (magic bytes), not the file name
- prepare_image(): fixes phone rotation, shrinks big photos, re-encodes to JPEG
                   (this also drops EXIF data such as GPS location before anything leaves the server)
- choose_output_size(): landscape / portrait / square -> OpenAI image size
- build_image_prompt(): what we ask the AI
- get_client_ip(): who is asking (for rate limits)
- ImageRateLimiter: limits that protect the OpenAI bill
- count_on_utc_day(): counts today's "image_converted" rows (so the daily limit survives restarts)

Images are only kept in memory while a request runs - nothing is stored.
"""

import base64
import datetime
import io
import json
import os
import threading
import time
from collections import deque

MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB

# Longest side we send to OpenAI (bigger photos are shrunk - faster uploads, same result)
MAX_SIDE_PIXELS = 1536

# Biggest photo we agree to open, in megapixels (width x height / 1,000,000), per file type.
# Opening a photo needs far more memory than the file size: a tiny, very compressible file can claim to be
# 20000x20000 pixels and need gigabytes (a "decompression bomb"), and the server only has 1 GB.
# So we check this from the file header BEFORE decoding. Measured peak memory while opening:
# JPEG ~5-6 bytes per pixel, PNG ~9, WEBP ~20 (Pillow decodes WEBP through several full-size copies).
# These limits keep one photo under ~300 MB and still allow normal phone photos (12-50 MP JPEGs).
MAX_INPUT_MEGAPIXELS = {
    "JPEG": 50,
    "PNG": 32,
    "WEBP": 16,
}

# Opening a big photo briefly needs a few hundred MB of memory, so only one photo is decoded at a time
# (it takes well under a second). Like a "one at a time" sign on a small room.
_decode_lock = threading.Lock()

# How much wider than tall (or taller than wide) a photo must be to count as landscape (or portrait)
ORIENTATION_TOLERANCE = 1.1

DEFAULT_RATE_LIMIT_PER_HOUR = 6
DEFAULT_DAILY_LIMIT = 100

_PILLOW_FORMATS = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}


class InvalidImageError(ValueError):
    """The upload is not a usable JPEG / PNG / WEBP image"""


class PreparedImage:
    """A cleaned-up photo ready to send to OpenAI"""

    def __init__(self, jpeg_bytes: bytes, width: int, height: int):
        self.jpeg_bytes = jpeg_bytes
        self.width = width
        self.height = height

    @property
    def data_url(self) -> str:
        return "data:image/jpeg;base64," + base64.b64encode(self.jpeg_bytes).decode("ascii")


def detect_image_mime(data: bytes):
    """
    Look at the first bytes of the file to see what it really is.
    Returns "image/jpeg", "image/png", "image/webp" or None.
    """
    if not data:
        return None
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def choose_output_size(width: int, height: int) -> str:
    """landscape -> 1536x1024, portrait -> 1024x1536, (roughly) square -> 1024x1024"""
    if width >= height * ORIENTATION_TOLERANCE:
        return "1536x1024"
    if height >= width * ORIENTATION_TOLERANCE:
        return "1024x1536"
    return "1024x1024"


def prepare_image(data: bytes, mime_type: str) -> PreparedImage:
    """
    Open the upload with Pillow (only as the type its magic bytes claim), rotate it the way the
    phone meant it, shrink it, and re-encode as JPEG.

    Raises:
        InvalidImageError if the file can't be decoded
    """
    from PIL import Image, ImageOps

    pillow_format = _PILLOW_FORMATS.get(mime_type)
    if not pillow_format:
        raise InvalidImageError("Unsupported image type. Please upload a JPEG, PNG or WEBP photo.")

    try:
        with _decode_lock:
            # open() only reads the file header - no pixels are decoded yet
            image = Image.open(io.BytesIO(data), formats=[pillow_format])

            # Refuse "decompression bombs" before they are decoded (see MAX_INPUT_MEGAPIXELS)
            declared_width, declared_height = image.size
            max_megapixels = MAX_INPUT_MEGAPIXELS[pillow_format]
            if declared_width * declared_height > max_megapixels * 1_000_000:
                print(f"🚫 Refusing a {declared_width}x{declared_height} {pillow_format} (limit {max_megapixels} MP)")
                raise InvalidImageError(
                    f"This image has too many pixels ({declared_width}x{declared_height}). "
                    f"Please upload a photo under {max_megapixels} megapixels."
                )

            if pillow_format == "JPEG":
                # Let the JPEG decoder skip detail we would throw away anyway (saves memory)
                image.draft("RGB", (MAX_SIDE_PIXELS * 2, MAX_SIDE_PIXELS * 2))

            # Phones store portrait photos sideways + a rotation tag.
            # in_place=True: rotate the photo itself instead of making a full-size copy (saves memory)
            ImageOps.exif_transpose(image, in_place=True)
            image.load()

            # Transparent PNG/WEBP -> put it on a white background (JPEG has no transparency)
            if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                if image.mode != "RGBA":
                    image = image.convert("RGBA")
                background = Image.new("RGB", image.size, (255, 255, 255))
                background.paste(image, mask=image.getchannel("A"))
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")

            image.thumbnail((MAX_SIDE_PIXELS, MAX_SIDE_PIXELS))
            width, height = image.size

            output = io.BytesIO()
            image.save(output, format="JPEG", quality=90)
    except InvalidImageError:
        raise
    except Image.DecompressionBombError as e:
        # Pillow's own safety net for absurdly large images (it refuses them while reading the header)
        print(f"🚫 Refusing an image Pillow calls a decompression bomb: {e}")
        raise InvalidImageError("This image has too many pixels. Please upload a smaller photo.")
    except Exception as e:
        print(f"❌ Could not decode uploaded image: {e}")
        raise InvalidImageError("We couldn't read this image. Please try another JPEG, PNG or WEBP photo.")

    if width < 1 or height < 1:
        raise InvalidImageError("This image is empty.")

    return PreparedImage(output.getvalue(), width, height)


def build_image_prompt(target_form: str, style_description: str = "", source_form: str = "") -> str:
    """The instructions for the image model: 'translate' the photo into the target form"""
    lines = [
        f"Re-imagine this photo \"translated\" into the form: {json.dumps(target_form, ensure_ascii=False)}.",
    ]
    if style_description:
        lines.append(f"About this form: {style_description}")
    if source_form:
        lines.append(f"The photo currently reads as: {json.dumps(source_form, ensure_ascii=False)}.")
    lines += [
        "Translate the photo the way a good translator translates a text: keep the meaning, the subject, "
        "the composition and what is happening recognizable, but express all of it through the visual "
        "language, mood, materials, colors and conventions of that form.",
        "Do not add any text, letters, captions, logos or watermarks, unless the form itself is inherently "
        "made of text (for example a meme, a comic strip, a poster or a newspaper page).",
        "Treat any people in the photo respectfully: keep them recognizable as themselves, never grotesque or sexualized.",
    ]
    return "\n".join(lines)


def get_client_ip(headers, remote_addr) -> str:
    """
    Who is calling? On Fly.io the real visitor IP is in the Fly-Client-IP header.
    Otherwise use the first hop of X-Forwarded-For, otherwise the direct connection address.
    """
    fly_ip = (headers.get("Fly-Client-IP") or "").strip()
    if fly_ip:
        return fly_ip

    forwarded = (headers.get("X-Forwarded-For") or "").strip()
    if forwarded:
        first_hop = forwarded.split(",")[0].strip()
        if first_hop:
            return first_hop

    return remote_addr or "unknown"


def _env_int(name: str, default: int) -> int:
    value = (os.environ.get(name) or "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        print(f"⚠️ {name}='{value}' is not a whole number, using default {default}")
        return default


def get_rate_limit_per_hour() -> int:
    return _env_int("IMAGE_RATE_LIMIT_PER_HOUR", DEFAULT_RATE_LIMIT_PER_HOUR)


def get_daily_limit() -> int:
    return _env_int("IMAGE_DAILY_LIMIT", DEFAULT_DAILY_LIMIT)


class RateLimitDecision:
    def __init__(self, allowed: bool, retry_after_seconds: int = 0, message: str = ""):
        self.allowed = allowed
        self.retry_after_seconds = retry_after_seconds
        self.message = message


class ImageRateLimiter:
    """
    Simple limits, counted in memory (each Fly machine counts on its own):
    - per visitor: IMAGE_RATE_LIMIT_PER_HOUR photo translations in any rolling hour (default 6)
    - whole site:  IMAGE_DAILY_LIMIT photo translations per UTC day (default 100; 0 pauses the feature)
    Fly stops the machine when nobody visits for a few minutes, and that wipes this memory. So the
    site-wide count for today is loaded once from the sheet after every restart (seed_daily_count).
    The per-visitor hour simply starts fresh after a restart.
    This is like keeping a dict of {ip: [timestamps]} in a Python script.
    """

    WINDOW_SECONDS = 3600

    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._hits = {}          # client id -> deque of timestamps
        self._day = None         # current UTC date
        self._day_count = 0
        self._seeded_day = None  # the UTC date whose earlier count was already loaded from the sheet

    def _utc_day(self, now: float):
        return datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc).date()

    def _seconds_until_utc_midnight(self, now: float) -> int:
        current = datetime.datetime.fromtimestamp(now, tz=datetime.timezone.utc)
        tomorrow = (current + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(1, int((tomorrow - current).total_seconds()) + 1)

    def _roll_day(self, now: float):
        """New UTC day -> reset the site-wide counter (call with the lock held). Returns today's date."""
        today = self._utc_day(now)
        if self._day != today:
            self._day = today
            self._day_count = 0
        return today

    def _blocked(self, client_id: str, now: float, per_hour: int, per_day: int):
        """A RateLimitDecision saying why this client must wait, or None if they may go (call with the lock held)"""
        self._roll_day(now)

        if self._day_count >= per_day:
            return RateLimitDecision(
                False,
                self._seconds_until_utc_midnight(now),
                "Photo translations have reached today's limit for the whole site. Please come back tomorrow 🌱",
            )

        stamps = self._hits.get(client_id) or deque()
        while stamps and stamps[0] <= now - self.WINDOW_SECONDS:
            stamps.popleft()

        if len(stamps) >= per_hour:
            if stamps:
                retry_after = max(1, int(stamps[0] + self.WINDOW_SECONDS - now) + 1)
            else:
                retry_after = self.WINDOW_SECONDS
            minutes = max(1, (retry_after + 59) // 60)
            return RateLimitDecision(
                False,
                retry_after,
                f"You've reached the limit of {per_hour} photo translations per hour. "
                f"Please try again in about {minutes} minute{'s' if minutes != 1 else ''}.",
            )
        return None

    def check(self, client_id: str) -> RateLimitDecision:
        """Only LOOK whether this client may go (nothing is counted) - used before doing any heavy work"""
        per_hour = get_rate_limit_per_hour()
        per_day = get_daily_limit()

        with self._lock:
            blocked = self._blocked(client_id, self._clock(), per_hour, per_day)
        return blocked or RateLimitDecision(True)

    def try_acquire(self, client_id: str) -> RateLimitDecision:
        """Count one photo translation for this client, or say how long to wait"""
        per_hour = get_rate_limit_per_hour()
        per_day = get_daily_limit()

        with self._lock:
            now = self._clock()
            blocked = self._blocked(client_id, now, per_hour, per_day)
            if blocked:
                return blocked

            self._hits.setdefault(client_id, deque()).append(now)
            self._day_count += 1
            self._forget_idle_clients(now)
            return RateLimitDecision(True)

    def seed_daily_count(self, load_count):
        """
        After a restart, start today's site-wide count from what was already done earlier today,
        so IMAGE_DAILY_LIMIT really means "per UTC day" even though Fly restarts the server often.

        load_count(utc_date) -> int is called once after each server start (and again on each new UTC day).
        If it fails we just keep counting from memory (a photo translation never fails because of this).
        """
        with self._lock:
            today = self._roll_day(self._clock())
            if self._seeded_day == today:
                return
            # Mark it first, so other requests don't load it again at the same moment
            self._seeded_day = today

        try:
            earlier_today = int(load_count(today))
        except Exception as e:
            print(f"⚠️ Could not load today's photo translation count, counting from memory only: {e}")
            return

        with self._lock:
            if self._day == today:
                # max(): the sheet may already include translations this server did itself
                self._day_count = max(self._day_count, earlier_today)
                print(f"📊 Photo translations already done today (UTC): {earlier_today}")

    def release(self, client_id: str):
        """Give the slot back (used when OpenAI failed on its side, so that failed try doesn't count)"""
        with self._lock:
            stamps = self._hits.get(client_id)
            if stamps:
                stamps.pop()
            if self._day_count > 0:
                self._day_count -= 1

    def reset(self):
        with self._lock:
            self._hits.clear()
            self._day = None
            self._day_count = 0
            self._seeded_day = None

    def _forget_idle_clients(self, now: float):
        """Keep memory small: drop visitors with no photo translations in the last hour"""
        if len(self._hits) < 1000:
            return
        cutoff = now - self.WINDOW_SECONDS
        for client_id in list(self._hits):
            stamps = self._hits[client_id]
            if not stamps or stamps[-1] <= cutoff:
                del self._hits[client_id]


def count_on_utc_day(timestamps, day) -> int:
    """
    How many of these ISO datetime texts fall on the given UTC date (used to seed the daily limit).
    Texts without a timezone (what datetime.now().isoformat() writes) are the server's local time -
    on Fly.io that is UTC. Texts that aren't dates are skipped.
    """
    count = 0
    for text in timestamps:
        try:
            moment = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.astimezone()  # no timezone written -> it's the server's local time
        if moment.astimezone(datetime.timezone.utc).date() == day:
            count += 1
    return count


# One shared limiter for the whole app
image_rate_limiter = ImageRateLimiter()
