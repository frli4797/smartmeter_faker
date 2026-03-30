#!/usr/bin/with-contenv bashio

set -euo pipefail

export HA_URL
HA_URL="${HA_URL:-http://supervisor/core}"

export HA_TOKEN
HA_TOKEN="${HA_TOKEN:-${SUPERVISOR_TOKEN:-}}"

export HA_ENTITY_TOTAL_POWER_W
HA_ENTITY_TOTAL_POWER_W="$(bashio::config 'total_power_w')"
export HA_ENTITY_TOTAL_PF
HA_ENTITY_TOTAL_PF="$(bashio::config 'total_pf')"
export HA_ENTITY_TOTAL_IMPORT_KWH
HA_ENTITY_TOTAL_IMPORT_KWH="$(bashio::config 'total_import_kwh')"
export HA_ENTITY_L1_V
HA_ENTITY_L1_V="$(bashio::config 'l1_v')"
export HA_ENTITY_L2_V
HA_ENTITY_L2_V="$(bashio::config 'l2_v')"
export HA_ENTITY_L3_V
HA_ENTITY_L3_V="$(bashio::config 'l3_v')"
export HA_ENTITY_L1_A
HA_ENTITY_L1_A="$(bashio::config 'l1_a')"
export HA_ENTITY_L2_A
HA_ENTITY_L2_A="$(bashio::config 'l2_a')"
export HA_ENTITY_L3_A
HA_ENTITY_L3_A="$(bashio::config 'l3_a')"

export HEALTHCHECK_MAX_AGE_SECONDS
HEALTHCHECK_MAX_AGE_SECONDS="$(bashio::config 'healthcheck_max_age_seconds')"

declare -a args
args=(
    "python3"
    "/app/modbus_bridge.py"
    "--host"
    "0.0.0.0"
    "--port"
    "5020"
    "--poll-interval"
    "$(bashio::config 'poll_interval')"
    "--grid-frequency"
    "$(bashio::config 'grid_frequency')"
)

if bashio::config.true 'use_phase_sum_for_total_power'; then
    args+=("--use-phase-sum-for-total-power")
fi

if bashio::config.true 'log_reads'; then
    args+=("--log-reads")
fi

if bashio::config.true 'debug'; then
    args+=("--debug")
fi

bashio::log.info "Starting Smartmeter Faker"
bashio::log.info "Home Assistant endpoint: ${HA_URL}"
bashio::log.info "Modbus TCP endpoint: 0.0.0.0:5020"

exec "${args[@]}"
