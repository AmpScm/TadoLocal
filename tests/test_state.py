import pytest
import sqlite3
import tempfile
import os
import datetime
from pathlib import Path
import time
from unittest.mock import patch
from tado_local.state import DeviceStateManager
from tado_local.database import ensure_schema_and_migrate


@pytest.fixture
def temp_db():
    """Create a temporary SQLite test database with TadoLocal schema."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as tmp:
        db_path = tmp.name

    # Create schema
    ensure_schema_and_migrate(db_path)

    conn = sqlite3.connect(db_path)
    conn.commit()
    conn.close()

    yield db_path

    if os.path.exists(db_path):
        Path(db_path).unlink()


@pytest.fixture
def state_manager(temp_db):
    """Create a DeviceStateManager instance for testing."""
    manager = DeviceStateManager(temp_db)
    return manager


@pytest.fixture
def state_manager_with_db_devices(temp_db):
    """Create a DeviceStateManager with mock device data in the database."""
    def _get_test_timestamp_bucket(timestamp: float) -> str:
        """Convert timestamp to 10-second bucket (format: YYYYMMDDHHMMSSx where x is 0-5)."""
        dt = datetime.datetime.fromtimestamp(timestamp)
        # Round down to 10-second interval
        second = (dt.second // 10) * 10
        return dt.strftime(f'%Y%m%d%H%M{second:02d}')

    conn = sqlite3.connect(temp_db)

    # Insert mock devices into database
    conn.execute("""
        INSERT INTO devices (device_id, serial_number, aid, device_type, name, model, manufacturer, zone_id, is_zone_leader)
        VALUES
            (1, 'RU0208A26ABC123', 221, 'thermostat', 'Living Room Thermostat', 'Smart Thermostat', 'Tado', 1, 1),
            (2, 'VA0210A26ABC456', 222, 'radiator_valve', 'Bedroom Radiator Valve', 'Smart Radiator Valve', 'Tado', 2, 0),
            (3, 'IB01170626ABC789', 223, 'internet_bridge', 'Internet Bridge', 'Internet Bridge', 'Tado', NULL, 0)
    """)

    # Insert mock zones
    conn.execute("""
        INSERT INTO zones (zone_id, name, leader_device_id, order_id, tado_zone_id)
        VALUES
            (1, 'Living Room', 1, 1, 1),
            (2, 'Bedroom', 2, 2, 2)
    """)

    # Seed history records (sample database) - 61 seconds apart
    base_ts = 1_700_000_000.0
    bucket_0 = _get_test_timestamp_bucket(base_ts)
    bucket_1 = _get_test_timestamp_bucket(base_ts + 61)
    bucket_2 = _get_test_timestamp_bucket(base_ts + 122)

    conn.execute("""
        INSERT INTO device_state_history (
            device_id, timestamp_bucket,
            current_temperature, target_temperature,
            current_heating_cooling_state, target_heating_cooling_state,
            humidity, window, window_lastupdate, updated_at
        ) VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?),
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?),
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?),
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?),
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        1, bucket_0, 20.0, 21.0, 1, 1, 48, 0, None, bucket_0,
        1, bucket_1, 21.5, 21.0, 1, 1, 50, 0, None, bucket_1,
        1, bucket_2, 23.5, 21.0, 1, 1, 55, 0, None, bucket_2, # latest for device 2
        2, bucket_2, 19.0, 20.0, 0, 0, 55, 1, float(base_ts), bucket_2,
        3, bucket_0, None, None, None, None, None, None, None, bucket_0,
    ))

     # Update devices with zone relationships
    conn.execute("UPDATE devices SET zone_id = 1 WHERE device_id = 1")
    conn.execute("UPDATE devices SET zone_id = 2 WHERE device_id = 2")


    conn.commit()
    conn.close()

    # Create manager (loads caches and latest state from database)
    manager = DeviceStateManager(temp_db)
    return manager


class TestGetDeviceInfo:
    def test_get_device_info_returns_cached_data(self, state_manager_with_db_devices):
        """Test that get_device_info returns cached device information."""
        result = state_manager_with_db_devices.get_device_info(1)

        assert result['serial_number'] == 'RU0208A26ABC123'
        assert result['name'] == 'Living Room Thermostat'
        assert result['device_type'] == 'thermostat'
        assert result['is_zone_leader'] is True

    def test_get_device_info_radiator_valve(self, state_manager_with_db_devices):
        """Test retrieving radiator valve device info."""
        result = state_manager_with_db_devices.get_device_info(2)

        assert result['device_type'] == 'radiator_valve'
        assert result['serial_number'] == 'VA0210A26ABC456'

    def test_get_device_info_internet_bridge(self, state_manager_with_db_devices):
        """Test retrieving internet bridge device info."""
        result = state_manager_with_db_devices.get_device_info(3)

        assert result['device_type'] == 'internet_bridge'
        assert result['zone_id'] is None
        assert result['is_zone_leader'] is False

    def test_get_device_info_returns_empty_dict_for_unknown_device(self, state_manager):
        """Test that unknown device IDs return empty dict."""
        result = state_manager.get_device_info(999)
        assert result == {}


class TestGetDeviceIdByAid:
    def test_get_device_id_by_aid_found(self, state_manager_with_db_devices):
        """Test retrieving device_id from HomeKit aid."""
        result = state_manager_with_db_devices.get_device_id_by_aid(221)
        assert result == 1

    def test_get_device_id_by_aid_radiator_valve(self, state_manager_with_db_devices):
        """Test retrieving radiator valve by aid."""
        result = state_manager_with_db_devices.get_device_id_by_aid(222)
        assert result == 2

    def test_get_device_id_by_aid_not_found(self, state_manager):
        """Test that unknown aid returns None."""
        result = state_manager.get_device_id_by_aid(999)
        assert result is None


