# Atomity Engineering Challenge — Task 4: Resumable Review Workflow

**Candidate:** Om Chauhan  
**Task:** Task 4 — Resumable Review Workflow  
**Submission Directory:** `src/task-4_om-chauhan/`  
**Technology Stack:** Python 3.10+, LangGraph 1.2+, Pydantic v2, SQLite (`SqliteSaver`), pytest  

---

## 1. Problem Overview & Design Goals

In stateful content-generation pipelines, automated evaluators critique content, trigger revisions, and gate publication behind human-in-the-loop review. A production-ready orchestration system must satisfy four operational constraints:

1. **Durable Persistence at Review Boundaries:** When a human review is required, the system must pause and commit its graph state to non-volatile storage.
2. **True Process Resumption:** When restarting the process (e.g. CLI restart, machine reboot, pod reschedule), the workflow must resume from the saved checkpoint without repeating completed evaluation or revision work.
3. **Strict Bounded Execution:** The workflow must enforce a strict, configurable revision limit (default: 2) to eliminate the risk of infinite revision loops.
4. **Idempotency & Auditability:** Repeated or concurrent approval commands must never trigger duplicate completion events or rerun completed nodes. Every lifecycle stage must append to an inspectable audit log.

---

## 2. Architecture & LangGraph Node Graph

The workflow is constructed as an explicit, typed LangGraph `StateGraph` compiled with a transactional SQLite checkpointer (`SqliteSaver`).

### Visual Flowchart

```mermaid
flowchart TD
    START([Start]) --> evaluate[evaluate Node]
    
    evaluate --> route_eval{Evaluation Decision}
    
    route_eval -->|decision == 'revise' & count < limit| revise[revise Node]
    route_eval -->|decision == 'revise' & count >= limit| retry_limit[retry_limit Node]
    route_eval -->|decision == 'human_review'| human_review[human_review Node<br/><i>pauses via interrupt()</i>]
    route_eval -->|decision == 'reject'| reject[reject Node]
    route_eval -->|error / invalid| fail[fail Node]
    
    revise -->|Increment count, update body| evaluate
    
    retry_limit --> END_RETRY([END - RETRY_LIMIT_REACHED])
    
    human_review -.->|Persisted checkpoint to SQLite| PAUSED([PAUSED AT CHECKPOINT])
    PAUSED -.->|CLI Resume with Command| human_review
    
    human_review --> route_review{Review Decision}
    route_review -->|approve| complete[complete Node]
    route_review -->|reject| reject[reject Node]
    
    complete --> END_COMPLETED([END - COMPLETED])
    reject --> END_REJECTED([END - REJECTED])
    fail --> END_FAILED([END - FAILED])
```

### Graph Nodes

| Node | Purpose | Side Effects / Output |
| :--- | :--- | :--- |
| `evaluate` | Evaluates content item with `ContentEvaluator`. | Logs `evaluation_started`, `evaluation_completed`. Sets `evaluation_result`. |
| `revise` | Applies deterministic transformation to content text. | Increments `version` and `revision_count`. Logs `revision_requested`, `revision_completed`. Loops back to `evaluate`. |
| `retry_limit` | Terminal node when revision limit is reached. | Sets status `RETRY_LIMIT_REACHED`. Logs `retry_limit_reached`. Exits to `END`. |
| `human_review` | Pauses workflow execution using LangGraph `interrupt()`. | Logs `human_review_required` and `checkpoint_saved`. Saves state in SQLite. On resume, parses reviewer sign-off. |
| `complete` | Terminal node for human approval. | Sets status `COMPLETED`. Appends idempotent `workflow_completed` event. Exits to `END`. |
| `reject` | Terminal node for human rejection. | Sets status `REJECTED`. Logs `workflow_rejected`. Exits to `END`. |
| `fail` | Terminal node for unhandled exceptions or malformed data. | Sets status `FAILED`. Logs `workflow_failed`. Exits to `END`. |

---

## 3. Workflow State & Typed Contracts

