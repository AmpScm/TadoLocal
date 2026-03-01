import sqlite3
from unittest.mock import MagicMock, patch
import pytest

from tado_local.database import ensure_schema_and_migrate
from tado_local.sync import TadoCloudSync


@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "test_sync.db"
    ensure_schema_and_migrate(str(db_path))
    return str(db_path)


@pytest.fixture
def syncer(temp_db):
    return TadoCloudSync(temp_db)


class TestSyncHome:
    def test_normalize_device_type(self, syncer):
        """Test that device types are normalized correctly."""
        from tado_local.sync import normalize_device_type

        type = normalize_device_type("VA02")
        assert type == "radiator_valve"

        type = normalize_device_type("IB01")
        assert type == "internet_bridge"

        type = normalize_device_type("SU02")
        assert type == "smart_ac_control"

        # Unknown types should return the original string
        type = normalize_device_type("mock-name")
        assert type == "mock-name"

        # Nove value should return "unknown" and not raise an exception
        try:
            device_type = normalize_device_type(None)
        except Exception as exc:
            pytest.fail(f"normalize_device_type('None') raised unexpectedly: {exc}")
        assert device_type == "unknown"

    def test_sync_home_inserts_row(self, syncer):
        home_data = {
            "id": 123,
            "name": "My Home",
            "dateTimeZone": "Europe/Amsterdam",
            "temperatureUnit": "CELSIUS",
        }

        assert syncer.sync_home(home_data) is True

        conn = sqlite3.connect(syncer.db_path)
        row = conn.execute(
            "SELECT tado_home_id, name, timezone, temperature_unit FROM tado_homes WHERE tado_home_id = ?",
            (123,),
        ).fetchone()
        conn.close()

        assert row == (123, "My Home", "Europe/Amsterdam", "CELSIUS")

    def test_sync_home_returns_false_on_error(self, syncer):
        with patch("tado_local.sync.sqlite3.connect", side_effect=Exception("db down")):
            assert syncer.sync_home({"id": 1, "name": "x"}) is False


class TestSyncZones:
    def test_sync_zones_creates_zone_devices_and_skips_hot_water(self, syncer):
        zones_data = [
            {
                "id": 10,
                "name": "Living Room",
                "type": "HEATING",
                "devices": [
                    {
                        "serialNo": "RU123456789",
                        "deviceType": "THERMOSTAT",
                        "currentFwVersion": "1.0",
                        "batteryState": "GOOD",
                        "duties": ["ZONE_LEADER"],
                    }
                ],
            },
            {
                "id": 20,
                "name": "Hot Water",
                "type": "HOT_WATER",
                "devices": [{"serialNo": "HW1", "deviceType": "X"}],
            },
        ]

        assert syncer.sync_zones(zones_data, home_id=1) is True

        conn = sqlite3.connect(syncer.db_path)
        zone_count = conn.execute(
            "SELECT COUNT(*) FROM zones WHERE tado_home_id = ?",
            (1,),
        ).fetchone()[0]
        device = conn.execute(
            "SELECT serial_number, is_zone_leader FROM devices WHERE serial_number = ?",
            ("RU123456789",),
        ).fetchone()
        leader = conn.execute(
            "SELECT leader_device_id FROM zones WHERE tado_home_id = ? AND tado_zone_id = ?",
            (1, 10),
        ).fetchone()
        conn.close()

        assert zone_count == 1
        assert device is not None
        assert device[0] == "RU123456789"
        assert device[1] in (1, True)
        assert leader is not None
        assert leader[0] is not None

    def test_sync_zones_updates_existing_and_removes_stale(self, syncer):
        conn = sqlite3.connect(syncer.db_path)
        conn.execute("INSERT INTO zones (tado_zone_id, tado_home_id, name, zone_type, order_id, uuid) " "VALUES (1, 7, 'Old', 'HEATING', 9, 'u1')")
        conn.execute("INSERT INTO zones (tado_zone_id, tado_home_id, name, zone_type, order_id, uuid) " "VALUES (99, 7, 'Stale', 'HEATING', 1, 'u2')")
        conn.commit()
        conn.close()

        zones_data = [{"id": 1, "name": "New Name", "type": "HEATING", "devices": []}]
        assert syncer.sync_zones(zones_data, home_id=7) is True

        conn = sqlite3.connect(syncer.db_path)
        updated_name = conn.execute(
            "SELECT name FROM zones WHERE tado_home_id = ? AND tado_zone_id = ?",
            (7, 1),
        ).fetchone()[0]
        stale = conn.execute(
            "SELECT COUNT(*) FROM zones WHERE tado_home_id = ? AND tado_zone_id = ?",
            (7, 99),
        ).fetchone()[0]
        conn.close()

        assert updated_name == "New Name"
        assert stale == 0

    def test_sync_zones_with_existing_devices(self, syncer):
        conn = sqlite3.connect(syncer.db_path)
        conn.execute("INSERT INTO devices (serial_number, name, device_type) VALUES ('RU001', 'dev', 'unknown')")
        conn.commit()
        conn.close()

        zones_data = [
            {
                "id": 1,
                "name": "Zone 1",
                "type": "HEATING",
                "devices": [
                    {
                        "serialNo": "RU001",
                        "deviceType": "THERMOSTAT",
                        "currentFwVersion": "1.0",
                        "batteryState": "GOOD",
                        "duties": ["ZONE_LEADER"],
                    }
                ],
            }
        ]

        assert syncer.sync_zones(zones_data, home_id=1) is True

        conn = sqlite3.connect(syncer.db_path)
        device = conn.execute(
            "SELECT serial_number, name, device_type FROM devices WHERE serial_number = ?",
            ("RU001",),
        ).fetchone()
        leader = conn.execute(
            "SELECT leader_device_id FROM zones WHERE tado_home_id = ? AND tado_zone_id = ?",
            (1, 1),
        ).fetchone()
        conn.close()

        assert device is not None
        assert device[0] == "RU001"
        assert device[1] == "dev"
        assert device[2] == "THERMOSTAT"
        assert leader is not None
        assert leader[0] is not None


