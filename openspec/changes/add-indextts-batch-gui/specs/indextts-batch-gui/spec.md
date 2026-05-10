## ADDED Requirements

### Requirement: Configurable WebUI Endpoint
The application MUST allow users to configure the IndexTTS WebUI host and port before running generation tasks, and MUST use the configured endpoint for all synthesis requests in the active session.

#### Scenario: Update endpoint before execution
- **WHEN** a user edits host and port values and saves settings
- **THEN** subsequent task generation requests are sent to the new endpoint

#### Scenario: Endpoint validation failure
- **WHEN** a user provides an invalid endpoint format
- **THEN** the application rejects the value and provides an error message without starting generation

### Requirement: Task Definition With Per-Task Inputs
The application MUST support creating multiple generation tasks, and each task MUST store its own text, reference audio path, and synthesis configuration.

#### Scenario: Create task with explicit inputs
- **WHEN** a user creates a new task and fills text, reference audio, and config
- **THEN** the task is added to the batch list with those values bound to that task only

#### Scenario: Edit a task without affecting others
- **WHEN** a user edits one task's config or reference audio
- **THEN** other tasks in the same task set retain their existing values

### Requirement: Task-Set Defaults For New Tasks
The application MUST support task-set level default reference audio and default synthesis config, and MUST apply these defaults when creating new tasks.

#### Scenario: New task inherits defaults
- **WHEN** default reference audio and config are configured in the task-set settings and the user adds a new task
- **THEN** the new task starts with those default values prefilled

#### Scenario: Defaults changed later
- **WHEN** a user updates task-set defaults after existing tasks already exist
- **THEN** existing tasks remain unchanged unless explicitly edited

### Requirement: Line-Based Task Expansion
The application MUST support taking long text input and generating one task per non-empty line using the active defaults.

#### Scenario: Generate tasks from multiline text
- **WHEN** a user submits multiline text input with N non-empty lines
- **THEN** exactly N tasks are created in order, each with one line as task text

#### Scenario: Ignore blank lines
- **WHEN** multiline input contains empty or whitespace-only lines
- **THEN** those lines are skipped and no empty-text tasks are created

### Requirement: Batch Execution With Per-Task Progress And Playback
The application MUST provide batch start execution and display per-task progress and status, and MUST enable playback for each task after successful audio generation.

#### Scenario: Start batch generation
- **WHEN** a user clicks start batch generation
- **THEN** pending tasks transition through execution states and each task row shows its own progress indicator

#### Scenario: Play generated audio
- **WHEN** a task completes successfully and has an audio file
- **THEN** the task row exposes a play action that plays that task's generated audio

### Requirement: Task Set Directory Lifecycle
The application MUST support creating and selecting task sets where each task set maps to one directory, and selecting a task set MUST automatically load all persisted tasks into the batch task area.

#### Scenario: Create new task set
- **WHEN** a user creates a task set named X
- **THEN** the application creates a dedicated directory for X and initializes task-set metadata/defaults storage

#### Scenario: Load existing task set
- **WHEN** a user selects an existing task set directory
- **THEN** the application reads task JSON files and renders all tasks in the batch area

### Requirement: Per-Task JSON Source Of Truth And Audio Artifact Naming
Each task in a task set MUST be represented by one JSON file as its source of truth, and generated audio MUST be stored as a separate file whose basename derives from task text with special characters removed.

#### Scenario: Persist task to JSON
- **WHEN** a task is created or updated
- **THEN** the corresponding task JSON file is written with current text, config, reference audio, generation status, and generated audio path metadata

#### Scenario: Derive audio filename from text
- **WHEN** generation produces audio for a task
- **THEN** the audio filename is generated from sanitized task text and stored in task JSON metadata

### Requirement: Regeneration Replaces Old Audio And Updates Metadata
If a user modifies a task's configuration and regenerates, the application MUST delete the task's previous generated audio file (if present), generate a new audio file, and update the task JSON with the new output metadata.

#### Scenario: Regenerate after config change
- **WHEN** a completed task's config is edited and the user triggers regeneration
- **THEN** old audio output for that task is removed before new generation starts

#### Scenario: Regeneration metadata update
- **WHEN** regeneration finishes successfully
- **THEN** task JSON is updated with new audio filename/path, status, and latest task configuration
