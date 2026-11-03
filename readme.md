# Form Translator Backend

## Setup Instructions

### 1. Environment Setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Environment Variables
Create a `.env` file in the project root with:
```
OPENAI_API_KEY=your_openai_api_key_here
SECRET_KEY=your_flask_secret_key_here
DEBUG=False
```

### 3. Google Sheets Credentials
- Place your Google Service Account JSON file in the project root as `Form Translator DB IAM.json`
- Or set the `GOOGLE_APPLICATION_CREDENTIALS` environment variable to point to your credentials file

### 4. Run the Application
```bash
python app.py
```

## Security Notes
- Never commit credentials files to Git
- Use environment variables for sensitive data
- The `.gitignore` file excludes all credential files


## OpenAI settings (environment variables)
All of these have sensible defaults - only `OPENAI_API_KEY` is required.

| Variable | Default | What it does |
|---|---|---|
| `OPENAI_API_KEY` | - | your OpenAI key |
| `OPENAI_MODEL` | `gpt-5.6-luna` | text model (translate, detect form, auto-grouping, photo captions). `gpt-5.6-terra` is stronger but ~10x the price |
| `OPENAI_REASONING_EFFORT` | `low` | `none` / `low` / `medium` / `high` / `xhigh` / `max`. Set it to `off` (or empty) for models without reasoning |
| `OPENAI_IMAGE_MODEL` | `gpt-image-2.5-flare` | model for photo "translation" |
| `OPENAI_IMAGE_QUALITY` | `low` | `low` / `medium` / `high` / `xhigh` / `max` / `auto` (higher = slower + pricier) |
| `IMAGE_RATE_LIMIT_PER_HOUR` | `6` | photo translations per visitor (IP) per rolling hour |
| `IMAGE_DAILY_LIMIT` | `100` | photo translations for the whole site per UTC day (`0` pauses the feature) |

The per-visitor limit is counted in memory, so it starts fresh whenever the server restarts (Fly stops the machine after a few idle minutes and starts it again on the next visit). The daily limit survives restarts: after each start the backend counts today's `image_converted` rows in the `interest_registered` tab and continues from there. Each Fly machine counts on its own.
A photo that OpenAI refuses (safety block) still counts toward the limits; OpenAI outages and errors on our side don't.

### Setting them on Fly.io
```bash
flyctl secrets set OPENAI_MODEL=gpt-5.6-luna OPENAI_REASONING_EFFORT=low
flyctl secrets set OPENAI_IMAGE_MODEL=gpt-image-2.5-flare OPENAI_IMAGE_QUALITY=low
flyctl secrets set IMAGE_RATE_LIMIT_PER_HOUR=6 IMAGE_DAILY_LIMIT=100
flyctl secrets list   # names only, values stay secret
```
(`flyctl secrets set` restarts the app with the new values.)

## The "forms" tab in the Google Sheet
The header row says something slightly different, but this is what the backend reads (the header row itself is never rewritten):

| Column | Meaning |
|---|---|
| A | id |
| B | form name |
| C | Ukrainian name (shown as "name - Ukrainian name" in the website's list) |
| D | English description of the style (given to the AI when translating into / from this form) |
| E | **semantic group** (e.g. `religion`, `person`, `political ideology`) - forms with the same group are shown together |
| F | `TRUE` = added by a visitor |

- **Hide a form** from the website: type `hidden` in column E. It disappears from the list but still works if someone types it.
- New forms typed by visitors are added automatically as `id | name | | | group | TRUE`. The group is picked by the AI from your existing groups (empty if nothing fits, `hidden` if the name is nasty or looks like private data). You can always change it by hand.
- Old visitor rows written before this change have `TRUE` in column E (and no group). They still count as visitor forms.
- Duplicates (`gen z` / `Gen Z ` / `socialism ` with a trailing space) are shown once - your own row wins over a visitor's. `neutral`, `detect` and `custom` are never shown.

### Suggesting groups for forms that don't have one
```bash
python scripts/suggest_groups.py            # dry run: prints a table, writes nothing
python scripts/suggest_groups.py --apply    # writes the groups into column E (only empty E cells, nothing else)
python scripts/suggest_groups.py --no-new-groups          # only use groups you already have
python scripts/suggest_groups.py --model gpt-5.6-terra    # stronger model for this one-off job
```
It uses your `.env` (`OPENAI_API_KEY`) and the Google credentials file, like the app. It prefers your existing groups and may propose a few new short ones (like "workplace", "nature", "internet culture") when several forms clearly share a theme.

## Photo translation: `POST /translate-image`
`multipart/form-data` with `image` (JPEG / PNG / WEBP, max 10 MB), `targetForm` (required) and `sourceForm` (optional).
Returns `{"imageBase64", "mimeType": "image/jpeg", "caption", "targetForm", "timestamp"}`.
Errors: `400` bad input, `413` too large, `429` rate limited (with `retryAfterSeconds`), `500` with the real reason.
Photos are never stored. Each successful conversion is counted in the `interest_registered` tab as `image_converted`.
To protect the server's memory (1 GB), photos above 50 megapixels (JPEG), 32 MP (PNG) or 16 MP (WEBP) are refused with a `400`. `targetForm` / `sourceForm` can be at most 200 characters.

## Running the tests
No internet or keys needed (Google Sheets and OpenAI are faked):
```bash
pip install -r requirements-dev.txt
python -m pytest
```
