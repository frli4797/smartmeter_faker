# Changelog

All notable changes to this add-on will be documented in this file.

The sections below are seeded from the existing release history. Future entries are added automatically when a GitHub release is published.

<!-- changelog:release-entries -->

## [v1.0.12] - 2026-05-07

# Release Title

`v1.0.12` fix startup loop when optional power factor is unset.

## Highlights

- Fix an add-on startup loop caused by an unset optional `total_pf` value being exported as the literal entity ID `null`.
- Keep calculated power factor mode working when `total_pf` is intentionally left empty.
- Add regression coverage for Home Assistant add-on optional config normalization.

## What's New

### Features

- No new user-facing features in this release.

### Fixes and Improvements

- Treat optional config values of `null`, `none`, and empty strings as unset instead of Home Assistant entity IDs.
- Normalize `total_pf` in the add-on startup script before exporting `HA_ENTITY_TOTAL_PF`.
- Preserve validation behavior when `total_pf` is required: `null` is still rejected unless `calculate_power_factor` is enabled.

### CI/CD and Tooling

- Added regression tests for `HA_ENTITY_TOTAL_PF=null` with calculated power factor enabled and disabled.
- Added shell syntax validation for the add-on startup script during local verification.

## Upgrade Notes

- Upgrade normally to `v1.0.12`.
- Verify the add-on version shows `1.0.12` after upgrading.
- If you use `calculate_power_factor`, `total_pf` may remain unset.
- If you do not use `calculate_power_factor`, keep `total_pf` configured as before.

## Notes

- This release is a focused hotfix for the startup loop seen after `v1.0.11` when Home Assistant returns `null` for the optional `total_pf` field.

## Full Changelog

- Compare: `v1.0.11...v1.0.12`


## [v1.0.11] - 2026-05-07

# Release Title

`v1.0.11` harden Modbus serving when Home Assistant data is unavailable.

## Highlights

- Stop serving Modbus register data when required Home Assistant entities are unavailable, unknown, empty, or non-numeric.
- Resume Modbus serving automatically after Home Assistant data recovers.
- Add an option to calculate total power factor from total power plus per-phase voltage and current sensors.

## What's New

### Features

- Added `calculate_power_factor` so the bridge can derive total power factor when no dedicated Home Assistant power factor entity is available.
- Allow `total_pf` to be omitted when calculated power factor mode is enabled.

### Fixes and Improvements

- Required Home Assistant readings are now handled strictly for register updates: zero remains valid, but unavailable or invalid states stop the poll.
- Modbus reads are blocked until the bridge has completed a successful Home Assistant poll with valid required data.
- Modbus reads resume after a later successful Home Assistant poll, without requiring an add-on restart.
- Phase-derived total power behavior has been simplified around the calculated power factor flow.

### CI/CD and Tooling

- Added regression coverage for zero-valued readings, unavailable entity handling, and recovery after Home Assistant entities become available again.
- Added test coverage for the calculated power factor configuration path.

## Upgrade Notes

- Upgrade normally to `v1.0.11`.
- Verify the add-on version shows `1.0.11` after upgrading.
- If you enable `calculate_power_factor`, `total_pf` can be left empty; otherwise keep `total_pf` configured as before.
- If a required Home Assistant sensor becomes unavailable, Modbus clients will stop receiving meter data until the sensor returns to a numeric state.

## Notes

- This release focuses on data correctness and avoiding misleading fallback values on the Modbus interface.

## Full Changelog

- Compare: `v1.0.10...v1.0.11`


## [v1.0.10] - 2026-05-07

# Release Title

`v1.0.10` improve startup resilience when Home Assistant is temporarily unavailable.

## Highlights

- Keep the bridge process alive when Home Assistant is temporarily unavailable during startup.
- Add regression test coverage for startup validation failures and auth failures.
- Refresh add-on documentation and setup guidance in the README.

## What's New

### Features

- The bridge now defers transient Home Assistant startup validation failures and keeps retrying in the background instead of exiting immediately.

### Fixes and Improvements

- Authorization failures and invalid entity configuration still fail fast, so real misconfiguration remains visible.
- Added explicit startup logging for deferred Home Assistant validation failures.
- Fixed the startup test dependency stubs so the unit tests import and run correctly in GitHub Actions.
- Refined README guidance for the Home Assistant add-on and standalone Docker workflows.

### CI/CD and Tooling

- Added unit test execution to the Python GitHub Actions workflow.
- Added regression coverage for transient startup outages and auth failure behavior.

## Upgrade Notes

- Upgrade normally to `v1.0.10`.
- Verify the add-on version shows `1.0.10` after upgrading.
- If Home Assistant is temporarily unavailable during add-on startup, the bridge should now remain running and recover automatically when Home Assistant comes back.

## Notes

- This release focuses on startup resilience and test coverage rather than configuration changes.

## Full Changelog

- Compare: `v1.0.9...v1.0.10`



## [v1.0.9] - 2026-03-30

<!-- Release notes generated using configuration in .github/release.yml at v1.0.9 -->

## What's Changed
### Other Changes
* Move add-on changelog into the Home Assistant add-on folder by @frli4797 in https://github.com/frli4797/smartmeter_bridge/pull/12


**Full Changelog**: https://github.com/frli4797/smartmeter_bridge/compare/v1.0.8...v1.0.9



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
