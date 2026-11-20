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
from pathlib import Path
import re

# The repository root
ROOT = Path(__file__).resolve().parents[3]

# Old appriseit.com pages that only still work through a server redirect
RETIRED = (
    "/config/",
    "/dev/",
    "/getting-started/persistent-storage/",
    "/tools/",
)

# An appriseit.com link and the path it points to
LINK_RE = re.compile(r"https?://(?:www\.)?appriseit\.com(?:/fr)?(?P<path>/[^\s)\"'<>`]*)")


def stale_links(files: list[Path]) -> list[str]:
    """Return every link in the files that points at a retired page."""
    return [
        f"{path.name}: {found.group(0)}"
        for path in files
        for found in LINK_RE.finditer(path.read_text(encoding="utf-8"))
        if found.group("path").startswith(RETIRED)
    ]


def test_links_to_appriseit_use_current_pages():
    """Links to appriseit.com point at current pages, not retired ones."""
    files = [ROOT / "README.md", ROOT / "README-es.md", ROOT / "swagger.yaml"]
    for pattern in ("*.html", "*.py", "*.po"):
        files += [path for path in sorted((ROOT / "apprise_api").rglob(pattern)) if "tests" not in path.parts]

    stale = stale_links(files)
    assert not stale, "\n".join(stale)


def test_a_retired_link_is_caught(tmp_path):
    """An old /tools/url-builder/ link is reported."""
    page = tmp_path / "page.html"
    page.write_text('<a href="https://appriseit.com/tools/url-builder/">x</a> https://appriseit.com/url-builder/')
    assert stale_links([page]) == ["page.html: https://appriseit.com/tools/url-builder/"]