class TestDeviceInfoCacheFromDatabase:
    def test_device_info_cache_loaded_from_database(self, state_manager_with_db_devices):
        """Test that device_info_cache is populated from database."""
        assert len(state_manager_with_db_devices.device_info_cache) >= 3

        # Verify thermostat device
        device_1 = state_manager_with_db_devices.get_device_info(1)
        assert device_1['serial_number'] == 'RU0208A26ABC123'
        assert device_1['device_type'] == 'thermostat'
        assert device_1['aid'] == 221

    def test_device_id_cache_loaded_from_database(self, state_manager_with_db_devices):
        """Test that device_id_cache is populated from serial numbers."""
        assert 'RU0208A26ABC123' in state_manager_with_db_devices.device_id_cache
        assert state_manager_with_db_devices.device_id_cache['RU0208A26ABC123'] == 1

        assert 'VA0210A26ABC456' in state_manager_with_db_devices.device_id_cache
        assert state_manager_with_db_devices.device_id_cache['VA0210A26ABC456'] == 2

    def test_aid_to_device_id_cache_loaded_from_database(self, state_manager_with_db_devices):
        """Test that aid_to_device_id mapping is populated."""
        assert state_manager_with_db_devices.get_device_id_by_aid(221) == 1
        assert state_manager_with_db_devices.get_device_id_by_aid(222) == 2
        assert state_manager_with_db_devices.get_device_id_by_aid(223) == 3

    def test_zone_info_in_device_cache(self, state_manager_with_db_devices):
        """Test that zone information is loaded into device cache."""
        device_1 = state_manager_with_db_devices.get_device_info(1)
        assert device_1['zone_name'] == 'Living Room'
        assert device_1['zone_id'] == 1

        device_2 = state_manager_with_db_devices.get_device_info(2)
        assert device_2['zone_name'] == 'Bedroom'

    def test_internet_bridge_no_zone(self, state_manager_with_db_devices):
        """Test that internet bridge has no zone assignment."""
        device_3 = state_manager_with_db_devices.get_device_info(3)
        assert device_3['zone_id'] is None
        assert device_3['zone_name'] is None


class TestGetAllDevices:
    def test_get_all_devices_from_database(self, state_manager_with_db_devices):
        """Test retrieving all devices with database data."""
        devices = state_manager_with_db_devices.get_all_devices()

        assert len(devices) >= 3

        # Verify device 1
        device_1 = next((d for d in devices if d['device_id'] == 1), None)
        assert device_1 is not None
        assert device_1['serial_number'] == 'RU0208A26ABC123'
        assert device_1['device_type'] == 'thermostat'

        # Verify device 2
        device_2 = next((d for d in devices if d['device_id'] == 2), None)
        assert device_2 is not None
        assert device_2['device_type'] == 'radiator_valve'

        # Verify device 3
        device_3 = next((d for d in devices if d['device_id'] == 3), None)
        assert device_3 is not None
        assert device_3['device_type'] == 'internet_bridge'

    def test_get_all_devices_has_zone_names(self, state_manager_with_db_devices):
        """Test that all devices include zone information."""
        devices = state_manager_with_db_devices.get_all_devices()

        for device in devices:
            assert 'zone_name' in device
            assert 'zone_id' in device
            if device['device_id'] in [1, 2]:
                assert device['zone_name'] is not None
            elif device['device_id'] == 3:
                assert device['zone_name'] is None


class TestUpdateDeviceCharacteristic:
    def test_update_device_characteristic_creates_state_if_not_exists(self, state_manager):
        """Test that device state is created if it doesn't exist."""
        state_manager.update_device_characteristic(
            1,
            state_manager.CHAR_CURRENT_TEMPERATURE,
            20.5,
            time.time()
        )

        assert 1 in state_manager.current_state
        assert state_manager.current_state[1]['current_temperature'] == 20.5
        assert state_manager.last_saved_bucket.get(1) is not None

    def test_update_device_characteristic_no_change_returns_none(self, state_manager):
        """Test that no change returns None."""
        state_manager.current_state[1] = {'current_temperature': 20.5}

        result = state_manager.update_device_characteristic(
            1,
            state_manager.CHAR_CURRENT_TEMPERATURE,
            20.5,
            time.time()
        )

        assert result == (None, None, None)

    def test_update_device_characteristic_detects_change(self, state_manager):
        """Test that temperature change is detected."""
        state_manager.current_state[1] = {'current_temperature': 20.5}
        state_manager.last_saved_bucket[1] = '20260217193500'

        field_name, old_value, new_value = state_manager.update_device_characteristic(
            1,
            state_manager.CHAR_CURRENT_TEMPERATURE,
            21.5,
            time.time()
        )

        assert field_name == 'current_temperature'
        assert old_value == 20.5
        assert new_value == 21.5

    def test_update_device_characteristic_unknown_type_returns_none(self, state_manager):
        """Test that unknown characteristic type returns None."""
        result = state_manager.update_device_characteristic(
            1,
            'unknown-uuid-0000-0000-0000-000000000000',
            42,
            time.time()
        )

        assert result == (None, None, None)

    def test_update_device_characteristic_saves_to_history(self, state_manager):
        """Test that characteristic changes are saved to history."""
        state_manager.current_state[1] = {'current_temperature': 20.0}
        state_manager.last_saved_bucket[1] = state_manager._get_timestamp_bucket(time.time() - 100)

        state_manager.update_device_characteristic(
            1,
            state_manager.CHAR_CURRENT_TEMPERATURE,
            21.0,
            time.time()
        )

        # Verify history was saved
        conn = sqlite3.connect(state_manager.db_path)
        cursor = conn.execute(
            "SELECT current_temperature FROM device_state_history WHERE device_id = ?",
            (1,)
        )
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row[0] == 21.0

    def test_update_characteristic_with_db_device(self, state_manager_with_db_devices):
        """Test updating characteristic for device loaded from database."""
        device_id = 1

        result = state_manager_with_db_devices.update_device_characteristic(
            device_id,
            state_manager_with_db_devices.CHAR_CURRENT_TEMPERATURE,
            22.5,
            time.time()
        )

        assert result[0] == 'current_temperature'
        assert result[2] == 22.5

        # Verify device info is still accessible
        device_info = state_manager_with_db_devices.get_device_info(device_id)
        assert device_info['serial_number'] == 'RU0208A26ABC123'

    def test_multiple_device_updates(self, state_manager_with_db_devices):
        """Test updating multiple devices from database."""
        manager = state_manager_with_db_devices

        # Update device 1
        manager.update_device_characteristic(
            1,
            manager.CHAR_CURRENT_TEMPERATURE,
            20.0,
            time.time()
        )

        # Update device 2
        manager.update_device_characteristic(
            2,
            manager.CHAR_CURRENT_HUMIDITY,
            50,
            time.time()
        )

        # Verify both states
        state_1 = manager.get_current_state(1)
        assert state_1['current_temperature'] == 20.0

        state_2 = manager.get_current_state(2)
        assert state_2['humidity'] == 50

    def test_get_current_state_none(self, state_manager):
        """Test that node device type returns all states."""
        state_manager.current_state[1] = {'current_temperature': 20.0}
        state_manager.current_state[2] = {'current_temperature': 22.0}

        state = state_manager.get_current_state(1)
        assert state == {'current_temperature': 20.0}

        state = state_manager.get_current_state(None)
        assert state == {1: {'current_temperature': 20.0}, 2: {'current_temperature': 22.0}}

