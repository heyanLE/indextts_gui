## Context

Current IndexTTS WebUI interaction is optimized for single manual synthesis and does not provide native task batching, task-set persistence, or per-task lifecycle management. The new desktop GUI must orchestrate many synthesis jobs against a configurable WebUI endpoint while preserving each task as recoverable local state.

Primary constraints:
- Must run as a Windows executable for non-developer usage.
- Must keep per-task data durable on disk without requiring a database.
- Must allow users to reopen a task set directory and continue from persisted JSON state.

Stakeholders:
- Content producers generating many voice clips from scripts.
- Operators who need repeatable regeneration when parameters change.

## Goals / Non-Goals

**Goals:**
- Provide a desktop batch workflow for IndexTTS with configurable host/port.
- Persist task sets as filesystem directories containing task JSON and audio artifacts.
- Support defaults at task-set scope and line-based bulk task creation.
- Execute queued tasks with per-task status/progress and playback after success.
- Guarantee deterministic regeneration semantics (delete old audio, rewrite task JSON).

**Non-Goals:**
- Replacing IndexTTS model serving or changing WebUI synthesis behavior.
- Building collaborative multi-user or cloud-synced task management.
- Providing advanced audio editing or post-processing tooling.

## Decisions

### Decision 1: Use task JSON as single source of truth
- Choice: Store each task in one JSON file that contains input text, config, reference audio, output path, status, and error metadata.
- Rationale: Supports crash recovery, portability, and transparent debugging without database migration overhead.
- Alternative considered: SQLite database with normalized task tables.
- Why not chosen: Adds migration and packaging complexity that is unnecessary for file-oriented task sets.

### Decision 2: Task set maps 1:1 to a directory
- Choice: Represent each task set as a folder with subfolders for tasks, outputs, and references plus defaults metadata.
- Rationale: Directly matches user mental model, enables manual backup/versioning, and satisfies auto-load requirement.
- Alternative considered: Global monolithic storage with logical grouping.
- Why not chosen: Harder selective backup and less transparent portability.

### Decision 3: Queue-based execution engine with bounded concurrency
- Choice: Implement a scheduler that reads pending tasks and submits synthesis requests with configurable concurrency (default 1, expandable if endpoint supports it).
- Rationale: Preserves compatibility with single-worker WebUI while allowing future performance tuning.
- Alternative considered: Fully parallel fire-and-forget requests.
- Why not chosen: Risks endpoint overload and unstable user feedback when backend serializes internally.

### Decision 4: Progress model supports both real and synthetic progress
- Choice: Use real progress if endpoint provides progress callbacks; otherwise use state-based progress (queued/requesting/writing/done).
- Rationale: Maintains meaningful per-task progress indicators regardless of backend capabilities.
- Alternative considered: No progress until completion.
- Why not chosen: Poor user feedback for long-running tasks.

### Decision 5: Regeneration is replace-in-place semantics
- Choice: On regenerate, delete previous output file before requesting new synthesis, then update task JSON atomically.
- Rationale: Prevents stale playback links and keeps one canonical output per task.
- Alternative considered: Keep historical outputs with version suffixes.
- Why not chosen: Increases storage complexity and contradicts explicit replace behavior requirement.

### Decision 6: Sanitized text-derived filename with collision guard
- Choice: Generate output basename from text with special character removal and append short stable hash when needed for uniqueness.
- Rationale: Keeps human-readable names while preventing overwrites for similar text.
- Alternative considered: UUID-only filenames.
- Why not chosen: Hard for users to map files to content.

## Risks / Trade-offs

- [WebUI API instability] -> Mitigation: isolate API adapter layer and add endpoint capability check on startup.
- [Backend may not support true concurrency] -> Mitigation: default concurrency to 1 and expose safe upper bound setting.
- [Partial write/corrupted JSON on crash] -> Mitigation: write JSON via temp file then atomic rename.
- [Filename sanitization collisions or empty names] -> Mitigation: fallback basename plus deterministic hash suffix.
- [Executable packaging playback dependencies on Windows] -> Mitigation: validate selected audio playback backend in packaged smoke tests.

## Migration Plan

1. Introduce standalone GUI application module and local task-set schema.
2. Ship initial executable with one-way creation/loading of new task sets.
3. Validate backward compatibility by allowing older task JSON missing newer optional fields to be auto-filled on load.
4. Rollback strategy: preserve task JSON and outputs; users can revert to prior binary without data loss because storage is file-based and additive.

## Open Questions

- What exact IndexTTS WebUI endpoint contract (path, request payload, response metadata) is stable for integration?
- Does the backend expose measurable progress events, or only completion responses?
- Should task defaults be global per app as well as per task set, or only per task set?
- Do we need explicit retry policies (max retries/backoff) in v1, or keep manual rerun only?
