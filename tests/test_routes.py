import pytest
import sqlite3
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient

from tado_local.database import ensure_schema_and_migrate

@pytest.fixture
def test_db():
    """Create a temporary SQLite test database with TadoLocal schema."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as tmp:
        db_path = tmp.name

    # Initialize schema using the actual schema function
    ensure_schema_and_migrate(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Insert sample data
    cursor.execute("INSERT INTO tado_homes (tado_home_id, name, timezone, temperature_unit) VALUES (1, 'My Home', 'Europe/Amsterdam', 'CELSIUS')")

    cursor.execute("INSERT INTO zones (tado_zone_id, tado_home_id, name, zone_type, leader_device_id, order_id) VALUES (100, 1, 'Living Room', 'HEATING', 1, 1)")
    cursor.execute("INSERT INTO zones (tado_zone_id, tado_home_id, name, zone_type, leader_device_id, order_id) VALUES (101, 1, 'Bedroom', 'HOT_WATER', 2, 2)")
    cursor.execute("INSERT INTO zones (tado_zone_id, tado_home_id, name, zone_type, leader_device_id, order_id) VALUES (102, 1, 'Kitchen', 'HEATING', NULL, 3)")

    cursor.execute("INSERT INTO devices (serial_number, aid, zone_id, tado_zone_id, device_type, name, model, manufacturer, firmware_version, battery_state, is_zone_leader) VALUES ('SN001', 1, 1, 100, 'thermostat', 'Living Room Thermostat', 'RU01', 'Tado', '1.45', 'NORMAL', 1)")
    cursor.execute("INSERT INTO devices (serial_number, aid, zone_id, tado_zone_id, device_type, name, model, manufacturer, firmware_version, battery_state, is_zone_leader) VALUES ('SN002', 2, 2, 101, 'bridge', 'Bedroom Bridge', 'RU01', 'Tado', '1.45', 'NORMAL', 1)")
    cursor.execute("INSERT INTO devices (serial_number, aid, zone_id, tado_zone_id, device_type, name, model, manufacturer, firmware_version, battery_state, is_zone_leader) VALUES ('SN003', 3, 3, 102, 'thermostat', 'Kitchen Thermostat', 'RB01', 'Tado', '2.10', 'LOW', 0)")
    cursor.execute("INSERT INTO devices (serial_number, aid, zone_id, tado_zone_id, device_type, name, model, manufacturer, firmware_version, battery_state, is_zone_leader) VALUES ('SN004', 4, 1, 100, 'radiator_thermostat', 'Living Room Radiator', 'RV01', 'Tado', '1.20', 'LOW', 0)")

    cursor.execute("INSERT INTO device_state_history (device_id, timestamp_bucket, current_temperature, target_temperature, current_heating_cooling_state, target_heating_cooling_state, humidity, battery_level) VALUES (1, '20260129100000', 21.5, 20.0, 1, 1, 45, 100)")
    cursor.execute("INSERT INTO device_state_history (device_id, timestamp_bucket, current_temperature, target_temperature, current_heating_cooling_state, target_heating_cooling_state, humidity, battery_level) VALUES (2, '20260129100500', 19.0, 18.0, 1, 1, 50, 95)")
    cursor.execute("INSERT INTO device_state_history (device_id, timestamp_bucket, current_temperature, target_temperature, current_heating_cooling_state, target_heating_cooling_state, humidity, battery_level) VALUES (3, '20260129100700', 22.0, 21.0, 0, 0, 40, 100)")
    cursor.execute("INSERT INTO device_state_history (device_id, timestamp_bucket, current_temperature, target_temperature, current_heating_cooling_state, target_heating_cooling_state, humidity, battery_level) VALUES (2, '20260129100700', 19.5, 18.5, 0, 1, 60, 95)")
    cursor.execute("INSERT INTO device_state_history (device_id, timestamp_bucket, current_temperature, target_temperature, current_heating_cooling_state, target_heating_cooling_state, humidity, battery_level) VALUES (4, '20260129101000', 20.5, 20.0, 1, 0, 45, 60)")
    cursor.execute("INSERT INTO device_state_history (device_id, timestamp_bucket, current_temperature, target_temperature, current_heating_cooling_state, target_heating_cooling_state, humidity, battery_level) VALUES (2, '20260129110500', 22.0, 21.0, 1, 1, 55, 75)")

    conn.commit()

    yield db_path

    conn.close()
    Path(db_path).unlink()


@pytest.fixture
def app_with_state(test_db):
    """Create FastAPI app with actual state manager (no mocks)."""
    from tado_local.routes import create_app, register_routes
    from tado_local.state import DeviceStateManager

    # Create real state manager with test database
    state_manager = DeviceStateManager(test_db)

    # Create mock API with real state manager
    mock_api = AsyncMock()
    mock_api.state_manager = state_manager
    mock_api.pairing = AsyncMock()
    mock_api.set_device_characteristics = AsyncMock(return_value=True)
    mock_api.accessories_cache = []

    mock_api.event_listeners = []
    mock_api.change_tracker = {'events_received': 0, 'polling_changes': 0}
    mock_api.cloud_api = None

    # Deterministic accessory fetch timestamp
    fetched_at = "2026-01-29T10:00:00Z"
    mock_api.last_update = 1700000000.0

    # Inject accessories_cache data
    mock_api.accessories_cache = [
        {
            "id": 1,
            "aid": 1,
            "serial_number": "SN001",
            "name": "Living Room Thermostat",
            "fetched_at": fetched_at,
            "services": [
                {
                    "iid": 2,
                    "type": "0000004A-0000-1000-8000-0026BB765291",
                    "value": "tado Smart Radiator Thermostat VA0123456789"
                 }
             ]
        },
        {
            "id": 2,
            "aid": 2,
            "serial_number": "SN002",
            "name": "Bedroom Thermostat",
            "fetched_at": fetched_at,
            "services": [
                {
                    "iid": 2,
                    "type": "00000023-0000-1000-8000-0026BB765291",
                    "value": "tado Internet Bridge IB0123456789"
                 }
             ]
        },
        {
            "id": 3,
            "aid": 3,
            "serial_number": "SN003",
            "name": "Kitchen Bridge",
            "fetched_at": fetched_at,
            "services": [
                {
                    "iid": 2,
                    "type": "0000004A-0000-1000-8000-0026BB765291",
                    "value": "tado Smart Radiator Thermostat VB0987654321"
                 }
             ]
        }
    ]
    mock_api.refresh_accessories.return_value = mock_api.accessories_cache

    app = create_app()
    register_routes(app, lambda: mock_api)

    return app, state_manager, mock_api


@pytest.fixture
def client(app_with_state):
    """Create test client."""
    app, _, _ = app_with_state
    return TestClient(app)


@pytest.fixture
def state_manager(app_with_state):
    """Extract state manager from app fixture."""
    _, state_manager, _ = app_with_state
    return state_manager


@pytest.fixture
def mock_api(app_with_state):
    """Extract mock API from app fixture."""
    _, _, mock_api = app_with_state
    return mock_api


class TestGetStatus:
    """Test suite for GET /status endpoint."""

    def test_get_status_returns_correct_structure(self, client, state_manager):
        """Test that GET /status returns correct structure."""
        response = client.get("/status")

        assert response.status_code == 200
        data = response.json()

        assert "status" in data
        assert "last_update" in data
        assert "bridge_connected" in data
        assert "cached_accessories" in data
        assert "active_listeners" in data
        assert "polling_changes" in data
        assert "cloud_api" in data


    def test_get_status_return_correct_values(self, client, state_manager, mock_api):
        """Test that GET /status returns correct values."""
        from tado_local.__version__ import __version__

        response = client.get("/status")

        assert response.status_code == 200
        data = response.json()

        assert data["version"] == __version__
        assert data["bridge_connected"] is True
        assert data["status"] == "connected"
        assert data["cached_accessories"] == 3
        assert data["active_listeners"] == 0
        assert data["polling_changes"] == 0
        assert data["cloud_api"]['authenticated'] is False
        assert data["cloud_api"]['enabled'] is False

class TestGetAccessories:
    """Test suite for GET /accessories endpoint."""

    def test_get_accessories_returns_all_accessories(self, client, mock_api):
        """Test that GET /accessories returns the cached accessories."""
        response = client.get("/accessories")

        assert response.status_code == 200
        data = response.json()
        assert "accessories" in data
        assert isinstance(data["accessories"], list)
        assert len(data["accessories"]) == 3

    def test_get_accessories_structure(self, client, mock_api):
        """Test that each accessory contains expected fields."""
        response = client.get("/accessories")
        data = response.json()
        acc = data["accessories"][0]

        assert "aid" in acc
        assert "id" in acc or "aid" in acc  # allow either id or aid presence
        assert "services" in acc
        assert isinstance(acc["services"], list)

    def test_get_accessories_values(self, client, mock_api):
        """Test that accessory values match the mock_api data."""
        response = client.get("/accessories")
        data = response.json()
        acc = data["accessories"][0]

        assert acc["aid"] == 1
        assert acc["id"] == 1
        assert acc["serial_number"] == "SN001"

        assert isinstance(acc["services"], list)
        assert acc["services"][0]["type"] == "0000004A-0000-1000-8000-0026BB765291"
        assert acc["services"][0]["type_name"] == "Thermostat"

    def test_get_accessory_by_aid(self, client, mock_api):
        """Test GET /accessories/{aid} returns specific accessory."""
        response = client.get("/accessories/1")
        assert response.status_code == 200
        data = response.json()
        # endpoint may return the accessory directly or under a key
        acc = data.get("accessory") if isinstance(data, dict) and "accessory" in data else data
        # ensure this is the accessory with aid 1
        assert acc["aid"] == 1 or acc.get("id") == 1

    def test_get_accessory_with_enhanced_is_false(self, client, mock_api):
        """Test GET /accessories/{aid}?enhanced=false returns accessory without enhancements."""
        response = client.get("/accessories?enhanced=false")
        assert response.status_code == 200
        data = response.json()
        assert "enhanced" in data
        assert data["enhanced"] is False
        assert "note" not in data

        acc = data.get("accessory") if isinstance(data, dict) and "accessory" in data else data

        # Check that no extra fields are present (like 'type_name')
        for service in acc.get("services", []):
            assert "type_name" not in service

    def test_get_accessory_with_enhanced_is_true(self, client, mock_api):
        """Test GET /accessories/{aid}?enhanced=true returns accessory without enhancements."""
        response = client.get("/accessories?enhanced=true")
        assert response.status_code == 200
        data = response.json()
        assert "enhanced" in data
        assert data["enhanced"] is True
        assert "note" in data

        # Now fetch without enhanced to compare should be the same (enhaced is default)
        response = client.get("/accessories")
        assert response.status_code == 200
        data_wo = response.json()
        assert data == data_wo

    def test_get_accessory_nonexistent(self, client, mock_api):
        """Test GET /accessories/{aid} for nonexistent accessory returns 404."""
        response = client.get("/accessories/999")
        assert response.status_code == 404

class TestGetZones:
    """Test suite for GET /zones endpoint."""

    def test_get_zones_returns_all_zones(self, client, state_manager):
        """Test that GET /zones returns all zones from database."""
        response = client.get("/zones")

        assert response.status_code == 200
        data = response.json()
        assert "zones" in data
        assert len(data["zones"]) == 3

    def test_get_zones_includes_zone_metadata(self, client, state_manager):
        """Test that zone data includes required metadata."""
        response = client.get("/zones")

        data = response.json()
        zones = data["zones"]

        zone = zones[0]
        assert "zone_id" in zone
        assert "tado_zone_id" in zone
        assert "name" in zone
        assert "leader_device_id" in zone
        assert "order_id" in zone
        assert "state" in zone
        assert "device_count" in zone


    def test_get_zones_ordered_by_order_id(self, client, state_manager):
        """Test that zones are returned ordered by order_id."""
        response = client.get("/zones")

        data = response.json()
        zones = data["zones"]

        assert zones[0]["name"] == "Living Room"
        assert zones[0]["order_id"] == 1
        assert zones[1]["name"] == "Bedroom"
        assert zones[1]["order_id"] == 2
        assert zones[2]["name"] == "Kitchen"
        assert zones[2]["order_id"] == 3
        assert zones[2]["home_id"] is None

        assert "state" in zones[2]
        assert zones[2]['state']['cur_temp_c'] == 22.0
        assert zones[2]['state']['cur_temp_f'] == 71.6
        assert zones[2]['state']['hum_perc'] == 40.0
        assert zones[2]['state']['target_temp_c'] == 21.0
        assert zones[2]['state']['target_temp_f'] == 69.8
        assert zones[2]['state']['mode'] == 0
        assert zones[2]['state']['cur_heating'] == 0


        assert zones[2]["device_count"] == 1

    def test_get_zones_correct_names(self, client, state_manager):
        """Test that zone names are correct from database."""
        response = client.get("/zones")

        data = response.json()
        zones = data["zones"]

        zone_names = [z["name"] for z in zones]
        assert "Living Room" in zone_names
        assert "Bedroom" in zone_names
        assert "Kitchen" in zone_names

    def test_get_zones_leader_device_ids(self, client, state_manager):
        """Test that zone leader device IDs are correct from database."""
        response = client.get("/zones")

        data = response.json()
        zones = data["zones"]

        zone_leaders = {z["name"]: z["leader_device_id"] for z in zones}
        assert zone_leaders["Living Room"] == 1
        assert zone_leaders["Bedroom"] == 2
        assert zone_leaders["Kitchen"] is None  # No leader

    def test_get_zones_tado_zone_ids(self, client, state_manager):
        """Test that tado_zone_ids are correct from database."""
        response = client.get("/zones")

        data = response.json()
        zones = data["zones"]

        zone_tado_ids = {z["name"]: z["tado_zone_id"] for z in zones}
        assert zone_tado_ids["Living Room"] == 100
        assert zone_tado_ids["Bedroom"] == 101
        assert zone_tado_ids["Kitchen"] == 102

    def test_get_zones_response_is_json(self, client, state_manager):
        """Test that response is valid JSON."""
        response = client.get("/zones")

        assert response.status_code == 200
        assert "application/json" in response.headers["content-type"]
        data = response.json()
        assert isinstance(data, dict)

    def test_get_zone_by_id(self, client, state_manager):
        """Test GET /zones/{zone_id} endpoint."""
        response = client.get("/zones/1")

        assert response.status_code == 200
        data = response.json()
        assert "zone" in data
        assert data["zone"]["zone_id"] == 1
        assert data["zone"]["name"] == "Living Room"
        assert "state"in data["zone"]
        assert data["zone"]['state']['cur_temp_c'] == 21.5
        assert data["zone"]['state']['cur_temp_f'] == 70.7
        assert data["zone"]['state']['hum_perc'] == 45.0
        assert data["zone"]['state']['target_temp_c'] == 20.0
        assert data["zone"]['state']['target_temp_f'] == 68.0
        assert data["zone"]['state']['mode'] == 1
        assert data["zone"]['state']['cur_heating'] == 1

    def test_get_zone_nonexistent(self, client, state_manager):
        """Test GET /zones/{zone_id} with nonexistent zone."""
        response = client.get("/zones/999")

        assert response.status_code == 404

    def test_get_zones_all_have_zone_ids(self, client, state_manager):
        """Test that all zones have zone_ids."""
        response = client.get("/zones")

        data = response.json()
        zones = data["zones"]

        for zone in zones:
            assert zone["zone_id"] is not None
            assert isinstance(zone["zone_id"], int)


class TestGetThermostats:
    """Test suite for GET /thermostats endpoint."""

    def test_get_thermostats_returns_all_thermostats(self, client, state_manager):
        """Test that GET /thermostats returns all thermostat devices from database."""
        response = client.get("/thermostats")

        assert response.status_code == 200
        data = response.json()
        assert "thermostats" in data
        assert len(data["thermostats"]) == 2

    def test_get_thermostats_excludes_non_thermostat_devices(self, client, state_manager):
        """Test that GET /thermostats excludes bridge and radiator devices."""
        response = client.get("/thermostats")

        data = response.json()
        thermostats = data["thermostats"]

        device_types = [t["device_type"] for t in thermostats]
        assert all(dt == "thermostat" for dt in device_types)
        assert "bridge" not in device_types
        assert "radiator_thermostat" not in device_types

    def test_get_thermostats_correct_names(self, client, state_manager):
        """Test that thermostat names are correct from database."""
        response = client.get("/thermostats")

        data = response.json()
        thermostats = data["thermostats"]

        names = [t["zone_name"] for t in thermostats]
        assert "Kitchen" in names
        assert "Living Room" in names

    def test_get_thermostats_includes_temperature_data(self, client, state_manager):
        """Test that thermostat data includes temperature information from state."""
        response = client.get("/thermostats")

        data = response.json()
        thermostats = data["thermostats"]

        thermostat = thermostats[0]
        assert "state" in thermostat

        assert thermostat['state'] is not None
        assert "cur_temp_c" in thermostat['state']
        assert "cur_temp_f" in thermostat['state']
        assert "target_temp_c" in thermostat['state']
        assert "target_temp_f" in thermostat['state']
        assert "hum_perc" in thermostat['state']
        assert "mode" in thermostat['state']
        assert "cur_heating" in thermostat['state']
        assert "valve_position" in thermostat['state']
        assert "battery_low" in thermostat['state']

    def test_get_thermostats_temperature_values(self, client, state_manager):
        """Test that thermostat temperature values are correct from state history."""
        response = client.get("/thermostats")

        data = response.json()
        thermostats = data["thermostats"]

        # Living room thermostat
        living_room = next((t for t in thermostats if t["zone_name"] == "Living Room"), None)
        assert living_room is not None
        assert living_room['aid'] == 1
        assert living_room['serial_number'] == 'SN001'
        assert living_room['zone_name'] == "Living Room"
        assert living_room['zone_id'] == 1
        assert living_room['is_zone_leader'] is True

        assert living_room['state']['cur_temp_c'] == 21.5
        assert living_room['state']['cur_temp_f'] == 70.7
        assert living_room['state']['hum_perc'] == 45.0
        assert living_room['state']['target_temp_c'] == 20.0
        assert living_room['state']['target_temp_f'] == 68.0
        assert living_room['state']['mode'] == 1
        assert living_room['state']['cur_heating'] == 1
        assert living_room['state']['valve_position'] is None
        assert living_room['state']['battery_low'] is False

        # kitchen thermostat
        kitchen = next((t for t in thermostats if t["zone_name"] == "Kitchen"), None)
        assert kitchen is not None
        assert kitchen['device_id'] == 3
        assert kitchen['aid'] == 3
        assert kitchen['serial_number'] == 'SN003'
        assert kitchen['zone_name'] == "Kitchen"
        assert kitchen['zone_id'] == 3
        assert kitchen['is_zone_leader'] is False

        assert kitchen['state']['cur_temp_c'] == 22.0
        assert kitchen['state']['cur_temp_f'] == 71.6
        assert kitchen['state']['hum_perc'] == 40.0
        assert kitchen['state']['target_temp_c'] == 21.0
        assert kitchen['state']['target_temp_f'] == 69.8
        assert kitchen['state']['mode'] == 0
        assert kitchen['state']['cur_heating'] == 0
        assert kitchen['state']['valve_position'] is None
        assert kitchen['state']['battery_low'] is True


    def test_get_thermostats_includes_device_metadata(self, client, state_manager):
        """Test that thermostat data includes device metadata from database."""
        response = client.get("/thermostats")

        data = response.json()
        thermostats = data["thermostats"]

        thermostat = thermostats[0]
        assert "device_id" in thermostat
        assert "aid" in thermostat
        assert "serial_number" in thermostat
        assert "zone_name" in thermostat
        assert "zone_id" in thermostat
        assert "device_type" in thermostat
        assert "is_zone_leader" in thermostat

    def test_get_thermostat_by_id(self, client, state_manager):
        """Test GET /thermostats/{thermostat_id} endpoint."""
        response = client.get("/thermostats/3")

        assert response.status_code == 200
        data = response.json()

        assert data["device_id"] == 3
        assert data["zone_name"] == "Kitchen"
        assert data["device_type"] == "thermostat"
        assert data['state']['cur_temp_c'] == 22.0
        assert data['state']['hum_perc'] == 40.0

    def test_get_thermostat_nonexistent(self, client, state_manager):
        """Test GET /thermostats/{thermostat_id} with nonexistent thermostat."""
        response = client.get("/thermostats/999")

        assert response.status_code == 404

    def test_get_thermostat_non_thermostat_device(self, client, state_manager):
        """Test GET /thermostats/{id} with non-thermostat device."""
        # Device 2 is a bridge, not a thermostat
        response = client.get("/thermostats/2")

        assert response.status_code == 400

    def test_get_thermostats_response_is_json(self, client, state_manager):
        """Test that response is valid JSON."""
        response = client.get("/thermostats")

        assert response.status_code == 200
        assert "application/json" in response.headers["content-type"]
        data = response.json()
        assert isinstance(data, dict)

    def test_get_thermostats_all_have_device_ids(self, client, state_manager):
        """Test that all thermostats have device_ids."""
        response = client.get("/thermostats")

        data = response.json()
        thermostats = data["thermostats"]

        for thermostat in thermostats:
            assert thermostat["device_id"] is not None
            assert isinstance(thermostat["device_id"], int)


class TestGetDevices:
    """Test suite for GET /devices endpoint."""

    def test_get_devices_returns_all_devices(self, client, state_manager):
        """Test that GET /devices returns all devices from database."""
        response = client.get("/devices")

        assert response.status_code == 200
        data = response.json()
        assert "devices" in data
        assert len(data["devices"]) == 4
        assert data["count"] == 4

    def test_get_devices_includes_device_metadata(self, client, state_manager):
        """Test that device data includes required metadata."""
        response = client.get("/devices")

        data = response.json()
        devices = data["devices"]

        device = devices[0]
        assert "device_id" in device
        assert "aid" in device
        assert "serial_number" in device
        assert "zone_id" in device
        assert "device_type" in device
        assert "zone_name" in device
        assert "model" in device
        assert "firmware_version" in device
        assert "is_zone_leader" in device
        assert "is_circuit_driver" in device

    def test_get_devices_response_is_json(self, client, state_manager):
        """Test that response is valid JSON."""
        response = client.get("/devices")

        assert response.status_code == 200
        assert "application/json" in response.headers["content-type"]
        data = response.json()
        assert isinstance(data, dict)

    def test_get_devices_includes_correct_metadata(self, client, state_manager):
        """Test that device data includes temperature information from state."""
        response = client.get("/devices")

        data = response.json()
        devices = data["devices"]

        device = devices[0]
        assert device["device_id"] == 1
        assert device["serial_number"] == 'SN001'
        assert device["zone_id"] == 1
        assert device["device_type"] == 'thermostat'
        assert device["zone_name"] == 'Living Room'
        assert device["model"] == 'RU01'
        assert device["firmware_version"] == '1.45'
        assert device["is_zone_leader"] is True
        assert device["is_circuit_driver"] is False

    def test_get_devices_correct_state_data(self, client, state_manager):
        """Test that device state data is correct from state history."""
        response = client.get("/devices")

        data = response.json()
        devices = data["devices"]

        # Living room thermostat
        living_room = next((d for d in devices if d["zone_name"] == "Living Room"), None)
        assert living_room is not None

        assert living_room['state']['cur_temp_c'] == 21.5
        assert living_room['state']['cur_temp_f'] == 70.7
        assert living_room['state']['hum_perc'] == 45.0
        assert living_room['state']['target_temp_c'] == 20.0
        assert living_room['state']['target_temp_f'] == 68.0
        assert living_room['state']['mode'] == 1
        assert living_room['state']['cur_heating'] == 1
        assert living_room['state']['valve_position'] is None
        assert living_room['state']['battery_low'] is False

    def test_get_device_by_id(self, client, state_manager):
        """Test GET /devices/{device_id} endpoint."""
        response = client.get("/devices/4")

        assert response.status_code == 200
        data = response.json()

        assert "device_id" in data
        assert data["device_id"] == 4
        assert data["zone_name"] == "Living Room"
        assert data["device_type"] == "radiator_thermostat"
        assert data['state']['cur_heating'] == 1
        assert data['state']['cur_temp_c'] == 20.5
        assert data['state']['battery_low'] is True

    def test_get_device_nonexistent(self, client, state_manager):
        """Test GET /devices/{device_id} with nonexistent device."""
        response = client.get("/devices/999")

        assert response.status_code == 404

    def test_get_devices_all_have_device_ids(self, client, state_manager):
        """Test that all devices have device_ids."""
        response = client.get("/devices")

        data = response.json()
        devices = data["devices"]

        for device in devices:
            assert device["device_id"] is not None
            assert isinstance(device["device_id"], int)

    def test_get_device_state_data(self, client, state_manager):
        """Test that device state data is correct from state history."""
        response = client.get("/devices/1")  # Living Room Thermostat

        assert response.status_code == 200
        data = response.json()
        state = data["state"]

        assert state['cur_temp_c'] == 21.5
        assert state['cur_temp_f'] == 70.7
        assert state['hum_perc'] == 45.0
        assert state['target_temp_c'] == 20.0
        assert state['target_temp_f'] == 68.0
        assert state['mode'] == 1
        assert state['cur_heating'] == 1

    def test_get_device_is_zone_leader(self, client, state_manager):
        """Test that device is_zone_leader flag is correct from database."""
        response = client.get("/devices/1")  # Living Room Thermostat, is leader

        assert response.status_code == 200
        data = response.json()
        assert data["is_zone_leader"] is True

        response = client.get("/devices/3")  # Kitchen Thermostat, not leader

        assert response.status_code == 200
        data = response.json()
        assert data["is_zone_leader"] is False

    def test_get_device_history(self, client, state_manager):
        """Test GET /devices/{device_id}/history endpoint."""
        response = client.get("/devices/2/history")

        assert response.status_code == 200
        data = response.json()

        assert "count" in data
        assert data["count"] == 3

        assert "device_id" in data
        assert data["device_id"] == 2

        assert "history" in data
        assert len(data["history"]) >= 1

        assert "state" in data["history"][0]
        assert "timestamp" in data["history"][0]

        state = data["history"][0]["state"]
        assert "cur_temp_c" in state
        assert "cur_temp_f" in state
        assert "target_temp_c" in state
        assert "target_temp_f" in state
        assert "mode" in state
        assert "cur_heating" in state
        assert "hum_perc" in state
        assert "battery_low" in state
        assert "valve_position" in state

    def test_get_device_history_valid_state_data(self, client, state_manager):
        """Test that device history state data is correct from state history."""
        response = client.get("/devices/2/history")  # Living Room Radiator

        assert response.status_code == 200
        data = response.json()
        history = data["history"]

        record = history[0]
        state = record["state"]

        assert state['cur_temp_c'] == 22.0
        assert state['cur_temp_f'] == 71.6
        assert state['hum_perc'] == 55.0
        assert state['target_temp_c'] == 21.0
        assert state['target_temp_f'] == 69.8
        assert state['mode'] == 1
        assert state['cur_heating'] == 1
        assert state['valve_position'] is None
        assert state['battery_low'] is False


    def test_get_device_history_nonexistent(self, client, state_manager):
        """Test GET /devices/{device_id}/history with nonexistent device."""
        response = client.get("/devices/999/history")

        assert response.status_code == 200
        data = response.json()

        assert "device_id" in data
        assert data["device_id"] == 999
        assert "history" in data
        assert len(data["history"]) == 0

        assert "count" in data
        assert "limit" in data
        assert "offset" in data

        assert data["count"] == 0
        assert data["limit"] == 100
        assert data["offset"] == 0

    def test_get_device_history_limit(self, client, state_manager):
        """Test GET /devices/{device_id}/history with limit and offset."""
        response = client.get("/devices/2/history?limit=2&offset=0")

        assert response.status_code == 200
        data = response.json()
        assert "history" in data
        assert len(data["history"]) == 2

        assert "count" in data
        assert "limit" in data
        assert "offset" in data

        assert data["count"] == 2
        assert data["limit"] == 2
        assert data["offset"] == 0

    def test_get_device_history_limit_and_offset(self, client, state_manager):
        """Test GET /devices/{device_id}/history with limit and offset."""
        response = client.get("/devices/2/history?limit=2&offset=2")

        assert response.status_code == 200
        data = response.json()
        assert "history" in data
        assert len(data["history"]) == 1

        assert "count" in data
        assert "limit" in data
        assert "offset" in data

        assert data["count"] == 1
        assert data["limit"] == 2
        assert data["offset"] == 2

        history = data["history"]

        record = history[0]
        state = record["state"]

        assert state['cur_temp_c'] == 19.0
        assert state['hum_perc'] == 50.0
        assert state['target_temp_c'] == 18.0
        assert state['mode'] == 1
        assert state['cur_heating'] == 1
        assert state['valve_position'] is None
        assert state['battery_low'] is False



    def test_get_device_history_limit_too_large(self, client, state_manager):
        """Test GET /devices/{device_id}/history with limit and offset."""
        response = client.get("/devices/4/history?limit=2&offset=0")

        assert response.status_code == 200
        data = response.json()
        assert "history" in data
        assert len(data["history"]) == 1

        assert "count" in data
        assert "limit" in data
        assert "offset" in data

        assert data["count"] == 1
        assert data["limit"] == 2
        assert data["offset"] == 0

    def test_get_device_history_limit_exceeds_count(self, client, state_manager):
        """Test GET /devices/{device_id}/history with limit exceeding count."""
        response = client.get("/devices/1/history?limit=10&offset=0")

        assert response.status_code == 200
        data = response.json()
        assert "history" in data
        assert len(data["history"]) == 1  # Only 1 record exists

        assert "count" in data
        assert data["count"] == 1


class TestSetZoneBridgeCommands:
    """Test suite for set_zone route bridge command generation."""

    def test_set_zone_temperature_calls_bridge(self, client, mock_api):
        """Test that setting zone temperature sends correct command to bridge."""
        response = client.post("/zones/1/set?temperature=22.5")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify bridge command was called
        mock_api.set_device_characteristics.assert_called_once()
        call_args = mock_api.set_device_characteristics.call_args

        # Check device_id (leader device 1)
        assert call_args[0][0] == 1

        # Check characteristics passed
        chars = call_args[0][1]
        assert 'target_temperature' in chars
        assert chars['target_temperature'] == 22.5

    def test_set_zone_heating_enabled_calls_bridge(self, client, mock_api):
        """Test that enabling heating sends correct command to bridge."""
        response = client.post("/zones/1/set?heating_enabled=true")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify bridge command was called
        mock_api.set_device_characteristics.assert_called_once()
        call_args = mock_api.set_device_characteristics.call_args

        # Check characteristics passed
        chars = call_args[0][1]
        assert 'target_heating_cooling_state' in chars
        assert chars['target_heating_cooling_state'] == 1  # Heating ON

    def test_set_zone_heating_disabled_calls_bridge(self, client, mock_api):
        """Test that disabling heating sends correct command to bridge."""
        response = client.post("/zones/1/set?heating_enabled=false")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify bridge command was called
        mock_api.set_device_characteristics.assert_called_once()
        call_args = mock_api.set_device_characteristics.call_args

        # Check characteristics passed
        chars = call_args[0][1]
        assert 'target_heating_cooling_state' in chars
        assert chars['target_heating_cooling_state'] == 0  # Heating OFF

    def test_set_zone_temperature_and_heating_calls_bridge(self, client, mock_api):
        """Test that setting both temperature and heating sends both commands."""
        response = client.post("/zones/1/set?temperature=21&heating_enabled=true")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify bridge command was called
        mock_api.set_device_characteristics.assert_called_once()
        call_args = mock_api.set_device_characteristics.call_args

        # Check characteristics passed
        chars = call_args[0][1]
        assert 'target_temperature' in chars
        assert chars['target_temperature'] == 21
        assert 'target_heating_cooling_state' in chars
        assert chars['target_heating_cooling_state'] == 1

    def test_set_zone_temperature_zero_disables_heating_bridge_call(self, client, mock_api):
        """Test that temperature=0 generates disable heating command."""
        response = client.post("/zones/1/set?temperature=0")

        assert response.status_code == 200

        # Verify bridge command was called with heating disabled
        mock_api.set_device_characteristics.assert_called_once()
        call_args = mock_api.set_device_characteristics.call_args

        chars = call_args[0][1]
        assert 'target_heating_cooling_state' in chars
        assert chars['target_heating_cooling_state'] == 0
        # Temperature should NOT be set when using 0
        assert 'target_temperature' not in chars

    def test_set_zone_temperature_resume_schedule_bridge_call(self, client, mock_api):
        """Test that temperature=-1 generates heating enable without temperature."""
        response = client.post("/zones/1/set?temperature=-1")

        assert response.status_code == 200

        # Verify bridge command was called
        mock_api.set_device_characteristics.assert_called_once()
        call_args = mock_api.set_device_characteristics.call_args

        chars = call_args[0][1]
        assert 'target_heating_cooling_state' in chars
        assert chars['target_heating_cooling_state'] == 1
        # Temperature should NOT be set when using -1
        assert 'target_temperature' not in chars

    def test_set_zone_calls_leader_device(self, client, mock_api):
        """Test that zone control calls the zone's leader device."""
        response = client.post("/zones/1/set?temperature=20")

        assert response.status_code == 200

        # Verify bridge command was called on leader device (device_id=1)
        mock_api.set_device_characteristics.assert_called_once()
        call_args = mock_api.set_device_characteristics.call_args

        device_id = call_args[0][0]
        assert device_id == 1  # Leader device ID for zone 1

    def test_set_zone_multiple_calls(self, client, mock_api):
        """Test that multiple zone control calls work correctly."""
        # First call
        response1 = client.post("/zones/1/set?temperature=20")
        assert response1.status_code == 200

        # Reset mock
        mock_api.set_device_characteristics.reset_mock()

        # Second call on different zone
        response2 = client.post("/zones/2/set?temperature=22")
        assert response2.status_code == 200

        # Verify second call was to correct leader (device_id=2)
        mock_api.set_device_characteristics.assert_called_once()
        call_args = mock_api.set_device_characteristics.call_args

        device_id = call_args[0][0]
        assert device_id == 2  # Leader device ID for zone 2

    def test_set_zone_temperature_characteristic_uuid(self, client, mock_api):
        """Test that temperature characteristic uses correct UUID."""
        response = client.post("/zones/1/set?temperature=23")

        assert response.status_code == 200

        call_args = mock_api.set_device_characteristics.call_args
        chars = call_args[0][1]

        # The key should be 'target_temperature' (implementation detail)
        assert 'target_temperature' in chars
        assert chars['target_temperature'] == 23

    def test_set_zone_heating_characteristic_uuid(self, client, mock_api):
        """Test that heating characteristic uses correct HomeKit service."""
        response = client.post("/zones/1/set?heating_enabled=true")

        assert response.status_code == 200

        call_args = mock_api.set_device_characteristics.call_args
        chars = call_args[0][1]

        # The key should be 'target_heating_cooling_state'
        assert 'target_heating_cooling_state' in chars
        assert chars['target_heating_cooling_state'] == 1

    def test_set_zone_temperature_wrong_zone_id(self, client, mock_api):
        """Test that temperature=-1 generates heating enable without temperature."""
        response = client.post("/zones/999/set?temperature=22")

        assert response.status_code == 404

        # Verify bridge command was not called
        mock_api.set_device_characteristics.assert_not_called()

