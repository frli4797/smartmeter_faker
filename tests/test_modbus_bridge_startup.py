import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock
import sys


def install_dependency_stubs() -> None:
    requests_module = types.ModuleType("requests")
    requests_module.Timeout = type("Timeout", (Exception,), {})
    requests_module.ConnectionError = type("ConnectionError", (Exception,), {})

    class Session:
        def __init__(self) -> None:
            self.headers = {}

        def close(self) -> None:
            return None

    class Response:
        def __init__(self) -> None:
            self.status_code = 200

        def json(self):
            return {}

    requests_module.Session = Session
    requests_module.Response = Response
    sys.modules.setdefault("requests", requests_module)

    yaml_module = types.ModuleType("yaml")
    yaml_module.YAMLError = Exception
    yaml_module.safe_load = lambda _data: {}
    sys.modules.setdefault("yaml", yaml_module)

    pymodbus_module = types.ModuleType("pymodbus")
    datastore_module = types.ModuleType("pymodbus.datastore")
    server_module = types.ModuleType("pymodbus.server")

    class ModbusSequentialDataBlock:
        def __init__(self, address: int, values: list[int]) -> None:
            self.address = address
            self.values = list(values)

        def getValues(self, address: int, count: int = 1) -> list[int]:
            return self.values[address:address + count]

        def setValues(self, address: int, values: list[int]) -> None:
            for index, value in enumerate(values):
                position = address + index
                if position >= len(self.values):
                    self.values.extend([0] * (position - len(self.values) + 1))
                self.values[position] = value

    class ModbusDeviceContext:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class ModbusServerContext:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    datastore_module.ModbusSequentialDataBlock = ModbusSequentialDataBlock
    datastore_module.ModbusDeviceContext = ModbusDeviceContext
    datastore_module.ModbusServerContext = ModbusServerContext
    server_module.StartTcpServer = lambda *args, **kwargs: None

    sys.modules.setdefault("pymodbus", pymodbus_module)
    sys.modules.setdefault("pymodbus.datastore", datastore_module)
    sys.modules.setdefault("pymodbus.server", server_module)


install_dependency_stubs()

from smartmeter_bridge import modbus_bridge


def make_config() -> modbus_bridge.HomeAssistantConfig:
    return modbus_bridge.HomeAssistantConfig(
        url="http://homeassistant.local:8123",
        token="token",
        entities=modbus_bridge.HomeAssistantEntities(
            total_power_w="sensor.total_power_w",
            total_pf="sensor.total_pf",
            total_import_kwh="sensor.total_import_kwh",
            l1_v="sensor.l1_v",
            l2_v="sensor.l2_v",
            l3_v="sensor.l3_v",
            l1_a="sensor.l1_a",
            l2_a="sensor.l2_a",
            l3_a="sensor.l3_a",
        ),
        source="test",
    )


class MainStartupTests(unittest.TestCase):
    def run_main(
        self,
        client: mock.Mock,
        health_path: Path,
    ) -> tuple[mock.Mock, mock.Mock]:
        thread = mock.Mock()
        with (
            mock.patch(
                "sys.argv",
                [
                    "modbus_bridge.py",
                    "--health-path",
                    str(health_path),
                ],
            ),
            mock.patch.object(
                modbus_bridge, "load_homeassistant_config", return_value=make_config()
            ),
            mock.patch.object(modbus_bridge, "HomeAssistantClient", return_value=client),
            mock.patch.object(
                modbus_bridge, "StartTcpServer", side_effect=KeyboardInterrupt
            ) as start_server,
            mock.patch.object(modbus_bridge.signal, "signal"),
            mock.patch.object(modbus_bridge.threading, "Thread", return_value=thread),
        ):
            modbus_bridge.main()
        return start_server, thread

    def test_main_keeps_running_when_home_assistant_is_temporarily_unavailable(self) -> None:
        client = mock.Mock()
        client.validate_access.side_effect = modbus_bridge.HomeAssistantError(
            "Home Assistant request failed for /api/ with HTTP 502"
        )
        client.token_fingerprint = "fingerprint"

        with tempfile.TemporaryDirectory() as temp_dir:
            start_server, thread = self.run_main(
                client=client,
                health_path=Path(temp_dir) / "health.json",
            )

        start_server.assert_called_once()
        client.validate_entities.assert_not_called()
        client.close.assert_called_once()
        thread.start.assert_called_once()
        thread.join.assert_called_once()

    def test_main_still_exits_on_home_assistant_auth_failure(self) -> None:
        client = mock.Mock()
        client.validate_access.side_effect = modbus_bridge.HomeAssistantAuthError(
            "token rejected"
        )
        client.token_fingerprint = "fingerprint"

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(SystemExit) as exc:
                self.run_main(
                    client=client,
                    health_path=Path(temp_dir) / "health.json",
                )

        self.assertEqual(exc.exception.code, 1)
        client.close.assert_called_once()


class PowerFactorCalculationTests(unittest.TestCase):
    def test_calculate_three_phase_power_factor(self) -> None:
        power_factor = modbus_bridge.calculate_three_phase_power_factor(
            total_power_w=9000.0,
            l1_v=230.0,
            l2_v=230.0,
            l3_v=230.0,
            l1_a=14.0,
            l2_a=14.0,
            l3_a=14.0,
        )

        self.assertAlmostEqual(power_factor, 0.9316770186, places=6)

    def test_load_homeassistant_config_allows_missing_total_pf_when_calculation_enabled(self) -> None:
        config = {
            "HA_URL": "http://homeassistant.local:8123",
            "HA_TOKEN": "token",
            "CALCULATE_POWER_FACTOR": "true",
            "HA_ENTITY_TOTAL_POWER_W": "sensor.total_power_w",
            "HA_ENTITY_TOTAL_IMPORT_KWH": "sensor.total_import_kwh",
            "HA_ENTITY_L1_V": "sensor.l1_v",
            "HA_ENTITY_L2_V": "sensor.l2_v",
            "HA_ENTITY_L3_V": "sensor.l3_v",
            "HA_ENTITY_L1_A": "sensor.l1_a",
            "HA_ENTITY_L2_A": "sensor.l2_a",
            "HA_ENTITY_L3_A": "sensor.l3_a",
        }

        with mock.patch.dict(modbus_bridge.os.environ, config, clear=True):
            loaded = modbus_bridge.load_homeassistant_config(Path("missing.yaml"))

        self.assertIsNone(loaded.entities.total_pf)


if __name__ == "__main__":
    unittest.main()
