# Design Documentation Index

The nine-document specification set governs this repository. Shared IDs (FR-x,
US-x, phase tags) are authoritative across all documents; changes to shared
contracts update every affected document in the same PR.

| # | Document | Governs |
|---|---|---|
| 1 | System Architecture | Planes, engines, ADR log, NFRs, roadmap skeleton |
| 2 | PRD | Scope, priorities, MVP, sprints, gates G1–G7, UX principles |
| 3 | Database Design | OLTP schema, star schema, SCD2, security, operations |
| 4 | Backend Design | FastAPI layering, patterns, API contracts |
| 5 | ETL Platform | Connectors, quality gates, loading, scheduling |
| 6 | Analytics Engine | 10 BI modules, 15 methods, metric registry content |
| 7 | AI Capabilities | 10 AI components, prompts, confidence, evaluation |
| 8 | UX Specification | Design system, 13 screens, states |
| 9 | DevOps Guide | Profiles, CI/CD, security, DR, maintenance |

<!-- TODO(S1): commit the 9 documents as markdown files in this directory. -->

## ADRs

Architecture Decision Records live in [`adr/`](adr/) — see the
[template](adr/adr-template.md). ADR-001…006 (from ARCH §7) are the founding
decisions and should be committed first.
