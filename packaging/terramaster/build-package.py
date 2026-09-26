#!/usr/bin/python3
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
#
# Builds the TerraMaster TOS 7 "Docker Application" package for Apprise
# API: a <appid>.tar.gz containing exactly config.ini, <appid>.lang,
# <appid>.svg, and docker-compose.yml, plus its required .sha256
# checksum file, per the TOS 7 Application Development Guide (Chapter 9):
#   https://github.com/TerraMasterOfficial/app-pkg-tools
#
# Usage:
#   python3 packaging/terramaster/build-package.py
#   python3 packaging/terramaster/build-package.py --version 1.5.2
#   python3 packaging/terramaster/build-package.py --platform aarch64
#
# Output lands in packaging/terramaster/dist/ (gitignored) as:
#   apprise-terramaster-tos7-app.tar.gz
#   apprise-terramaster-tos7-app.tar.gz.sha256
from __future__ import annotations

import argparse
from hashlib import sha256
import io
from json import JSONDecodeError, loads
from pathlib import Path
import re
import sys
import tarfile

# The Application ID
# - Must match config.ini "id", the .lang/.svg file base names,
#   and docker-compose.yml's service/container_name.
# - Deliberately distinct from the apprise-api repo/image name so the
#   release asset (<APP_ID>.tar.gz) can never be mistaken for the
#   Apprise API application itself. The *displayed* app name shown in
#   the TOS App Center is unrelated -- that's "name" in the .lang file.
APP_ID = "apprise-terramaster-tos7-app"

# TerraMaster requires all 14 of these language sections to be present.
REQUIRED_LANGUAGES = (
    "en-us",
    "zh-cn",
    "zh-hk",
    "fr-fr",
    "de-de",
    "it-it",
    "es-es",
    "hu-hu",
    "ja-jp",
    "ko-kr",
    "pl-pl",
    "ru-ru",
    "tr-tr",
    "pt-pt",
)
REQUIRED_LANG_KEYS = ("name", "auth", "descript", "release_note", "important")

# config.ini.version: digits and dots only, max 20 chars (Chapter 4.2).
VERSION_RE = re.compile(r"^[0-9]+(\.[0-9]+)*$")

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent.parent


def detect_version() -> str:
    """Parse version out of pyproject.toml"""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit('Could not find version = "..." in pyproject.toml')
    return match.group(1)


