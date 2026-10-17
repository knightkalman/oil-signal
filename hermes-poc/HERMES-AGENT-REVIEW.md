# Hermes Agent: Subagent Implementation Review

## Scope and source

This note records a static review of the subagent implementation in
[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent), inspected
at commit [`66888832345561efe66d334ff4d65fe35203018b`](https://github.com/NousResearch/hermes-agent/commit/66888832345561efe66d334ff4d65fe35203018b)
on 2026-10-17. The review focused on how agents are created, scheduled, supervised,
and returned to the parent. It did not run Hermes or its test suite.

## Summary

Hermes has a substantial subagent implementation centered on the `delegate_task`
tool. A parent can launch one child or a batch of independent children. Each child
is a fresh `AIAgent` with its own conversation and task-specific goal/context; it
does not inherit the parent conversation history. Parent and child share the
project workspace by default, but can optionally use separate Git worktrees.

Top-level model-initiated delegations normally run in the background and return a
dispatch handle. Their result is delivered back into the conversation later.
Nested orchestrator children run delegation synchronously so they can use child
results in their own turn. If asynchronous delivery is not supported or the
background pool cannot accept work, Hermes falls back to synchronous execution.

**Assessment:** the inspected launch and dispatch design is mature and includes
resource ownership, interruption, status/control, persistence, and failure
handling paths. No clear blocker to trying it was identified in this static
inspection. This is not a security audit or a guarantee that every provider,
transport, or deployment mode works without configuration.

## Implementation notes

### Child creation and capabilities

- Child agents are constructed on the main thread, then run by worker threads;
  the code explicitly avoids constructing them concurrently.
- Each gets a fresh system prompt based on its goal, supplied context, and
  discovered project context files. Anything else needed from the parent’s
  discussion must be passed in the task.
- Children inherit the parent’s tool access by default, with role/depth-based
  restrictions. Delegation depth is flat by default (`max_spawn_depth: 1`);
  nested orchestration requires raising the limit. `orchestrator_enabled` is a
  global kill switch.
- Provider/model routing can inherit from the parent or be overridden through
  delegation configuration. Child credential pools can be shared for
  credential rotation/rate-limit handling.
- Children receive separate session database handles when a parent has a
  session DB, avoiding parent teardown closing the child’s persistence handle.

### Scheduling, joining, and delivery

- Task batches execute in parallel up to `delegation.max_concurrent_children`.
- Background dispatch can keep a whole call together or split it into completion
  units by task/group when `delegation.independent_completions` is enabled.
- Results are collected and sorted by task index. Failed, interrupted, timed-out,
  and schema-invalid outputs have explicit result states/details; optional
  `output_schema` validation allows a bounded correction retry.
- Background children have control/status information and delivery tracking.
  Finished child results are recorded before final batch aggregation to reduce
  loss if an owner exits.
- Child execution uses heartbeats and stale-activity detection. A configured
  `delegation.child_timeout_seconds` is optional; the default has no wall-clock
  timeout. Cancellation is cooperative, with cleanup deferred when a worker has
  not safely unwound.

### Isolation and operational considerations

- Each child has a separate conversation, but **the working directory is shared
  by default**. Parallel edits to the same checkout can conflict or overwrite
  one another.
- `delegation.worktree_isolation: true` opts into per-child Git worktrees. The
  inspected implementation supports this only for the local terminal backend;
  setup errors can degrade to shared-workspace execution, so verify the actual
  worktree paths before relying on isolation.
- Dangerous-command approval in subagent worker threads defaults to deny;
  `delegation.subagent_auto_approve: true` explicitly opts into auto-approval.
- Finite one-shot runs have a separate child budget (`oneshot_max_children`,
  default 2; zero means unlimited).
- Background execution requires a session/runtime that can receive a later
  completion. Unsupported modes execute synchronously instead.
- The repository contains many focused delegation tests (including timeout,
  interruption, result handling, and credential routing), but this review did
  not execute them.

## Configuration discrepancy found

The runtime constant in `tools/delegate_tool_config.py` sets the default
`max_concurrent_children` to **10**. However, the user guide’s configuration
example sets it to **3**, and accompanying prose calls 3 the default. Copying
that example makes 3 the effective value. Configure this explicitly rather than
depending on the documented or implicit default.

## Suggested Hermes POC steps

1. **Install Hermes in a disposable environment** using the project’s current
   official instructions: [Hermes Agent README](https://github.com/NousResearch/hermes-agent/blob/main/README.md)
   and [installation guide](https://hermes-agent.nousresearch.com/docs/getting-started/installation).
   Review the install script and its permissions before running it; do not use
   production credentials or a valuable working copy for the first test.
2. **Configure a provider/model** and confirm an ordinary interactive
   `hermes` conversation works before testing delegation.
3. **Enable the delegation toolset** with `hermes tools` if it is not exposed in
   the current platform/tool configuration. Set
   `delegation.max_concurrent_children` explicitly (for example, 2 or 3) in the
   Hermes configuration for a controlled experiment.
4. **Try a non-mutating parallel task** with two independent research or
   inspection goals. Check that Hermes returns a dispatch handle, later posts
   the completed results, and identifies failures rather than silently
   discarding them.
5. **Try child control** by listing active children, steering one with a
   specific correction, and stopping another. Confirm the UI/runtime behavior
   matches the intended control and delivery path.
6. **Test bounded coding work** in a throwaway Git repository. Avoid assigning
   simultaneous edits to the same files. If the local backend is in use, enable
   `delegation.worktree_isolation: true` and verify each child’s worktree and
   how its changes are intended to be reviewed or integrated.
7. **Exercise failure/fallback behavior**: use a runtime without detached
   completion support (expect synchronous fallback), interrupt a child, and
   confirm the result clearly reports its terminal state.
8. **Review cost and security controls** before increasing concurrency or
   enabling nested orchestration/auto-approval. Each child uses model calls of
   its own; nested fan-out can multiply API usage quickly.
9. **For a source-level verification**, check the current upstream tests under
   `tests/tools/test_delegate*.py` and `tests/tools/test_subagent_*.py`, then run
   the relevant tests in the exact Hermes revision and environment selected for
   the POC.

## Key upstream references

- [Child construction and setup](https://github.com/NousResearch/hermes-agent/blob/66888832345561efe66d334ff4d65fe35203018b/tools/delegate_tool.py#L199-L338)
- [Delegate request validation and child construction](https://github.com/NousResearch/hermes-agent/blob/66888832345561efe66d334ff4d65fe35203018b/tools/delegate_tool.py#L488-L597)
- [Parallel child execution and result aggregation](https://github.com/NousResearch/hermes-agent/blob/66888832345561efe66d334ff4d65fe35203018b/tools/delegate_tool_dispatch.py#L111-L233)
- [Background dispatch and fallback](https://github.com/NousResearch/hermes-agent/blob/66888832345561efe66d334ff4d65fe35203018b/tools/delegate_tool_dispatch.py#L348-L478)
- [Delegation configuration defaults](https://github.com/NousResearch/hermes-agent/blob/66888832345561efe66d334ff4d65fe35203018b/tools/delegate_tool_config.py#L17-L22)
- [Subagent lifecycle API](https://github.com/NousResearch/hermes-agent/blob/66888832345561efe66d334ff4d65fe35203018b/agent/subagent_lifecycle.py)
- [User guide: delegation](https://github.com/NousResearch/hermes-agent/blob/66888832345561efe66d334ff4d65fe35203018b/website/docs/user-guide/features/delegation.md)
- [Developer guide: lifecycle API](https://github.com/NousResearch/hermes-agent/blob/66888832345561efe66d334ff4d65fe35203018b/website/docs/developer-guide/subagent-lifecycle-api.md)
