"""
Forms catalog helpers

Turns the raw rows of the "forms" tab (see sheets_service.py for the column layout)
into what the website needs:
- build_forms_payload(): the /forms response - de-duplicated, in sheet order, with groups
- find_form(): look up a form by name, ignoring letter case and extra spaces
- prompt_description(): the description we give the AI for a form
- pick_semantic_group(): one cheap AI call that puts a visitor-added form into one of her groups
"""

import json

from sheets_service import normalize_form_name, clean_form_name
import openai_service

# Names that are never shown in the list and never written to the sheet
# ("neutral" was a detection fallback, "detect"/"custom" are UI options, not forms)
RESERVED_FORM_NAMES = {"neutral", "detect", "custom"}

# Typing this into column E hides a form from the website (it still works for translation)
HIDDEN_GROUP = "hidden"


def is_reserved_form_name(name) -> bool:
    return normalize_form_name(name) in RESERVED_FORM_NAMES


def is_hidden_group(group) -> bool:
    return normalize_form_name(group) == HIDDEN_GROUP


def _kept_rows(rows):
    """
    Pick ONE row per form name (ignoring case / extra spaces):
    the first row she added by hand (not user_submitted) if there is one, otherwise the first row.

    Returns:
        list of (order, row) sorted by sheet position; order = 0-based position of the row
    """
    chosen = {}  # normalized name -> (order, row)
    for order, row in enumerate(rows):
        key = normalize_form_name(row.get('form_name'))
        if not key or key in RESERVED_FORM_NAMES:
            continue

        if key not in chosen:
            chosen[key] = (order, row)
        elif chosen[key][1].get('user_submitted') and not row.get('user_submitted'):
            # Her own row wins over an earlier visitor-added duplicate
            chosen[key] = (order, row)

    return sorted(chosen.values(), key=lambda item: item[0])


def build_forms_payload(rows):
    """
    Build the "forms" and "groups" parts of the GET /forms response.

    Args:
        rows: all rows from SheetsService.get_all_forms(include_user_submitted=True), in sheet order

    Returns:
        (forms, groups) where
        forms  = {name: {"description", "category", "userSubmitted", "order"}} in sheet order
        groups = distinct group names in order of first appearance
    """
    forms = {}
    groups = []
    group_spelling = {}  # "religion" -> "Religion" (first spelling she used wins)

    for order, row in _kept_rows(rows):
        group = row.get('group') or ""
        if is_hidden_group(group):
            continue  # moderation lever: "hidden" in column E

        name = str(row.get('form_name') or "").strip()
        ukrainian_name = (row.get('ukrainian_name') or "").strip()

        # Keep the exact description format the website already uses
        description = f"{name} - {ukrainian_name}" if ukrainian_name else name

        category = None
        if group:
            group_key = normalize_form_name(group)
            if group_key not in group_spelling:
                group_spelling[group_key] = group
                groups.append(group)
            category = group_spelling[group_key]

        forms[name] = {
            "description": description,
            "category": category,
            "userSubmitted": bool(row.get('user_submitted')),
            "order": order,
        }

    return forms, groups


def find_form(rows, name):
    """
    Find the row for a form name, ignoring case and extra spaces ("Gen Z " finds "gen z").
    Hidden forms are found too (they still work for translation).

    Returns:
        the row dict, or None if the form is not in the sheet
    """
    key = normalize_form_name(name)
    if not key:
        return None

    found = None
    for row in rows:
        if normalize_form_name(row.get('form_name')) != key:
            continue
        if found is None:
            found = row
        elif found.get('user_submitted') and not row.get('user_submitted'):
            found = row
    return found


def prompt_description(name, row) -> str:
    """
    Description of a form for the AI prompt.
    Uses the English style description (column D) when she wrote one,
    otherwise the Ukrainian name (column C), otherwise just the name.
    """
    name = clean_form_name(name)
    if row:
        if row.get('description'):
            return f"{name} - {row['description']}"
        if row.get('ukrainian_name'):
            return f"{name} - {row['ukrainian_name']}"
    return name


def distinct_groups(rows):
    """All group names used in the sheet (first spelling wins, "hidden" left out), in sheet order"""
    groups = []
    seen = set()
    for row in rows:
        group = row.get('group') or ""
        key = normalize_form_name(group)
        if not key or key == HIDDEN_GROUP or key in seen:
            continue
        seen.add(key)
        groups.append(group)
    return groups


GROUP_PICKER_SYSTEM_PROMPT = (
    "You sort the forms of a playful translation website into groups. "
    "Always respond with valid JSON."
)


def pick_semantic_group(form_name: str, groups) -> str:
    """
    Ask the AI which existing group a new visitor-added form belongs to.

    Returns:
        one of the existing groups (exact spelling), "" if nothing fits well,
        or "hidden" if the name is hateful / sexual / harassing / looks like private data.
        Never raises - on any error returns "".
    """
    try:
        groups_text = "\n".join(f"- {group}" for group in groups) or "(no groups yet)"

        prompt = f"""A visitor just added a new "form" to Form Translator, a website that translates text between forms of expression (ontologies, voices, styles, people, ideologies, levels of difficulty...).

New form name: {json.dumps(form_name, ensure_ascii=False)}

Existing groups:
{groups_text}

Rules:
- Pick the ONE existing group that fits the new form best, copied exactly as written above.
- If no group fits well, answer with an empty string "".
- Never invent a new group.
- Answer "hidden" if the name is hateful, sexual, harassing or insulting, or if it looks like private personal data (an email address, phone number, street address, ID or account number). Names of public figures, historical people, fictional characters or simply a person's name are fine.

Respond in this exact JSON format:
{{"group": "..."}}"""

        answer = openai_service.chat_completion(
            [
                {"role": "system", "content": GROUP_PICKER_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_completion_tokens=1000,
        )
        data = openai_service.parse_json_object(answer)
        picked = str(data.get("group") or "").strip()
    except Exception as e:
        print(f"⚠️ Could not pick a group for '{form_name}': {openai_service.describe_openai_error(e)}")
        return ""

    if is_hidden_group(picked):
        print(f"🙈 New form '{form_name}' was auto-hidden by the group picker")
        return HIDDEN_GROUP

    # Only accept groups that really exist (the model must not invent new ones)
    by_key = {normalize_form_name(group): group for group in groups}
    group = by_key.get(normalize_form_name(picked), "")
    print(f"🏷️ Group picked for '{form_name}': '{group}' (model said: '{picked}')")
    return group
