#!/usr/bin/env python3
import argparse
import logging
import os
import signal
import sys
import threading
import time
from typing import Any, Optional

import requests
from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.server import StartTcpServer

LOG = logging.getLogger("ha_em420")


# ----------------------------
# Home Assistant entity mapping
# ----------------------------

ENTITY_TOTAL_POWER_W = "sensor.power_hem"
ENTITY_TOTAL_PF = "sensor.tibber_effektfaktor"
ENTITY_TOTAL_IMPORT_KWH = "sensor.last_meter_consumption_hem"

ENTITY_L1_V = "sensor.tibber_spanning_fas1"
ENTITY_L2_V = "sensor.tibber_spanning_fas2"
ENTITY_L3_V = "sensor.tibber_spanning_fas3"

ENTITY_L1_A = "sensor.tibber_strom_fas1"
ENTITY_L2_A = "sensor.tibber_strom_fas2"
ENTITY_L3_A = "sensor.tibber_strom_fas3"


# ----------------------------
# Modbus helpers
# ----------------------------

def idx(reg: int) -> int:
    """
    In this pymodbus setup, requested Modbus register N maps to Python index N+1.
    """
    return reg + 1


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def set_u32_block(block, reg: int, value: int) -> None:
    i = idx(reg)
    value &= 0xFFFFFFFF
    block.setValues(i, [(value >> 16) & 0xFFFF, value & 0xFFFF])


def set_i32_block(block, reg: int, value: int) -> None:
    if value < 0:
        value = (1 << 32) + value
    set_u32_block(block, reg, value)


def set_u64_block(block, reg: int, value: int) -> None:
    i = idx(reg)
    value &= 0xFFFFFFFFFFFFFFFF
    block.setValues(
        i,
        [
            (value >> 48) & 0xFFFF,
            (value >> 32) & 0xFFFF,
            (value >> 16) & 0xFFFF,
            value & 0xFFFF,
        ],
    )


def allocate_registers(size: int = 2000) -> list[int]:
    return [0] * size


# ----------------------------
# Modbus datastore block
# ----------------------------

class LoggingBlock(ModbusSequentialDataBlock):
    def __init__(self, name: str, address: int, values: list[int], log_reads: bool = False):
        super().__init__(address, values)
        self.name = name
        self.log_reads = log_reads

    def getValues(self, address, count=1):
        if self.log_reads:
            LOG.info("%s read: address=%s count=%s", self.name, address, count)
        return super().getValues(address, count)


# ----------------------------
# Home Assistant client
# ----------------------------

class HomeAssistantClient:
    def __init__(self, base_url: str, token: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        )

    def get_state(self, entity_id: str) -> dict[str, Any]:
        url = f"{self.base_url}/api/states/{entity_id}"
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def get_float(self, entity_id: str, default: Optional[float] = None) -> Optional[float]:
        try:
            data = self.get_state(entity_id)
            state = data.get("state")
            if state in (None, "", "unknown", "unavailable"):
                return default
            return float(str(state).replace(",", "."))
        except Exception as exc:
            LOG.warning("Failed reading %s: %s", entity_id, exc)
            return default


# ----------------------------
# EM420 defaults and updates
# ----------------------------

def initialize_em420_defaults(hr_block: LoggingBlock, frequency_hz: float = 50.0) -> None:
    # Total
    set_u32_block(hr_block, 0, 0)                         # total active power import, 0.1 W
    set_i32_block(hr_block, 24, 1000)                    # total PF, 0.001
    set_u32_block(hr_block, 26, int(round(frequency_hz * 1000)))  # frequency, 0.001 Hz

    # L1
    set_u32_block(hr_block, 40, 0)                       # active power import, 0.1 W
    set_u32_block(hr_block, 60, 0)                       # current, 0.001 A
    set_u32_block(hr_block, 62, 230000)                  # voltage, 0.001 V
    set_i32_block(hr_block, 64, 1000)                    # PF, 0.001

    # L2
    set_u32_block(hr_block, 80, 0)
    set_u32_block(hr_block, 100, 0)
    set_u32_block(hr_block, 102, 230000)
    set_i32_block(hr_block, 104, 1000)

    # L3
    set_u32_block(hr_block, 120, 0)
    set_u32_block(hr_block, 140, 0)
    set_u32_block(hr_block, 142, 230000)
    set_i32_block(hr_block, 144, 1000)

    # Energy counters, 0.1 Wh
    set_u64_block(hr_block, 512, 0)
    set_u64_block(hr_block, 592, 0)
    set_u64_block(hr_block, 672, 0)
    set_u64_block(hr_block, 752, 0)