class TestSetDeviceBridgeCommands:
    """Test suite for set_device route bridge command generation."""

    def test_set_device_forwards_to_zone(self, client, mock_api):
        """Test that device control forwards to zone control which calls bridge."""
        response = client.post("/devices/1/set?temperature=22")

        assert response.status_code == 200
        data = response.json()
        assert data["controlled_via"] == "zone"

        # Verify bridge command was called through zone control
        mock_api.set_device_characteristics.assert_called_once()
        call_args = mock_api.set_device_characteristics.call_args

        # Should call leader device
        device_id = call_args[0][0]
        assert device_id == 1

    def test_set_device_calls_bridge(self, client, mock_api):
        """Test that setting device temperature sends command to bridge."""
        response = client.post("/devices/1/set?temperature=21")

        assert response.status_code == 200

        # Verify bridge command was called
        mock_api.set_device_characteristics.assert_called_once()
        call_args = mock_api.set_device_characteristics.call_args

        # Check characteristics
        chars = call_args[0][1]
        assert 'target_temperature' in chars
        assert chars['target_temperature'] == 21

    def test_set_device_heating_enabled_bridge_call(self, client, mock_api):
        """Test that enabling device heating sends command to bridge."""
        response = client.post("/devices/1/set?heating_enabled=true")

        assert response.status_code == 200

        # Verify bridge command was called
        mock_api.set_device_characteristics.assert_called_once()
        call_args = mock_api.set_device_characteristics.call_args

        chars = call_args[0][1]
        assert 'target_heating_cooling_state' in chars
        assert chars['target_heating_cooling_state'] == 1

    def test_set_device_temperature_and_heating_bridge_call(self, client, mock_api):
        """Test that setting both device temp and heating sends both commands."""
        response = client.post("/devices/1/set?temperature=20&heating_enabled=true")

        assert response.status_code == 200

        # Verify bridge command was called
        mock_api.set_device_characteristics.assert_called_once()
        call_args = mock_api.set_device_characteristics.call_args

        chars = call_args[0][1]
        assert 'target_temperature' in chars
        assert chars['target_temperature'] == 20
        assert 'target_heating_cooling_state' in chars
        assert chars['target_heating_cooling_state'] == 1

    def test_set_device_temperature_zero_bridge_call(self, client, mock_api):
        """Test that device temperature=0 sends disable heating to bridge."""
        response = client.post("/devices/1/set?temperature=0")

        assert response.status_code == 200

        # Verify bridge command was called with heating disabled
        mock_api.set_device_characteristics.assert_called_once()
        call_args = mock_api.set_device_characteristics.call_args

        chars = call_args[0][1]
        assert 'target_heating_cooling_state' in chars
        assert chars['target_heating_cooling_state'] == 0

    def test_set_device_different_devices(self, client, mock_api):
        """Test that different device controls call correct leaders."""
        # Device 1 (zone 1, leader 1)
        response1 = client.post("/devices/1/set?temperature=20")
        assert response1.status_code == 200

        call_args1 = mock_api.set_device_characteristics.call_args
        assert call_args1[0][0] == 1  # Leader device 1

        # Reset mock
        mock_api.set_device_characteristics.reset_mock()

        # Device 2 (zone 2, leader 2)
        response2 = client.post("/devices/2/set?temperature=22")
        assert response2.status_code == 200

        call_args2 = mock_api.set_device_characteristics.call_args
        assert call_args2[0][0] == 2  # Leader device 2

    def test_set_device_with_implicit_heating_enabled(self, client, mock_api):
        """Test that setting device temperature >= 5 implicitly enables heating."""
        response = client.post("/devices/1/set?temperature=18")

        assert response.status_code == 200

        call_args = mock_api.set_device_characteristics.call_args
        chars = call_args[0][1]

        # Both temperature and heating should be set
        assert 'target_temperature' in chars
        assert chars['target_temperature'] == 18
        assert 'target_heating_cooling_state' in chars
        assert chars['target_heating_cooling_state'] == 1


