# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
# All rights reserved.
#
# This code is licensed under the MIT License.
"""Regression tests for language negotiation, catalogs, and shared UI."""

import gettext
from pathlib import Path
import re

import apprise
from django.conf import settings
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.utils.translation import trans_real
import polib

from ..views import _apprise_asset, _apprise_object

# Values the API parses off the wire. They read the same in every language,
# so the guide must never present a translated form of one.
RESERVED_API_VALUES = frozenset(
    {str(entry.value) for group in (apprise.NotifyType, apprise.NotifyFormat, apprise.ConfigFormat) for entry in group}
    | {"auto", "simple", "hash", "disabled", "all", "yes", "no"}
)


class LocalizationTests(SimpleTestCase):
    """Keep browser, API, deployment, and Apprise language behavior aligned."""

    def test_supported_languages_match_apprise_mobile(self):
        # Keep changes to the public language list visible in code review.
        self.assertEqual(
            [code for code, _name in settings.LANGUAGES],
            [
                "ar",
                "de",
                "en",
                "es",
                "fr",
                "hi",
                "id",
                "it",
                "ja",
                "ko",
                "ms",
                "nl",
                "pl",
                "pt",
                "ru",
                "th",
                "tl",
                "tr",
                "vi",
                "zh",
            ],
        )

    def test_accept_language_uses_primary_fallback(self):
        # German is installed as ``de``, so ``de-DE`` falls back to it.
        response = self.client.get("/", headers={"accept-language": "de-DE,de;q=0.9,en;q=0.8"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Language"], "de")
        self.assertContains(response, 'lang="de"')
        self.assertContains(response, 'dir="ltr"')
        self.assertContains(response, "Die Apprise API")

    @override_settings(
        LANGUAGES=[
            ("en", "English"),
            ("fr", "Français"),
            ("de-de", "Deutsch (Deutschland)"),
        ]
    )
    def test_accept_language_prefers_exact_regional_match(self):
        # An exact regional catalog wins before any two-letter fallback.
        response = self.client.get(
            "/",
            headers={"accept-language": "fr-FR,DE-de;q=0.9,en;q=0.8"},
        )

        self.assertEqual(response.headers["Content-Language"], "de-de")
        self.assertContains(response, 'lang="de-de"')

    def test_accept_language_honors_quality_order(self):
        # Django sorts by q-value, so German remains ahead of French.
        response = self.client.get(
            "/",
            headers={"accept-language": "fr-CA;q=0.5,DE-de;q=0.9,xx-ZZ;q=0.1"},
        )

        self.assertEqual(response.headers["Content-Language"], "de")
        self.assertContains(response, 'lang="de"')

    def test_unknown_language_uses_english(self):
        # An unknown browser setting must never leave translation state unset.
        response = self.client.get("/", headers={"accept-language": "xx-ZZ"})

        self.assertEqual(response.headers["Content-Language"], "en")
        self.assertContains(response, 'lang="en"')
        self.assertContains(response, 'dir="ltr"')
        self.assertContains(response, "The Apprise API")

    def test_malformed_headers_use_english(self):
        # A header Django cannot parse must still leave the page translated.
        for header in (
            "",
            "   ",
            ",",
            ";",
            "en;q=abc",
            "fr;;q=0.5",
            "-",
            "fr-",
            "fr_CA",
            "\x00fr",
            "a" * 600,
        ):
            with self.subTest(header=header):
                response = self.client.get("/", headers={"accept-language": header})
                self.assertEqual(response.headers["Content-Language"], "en")

    def test_regional_headers_fall_back(self):
        # A region we do not carry a catalog for uses its plain language.
        for header, expected in (
            ("de-CH", "de"),
            ("pt-BR", "pt"),
            ("zh-Hans-CN", "zh"),
            ("FR-ca", "fr"),
            ("en-US-POSIX", "en"),
            # Neither the three-letter language nor its region is configured
            ("fil-PH", "en"),
            ("xx-ZZ", "en"),
        ):
            with self.subTest(header=header):
                response = self.client.get("/", headers={"accept-language": header})
                self.assertEqual(response.headers["Content-Language"], expected)

    def test_full_tags_come_before_base_languages(self):
        # Every full tag is tried before any shortened one, so a lower ranked
        # language can win over the base language of a higher ranked tag.
        for header, expected in (
            ("pt-BR,de", "de"),
            ("de-CH,fr", "fr"),
            ("zh-Hans,ja", "ja"),
        ):
            with self.subTest(header=header):
                response = self.client.get("/", headers={"accept-language": header})
                self.assertEqual(response.headers["Content-Language"], expected)

    def test_wildcard_keeps_listed_languages(self):
        # A wildcard picks no catalog, but a tag beside it still can.
        for header, expected in (
            ("*", "en"),
            ("*;q=0", "en"),
            ("*,de;q=0.1", "de"),
        ):
            with self.subTest(header=header):
                response = self.client.get("/", headers={"accept-language": header})
                self.assertEqual(response.headers["Content-Language"], expected)

    def test_oversized_header_is_handled(self):
        # Django trims a very long header; what survives is still honoured.
        response = self.client.get("/", headers={"accept-language": ("fr," * 300) + "de"})
        self.assertEqual(response.headers["Content-Language"], "fr")

    def test_cookie_only_selects_configured_languages(self):
        # Anything else in the cookie falls through to the browser header.
        for cookie, expected in (
            ("fr", "fr"),
            ("FR", "fr"),
            ("pt-BR", "pt"),
            ("xx", "de"),
            ("", "de"),
            ("   ", "de"),
            ("*", "de"),
            ("../../etc/passwd", "de"),
            ("a" * 5000, "de"),
        ):
            with self.subTest(cookie=cookie):
                self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = cookie
                response = self.client.get("/", headers={"accept-language": "de"})
                self.assertEqual(response.headers["Content-Language"], expected)

        del self.client.cookies[settings.LANGUAGE_COOKIE_NAME]

    def test_selector_accepts_every_language(self):
        # Every language offered in the header must be selectable.
        for code, _name in settings.LANGUAGES:
            with self.subTest(language=code):
                response = self.client.post("/i18n/setlang/", {"language": code, "next": "/"})

                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, code)

    def test_selector_rejects_unsupported_language(self):
        # Only a real language may be stored; nothing else sets a cookie.
        for value in (
            "xx",
            "../../etc/passwd",
            "",
            "*",
            "fr;q=0",
            "a" * 300,
            "en\nSet-Cookie: x=1",
        ):
            with self.subTest(language=value):
                response = self.client.post("/i18n/setlang/", {"language": value, "next": "/"})

                self.assertEqual(response.status_code, 302)
                self.assertIsNone(response.cookies.get(settings.LANGUAGE_COOKIE_NAME))

    def test_status_tokens_remain_stable(self):
        # Keep status tokens stable for mobile and other API clients.
        response = self.client.get(
            "/status",
            headers={"accept-language": "fr", "accept": "application/json"},
        )

        self.assertEqual(response.headers["Content-Language"], "fr")
        self.assertEqual(response.json()["status"]["details"], ["OK"])

    def test_language_cookie_applies_immediately(self):
        # The saved selector choice wins over a conflicting browser header.
        response = self.client.post(
            "/i18n/setlang/",
            {"language": "es", "next": "/"},
        )

        self.assertRedirects(response, "/", fetch_redirect_response=False)
        cookie = response.cookies[settings.LANGUAGE_COOKIE_NAME]
        self.assertEqual(cookie.value, "es")
        self.assertEqual(cookie["max-age"], settings.LANGUAGE_COOKIE_AGE)

        # The server is the only reader, and the choice survives navigation.
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")

        response = self.client.get("/", headers={"accept-language": "de"})
        self.assertEqual(response.headers["Content-Language"], "es")
        self.assertContains(response, 'lang="es"')
        self.assertContains(response, 'dir="ltr"')
        self.assertContains(response, "La API de Apprise")

    def test_arabic_uses_rtl_layout(self):
        # Arabic changes the document flow to right-to-left.
        response = self.client.get("/", headers={"accept-language": "ar"})

        self.assertEqual(response.headers["Content-Language"], "ar")
        self.assertContains(response, 'lang="ar"')
        self.assertContains(response, 'dir="rtl"')
        # Product and control groups remain together in an RTL layout.
        self.assertContains(response, 'class="nav-brand"')
        self.assertContains(response, 'class="nav-actions"')

        # URLs, paths, and code samples stay left to right, so /notify/KEY is
        # not reordered into notify/KEY/ on a right-to-left page.
        css = (Path(settings.BASE_DIR) / "static" / "css" / "base.css").read_text()
        self.assertIn("unicode-bidi: isolate;", css)

        # Only the configuration editor is pinned; a message field follows the
        # language being typed into it.
        self.assertIn(".apprise-config-editor textarea {", css)
        self.assertNotIn("\nsamp,\ntextarea {", css)

    def test_message_fields_follow_typed_language(self):
        # A person writing Arabic must see their own message right to left.
        response = self.client.get("/cfg/langcheck")
        content = response.content.decode()

        self.assertIn('name="body"', content)
        for field in ("body", "title"):
            with self.subTest(field=field):
                self.assertRegex(content, rf'<[^>]*name="{field}"[^>]*dir="auto"')

    def test_selector_uses_two_letter_codes(self):
        # Short labels fit phones; assistive text still uses native names.
        response = self.client.get("/")
        content = response.content.decode()

        self.assertIn('class="browser-default language-selector"', content)
        self.assertIn('action="/i18n/setlang/"', content)
        for code, _name in settings.LANGUAGES:
            self.assertIn(f'value="{code}"', content)
            self.assertIn(f">{code}</option>", content)

    def test_nginx_allows_language_selection(self):
        # Both nginx modes must pass the language form to Django.
        for filename in ("nginx.conf", "nginx-strict.conf"):
            nginx = (Path(settings.BASE_DIR) / "etc" / filename).read_text()
            with self.subTest(filename=filename):
                self.assertIn('location ~ "^/i18n/setlang/?$"', nginx)

                # Read the directives of that location block on their own so a
                # matching line elsewhere in the file cannot satisfy this test.
                start = nginx.index('location ~ "^/i18n/setlang/?$"')
                block = nginx[start : nginx.index("}", start)]
                self.assertIn("client_max_body_size 8k;", block)
                self.assertIn("proxy_pass http://apprise_upstream;", block)

                # Strict mode denies by default and throttles what it exposes.
                if filename == "nginx-strict.conf":
                    self.assertIn("limit_req zone=key_auth burst=20 nodelay;", block)

    def test_compiled_catalogs_are_valid(self):
        # Open every production catalog to catch missing or corrupt files.
        locale_root = Path(settings.LOCALE_PATHS[0])

        for code, _name in settings.LANGUAGES:
            if code == settings.LANGUAGE_CODE:
                continue

            catalog_path = locale_root / code / "LC_MESSAGES" / "django.mo"
            source_path = catalog_path.with_suffix(".po")
            with self.subTest(language=code):
                self.assertTrue(catalog_path.is_file())
                with catalog_path.open("rb") as catalog_file:
                    catalog = gettext.GNUTranslations(catalog_file)
                self.assertNotEqual(catalog.gettext("Language"), "Language")

                # A translation edited without running the compile step
                # would otherwise be served stale.
                source = polib.pofile(str(source_path))
                translated = {
                    entry.msgid: entry.msgstr
                    for entry in source.translated_entries()
                    if not entry.fuzzy and not entry.msgid_plural and not entry.msgctxt
                }
                self.assertTrue(translated)
                stale = [msgid for msgid, msgstr in translated.items() if catalog.gettext(msgid) != msgstr]
                self.assertEqual(stale, [], f"{code}: run `tox -e translations -- --compile`")

    def test_rendered_api_values_stay_english(self):
        # Whatever language the guide is read in, the values it tells people
        # to send have to be the ones the API accepts. Counting rather than
        # looking for one match means a single translated entry is caught
        # even where the same value is shown more than once.
        shown = ("info", "success", "warning", "failure", "yaml", "text")
        english = self.client.get("/", headers={"accept-language": "en"}).content.decode()
        expected = {value: english.count(f"<code>{value}</code>") for value in shown}
        self.assertTrue(all(expected.values()), expected)

        for language in ("es", "fr", "it", "zh", "ar"):
            with self.subTest(language=language):
                response = self.client.get("/", headers={"accept-language": language})
                content = response.content.decode()

                self.assertEqual(response.headers["Content-Language"], language)
                counted = {value: content.count(f"<code>{value}</code>") for value in shown}
                self.assertEqual(counted, expected)

    def test_api_values_carry_no_translation_markers(self):
        # Catch the mistake in the template, before a catalog ever sees it.
        marker = re.compile(r"{%\s*trans\s+[\"']([^\"']+)[\"']")
        templates = Path(settings.BASE_DIR) / "api" / "templates"
        for path in sorted(templates.rglob("*.html")):
            wrapped = sorted({v for v in marker.findall(path.read_text()) if v.strip() in RESERVED_API_VALUES})
            with self.subTest(template=path.name):
                self.assertEqual(wrapped, [], f"{path.name}: an API value must not be translated")

    def test_api_values_are_absent_from_catalogs(self):
        # The last line of defence: nothing translatable may be a wire value.
        locale_root = Path(settings.LOCALE_PATHS[0])
        for code, _name in settings.LANGUAGES:
            if code == settings.LANGUAGE_CODE:
                continue

            catalog = locale_root / code / "LC_MESSAGES" / "django.po"
            with self.subTest(language=code):
                offenders = sorted(
                    entry.msgid for entry in polib.pofile(str(catalog)) if entry.msgid.strip() in RESERVED_API_VALUES
                )
                self.assertEqual(offenders, [], f"{code}: remove the translation markers")

    def test_apprise_receives_request_language(self):
        # The asset carries the request language into every Apprise component.
        request = RequestFactory().get("/notify")
        request.LANGUAGE_CODE = "de-DE"

        asset = _apprise_asset(request)
        instance = _apprise_object(request, asset=asset)

        # Apprise stores a region with an underscore, so de-DE becomes de_DE.
        self.assertEqual(asset.language, "de_DE")
        self.assertEqual(instance.locale.lang, "de_DE")

    def test_details_follows_request_language(self):
        # The service listing is built from the same asset as the page, so the
        # language a caller asked for is the one Apprise reports in.
        def verify_label(language):
            """Return the Verify SSL field name the listing came back with."""
            response = self.client.get(
                "/details",
                headers={"accept-language": language, "accept": "application/json"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Content-Language"], language)

            for schema in response.json()["schemas"]:
                args = schema.get("details", {}).get("args", {})
                if "verify" in args:
                    return args["verify"]["name"]

            self.fail("no service reported a verify argument")

        english = verify_label("en")
        self.assertTrue(english)

        catalog = Path(apprise.__file__).parent / "i18n" / "de" / "LC_MESSAGES" / "apprise.mo"
        if not catalog.is_file():
            # An older installed Apprise carries no German catalog to show.
            self.skipTest("the installed Apprise has no German catalog")

        # The field name itself must come back translated, not just the header.
        self.assertNotEqual(verify_label("de"), english)

    def test_documented_client_header_is_honoured(self):
        # The example published in the API documentation has to keep working,
        # including for a client that is not a browser.
        response = self.client.get(
            "/details",
            headers={
                "accept-language": "en-US,en;q=0.9,de;q=0.5",
                "accept": "application/json",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Language"], "en")

        # A machine-readable reply still reports the language it used.
        german = self.client.get(
            "/status",
            headers={"accept-language": "de", "accept": "application/json"},
        )
        self.assertEqual(german.headers["Content-Language"], "de")
        self.assertEqual(german.json()["status"]["details"], ["OK"])

    def test_every_language_reaches_apprise(self):
        # A tag Apprise cannot read would raise on every request that uses it.
        for code, _name in settings.LANGUAGES:
            with self.subTest(language=code):
                request = RequestFactory().get("/notify")
                request.LANGUAGE_CODE = code

                # Apprise spells a region its own way, so compare languages.
                language = _apprise_asset(request).language
                self.assertEqual(language.split("_")[0], code.split("-")[0])

    def test_language_selector_works_before_login(self):
        # The login page offers the selector, so it must work without a cookie.
        with override_settings(APPRISE_AUTH_REQUIRED=True):
            page = self.client.get("/login/")
            self.assertContains(page, 'action="/i18n/setlang/"')

            response = self.client.post(
                "/i18n/setlang/",
                {"language": "fr", "next": "/"},
            )

        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "fr")

    def test_refused_language_is_not_selected(self):
        # A quality of zero means the client will not accept that language.
        for header, expected in (
            ("fr;q=0", "en"),
            ("xx;q=1,fr;q=0", "en"),
            ("*;q=1,fr;q=0", "en"),
            ("de;q=0.9,fr;q=0", "de"),
        ):
            with self.subTest(header=header):
                response = self.client.get("/", headers={"accept-language": header})
                self.assertEqual(response.headers["Content-Language"], expected)

    def test_repeated_language_tags_are_ignored(self):
        # The same tag listed twice, in any capitalization, is considered once.
        response = self.client.get("/", headers={"accept-language": "xx,XX;q=0.9,de;q=0.8"})

        self.assertEqual(response.headers["Content-Language"], "de")

    def test_django_header_parser_is_available(self):
        # Importing the middleware already proves the module is there; this
        # pins the shape of what it returns.
        self.assertTrue(callable(trans_real.parse_accept_lang_header))
        self.assertEqual(
            trans_real.parse_accept_lang_header("de;q=0.9,fr;q=0"),
            (("de", 0.9), ("fr", 0.0)),
        )
