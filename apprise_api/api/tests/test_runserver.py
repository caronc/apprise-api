from unittest.mock import patch

from django.test import SimpleTestCase

from apprise_api.runserver import install_apprise_branch, parse_args


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
        assert "--no-deps" in command

    def test_install_apprise_branch_rejects_shell_characters(self):
        with (
            patch("subprocess.check_call") as mock_check_call,
            self.assertRaises(ValueError),
        ):
            install_apprise_branch("master;echo-nope")

        mock_check_call.assert_not_called()