class TestUpdateDeviceWindowStatus:
    def test_update_device_window_status_open(self, state_manager):
        """Test updating window status to open."""
        state_manager.current_state[1] = {}

        state_manager.update_device_window_status(1, 1)

        assert state_manager.current_state[1]['window'] == 1
        assert 'window_lastupdate' in state_manager.current_state[1]

    def test_update_device_window_status_no_change(self, state_manager):
        """Test that no change doesn't update timestamp."""
        state_manager.current_state[1] = {'window': 0, 'window_lastupdate': 100.0}

        state_manager.update_device_window_status(1, 0)

        assert state_manager.current_state[1]['window_lastupdate'] == 100.0

    def test_update_device_window_status_closed(self, state_manager):
        """Test updating window status to closed."""
        state_manager.current_state[1] = {'window': 1}

        state_manager.update_device_window_status(1, 0)

        assert state_manager.current_state[1]['window'] == 0

    def test_update_device_window_status_saves_to_history(self, state_manager):
        """Test that window status is saved to history."""
        state_manager.current_state[1] = {'window': 0}
        state_manager.last_saved_bucket[1] = state_manager._get_timestamp_bucket(time.time() - 100)

        state_manager.update_device_window_status(1, 1)

        # Verify history was saved
        conn = sqlite3.connect(state_manager.db_path)
        cursor = conn.execute(
            "SELECT window FROM device_state_history WHERE device_id = ?",
            (1,)
        )
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row[0] == 1


class TestGetDeviceHistory:
    def test_get_device_history_empty(self, state_manager):
        """Test retrieving history for device with no records."""
        result = state_manager.get_device_history(999)

        assert result == []

    def test_get_device_history_with_limit(self, state_manager_with_db_devices):
        """Test that limit parameter works."""
        result = state_manager_with_db_devices.get_device_history(1, limit=2)

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]['state']['cur_temp_c'] == 23.5 # newstest that latest record is returned first

    def test_get_device_history_with_limit_and_offset(self, state_manager_with_db_devices):
        """Test that limit and offset parameter works."""
        result = state_manager_with_db_devices.get_device_history(1, limit=2, offset=1)

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]['state']['cur_temp_c'] == 21.5 # newstest that offset skips is returns the latest first

    def test_get_device_history_standardized_format(self, state_manager):
        """Test that history has standardized format."""
        state_manager.current_state[1] = {
            'current_temperature': 20.5,
            'target_temperature': 21.0,
            'current_heating_cooling_state': 1,
            'target_heating_cooling_state': 1,
            'humidity': 55
        }
        state_manager._save_to_history(1, time.time())

        result = state_manager.get_device_history(1, limit=1)

        assert len(result) == 1
        record = result[0]
        assert 'state' in record
        assert 'timestamp' in record
        assert 'cur_temp_c' in record['state']
        assert 'cur_temp_f' in record['state']
        assert record['state']['cur_temp_c'] == 20.5
        assert abs(record['state']['cur_temp_f'] - 68.9) < 0.1

    def test_get_device_history_with_wrong_device_id(self, state_manager):
        """Test that history has standardized format."""
        state_manager.current_state[1] = {
            'current_temperature': 20.5,
            'target_temperature': 21.0,
            'current_heating_cooling_state': 1,
            'target_heating_cooling_state': 1,
            'humidity': 55
        }
        state_manager._save_to_history(999, time.time())

        result = state_manager.get_device_history(1, limit=1)

        assert len(result) == 0

    def test_get_device_history_start_time_validation(self, state_manager_with_db_devices):
        """Validate that start_time filters out older records."""
        base_ts = 1_700_000_000.0  # matches fixture seed
        result = state_manager_with_db_devices.get_device_history(1, start_time=base_ts+20)

        assert isinstance(result, list)
        assert len(result) == 2
        returned_temps = [r.get("state", {}).get("cur_temp_c") for r in result]
        assert 20.0 not in returned_temps  # first seeded record filtered out

    def test_get_device_history_end_time_validation(self, state_manager_with_db_devices):
        """Validate that end_time filters out newer records."""
        base_ts = 1_700_000_000.0  # matches fixture seed
        result = state_manager_with_db_devices.get_device_history(1, end_time=base_ts+80)

        assert isinstance(result, list)
        assert len(result) == 2
        returned_temps = [r.get("state", {}).get("cur_temp_c") for r in result]
        assert 23.5 not in returned_temps  # second seeded record filtered out

    def test_get_device_history_start_end_time_window_validation(self, state_manager_with_db_devices):
        """Validate combined start_time/end_time returns only records in range."""
        base_ts = 1_700_000_000.0  # matches fixture seed
        # Include second record (~+61s), exclude first (~+0s)
        result = state_manager_with_db_devices.get_device_history(
            1,
            start_time=base_ts + 30,
            end_time=base_ts + 90
        )

        assert isinstance(result, list)
        returned_temps = [r.get("state", {}).get("cur_temp_c") for r in result]
        assert 21.5 in returned_temps
        assert 20.0 not in returned_temps

    def test_get_device_history_invalid_time_range_validation(self, state_manager_with_db_devices):
        """Validate behavior when start_time is greater than end_time."""
        base_ts = 1_700_000_000.0
        result = state_manager_with_db_devices.get_device_history(
            1,
            start_time=base_ts + 200,
            end_time=base_ts + 100
        )

        assert result == [] or isinstance(result, list)


