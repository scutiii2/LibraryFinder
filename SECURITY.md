# Security policy

Only the latest released version is supported. Please update before reporting a vulnerability.

Report vulnerabilities privately through GitHub private vulnerability reporting (the repository's Security tab, "Report a vulnerability"). Do not include Microsoft 365 data, library source code or credentials in public issues.

## Threat model

The web UI listens on `127.0.0.1` only and is intended for one trusted local user, not network hosting or untrusted local users.
The scanner reads Python syntax only. It never imports or runs the scanned library and never reads `.env` values or credentials.
AI-assisted cataloging sends bounded source snippets of the scanned library to the Microsoft 365 Copilot account signed in to Library Finder's dedicated Edge profile. Use `--no-copilot` for source that must not leave the machine.
Keep `.profiles/` (the signed-in Edge profile) and `catalog.json` (method source and explanations) private. Both are gitignored.
