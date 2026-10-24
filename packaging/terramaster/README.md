# TerraMaster TOS 7 Packaging
```bash
# Uses the shared version in apprise_api/core/version.py:
python3 packaging/terramaster/build-package.py

# Explicit Configuration
python3 packaging/terramaster/build-package.py \
    --version 1.5.2 --platform aarch64
```
**Platform.** Terraform requires a separate submission per platform
(x86_64 / aarch64) but the Docker-app asset naming rule
(`<appid>.tar.gz`) has no room/support for a platform suffix.

This script defaults to `--platform x86_64` and can produce an `aarch64` variant on request.

## Releasing

Once a version is tagged and pushed (see the Apprise API deployment
instructions), build and attach the package to that same GitHub Release as follows:

```bash
# Build package
python3 packaging/terramaster/build-package.py

# Upload assets created in /dist directory
gh release upload vX.Y.Z \
    packaging/terramaster/dist/apprise-terramaster-tos7-app.tar.gz \
    packaging/terramaster/dist/apprise-terramaster-tos7-app.tar.gz.sha256
```

The Release tag, `config.ini.version`, and the version entered on the TNAS
Developer Platform must all match exactly.

## Privacy Policy

TerraMaster requires a privacy policy link with each submission. Use:

```text
https://appriseit.com/api/privacy/
```

The template values in `docker-compose.yml` must be literal (for example
`PUID=1000`, not `PUID=${PUID:-1000}`) because the TOS App Center does not
expand them.
