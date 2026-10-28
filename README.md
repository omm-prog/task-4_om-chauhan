# Atomity Engineering Challenge — Task 4: Resumable Review Workflow

**Candidate:** Om Chauhan  
**Position:** AI Automation & Workflow Engineering Intern  
**Selected Task:** Task 4 — Resumable Review Workflow  
**Submission Folder:** [`engineering-challenge/src/task-4_om-chauhan/`](engineering-challenge/src/task-4_om-chauhan/)  
**Technology Stack:** Python 3.10+, LangGraph 1.2+, Pydantic v2, SQLite (`SqliteSaver`), pytest  

---

## 1. Problem Overview & Design Goals

In stateful content-generation pipelines, automated evaluators critique content, trigger revisions, and gate publication behind human-in-the-loop review. This implementation coordinates evaluation, revision, and human review with durable persistence:

1. **Durable Persistence at Review Boundaries:** When a human review is required, the system pauses and commits its graph state to persistent storage.
2. **True Process Resumption:** When restarting the process (e.g., CLI restart, machine reboot, pod reschedule), the workflow resumes from the saved checkpoint without repeating completed evaluation or revision work.
3. **Strict Bounded Execution:** Enforces an explicit, configurable revision limit (default: 2) to eliminate infinite revision loops.
4. **Idempotency & Auditability:** Repeated or concurrent approval commands never trigger duplicate completion events or rerun completed nodes. Every lifecycle stage appends to an inspectable audit log.

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

## 3. Quick Start & Verification

### Setup

```bash
# Navigate to the task submission directory
cd engineering-challenge/src/task-4_om-chauhan

# Install dependencies
pip install -r requirements.txt
```

### Run Automated Tests (13/13 Passing)

```bash
python -m pytest tests -v
```

### Run Interactive Demonstration

Executes an end-to-end demo exercising evaluation, revision, human-review pause, simulated process exit, SQLite checkpoint reload, approval, resumption, and idempotency verification:

```bash
python -m workflow demo
```

---

## 4. Key Mechanisms

### Bounded Execution
The conditional edge router `route_after_evaluation` inspects `state["revision_count"]` against `state["revision_limit"]` (default 2). When the evaluator returns `revise`:
* If `revision_count < revision_limit`: routes to `revise` node.
* If `revision_count >= revision_limit`: routes to `retry_limit` node.

### Checkpointing & Process Resumption
LangGraph's `SqliteSaver` records state snapshots into SQLite tables using `run_id` as `thread_id`. When `interrupt()` is called inside `human_review`:
1. LangGraph captures the active task execution state and writes a checkpoint.
2. The execution halts cleanly, returning control to the caller/process.
3. The process can terminate completely.
4. Resuming with `graph.invoke(Command(resume=approval_payload), config={"configurable": {"thread_id": run_id}})` loads the saved checkpoint directly without re-executing earlier nodes.

### Idempotency & Duplicate Approval Protection
Duplicate approval protection is enforced at two distinct layers:
1. **Application Layer (`WorkflowEngine.approve` / `resume`):** Checks whether the workflow is already `COMPLETED`. If so, returns an idempotent status (`already_completed`) without modifying state.
2. **Database Layer (`sqlite3` UNIQUE constraint):**
   ```sql
   CREATE UNIQUE INDEX IF NOT EXISTS uq_run_workflow_completed 
   ON workflow_events(run_id, event_type) 
   WHERE event_type = 'workflow_completed';
   ```
   Ensures duplicate completion events cannot be inserted even under concurrent invocations.

---

## 5. CLI Usage Guide

```bash
# 1. Start a workflow run (pauses at human review)
python -m workflow run --content fixtures/content.json --scenario revise_then_review --run-id demo-001

# 2. Inspect current persisted state
python -m workflow status --run-id demo-001

# 3. View the audit event log
python -m workflow events --run-id demo-001

# 4. Record human approval
python -m workflow approve --run-id demo-001 --decision approve --reviewer "Om Chauhan" --notes "Approved for publication."

# 5. Resume from checkpoint to completion
python -m workflow resume --run-id demo-001
```

---

## 6. Test Suite Coverage Summary

| Test File | Test Case | Description & Verification |
| :--- | :--- | :--- |
| `test_workflow.py` | `test_immediate_human_review_flow` | Evaluates once, reaches human review, verifies SQLite checkpoint and event log, approves, and completes. |
| `test_revision_flow.py` | `test_one_revision_then_human_review` | Evaluator requests revision, reviser updates content, re-evaluation passes, pauses for review, approves, resumes. Verifies earlier nodes are not rerun. |
| `test_retry_limit.py` | `test_default_retry_limit_reached` | Evaluator repeatedly requests revision. Proves execution terminates at `retry_limit_reached` after exactly 2 revisions. |
| `test_retry_limit.py` | `test_custom_retry_limit_one` | Verifies configurable revision limit (`revision_limit=1`) enforces bounds properly. |
| `test_restart.py` | `test_cross_process_restart_and_resume` | Subprocess 1 runs until human review pause and exits. Subprocess 2 checks status. Subprocess 3 approves. Subprocess 4 resumes. Verifies evaluation/revision nodes were never re-executed. |
| `test_idempotency.py` | `test_duplicate_approval_and_resume_idempotency` | Approving an already completed workflow returns an idempotent response. Verifies exactly 1 `workflow_completed` event exists. |
| `test_idempotency.py` | `test_cli_duplicate_approval_subprocess` | Issues duplicate approval from a separate CLI subprocess. Verifies database uniqueness and idempotent handling. |
| `test_validation.py` | `test_empty_content_rejection` | Empty ID, title, or body is rejected by Pydantic validators before graph execution. |
| `test_validation.py` | `test_untrusted_content_sanitization` | Proves content is treated as untrusted data; executable script injection is disarmed. |
| `test_validation.py` | `test_malformed_evaluator_response_handling` | Malformed evaluator responses transition gracefully to `failed` and log `workflow_failed`. |

---

## 7. Submission Structure

```text
├── README.md                                    # Root overview & quick start
├── pytest.ini                                   # Root test runner configuration
└── engineering-challenge/
    ├── challenge/
    │   ├── challenge.md                         # Challenge brief
    │   └── tasks/task-4.md                      # Task 4 specification
    └── src/
        └── task-4_om-chauhan/                   # Submission package
            ├── README.md                        # Task 4 in-depth design note
            ├── pyproject.toml
            ├── requirements.txt
            ├── fixtures/                        # Deterministic fixtures
            ├── src/workflow/                    # LangGraph implementation
            │   ├── graph.py                     # StateGraph definition
            │   ├── nodes.py                     # Workflow nodes & routers
            │   ├── engine.py                    # Orchestration & resume API
            │   ├── persistence.py              # SQLite checkpointer configuration
            │   ├── event_store.py              # Audit logging & database constraints
            │   ├── models.py                    # Pydantic v2 typed state & contracts
            │   ├── evaluator.py                 # Deterministic fake evaluator
            │   ├── reviser.py                   # Deterministic fake reviser
            │   └── cli.py                       # CLI interface
            └── tests/                           # 13 automated test suites
```