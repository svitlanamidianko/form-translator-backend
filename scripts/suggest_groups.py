#!/usr/bin/env python3
"""
Suggest semantic groups for forms that don't have one yet (column E of the "forms" tab).

It reads the forms tab, asks the AI to sort every form WITHOUT a group into one of your
existing groups (or, when several forms clearly share a theme, into a few new short
lowercase groups like "workplace", "nature", "internet culture", "voices & moods"),
and prints a table.

Dry run by default - nothing is written:
    python scripts/suggest_groups.py

Write the suggestions into column E (ONLY column E, only cells that are still empty):
    python scripts/suggest_groups.py --apply

Other options:
    --no-new-groups          only use groups that already exist
    --max-new-groups 4       how many new groups the AI may invent (default 6)
    --model gpt-5.6-terra    use a stronger model for this one-off job (default: OPENAI_MODEL or gpt-5.6-luna)
    --effort medium          reasoning effort for this run (default: OPENAI_REASONING_EFFORT or low)

Needs the same setup as running the backend locally:
    - OPENAI_API_KEY in .env (or in your environment)
    - Google credentials: "Form Translator DB IAM.json" in the project folder
      (or GOOGLE_APPLICATION_CREDENTIALS / GOOGLE_APPLICATION_CREDENTIALS_JSON)

Rows that still have the old TRUE flag in column E (visitor forms added by the old backend)
are skipped, because writing a group there would erase that flag. To group them: move TRUE
to column F by hand, then run this script again.
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import forms_catalog  # noqa: E402
import openai_service  # noqa: E402
from sheets_service import SheetsService, clean_form_name, is_bool_like, is_true_like, normalize_form_name  # noqa: E402

# Same sheet as routes/api_routes.py
SHEET_ID = "1sEnmusmz4X_18emilcsLFIn48nwM6qInLgQKN2rXC5M"

MAX_NEW_GROUP_LENGTH = 40


def find_candidates(rows):
    """
    Forms that need a group: empty column E, a real name, not "neutral"/"detect"/"custom".
    Rows with the old TRUE flag in column E are returned separately (we don't overwrite them).

    Returns:
        (candidates, skipped_true_rows) - lists of row dicts
    """
    candidates, skipped = [], []
    for row in rows:
        name = clean_form_name(row.get('form_name'))
        if not name or forms_catalog.is_reserved_form_name(name):
            continue
        if row.get('group'):
            continue
        if row.get('_raw_e_is_flag'):
            skipped.append(row)
            continue
        candidates.append(row)
    return candidates, skipped


def e_cell_is_free(cell) -> bool:
    """Column E may get a group: it's empty (or just FALSE, which carries no information)"""
    return str(cell or "").strip() == "" or (is_bool_like(cell) and not is_true_like(cell))


def read_rows(service):
    """All rows of the forms tab + the raw column E text (to spot the old TRUE flags)"""
    rows = service.get_all_forms("forms", include_user_submitted=True, raise_errors=True)
    raw = service.service.spreadsheets().values().get(
        spreadsheetId=service.sheet_id, range="forms!E:E"
    ).execute().get('values', [])
    for row in rows:
        index = row['row_number'] - 1
        cell = raw[index][0] if index < len(raw) and raw[index] else ""
        row['_raw_e'] = cell
        row['_raw_e_is_flag'] = is_true_like(cell)
    return rows


def group_examples(rows, groups, per_group=6):
    """{group: [a few member names]} so the AI understands what each of her groups means"""
    examples = {group: [] for group in groups}
    by_key = {normalize_form_name(group): group for group in groups}
    for row in rows:
        group = by_key.get(normalize_form_name(row.get('group')))
        if group and len(examples[group]) < per_group:
            examples[group].append(clean_form_name(row.get('form_name')))
    return examples


def build_prompt(candidates, examples, allow_new_groups, max_new_groups):
    groups_text = "\n".join(
        f"- {json.dumps(group, ensure_ascii=False)} (e.g. {', '.join(members) or 'no members yet'})"
        for group, members in examples.items()
    ) or "(none yet)"

    seen = set()
    forms_lines = []
    for row in candidates:
        name = clean_form_name(row.get('form_name'))
        if normalize_form_name(name) in seen:
            continue
        seen.add(normalize_form_name(name))
        details = [part for part in (row.get('description'), row.get('ukrainian_name')) if part]
        forms_lines.append(f"- {json.dumps(name, ensure_ascii=False)}" + (f": {' / '.join(details)}" if details else ""))

    if allow_new_groups:
        new_group_rule = (
            f"- You MAY create up to {max_new_groups} new groups, but only when at least 2 forms clearly share a theme "
            "that no existing group covers. New group names are short, lowercase, 1-3 words "
            "(for example \"workplace\", \"nature\", \"internet culture\", \"voices & moods\")."
        )
    else:
        new_group_rule = "- Do NOT create new groups."

    return f"""Form Translator is a playful website that translates text between "forms" of expression
(ontologies, voices, styles, people, ideologies, levels of difficulty...). Its owner sorts forms into semantic groups.

Existing groups (with example members):
{groups_text}

Forms without a group:
{chr(10).join(forms_lines)}

Rules:
- Prefer an existing group whenever it fits; copy its name exactly.
{new_group_rule}
- If nothing fits well, use an empty string "".
- Every form listed above must appear exactly once in your answer, spelled exactly as given.

Respond in this exact JSON format:
{{"assignments": [{{"form": "<form name>", "group": "<group or empty string>"}}]}}"""


def ask_ai(candidates, examples, allow_new_groups=True, max_new_groups=6, model=None, effort=None):
    """One AI call for all ungrouped forms. Returns {normalized form name: proposed group}"""
    prompt = build_prompt(candidates, examples, allow_new_groups, max_new_groups)
    kwargs = {}
    if effort is not None:
        kwargs['reasoning_effort'] = effort
    answer = openai_service.chat_completion(
        [
            {"role": "system", "content": "You organize a catalog of writing forms into tidy semantic groups. Always respond with valid JSON."},
            {"role": "user", "content": prompt},
        ],
        max_completion_tokens=8000,
        model=model,
        **kwargs
    )
    data = openai_service.parse_json_object(answer)
    proposals = {}
    for item in data.get('assignments') or []:
        if not isinstance(item, dict):
            continue
        key = normalize_form_name(item.get('form'))
        if key:
            proposals[key] = str(item.get('group') or "").strip()
    return proposals


def clean_proposals(proposals, existing_groups, allow_new_groups=True, max_new_groups=6):
    """
    Keep only sensible answers:
    - existing groups are mapped to her exact spelling
    - new groups must be short, lowercase, used by 2+ forms, and at most max_new_groups of them
    - "hidden" is never proposed by this script (hiding is her decision)

    Returns:
        ({normalized form name: group or ""}, [new group names])
    """
    existing = {normalize_form_name(group): group for group in existing_groups}
    cleaned = {}
    new_counts = {}

    for key, group in proposals.items():
        group_key = normalize_form_name(group)
        if not group_key or group_key == forms_catalog.HIDDEN_GROUP:
            cleaned[key] = ""
        elif group_key in existing:
            cleaned[key] = existing[group_key]
        elif allow_new_groups and len(group_key) <= MAX_NEW_GROUP_LENGTH:
            cleaned[key] = group_key  # lowercase, single spaces
            new_counts[group_key] = new_counts.get(group_key, 0) + 1
        else:
            cleaned[key] = ""

    # New groups need at least 2 members, and we keep only the biggest few
    keep = [group for group, count in sorted(new_counts.items(), key=lambda item: -item[1]) if count >= 2][:max_new_groups]
    for key, group in cleaned.items():
        if group in new_counts and group not in keep:
            cleaned[key] = ""

    return cleaned, keep


def print_table(candidates, cleaned, new_groups, skipped):
    print()
    print(f"{'row':>4}  {'form':<32} {'suggested group':<36} note")
    print(f"{'-' * 4}  {'-' * 32} {'-' * 36} {'-' * 10}")
    for row in candidates:
        name = clean_form_name(row.get('form_name'))
        group = cleaned.get(normalize_form_name(name), "")
        note = "NEW group" if group in new_groups else ("" if group else "(no good fit)")
        print(f"{row['row_number']:>4}  {name[:32]:<32} {group[:36]:<36} {note}")

    if skipped:
        print()
        print("Skipped (column E still holds the old TRUE flag - move TRUE to column F by hand to group these):")
        for row in skipped:
            print(f"{row['row_number']:>4}  {clean_form_name(row.get('form_name'))}")

    if new_groups:
        print()
        print("New groups proposed: " + ", ".join(new_groups))
    print()


def apply_suggestions(service, candidates, cleaned):
    """Write the groups into column E - re-checking each row first, so nothing she changed meanwhile is overwritten"""
    fresh = {row['row_number']: row for row in read_rows(service)}
    updates = {}
    for row in candidates:
        group = cleaned.get(normalize_form_name(row.get('form_name')), "")
        if not group:
            continue
        current = fresh.get(row['row_number'])
        if not current or normalize_form_name(current.get('form_name')) != normalize_form_name(row.get('form_name')):
            print(f"⚠️ Row {row['row_number']} changed since we read it, skipping")
            continue
        if not e_cell_is_free(current.get('_raw_e')):
            print(f"⚠️ Row {row['row_number']} already has something in column E now, skipping")
            continue
        updates[row['row_number']] = group

    if not updates:
        print("Nothing to write.")
        return 0
    return service.update_form_groups(updates)


def main(argv=None, service=None):
    parser = argparse.ArgumentParser(description="Suggest semantic groups (column E) for forms without one.")
    parser.add_argument("--apply", action="store_true", help="write the suggestions into column E")
    parser.add_argument("--no-new-groups", action="store_true", help="only use groups that already exist")
    parser.add_argument("--max-new-groups", type=int, default=6, help="how many new groups the AI may create")
    parser.add_argument("--model", default=None, help="OpenAI model for this run (default: OPENAI_MODEL)")
    parser.add_argument("--effort", default=None, help="reasoning effort for this run (default: OPENAI_REASONING_EFFORT)")
    parser.add_argument("--sheet-id", default=SHEET_ID, help="Google Sheet id")
    args = parser.parse_args(argv)

    if service is None:
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(ROOT, ".env"))
        except ImportError:
            pass
        # The credentials file name is relative to the project folder
        os.chdir(ROOT)
        service = SheetsService()
        service.set_sheet_id(args.sheet_id)

    rows = read_rows(service)
    existing_groups = forms_catalog.distinct_groups(rows)
    candidates, skipped = find_candidates(rows)

    print(f"📋 {len(rows)} rows, {len(existing_groups)} existing groups: {', '.join(existing_groups) or '-'}")
    print(f"🔎 {len(candidates)} forms without a group")
    if not candidates:
        print_table([], {}, [], skipped)
        print("✨ Every form already has a group.")
        return 0

    allow_new = not args.no_new_groups
    print(f"🤖 Asking {args.model or openai_service.get_text_model()} for suggestions...")
    proposals = ask_ai(candidates, group_examples(rows, existing_groups), allow_new, args.max_new_groups,
                       model=args.model, effort=args.effort)
    cleaned, new_groups = clean_proposals(proposals, existing_groups, allow_new, args.max_new_groups)

    print_table(candidates, cleaned, new_groups, skipped)

    if not args.apply:
        print("🧪 Dry run - nothing was written. Run again with --apply to write these groups into column E.")
        return 0

    written = apply_suggestions(service, candidates, cleaned)
    print(f"✅ Wrote {written} groups into column E.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"❌ {openai_service.describe_openai_error(error)}")
        sys.exit(1)
