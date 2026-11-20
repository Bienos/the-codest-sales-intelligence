# ADR-0002 PostgreSQL-backed durable queue
`jobs` table with `FOR UPDATE SKIP LOCKED`, leases, idempotency keys, backoff honouring Retry-After, dead letters and replay. Gives transactional enqueue with domain writes and no new infrastructure. Revisit (Temporal/SQS) if throughput or multi-region needs appear; the handler interface is queue-agnostic.
