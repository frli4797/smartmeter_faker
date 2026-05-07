# Smartmeter Bridge

This add-on exposes a fake Modbus TCP energy meter that mirrors values from Home Assistant sensors. It is meant for devices that expect a meter speaking Modbus, such as a KEBA wallbox expecting an EM420-style meter.

## What it does

The add-on polls Home Assistant sensor states and publishes them as Modbus TCP registers on port `5020`.

By default, the add-on talks to Home Assistant through the internal Supervisor proxy:

- URL: `http://supervisor/core`
- Token: `SUPERVISOR_TOKEN`

That means the add-on does not need a user-configured Home Assistant URL or long-lived token in normal use.

## Installation

### Local repository

1. Copy this repository into your Home Assistant add-ons directory, for example:
   `/addons/smartmeter_bridge_repo`
2. In Home Assistant, go to **Settings -> Add-ons -> Add-on Store**.
3. Open the menu, choose **Check for updates** or reload the local repository.
4. Open **Smartmeter Bridge** and install it.

### Git repository

Add this repository URL in Home Assistant's add-on store, then install the `Smartmeter Bridge` add-on from that repository.

## Configuration

Set the entity IDs for your Home Assistant sensors:

- `total_power_w`
- `total_import_kwh`
- `l1_v`
- `l2_v`
- `l3_v`
- `l1_a`
- `l2_a`
- `l3_a`

Optional settings:

- `poll_interval`: Seconds between polls.
- `grid_frequency`: Usually `50` or `60`.
- `use_phase_sum_for_total_power`: Derive total power from phase values instead of using the total power entity.
- `calculate_power_factor`: Calculate the total power factor from total power and per-phase voltage/current values instead of reading `total_pf` from Home Assistant.
- `log_reads`: Log Modbus reads.
- `debug`: Enable debug logging.
- `healthcheck_max_age_seconds`: Maximum age of the last successful poll before the container is considered unhealthy.

If `calculate_power_factor` is disabled, configure `total_pf` as a Home Assistant entity ID. If it is enabled, `total_pf` can be left empty.

Home Assistant add-on configuration does not support conditionally hiding options in the add-on UI, so the `total_pf` field will still be visible even when `calculate_power_factor` is enabled.

## Connecting your client

Point your Modbus TCP client or wallbox at the Home Assistant host IP and port `5020`.

## Notes

- The add-on exposes port `5020/tcp`.
- Logs are JSON-formatted from the Python service.
- Startup validation fails fast if Home Assistant cannot be reached or an entity ID is missing.
- The add-on uses `http://supervisor/core` and `SUPERVISOR_TOKEN` internally.
