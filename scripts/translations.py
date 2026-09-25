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


def _manage(*arguments):
    """Run a Django translation command from the repository root."""
    subprocess.run(
        [sys.executable, str(APP_ROOT / "manage.py"), *arguments],
        cwd=ROOT,
        check=True,
    )


def _update_catalogs():
    """Extract current source messages into every non-English catalog."""
    # English source text does not need its own PO file.
    languages = [code for code, _name in settings.LANGUAGES if code != settings.LANGUAGE_CODE]
    arguments = ["makemessages", "--no-obsolete", "--no-wrap"]
    # Update every catalog from the same source scan.
    for language in languages:
        arguments.extend(("--locale", language))
    _manage(*arguments)


def _catalog_issues(path):
    """Return missing messages and fuzzy messages from one PO catalog."""
    catalog = polib.pofile(path)
    # Separate missing text from translations that need review.
    missing = [entry for entry in catalog.untranslated_entries() if not entry.fuzzy]
    fuzzy = list(catalog.fuzzy_entries())
    metadata_fuzzy = bool(catalog.metadata_is_fuzzy)
    return catalog, missing, fuzzy, metadata_fuzzy


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

        catalog, missing, fuzzy, metadata_fuzzy = _catalog_issues(path)
        # Summarize complete catalogs instead of printing every message.
        if not missing and not fuzzy and not metadata_fuzzy:
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

    if issue_count:
        print(f"\nFound {issue_count} missing or fuzzy translation issue(s).")
    else:
        print("\nAll translation catalogs are complete and contain no fuzzy entries.")
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
        _manage("compilemessages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