def normalize_pf(raw_pf: float) -> float:
    """
    Tibber may expose PF as:
    - 0.98
    - 98
    - 980
    """
    if raw_pf > 10:
        pf = raw_pf / 1000.0
    elif raw_pf > 1.5:
        pf = raw_pf / 100.0
    else:
        pf = raw_pf
    return clamp(pf, -1.0, 1.0)


def distribute_total_energy_wh(
    total_wh: float,
    l1_w: float,
    l2_w: float,
    l3_w: float,
) -> tuple[float, float, float]:
    phase_sum = max(l1_w + l2_w + l3_w, 0.001)
    return (
        total_wh * l1_w / phase_sum,
        total_wh * l2_w / phase_sum,
        total_wh * l3_w / phase_sum,
    )


def update_em420_registers_from_ha(
    hr_block: LoggingBlock,
    ha: HomeAssistantClient,
    frequency_hz: float = 50.0,
    use_phase_sum_for_total_power: bool = False,
) -> None:
    total_power_w = ha.get_float(ENTITY_TOTAL_POWER_W, 0.0) or 0.0
    total_pf_raw = ha.get_float(ENTITY_TOTAL_PF, 1.0) or 1.0
    total_import_kwh = ha.get_float(ENTITY_TOTAL_IMPORT_KWH, 0.0) or 0.0

    l1_v = ha.get_float(ENTITY_L1_V, 230.0) or 230.0
    l2_v = ha.get_float(ENTITY_L2_V, 230.0) or 230.0
    l3_v = ha.get_float(ENTITY_L3_V, 230.0) or 230.0

    l1_a = ha.get_float(ENTITY_L1_A, 0.0) or 0.0
    l2_a = ha.get_float(ENTITY_L2_A, 0.0) or 0.0
    l3_a = ha.get_float(ENTITY_L3_A, 0.0) or 0.0

    total_pf = normalize_pf(total_pf_raw)

    # Estimate per-phase active power from V * I * PF
    l1_power_w = max(0.0, l1_v * l1_a * total_pf)
    l2_power_w = max(0.0, l2_v * l2_a * total_pf)
    l3_power_w = max(0.0, l3_v * l3_a * total_pf)

    estimated_total_from_phases = l1_power_w + l2_power_w + l3_power_w

    if use_phase_sum_for_total_power:
        total_power_w = estimated_total_from_phases
    elif total_power_w > 0.0 and estimated_total_from_phases > 1.0:
        # Scale the phase powers to match the HA total if it is available.
        scale = total_power_w / estimated_total_from_phases
        l1_power_w *= scale
        l2_power_w *= scale
        l3_power_w *= scale

    def phase_pf(p_w: float, v: float, a: float, fallback: float) -> float:
        apparent = v * a
        if apparent <= 0.1:
            return fallback
        return clamp(p_w / apparent, -1.0, 1.0)

    l1_pf = phase_pf(l1_power_w, l1_v, l1_a, total_pf)
    l2_pf = phase_pf(l2_power_w, l2_v, l2_a, total_pf)
    l3_pf = phase_pf(l3_power_w, l3_v, l3_a, total_pf)

    total_import_wh = max(0.0, total_import_kwh * 1000.0)
    l1_import_wh, l2_import_wh, l3_import_wh = distribute_total_energy_wh(
        total_import_wh,
        max(l1_power_w, 0.001),
        max(l2_power_w, 0.001),
        max(l3_power_w, 0.001),
    )

    # EM420 register scaling:
    # power: 0.1 W
    # current: 0.001 A
    # voltage: 0.001 V
    # PF: 0.001
    # energy: 0.1 Wh

    # Total
    set_u32_block(hr_block, 0, int(round(max(0.0, total_power_w) * 10)))
    set_i32_block(hr_block, 24, int(round(total_pf * 1000)))
    set_u32_block(hr_block, 26, int(round(frequency_hz * 1000)))

    # L1
    set_u32_block(hr_block, 40, int(round(l1_power_w * 10)))
    set_u32_block(hr_block, 60, int(round(l1_a * 1000)))
    set_u32_block(hr_block, 62, int(round(l1_v * 1000)))
    set_i32_block(hr_block, 64, int(round(l1_pf * 1000)))

    # L2
    set_u32_block(hr_block, 80, int(round(l2_power_w * 10)))
    set_u32_block(hr_block, 100, int(round(l2_a * 1000)))
    set_u32_block(hr_block, 102, int(round(l2_v * 1000)))
    set_i32_block(hr_block, 104, int(round(l2_pf * 1000)))

    # L3
    set_u32_block(hr_block, 120, int(round(l3_power_w * 10)))
    set_u32_block(hr_block, 140, int(round(l3_a * 1000)))
    set_u32_block(hr_block, 142, int(round(l3_v * 1000)))
    set_i32_block(hr_block, 144, int(round(l3_pf * 1000)))

    # Energy
    set_u64_block(hr_block, 512, int(round(total_import_wh * 10)))
    set_u64_block(hr_block, 592, int(round(l1_import_wh * 10)))
    set_u64_block(hr_block, 672, int(round(l2_import_wh * 10)))
    set_u64_block(hr_block, 752, int(round(l3_import_wh * 10)))

    LOG.info(
        "Updated from HA: total=%.1fW pf=%.3f "
        "L1=%.1fW %.3fA %.1fV "
        "L2=%.1fW %.3fA %.1fV "
        "L3=%.1fW %.3fA %.1fV "
        "total_import=%.1fWh",
        total_power_w,
        total_pf,
        l1_power_w,
        l1_a,
        l1_v,
        l2_power_w,
        l2_a,
        l2_v,
        l3_power_w,
        l3_a,
        l3_v,
        total_import_wh,
    )