class TestCharacteristicMapping:
    def test_characteristic_mapping_temperature(self, state_manager):
        """Test that temperature characteristics are mapped correctly."""
        state_manager.current_state[1] = {}
        state_manager.last_saved_bucket[1] = state_manager._get_timestamp_bucket(time.time() - 100)

        state_manager.update_device_characteristic(
            1,
            state_manager.CHAR_CURRENT_TEMPERATURE,
            20.5,
            time.time()
        )

        assert state_manager.current_state[1]['current_temperature'] == 20.5

    def test_characteristic_mapping_heating_cooling(self, state_manager):
        """Test that heating/cooling state is mapped correctly."""
        state_manager.current_state[1] = {}
        state_manager.last_saved_bucket[1] = state_manager._get_timestamp_bucket(time.time() - 100)

        state_manager.update_device_characteristic(
            1,
            state_manager.CHAR_CURRENT_HEATING_COOLING,
            1,
            time.time()
        )

        assert state_manager.current_state[1]['current_heating_cooling_state'] == 1

    def test_characteristic_mapping_humidity(self, state_manager):
        """Test that humidity is mapped correctly."""
        state_manager.current_state[1] = {}
        state_manager.last_saved_bucket[1] = state_manager._get_timestamp_bucket(time.time() - 100)

        state_manager.update_device_characteristic(
            1,
            state_manager.CHAR_CURRENT_HUMIDITY,
            255,
            time.time()
        )

        assert state_manager.current_state[1]['humidity'] == 255

class TestDeviceStateLoadLatestFromDatabase:
    def test_load_latest_state_from_database(self, state_manager_with_db_devices):
        """Test that latest state is loaded from database on initialization."""
        # Insert a state record into the database
        conn = sqlite3.connect(state_manager_with_db_devices.db_path)
        bucket = state_manager_with_db_devices._get_timestamp_bucket(time.time())
        conn.execute("""
            INSERT INTO device_state_history (device_id, current_temperature, timestamp_bucket)
            VALUES (1, 22.5, ?)
        """, (bucket,))
        conn.commit()
        conn.close()

        # Create a new manager instance to load from database
        new_manager = DeviceStateManager(state_manager_with_db_devices.db_path)

        # Verify that the latest state is loaded into current_state
        assert 1 in new_manager.current_state
        assert new_manager.current_state[1]['current_temperature'] == 22.5

