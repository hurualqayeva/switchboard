# ADR-0004: Terraform, Helm and GitHub Actions

Date: 2026-10-30
Status: accepted

## Context

Previous work used GitLab CI and hand-managed clusters. Options for this project:
Terraform vs AWS CDK vs Pulumi; Helm vs Kustomize; GitHub Actions vs GitLab CI.

## Decision

- Terraform for all AWS resources, modules per concern, remote state in S3 with a
  DynamoDB lock, Checkov in CI.
- Helm for Kubernetes workloads, one chart, values per environment, tested on kind.
- GitHub Actions with OIDC federation to AWS (no long-lived keys), because the repo is
  public and GitHub is where hiring managers look.

## Consequences

- Terraform is the most requested IaC tool in senior AI and platform postings and was
  absent from the CV.
- Helm keeps parity with prior Kubernetes experience and supports KEDA and
  kube-prometheus-stack as sub-charts.
- One ephemeral environment only; `terraform destroy` is part of the session checklist.
