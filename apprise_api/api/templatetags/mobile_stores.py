"""Local store artwork for the shared mobile download links."""

from django import template
from django.utils.translation import get_language, gettext

register = template.Library()

APP_STORE_URL = "https://apps.apple.com/app/id6821633589"
GOOGLE_PLAY_URL = "https://play.google.com/store/apps/details?id=com.appriseit.mobile"
# Intrinsic proportions of the unmodified artwork. Only these known locales
# may become asset paths; regional or unknown language codes fall back safely.
BADGE_RATIOS = {
    "ar": (2.991592, 3.371808),
    "de": (2.991602, 3.371808),
    "en": (2.991602, 3.371808),
    "es": (2.991602, 3.371808),
    "fr": (3.162688, 3.371808),
    "hi": (2.991602, 3.371808),
    "id": (2.991602, 3.371808),
    "it": (2.991602, 3.371808),
    "ja": (2.721289, 3.374983),
    "ko": (3.242518, 3.371808),
    "ms": (2.991602, 3.371808),
    "nl": (2.991602, 3.371808),
    "pl": (2.991602, 3.37985),
    "pt": (2.991602, 3.371808),
    "ru": (2.991602, 3.371808),
    "th": (2.991602, 3.371808),
    "tl": (2.991602, 3.371808),
    "tr": (3.782254, 3.371808),
    "vi": (2.991602, 3.371808),
    "zh": (2.721289, 3.371808),
}


@register.inclusion_tag("components/mobile_store_links.html")
def mobile_store_links():
    """Select badges for the active Django language without external requests."""
    language = (get_language() or "en").lower().split("-", 1)[0]
    language = {"fil": "tl", "in": "id"}.get(language, language)
    if language not in BADGE_RATIOS:
        language = "en"
    apple, google = BADGE_RATIOS[language]
    return {
        "app_store_url": APP_STORE_URL,
        "google_play_url": GOOGLE_PLAY_URL,
        "apple_badge": f"mobile/store-badges/app-store-{language}.svg",
        "google_badge": f"mobile/store-badges/google-play-{language}.svg",
        "apple_label": gettext("App Store"),
        "google_label": gettext("Get It on Google Play"),
        "apple_ratio": format(apple, ".6f"),
        "google_ratio": format(google, ".6f"),
        "badge_width": format((apple + google) * 40 + 12, ".3f"),
    }
