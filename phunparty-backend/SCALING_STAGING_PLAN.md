# Scaling And Concurrency Staging Plan

This branch has started the low-risk scaling fixes from the audit. The remaining items are larger architectural changes and should be landed in separate commits or pull requests with focused tests.

## Completed On This Branch

- Atomic Redis rate limiting with one Lua `EVAL` per hit.
- Beat the Clock hot paths can read one player's Redis state instead of scanning the full player hash.
- Reverse indexes for WebSocket-to-connection and player-to-connection lookups.
- NAT-aware WebSocket connection limiting with a post-auth player/session limiter.
- Standard trivia answer submission skips the progression row lock when the question is clearly still waiting for more players.
- Redis Pub/Sub dispatch is queued per session so one large local broadcast does not block unrelated sessions.
- Roster updates during join/reconnect churn are debounced.

## Next Stage 1: Async Redis State Access

Goal: remove synchronous Redis calls from async WebSocket hot paths.

Suggested scope:
- Add async equivalents for shared JSON/hash helpers in `ConnectionManager`.
- Convert per-message hot paths first:
  - connection generation read/write
  - phase reads
  - Beat the Clock per-player reads/writes
  - Fair Play hash reads/writes
- Keep sync helpers temporarily for non-async call sites.

Proof:
- Existing regression suite.
- New tests using a fake async Redis client.
- Manual two-worker smoke test with Redis enabled.

## Next Stage 2: Synchronous DB Isolation

Goal: stop blocking the event loop with synchronous SQLAlchemy sessions during WebSocket message handling.

Migration-safe option:
- Introduce a small helper that runs synchronous DB work in a worker thread.
- Create and close the SQLAlchemy session inside that worker thread.
- Move one message path at a time, starting with standard answer submission.

Do not pass an existing SQLAlchemy `Session` from the event loop into a worker thread.

Long-term option:
- Add SQLAlchemy async engine/session support using an async PostgreSQL driver.
- Migrate route groups incrementally.

Proof:
- Regression tests around RLS context.
- One local multi-client answer test.
- Event-loop lag measurement before/after.

## Next Stage 3: Join Queue Decision

Goal: remove or redesign process-local join queue behavior.

Preferred direction:
- Make direct join idempotent using database constraints and conflict-safe writes.
- Keep the application path stateless across workers.
- Remove queue-status dependence on process-local memory.

Proof:
- Duplicate join tests.
- Two-worker test where join and status/read happen on different workers.

## Next Stage 4: Presence Snapshot Consolidation

Goal: avoid repeated presence scans inside one logical roster/stat operation.

Suggested scope:
- Add one snapshot function that returns raw shared/local presence once.
- Derive mobile players, stats, duplicate breakdown, readiness, and host counts from that snapshot.
- Replace duplicated `get_session_stats` definitions with one implementation.

Proof:
- Existing roster tests.
- New test that a roster broadcast reads shared presence once.

## Next Stage 5: Load Test Harness

Goal: prove behavior under realistic realtime load, not generic HTTP load.

Scenarios:
- WebSocket connection capacity.
- Standard trivia answer burst in one large session.
- Many small simultaneous sessions.
- Beat the Clock answer burst.
- Reconnect storm.

Metrics to collect:
- answer latency p50/p95/p99
- broadcast duration p95/p99
- event-loop lag
- Redis command latency
- PostgreSQL pool checkout wait
- PostgreSQL row-lock wait
- time from last answer to next question

## Deployment Note

Schema maintenance should eventually move out of app worker startup and into a one-time deployment migration step. Do this after the current runtime scaling work is stable, because it changes deployment operations rather than request behavior.
