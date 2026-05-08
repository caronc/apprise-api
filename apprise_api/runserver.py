import argparse
from importlib import metadata
import json
from pathlib import Path
import re
import subprocess
import sys

BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def parse_args(argv):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--branch")
    return parser.parse_known_args(argv)


def install_apprise_branch(branch):
    if not BRANCH_RE.match(branch):
        raise ValueError("Apprise branch names may only contain letters, numbers, '.', '_', '-', and '/'.")

    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--no-cache-dir",
            "--no-deps",
            f"git+https://github.com/caronc/apprise.git@{branch}",
        ]
    )


def apprise_is_vcs_installed():
    try:
        dist = metadata.distribution("apprise")
    except metadata.PackageNotFoundError:
        return False

    direct_url = Path(dist.locate_file("direct_url.json"))
    if not direct_url.is_file():
        return False

    try:
        return "vcs_info" in json.loads(direct_url.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False


def install_apprise_pypi():
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--no-cache-dir",
            "--no-deps",
            "apprise",
        ]
    )


def main(argv=None):
    options, runserver_args = parse_args(sys.argv[1:] if argv is None else argv)
    if options.branch:
        install_apprise_branch(options.branch)
    elif apprise_is_vcs_installed():
        install_apprise_pypi()

    return subprocess.call([sys.executable, "manage.py", "runserver", *runserver_args])


if __name__ == "__main__":
    raise SystemExit(main())
