#!/usr/bin/env python3
"""Update, validate, report, and compile Apprise API translations."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

import polib

ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "apprise_api"
# Reuse the application's language list instead of maintaining another copy.
sys.path.insert(0, str(APP_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

from django.conf import settings  # noqa: E402

# Local environments and build output hold other packages' files; never scan them.
IGNORED_FOLDERS = (".tox", ".venv*", "bin", "lib", "lib64", "include", "build", "dist", "htmlcov", "var")


def _manage(*arguments, cwd=ROOT):
    """Run a Django translation command, by default from the repository root."""
    subprocess.run(
        [sys.executable, str(APP_ROOT / "manage.py"), *arguments],
        cwd=cwd,
        check=True,
    )


def _update_catalogs():
    """Extract current source messages into every non-English catalog."""
    # English source text does not need its own PO file.
    languages = [code for code, _name in settings.LANGUAGES if code != settings.LANGUAGE_CODE]
    arguments = ["makemessages", "--no-obsolete", "--no-wrap"]
    for folder in IGNORED_FOLDERS:
        arguments.extend(("--ignore", folder))
    # Update every catalog from the same source scan.
    for language in languages:
        arguments.extend(("--locale", language))
    _manage(*arguments)


# Many translations sit inside JavaScript strings, where a straight quote or
# backslash can end the string early. Asking for typographic quotes catches
# that before it ships; it is a quick early warning, not a guarantee. The test
# suite checks every page's scripts in every language.
UNSAFE_CHARACTERS = ("'", '"', "`", "\\")


def _unsafe_entries(catalog):
    """Return translations that add a quote or backslash the English lacks."""
    unsafe = []
    for entry in catalog.translated_entries():
        source = entry.msgid + (entry.msgid_plural or "")
        text = entry.msgstr + "".join(entry.msgstr_plural.values())
        if any(char in text and char not in source for char in UNSAFE_CHARACTERS):
            unsafe.append(entry)
    return unsafe


def _catalog_issues(path):
    """Return missing, fuzzy, and unsafe messages from one PO catalog."""
    catalog = polib.pofile(path)
    # Separate missing text from translations that need review.
    missing = [entry for entry in catalog.untranslated_entries() if not entry.fuzzy]
    fuzzy = list(catalog.fuzzy_entries())
    metadata_fuzzy = bool(catalog.metadata_is_fuzzy)
    return catalog, missing, fuzzy, metadata_fuzzy, _unsafe_entries(catalog)


def _label(entry):
    """Format one source message on a single readable output line."""
    label = entry.msgid.replace("\n", "\\n")
    if entry.msgctxt:
        label = f"[{entry.msgctxt}] {label}"
    return label


def _report():
    """Print supported languages and actionable catalog issues."""
    # Show the full language list before reporting catalog problems.
    print("Supported Languages")
    for code, name in settings.LANGUAGES:
        suffix = " (source language)" if code == settings.LANGUAGE_CODE else ""
        print(f"- {code}: {name}{suffix}")

    print("\nTranslation Status")
    issue_count = 0
    locale_root = Path(settings.LOCALE_PATHS[0])
    for code, name in settings.LANGUAGES:
        if code == settings.LANGUAGE_CODE:
            continue

        path = locale_root / code / "LC_MESSAGES" / "django.po"
        print(f"- {code}: {name}")
        # A missing PO file is itself an actionable translation problem.
        if not path.is_file():
            print(f"  - Missing catalog: {path.relative_to(ROOT)}")
            issue_count += 1
            continue

        catalog, missing, fuzzy, metadata_fuzzy, unsafe = _catalog_issues(path)
        # Summarize complete catalogs instead of printing every message.
        if not missing and not fuzzy and not metadata_fuzzy and not unsafe:
            print(f"  - Complete: {len(catalog)} translated messages")
            continue

        if metadata_fuzzy:
            print("  - Fuzzy catalog metadata")
            issue_count += 1
        for entry in missing:
            print(f"  - Missing: {_label(entry)}")
            issue_count += 1
        for entry in fuzzy:
            print(f"  - Fuzzy: {_label(entry)}")
            issue_count += 1
        for entry in unsafe:
            print(f"  - Straight quote or backslash (use \u2019 for an apostrophe): {_label(entry)}")
            issue_count += 1

    if issue_count:
        print(f"\nFound {issue_count} translation issue(s).")
    else:
        print("\nAll translation catalogs are complete and contain no fuzzy or unsafe entries.")
    return issue_count


def main():
    """Run the requested translation workflow."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        help="refresh PO catalogs from the current Python and template sources",
    )
    parser.add_argument(
        "--compile",
        action="store_true",
        help="compile complete PO catalogs into deployable MO files",
    )
    arguments = parser.parse_args()

    # Updating is opt-in because it rewrites PO source references and dates.
    if arguments.update:
        _update_catalogs()

    issue_count = _report()
    # Do not compile catalogs with missing or unreviewed text.
    if issue_count:
        return 1

    if arguments.compile:
        # Django compiles every catalog it finds below where it runs, so start
        # inside the app to reach only Apprise API's own catalogs.
        _manage("compilemessages", cwd=APP_ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
