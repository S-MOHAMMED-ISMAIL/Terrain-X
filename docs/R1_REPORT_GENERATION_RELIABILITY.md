# R1 Report Generation Reliability

## Outcome

R1 gives every report a deterministic path from the existing active states (`pending` and
`generating`) to a terminal state (`completed` or `failed`). It adds no database migration,
queue service, scheduler, or frontend-only timeout.

## VERIFIED: Old lifecycle and failure mode

- The API created a `pending` report row and enqueued `report-{report_id}` in RQ.
- The enqueue call inherited RQ 2.1.0's implicit 180-second timeout.
- The worker changed the report to `generating`, rendered files, and then committed
  `completed` plus all storage keys and the frozen metadata snapshot.
- Ordinary exceptions were caught, partial files were removed, and the report was marked
  `failed`.
- A killed work horse, worker/container crash, or lost queued job could bypass that exception
  path and leave `pending` or `generating` forever.
- The frontend polled every three seconds while either active state existed and stopped only
  for `completed` or `failed`, so an orphan caused indefinite polling.
- Download endpoints already required `completed`, preventing active/failed files from being
  presented as successful reports.

The existing PostgreSQL enum already supports all required states. `pending` is the existing
name for the queued state, so no migration was necessary.

## VERIFIED: New lifecycle

1. Creation commits `pending` and enqueues the deterministic RQ job with an explicit timeout
   and failure callback.
2. A worker atomically claims only `pending -> generating`.
3. Successful generation writes all applicable files, then atomically commits
   `generating -> completed` with metadata and storage keys.
4. Application exceptions roll back the worker ORM session before atomically changing the
   active report to `failed` and removing its storage prefix. This keeps failure persistence
   usable after a flush or transaction error.
5. RQ timeout is persisted as `failed` with `Report generation timed out.`
6. Other failures escaping the task invoke the RQ failure callback and atomically fail the
   active report.
7. Stale `pending` or `generating` rows are atomically reconciled to `failed` by the backend
   startup/periodic sweep. Report list/status polling also performs ownership-scoped
   reconciliation.
8. The existing frontend receives the backend terminal state and naturally stops polling.

## VERIFIED: Timeout relationship

| Layer | Setting | Default | Semantics |
| --- | --- | ---: | --- |
| RQ report execution | `REPORT_GENERATION_TIMEOUT_SECONDS` | 900 s | Maximum executing work-horse time |
| Backend stale policy | `STALE_REPORT_AFTER_SECONDS` | 1200 s | Maximum unchanged active report age |
| Backend sweep | `STALE_JOB_SWEEP_INTERVAL_SECONDS` | 60 s | Reconciliation cadence |
| Frontend polling | `VITE_ANALYSIS_POLL_INTERVAL_MS` | 3000 ms | Read cadence only; not lifecycle authority |

Configuration validation refuses a stale threshold less than or equal to the report execution
timeout. The five-minute gap ensures a legitimate RQ work horse cannot be failed by the stale
policy while it is still within its configured execution budget.

The 900-second execution limit reuses the project's established, empirically justified worker
budget. Existing report tests complete far below it, while a report bundle can copy every
analysis artifact and therefore should not retain RQ's tight implicit 180-second default.

## VERIFIED: Reconciliation and races

- Reconciliation uses one conditional SQL `UPDATE` over active status plus stale
  `updated_at`; it does not perform a read-then-write state transition.
- Terminal rows never match reconciliation.
- Concurrent reconcilers serialize on the database row; only one can transition it.
- Duplicate or delayed RQ deliveries cannot revive a failed/completed report because worker
  claim matches only `pending`.
- Worker completion matches only `generating`. If a terminal transition already won, the
  completion update matches nothing and its newly written files are discarded.
- The deterministic RQ ID remains `report-{report_id}` and is used for persisted queue policy
  and callback handling; no new identity column is needed.

## VERIFIED: Cleanup and downloads

The report storage prefix is deleted on application failure, RQ callback failure, stale
reconciliation, or a lost completion race. Metadata and all output keys are cleared when an
active report fails. A completed report is never matched or deleted by reconciliation.

PDF, JSON, CSV, and bundle endpoints continue to require `completed` and retain their existing
ownership checks and successful file semantics.

## VERIFIED: Tests

Focused coverage includes:

- normal completion and generation exception;
- actual `JobTimeoutException` to explicit `failed` state;
- RQ timeout/failure callback classification;
- stale `pending` and `generating` reconciliation;
- fresh generating report at the timeout boundary;
- completed/failed immutability;
- repeated and concurrent reconciliation;
- late worker delivery protection;
- partial-file cleanup and null output keys;
- explicit queue timeout/callback metadata;
- completed-only downloads and unchanged ownership security.

Final validation results:

- R1 focused: 31 passed.
- Expanded report/worker/reconciliation: 40 passed.
- Full backend: 424 passed, 3 skipped in 18m17s.
- Vitest: 204 passed.
- TypeScript: clean.
- ESLint: 0 errors and 5 existing Fast Refresh warnings.
- Vite build: passed.
- `phase13-golden-path`: 1 passed.
- D1/D2/D3 combined browser regression: 5 passed.

Ruff reconciliation:

- A. R1-touched files: the nine Python production/test files pass the available local Ruff
  0.16.8 executable with no findings.
- B. Files outside the R1-touched set: repository-wide Ruff 0.16.8 reports 41 findings in
  those files, comprising 31 `I001` unsorted-import findings and 10 `UP042` string-enum
  findings.
- C. Provenance: because this repository is on an unborn branch with no commits, Git cannot
  establish whether or when those repository-wide findings were introduced. They are outside
  the R1-touched set, but must not be described as pre-existing from Git evidence.
- The project validation environment pins Ruff 0.8.4 in `requirements-dev.txt`, and the
  backend Docker image installs that pin. The available local environment therefore does not
  match the intended Ruff version.
- Docker Desktop's engine was unavailable during final reconciliation, so the pinned Docker
  Ruff run could not be repeated. The full repository must not be described as lint-clean.

## INFERENCE

- A production-size report-duration benchmark does not yet exist. The explicit 900-second
  limit is a conservative reuse of the project's measured worker budget, not a claimed maximum
  report duration.
- In the current single-backend deployment, the in-process periodic sweep is reliable whenever
  the API is healthy. Scoped status/list reconciliation additionally resolves stale rows during
  the exact frontend polling flow.

## UNRESOLVED

- A prolonged outage of both the API and worker cannot reconcile rows while both services are
  offline. Reconciliation runs immediately when the API starts again, so the state is bounded
  after service recovery rather than requiring a separate scheduler.
