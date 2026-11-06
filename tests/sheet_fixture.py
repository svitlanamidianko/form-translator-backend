"""
Fixture data shaped exactly like the REAL "forms" tab (see the top of sheets_service.py):

    A id | B form name | C Ukrainian name | D English description | E group (or TRUE) | F user_submitted

The names, ids, Ukrainian names and descriptions mirror the live sheet (Oct 2026).
The group values in column E are realistic guesses (her real groups include
"five levels of difficulty | wired", "religion", "person", "aletheia", "philosophical",
"political ideology"). The last row ("spam test" / hidden) is synthetic.
"""

FORMS_HEADER = ["id", "form name", "form description", "category", "user_submitted", "user_submitted"]

LEVELS = "five levels of difficulty | wired"

FORMS_ROWS = [
    ["1", "ELI5", "поясни як п’ятирічній дитині", "Explain like I’m five: simple nouns and verbs, short sentences, one idea at a time.", LEVELS],
    ["2", "teen", "підлітковий", "Casual, direct, meme-aware; plain language with light slang.", LEVELS],
    ["3", "college student", "студент коледжу", "Intro-level textbook tone; clear claims + examples.", LEVELS],
    ["4", "grad student", "аспірант", "Scholarly but accessible; define terms, cite mechanisms.", LEVELS],
    ["5", "expert (PhD)", "доктор філософії (PhD)", "Field-native terminology, formal register, mechanistic precision.", LEVELS],
    ["6", "tech bro startup", "стартап у стилі «тех-бро»", "Hypey, metrics-first, product/market framing; talks in growth loops."],
    ["7", "corporate", "корпоративний", "Professional, neutral, de-risked; crisp bullets, action items."],
    ["8", "poetic", "поетичний", "Image-rich, metaphor-forward lines; musical cadence."],
    ["9", "analytical", "аналітичний", "Logical; Structured reasoning with premises → inferences → conclusion."],
    ["10", "silly", "дуркуватий", "Playful, absurd, punny; exaggeration and whimsy; keeps the core point."],
    ["11", "non-dualist", "недвоїстий", "Advaita/Dzogchen vibe; point to awareness as baseline.", "philosophical"],
    ["12", "christian", "християнський", "Pastoral tone with Scripture/liturgy idiom; virtue, grace.", "religion"],
    ["13", "hindu", "індуїстський", "Dharmic framing with Sanskrit terms (dharma, karma, moksha).", "religion"],
    ["14", "woo woo", "містично-езотеричний", "Mystical–esoteric register; energy, vibrations, manifestation."],
    ["15", "science", "науковий", "Operational definitions, mechanisms, falsifiability."],
    ["16", "nature", "природний", "Eco-metaphors and seasons; land/sky/water imagery."],
    ["17", "formal", "офіційний", "High register, complete sentences, impersonal voice."],
    ["18", "yoga teacher", "викладач йоги", "Instructional, somatic cues; Sanskrit where apt (sthira, sukha)."],
    ["20", "michael levin", "Майкл Левін", "Bioelectric/morphogenesis frame: cellular decision-making, goals.", "person"],
    ["21", "poetic attunement", "поет", "Gentle, metaphor of growth and tending; first-person.", "aletheia"],
    ["22", "technological attunement", "ІТ технології", "Systems/engineering idiom; inputs, feedback loops.", "aletheia"],
    ["23", "buddhist", "буддистський", "Four Noble Truths/8-fold path vocabulary; impermanence.", "religion"],
    ["24", "self-hate", "самоненависть", "Inner critic voice: harsh, absolutist, catastrophizing."],
    ["25", "self-love", "любов до себе", "Warm, affirming, self-compassionate; boundaries + care."],
    ["26", "objectivist", "об’єктивіст", "objectivism frame", "philosophical"],
    ["27", "subjectivist", "суб’єктивіст", "", "philosophical"],
    ["28", "islam", "іслам", "", "religion"],
    ["29", "child", "дитина", "Simple, playful language that a 6-year-old would use.", "", "TRUE"],
    ["30", "adult", "дорослий", "Professional, mature language suitable for workplace.", "", "TRUE"],
    ["31", "baby talk", "сюсюкання", "Very simple words and sounds that babies and toddlers use."],
    ["32", "professional", "діловий", "Formal business language used in corporate settings.", "", "TRUE"],
    ["33", "ayn rand", "Айн Ренд", "Objectivist philosophy with focus on individual rights.", "person", "TRUE"],
    ["34", "tree", "", "Natural, organic language like a tree would use.", "", "TRUE"],
    ["35", "flower", "", "Delicate, beautiful language like a flower.", "", "TRUE"],
    ["36", "jewish mysticism", "юдейська містика", "", "religion"],
    ["37", "profanity", "нецензурна лексика / лайка"],
    ["38", "gen z", "покоління Z"],
    ["39", "brainrot", "брейнрот"],
    ["40", "svitlana midianko", "", "", "person", "TRUE"],
    ["41", "andre vacha", "", "", "person", "TRUE"],
    ["42", "beauty girl", "", "", "", "TRUE"],
    ["43", "san francisco", "", "", "", "TRUE"],
    ["44", "communism", "комунізм", "", "political ideology"],
    ["45", "capitalism", "капіталізм", "", "political ideology"],
    ["46", "ukrainian", "", "", "", "TRUE"],
    ["47", "anarchism", "анархізм", "", "political ideology"],
    ["49", "socialism ", "соціалізм", "", "political ideology"],
    ["50", "feudalism", "феодалізм", "", "political ideology"],
    # Rows written by the old backend for visitor-added forms: TRUE landed in column E
    ["51", "neutral", "", "", "TRUE"],
    ["52", "ІТ технології", "", "", "TRUE"],
    ["53", "business", "", "", "TRUE"],
    ["54", "gen z", "", "", "TRUE"],
    ["55", "haiku"],
    ["1", "PHILOSOPHY", "", "", "TRUE"],
    ["2", "PHILOSOPHY", "", "", "TRUE"],
    ["56", "baby talk", "", "", "TRUE"],
    ["57", "calm", "", "", "TRUE"],
    ["58", "neutral", "", "", "TRUE"],
    # Synthetic: a form she hid by typing "hidden" in column E
    ["59", "spam test", "", "", "hidden", "TRUE"],
]