Defined strictly via Pydantic v2 in [`src/workflow/models.py`](src/workflow/models.py):

* **`ContentItem`**: Strongly typed data container (`id`, `title`, `body`, `author`, `tags`, `metadata`, `version`). Content is treated strictly as passive, untrusted data.
* **`EvaluationResult`**: `decision` (`revise`, `human_review`, `reject`), `score` (0.0 to 1.0), `reason`, `feedback`, `metadata`.
* **`WorkflowStatus`**: Enum: `INITIALIZED`, `IN_REVIEW`, `COMPLETED`, `REJECTED`, `RETRY_LIMIT_REACHED`, `FAILED`.
* **`WorkflowStage`**: Enum: `INITIALIZED`, `EVALUATING`, `REVISING`, `HUMAN_REVIEW`, `APPROVAL`, `COMPLETED`, `REJECTED`, `RETRY_LIMIT_REACHED`, `FAILED`.
* **`WorkflowEvent`**: Audit log entry containing `event_id`, `run_id`, `event_type`, `stage`, `timestamp` (UTC ISO 8601), and structured `details`.
* **`WorkflowState`**: Top-level snapshot combining run ID, current content, status, stage, evaluation result, revision counters, and checkpoint metadata.

---

## 4. Key Mechanisms

### How the Revision Limit is Enforced
The conditional edge router `route_after_evaluation` inspects `state["revision_count"]` against `state["revision_limit"]` (default 2). When the evaluator returns `revise`:
* If `revision_count < revision_limit`: routes to `revise` node.
* If `revision_count >= revision_limit`: routes to `retry_limit` node.
This creates a hard stop, preventing runaway LLM revision loops.

### How LangGraph Checkpointing Works
LangGraph's `SqliteSaver` records state snapshots into SQLite tables (`checkpoints` and `writes`) using the `run_id` as `thread_id`. When `interrupt()` is called inside `human_review`:
1. LangGraph captures the active task execution state and writes a checkpoint.
2. The execution halts cleanly, returning control to the caller/process.
3. The process can terminate completely.

### How Resumption Works Across Process Restarts
When a new process is started with the same `run_id` and database path:
1. `WorkflowEngine` connects to the SQLite database and compiles the graph with `SqliteSaver`.
2. Invoking `graph.invoke(Command(resume=approval_payload), config={"configurable": {"thread_id": run_id}})` loads the saved checkpoint.
3. Execution resumes directly inside `human_review` at the `interrupt()` point.
4. Earlier nodes (`evaluate` and `revise`) are **never re-executed**.

### Prevention of Duplicate Approval & Idempotency
Duplicate approval protection is enforced at two distinct layers:
1. **Application Layer (`WorkflowEngine.approve` / `resume`):** Before executing any state transition, the engine checks whether the workflow is already `COMPLETED` or if `workflow_completed` has been logged. If already completed, it returns an idempotent status (`already_completed`) without modifying state or invoking the graph.
2. **Database Layer (`sqlite3` UNIQUE constraint):**
   ```sql
   CREATE UNIQUE INDEX IF NOT EXISTS uq_run_workflow_completed 
   ON workflow_events(run_id, event_type) 
   WHERE event_type = 'workflow_completed';
   ```
   This schema-level partial unique index guarantees that even under race conditions or concurrent CLI invocations, duplicate completion events cannot be inserted.

---

## 5. Storage Layout

Both checkpoints and event records are stored in a single SQLite database file (default `workflow.db` or user-specified `--db <path>`):

* **`checkpoints`**: LangGraph internal serialized execution state (msgpack blobs).
* **`writes`**: LangGraph step write records.
* **`workflow_events`**: Structured audit log containing chronological event records with timestamps, stages, and execution metadata.
* **`workflow_approvals`**: Staged human reviewer decisions for cross-process coordination.

Database connections are configured with `PRAGMA journal_mode=WAL;` and `PRAGMA busy_timeout=30000;` for concurrent multi-process access and crash resilience.

---

## 6. Installation & Setup

