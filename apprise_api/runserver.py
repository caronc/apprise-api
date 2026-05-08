import argparse
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
            "--no-deps",
            f"git+https://github.com/caronc/apprise.git@{branch}",
        ]
    )


def main(argv=None):
    options, runserver_args = parse_args(sys.argv[1:] if argv is None else argv)
    if options.branch:
        install_apprise_branch(options.branch)

    return subprocess.call([sys.executable, "manage.py", "runserver", *runserver_args])


if __name__ == "__main__":
    raise SystemExit(main())
