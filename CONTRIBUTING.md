# Contributing to Apprise API

Thank you for your interest in contributing to Apprise API.

Apprise API is the web application and API layer that wraps the Apprise core library. Contributions are welcome across code, bug fixes, UI improvements, documentation, and deployment tooling.

This repository uses the MIT License.

## Source of Truth

The canonical contribution documentation for Apprise API lives in the Apprise documentation site:

- https://appriseit.com/contributing/api/

This file is intentionally concise and focuses on contribution expectations and the most common local workflows.

## Quick Checklist Before You Submit

- Lint and style checks pass:
  ```bash
  tox -e lint
  ```
  This also validates the OpenAPI spec (`swagger.yaml`) and checks Django
  templates. If it reports issues, auto-fix with:
  ```bash
  tox -e format
  ```

- Tests pass:
  ```bash
  tox -e test
  ```

- For broader changes, run the full quality gate:
  ```bash
  tox -e qa
  ```
  This combines linting and tests with coverage.

- Your pull request description clearly explains what changed and why.

## Retrieve from GitHub

```bash
git clone git@github.com:caronc/apprise-api.git
cd apprise-api
```

## Development Workflows

Apprise API supports both a local (bare metal) workflow and Docker Compose workflows.

### Workflow A: Bare Metal

Run the Django development server:

```bash
tox -e runserver
# visit: http://localhost:8000/
```

Bind to a different address or port:

```bash
tox -e runserver -- "localhost:8080"
tox -e runserver -- "0.0.0.0:8080"
```

Run against a specific Apprise core branch:

```bash
tox -e runserver -- --branch=1341-retries-and-priorities
```

When `--branch` is provided, the runserver environment force-reinstalls Apprise
from that GitHub branch with pip caching disabled, so rerunning the command
refreshes branch changes. Running `tox -e runserver` without `--branch` restores
the PyPI Apprise package if the environment was previously switched to a branch.

Run the test suite (supports pytest positional arguments):

```bash
tox -e test
tox -e test -- -k "some_test_name"
```

### Workflow B: Docker Compose for Development

A fresh checkout can be run with Docker Compose. Running `docker compose up` will apply `docker-compose.override.yml` automatically, and is designed for live iteration.

```bash
# Pre-create the paths you will mount to
mkdir -p attach config plugin

# Run the stack
PUID=$(id -u) PGID=$(id -g) docker compose up
```

Notes:

- For production-style deployments, prefer the base Compose file only, so you run the immutable image and its bundled static assets.
- Development compose mounts the local source tree and static assets so template and UI changes reflect without rebuilding.

### Workflow C: Standalone Swagger UI (OpenAPI)

Apprise API includes an OpenAPI 3 specification in `swagger.yaml`.

For local development you can bring up a standalone Swagger UI that reads the checked-in spec file:

```bash
docker compose -f docker-compose.swagger.yml up -d
# browse: http://localhost:8001
```

## Repository Standards

### Linting and Formatting

`tox -e lint` runs all checks in read-only mode:

- Ruff lint check (`ruff check`)
- Ruff style check (`ruff format --check`)
- djlint for Django template linting
- OpenAPI validation for `swagger.yaml`

```bash
tox -e lint
```

`tox -e format` auto-fixes everything it can:

- Ruff lint auto-fix (`ruff check --fix`)
- Ruff style formatting (`ruff format`)
- djlint template reformatting
- CSS formatting via css-beautify

```bash
tox -e format
```

The workflow is: run `tox -e lint` to see what is wrong, then `tox -e format`
to fix it, then re-run `tox -e lint` to confirm clean.

### Tests and Coverage

Run tests:

```bash
tox -e test
```

Coverage reporting is configured via `pyproject.toml`, and the QA environment runs coverage in parallel mode as part of `tox -e qa`.

## Pull Request Guidance

- Prefer small, well-scoped pull requests.
- Include tests when practical, especially for behavioural changes.
- If you modify templates or UI, ensure `tox -e lint` is clean (djlint checks are included).
- If you modify the OpenAPI spec, ensure `tox -e lint` passes (OpenAPI validation is included).

## Thank You

Your contributions help keep Apprise API reliable and maintainable. Thank you.
