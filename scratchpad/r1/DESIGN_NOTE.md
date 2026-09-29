# R1 Report Generation Reliability - Design Note

## Status

Implemented lifecycle trace and final design decision.

## VERIFIED: Existing lifecycle

1. `POST /projects/{project_id}/datasets/{dataset_id}/reports` validates ownership and that at
   least one completed analysis exists.
2. `create_report` commits a `Report` row with status `pending`, then enqueues
   `run_report_generation` with deterministic RQ job ID `report-{report.id}`.
3. The enqueue call does not currently set `job_timeout`, so RQ 2.1.0 applies its default
   180-second timeout.
4. The worker calls `generate_report`, which unconditionally changes the row to `generating`.
5. Successful generation writes PDF, optional CSV, and ZIP files, then commits metadata,
   storage keys, `completed_at`, and status `completed`.
6. An ordinary exception inside `generate_report` deletes the report storage prefix, clears
   output keys, and commits status `failed` with the exception message.
7. RQ raises `JobTimeoutException` in the work horse. The current broad exception handler may
   catch that exception, but there is no explicit report timeout configuration or fallback if
   the work horse is killed before the database failure transition commits.
8. A worker/container crash, hard kill, restart, or disappearance can leave the database row
   in `pending` or `generating` forever. The existing stale-job sweep only handles
   `AnalysisJob.status == running`; it does not inspect reports.
9. The frontend polls the report list every three seconds while any report is `pending` or
   `generating`, and stops naturally when all reports are `completed` or `failed`. It has no
   client-side terminal timeout, correctly leaving lifecycle authority with the backend.
10. Downloads already require `completed`; partial files are not downloadable while a report
    is active or failed.

The database enum already contains `pending`, `generating`, `completed`, and `failed`.
`pending` is the existing queued state. No schema change or migration is needed.

## VERIFIED: Existing timeout precedent

- RQ version: 2.1.0.
- Current report timeout: RQ's implicit 180-second default.
- Existing analysis timeout: `ANALYSIS_JOB_TIMEOUT_SECONDS=900`, based on a measured
  170.7-second worst-case analysis run and deliberately allowing slower/cold hosts.
- Existing stale analysis threshold: 1200 seconds.
- Existing backend reconciliation cadence: immediate at API startup, then every 60 seconds.
- Report generation consumes already-persisted results and renders/archives them. Existing
  tests complete it well inside the analysis budget, but they are not a production-size
  benchmark; bundle time also depends on artifact volume.

## INFERENCE: Timeout policy

Use `REPORT_GENERATION_TIMEOUT_SECONDS=900`. This is not a new tight estimate: it reuses the
project's already justified maximum worker-execution budget and is substantially above both
RQ's old 180-second implicit limit and observed test report durations. It avoids falsely
terminating a large but legitimate bundle without making report work unbounded.

Use `STALE_REPORT_AFTER_SECONDS=1200`, safely above the 900-second RQ limit by five minutes.
The existing 60-second sweep means an orphan is normally terminal no later than roughly
21 minutes after its last committed state change. Status/list polling will also invoke scoped
reconciliation, so a user viewing the report receives the terminal state without waiting for
the next periodic sweep.

## New lifecycle

1. Create and commit `pending` report.
2. Enqueue with deterministic job ID, explicit 900-second timeout, and an RQ failure callback.
3. Worker atomically claims only `pending -> generating`. Duplicate or late deliveries cannot
   resurrect `failed` or `completed` rows.
4. Success writes files first, then atomically commits `generating -> completed` with all
   storage keys and metadata.
5. Application exceptions first roll back the worker ORM session, then atomically commit
   `generating -> failed` and remove the report storage prefix. This prevents a failed ORM
   transaction from blocking failure persistence.
6. RQ failure/timeout callback atomically fails an active (`pending` or `generating`) row. A
   timeout uses the explicit message `Report generation timed out.`
7. Periodic or status-request reconciliation atomically changes stale `pending`/`generating`
   rows to `failed`, clears all output metadata/keys, and removes partial files.

## Race handling

- All lifecycle transitions use conditional SQL updates rather than read-then-write status
  assignment.
- Reconciliation matches only active states and an old `updated_at`, so terminal rows are
  immutable and fresh workers are untouched.
- Concurrent reconciliation calls serialize at the database row; only one update can match.
- Worker completion matches only `generating`. If reconciliation already won, completion
  cannot overwrite `failed`, and newly written partial files are removed.
- A late/duplicate RQ delivery can claim only `pending`; it cannot move `failed` back to
  `generating`.

## Cleanup semantics

The report storage prefix is private until the database row becomes `completed`. Failure and
stale reconciliation delete that prefix and clear metadata/storage keys. A completed report
never matches reconciliation and its successfully generated files are never deleted.

## Implemented tests

- Existing normal completion and ordinary exception tests remain.
- Explicit RQ timeout and failure callback behavior.
- Stale `generating` and stale `pending` reconciliation.
- Boundary checks around the stale threshold without sleeping.
- Terminal-state immutability and repeated reconciliation idempotence.
- Concurrent reconciliation safety.
- Duplicate/late worker delivery cannot revive failed reports.
- Partial artifacts are deleted and never exposed as completed.
- Downloads remain completed-only and ownership remains unchanged.
- Queue job stores the configured timeout and failure callback.

## UNRESOLVED

- There is no production-size report-generation benchmark. The 900-second limit is therefore a
  conservative reuse of the project's empirically justified worker budget, not a measured
  maximum report duration. Per-report generation timings remain available for future tuning.
- A total Redis outage prevents enqueue and is already handled immediately by `create_report`.
  Reconciliation itself depends only on Postgres and storage, so it does not require Redis to
  recover already-orphaned reports.
