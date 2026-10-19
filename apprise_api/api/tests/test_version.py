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
"""The API version must agree across the UI and source-based deployments."""

import os
from pathlib import Path
import runpy
import shutil
import site
import subprocess
import sys
import tempfile
from unittest.mock import patch

from core.version import __version__
from django.conf import settings
from django.test import SimpleTestCase

from ..context_processors import apprise_metadata


class VersionTests(SimpleTestCase):
    def test_ui_uses_api_version_independently_of_library_version(self):
        """The API footer and asset URLs use the API's own release version."""
        with patch("apprise.__version__", "library-test-version"):
            metadata = apprise_metadata(None)
            self.assertEqual(metadata["APPRISE_LIB_VERSION"], "library-test-version")
            self.assertEqual(metadata["APPRISE_API_VERSION"], __version__)
            response = self.client.get("/")

        self.assertContains(response, f"v{__version__}</a>")
        self.assertContains(response, f"css/base.css?v={__version__}")

    def test_version_survives_renamed_source_deployment(self):
        """Docker and LXC copies can read the version without an installed package."""
        with tempfile.TemporaryDirectory() as temp_dir:
            webapp = Path(temp_dir) / "webapp"
            shutil.copytree(
                Path(settings.BASE_DIR) / "core", webapp / "core", ignore=shutil.ignore_patterns("__pycache__")
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-c",
                    "import sys; sys.path[:0] = sys.argv[1:]; "
                    "from core.settings import APP_VERSION; sys.stdout.write(APP_VERSION)",
                    str(webapp),
                    *site.getsitepackages(),
                ],
                cwd=temp_dir,
                env={**os.environ, "APPRISE_CONFIG_DIR": str(Path(temp_dir) / "config"), "APPRISE_AUTH_REQUIRED": "no"},
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )

        self.assertEqual(result.stdout, __version__)

    def test_terramaster_builder_uses_api_version(self):
        """Automatic package version detection shares the runtime version."""
        repo_root = Path(settings.BASE_DIR).parent
        builder = runpy.run_path(str(repo_root / "packaging" / "terramaster" / "build-package.py"))
        self.assertEqual(builder["detect_version"](), __version__)
