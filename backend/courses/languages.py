"""
Canonical list of supported languages for lesson audio tracks.

Shared by the manual audio-upload workflow (views.py) and available for
reference by the AI-dubbing pipeline (services/ai_translator.py has its own
LANGUAGE_MAP for translate/TTS-specific config, kept separate on purpose).

Codes here are simple base codes (e.g. 'hi', 'ta'). Legacy TranslatedAudio
rows created by the AI pipeline may use regional codes such as 'hi-IN' /
'ta-IN' / 'ml-IN' -- get_language_name() normalizes those by matching on
the base code before the '-', so existing data keeps displaying correctly.
"""

SUPPORTED_LANGUAGES = [
    ("ml", "Malayalam"),
    ("hi", "Hindi"),
    ("ta", "Tamil"),
    ("te", "Telugu"),
    ("kn", "Kannada"),
    ("bn", "Bengali"),
    ("mr", "Marathi"),
    ("gu", "Gujarati"),
    ("pa", "Punjabi"),
    ("ar", "Arabic"),
    ("fr", "French"),
    ("de", "German"),
    ("es", "Spanish"),
    ("pt", "Portuguese"),
    ("it", "Italian"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("zh", "Chinese"),
    ("ru", "Russian"),
]

LANGUAGE_NAME_MAP = dict(SUPPORTED_LANGUAGES)


def get_language_name(code: str) -> str:
    """
    Look up a display name for a language code, tolerant of regional
    suffixes like 'hi-IN'. Falls back to the raw code if unknown so an
    unrecognized/custom code never breaks display.
    """
    if not code:
        return code
    base = code.split('-')[0].lower()
    return LANGUAGE_NAME_MAP.get(base, code)