### Requirements
* Python 3.10, 3.11, or 3.12
* Virtual environment (recommended)

### Install Package and Dependencies

From `src/task-4_om-chauhan/`:

```bash
# Option A: Editable installation (recommended)
pip install -e .

# Option B: Install from requirements.txt
pip install -r requirements.txt
```

---

## 7. CLI Usage Guide

The CLI supports `run`, `status`, `events`, `approve`, `resume`, `demo`, and `reset`.

### 1. Start a Workflow Run
Starts execution using a content fixture and deterministic evaluator scenario. Pauses at human review.

```bash
python -m workflow run --content fixtures/content.json --scenario revise_then_review --run-id demo-001
```

Supported scenarios:
* `revise_then_review`: Revises once, then clears for human review (default).
* `immediate_review`: Clears for human review on first evaluation attempt.
* `retry_limit`: Continuously requests revisions until the maximum limit is reached.
* `malformed`: Simulates an invalid/corrupted evaluator payload to test graceful failure handling.

### 2. Inspect Current State
Displays status, stage, revision count, content details, and evaluator feedback:

```bash
python -m workflow status --run-id demo-001
```

### 3. Inspect Audit Event Log
Prints the chronological event history in a formatted ASCII table or JSON:

```bash
# Formatted table
python -m workflow events --run-id demo-001

# Raw JSON output
python -m workflow events --run-id demo-001 --json
```

### 4. Approve a Paused Workflow
Records human review sign-off:

```bash
python -m workflow approve --run-id demo-001 --decision approve --reviewer "Om Chauhan" --notes "Approved for publication."
```

### 5. Resume from Checkpoint
Resumes execution from the persisted SQLite checkpoint to terminal completion:

```bash
python -m workflow resume --run-id demo-001
```

### 6. Run the Interactive End-to-End Demo
Executes a complete demonstration of the pause, process restart, resume, and idempotency:

```bash
python -m workflow demo
```

### 7. Reset State (Development / Testing)
Safely clears checkpoints and audit events for a test run ID:

```bash
python -m workflow reset --run-id demo-001
```

---

## 8. Automated Test Suite

The test suite covers all acceptance criteria and requirements specified in Task 4.

### Run All Tests

From `src/task-4_om-chauhan/`:

```bash
python -m pytest tests -v
```

From the repository root (`engineering-challenge/`):

```bash
python -m pytest src/task-4_om-chauhan/tests -v
```

### Test Coverage Summary

| Test File | Test Case | Description & Verification |
| :--- | :--- | :--- |
| `test_workflow.py` | `test_immediate_human_review_flow` | **Test A:** Evaluates once, reaches human review, verifies SQLite checkpoint and event log, approves, and completes. |
| `test_revision_flow.py` | `test_one_revision_then_human_review` | **Test B:** Evaluator requests revision, reviser updates content, re-evaluation passes, pauses for review, approves, resumes. Verifies earlier nodes are not rerun. |
| `test_retry_limit.py` | `test_default_retry_limit_reached` | **Test C:** Evaluator always requests revision. Proves execution terminates at `retry_limit_reached` after exactly 2 revisions. |
| `test_retry_limit.py` | `test_custom_retry_limit_one` | Verifies configurable revision limit (`revision_limit=1`) enforces bounds properly. |
| `test_restart.py` | `test_cross_process_restart_and_resume` | **Test D:** Subprocess 1 runs until human review pause and exits. Subprocess 2 checks status. Subprocess 3 approves. Subprocess 4 resumes. Verifies evaluation/revision nodes were never re-executed. |
| `test_idempotency.py` | `test_duplicate_approval_and_resume_idempotency` | **Test E:** Approving an already completed workflow returns an idempotent response. Verifies exactly 1 `workflow_completed` event exists. |
| `test_idempotency.py` | `test_cli_duplicate_approval_subprocess` | Issues duplicate approval from a separate CLI subprocess. Verifies database uniqueness and idempotent handling. |
| `test_validation.py` | `test_empty_content_rejection` | **Test F:** Empty ID, title, or body is rejected by Pydantic validators before graph execution. |
| `test_validation.py` | `test_invalid_revision_limit_rejection` | Revision limit `< 1` is rejected. |
| `test_validation.py` | `test_empty_run_id_rejection` | Empty or whitespace run IDs are rejected. |
| `test_validation.py` | `test_unknown_run_id_approval` | Attempting to approve an unknown workflow fails cleanly with an error. |
| `test_validation.py` | `test_untrusted_content_sanitization` | Proves content is treated as untrusted data; executable script injection is disarmed. |
| `test_validation.py` | `test_malformed_evaluator_response_handling` | Malformed evaluator responses transition gracefully to `failed` and log `workflow_failed`. |