class TestSetThermostatBridgeCommands:
    """Test suite for set_thermostat route bridge command generation."""

    def test_set_thermostat_forwards_to_zone(self, client, mock_api):
        """Test that thermostat control forwards to zone control."""
        response = client.post("/thermostats/1/set?temperature=21")

        assert response.status_code == 200
        data = response.json()
        assert data["controlled_via"] == "zone"

        # Verify bridge command was called
        mock_api.set_device_characteristics.assert_called_once()

    def test_set_thermostat_calls_bridge(self, client, mock_api):
        """Test that thermostat temperature command reaches bridge."""
        response = client.post("/thermostats/1/set?temperature=23")

        assert response.status_code == 200

        # Verify bridge command was called
        mock_api.set_device_characteristics.assert_called_once()
        call_args = mock_api.set_device_characteristics.call_args

        chars = call_args[0][1]
        assert 'target_temperature' in chars
        assert chars['target_temperature'] == 23

    def test_set_thermostat_heating_enabled_bridge_call(self, client, mock_api):
        """Test that thermostat heating enable reaches bridge."""
        response = client.post("/thermostats/1/set?heating_enabled=true")

        assert response.status_code == 200

        call_args = mock_api.set_device_characteristics.call_args
        chars = call_args[0][1]

        assert 'target_heating_cooling_state' in chars
        assert chars['target_heating_cooling_state'] == 1

    def test_set_thermostat_temperature_and_heating_bridge_call(self, client, mock_api):
        """Test that thermostat with both parameters sends both to bridge."""
        response = client.post("/thermostats/1/set?temperature=19&heating_enabled=false")

        assert response.status_code == 200

        call_args = mock_api.set_device_characteristics.call_args
        chars = call_args[0][1]

        assert 'target_temperature' in chars
        assert chars['target_temperature'] == 19
        assert 'target_heating_cooling_state' in chars
        assert chars['target_heating_cooling_state'] == 0


