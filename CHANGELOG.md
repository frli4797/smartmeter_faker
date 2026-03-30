# Changelog

All notable changes to this project will be documented in this file.

The sections below are seeded from the existing release history. Future entries are added automatically when a GitHub release is published.

<!-- changelog:release-entries -->

## [v1.0.7] - 2026-03-30

- Added a bridge-themed icon for the Home Assistant add-on.
- Bumped the add-on version to `1.0.7`.

## [v1.0.6] - 2026-03-30

- Simplified add-on version handling by keeping the version in `config.yaml`.
- Removed the Docker workflow step that rewrote the add-on manifest version during builds.

## [v1.0.5] - 2026-03-30

- Fixed add-on version resolution for release builds.

## [v1.0.4] - 2026-03-30

- Consolidated Docker image publishing metadata and release build handling.

## [v1.0.3] - 2026-03-30

- Added a Home Assistant button for adding the repository from the README.
- Fixed stale README links after the repository rename to `smartmeter_bridge`.

## [v1.0.2] - 2026-03-30

- Added release note templates and changelog configuration to improve release management.
- Refined changelog categories to better match the repository's change areas.

## [v1.0.1] - 2026-03-30

- Updated the Docker build workflow to derive the add-on version from release tags.
- Adjusted metadata extraction logic used during release builds.

## [v1.0.0] - 2026-03-30

- Released the first stable version of Smartmeter Bridge as a Home Assistant add-on.
- Added full add-on packaging, including repository metadata, add-on config, docs, startup script, and health checks.
- Preserved standalone Docker and Compose usage with updated packaging and documentation.
- Improved runtime resilience with structured logging, startup validation, health reporting, and polling backoff.
- Updated CI/CD to build and validate the add-on from its new repository layout.

## [v0.0.1] - 2026-03-21

- Added pragmatic linting and general warning cleanup.
- Improved Docker image references and Compose-related setup.
- Enhanced Modbus logging and error handling in the runtime.

## [v0.0.1-alpha.2] - 2026-03-19

- Hardened runtime polling and logging.
- Expanded Docker publishing to both GHCR and Docker Hub on `main` branch pushes.

## [v0.0.1-alpha.1] - 2026-03-19

- Improved Home Assistant integration with better error handling, token validation, and configuration source tracking.