---

## 9. Example Audit Event History

Here is an inspectable event history generated during an execution with one revision:

```text
#   | Timestamp                 | Stage              | Event Type               | Details
---------------------------------------------------------------------------------------------------------
1   | 2026-10-26T21:30:28.413   | initialized        | workflow_started         | {"content_id": "content-001", "revision_limit": 2}
2   | 2026-10-26T21:30:28.530   | evaluating         | evaluation_started       | {"content_id": "content-001", "revision_count": 0}
3   | 2026-10-26T21:30:28.541   | evaluating         | evaluation_completed     | {"decision": "revise", "score": 0.48}
4   | 2026-10-26T21:30:28.543   | revising           | revision_requested       | {"feedback": "Evidence citations needed."}
5   | 2026-10-26T21:30:28.544   | revising           | revision_completed       | {"new_version": 2, "revision_count": 1}
6   | 2026-10-26T21:30:28.545   | evaluating         | evaluation_started       | {"content_id": "content-001", "revision_count": 1}
7   | 2026-10-26T21:30:28.546   | evaluating         | evaluation_completed     | {"decision": "human_review", "score": 0.93}
8   | 2026-10-26T21:30:28.548   | human_review       | human_review_required    | {"content_id": "content-001", "revision_count": 1}
9   | 2026-10-26T21:30:28.548   | human_review       | checkpoint_saved         | {"run_id": "demo-001", "stage": "human_review"}
10  | 2026-10-26T21:30:28.585   | approval           | human_approved           | {"notes": "Approved for publication.", "reviewer": "Om Chauhan"}
11  | 2026-10-26T21:30:28.588   | completed          | workflow_completed       | {"completed_at": "2026-10-26T21:30:28Z"}
```

---

## 10. Design Decisions, Trade-offs & Limitations

### Decisions & Rationale
1. **LangGraph Native `interrupt()` vs Custom Polling:** LangGraph's native `interrupt()` halts graph execution cleanly and relies on the checkpointer to save state. Resumption with `Command(resume=...)` continues from the exact node without restarting the graph from the root.
2. **Dedicated SQLite Connection Handles:** LangGraph's checkpointer writes in background worker threads. Sharing a single connection handle with the event logger caused SQLite transaction conflicts (`OperationalError: cannot start a transaction within a transaction`). We separated the checkpointer connection and the audit log connection while enabling SQLite WAL mode, guaranteeing zero deadlock issues.
3. **Database Uniqueness Constraint on Terminal Events:** While application-level checks prevent duplicate actions, a partial unique index on `(run_id, event_type)` ensures database-level atomicity against concurrent requests.

### Assumptions
* Local execution: SQLite file storage is used. No external server, Redis, or cloud database is required.
* Fakes: Evaluators and revisers implement standard Python `typing.Protocol` interfaces, enabling drop-in replacement with real LLM services in future production environments.

### Known Limitations
* High Concurrency: SQLite WAL mode supports multiple readers and serialized writers. For high-throughput enterprise scale (>1,000 concurrent writes/sec), LangGraph's PostgreSQL checkpointer (`PostgresSaver`) should be adopted.
* Large Binary Payloads: Content is stored within state payloads. For multi-megabyte media attachments, external object storage (e.g. S3 / GCS) with reference pointers would be preferred.
