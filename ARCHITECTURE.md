# Architecture and state ownership

## Supported applications

The supported application entry point is `src.main:main` (`indextts-gui`). Its
code is split into four layers:

- `src/core`: task state, task-set persistence, recipes, configuration, and the
  generation queue.
- `src/engines`: adapters for remote TTS services. Engine adapters do not own
  task state or write task metadata.
- `src/ui`: widgets that edit the in-memory core objects and emit user intents.
- `src/app.py`: the composition root. It owns the current task set, queue/thread
  lifetime, and cross-widget signal wiring.

`src/indextts_batch_gui` is the retained v1 compatibility application. It uses
a different data model and storage schema. It is tested because existing v1
task sets may still need it, but it is not the `indextts-gui` entry point.

Never open the same task-set directory with both applications. Each storage
implementation writes an explicit format marker and rejects the other format
before creating or changing files.

## v2 task lifecycle

All v2 task transitions go through `Task.transition_to`; UI code must not assign
`Task.status` directly.

```text
PENDING -> QUEUED -> GENERATING -> COMPLETED
                     |
                     +-----------> FAILED

FAILED -----> QUEUED       (retry)
COMPLETED --> QUEUED       (regenerate while retaining the old output)
COMPLETED --> FAILED       (output or durable-result validation failed)
QUEUED -----> PENDING      (cancel a task without an existing output)
QUEUED -----> COMPLETED    (cancel regeneration and retain the old output)
```

`QUEUED` and `GENERATING` are process-local states. On restart they are restored
to `PENDING`, or to `COMPLETED` when a valid previous output is still available.
The queue captures one immutable request snapshot when a task starts. The same
snapshot is used for validation, the engine request, and `generation_config`.

## Persistence rules

- JSON and generated audio are written to unique temporary files in the target
  directory, flushed, and atomically replaced.
- A task-set save writes atomic task snapshots first and commits their order in
  the task-set metadata last. Configuration and recipe mutators roll back their
  in-memory candidate when persistence fails.
- Corrupt committed task data is reported instead of being silently discarded
  and pruned by the next save.
- Managed output paths are constrained to the task set's `outputs` directory.
  Paths stored in task JSON are relative when possible so a complete task-set
  directory can be moved.

## Threading and UI ownership

The Qt main thread is the only owner of widgets. Connection probes and engine
generation run in worker threads and communicate through signals. A task queue
belongs to exactly one `TaskSet`; switching task sets disconnects and retires
the old queue without allowing late signals to refresh the new task set.

Task-set switching follows a prepare/commit order:

1. Flush pending edits for the old task set.
2. Load and validate the requested task set without changing global/UI state.
3. Stop or retire the old queue.
4. Publish the new task set to the window and widgets.
5. Persist the recent/current path only after the switch succeeds.

On shutdown, pending debounced edits are flushed and all owned worker threads
are asked to stop and joined before Qt destroys their objects.
