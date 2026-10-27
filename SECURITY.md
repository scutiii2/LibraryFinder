# Security policy

Only the latest released version is supported. Please update before reporting a vulnerability.

Report vulnerabilities privately through GitHub private vulnerability reporting (the repository's Security tab, "Report a vulnerability"). Do not include library source code or credentials in public issues.

## Threat model

The web UI listens on `127.0.0.1` only and is intended for one trusted local user, not network hosting or untrusted local users.
The scanner reads Python syntax only. It never imports or runs the scanned library and never reads `.env` values or credentials.
AI-assisted cataloging sends bounded source snippets to the configured MiniCPM service. Use trusted service hosts and `--no-enrichment` for source that must stay in the local scanner.
Keep `data/` (the saved library paths and catalog containing method source and explanations) private. It is gitignored.
