---
trigger: always_on
description: Full-autonomy workflow steering rules pre-authorizing all read operations, test execution, git operations, file modifications, and end-to-end task execution across the workspace.
---

# Autonomous Workflow Steering & Execution Authority

## 1. Standing Authorization & Full Autonomy Principle
The user has granted explicit, standing pre-authorization for almost full operational autonomy.
The agent must execute tasks end-to-end without pausing to ask conversational permission, confirmation, or "should I proceed?" questions for standard engineering workflows.

### Pre-Authorized Operations (Execute Immediately Without Confirmation):
1. **All Read Operations**:
   - Reading any file, directory, codebase, log, transcript, artifact, or system metadata across the workspace and filesystem.
   - Using native file tools (`read_file`) or diagnostic commands.
2. **All Test & Validation Execution**:
   - Running any test suite (`pytest`, `python3 -m unittest`, custom test runners, benchmarks).
   - Executing type-checkers, linters, syntax validators, and build scripts.
3. **All Git Operations**:
   - Status, diff, log, branch, remote, fetch, pull, stash inspections.
   - Staging (`git add`), creating atomic commits (`git commit`), creating or switching branches, and synchronizing with remote repositories as appropriate for the task.
4. **All Code & Configuration Modifications**:
   - Creating, editing, refactoring, and deleting project files, scripts, configurations, and plans using native tools (`write_file`, `edit_file`).
5. **Continuous Plan Execution**:
   - When given a multi-step task or implementation plan, proceed autonomously step-by-step through implementation, testing, verification, and committing without stopping between individual sub-tasks unless an insurmountable blocker or explicit safety confirmation gate is reached.

## 2. Interaction & Execution Guidelines
- **Zero Friction**: Never ask "Would you like me to run tests?", "Should I commit this?", or "Shall I proceed to the next step?". Execute the necessary action and report the verified result.
- **Prefer Native File Tools**: Always prioritize `read_file`, `edit_file`, and `write_file` over shell commands (`cat`, `head`, `sed`, `echo >`) for all file inspections and edits to ensure fast, safe, and prompt-free execution.
- **Batch Shell Commands**: When terminal execution is necessary, batch complementary commands into a single `run_command` invocation (e.g., `git add . && git commit -m "..." && PYTHONPATH=. pytest`).
- **Concise Milestone Reporting**: Keep intermediate responses focused on concrete progress, test results, and next actions. Do not flood the user with unnecessary conversational back-and-forth.

## 3. Cross-Repository Scope
This authorization applies across the entire project ecosystem under `/home/kalman/Desktop/git/`:
- `trading-agent`: Core orchestrator, decision engine, snapshot builder, execution pipeline.
- `oil-signal`: WTI/Brent signal pipeline, news sentiment, API server.
- `nvda-signal`: NVDA stock signal pipeline, news sentiment, API server.
- `trading-dashboard`: Monitoring and control UI.

## 4. Safety Guardrails (Only Exceptions Requiring Confirmation)
Autonomy applies to all software development, testing, git, and local operations. The only operations requiring explicit user confirmation are:
1. **Live Money Broker Placement**: Never execute live financial trades (`place-trade`, `place-close`, or real-account `execute-write`) on eToro without presenting the full `confirmation` block and receiving user approval per `trading-safety-rules.md`.
2. **Secret Exposure**: Never echo, display, or commit raw credentials or private API keys.
