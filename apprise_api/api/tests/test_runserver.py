from unittest.mock import patch

from django.test import SimpleTestCase

from apprise_api.runserver import install_apprise_branch, install_apprise_pypi, main, parse_args


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
