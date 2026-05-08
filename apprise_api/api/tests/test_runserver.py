from importlib import metadata
import json
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
from unittest.mock import patch
import warnings

from django.test import SimpleTestCase

from apprise_api.runserver import (
    apprise_is_vcs_installed,
    install_apprise_branch,
    install_apprise_pypi,
    main,
    parse_args,
)


class _Distribution:
    def __init__(self, direct_url):
        self.direct_url = direct_url

    def locate_file(self, path):
        return self.direct_url if path == "direct_url.json" else path


class RunserverTests(SimpleTestCase):
    def test_parse_branch_option(self):
        options, runserver_args = parse_args(["--branch=feature/retry", "127.0.0.1:8001"])

        assert options.branch == "feature/retry"
        assert runserver_args == ["127.0.0.1:8001"]

    def test_install_apprise_branch(self):
        with patch("subprocess.check_call") as mock_check_call:
            install_apprise_branch("1341-retries-and-priorities")

        command = mock_check_call.call_args.args[0]
        assert command[-1] == "git+https://github.com/caronc/apprise.git@1341-retries-and-priorities"
        assert "--no-cache-dir" in command
        assert "--no-deps" in command

    def test_install_apprise_branch_rejects_shell_characters(self):
        with (
            patch("subprocess.check_call") as mock_check_call,
            self.assertRaises(ValueError),
        ):
            install_apprise_branch("master;echo-nope")

        mock_check_call.assert_not_called()

    def test_install_apprise_pypi(self):
        with patch("subprocess.check_call") as mock_check_call:
            install_apprise_pypi()

        command = mock_check_call.call_args.args[0]
        assert command[-1] == "apprise"
        assert "--no-cache-dir" in command
        assert "--no-deps" in command
        assert "--force-reinstall" in command

    def test_apprise_is_vcs_installed(self):
        with TemporaryDirectory() as tmpdir:
            direct_url = Path(tmpdir) / "direct_url.json"
            direct_url.write_text(
                json.dumps(
                    {
                        "url": "https://github.com/caronc/apprise.git",
                        "vcs_info": {"vcs": "git"},
                    }
                ),
                encoding="utf-8",
            )
            with patch("apprise_api.runserver.metadata.distribution", return_value=_Distribution(direct_url)):
                assert apprise_is_vcs_installed() is True

            direct_url.write_text(json.dumps({"url": "https://files.pythonhosted.org/apprise.whl"}), encoding="utf-8")
            with patch("apprise_api.runserver.metadata.distribution", return_value=_Distribution(direct_url)):
                assert apprise_is_vcs_installed() is False

            direct_url.write_text("{", encoding="utf-8")
            with patch("apprise_api.runserver.metadata.distribution", return_value=_Distribution(direct_url)):
                assert apprise_is_vcs_installed() is False

            direct_url.unlink()
            with patch("apprise_api.runserver.metadata.distribution", return_value=_Distribution(direct_url)):
                assert apprise_is_vcs_installed() is False

    def test_apprise_is_vcs_installed_missing_distribution(self):
        with patch(
            "apprise_api.runserver.metadata.distribution",
            side_effect=metadata.PackageNotFoundError,
        ):
            assert apprise_is_vcs_installed() is False

    def test_main_restores_pypi_after_branch_install(self):
        with (
            patch("apprise_api.runserver.apprise_is_vcs_installed", return_value=True),
            patch("apprise_api.runserver.install_apprise_pypi") as mock_install_pypi,
            patch("subprocess.call", return_value=0) as mock_call,
        ):
            assert main([]) == 0

        mock_install_pypi.assert_called_once_with()
        assert mock_call.call_args.args[0][-2:] == ["manage.py", "runserver"]

    def test_main_does_not_reinstall_pypi_when_already_pypi(self):
        with (
            patch("apprise_api.runserver.apprise_is_vcs_installed", return_value=False),
            patch("apprise_api.runserver.install_apprise_pypi") as mock_install_pypi,
            patch("subprocess.call", return_value=0),
        ):
            assert main([]) == 0

        mock_install_pypi.assert_not_called()

    def test_main_installs_requested_branch(self):
        with (
            patch("apprise_api.runserver.install_apprise_branch") as mock_install_branch,
            patch("apprise_api.runserver.apprise_is_vcs_installed") as mock_is_vcs,
            patch("subprocess.call", return_value=0) as mock_call,
        ):
            assert main(["--branch=feature/retry", "127.0.0.1:8001"]) == 0

        mock_install_branch.assert_called_once_with("feature/retry")
        mock_is_vcs.assert_not_called()
        assert mock_call.call_args.args[0][-1] == "127.0.0.1:8001"

    def test_module_entrypoint(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            with (
                patch("apprise_api.runserver.apprise_is_vcs_installed", return_value=False),
                patch("subprocess.call", return_value=7),
                patch("sys.argv", ["apprise_api.runserver"]),
                self.assertRaises(SystemExit) as ctx,
            ):
                runpy.run_module("apprise_api.runserver", run_name="__main__")

        assert ctx.exception.code == 7
