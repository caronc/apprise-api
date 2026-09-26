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
"""Compiles the translation catalogs that Apprise API and the Apprise library read.

Only compiled ``.mo`` catalogs are read, and without them every language
quietly falls back to English. Two sets are compiled in place:

- Apprise API's own catalogs under ``apprise_api/locale``; only the ``.po``
  sources are kept in git.
- The Apprise library's catalogs, which an install from git (for example a
  ``--branch`` install) or an older release can leave out.

Run it with ``python -m apprise_api.apprise_translations`` (needs ``polib``).
The Docker build and ``tox -e runserver`` already run it for you.
"""

import importlib.util
from pathlib import Path
import sys


def api_locale_dir():
    """Where Apprise API keeps its own catalogs."""
    return Path(__file__).resolve().parent / "locale"


def apprise_i18n_dir():
    """Where the installed Apprise keeps its catalogs, or None if it has none."""
    # Found without importing Apprise, so a just-reinstalled copy is the one used
    spec = importlib.util.find_spec("apprise")
    if spec is None or spec.origin is None:
        return None

    i18n = Path(spec.origin).parent / "i18n"
    return i18n if i18n.is_dir() else None


def compile_apprise_translations(i18n_dir=None):
    """Compiles each catalog whose ``.mo`` is missing or older than its ``.po``.

    - ``i18n_dir`` is the catalog folder; defaults to the installed Apprise's.
    - Returns how many catalogs were compiled.
    """
    # Nothing to do without a catalog folder
    i18n_dir = i18n_dir or apprise_i18n_dir()
    if i18n_dir is None:
        return 0

    # polib is only needed when there is actually something to compile
    compiled = 0
    for po_path in sorted(Path(i18n_dir).glob("*/LC_MESSAGES/*.po")):
        mo_path = po_path.with_suffix(".mo")

        # Already compiled, and up to date with its source
        if mo_path.is_file() and mo_path.stat().st_mtime >= po_path.stat().st_mtime:
            continue

        import polib

        # Write the compiled copy gettext actually reads
        polib.pofile(str(po_path)).save_as_mofile(str(mo_path))
        compiled += 1

    return compiled


def compile_all_translations():
    """Compiles Apprise API's own catalogs, then the Apprise library's.

    - Returns how many catalogs were compiled in total.
    """
    return compile_apprise_translations(api_locale_dir()) + compile_apprise_translations()


def main():
    """Compiles what is missing and reports how many catalogs were compiled."""
    compiled = compile_all_translations()
    sys.stderr.write(f"Compiled {compiled} translation catalog(s)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
