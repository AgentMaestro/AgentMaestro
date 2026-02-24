# Contributing to AgentMaestro

Thank you for your interest in contributing to AgentMaestro.

AgentMaestro is designed as a deterministic, state-machine-driven
orchestration engine for multi-agent systems. Contributions should align
with the project's core architectural principles.

------------------------------------------------------------------------

## Development Philosophy

-   Deterministic orchestration over implicit control flow
-   Explicit state transitions
-   Clear separation of probabilistic reasoning and execution control
-   Multi-tenant safety
-   Auditable, replayable run history

Before contributing, please read `ARCHITECTURE.md`.

------------------------------------------------------------------------

## Getting Started

1.  Fork the repository.

2.  Create a feature branch:

        git checkout -b feature/your-feature-name

3.  Make changes with clear commits.

4.  Submit a Pull Request.

------------------------------------------------------------------------

## Code Guidelines

-   Follow PEP8 and standard Django conventions.
-   Keep orchestration logic deterministic.
-   Avoid hidden side effects in run transitions.
-   Keep tool execution isolated from orchestration logic.
-   Add tests for orchestration and state transitions where possible.

------------------------------------------------------------------------

## Pull Request Requirements

-   Clear description of what changed and why.
-   Reference related issues.
-   Keep PRs focused and minimal in scope.
-   No breaking architectural principles without discussion.

------------------------------------------------------------------------

## Issue Labels

We use the following labels:

-   bug
-   enhancement
-   design
-   discussion
-   good first issue
-   help wanted

------------------------------------------------------------------------

## Code of Conduct

Be respectful and constructive. We aim to build a serious infrastructure
project with thoughtful collaboration.

