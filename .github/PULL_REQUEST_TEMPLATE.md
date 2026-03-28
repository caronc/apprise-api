## Description
**Related issue (if applicable):** #<!-- apprise-api issue number goes here -->

<!--
  -- Have anything else to describe?
  -- Define it here.
-->

<!-- The following must be completed or your PR cannot be merged -->
## Checklist
* [ ] Documentation ticket created (if applicable): [apprise-docs/##](https://github.com/caronc/apprise-docs/pull/<!--apprise-docs pull request ## goes here-->)
* [ ] The change is tested and works locally.
* [ ] No commented-out code in this PR.
* [ ] No lint errors (use `tox -e lint` and optionally `tox -e format`).
* [ ] Test coverage added or updated (use `tox -e qa`).

## Testing
<!-- If your change is testable by others, define how to validate it here -->
Anyone can help test as follows:
```bash
# Clone the branch
git clone -b <this.branch-name> https://github.com/caronc/apprise-api.git
cd apprise-api

# Run the unit tests
tox -e test

# Alternatively, spin up the full stack with Docker
docker compose up
```
