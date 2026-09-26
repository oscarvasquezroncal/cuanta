# Security policy

## Supported versions

Security fixes target the latest `0.3.x` release. Older versions do not receive security
backports. Upgrade to the latest supported version before checking whether a problem persists.

## Reporting a vulnerability

Report vulnerabilities privately through GitHub's private vulnerability reporting: open the
repository's [Security tab](https://github.com/oscarvasquezroncal/cuanta/security) and choose
[Report a vulnerability](https://github.com/oscarvasquezroncal/cuanta/security/advisories/new).
Do not share vulnerability details, exploit code or sensitive data anywhere public.

Include the cuanta version, operating system, affected engine and version, a minimal
reproduction using synthetic data, and the expected impact. Remove credentials, private
prompts, client files and local personal paths from logs.

See the [engine guarantees and privacy limits](README.md#privacy-and-safety) before relying
on a sandbox, permission rule or spend cap. Engine-specific verification is recorded in
[CONTRACTS.md](docs/CONTRACTS.md).