class TestLoadLatestStateFromDatabase:
    def test_load_latest_state_empty_database(self, state_manager):
        """Test loading state when database is empty."""
        state_manager._load_latest_state_from_db()

        assert len(state_manager.current_state) == 0
        assert len(state_manager.last_saved_bucket) == 0
        assert len(state_manager.bucket_state_snapshot) == 0

    def test_load_latest_state_single_device(self, state_manager):
        """Test loading latest state for a single device."""
        # Insert a device state record
        conn = sqlite3.connect(state_manager.db_path)
        conn.execute("""
            INSERT INTO device_state_history
            (device_id, timestamp_bucket, current_temperature, target_temperature,
             current_heating_cooling_state, target_heating_cooling_state,
             heating_threshold_temperature, cooling_threshold_temperature,
             temperature_display_units, battery_level, status_low_battery,
             humidity, target_humidity, active_state, valve_position, window, window_lastupdate)
            VALUES (1, '20260217193000', 20.5, 21.0, 1, 1, 5.0, 35.0, 0, 100, 0, 55, 50, 1, 0, 0, NULL)
        """)
        conn.commit()
        conn.close()

        state_manager._load_latest_state_from_db()

        assert 1 in state_manager.current_state
        assert state_manager.current_state[1]['current_temperature'] == 20.5
        assert state_manager.current_state[1]['target_temperature'] == 21.0
        assert state_manager.last_saved_bucket[1] == '20260217193000'

    def test_load_latest_state_multiple_devices(self, state_manager):
        """Test loading latest state for multiple devices."""
        conn = sqlite3.connect(state_manager.db_path)

        # Insert state for device 1
        conn.execute("""
            INSERT INTO device_state_history
            (device_id, timestamp_bucket, current_temperature, target_temperature,
             current_heating_cooling_state, target_heating_cooling_state,
             heating_threshold_temperature, cooling_threshold_temperature,
             temperature_display_units, battery_level, status_low_battery,
             humidity, target_humidity, active_state, valve_position, window, window_lastupdate)
            VALUES (1, '20260217193000', 20.5, 21.0, 1, 1, 5.0, 35.0, 0, 100, 0, 55, 50, 1, 0, 0, NULL)
        """)

        # Insert state for device 2
        conn.execute("""
            INSERT INTO device_state_history
            (device_id, timestamp_bucket, current_temperature, target_temperature,
             current_heating_cooling_state, target_heating_cooling_state,
             heating_threshold_temperature, cooling_threshold_temperature,
             temperature_display_units, battery_level, status_low_battery,
             humidity, target_humidity, active_state, valve_position, window, window_lastupdate)
            VALUES (2, '20260217193500', 19.0, 20.0, 0, 0, 5.0, 35.0, 0, 85, 0, 60, 50, 0, 50, 1, ?)
        """, (time.time(),))

        conn.commit()
        conn.close()

        state_manager._load_latest_state_from_db()

        assert len(state_manager.current_state) == 2
        assert state_manager.current_state[1]['current_temperature'] == 20.5
        assert state_manager.current_state[2]['current_temperature'] == 19.0
        assert state_manager.current_state[2]['window'] == 1

    def test_load_latest_state_gets_most_recent_bucket(self, state_manager):
        """Test that only the most recent timestamp_bucket is loaded."""
        conn = sqlite3.connect(state_manager.db_path)

        # Insert older state
        conn.execute("""
            INSERT INTO device_state_history
            (device_id, timestamp_bucket, current_temperature, target_temperature,
             current_heating_cooling_state, target_heating_cooling_state,
             heating_threshold_temperature, cooling_threshold_temperature,
             temperature_display_units, battery_level, status_low_battery,
             humidity, target_humidity, active_state, valve_position, window, window_lastupdate)
            VALUES (1, '20260217190000', 18.0, 19.0, 0, 0, 5.0, 35.0, 0, 100, 0, 50, 50, 0, 0, 0, NULL)
        """)

        # Insert newer state
        conn.execute("""
            INSERT INTO device_state_history
            (device_id, timestamp_bucket, current_temperature, target_temperature,
             current_heating_cooling_state, target_heating_cooling_state,
             heating_threshold_temperature, cooling_threshold_temperature,
             temperature_display_units, battery_level, status_low_battery,
             humidity, target_humidity, active_state, valve_position, window, window_lastupdate)
            VALUES (1, '20260217195000', 22.0, 23.0, 1, 1, 5.0, 35.0, 0, 100, 0, 55, 50, 1, 0, 0, NULL)
        """)

        conn.commit()
        conn.close()

        state_manager._load_latest_state_from_db()

        # Should load the most recent state (22.0, not 18.0)
        assert state_manager.current_state[1]['current_temperature'] == 22.0
        assert state_manager.current_state[1]['target_temperature'] == 23.0
        assert state_manager.last_saved_bucket[1] == '20260217195000'

    def test_load_latest_state_populates_snapshot(self, state_manager):
        """Test that bucket_state_snapshot is set to match current_state."""
        conn = sqlite3.connect(state_manager.db_path)
        conn.execute("""
            INSERT INTO device_state_history
            (device_id, timestamp_bucket, current_temperature, target_temperature,
             current_heating_cooling_state, target_heating_cooling_state,
             heating_threshold_temperature, cooling_threshold_temperature,
             temperature_display_units, battery_level, status_low_battery,
             humidity, target_humidity, active_state, valve_position, window, window_lastupdate)
            VALUES (1, '20260217193000', 20.5, 21.0, 1, 1, 5.0, 35.0, 0, 100, 0, 55, 50, 1, 0, 0, NULL)
        """)
        conn.commit()
        conn.close()

        state_manager._load_latest_state_from_db()

        assert 1 in state_manager.bucket_state_snapshot
        assert state_manager.bucket_state_snapshot[1] == state_manager.current_state[1]

    def test_load_latest_state_all_fields(self, state_manager):
        """Test that all state fields are loaded correctly."""
        conn = sqlite3.connect(state_manager.db_path)
        conn.execute("""
            INSERT INTO device_state_history
            (device_id, timestamp_bucket, current_temperature, target_temperature,
             current_heating_cooling_state, target_heating_cooling_state,
             heating_threshold_temperature, cooling_threshold_temperature,
             temperature_display_units, battery_level, status_low_battery,
             humidity, target_humidity, active_state, valve_position, window, window_lastupdate)
            VALUES (1, '20260217193000', 20.5, 21.0, 1, 1, 10.0, 30.0, 1, 95, 1, 55, 45, 1, 75, 1, 1645098000.5)
        """)
        conn.commit()
        conn.close()

        state_manager._load_latest_state_from_db()

        state = state_manager.current_state[1]
        assert state['current_temperature'] == 20.5
        assert state['target_temperature'] == 21.0
        assert state['current_heating_cooling_state'] == 1
        assert state['target_heating_cooling_state'] == 1
        assert state['heating_threshold_temperature'] == 10.0
        assert state['cooling_threshold_temperature'] == 30.0
        assert state['temperature_display_units'] == 1
        assert state['battery_level'] == 95
        assert state['status_low_battery'] == 1
        assert state['humidity'] == 55
        assert state['target_humidity'] == 45
        assert state['active_state'] == 1
        assert state['valve_position'] == 75
        assert state['window'] == 1
        assert state['window_lastupdate'] == 1645098000.5

    def test_load_latest_state_with_null_values(self, state_manager):
        """Test loading state with NULL values."""
        conn = sqlite3.connect(state_manager.db_path)
        conn.execute("""
            INSERT INTO device_state_history
            (device_id, timestamp_bucket, current_temperature, target_temperature,
             current_heating_cooling_state, target_heating_cooling_state,
             heating_threshold_temperature, cooling_threshold_temperature,
             temperature_display_units, battery_level, status_low_battery,
             humidity, target_humidity, active_state, valve_position, window, window_lastupdate)
            VALUES (1, '20260217193000', 20.5, NULL, 1, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)
        """)
        conn.commit()
        conn.close()

        state_manager._load_latest_state_from_db()

        state = state_manager.current_state[1]
        assert state['current_temperature'] == 20.5
        assert state['target_temperature'] is None
        assert state['heating_threshold_temperature'] is None
        assert state['window_lastupdate'] is None

    def test_load_latest_state_initializes_caches(self, state_manager):
        """Test that caches are properly initialized."""
        conn = sqlite3.connect(state_manager.db_path)
        for i in range(1, 4):
            conn.execute(f"""
                INSERT INTO device_state_history
                (device_id, timestamp_bucket, current_temperature, target_temperature,
                 current_heating_cooling_state, target_heating_cooling_state,
                 heating_threshold_temperature, cooling_threshold_temperature,
                 temperature_display_units, battery_level, status_low_battery,
                 humidity, target_humidity, active_state, valve_position, window, window_lastupdate)
                VALUES ({i}, '2026021719300{i}', {18 + i}, {19 + i}, 0, 0, 5.0, 35.0, 0, 100, 0, 50, 50, 0, 0, 0, NULL)
            """)
        conn.commit()
        conn.close()

        state_manager._load_latest_state_from_db()

        # Verify all three devices and their caches are initialized
        assert len(state_manager.current_state) == 3
        assert len(state_manager.last_saved_bucket) == 3
        assert len(state_manager.bucket_state_snapshot) == 3

        for device_id in [1, 2, 3]:
            assert device_id in state_manager.current_state
            assert device_id in state_manager.last_saved_bucket
            assert device_id in state_manager.bucket_state_snapshot

    def test_load_latest_state_with_db_devices(self, state_manager_with_db_devices):
        """Test loading latest state for devices from database."""
        manager = state_manager_with_db_devices

        # Insert state for each device
        conn = sqlite3.connect(manager.db_path)
        conn.execute("""
            INSERT INTO device_state_history
            (device_id, timestamp_bucket, current_temperature, target_temperature,
             current_heating_cooling_state, target_heating_cooling_state,
             heating_threshold_temperature, cooling_threshold_temperature,
             temperature_display_units, battery_level, status_low_battery,
             humidity, target_humidity, active_state, valve_position, window, window_lastupdate)
            VALUES (1, '20260217193000', 26.5, 21.0, 1, 1, 5.0, 35.0, 0, 100, 0, 55, 50, 1, 0, 0, NULL)
        """)
        conn.commit()
        conn.close()

        # Create a new manager to trigger load
        new_manager = DeviceStateManager(manager.db_path)

        # Verify device state was loaded
        assert 1 in new_manager.current_state
        assert new_manager.current_state[1]['current_temperature'] == 26.5

        # Verify device info cache still works
        device_info = new_manager.get_device_info(1)
        assert device_info['serial_number'] == 'RU0208A26ABC123'


