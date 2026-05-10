## 1. Project Setup And Foundations

- [x] 1.1 Create Python GUI project structure for app, domain models, services, and adapters
- [x] 1.2 Add runtime dependencies for GUI, HTTP requests, and audio playback
- [x] 1.3 Define application configuration model including configurable WebUI host and port

## 2. Task Set Storage Layer

- [x] 2.1 Implement task-set directory bootstrap (tasks, outputs, refs, defaults metadata)
- [x] 2.2 Implement task JSON read/write repository with atomic write behavior
- [x] 2.3 Implement defaults persistence and load path for default reference audio and config
- [x] 2.4 Implement audio filename sanitizer based on task text with collision-safe suffixing

## 3. Task Authoring Workflows

- [x] 3.1 Build UI flow to create, edit, and delete individual tasks with per-task text/reference/config
- [x] 3.2 Apply task-set defaults automatically when creating a new task
- [x] 3.3 Implement multiline text input that expands into one task per non-empty line
- [x] 3.4 Implement task-set selector that auto-loads all task JSON files into batch task list

## 4. Batch Execution Engine

- [x] 4.1 Implement IndexTTS WebUI client adapter using configured host and port
- [x] 4.2 Implement batch scheduler with queue states (pending, queued, generating, done, failed)
- [x] 4.3 Implement per-task progress update model and bind to task-row progress bars
- [x] 4.4 Implement per-task error capture and retry/re-run entry points

## 5. Regeneration And Artifact Replacement

- [x] 5.1 Detect task config changes and mark affected tasks as needing regeneration
- [x] 5.2 On regenerate, delete prior task audio output before requesting new synthesis
- [x] 5.3 Persist regenerated output metadata (new audio path/status/config snapshot) to task JSON

## 6. Playback And UX Completion

- [x] 6.1 Enable play action per task row only after successful audio generation
- [x] 6.2 Implement audio playback service for generated local audio files
- [x] 6.3 Add endpoint/config validation and user-facing error messages before batch start

## 7. Verification And Packaging

- [x] 7.1 Add tests for task JSON persistence, filename sanitization, and regeneration replacement logic
- [x] 7.2 Add integration tests or smoke checks for batch execution state transitions
- [x] 7.3 Create Windows executable packaging configuration and run packaged smoke test
