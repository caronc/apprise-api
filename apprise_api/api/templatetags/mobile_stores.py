#
# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
# All rights reserved.
#
# This code is licensed under the MIT License.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files(the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and / or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions :
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
"""Local store artwork for the shared mobile download links."""

from django import template
from django.utils.translation import get_language, gettext

register = template.Library()

# Where each store badge links to.
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
def mobile_store_links() -> dict[str, str]:
    """Select badges for the active Django language without external requests."""
    # The page language without its region, e.g. "fr" for "fr-CA".
    language = (get_language() or "en").lower().split("-", 1)[0]
    # Other codes for Tagalog and Indonesian use those badges.
    language = {"fil": "tl", "in": "id"}.get(language, language)
    # Only languages with artwork are used; the rest show English.
    if language not in BADGE_RATIOS:
        language = "en"
    # Each badge's width for its height, so both line up at the same height.
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
        # Both badges side by side at 40px high, plus the 12px gap between them.
        "badge_width": format((apple + google) * 40 + 12, ".3f"),
    }
