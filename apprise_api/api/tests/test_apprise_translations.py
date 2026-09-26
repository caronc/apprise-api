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
import importlib.util
import os
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import warnings

from django.conf import settings
from django.test import SimpleTestCase
import polib

from apprise_api.apprise_translations import (
    api_locale_dir,
    apprise_i18n_dir,
    compile_all_translations,
    compile_apprise_translations,
    main,
)

_real_find_spec = importlib.util.find_spec


def _apprise_not_installed(name, *args, **kwargs):
    """``find_spec`` that finds everything except Apprise itself."""
    return None if name == "apprise" else _real_find_spec(name, *args, **kwargs)


def _write_catalog(i18n, language, source, translation):
    """Writes a one-entry ``.po`` catalog for ``language`` and returns its path."""
    folder = Path(i18n) / language / "LC_MESSAGES"
    folder.mkdir(parents=True)
    catalog = polib.POFile()
    catalog.metadata = {"Content-Type": "text/plain; charset=utf-8"}
    catalog.append(polib.POEntry(msgid=source, msgstr=translation))
    po_path = folder / "apprise.po"
    catalog.save(str(po_path))
    return po_path


class AppriseTranslationsTests(SimpleTestCase):
    def test_a_missing_catalog_is_compiled_and_readable(self):
        with TemporaryDirectory() as i18n:
            po_path = _write_catalog(i18n, "fr", "Targets", "Cibles")

            assert compile_apprise_translations(Path(i18n)) == 1

            # The compiled copy is what gettext reads, and it translates
            mo = polib.mofile(str(po_path.with_suffix(".mo")))
            assert mo.find("Targets").msgstr == "Cibles"

    def test_every_language_is_compiled(self):
        with TemporaryDirectory() as i18n:
            for language in ("fr", "de", "pt_BR"):
                _write_catalog(i18n, language, "Targets", f"Targets ({language})")

            assert compile_apprise_translations(Path(i18n)) == 3
            assert len(list(Path(i18n).glob("*/LC_MESSAGES/*.mo"))) == 3

    def test_an_up_to_date_catalog_is_left_alone(self):
        with TemporaryDirectory() as i18n:
            _write_catalog(i18n, "fr", "Targets", "Cibles")
            compile_apprise_translations(Path(i18n))

            # A second pass has nothing to do
            assert compile_apprise_translations(Path(i18n)) == 0

    def test_an_edited_catalog_is_compiled_again(self):
        with TemporaryDirectory() as i18n:
            po_path = _write_catalog(i18n, "fr", "Targets", "Cibles")
            compile_apprise_translations(Path(i18n))
            mo_path = po_path.with_suffix(".mo")

            # The source is newer than its compiled copy
            os.utime(mo_path, (1, 1))
            assert compile_apprise_translations(Path(i18n)) == 1
            assert mo_path.stat().st_mtime > 1

    def test_no_catalog_folder_means_nothing_to_do(self):
        with patch("apprise_api.apprise_translations.apprise_i18n_dir", return_value=None):
            assert compile_apprise_translations() == 0

    def test_the_installed_apprise_catalog_folder_is_found(self):
        with TemporaryDirectory() as root:
            (Path(root) / "i18n").mkdir()
            spec = SimpleNamespace(origin=str(Path(root) / "__init__.py"))
            with patch("importlib.util.find_spec", return_value=spec):
                assert apprise_i18n_dir() == Path(root) / "i18n"

    def test_an_apprise_without_catalogs_or_not_installed_has_no_folder(self):
        with TemporaryDirectory() as root:
            # Installed, but with no i18n folder at all
            spec = SimpleNamespace(origin=str(Path(root) / "__init__.py"))
            with patch("importlib.util.find_spec", return_value=spec):
                assert apprise_i18n_dir() is None

        # Not installed, or a namespace package with no file of its own
        with patch("importlib.util.find_spec", return_value=None):
            assert apprise_i18n_dir() is None
        with patch("importlib.util.find_spec", return_value=SimpleNamespace(origin=None)):
            assert apprise_i18n_dir() is None

    def test_the_api_catalog_folder_is_found(self):
        assert api_locale_dir() == Path(settings.LOCALE_PATHS[0]).resolve()
        assert list(api_locale_dir().glob("*/LC_MESSAGES/django.po"))

    def test_both_the_api_and_apprise_catalogs_are_compiled(self):
        with patch(
            "apprise_api.apprise_translations.compile_apprise_translations", side_effect=[19, 3]
        ) as mock_compile:
            assert compile_all_translations() == 22

        # The API's own folder first, then the installed Apprise's
        assert mock_compile.call_args_list[0].args == (api_locale_dir(),)
        assert mock_compile.call_args_list[1].args == ()

    def test_main_reports_how_many_were_compiled(self):
        with (
            patch("apprise_api.apprise_translations.compile_all_translations", return_value=20),
            patch("sys.stderr.write") as mock_write,
        ):
            assert main() == 0

        assert "Compiled 20" in mock_write.call_args.args[0]

    def test_module_entrypoint(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            with (
                # Re-run fresh, so patch what it looks up: Apprise reads as not installed
                patch("importlib.util.find_spec", side_effect=_apprise_not_installed),
                patch("sys.stderr.write"),
                self.assertRaises(SystemExit) as ctx,
            ):
                runpy.run_module("apprise_api.apprise_translations", run_name="__main__")

        assert ctx.exception.code == 0
