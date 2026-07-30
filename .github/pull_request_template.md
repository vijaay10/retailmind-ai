## What & why

<!-- One paragraph. Link the FR/US id (e.g. FR-D03, US-01) this serves. -->

## Design traceability

- [ ] Matches the design docs (name section, e.g. "Backend §19") — or an ADR is attached for the deviation
- [ ] Shared IDs / contracts touched? → companion docs updated in this PR

## Evidence

- [ ] Tests added/updated (name them)
- [ ] `make lint && make test` green locally

## Security checklist (delete if N/A)

- [ ] No secrets, no PII in code/fixtures/logs
- [ ] New endpoint? authz matrix + tenant-probe tests updated
- [ ] New dependency? justified above
