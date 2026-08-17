# Threat Model

## Purpose

This document captures the intended safety posture, legal boundaries, and handling assumptions for Garden.

Garden is built for authorized, bounded, internal security verification of authenticated application surfaces. It is not designed for exploit execution, broad internet scanning, or destructive testing.

## Authorized Use Only

Garden is intended for:

- legal use
- authorized use
- bounded targets
- internal or approved demo environments
- non-destructive verification workflows

It must not be positioned as a tool for unauthorized access, mass scanning, or offensive exploitation.

## Default Posture

Garden is safe by default:

- authorized public and loopback targets are enabled by default
- private RFC1918/ULA targets require explicit opt-in
- no destructive checks
- no exploit modules
- no mass or unauthorized scanning
- no true IDOR exploitation
- no dangerous file upload testing
- no aggressive crawling or fuzzing

This default posture is a core product constraint, not just an implementation detail.

## Target Restrictions

By default, Garden allows:

- `localhost`
- `127.0.0.1`
- authorized public targets

Operators must:

- submit only targets for which they have explicit authorization
- set `GARDEN_ALLOW_NON_LOCAL_TARGETS=false` when local-only operation is required

Private RFC1918/ULA targets require the separate
`GARDEN_ALLOW_PRIVATE_TARGETS=true` opt-in. URL scans resolve and classify DNS
answers before connecting and repeat admission checks for redirects. Embedded
URL credentials, link-local/cloud-metadata destinations, multicast, unspecified
addresses, mixed allowed/denied DNS answers, unsupported schemes, and unbounded
redirects are rejected. Environment proxy variables are ignored unless a scan
proxy is explicitly configured.

Passive collection is bounded by per-request and overall timeouts, response-size,
page/depth, redirect, retry, and concurrent-task limits. Only GET requests are
issued by the automatic URL pipeline.

## Non-Destructive Behavior

Garden's checks are marker-based and review-oriented. They are not meant to alter state or exploit vulnerabilities.

Examples of what Garden intentionally avoids:

- cross-user data access attempts
- exploit payload delivery
- brute force
- broad fuzzing
- destructive import or upload attempts
- malicious workflow automation

## Redaction Strategy

Redaction is on by default across display and export paths.

Garden masks or partially masks:

- `Cookie`
- `Authorization`
- bearer tokens
- session identifiers
- passwords
- secrets
- keys
- token-like strings
- email addresses with partial masking

Redaction is centralized so CLI, UI, and export outputs do not drift into inconsistent exposure behavior.

## Evidence Handling

Evidence is designed to be reviewable without becoming a casual leak channel.

Key handling assumptions:

- previews are bounded in size
- screenshot files are indexed separately from structured payload JSON
- raw session or request material is not casually shown in UI or exports
- exports remain redacted by default
- evidence should stay structured and traceable, not pile up as random files

## Secret Handling Assumptions

Garden assumes:

- secrets are supplied through environment variables or secret references
- real secrets are never committed into the repository
- `secret_ref` is stored, not raw secret material
- display surfaces never print secrets back to the user

The repo examples only use local demo placeholders and safe example values.

## Audit and Logging Concerns

Important workflow events should remain auditable, especially:

- login attempts
- session validation
- session refresh
- finding status changes
- retest runs
- evidence exports
- report exports

Audit detail should be useful without exposing sensitive material.

Logs should support troubleshooting and workflow review, but should not become a side-channel for raw session or secret leakage.

## Trust Boundaries

Garden crosses several trust boundaries:

- reading local config files
- resolving secret references
- restoring authenticated session state
- collecting post-login pages and network observations
- storing evidence and exports

The architecture therefore keeps these concerns separate:

- config parsing
- auth adapter execution
- session storage
- inventory collection
- redaction
- export generation
- audit recording

## False-Positive and Over-Collection Risks

Garden tries to stay useful without becoming noisy or over-collecting data.

Primary risks include:

- collecting more response detail than needed
- generating low-value findings repeatedly
- over-broad dedup logic hiding context
- weak redaction missing uncommon sensitive fields
- treating internal demo artifacts as production-grade conclusions

These risks are mitigated through:

- bounded previews
- explicit finding metadata
- dedup with preserved `first_seen` / `last_seen`
- marker-based low-risk checks
- conservative local demo defaults

## Practical Security Promise

Garden's promise is not "fully safe under every misuse scenario."  
Its practical promise is:

- it defaults to local and authorized use
- it avoids exploit behavior
- it redacts evidence and exports by default
- it keeps sensitive workflow steps auditable
- it stays narrow enough to be credible as an internal AppSec MVP