class TestBridgeCommandErrorHandling:
    """Test suite for error handling in bridge commands."""

    def test_set_zone_bridge_failure_returns_error(self, client, mock_api):
        """Test that bridge command failure returns proper error."""
        mock_api.set_device_characteristics.side_effect = Exception("Connection lost")

        response = client.post("/zones/1/set?temperature=20")

        assert response.status_code == 500
        data = response.json()
        assert "Failed to set zone control" in data["detail"]

    def test_set_device_bridge_failure_returns_error(self, client, mock_api):
        """Test that device command bridge failure returns proper error."""
        mock_api.set_device_characteristics.side_effect = Exception("Timeout")

        response = client.post("/devices/1/set?temperature=20")

        assert response.status_code == 500

    def test_set_zone_no_bridge_connection(self, client, mock_api):
        """Test that setting zone with no bridge connection fails gracefully."""
        mock_api.pairing = None

        response = client.post("/zones/1/set?temperature=20")

        assert response.status_code == 503
        data = response.json()
        assert "Bridge not connected" in data["detail"]

    def test_set_zone_api_not_initialized(self):
        """Test that setting zone when API not initialized fails."""
        from tado_local.routes import create_app, register_routes

        app = create_app()
        register_routes(app, lambda: None)
        test_client = TestClient(app)

        response = test_client.post("/zones/1/set?temperature=20")

        assert response.status_code == 503


