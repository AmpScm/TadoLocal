import pytest
import sqlite3
import tempfile
import os
from tado_local.cache import CharacteristicCacheSQLite

@pytest.fixture
def temp_db():
    """Create a temporary database file for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)

@pytest.fixture
def cache(temp_db):
    """Create a CharacteristicCacheSQLite instance for testing."""
    return CharacteristicCacheSQLite(temp_db)


class TestCharacteristicCacheSQLite:
    def test_init_creates_database(self, temp_db):
        """Test that initialization creates the database."""
        cache = CharacteristicCacheSQLite(temp_db)
        assert os.path.exists(temp_db)
        assert cache.db_path == temp_db

    def test_storage_data_accessible(self, cache):
        """Test that storage_data dict is accessible."""
        assert hasattr(cache, 'storage_data')
        assert isinstance(cache.storage_data, dict)

    def test_async_create_or_update_map(self, cache, temp_db):
        """Test creating or updating a pairing cache."""
        homekit_id = "test_home_1"
        config_num = 42
        accessories = [{"aid": 1, "services": []}]
        broadcast_key = "key123"
        state_num = 1

        result = cache.async_create_or_update_map(
            homekit_id, config_num, accessories, broadcast_key, state_num
        )

        assert result is not None
        assert homekit_id in cache.storage_data

    def test_async_create_or_update_map_persists_to_db(self, cache, temp_db):
        """Test that data is persisted to database."""
        homekit_id = "test_home_2"
        config_num = 50
        accessories = [{"aid": 1, "services": []}]
        broadcast_key = "key456"

        cache.async_create_or_update_map(homekit_id, config_num, accessories, broadcast_key)

        conn = sqlite3.connect(temp_db)
        cursor = conn.execute(
            "SELECT homekit_id, config_num FROM homekit_cache WHERE homekit_id = ?",
            (homekit_id,)
        )
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row[0] == homekit_id
        assert row[1] == config_num

    def test_async_delete_map(self, cache, temp_db):
        """Test deleting a pairing cache."""
        homekit_id = "test_home_3"
        accessories = [{"aid": 1}]

        cache.async_create_or_update_map(homekit_id, 1, accessories)
        assert homekit_id in cache.storage_data

        cache.async_delete_map(homekit_id)
        assert homekit_id not in cache.storage_data

    def test_async_delete_map_removes_from_db(self, cache, temp_db):
        """Test that deletion removes data from database."""
        homekit_id = "test_home_4"
        cache.async_create_or_update_map(homekit_id, 1, [{"aid": 1}])

        cache.async_delete_map(homekit_id)

        conn = sqlite3.connect(temp_db)
        cursor = conn.execute(
            "SELECT * FROM homekit_cache WHERE homekit_id = ?",
            (homekit_id,)
        )
        assert cursor.fetchone() is None
        conn.close()

    def test_load_from_db_on_init(self, temp_db):
        """Test that data is loaded from database on initialization."""
        cache1 = CharacteristicCacheSQLite(temp_db)
        homekit_id = "test_home_5"
        accessories = [{"aid": 1, "services": []}]
        cache1.async_create_or_update_map(homekit_id, 10, accessories)

        cache2 = CharacteristicCacheSQLite(temp_db)
        assert homekit_id in cache2.storage_data

    def test_save_to_db_with_none_values(self, cache, temp_db):
        """Test saving to database with optional None values."""
        homekit_id = "test_home_6"
        cache.async_create_or_update_map(homekit_id, 5, [{"aid": 1}], None, None)

        conn = sqlite3.connect(temp_db)
        cursor = conn.execute(
            "SELECT broadcast_key, state_num FROM homekit_cache WHERE homekit_id = ?",
            (homekit_id,)
        )
        row = cursor.fetchone()
        conn.close()

        assert row[0] is None
        assert row[1] is None

    def test_accessories_json_serialization(self, cache, temp_db):
        """Test that accessories are properly serialized/deserialized."""
        homekit_id = "test_home_7"
        accessories = [
            {"aid": 1, "services": [{"iid": 1, "type": "service_type"}]},
            {"aid": 2, "services": []}
        ]
        cache.async_create_or_update_map(homekit_id, 1, accessories)

        loaded_data = cache.storage_data[homekit_id]
        assert loaded_data['accessories'] == accessories

    def test_update_existing_cache_entry(self, cache, temp_db):
        """Test updating an existing cache entry."""
        homekit_id = "test_home_8"
        cache.async_create_or_update_map(homekit_id, 1, [{"aid": 1}])
        cache.async_create_or_update_map(homekit_id, 2, [{"aid": 2}])

        conn = sqlite3.connect(temp_db)
        cursor = conn.execute(
            "SELECT COUNT(*) FROM homekit_cache WHERE homekit_id = ?",
            (homekit_id,)
        )
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 1

    def test_load_from_db_with_corrupt_json(self, temp_db):
        """Test handling of corrupted JSON in database."""
        CharacteristicCacheSQLite(temp_db)
        conn = sqlite3.connect(temp_db)
        conn.execute("""
            INSERT INTO homekit_cache
            (homekit_id, config_num, accessories, broadcast_key, state_num)
            VALUES (?, ?, ?, ?, ?)
        """, ("bad_json", 1, "INVALID_JSON", "key", 1))
        conn.commit()
        conn.close()

        cache2 = CharacteristicCacheSQLite(temp_db)
        assert "bad_json" not in cache2.storage_data
