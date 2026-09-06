# Security policy

This repository is a local lab. It is designed to run on `localhost` with dummy credentials and synthetic data. The
threat model, the n8n hardening that is enabled, and what to change before exposing the stack are documented in
[docs/security.md](docs/security.md).

## Reporting

- Found a committed secret, a real email address, or real personal data in the repo? Open a GitHub issue with the
  "Bug report" template and the file path. Do not paste the value.
- Found a vulnerability in the stack configuration (compose file, init scripts, mock-api, docgen)? Use GitHub's
  private vulnerability reporting on the repository, or open an issue titled `[security]` with steps to reproduce.
- Vulnerabilities in n8n itself belong to the n8n project, not here: <https://github.com/n8n-io/n8n/security>.

## Supported versions

Only the pinned n8n version (`2.37.10`, see [ADR 0001](docs/decisions/0001-n8n-version-pin.md)) and the image tags in
`docker-compose.yml` are tested. Bumps go through Dependabot and a compose smoke test.
