## Project Summary

Build a tiny Python CLI app called todo that lets a user add, list, and complete tasks from the command line. The project must include unit tests and run entirely locally. The outcome is a working CLI with a minimal persistent storage file and passing pytest.

## Goals and Non-Goals

Goals

Provide CLI commands: add, list, done

Persist tasks to a local file

Include pytest unit tests

Provide clear help text

Non-Goals

No web UI

No database

No multi-user or sync

## Users and Use Cases

_Section locked content pending._

## Functional Requirements

todo add "text" adds a new task with an auto-incremented ID and status open.

todo list prints all tasks with ID, status, and text.

todo done <id> marks a task complete.

Tasks persist between runs in a local file under the project directory.

Return code 0 on success; non-zero on invalid input.

## Non-Functional Requirements

_Section locked content pending._

## Interfaces

CLI entrypoint: python -m todo ... (or console script todo ... if easy)

Commands:

add <text>

list

done <id>

--help shows usage.

## Data Model / Storage

_Section locked content pending._

## Architecture & Components

_Section locked content pending._

## Operational Requirements

_Section locked content pending._

## Acceptance Criteria / Definition of Done

Running python -m pytest -q passes.

python -m todo add "buy milk" then python -m todo list shows the task.

python -m todo done 1 marks it complete and list reflects it.

Code formatted/linted as per repo defaults (ruff if configured).

All work committed to a feature branch.

## Out of Scope / Future Work

_Section locked content pending._

## Risks & Assumptions

Assumption: Windows file permissions allow writing under repo workspace.

Risk: Path handling must stay inside repo/workspace.
