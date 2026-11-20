# Security Policy

## Supported Versions

Security fixes go into the latest release.

| Version | Supported          |
| ------- | ------------------ |
| 2.1.x   | :white_check_mark: |
| < 2.1.0 | :x:                |

## Reporting a Vulnerability

Please report vulnerabilities privately through [GitHub's private reporting](https://github.com/caronc/apprise-api/security/advisories/new), not in a public issue. Include the version, the settings involved, and the steps to reproduce it.

## Security Model

Apprise API sends notifications on behalf of whoever can reach it. Out of the box it has no logins, so it is meant for a trusted network or to sit behind something that controls access. The following are how the server works by design, not vulnerabilities:

- **Open by default.** Without `APPRISE_AUTH_REQUIRED=yes`, anyone who can reach the server can save configurations, send notifications and use every other feature, including reminders.
- **Callers choose where notifications go.** With stateless mode on, a caller can send to any notification URL. With reminders on, a reminder can hold its own notification URLs too.
- **Work that lasts.** Saved configurations, reminders and their attachments stay on disk, and reminders keep sending until they end or are removed.

A server reachable by people you do not trust needs logins and the other settings in the [Apprise API Security Guide](https://appriseit.com/api/security/). Reports are welcome when one of those settings does not do what it says, or when a caller gets past a login or an access level.