def to_lf(text: str) -> str:
    """Normalize to LF line endings only (CRLF is rejected by TOS 7)."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def render(template_path: Path, substitutions: dict) -> str:
    """Build packaging"""
    text = to_lf(template_path.read_text(encoding="utf-8"))
    for key, value in substitutions.items():
        text = text.replace("{{" + key + "}}", value)

    remaining = re.findall(r"{{[A-Z_]+}}", text)
    if remaining:
        raise SystemExit(
            f"Unfilled template placeholder(s) in {template_path.name}: {remaining}")

    return text


def validate_config_ini(content: str, expected_version: str, expected_platform: str) -> None:
    try:
        data = loads(content)

    except JSONDecodeError as e:
        raise SystemExit(f"config.ini is not valid JSON: {e}") from e

    if data.get("id") != APP_ID:
        raise SystemExit(f"config.ini id must be {APP_ID!r}, got {data.get('id')!r}")

    if data.get("version") != expected_version:
        raise SystemExit(f"config.ini version {data.get('version')!r} != {expected_version!r}")

    if not VERSION_RE.match(expected_version) or len(expected_version) > 20:
        raise SystemExit(
            f"Version {expected_version!r} must be digits/dots only and <= 20 chars"
        )

    if data.get("platform") != expected_platform:
        raise SystemExit(f"config.ini platform {data.get('platform')!r} != {expected_platform!r}")

    if data.get("icon") != f"/images/icons/{APP_ID}.svg":
        raise SystemExit("config.ini icon must be /images/icons/{}.svg".format(APP_ID))


def validate_lang(content: str) -> None:
    sections = dict(re.findall(r"(?ms)^\[([a-z]{2}-[a-z]{2})\]\n(.*?)(?=\n\[|\Z)", content))

    missing = [lang for lang in REQUIRED_LANGUAGES if lang not in sections]
    if missing:
        raise SystemExit(f"{APP_ID}.lang is missing language section(s): {missing}")

    for lang, body in sections.items():
        for key in REQUIRED_LANG_KEYS:
            if not re.search(rf'(?m)^{key}\s*=\s*".*"\s*$', body):
                raise SystemExit(f"{APP_ID}.lang [{lang}] is missing a quoted {key!r} value")


def validate_compose(content: str) -> None:
    if re.search(r"image:\s*\S+:latest\b", content):
        raise SystemExit("docker-compose.yml must not reference a :latest image tag")

    if f"container_name: {APP_ID}" not in content:
        raise SystemExit(f"docker-compose.yml container_name must equal the app id {APP_ID!r}")

    if "healthcheck:" not in content:
        raise SystemExit("docker-compose.yml is missing a healthcheck (required for every service)")

    if "privileged" in content or "network_mode: host" in content:
        raise SystemExit("docker-compose.yml must not use privileged mode or host networking")


def build(version: str, platform: str, release_note: str, out_dir: Path) -> Path:
    substitutions = {"VERSION": version, "PLATFORM": platform}

    # Source template filenames in this directory are prefixed with
    # "terramaster-" so nobody browsing the repo mistakes them for the
    # project's real config.ini/docker-compose.yml. The prefix is stripped
    # off again below: the files written *inside* the tar.gz must be the
    # exact names TOS 7 requires (Chapter 9.2), with no prefix at all.
    config_ini = render(PACKAGE_DIR / "terramaster-config.ini.tmpl", substitutions)
    validate_config_ini(config_ini, version, platform)

    compose = render(PACKAGE_DIR / "terramaster-docker-compose.yml.tmpl", substitutions)
    validate_compose(compose)

    # Error Handling
    if '"' in release_note:
        raise SystemExit('--release-note must not contain a literal \'"\' character (no escaping in .lang)')

    # Release Notes
    lang = render(
        PACKAGE_DIR / "terramaster-apprise-api.lang.tmpl", {"RELEASE_NOTE": release_note})
    validate_lang(lang)

    # Source icon filename is a fixed literal, not derived from APP_ID --
    # see the header comment on APP_ID for why the two are intentionally
    # different.
    svg = to_lf((PACKAGE_DIR / "terramaster-apprise-api.svg").read_text(encoding="utf-8"))

    out_dir.mkdir(parents=True, exist_ok=True)
    archive_path = out_dir / f"{APP_ID}.tar.gz"

    # Files must sit at the archive root with no wrapping directory
    payload = {
        "config.ini": config_ini,
        f"{APP_ID}.lang": lang,
        f"{APP_ID}.svg": svg,
        "docker-compose.yml": compose,
    }

    # Prepare TOS 7 Package
    with tarfile.open(archive_path, "w:gz") as tar:
        for name, text in payload.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mode = 0o644
            # Fixed metadata keeps TOS 7 packages reproducible across builds.
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, fileobj=io.BytesIO(data))

    checksum = sha256(archive_path.read_bytes()).hexdigest()
    checksum_path = out_dir / f"{APP_ID}.tar.gz.sha256"
    checksum_path.write_text(f"{checksum}  {archive_path.name}\n", encoding="utf-8")

    return archive_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        default=None,
        help="Version to embed (default: read from pyproject.toml)",
    )
    parser.add_argument(
        "--platform",
        default="x86_64",
        choices=("x86_64", "aarch64"),
        help="TOS platform field; TerraMaster wants a separate submission per platform",
    )
    parser.add_argument(
        "--release-note",
        default=None,
        help="release_note text (default: link to the GitHub release changelog)",
    )
    parser.add_argument(
        "--out-dir",
        default=str(PACKAGE_DIR / "dist"),
        help=f"Directory to write {APP_ID}.tar.gz + .sha256 into",
    )
    args = parser.parse_args()

    version = args.version or detect_version()
    release_note = args.release_note or (
        f"See the full changelog: https://github.com/caronc/apprise-api/releases/tag/v{version}"
    )

    archive_path = build(version, args.platform, release_note, Path(args.out_dir))
    checksum_path = archive_path.with_suffix(archive_path.suffix + ".sha256")

    print(f"Built {archive_path} ({archive_path.stat().st_size} bytes)")
    print(f"Checksum {checksum_path}")
    print()
    print("Next steps:")
    print(f"  1. Create/confirm a GitHub Release tagged v{version} (or {version}) on apprise-api.")
    print(f"  2. Attach {archive_path.name} and {checksum_path.name} as release assets.")
    print(f"  3. On the TNAS Developer Platform, add/update the Apprise ({APP_ID}) app version")
    print(f"     to {version}, matching config.ini and the Release tag exactly.")


if __name__ == "__main__":
    sys.exit(main())
