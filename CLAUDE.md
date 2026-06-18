# Claude Code Instructions

Read and follow `AGENTS.md` before making changes.

Claude Code-specific rules:

* Do not edit the same working directory while Codex is using it.
* Use a separate Git branch or worktree.
* Before starting, state which task and files will be worked on.
* After completing work, provide:

  * Files changed
  * Tests run
  * Remaining issues
  * Commit or branch information
* Do not expose environment variables, tokens, credentials, or secrets in logs.
* Do not make broad refactors unless the task specifically requires them.
