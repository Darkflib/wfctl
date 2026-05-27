Below is an agent-ready PRD. It deliberately keeps wfctl as a systemd unit reconciler, not a scheduler or runner.

Key assumption: wfctl should render and reconcile systemd --user .service and .timer units from YAML workflow definitions. Runtime execution, scheduling, logging, resource controls, and most hardening are delegated to systemd. uv run is the preferred Python execution mode because it runs commands in the project environment and keeps the environment up to date; uv run --script is appropriate for standalone scripts with inline metadata; uvx / uv tool run is more suitable for ephemeral third-party CLI tools than for project-local workflows.  ￼

⸻

PRD: wfctl — Declarative Workflow Controller for systemd User Units

1. Product summary

wfctl is a local-first command-line tool that syncs a directory of declarative workflow definitions into managed systemd --user units.

It does not run scheduled jobs itself. It does not implement its own scheduler, daemon, retry loop, worker pool, or log store. Instead, it acts like a small Kubernetes-style reconciler:

desired state:
  workflows/*.yaml
actual state:
  ~/.config/systemd/user/wfctl-*.service
  ~/.config/systemd/user/wfctl-*.timer
runtime authority:
  systemd --user

wfctl reads YAML workflow definitions, validates them, renders corresponding .service and optionally .timer units, writes them atomically into the user systemd unit directory, reloads the user manager, and enables, disables, starts, or stops managed timers as required.

The goal is to make personal/local workflows easy to define, inspect, constrain, and operate while letting systemd handle the boring reliability primitives: scheduling, process execution, journald logging, timers, limits, and sandboxing. systemd timer units are specifically designed for timer-based activation, and calendar expressions can be validated using systemd-analyze calendar.  ￼

⸻

2. Goals

2.1 Primary goals

Build a CLI tool named wfctl that can:

1. Read workflow definitions from a configured directory.
2. Validate workflow YAML files using a strict schema.
3. Render managed systemd --user .service units.
4. Render managed .timer units when a schedule is configured.
5. Produce a human-readable dry-run plan.
6. Apply changes safely and idempotently.
7. Enable and start timers for enabled scheduled workflows.
8. Disable managed timers for disabled workflows.
9. Support manual workflows with services but no timers.
10. Provide thin convenience wrappers around systemctl --user and journalctl.
11. Avoid touching any systemd units not explicitly managed by wfctl.

2.2 Secondary goals

Add a clean foundation for later features:

1. Optional status summaries.
2. Optional derived run history from journald.
3. Optional Podman executor mode.
4. Optional FastAPI local dashboard.
5. Optional notification hooks.

These should not be included in v0.1 unless trivial.

⸻

3. Non-goals

wfctl v0.1 must not implement:

1. A long-running scheduler daemon.
2. A custom retry engine.
3. A custom worker pool.
4. DAG orchestration.
5. Distributed execution.
6. A web UI.
7. Multi-user RBAC.
8. Secrets management beyond environment files and explicit environment variables.
9. A custom log database.
10. Arbitrary Python function importing as the core execution model.
11. Any system-level unit management requiring root.

This is a controller, not an orchestrator.

⸻

4. Target user

Primary user: a technical Linux user who wants to keep local/personal automation workflows in Git, sync them to systemd --user, and operate them with a nicer UX than hand-written unit files.

Assumed environment:

- Linux with systemd
- systemd user manager available
- Python 3.12+
- uv installed
- workflows stored in a local project directory

⸻

5. Core design principle

wfctl owns desired state.

systemd owns runtime state.

Therefore:

wfctl:
  - parse YAML
  - validate config
  - render units
  - diff desired versus actual units
  - apply changes
  - call systemctl/journalctl for convenience
systemd:
  - schedule
  - start processes
  - constrain processes
  - log output
  - retry according to unit policy
  - expose status

⸻

6. CLI requirements

Use click or typer. Prefer typer if rapid development and type hints are useful; prefer click if minimising dependencies and maximising CLI stability. Either is acceptable.

The CLI command is:

wfctl

6.1 Required commands for v0.1

wfctl validate
wfctl plan
wfctl apply
wfctl list
wfctl status <workflow_id>
wfctl logs <workflow_id>
wfctl run <workflow_id>
wfctl paths

6.2 Optional commands for v0.1 if easy

wfctl disable <workflow_id>
wfctl enable <workflow_id>
wfctl prune

If implemented, enable and disable should update local desired-state files only if a config mutation mode exists. Otherwise, they should call systemctl --user enable/disable and clearly warn that the next wfctl apply may revert the change.

For v0.1, prefer keeping enable/disable as YAML-driven.

⸻

7. Configuration locations

Default workflow definition directory:

~/.config/wfctl/workflows

Default generated unit target directory:

~/.config/systemd/user

Default cache/state directory:

~/.local/state/wfctl

Default template/debug output directory:

~/.local/share/wfctl/generated

Allow overrides via:

wfctl --config-dir ./workflows ...
wfctl --unit-dir ./tmp/systemd-user ...

Environment variables:

WFCTL_CONFIG_DIR
WFCTL_UNIT_DIR
WFCTL_STATE_DIR

CLI flags should take precedence over environment variables.

⸻

8. Workflow definition schema

Workflow files are YAML files ending in:

.yaml
.yml

Each workflow definition must define exactly one workflow.

Example:

id: daily-news
description: Generate daily personalised news digest
enabled: true
exec:
  mode: uv-run
  working_directory: /home/mike/workflows
  command:
    - python
    - -m
    - workflows.daily_news
schedule:
  on_calendar: "*-*-* 07:00:00"
  persistent: true
  randomized_delay_sec: 120
environment:
  variables:
    PYTHONUNBUFFERED: "1"
  files:
    - /home/mike/.config/workflows/daily-news.env
timeout_sec: 600
restart:
  policy: on-failure
  restart_sec: 60
  start_limit_burst: 3
  start_limit_interval_sec: 900
security:
  profile: basic
  read_write_paths:
    - /home/mike/.cache/workflows/daily-news
resources:
  memory_max: 1G
  cpu_quota: "50%"
  tasks_max: 64

⸻

9. Schema details

9.1 Top-level fields

Required:

id: string
description: string
exec: object

Optional:

enabled: bool = true
schedule: object | null
environment: object | null
timeout_sec: int | null
restart: object | null
security: object | null
resources: object | null
metadata: object | null

9.2 id

Rules:

- Required.
- Lowercase slug.
- Allowed characters: a-z, 0-9, hyphen.
- Must start with a letter or number.
- Must not exceed 80 characters.

Regex:

^[a-z0-9][a-z0-9-]{0,79}$

Unit names derive from this:

wfctl-<id>.service
wfctl-<id>.timer

9.3 exec

Supported modes for v0.1:

exec:
  mode: uv-run
exec:
  mode: command

Optional experimental mode:

exec:
  mode: uv-script

9.3.1 uv-run

Recommended default for Python project workflows.

Example:

exec:
  mode: uv-run
  working_directory: /home/mike/workflows
  command:
    - python
    - -m
    - workflows.daily_news

Rendered ExecStart should be equivalent to:

uv run -- python -m workflows.daily_news

Use -- between uv options and the command to avoid ambiguity. uv’s own documentation notes that options to uv must come before the command, and -- may be used to separate the command from uv options.  ￼

Additional supported options:

exec:
  mode: uv-run
  working_directory: /home/mike/workflows
  uv_binary: /home/mike/.local/bin/uv
  python: "3.12"
  no_sync: false
  frozen: true
  command:
    - python
    - -m
    - workflows.daily_news

Mapping:

uv_binary -> executable path, default "uv"
python -> --python <value>
frozen -> --frozen
no_sync -> --no-sync
command -> args after --

Recommendation:

* Default frozen: true for reproducible scheduled automation.
* Default no_sync: false, because uv run can ensure the environment is up to date before execution.  ￼
* Allow no_sync: true for users who want faster runs and promise to sync separately.

9.3.2 uv-script

For standalone scripts using inline dependency metadata.

Example:

exec:
  mode: uv-script
  script: /home/mike/workflows/scripts/fetch_news.py
  uv_binary: /home/mike/.local/bin/uv

Rendered command:

uv run --script /home/mike/workflows/scripts/fetch_news.py

uv run --script is appropriate for standalone scripts, including scripts with inline dependency metadata.  ￼

This should be included only if simple. Otherwise, defer to v0.2.

9.3.3 command

Raw command execution.

Example:

exec:
  mode: command
  working_directory: /home/mike/workflows
  command:
    - /usr/bin/bash
    - -lc
    - ./scripts/backup.sh

Use this for non-Python workflows.

9.4 schedule

If absent, generate only a .service unit.

If present, generate both .service and .timer.

Example:

schedule:
  on_calendar: "*-*-* 07:00:00"
  persistent: true
  randomized_delay_sec: 120

Mapping:

on_calendar -> OnCalendar=
persistent -> Persistent=
randomized_delay_sec -> RandomizedDelaySec=

Validate on_calendar by running:

systemd-analyze calendar <expr>

If this command is unavailable, fall back to syntactic validation and warn.

9.5 environment

Example:

environment:
  variables:
    PYTHONUNBUFFERED: "1"
    LOG_LEVEL: INFO
  files:
    - /home/mike/.config/workflows/daily-news.env

Mapping:

variables -> Environment=
files -> EnvironmentFile=

Rules:

- Do not print environment variable values in logs.
- Validate env var names.
- Do not allow newline characters in env var names or values.
- Environment files must be absolute paths.

9.6 timeout_sec

Mapping:

TimeoutStartSec=

Default:

300

Allow null to omit timeout.

9.7 restart

Example:

restart:
  policy: on-failure
  restart_sec: 60
  start_limit_burst: 3
  start_limit_interval_sec: 900

Mapping:

policy -> Restart=
restart_sec -> RestartSec=
start_limit_burst -> StartLimitBurst=
start_limit_interval_sec -> StartLimitIntervalSec=

Supported policies:

no
on-failure
always

Default:

restart:
  policy: no

Important: systemd restart behaviour is process-level retry, not semantic application retry. wfctl must not pretend otherwise.

9.8 security

Example:

security:
  profile: basic
  read_write_paths:
    - /home/mike/.cache/workflows/daily-news

Supported profiles for v0.1:

none
basic
readonly-home
strict
networkless

none

No hardening directives.

basic

Render:

NoNewPrivileges=true
PrivateTmp=true
RestrictSUIDSGID=true

readonly-home

Render basic, plus:

ProtectHome=read-only

strict

Render readonly-home, plus:

ProtectSystem=strict
PrivateDevices=true
LockPersonality=true

networkless

Render strict, plus:

PrivateNetwork=true

read_write_paths maps to:

ReadWritePaths=

Rules:

- Paths must be absolute.
- Empty read_write_paths is allowed.
- Do not enable MemoryDenyWriteExecute by default; it can break Python/native dependencies.

9.9 resources

Example:

resources:
  memory_max: 1G
  cpu_quota: "50%"
  tasks_max: 64

Mapping:

memory_max -> MemoryMax=
cpu_quota -> CPUQuota=
tasks_max -> TasksMax=

Do minimal validation only:

- memory_max: string matching systemd-ish size format, or int bytes
- cpu_quota: string ending in %
- tasks_max: positive integer

⸻

10. Generated unit format

Every generated unit must include a managed header.

Example:

# Generated by wfctl. Do not edit directly.
# Managed-By: wfctl
# Workflow-Id: daily-news
# Source-Path: /home/mike/.config/wfctl/workflows/daily-news.yaml
# Source-SHA256: abc123...

wfctl must only modify or delete files with:

# Managed-By: wfctl

and matching expected filename prefix:

wfctl-*.service
wfctl-*.timer

⸻

11. Example generated service

Input:

id: daily-news
description: Generate daily personalised news digest
enabled: true
exec:
  mode: uv-run
  working_directory: /home/mike/workflows
  uv_binary: /home/mike/.local/bin/uv
  frozen: true
  command:
    - python
    - -m
    - workflows.daily_news
schedule:
  on_calendar: "*-*-* 07:00:00"
  persistent: true
  randomized_delay_sec: 120
environment:
  variables:
    PYTHONUNBUFFERED: "1"
  files:
    - /home/mike/.config/workflows/daily-news.env
timeout_sec: 600
restart:
  policy: on-failure
  restart_sec: 60
  start_limit_burst: 3
  start_limit_interval_sec: 900
security:
  profile: basic
  read_write_paths:
    - /home/mike/.cache/workflows/daily-news
resources:
  memory_max: 1G
  cpu_quota: "50%"
  tasks_max: 64

Generated wfctl-daily-news.service:

# Generated by wfctl. Do not edit directly.
# Managed-By: wfctl
# Workflow-Id: daily-news
# Source-Path: /home/mike/.config/wfctl/workflows/daily-news.yaml
# Source-SHA256: <sha256>
[Unit]
Description=Workflow: Generate daily personalised news digest
Documentation=file:/home/mike/.config/wfctl/workflows/daily-news.yaml
StartLimitIntervalSec=900
StartLimitBurst=3
[Service]
Type=oneshot
WorkingDirectory=/home/mike/workflows
ExecStart=/home/mike/.local/bin/uv run --frozen -- python -m workflows.daily_news
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/home/mike/.config/workflows/daily-news.env
TimeoutStartSec=600
Restart=on-failure
RestartSec=60
NoNewPrivileges=true
PrivateTmp=true
RestrictSUIDSGID=true
ReadWritePaths=/home/mike/.cache/workflows/daily-news
MemoryMax=1G
CPUQuota=50%
TasksMax=64
SyslogIdentifier=wfctl-daily-news

Generated wfctl-daily-news.timer:

# Generated by wfctl. Do not edit directly.
# Managed-By: wfctl
# Workflow-Id: daily-news
# Source-Path: /home/mike/.config/wfctl/workflows/daily-news.yaml
# Source-SHA256: <sha256>
[Unit]
Description=Timer for workflow: daily-news
[Timer]
OnCalendar=*-*-* 07:00:00
Persistent=true
RandomizedDelaySec=120
Unit=wfctl-daily-news.service
[Install]
WantedBy=timers.target

⸻

12. Apply behaviour

12.1 wfctl plan

Reads desired workflows and actual managed units, then prints actions:

CREATE   wfctl-daily-news.service
CREATE   wfctl-daily-news.timer
UPDATE   wfctl-joplin-backup.service
DELETE   wfctl-old-test.timer
UNCHANGED wfctl-invoice-scan.service

By default, plan should not include deletes unless --prune is passed.

Required flags:

wfctl plan --prune
wfctl plan --json

12.2 wfctl apply

Applies the plan.

Required behaviour:

1. Validate all definitions first.
2. Render all units in memory.
3. Write changed unit files atomically.
4. Do not partially apply if validation fails.
5. Run:

systemctl --user daemon-reload

6. For enabled scheduled workflows:

systemctl --user enable --now wfctl-<id>.timer

7. For disabled scheduled workflows:

systemctl --user disable --now wfctl-<id>.timer

8. For manual workflows, do not enable timers.
9. Do not auto-start services unless explicitly requested.

Flags:

wfctl apply --dry-run
wfctl apply --prune
wfctl apply --no-systemctl

--no-systemctl should render/write files but skip daemon reload and enable/disable operations. Useful for tests and non-systemd CI.

12.3 Atomic writes

Write to a temp file in the same directory, then rename.

Pseudo-flow:

write ~/.config/systemd/user/.wfctl-daily-news.service.tmp-<pid>
fsync file
rename to wfctl-daily-news.service

Best effort is acceptable for v0.1; do not over-engineer.

⸻

13. Status and runtime commands

13.1 wfctl list

Shows configured workflows and whether unit files exist.

Example:

ID             ENABLED  SCHEDULE             UNIT STATE   TIMER STATE
daily-news     yes      *-*-* 07:00:00       inactive     active
joplin-backup  yes      hourly               inactive     active
invoice-scan   no       manual               inactive     -

Implementation may call:

systemctl --user show ...
systemctl --user list-timers ...

13.2 wfctl status <id>

Thin wrapper:

systemctl --user status wfctl-<id>.service

If a timer exists, also show:

systemctl --user status wfctl-<id>.timer

13.3 wfctl logs <id>

Thin wrapper:

journalctl --user-unit wfctl-<id>.service

Options:

wfctl logs daily-news --tail 100
wfctl logs daily-news --follow
wfctl logs daily-news --since today

Mapping:

--tail -> -n
--follow -> -f
--since -> --since

13.4 wfctl run <id>

Starts the service manually:

systemctl --user start wfctl-<id>.service

Optional:

wfctl run daily-news --wait

For v0.1, --wait can be omitted unless easy.

⸻

14. Validation requirements

wfctl validate must check:

1. YAML parse success.
2. Required fields.
3. ID format.
4. Duplicate IDs.
5. Absolute paths where required.
6. exec.command is non-empty.
7. exec.working_directory exists, unless --skip-path-checks.
8. Environment files exist, unless --skip-path-checks.
9. schedule.on_calendar validates via systemd-analyze calendar, if available.
10. Security profile is recognised.
11. Resource values are syntactically plausible.
12. Generated unit names are valid and predictable.

Validation must fail closed. Do not apply invalid configs.

⸻

15. uv execution semantics

15.1 Recommended default

For project-local Python workflows, prefer:

exec:
  mode: uv-run
  working_directory: /path/to/project
  frozen: true
  command:
    - python
    - -m
    - package.module

Render:

uv run --frozen -- python -m package.module

Rationale:

* Avoids directly coupling generated units to .venv/bin/python.
* Lets uv manage the project environment.
* Keeps systemd units stable across venv rebuilds.
* Supports lockfile-oriented reproducibility.

15.2 Standalone scripts

For standalone scripts with inline metadata:

exec:
  mode: uv-script
  script: /path/to/script.py

Render:

uv run --script /path/to/script.py

This is useful for “FaaS-script-like” jobs, but it should be a secondary mode, not the initial default.

15.3 uv tool run / uvx

Use this for Python CLI tools distributed as packages, not for normal project-local workflows.

Example future mode:

exec:
  mode: uv-tool
  tool: ruff
  version: "0.5.0"
  args:
    - check
    - .

Render:

uvx ruff@0.5.0 check .

Do not prioritise this for v0.1. uvx/uv tool run is documented as running tools in ephemeral environments, similar to pipx.  ￼

⸻

16. Security requirements

16.1 Do not run as root

wfctl v0.1 targets user units only.

If run as root, warn unless explicitly overridden:

wfctl --allow-root apply

Even then, do not manage system units in v0.1.

16.2 Managed namespace only

wfctl must only manage units matching:

wfctl-*.service
wfctl-*.timer

and containing:

Managed-By: wfctl

16.3 No shell interpolation by default

When rendering ExecStart, do not use shell commands unless the user explicitly chooses command with shell arguments.

Good:

ExecStart=/home/mike/.local/bin/uv run --frozen -- python -m workflows.daily_news

Avoid generating:

ExecStart=/bin/sh -c "..."

unless user requested it explicitly.

16.4 Secrets

Rules:

- Prefer EnvironmentFile for secrets.
- Do not log secret values.
- Do not include secret values in generated comments.
- Warn if environment variable names look secret-like and are placed directly in YAML.

Secret-like names include:

TOKEN
SECRET
PASSWORD
API_KEY
PRIVATE_KEY
CREDENTIAL

This should be a warning, not a hard failure.

16.5 File permissions

When writing generated units:

mode: 0644

When writing internal state/cache files:

mode: 0600 where appropriate

wfctl should not create environment files in v0.1.

⸻

17. Project implementation requirements

17.1 Language and packaging

Use:

Python 3.12+
uv
pyproject.toml
ruff
pytest

Suggested dependencies:

pydantic
PyYAML or ruamel.yaml
typer or click
rich

Optional:

jinja2

Avoid Jinja2 if simple string rendering is sufficient. Generated unit files are simple enough that explicit rendering functions may be better and safer.

17.2 Suggested package layout

wfctl/
  pyproject.toml
  README.md
  src/
    wfctl/
      __init__.py
      cli.py
      config.py
      models.py
      loader.py
      render.py
      plan.py
      apply.py
      systemd.py
      validate.py
      paths.py
      errors.py
      logging.py
  tests/
    test_models.py
    test_loader.py
    test_render.py
    test_plan.py
    test_apply.py
    fixtures/
      workflows/
        daily-news.yaml
        manual-job.yaml

17.3 Code quality

Required:

- Type hints on public functions.
- No broad except without logging or re-raise.
- Clear domain exceptions.
- Unit tests for model validation and rendering.
- No network calls.
- No root-required behaviour.

⸻

18. Internal architecture

18.1 Main modules

models.py

Pydantic models:

WorkflowDefinition
ExecConfig
ScheduleConfig
EnvironmentConfig
RestartConfig
SecurityConfig
ResourcesConfig

loader.py

Responsibilities:

- Discover YAML files.
- Parse YAML.
- Attach source path.
- Compute source SHA256.
- Detect duplicate IDs.

render.py

Responsibilities:

- Convert workflow model into service text.
- Convert workflow model into timer text.
- Escape systemd values safely.
- Generate managed headers.

plan.py

Responsibilities:

- Compare desired rendered units with actual files.
- Produce create/update/delete/unchanged actions.
- Support JSON output.

apply.py

Responsibilities:

- Execute plan.
- Atomic writes.
- Safe deletion only for managed files.
- Call systemd daemon-reload and enable/disable as needed.

systemd.py

Responsibilities:

- Wrapper for systemctl --user.
- Wrapper for journalctl.
- Wrapper for systemd-analyze calendar.
- Centralised subprocess execution.

cli.py

Responsibilities:

- CLI command wiring.
- Rich output.
- Exit codes.

⸻

19. Exit codes

Use stable exit codes:

0 success
1 generic error
2 validation error
3 systemd command failed
4 unsafe operation refused
5 workflow not found

⸻

20. Acceptance criteria

20.1 Validation

Given a valid workflow YAML, wfctl validate exits 0.

Given duplicate IDs, wfctl validate exits 2.

Given an invalid ID, wfctl validate exits 2.

Given an invalid on_calendar, wfctl validate exits 2 if systemd-analyze calendar is available.

20.2 Rendering

Given a scheduled workflow, wfctl plan proposes:

CREATE wfctl-<id>.service
CREATE wfctl-<id>.timer

Given a manual workflow without schedule, wfctl plan proposes only:

CREATE wfctl-<id>.service

Rendered units include:

Managed-By: wfctl
Workflow-Id: <id>
Source-SHA256: <sha>

20.3 Applying

wfctl apply --no-systemctl writes expected units without invoking systemctl.

wfctl apply invokes:

systemctl --user daemon-reload

For enabled scheduled workflows, wfctl apply invokes:

systemctl --user enable --now wfctl-<id>.timer

For disabled scheduled workflows, wfctl apply invokes:

systemctl --user disable --now wfctl-<id>.timer

20.4 Safety

wfctl prune refuses to delete any file that does not contain:

Managed-By: wfctl

wfctl apply --prune does not delete unmanaged systemd units.

20.5 Runtime wrappers

wfctl run <id> invokes:

systemctl --user start wfctl-<id>.service

wfctl logs <id> --tail 100 invokes:

journalctl --user-unit wfctl-<id>.service -n 100

⸻

21. Test strategy

21.1 Unit tests

Test:

- YAML loading
- schema validation
- duplicate detection
- service rendering
- timer rendering
- plan diffing
- managed-file detection
- command construction

21.2 Integration-ish tests without real systemd

Use temp directories and mocked subprocess calls.

Example:

wfctl --config-dir tests/fixtures/workflows --unit-dir /tmp/units apply --no-systemctl

Assert expected files exist.

21.3 Subprocess mocking

Mock calls to:

systemctl
journalctl
systemd-analyze

Do not require live systemd in CI.

⸻

22. Example v0.1 workflows

22.1 Scheduled uv project workflow

id: daily-news
description: Generate daily personalised news digest
enabled: true
exec:
  mode: uv-run
  working_directory: /home/mike/workflows
  uv_binary: /home/mike/.local/bin/uv
  frozen: true
  command:
    - python
    - -m
    - workflows.daily_news
schedule:
  on_calendar: "*-*-* 07:00:00"
  persistent: true
  randomized_delay_sec: 120
environment:
  variables:
    PYTHONUNBUFFERED: "1"
  files:
    - /home/mike/.config/workflows/daily-news.env
timeout_sec: 600
restart:
  policy: on-failure
  restart_sec: 60
  start_limit_burst: 3
  start_limit_interval_sec: 900
security:
  profile: basic
  read_write_paths:
    - /home/mike/.cache/workflows/daily-news
resources:
  memory_max: 1G
  cpu_quota: "50%"
  tasks_max: 64

22.2 Manual shell workflow

id: joplin-backup
description: Back up Joplin export directory
enabled: true
exec:
  mode: command
  working_directory: /home/mike
  command:
    - /usr/bin/bash
    - -lc
    - /home/mike/bin/backup-joplin.sh
timeout_sec: 1800
security:
  profile: readonly-home
  read_write_paths:
    - /home/mike/backups
    - /home/mike/.cache/restic
resources:
  memory_max: 512M
  cpu_quota: "25%"

22.3 Standalone uv script workflow

id: fetch-reading-list
description: Fetch and summarise reading list
exec:
  mode: uv-script
  script: /home/mike/workflows/scripts/fetch_reading_list.py
  uv_binary: /home/mike/.local/bin/uv
schedule:
  on_calendar: "hourly"
  persistent: true
timeout_sec: 300
security:
  profile: basic

⸻

23. README requirements

The generated project should include a README with:

1. What wfctl is.
2. What wfctl is not.
3. Install instructions using uv.
4. Example workflow YAML.
5. Example commands.
6. systemd user unit caveats.
7. Security notes.
8. Troubleshooting.

Mention that user timers may require the user systemd manager to be running. For always-on headless use, the user may need lingering enabled via:

loginctl enable-linger "$USER"

Do not automatically run this command.

⸻

24. Suggested implementation phases for Codex

Phase 1: Skeleton

Build:

- pyproject.toml
- package structure
- CLI skeleton
- basic logging
- test setup

Commands may initially print placeholders.

Phase 2: Models and loader

Build:

- Pydantic schema
- YAML loader
- duplicate detection
- source SHA256
- validate command

Phase 3: Renderer

Build:

- service renderer
- timer renderer
- unit escaping
- managed headers
- golden-file tests

Phase 4: Planner

Build:

- desired unit set
- actual managed unit discovery
- create/update/delete/unchanged diff
- text and JSON output

Phase 5: Apply

Build:

- atomic writes
- safe delete
- daemon-reload
- timer enable/disable
- --no-systemctl mode
- subprocess wrapper

Phase 6: Runtime wrappers

Build:

- list
- status
- logs
- run
- paths

Phase 7: Polish

Build:

- README
- examples
- error messages
- ruff config
- pytest coverage

⸻

25. Codex build prompt

You can give Codex this as the task:

Build a Python 3.12+ CLI project named wfctl.
wfctl is a local-first controller that reconciles YAML workflow definitions into managed systemd --user .service and .timer units. It must not implement its own scheduler or runner. systemd owns runtime execution, scheduling, logging, restart behaviour, and process constraints.
Implement the v0.1 scope from the PRD:
- CLI: validate, plan, apply, list, status, logs, run, paths.
- Read YAML workflow definitions from ~/.config/wfctl/workflows by default.
- Write generated units to ~/.config/systemd/user by default.
- Support --config-dir, --unit-dir, and --state-dir overrides.
- Validate workflow definitions with Pydantic.
- Render wfctl-<id>.service for every workflow.
- Render wfctl-<id>.timer only when schedule is present.
- Use Managed-By: wfctl headers.
- Only modify/delete managed wfctl-* units.
- Implement dry-run planning.
- Implement apply with atomic writes.
- Implement --no-systemctl for tests.
- Implement uv-run and command exec modes.
- Implement uv-script mode if straightforward.
- Use uv-run as the recommended Python project execution mode.
- Prefer ExecStart using argv-style command rendering, not shell interpolation.
- Include tests with mocked subprocess calls.
- Include README and example workflow YAML files.
Use Python 3.12+, uv, ruff, pytest, and either typer or click. Keep the implementation boring, explicit, and secure by default.

⸻

26. Opinionated implementation notes

I would strongly bias the first build towards:

- Typer + Rich for CLI UX
- Pydantic v2 for schema
- PyYAML for parsing
- explicit string renderers instead of Jinja2
- subprocess.run wrapper with logged command metadata
- tests that never require actual systemd

For uv, I would make uv-run the default recommended mode and render:

uv run --frozen -- python -m module

not:

/path/to/.venv/bin/python -m module

That keeps the unit robust against venv churn and aligns with how uv run is intended to execute commands inside the project environment.  ￼