class TestSyncZoneStatesData:
    def test_sync_zone_states_data_creates_humidity_tasks(self, syncer):
        conn = sqlite3.connect(syncer.db_path)
        conn.execute("INSERT INTO devices (serial_number, aid, tado_zone_id, name)" "VALUES ('RU001', 11, '1', 'dev1')")
        conn.commit()
        conn.close()

        zone_states_data = {
            "zoneStates": {
                "1": {
                    "setting": {"type": "HEATING"},
                    "sensorDataPoints": {"humidity": {"percentage": 56}},
                }
            }
        }

        tado_api = MagicMock()
        tado_api.get_iid_from_characteristics.return_value = 200

        with patch("tado_local.sync.asyncio.create_task") as create_task:
            assert syncer.sync_zone_states_data(zone_states_data, home_id=1, tado_api=tado_api) is True

        tado_api.get_iid_from_characteristics.assert_any_call(11, "CurrentRelativeHumidity")
        assert create_task.call_count >= 1

    def test_sync_zone_states_data_creates_temperature_tasks(self, syncer):
        conn = sqlite3.connect(syncer.db_path)
        conn.execute("INSERT INTO devices (serial_number, aid, tado_zone_id, name)" "VALUES ('SU001', 22, '5', 'ac_ctrl')")
        conn.commit()
        conn.close()

        zone_states_data = {
            "zoneStates": {
                "5": {
                    "setting": {"type": "AIR_CONDITIONING"},
                    "sensorDataPoints": {
                        "insideTemperature": {"celsius": 23.4, "fahrenheit": 74.1},
                    },
                }
            }
        }

        tado_api = MagicMock()
        tado_api.get_iid_from_characteristics.return_value = 300

        with patch("tado_local.sync.asyncio.create_task") as create_task:
            assert syncer.sync_zone_states_data(zone_states_data, home_id=1, tado_api=tado_api) is True

        tado_api.get_iid_from_characteristics.assert_any_call(22, "CurrentTemperature")
        create_task.assert_called_once()

    def test_sync_zone_states_data_syncs_both_temp_and_humidity(self, syncer):
        conn = sqlite3.connect(syncer.db_path)
        conn.execute("INSERT INTO devices (serial_number, aid, tado_zone_id, name)" "VALUES ('RU002', 33, '7', 'thermostat')")
        conn.commit()
        conn.close()

        zone_states_data = {
            "zoneStates": {
                "7": {
                    "setting": {"type": "HEATING"},
                    "sensorDataPoints": {
                        "insideTemperature": {"celsius": 21.0},
                        "humidity": {"percentage": 48},
                    },
                }
            }
        }

        tado_api = MagicMock()
        tado_api.get_iid_from_characteristics.return_value = 100

        with patch("tado_local.sync.asyncio.create_task") as create_task:
            assert syncer.sync_zone_states_data(zone_states_data, home_id=1, tado_api=tado_api) is True

        calls = tado_api.get_iid_from_characteristics.call_args_list
        char_names = [c[0][1] for c in calls]
        assert "CurrentRelativeHumidity" in char_names
        assert "CurrentTemperature" in char_names
        assert create_task.call_count == 2

    def test_sync_zone_states_data_skips_when_no_sensor_data(self, syncer):
        zone_states_data = {
            "zoneStates": {
                "1": {
                    "setting": {"type": "HEATING"},
                    "sensorDataPoints": {},
                }
            }
        }

        tado_api = MagicMock()

        with patch("tado_local.sync.asyncio.create_task") as create_task:
            assert syncer.sync_zone_states_data(zone_states_data, home_id=1, tado_api=tado_api) is True

        create_task.assert_not_called()

    def test_sync_zone_states_data_skips_hot_water(self, syncer):
        zone_states_data = {
            "zoneStates": {
                "1": {
                    "setting": {"type": "HOT_WATER"},
                    "sensorDataPoints": {"humidity": {"percentage": 40}},
                }
            }
        }

        tado_api = MagicMock()

        with patch("tado_local.sync.asyncio.create_task") as create_task:
            assert syncer.sync_zone_states_data(zone_states_data, home_id=1, tado_api=tado_api) is True

        create_task.assert_not_called()


