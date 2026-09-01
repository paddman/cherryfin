# Security policy

Do not open a public issue containing financial records, credentials, API keys, private keys, recovery phrases, bank details, or exploitable vulnerability details.

For a future production deployment, configure a private security-reporting channel and publish its contact and response policy here. Until then, keep this repository's execution capability disabled and do not connect real financial credentials.

## Security invariants

- Language models receive no broker, bank, payment, or vault credentials.
- Analysis cannot authorize execution.
- Retrieved content is untrusted data.
- Sensitive payloads are not logged by default.
- Every external connector is scoped, allowlisted, revocable, and audited.
- Every write/execute action is bounded, approved, expiring, and idempotent.