PROMPT_HEADER = ["id", "prompt", "data", "version"]
PROMPT_ROWS = [
    ["1", "Translate the text below from the form {source_form} ({source_description}) into {target_form} ({target_description}). "
          "Keep the meaning, change the form.\n\nText: {source_text}", "", "1"],
]

HISTORY_HEADER = ["id", "stars_count", "source_form", "source_form_id", "source_text",
                  "target_form", "target_form_id", "target_text", "datetime"]
HISTORY_ROWS = [
    ["a1b2c3d4", "5", "corporate", "", "We need to align on deliverables.", "poetic", "",
     "Let our tasks braid like rivers toward one sea.", "10/20/2026 14:08:10"],
    ["b2c3d4e5", "3", "science", "", "Plants convert light into chemical energy.", "ELI5", "",
     "Plants eat sunshine and turn it into food!", "10/21/2026 09:12:44"],
    ["c3d4e5f6", "1", "teen", "", "this lowkey slaps ngl", "formal", "",
     "This is, admittedly, rather excellent.", "10/22/2026 18:30:01"],
    ["d4e5f6a7", "0", "buddhist", "", "Everything changes.", "tech bro startup", "",
     "Impermanence is just rapid iteration at scale 🚀", "10/25/2026 11:00:00"],
    ["e5f6a7b8", "0", "silly", "", "I lost my keys again.", "michael levin", "",
     "My keys, a collective of cells, pursued their own goal state elsewhere.", "10/28/2026 20:15:33"],
]

INTEREST_HEADER = ["id", "what", "datetime"]
FEEDBACK_HEADER = ["Id", "Text", "DateTime"]


def make_tabs(extra_form_rows=None):
    """Fresh copy of all tabs (so tests can change them freely)"""
    forms = [list(FORMS_HEADER)] + [list(row) for row in FORMS_ROWS] + [list(row) for row in (extra_form_rows or [])]
    return {
        "forms": forms,
        "prompt": [list(PROMPT_HEADER)] + [list(row) for row in PROMPT_ROWS],
        "history": [list(HISTORY_HEADER)] + [list(row) for row in HISTORY_ROWS],
        "interest_registered": [list(INTEREST_HEADER)],
        "feedback": [list(FEEDBACK_HEADER)],
    }
