# ADR-0005 Approval objects and a single executor
Every consequential action is an `approvals` row bound to target, operation and a canonical payload hash, with expiry, two-person rule and single use. Only `execute_approved` mutates external systems, behind an environment kill switch, with a marker-based fetch-before-retry for ambiguous timeouts. There is deliberately no `sent` status for drafts.
