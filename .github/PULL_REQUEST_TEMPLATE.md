## Description
**Related issue (if applicable):** #<!-- apprise-api issue number goes here -->

<!--
  -- Have anything else to describe?
  -- Define it here.
-->

<!-- The following must be completed or your PR cannot be merged -->
## Checklist
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

# Run with Docker
docker compose up
```
