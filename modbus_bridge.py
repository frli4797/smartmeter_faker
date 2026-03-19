#!/usr/bin/env python3
import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

APP_VERSION = os.getenv("APP_VERSION", "dev")
if "--version" in sys.argv:
    print(f"{Path(sys.argv[0]).name} {APP_VERSION}")
    raise SystemExit(0)

import requests
from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.server import StartTcpServer

try:
    import yaml
except ImportError as exc:
    print("Missing dependency: PyYAML. Install it with 'pip install pyyaml'.", file=sys.stderr)
    raise SystemExit(1) from exc

LOG = logging.getLogger("ha_em420")


DEFAULT_CONFIG_PATH = Path("homeassistant.yaml")
DEFAULT_HEALTH_PATH = Path(os.getenv("HEALTH_STATUS_PATH", "/tmp/smartmeter-faker-health.json"))


class HealthState:
    def __init__(self, path: Path, version: str):
        self.path = path
        self.version = version
        self.lock = threading.Lock()
        self.last_success_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self.status = "starting"

    def mark_starting(self) -> None:
        with self.lock:
            self.status = "starting"
            self.last_error = None
            self._write_locked()

    def mark_success(self) -> None:
        with self.lock:
            self.status = "healthy"
            self.last_success_at = time.time()
            self.last_error = None
            self._write_locked()

    def mark_error(self, error: str) -> None:
        with self.lock:
            self.status = "error"
            self.last_error = error
            self._write_locked()

    def _write_locked(self) -> None:
        payload = {
            "status": self.status,
            "version": self.version,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "updated_at": time.time(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload), encoding="utf-8")


@dataclass(frozen=True)
class HomeAssistantEntities:
    total_power_w: str
    total_pf: str
    total_import_kwh: str
    l1_v: str
    l2_v: str
    l3_v: str
    l1_a: str
    l2_a: str
    l3_a: str


@dataclass(frozen=True)
class HomeAssistantConfig:
    url: str
    token: str
    entities: HomeAssistantEntities


def load_homeassistant_config(path: Path) -> HomeAssistantConfig:
    try:
        with path.open("r", encoding="utf-8") as config_file:
            raw_config = yaml.safe_load(config_file) or {}
    except FileNotFoundError as exc:
        raise ValueError(f"Config file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in config file {path}: {exc}") from exc

    homeassistant = raw_config.get("homeassistant")
    if not isinstance(homeassistant, dict):
        raise ValueError(f"Config file {path} must contain a 'homeassistant' mapping")

    entities = homeassistant.get("entities")
    if not isinstance(entities, dict):
        raise ValueError(
            f"Config file {path} must contain a 'homeassistant.entities' mapping"
        )

    required_entity_keys = (
        "total_power_w",
        "total_pf",
        "total_import_kwh",
        "l1_v",
        "l2_v",
        "l3_v",
        "l1_a",
        "l2_a",
        "l3_a",
    )
    missing_entity_keys = [
        key for key in required_entity_keys if not isinstance(entities.get(key), str) or not entities[key]
    ]
    if missing_entity_keys:
        missing = ", ".join(missing_entity_keys)
        raise ValueError(f"Missing or invalid Home Assistant entity ids in {path}: {missing}")

    url = homeassistant.get("url")
    token = homeassistant.get("token")
    if not isinstance(url, str) or not url:
        raise ValueError(f"Missing or invalid homeassistant.url in {path}")
    if not isinstance(token, str) or not token:
        raise ValueError(f"Missing or invalid homeassistant.token in {path}")

    return HomeAssistantConfig(
        url=url,
        token=token,
        entities=HomeAssistantEntities(**{key: entities[key] for key in required_entity_keys}),
    )


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
    entities: HomeAssistantEntities,
    frequency_hz: float = 50.0,
    use_phase_sum_for_total_power: bool = False,
) -> None:
    total_power_w = ha.get_float(entities.total_power_w, 0.0) or 0.0
    total_pf_raw = ha.get_float(entities.total_pf, 1.0) or 1.0
    total_import_kwh = ha.get_float(entities.total_import_kwh, 0.0) or 0.0

    l1_v = ha.get_float(entities.l1_v, 230.0) or 230.0
    l2_v = ha.get_float(entities.l2_v, 230.0) or 230.0
    l3_v = ha.get_float(entities.l3_v, 230.0) or 230.0

    l1_a = ha.get_float(entities.l1_a, 0.0) or 0.0
    l2_a = ha.get_float(entities.l2_a, 0.0) or 0.0
    l3_a = ha.get_float(entities.l3_a, 0.0) or 0.0

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
    entities: HomeAssistantEntities,
    health_state: HealthState,
    interval_s: float,
    frequency_hz: float,
    use_phase_sum_for_total_power: bool,
) -> None:
    while True:
        try:
            update_em420_registers_from_ha(
                hr_block=hr_block,
                ha=ha,
                entities=entities,
                frequency_hz=frequency_hz,
                use_phase_sum_for_total_power=use_phase_sum_for_total_power,
            )
            health_state.mark_success()
        except Exception as exc:
            health_state.mark_error(str(exc))
            LOG.exception("Updater failed: %s", exc)
        time.sleep(interval_s)


# ----------------------------
# Main
# ----------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Home Assistant -> fake TQ EM420 Modbus TCP bridge for KEBA P30"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {APP_VERSION}",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=5020, help="Modbus TCP port")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to YAML config file with Home Assistant settings and entity IDs",
    )
    parser.add_argument(
        "--ha-url",
        default=os.getenv("HA_URL"),
        help="Override Home Assistant base URL from config",
    )
    parser.add_argument(
        "--ha-token",
        default=os.getenv("HA_TOKEN"),
        help="Override Home Assistant long-lived access token from config",
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
    parser.add_argument(
        "--health-path",
        default=str(DEFAULT_HEALTH_PATH),
        help="Path to the JSON health status file used by container health checks",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        ha_config = load_homeassistant_config(Path(args.config))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)

    ha_url = args.ha_url or ha_config.url
    ha_token = args.ha_token or ha_config.token
    if not ha_token:
        print(
            "Missing Home Assistant token. Set it in the YAML config or pass --ha-token / HA_TOKEN.",
            file=sys.stderr,
        )
        sys.exit(1)

    ha = HomeAssistantClient(base_url=ha_url, token=ha_token)
    health_state = HealthState(Path(args.health_path), APP_VERSION)
    health_state.mark_starting()

    hr_values = allocate_registers(2000)
    hr_block = LoggingBlock("HR", 0, hr_values, log_reads=args.log_reads)

    initialize_em420_defaults(hr_block, frequency_hz=args.grid_frequency)

    updater = threading.Thread(
        target=updater_loop,
        args=(
            hr_block,
            ha,
            ha_config.entities,
            health_state,
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

    LOG.info(
        "Starting fake EM420 version=%s on %s:%s using HA at %s",
        APP_VERSION,
        args.host,
        args.port,
        ha_url,
    )
    StartTcpServer(context=context, address=(args.host, args.port))


if __name__ == "__main__":
    main()
