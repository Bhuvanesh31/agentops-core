# AgentOps Core Data Model

## 1. Project

Represents a business or software project.

Examples:

- AgentOps Core
- AI Work Journal
- Leadle GTM Intelligence
- UC30 Revenue Dashboard

Fields:

- project_id
- project_name
- category
- status
- owner
- created_at

---

## 2. Repository

Represents a Git repository connected to a project.

A project may have one or more repositories.

Fields:

- repository_id
- project_id
- repository_name
- remote_url
- local_path
- default_branch
- created_at

---

## 3. Tool

Represents the AI development tool being used.

Examples:

- Claude Code
- Codex

Fields:

- tool_id
- tool_name
- tool_version

---

## 4. Run

Represents one AI work session.

Fields:

- run_id
- project_id
- repository_id
- tool_id
- model
- session_id
- human
- branch
- worktree
- task
- intent
- status
- started_at
- ended_at

Possible statuses:

- active
- completed
- committed
- merged
- rejected
- abandoned
- failed

---

## 5. Run Event

Represents an activity that happened during a run.

Examples:

- Session started
- Prompt submitted
- File edited
- Command executed
- Session completed

Fields:

- event_id
- run_id
- event_type
- tool_name
- files_touched
- raw_payload
- occurred_at
- received_at

---

## 6. Commit

Connects Git activity to an AI run.

Fields:

- commit_sha
- run_id
- repository_id
- branch
- author
- committed_at

---

## 7. Outcome

Records whether the work was useful or accepted.

Fields:

- outcome_id
- run_id
- verdict
- tests_passed
- merged
- reverted
- notes
- reviewed_at

Possible verdicts:

- accepted_as_is
- accepted_with_minor_changes
- accepted_with_major_changes
- rejected
- abandoned
- superseded

---

## 8. Usage

Stores available tool-usage information.

Fields:

- run_id
- input_tokens
- output_tokens
- estimated_cost
- cost_source
- iteration_count

Cost source values:

- reported
- estimated
- unavailable

Missing usage data must remain unavailable and must not be stored as zero.