def updater_loop(
    hr_block: LoggingBlock,
    ha: HomeAssistantClient,
    interval_s: float,
    frequency_hz: float,
    use_phase_sum_for_total_power: bool,
) -> None:
    while True:
        try:
            update_em420_registers_from_ha(
                hr_block=hr_block,
                ha=ha,
                frequency_hz=frequency_hz,
                use_phase_sum_for_total_power=use_phase_sum_for_total_power,
            )
        except Exception as exc:
            LOG.exception("Updater failed: %s", exc)
        time.sleep(interval_s)


# ----------------------------
# Main
# ----------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Home Assistant -> fake TQ EM420 Modbus TCP bridge for KEBA P30"
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=5020, help="Modbus TCP port")
    parser.add_argument(
        "--ha-url",
        default=os.getenv("HA_URL", "http://homeassistant.local:8123"),
        help="Home Assistant base URL",
    )
    parser.add_argument(
        "--ha-token",
        default=os.getenv("HA_TOKEN"),
        help="Home Assistant long-lived access token",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between Home Assistant polls",
    )
    parser.add_argument(
        "--grid-frequency",
        type=float,
        default=50.0,
        help="Grid frequency in Hz",
    )
    parser.add_argument(
        "--use-phase-sum-for-total-power",
        action="store_true",
        help="Ignore sensor.power_hem and derive total power from phase V*I*PF",
    )
    parser.add_argument("--log-reads", action="store_true", help="Log Modbus reads")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.ha_token:
        print("Missing Home Assistant token. Set --ha-token or HA_TOKEN.", file=sys.stderr)
        sys.exit(1)

    ha = HomeAssistantClient(base_url=args.ha_url, token=args.ha_token)

    hr_values = allocate_registers(2000)
    hr_block = LoggingBlock("HR", 0, hr_values, log_reads=args.log_reads)

    initialize_em420_defaults(hr_block, frequency_hz=args.grid_frequency)

    updater = threading.Thread(
        target=updater_loop,
        args=(
            hr_block,
            ha,
            args.poll_interval,
            args.grid_frequency,
            args.use_phase_sum_for_total_power,
        ),
        daemon=True,
    )
    updater.start()

    store = ModbusDeviceContext(hr=hr_block)
    context = ModbusServerContext(devices={1: store}, single=False)

    def _handle_signal(signum, frame):
        LOG.info("Received signal %s, shutting down", signum)
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    LOG.info("Starting fake EM420 on %s:%s using HA at %s", args.host, args.port, args.ha_url)
    StartTcpServer(context=context, address=(args.host, args.port))


if __name__ == "__main__":
    main()
