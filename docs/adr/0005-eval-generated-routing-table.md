# ADR-0005: The eval harness generates the routing table

Date: 2026-10-30
Status: accepted

## Context

Routing could be a hand-written YAML of rules ("code goes to model X"). That is how most
gateways do it and it rots immediately when prices or models change.

## Decision

The eval harness scores every catalog deployment per task class on a versioned dataset,
through the gateway itself so cost and latency are real. The routing table is the output:
the cheapest deployment per task class whose score clears that class's threshold.
Tables are versioned in Postgres, proposed as PRs, and CI fails on regression or
staleness.

## Consequences

- Routing decisions carry evidence; the admin UI can show "why this model" with scores.
- Adding a model or changing a price is a catalog change plus an eval run, no rule edits.
- Hand overrides still exist at tenant level for compliance or region pinning.
- Cost: eval runs call paid models; keep a cheap slice for iteration and run the full set
  nightly only.
- Judge-based scores carry noise; thresholds use a 2-point margin and the judge is
  calibrated against a human-labelled sample.