class TestBridgeCommandValidation:
    """Test suite for validation before bridge commands."""

    def test_set_zone_invalid_temperature_no_bridge_call(self, client, mock_api):
        """Test that invalid temperature prevents bridge call."""
        response = client.post("/zones/1/set?temperature=35")

        assert response.status_code == 400
        # Bridge should NOT have been called
        mock_api.set_device_characteristics.assert_not_called()

    def test_set_zone_no_parameters_no_bridge_call(self, client, mock_api):
        """Test that missing parameters prevent bridge call."""
        response = client.post("/zones/1/set")

        assert response.status_code == 400
        # Bridge should NOT have been called
        mock_api.set_device_characteristics.assert_not_called()

    def test_set_zone_nonexistent_zone_no_bridge_call(self, client, mock_api):
        """Test that nonexistent zone prevents bridge call."""
        response = client.post("/zones/999/set?temperature=20")

        assert response.status_code == 404
        # Bridge should NOT have been called
        mock_api.set_device_characteristics.assert_not_called()

    def test_set_device_nonexistent_device_no_bridge_call(self, client, mock_api):
        """Test that nonexistent device prevents bridge call."""
        response = client.post("/devices/999/set?temperature=20")

        assert response.status_code == 404
        # Bridge should NOT have been called
        mock_api.set_device_characteristics.assert_not_called()
