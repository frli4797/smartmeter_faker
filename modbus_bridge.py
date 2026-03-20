#!/usr/bin/env python3
import argparse
import hashlib
import json
import logging
import math
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

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

APP_VERSION = os.getenv("APP_VERSION", "dev")
LOG = logging.getLogger("ha_em420")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        event_fields = getattr(record, "event_fields", None)
        if isinstance(event_fields, dict):
            payload.update(event_fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True)


def log_event(level: int, message: str, **fields: Any) -> None:
    LOG.log(level, message, extra={"event_fields": fields})


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

    def mark_stopping(self) -> None:
        with self.lock:
            self.status = "stopping"
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
    source: str = "unknown"


class HomeAssistantError(Exception):
    pass


class HomeAssistantAuthError(HomeAssistantError):
    pass


class HomeAssistantConnectivityError(HomeAssistantError):
    pass


class HomeAssistantEntityError(HomeAssistantError):
    pass


REQUIRED_ENTITY_KEYS = (
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

ENTITY_ENV_VARS = {
    "total_power_w": "HA_ENTITY_TOTAL_POWER_W",
    "total_pf": "HA_ENTITY_TOTAL_PF",
    "total_import_kwh": "HA_ENTITY_TOTAL_IMPORT_KWH",
    "l1_v": "HA_ENTITY_L1_V",
    "l2_v": "HA_ENTITY_L2_V",
    "l3_v": "HA_ENTITY_L3_V",
    "l1_a": "HA_ENTITY_L1_A",
    "l2_a": "HA_ENTITY_L2_A",
    "l3_a": "HA_ENTITY_L3_A",
}


def load_homeassistant_config_from_yaml(path: Path) -> HomeAssistantConfig:
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

    missing_entity_keys = [
        key
        for key in REQUIRED_ENTITY_KEYS
        if not isinstance(entities.get(key), str) or not entities[key]
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
        entities=HomeAssistantEntities(
            **{key: entities[key] for key in REQUIRED_ENTITY_KEYS}
        ),
        source=f"yaml:{path}",
    )


def _read_secret_from_file(path_value: Optional[str]) -> Optional[str]:
    if not path_value:
        return None
    secret_path = Path(path_value)
    try:
        return secret_path.read_text(encoding="utf-8").strip() or None
    except OSError as exc:
        raise ValueError(f"Failed reading secret file {secret_path}: {exc}") from exc


def _validate_token(token: Optional[str]) -> Optional[str]:
    if token is None:
        return None
    normalized = token.strip()
    if not normalized:
        return None
    if normalized == "YOUR_LONG_LIVED_ACCESS_TOKEN":
        raise ValueError("Home Assistant token is still set to the example placeholder value")
    return normalized


def load_homeassistant_config(path: Path) -> HomeAssistantConfig:
    yaml_config: Optional[HomeAssistantConfig] = None
    if path.exists():
        yaml_config = load_homeassistant_config_from_yaml(path)
    else:
        LOG.info("No YAML config found at %s, relying on environment configuration", path)

    env_url = os.getenv("HA_URL")
    env_token_raw = os.getenv("HA_TOKEN") or _read_secret_from_file(
        os.getenv("HA_TOKEN_FILE")
    )
    env_entities = {key: os.getenv(env_var) for key, env_var in ENTITY_ENV_VARS.items()}
    used_env = bool(env_url or env_token_raw or any(env_entities.values()))

    url = env_url or (yaml_config.url if yaml_config else None)
    env_token = _validate_token(env_token_raw)
    yaml_token = _validate_token(yaml_config.token if yaml_config else None)
    token = env_token or yaml_token
    entity_values = {
        key: env_entities[key] or (
            getattr(yaml_config.entities, key) if yaml_config else None
        )
        for key, env_var in ENTITY_ENV_VARS.items()
    }

    missing = []
    if not url:
        missing.append("HA_URL or homeassistant.url")
    if not token:
        missing.append("HA_TOKEN, HA_TOKEN_FILE, or homeassistant.token")
    missing.extend(
        f"{env_var} or homeassistant.entities.{key}"
        for key, env_var in ENTITY_ENV_VARS.items()
        if not entity_values.get(key)
    )
    if missing:
        raise ValueError(
            "Incomplete Home Assistant configuration. Missing: "
            + ", ".join(missing)
        )

    return HomeAssistantConfig(
        url=url,
        token=token,
        entities=HomeAssistantEntities(**entity_values),
        source=(
            "environment+yaml"
            if yaml_config and used_env
            else ("environment" if used_env else f"yaml:{path}")
        ),
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
        self._default_value = 0

    def _safe_count(self, count: Any) -> int:
        if isinstance(count, int) and count > 0:
            return count
        return 1

    def _log_access(self, access_type: str, address: Any, count: Any, **fields: Any) -> None:
        log_event(
            logging.INFO,
            "Modbus server accessed",
            event="modbus_access",
            block=self.name,
            access_type=access_type,
            address=address,
            count=count,
            **fields,
        )

    def getValues(self, address: int, count: int = 1) -> list[int]:  # pylint: disable=invalid-name
        self._log_access("read", address, count)
        try:
            values = super().getValues(address, count)
        except Exception as exc:
            safe_count = self._safe_count(count)
            log_event(
                logging.ERROR,
                "Modbus read could not be served",
                event="modbus_request_error",
                block=self.name,
                access_type="read",
                address=address,
                count=count,
                fallback_count=safe_count,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return [self._default_value] * safe_count
        if self.log_reads:
            log_event(
                logging.INFO,
                "Modbus read completed",
                event="modbus_read",
                block=self.name,
                address=address,
                count=count,
                returned_count=len(values),
            )
        return values

    def setValues(self, address, values):
        count = len(values) if hasattr(values, "__len__") else None
        self._log_access("write", address, count)
        try:
            return super().setValues(address, values)
        except Exception as exc:
            log_event(
                logging.ERROR,
                "Modbus write could not be applied",
                event="modbus_request_error",
                block=self.name,
                access_type="write",
                address=address,
                count=count,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return None


# ----------------------------
# Home Assistant client
# ----------------------------

class HomeAssistantClient:
    def __init__(self, base_url: str, token: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token_fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": f"smartmeter-faker/{APP_VERSION}",
            }
        )

    def close(self) -> None:
        self.session.close()

    def _get(self, path: str) -> requests.Response:
        url = f"{self.base_url}{path}"
        try:
            response = self.session.get(url, timeout=self.timeout)
        except requests.Timeout as exc:
            raise HomeAssistantConnectivityError(
                f"Timed out contacting Home Assistant at {self.base_url}"
            ) from exc
        except requests.ConnectionError as exc:
            raise HomeAssistantConnectivityError(
                f"Could not connect to Home Assistant at {self.base_url}"
            ) from exc

        if response.status_code in (401, 403):
            raise HomeAssistantAuthError(
                "Home Assistant rejected the access token or the token lacks permission"
            )
        if response.status_code == 404:
            raise HomeAssistantEntityError(f"Home Assistant resource not found: {path}")
        if response.status_code >= 400:
            raise HomeAssistantError(
                f"Home Assistant request failed for {path} with HTTP {response.status_code}"
            )
        return response

    def validate_access(self) -> None:
        response = self._get("/api/")
        try:
            payload = response.json()
        except ValueError as exc:
            raise HomeAssistantError(
                "Home Assistant access check returned invalid JSON"
            ) from exc
        if payload.get("message") != "API running.":
            raise HomeAssistantError("Unexpected response from Home Assistant access check")

    def validate_entities(self, entities: HomeAssistantEntities) -> None:
        for entity_id in entities.__dict__.values():
            self.get_state(entity_id)

    def get_state(self, entity_id: str) -> dict[str, Any]:
        response = self._get(f"/api/states/{entity_id}")
        try:
            return response.json()
        except ValueError as exc:
            raise HomeAssistantError(
                f"Home Assistant returned invalid JSON for entity {entity_id}"
            ) from exc

    def get_float(self, entity_id: str, default: Optional[float] = None) -> Optional[float]:
        data = self.get_state(entity_id)
        state = data.get("state")
        if state in (None, "", "unknown", "unavailable"):
            log_event(
                logging.WARNING,
                "Entity returned unavailable state",
                event="ha_entity_defaulted",
                entity_id=entity_id,
                state=state,
                default=default,
            )
            return default
        try:
            return float(str(state).replace(",", "."))
        except ValueError:
            log_event(
                logging.WARNING,
                "Entity returned non-numeric state",
                event="ha_entity_defaulted",
                entity_id=entity_id,
                state=state,
                default=default,
            )
            return default


class PollReporter:
    def __init__(self, info_interval_s: float = 60.0):
        self.info_interval_s = info_interval_s
        self.last_info_at = 0.0
        self.success_count = 0

    def log_success(self, **fields: Any) -> None:
        now = time.time()
        self.success_count += 1
        log_event(
            logging.DEBUG,
            "Home Assistant poll succeeded",
            event="ha_poll_success",
            success_count=self.success_count,
            **fields,
        )
        if now - self.last_info_at >= self.info_interval_s:
            self.last_info_at = now
            log_event(
                logging.INFO,
                "Home Assistant polling healthy",
                event="ha_poll_heartbeat",
                success_count=self.success_count,
                total_power_w=fields["total_power_w"],
                total_import_wh=fields["total_import_wh"],
            )


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
    reporter: PollReporter,
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

    reporter.log_success(
        total_power_w=round(total_power_w, 3),
        total_pf=round(total_pf, 6),
        l1_power_w=round(l1_power_w, 3),
        l1_a=round(l1_a, 6),
        l1_v=round(l1_v, 6),
        l2_power_w=round(l2_power_w, 3),
        l2_a=round(l2_a, 6),
        l2_v=round(l2_v, 6),
        l3_power_w=round(l3_power_w, 3),
        l3_a=round(l3_a, 6),
        l3_v=round(l3_v, 6),
        total_import_wh=round(total_import_wh, 3),
    )


def calculate_backoff_delay(
    base_interval_s: float,
    consecutive_failures: int,
    max_interval_s: float,
) -> float:
    if consecutive_failures <= 0:
        return base_interval_s
    return min(base_interval_s * math.pow(2, consecutive_failures - 1), max_interval_s)


def updater_loop(
    hr_block: LoggingBlock,
    ha: HomeAssistantClient,
    entities: HomeAssistantEntities,
    health_state: HealthState,
    reporter: PollReporter,
    stop_event: threading.Event,
    interval_s: float,
    frequency_hz: float,
    use_phase_sum_for_total_power: bool,
    max_backoff_s: float,
) -> None:
    consecutive_failures = 0
    while not stop_event.is_set():
        next_delay = interval_s
        try:
            update_em420_registers_from_ha(
                hr_block=hr_block,
                ha=ha,
                entities=entities,
                reporter=reporter,
                frequency_hz=frequency_hz,
                use_phase_sum_for_total_power=use_phase_sum_for_total_power,
            )
            if consecutive_failures:
                log_event(
                    logging.INFO,
                    "Recovered Home Assistant polling",
                    event="ha_poll_recovered",
                    consecutive_failures=consecutive_failures,
                )
            consecutive_failures = 0
            health_state.mark_success()
        except HomeAssistantAuthError as exc:
            consecutive_failures += 1
            health_state.mark_error(str(exc))
            next_delay = calculate_backoff_delay(interval_s, consecutive_failures, max_backoff_s)
            log_event(
                logging.ERROR,
                "Home Assistant authorization failed",
                event="ha_poll_failure",
                failure_type="authorization",
                consecutive_failures=consecutive_failures,
                retry_delay_s=round(next_delay, 3),
                token_fingerprint=ha.token_fingerprint,
                error=str(exc),
            )
        except HomeAssistantError as exc:
            consecutive_failures += 1
            health_state.mark_error(str(exc))
            next_delay = calculate_backoff_delay(interval_s, consecutive_failures, max_backoff_s)
            log_event(
                logging.WARNING,
                "Home Assistant update failed",
                event="ha_poll_failure",
                failure_type=type(exc).__name__,
                consecutive_failures=consecutive_failures,
                retry_delay_s=round(next_delay, 3),
                error=str(exc),
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            consecutive_failures += 1
            health_state.mark_error(str(exc))
            next_delay = calculate_backoff_delay(
                interval_s, consecutive_failures, max_backoff_s
            )
            LOG.exception(
                "Updater failed unexpectedly",
                extra={
                    "event_fields": {
                        "event": "ha_poll_failure",
                        "failure_type": type(exc).__name__,
                        "consecutive_failures": consecutive_failures,
                        "retry_delay_s": round(next_delay, 3),
                    }
                },
            )
        stop_event.wait(next_delay)


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
    parser.add_argument(
        "--max-backoff",
        type=float,
        default=30.0,
        help="Maximum retry delay in seconds after Home Assistant polling failures",
    )
    args = parser.parse_args()

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        handlers=[handler],
        force=True,
    )
    logging.getLogger("pymodbus").setLevel(logging.DEBUG if args.debug else logging.INFO)

    try:
        ha_config = load_homeassistant_config(Path(args.config))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)

    ha_url = (args.ha_url or ha_config.url).strip()
    try:
        ha_token = _validate_token(args.ha_token or ha_config.token)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
    if not ha_token:
        print(
            (
                "Missing Home Assistant token. Set it in the YAML config "
                "or pass --ha-token / HA_TOKEN."
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    ha = HomeAssistantClient(base_url=ha_url, token=ha_token)
    try:
        ha.validate_access()
        ha.validate_entities(ha_config.entities)
    except HomeAssistantAuthError as exc:
        print(f"Home Assistant authorization failed: {exc}", file=sys.stderr)
        sys.exit(1)
    except HomeAssistantError as exc:
        print(f"Home Assistant validation failed: {exc}", file=sys.stderr)
        sys.exit(1)

    health_state = HealthState(Path(args.health_path), APP_VERSION)
    health_state.mark_starting()
    reporter = PollReporter()
    stop_event = threading.Event()

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
            reporter,
            stop_event,
            args.poll_interval,
            args.grid_frequency,
            args.use_phase_sum_for_total_power,
            args.max_backoff,
        ),
        daemon=True,
    )
    updater.start()

    store = ModbusDeviceContext(hr=hr_block)
    context = ModbusServerContext(devices={1: store}, single=False)

    def _handle_signal(signum: int, _frame: object) -> None:
        signal_name = signal.Signals(signum).name
        log_event(
            logging.INFO,
            "Shutdown signal received",
            event="server_signal",
            signal=signal_name,
        )
        stop_event.set()
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    log_event(
        logging.INFO,
        "Starting Modbus bridge",
        event="server_starting",
        version=APP_VERSION,
        host=args.host,
        port=args.port,
        ha_url=ha_url,
        poll_interval_s=args.poll_interval,
        max_backoff_s=args.max_backoff,
    )
    log_event(
        logging.INFO,
        "Home Assistant configuration loaded",
        event="ha_config_loaded",
        config_source=ha_config.source,
        token_fingerprint=ha.token_fingerprint,
    )
    try:
        while not stop_event.is_set():
            try:
                StartTcpServer(context=context, address=(args.host, args.port))
                break
            except KeyboardInterrupt:
                log_event(
                    logging.INFO,
                    "Modbus bridge shutdown requested",
                    event="server_stopping",
                )
                break
            except Exception as exc:
                if stop_event.is_set():
                    break
                log_event(
                    logging.ERROR,
                    "Modbus server crashed unexpectedly; restarting",
                    event="server_runtime_error",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    restart_delay_s=1.0,
                )
                stop_event.wait(1.0)
    finally:
        stop_event.set()
        health_state.mark_stopping()
        ha.close()
        updater.join(timeout=min(args.max_backoff, 5.0) + 1.0)
        log_event(
            logging.INFO,
            "Modbus bridge stopped",
            event="server_stopped",
        )


if __name__ == "__main__":
    main()
