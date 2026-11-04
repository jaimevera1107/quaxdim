# Security policy

Supported public line: **0.8.x pre-releases** (no stable security promises yet).

Do not open public issues containing real records, API keys, credentials or
private datasets. Report security problems using GitHub's private vulnerability
reporting feature, if enabled on the repository. Otherwise contact the repository
owner privately using a non-public channel.

By default the library runs locally, does not send telemetry, and does not
require network credentials. Third-party backends and optional dependencies may
have their own security considerations. Treat untrusted contracts as input that
requires validation and run with least-privileged access.
