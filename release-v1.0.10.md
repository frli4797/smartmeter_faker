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
