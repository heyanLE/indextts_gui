## Why

IndexTTS WebUI currently processes one synthesis request at a time through manual interaction, which makes large-volume voice generation slow and error-prone. A desktop batch GUI is needed to define, persist, and execute many generation jobs with per-task configuration and reliable task-set recovery.

## What Changes

- Add a Python desktop executable GUI for batch IndexTTS generation with configurable WebUI host and port.
- Introduce task-level job definitions that include text, reference audio, and synthesis config.
- Add task-set level defaults (default reference audio and default config) that auto-apply when creating new tasks.
- Support creating multiple tasks from long text by splitting on lines.
- Add batch execution with per-task progress, status, and post-completion audio playback.
- Introduce task set concept mapped to a directory; selecting a task set auto-loads saved tasks into the UI.
- Persist each task as a single JSON source of truth and maintain generated audio output files next to task data.
- On regenerate after task config edits, delete prior audio output, generate a new audio file, and update task JSON metadata.

## Capabilities

### New Capabilities
- `indextts-batch-gui`: Desktop batch task management and execution workflow for IndexTTS WebUI, including task sets, per-task persistence, and regeneration behavior.

### Modified Capabilities
- None.

## Impact

- Affected systems: new Python GUI app, HTTP client integration with existing IndexTTS WebUI endpoint, local filesystem persistence.
- Data impact: introduce task-set directory structure with task JSON files and generated audio files.
- Runtime impact: batch orchestration, progress tracking, and retry/error handling for multiple synthesis jobs.
- Packaging impact: generate Windows executable distribution for the GUI application.
