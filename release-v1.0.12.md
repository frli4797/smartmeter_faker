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
