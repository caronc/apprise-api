# Contributing to Apprise API

Thank you for your interest in contributing to Apprise API.

Apprise API is the web application and API layer that wraps the Apprise core library. Contributions are welcome across code, bug fixes, UI improvements, documentation, and deployment tooling.

This repository uses the MIT License.

## Source of Truth

The canonical contribution documentation for Apprise API lives in the Apprise documentation site:

- https://appriseit.com/contributing/api/

This file is intentionally concise and focuses on contribution expectations and the most common local workflows.

## Quick Checklist Before You Submit

- Lint passes:
  ```bash
  tox -e lint
  ```
  This also validates the OpenAPI spec (`swagger.yaml`) and checks Django templates.

- Tests pass:
  ```bash
  tox -e test
  ```

- Code is formatted:
  ```bash
  tox -e format
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

Linting checks:

- Ruff for Python linting
- djlint for Django template linting
- OpenAPI validation for `swagger.yaml`

Run:

```bash
tox -e lint
```

Auto-format:

```bash
tox -e format
```

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