class TestSyncDeviceList:
    def test_sync_device_list_updates_existing_device(self, syncer):
        conn = sqlite3.connect(syncer.db_path)
        conn.execute("INSERT INTO devices (serial_number, name, device_type) " + "VALUES ('RU001', 'dev', 'unknown')")
        conn.commit()
        conn.close()

        payload = {
            "entries": [
                {
                    "device": {
                        "serialNo": "RU001",
                        "batteryState": "GOOD",
                        "currentFwVersion": "2.1",
                        "deviceType": "VA02",
                    },
                    "zone": {"discriminator": 9},
                }
            ]
        }

        assert syncer.sync_device_list(payload, home_id=1) is True

        conn = sqlite3.connect(syncer.db_path)
        row = conn.execute("SELECT battery_state, firmware_version, tado_zone_id, model " + "FROM devices WHERE serial_number = 'RU001'").fetchone()
        conn.close()

        assert row[0] == "GOOD"
        assert row[1] == "2.1"
        assert str(row[2]) == "9"
        assert row[3] == "VA02"

    def test_sync_device_list_ignores_missing_device_data(self, syncer):
        payload = {"entries": [{"zone": {"discriminator": 1}}, {"device": {}}]}
        assert syncer.sync_device_list(payload, home_id=1) is True


class TestSyncAll:
    @pytest.mark.asyncio
    async def test_sync_all_returns_false_when_not_authenticated(self, syncer):
        cloud_api = MagicMock()
        cloud_api.is_authenticated.return_value = False

        ok = await syncer.sync_all(cloud_api)
        assert ok is False

    @pytest.mark.asyncio
    async def test_sync_all_with_prefetched_data(self, syncer):
        cloud_api = MagicMock()
        cloud_api.is_authenticated.return_value = True
        cloud_api.home_id = 100
        cloud_api.tado_api = MagicMock()

        with (
            patch.object(syncer, "sync_home", return_value=True) as p_home,
            patch.object(syncer, "sync_zones", return_value=True) as p_zones,
            patch.object(syncer, "sync_device_list", return_value=True) as p_devices,
        ):
            ok = await syncer.sync_all(
                cloud_api,
                home_data={"id": 100, "name": "H"},
                zones_data=[{"id": 1, "name": "Z", "type": "HEATING", "devices": []}],
                zone_states_data={"zoneStates": {}},
                devices_data={"entries": []},
            )

        assert ok is True
        p_home.assert_called_once()
        p_zones.assert_called_once()
        p_devices.assert_called_once()