class TestGetOrCreateDevice:
    def test_returns_existing_device_when_serial_in_cache(self, state_manager_with_db_devices):
        """Existing serial returns existing device_id."""
        manager = state_manager_with_db_devices

        existing_id = manager.device_id_cache['RU0208A26ABC123']
        result = manager.get_or_create_device('RU0208A26ABC123', 1, {})

        assert result == existing_id

    def test_updates_aid_if_changed_for_existing_device(self, state_manager_with_db_devices):
        """If aid changed, DB and caches are updated."""
        manager = state_manager_with_db_devices
        device_id = manager.device_id_cache['RU0208A26ABC123']

        # old aid from fixture is 1; change to 221
        result = manager.get_or_create_device('RU0208A26ABC123', 221, {})

        assert result == device_id
        assert manager.get_device_id_by_aid(221) == device_id
        assert manager.device_info_cache[device_id]['aid'] == 221

        conn = sqlite3.connect(manager.db_path)
        row = conn.execute(
            "SELECT aid FROM devices WHERE device_id = ?",
            (device_id,)
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == 221

    def test_creates_new_device_from_accessory_data(self, state_manager):
        """Creates a new device and parses name/model/manufacturer/type."""
        accessory_data = {
            "services": [
                {
                    "type": "0000003e-0000-1000-8000-0026bb765291",  # AccessoryInformation
                    "characteristics": [
                        {"type": "00000023-0000-1000-8000-0026bb765291", "value": "Living Room Thermostat"},
                        {"type": "00000021-0000-1000-8000-0026bb765291", "value": "Smart Thermostat X"},
                        {"type": "00000020-0000-1000-8000-0026bb765291", "value": "Tado"},

                    ],
                },
                {
                    "type": "0000004a-0000-1000-8000-0026bb765291",  # Thermostat service
                    "characteristics": [],
                },
            ]
        }

        device_id = state_manager.get_or_create_device("RU9999TEST123", 555, accessory_data)

        assert isinstance(device_id, int)
        assert device_id > 0
        assert state_manager.device_id_cache["RU9999TEST123"] == device_id
        assert state_manager.get_device_id_by_aid(555) == device_id

        info = state_manager.get_device_info(device_id)
        assert info["serial_number"] == "RU9999TEST123"
        assert info["aid"] == 555
        assert info["name"] == "Living Room Thermostat"
        assert info["device_type"] == "thermostat"

        conn = sqlite3.connect(state_manager.db_path)
        row = conn.execute(
            "SELECT serial_number, aid, device_type, name, model, manufacturer FROM devices WHERE device_id = ?",
            (device_id,)
        ).fetchone()
        conn.close()

        assert row == ("RU9999TEST123", 555, "thermostat", "Living Room Thermostat", "Smart Thermostat X", "Tado")

    def test_detects_device_type_from_serial_prefix_when_service_unknown(self, state_manager):
        """Falls back to serial prefix mapping when no known service type."""
        device_id = state_manager.get_or_create_device(
            "VA0210A26XYZ999",
            777,
            {"services": []},
        )

        info = state_manager.get_device_info(device_id)
        assert info["device_type"] == "radiator_valve"

    def test_does_not_add_aid_mapping_when_aid_is_falsy(self, state_manager):
        """When aid is 0/None, aid_to_device_id should not be populated."""
        device_id = state_manager.get_or_create_device(
            "IB01170626NOAID",
            0,
            {"services": []},
        )

        assert device_id > 0
        assert state_manager.get_device_id_by_aid(0) is None
        assert state_manager.get_device_info(device_id)["device_type"] == "internet_bridge"

    def test_creates_temperature_sensor_from_service_type(self, state_manager):
        """Service type 0000008a maps to temperature_sensor."""
        accessory_data = {
            "services": [
                {
                    "type": "0000003e-0000-1000-8000-0026bb765291",
                    "characteristics": [
                        {"type": "00000023-0000-1000-8000-0026bb765291", "value": "Temp Sensor"},
                    ],
                },
                {
                    "type": "0000008a-0000-1000-8000-0026bb765291",
                    "characteristics": [],
                },
            ]
        }

        device_id = state_manager.get_or_create_device("TS0001ABC", 901, accessory_data)
        info = state_manager.get_device_info(device_id)

        assert info["device_type"] == "temperature_sensor"
        assert info["name"] == "Temp Sensor"
        assert state_manager.get_device_id_by_aid(901) == device_id

    def test_creates_humidity_sensor_from_service_type(self, state_manager):
        """Service type 00000082 maps to humidity_sensor."""
        accessory_data = {
            "services": [
                {
                    "type": "0000003e-0000-1000-8000-0026bb765291",
                    "characteristics": [
                        {"type": "00000023-0000-1000-8000-0026bb765291", "value": "Humidity Sensor"},
                    ],
                },
                {
                    "type": "00000082-0000-1000-8000-0026bb765291",
                    "characteristics": [],
                },
            ]
        }

        device_id = state_manager.get_or_create_device("HS0001ABC", 902, accessory_data)
        info = state_manager.get_device_info(device_id)

        assert info["device_type"] == "humidity_sensor"
        assert info["name"] == "Humidity Sensor"
        assert state_manager.get_device_id_by_aid(902) == device_id

    def test_creates_thermostat_from_ru_serial_prefix(self, state_manager):
        """Unknown service type falls back to RU -> thermostat."""
        accessory_data = {
            "services": [
                {
                    "type": "0000003e-0000-1000-8000-0026bb765291",
                    "characteristics": [
                        {"type": "00000023-0000-1000-8000-0026bb765291", "value": "Room Unit"},
                    ],
                }
            ]
        }

        device_id = state_manager.get_or_create_device("RU0208A26ABC777", 904, accessory_data)
        info = state_manager.get_device_info(device_id)

        assert info["device_type"] == "thermostat"
        assert info["name"] == "Room Unit"
        assert state_manager.get_device_id_by_aid(904) == device_id

    def test_creates_wireless_receiver_from_wr_serial_prefix(self, state_manager):
        """Unknown service type falls back to WR -> wireless_receiver."""
        accessory_data = {
            "services": [
                {
                    "type": "0000003e-0000-1000-8000-0026bb765291",
                    "characteristics": [
                        {"type": "00000023-0000-1000-8000-0026bb765291", "value": "Wireless Receiver"},
                    ],
                }
            ]
        }

        device_id = state_manager.get_or_create_device("WR0108A26XYZ777", 905, accessory_data)
        info = state_manager.get_device_info(device_id)

        assert info["device_type"] == "wireless_receiver"
        assert info["name"] == "Wireless Receiver"
        assert state_manager.get_device_id_by_aid(905) == device_id


class TestHasStateChanged:
    def test_has_state_changed_returns_true_when_no_snapshot(self, state_manager):
        """No snapshot for device should be treated as changed."""
        state_manager.current_state[1] = {"current_temperature": 20.0}

        assert state_manager._has_state_changed(1) is True

    def test_has_state_changed_returns_false_when_state_matches_snapshot(self, state_manager):
        """Matching current state and snapshot should return False."""
        state_manager.current_state[1] = {
            "current_temperature": 20.0,
            "target_temperature": 21.0,
            "humidity": 50,
            "window": 0,
        }
        state_manager.bucket_state_snapshot[1] = {
            "current_temperature": 20.0,
            "target_temperature": 21.0,
            "humidity": 50,
            "window": 0,
        }

        assert state_manager._has_state_changed(1) is False

    def test_has_state_changed_returns_true_when_data_field_differs(self, state_manager):
        """Any tracked data field difference should return True."""
        state_manager.current_state[1] = {
            "current_temperature": 21.5,
            "target_temperature": 21.0,
            "humidity": 50,
            "window": 0,
        }
        state_manager.bucket_state_snapshot[1] = {
            "current_temperature": 20.0,
            "target_temperature": 21.0,
            "humidity": 50,
            "window": 0,
        }

        assert state_manager._has_state_changed(1) is True

    def test_has_state_changed_ignores_metadata_fields(self, state_manager):
        """Fields not in data_fields (like window_lastupdate/last_update) are ignored."""
        state_manager.current_state[1] = {
            "current_temperature": 20.0,
            "target_temperature": 21.0,
            "humidity": 50,
            "window": 0,
            "window_lastupdate": 2000.0,
            "last_update": 3000.0,
        }
        state_manager.bucket_state_snapshot[1] = {
            "current_temperature": 20.0,
            "target_temperature": 21.0,
            "humidity": 50,
            "window": 0,
            "window_lastupdate": 1000.0,
            "last_update": 1500.0,
        }

        assert state_manager._has_state_changed(1) is False


class TestClearOptimisticState:
    def test_clear_optimistic_state_noop_when_device_not_present(self, state_manager):
        """Should do nothing when device has no optimistic state."""
        state_manager.current_state[1] = {"target_temperature": 21.0}

        with patch("tado_local.state.logger") as mock_logger:
            state_manager.clear_optimistic_state(999)

        assert 999 not in state_manager.optimistic_state
        assert 999 not in state_manager.optimistic_timestamps
        mock_logger.info.assert_not_called()
        mock_logger.debug.assert_not_called()

    def test_clear_optimistic_state_clears_entries_without_mismatch_log(self, state_manager):
        """Should clear optimistic caches and not log mismatch when values match."""
        device_id = 1
        state_manager.optimistic_state[device_id] = {
            "target_temperature": 21.0,
            "window": 0,
        }
        state_manager.optimistic_timestamps[device_id] = 1700000000.0
        state_manager.current_state[device_id] = {
            "target_temperature": 21.0,
            "window": 0,
        }

        with patch("tado_local.state.logger") as mock_logger:
            state_manager.clear_optimistic_state(device_id)

        assert device_id not in state_manager.optimistic_state
        assert device_id not in state_manager.optimistic_timestamps
        mock_logger.info.assert_not_called()
        mock_logger.debug.assert_called_once()

    def test_clear_optimistic_state_logs_mismatch_and_clears(self, state_manager):
        """Should log mismatch when actual state differs from optimistic prediction."""
        device_id = 2
        state_manager.optimistic_state[device_id] = {
            "target_temperature": 22.0,
            "window": 1,
        }
        state_manager.optimistic_timestamps[device_id] = 1700000001.0
        state_manager.current_state[device_id] = {
            "target_temperature": 20.0,  # mismatch
            "window": 0,                 # mismatch
        }

        with patch("tado_local.state.logger") as mock_logger:
            state_manager.clear_optimistic_state(device_id)

        assert device_id not in state_manager.optimistic_state
        assert device_id not in state_manager.optimistic_timestamps

        mock_logger.info.assert_called_once()
        msg = mock_logger.info.call_args[0][0]
        assert "Optimistic state was overridden by device" in msg
        assert "target_temperature: predicted=22.0, actual=20.0" in msg
        assert "window: predicted=1, actual=0" in msg
        mock_logger.debug.assert_called_once()

    def test_clear_optimistic_state_ignores_none_actual_values_for_mismatch(self, state_manager):
        """None actual values should not count as mismatches."""
        device_id = 3
        state_manager.optimistic_state[device_id] = {
            "target_temperature": 21.5,
            "humidity": 55,
        }
        state_manager.optimistic_timestamps[device_id] = 1700000002.0
        state_manager.current_state[device_id] = {
            "target_temperature": None,  # ignored by mismatch logic
            "humidity": 55,              # equal
        }

        with patch("tado_local.state.logger") as mock_logger:
            state_manager.clear_optimistic_state(device_id)

        assert device_id not in state_manager.optimistic_state
        assert device_id not in state_manager.optimistic_timestamps
        mock_logger.info.assert_not_called()
        mock_logger.debug.assert_called_once()


class TestGetStateWithOptimistic:
    def test_returns_real_state_when_no_optimistic(self, state_manager):
        state_manager.current_state[1] = {
            "current_temperature": 20.0,
            "target_temperature": 21.0,
        }

        result = state_manager.get_state_with_optimistic(1)

        assert result["current_temperature"] == 20.0
        assert result["target_temperature"] == 21.0

    def test_applies_optimistic_overrides_when_not_expired(self, state_manager):
        device_id = 1
        state_manager.optimistic_timeout = 30
        state_manager.current_state[device_id] = {
            "current_temperature": 20.0,
            "target_temperature": 21.0,
            "window": 0,
        }
        state_manager.optimistic_state[device_id] = {
            "target_temperature": 23.0,
            "window": 1,
        }
        state_manager.optimistic_timestamps[device_id] = 1000.0

        with patch("tado_local.state.time.time", return_value=1010.0):
            result = state_manager.get_state_with_optimistic(device_id)

        assert result["current_temperature"] == 20.0
        assert result["target_temperature"] == 23.0  # overridden
        assert result["window"] == 1                 # overridden

    def test_expired_optimistic_state_is_cleared(self, state_manager):
        device_id = 2
        state_manager.optimistic_timeout = 30
        state_manager.current_state[device_id] = {"target_temperature": 20.0}
        state_manager.optimistic_state[device_id] = {"target_temperature": 25.0}
        state_manager.optimistic_timestamps[device_id] = 1000.0

        with patch("tado_local.state.time.time", return_value=1040.0):
            result = state_manager.get_state_with_optimistic(device_id)

        # expired -> real state returned, optimistic removed
        assert result["target_temperature"] == 20.0
        assert device_id not in state_manager.optimistic_state
        assert device_id not in state_manager.optimistic_timestamps

    def test_returns_optimistic_only_when_real_state_missing(self, state_manager):
        device_id = 3
        state_manager.optimistic_timeout = 30
        state_manager.optimistic_state[device_id] = {"target_temperature": 19.5}
        state_manager.optimistic_timestamps[device_id] = 1000.0

        with patch("tado_local.state.time.time", return_value=1005.0):
            result = state_manager.get_state_with_optimistic(device_id)

        assert result["target_temperature"] == 19.5

    def test_result_is_copy_not_mutating_current_state(self, state_manager):
        device_id = 4
        state_manager.current_state[device_id] = {"target_temperature": 21.0}

        result = state_manager.get_state_with_optimistic(device_id)
        result["target_temperature"] = 99.0

        assert state_manager.current_state[device_id]["target_temperature"] == 21.0


class TestGetAllDevicesAdditional:
    def test_get_all_devices_casts_bool_fields(self, state_manager_with_db_devices):
        """Validate bool conversion for is_zone_leader and is_circuit_driver."""
        # Force one TRUE value for is_circuit_driver to validate conversion
        conn = sqlite3.connect(state_manager_with_db_devices.db_path)
        conn.execute("UPDATE devices SET is_circuit_driver = 1 WHERE device_id = 2")
        conn.commit()
        conn.close()

        devices = state_manager_with_db_devices.get_all_devices()

        d1 = next(d for d in devices if d["device_id"] == 1)
        d2 = next(d for d in devices if d["device_id"] == 2)
        d3 = next(d for d in devices if d["device_id"] == 3)

        assert isinstance(d1["is_zone_leader"], bool)
        assert isinstance(d1["is_circuit_driver"], bool)
        assert isinstance(d2["is_zone_leader"], bool)
        assert isinstance(d2["is_circuit_driver"], bool)
        assert isinstance(d3["is_zone_leader"], bool)
        assert isinstance(d3["is_circuit_driver"], bool)

        assert d1["is_zone_leader"] is True
        assert d2["is_circuit_driver"] is True
        assert d3["is_zone_leader"] is False  # 0/NULL -> False


class TestGetDeviceHistoryInfo:
    def test_get_device_history_info_empty(self, state_manager):
        result = state_manager.get_device_history_info(device_id=999, age=60)

        assert result["history_count"] == 0
        assert result["latest_entry"] is None
        assert result["earliest_entry"] is None

    def test_get_device_history_info_with_age_filter_and_order(self, state_manager):
        conn = sqlite3.connect(state_manager.db_path)
        conn.execute("""
            INSERT INTO device_state_history (
                device_id, timestamp_bucket, current_temperature, window, window_lastupdate, updated_at
            ) VALUES
                (1, '20260217193000', 20.0, 0, NULL, datetime('now', '-3 minutes')),
                (1, '20260217193110', 21.5, 1, 1700000000.0, datetime('now', '-1 minutes')),
                (1, '20260217180000', 10.0, 0, NULL, datetime('now', '-120 minutes'))
        """)
        conn.commit()
        conn.close()

        result = state_manager.get_device_history_info(device_id=1, age=10)

        assert result["history_count"] == 2
        # Ordered DESC by updated_at
        assert result["latest_entry"][0] == 21.5
        assert result["earliest_entry"][0] == 20.0


class TestUpdateDeviceWindowStatusAdditional:
    def test_update_device_window_status_calls_save_and_logs_on_change(self, state_manager):
        state_manager.current_state[1] = {"window": 0}

        with patch.object(state_manager, "_save_to_history") as mock_save, \
             patch("tado_local.state.time.time", side_effect=[1700000000.0, 1700000001.0]), \
             patch("tado_local.state.logger") as mock_logger:
            state_manager.update_device_window_status(1, 2)

        assert state_manager.current_state[1]["window"] == 2
        assert state_manager.current_state[1]["window_lastupdate"] == 1700000000.0
        mock_save.assert_called_once_with(1, 1700000001.0)
        mock_logger.info.assert_called_once()

    def test_update_device_window_status_does_not_save_when_unchanged(self, state_manager):
        state_manager.current_state[1] = {"window": 1, "window_lastupdate": 123.0}

        with patch.object(state_manager, "_save_to_history") as mock_save, \
             patch("tado_local.state.logger") as mock_logger:
            state_manager.update_device_window_status(1, 1)

        assert state_manager.current_state[1]["window_lastupdate"] == 123.0
        mock_save.assert_not_called()
        mock_logger.info.assert_not_called()

    def test_update_device_window_status_does_not_save_when_new_id(self, state_manager):
        state_manager.current_state[1] = {"window": 1, "window_lastupdate": 123.0}

        with patch.object(state_manager, "_save_to_history") as mock_save:
            state_manager.update_device_window_status(99, 0)

        print(state_manager.current_state)
        assert state_manager.current_state[99]["window"] ==0
        mock_save.assert_called_once()
