# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
# All rights reserved.
#
# This code is licensed under the MIT License.
"""Choose one supported language for each incoming request."""

from django.conf import settings
from django.middleware.locale import LocaleMiddleware
from django.utils import translation

# Django's own LocaleMiddleware reads Accept-Language through this module. It
# caps the header length and caches parsed values, which is why it is used here
# instead of a parser of our own. The module is private to Django, so an
# upgrade that renames it breaks this import outright; a test covers what it
# returns so a quieter change in behaviour is noticed too.
from django.utils.translation import trans_real


def _language_map():
    """Return each configured language keyed by its case-insensitive tag."""
    # Built on demand so a settings change is picked up straight away.
    return {code.casefold(): code for code, _name in settings.LANGUAGES}


def _configured_language(language):
    """Return the configured spelling of an exact, case-insensitive match."""
    # Language tags ignore letter case; return the spelling from settings.
    return _language_map().get(language.casefold())


def _language_candidates(languages):
    """Yield exact language tags first, followed by untried primary tags."""
    # Django has already sorted these entries by their q-values.
    exact = []
    seen = set()
    for language in languages:
        # A wildcard cannot select a specific catalog. Unlike Django, scanning
        # continues so a tag listed beside it can still be honoured.
        if language == "*":
            continue

        # Skip repeated tags, even when their capitalization differs.
        normalized = language.casefold()
        if normalized not in seen:
            exact.append(language)
            seen.add(normalized)

    # Try every full tag before considering a shortened one.
    yield from exact

    # Try each untested primary tag in the same order, such as de for de-CH.
    for language in exact:
        primary = language.split("-", 1)[0]
        normalized = primary.casefold()
        if normalized not in seen:
            seen.add(normalized)
            yield primary


def _best_configured_language(languages):
    """Select a configured language using Apprise API's two-pass fallback."""
    # Only languages listed in settings are available. The lookup is built once
    # here rather than rescanning the list for every candidate.
    configured = _language_map()
    for candidate in _language_candidates(languages):
        if language := configured.get(candidate.casefold()):
            return language
    return None


class AcceptLanguageLocaleMiddleware(LocaleMiddleware):
    """Use Django's parser with exact-first regional language negotiation."""

    def process_request(self, request):
        """Activate the cookie language, or the best supported header one.

        Every URL serves every language, so there is no language prefix to
        read from the path the way Django's own middleware allows for.
        """
        # A saved selector choice takes priority over the browser header.
        cookie = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
        language = _best_configured_language((cookie,)) if cookie else None

        if language is None:
            # Django safely parses commas, q-values, and malformed headers.
            # A quality of zero means the client refuses that language, so
            # those entries are dropped rather than used as a fallback.
            accepted = (
                code
                for code, quality in trans_real.parse_accept_lang_header(request.META.get("HTTP_ACCEPT_LANGUAGE", ""))
                if quality
            )
            language = _best_configured_language(accepted)

        if language is None:
            # Use English when no requested language is available.
            language = _configured_language(settings.LANGUAGE_CODE) or "en"

        # Activate translations and expose the choice to templates and views.
        translation.activate(language)
        request.LANGUAGE_CODE = translation.get_language()
