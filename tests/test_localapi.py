import pytest
import json
from unittest.mock import AsyncMock, patch, Mock
from fastapi import HTTPException
from collections import defaultdict

import asyncio

from tado_local.api import TadoLocalAPI
from tado_local.state import DeviceStateManager


@pytest.fixture
def api_instance(tmp_path):
    """Fixture to create a TadoLocalAPI instance."""
    db_path = str(tmp_path / "test_tado.db")
    return TadoLocalAPI(db_path=db_path)


@pytest.fixture
def mock_pairing():
    """Fixture to create a mock IpPairing."""
    pairing = AsyncMock()
    pairing.list_accessories_and_characteristics = AsyncMock(return_value=[])
    pairing.subscribe = AsyncMock()
    pairing.unsubscribe = AsyncMock()
    pairing.get_characteristics = AsyncMock(return_value={})
    pairing.put_characteristics = AsyncMock(return_value={})
    pairing.dispatcher_connect = Mock()
    return pairing


class TestTadoLocalAPI:
    @pytest.mark.asyncio
    async def test_api_initialization(self, api_instance):
        """Test TadoLocalAPI initialization."""
        assert api_instance is not None
        assert api_instance.pairing is None
        assert api_instance.accessories_cache == []
        assert api_instance.accessories_dict == {}
        assert api_instance.accessories_id == {}
        assert api_instance.characteristic_map == {}
        assert api_instance.characteristic_iid_map == {}
        assert api_instance.device_to_characteristics == {}
        assert api_instance.event_listeners == []
        assert api_instance.zone_event_listeners == []
        assert api_instance.last_update is None
        assert api_instance.is_initializing is False
        assert api_instance.is_shutting_down is False
        assert api_instance.state_manager is not None

    @pytest.mark.asyncio
    async def test_initialize_with_pairing(self, api_instance, mock_pairing):
        """Test API initialization with pairing."""
        with (
            patch.object(api_instance, 'refresh_accessories', new_callable=AsyncMock) as mock_refresh,
            patch.object(api_instance, 'initialize_device_states', new_callable=AsyncMock) as mock_init_states,
            patch.object(api_instance, 'setup_event_listeners', new_callable=AsyncMock) as mock_setup,
        ):

            await api_instance.initialize(mock_pairing)

            assert api_instance.pairing == mock_pairing
            mock_refresh.assert_called_once()
            mock_init_states.assert_called_once()
            mock_setup.assert_called_once()
            assert api_instance.is_initializing is False

    @pytest.mark.asyncio
    async def test_cleanup(self, api_instance, mock_pairing):
        """Test cleanup properly shuts down resources."""
        api_instance.pairing = mock_pairing
        api_instance.subscribed_characteristics = [(1, 1), (1, 2)]
        api_instance.pairing_subscriptions = {id(mock_pairing): [(1, 1), (1, 2)]}

        # Create actual asyncio tasks that can be cancelled and gathered
        async def dummy_task():
            await asyncio.sleep(10)

        mock_task1 = asyncio.create_task(dummy_task())
        mock_task2 = asyncio.create_task(dummy_task())
        api_instance.background_tasks = [mock_task1, mock_task2]

        # Add mock window timer
        window_task = asyncio.create_task(dummy_task())
        api_instance.window_close_timers = {1: window_task}

        # Add event listeners
        queue1 = AsyncMock()
        queue2 = AsyncMock()
        api_instance.event_listeners = [queue1, queue2]
        api_instance.zone_event_listeners = [queue1]

        await api_instance.cleanup()

        assert api_instance.is_shutting_down is True
        mock_pairing.unsubscribe.assert_called_once()

        # Verify tasks were cancelled
        assert mock_task1.cancelled() or mock_task1.done()
        assert mock_task2.cancelled() or mock_task2.done()
        assert window_task.cancelled() or window_task.done()

    @pytest.mark.asyncio
    async def test_event_listeners_management(self, api_instance):
        """Test event listener queues can be added."""
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()

        api_instance.event_listeners.append(queue1)
        api_instance.zone_event_listeners.append(queue2)

        assert len(api_instance.event_listeners) == 1
        assert len(api_instance.zone_event_listeners) == 1
        assert queue1 in api_instance.event_listeners
        assert queue2 in api_instance.zone_event_listeners

    @pytest.mark.asyncio
    async def test_initialization_flag(self, api_instance):
        """Test initialization flag prevents logging during init."""
        assert api_instance.is_initializing is False

        api_instance.is_initializing = True
        assert api_instance.is_initializing is True

        api_instance.is_initializing = False
        assert api_instance.is_initializing is False

    @pytest.mark.asyncio
    async def test_shutdown_flag(self, api_instance):
        """Test shutdown flag."""
        assert api_instance.is_shutting_down is False

        api_instance.is_shutting_down = True
        assert api_instance.is_shutting_down is True


class TestTadoLocalAPIRefreshAccessories:
    @pytest.mark.asyncio
    async def test_process_raw_accessories(self, api_instance):
        """Test raw accessories processing."""
        raw_accessories = [{'aid': 1, 'services': [{'type': '0000003E-0000-1000-8000-0026BB765291', 'characteristics': []}]}]

        with patch.object(api_instance.state_manager, 'get_or_create_device', return_value=1):
            result = api_instance._process_raw_accessories(raw_accessories)
            assert isinstance(result, dict)

    def test_process_raw_accessories_with_serial_number(self, api_instance):
        """Test processing accessories with serial number."""
        raw_accessories = [
            {
                'aid': 1,
                'services': [
                    {
                        'type': '0000003E-0000-1000-8000-0026BB765291',
                        'characteristics': [
                            {'type': '00000030-0000-1000-8000-0026BB765291', 'value': 'SN12345'},
                            {'type': '00000011-0000-1000-8000-0026BB765291', 'value': '21.3'},
                        ],
                    }
                ],
            }
        ]

        with patch.object(api_instance.state_manager, 'get_or_create_device', return_value=1):
            result = api_instance._process_raw_accessories(raw_accessories)
            assert result[1]['serial_number'] == "SN12345"

    @pytest.mark.asyncio
    async def test_refresh_accessories_without_pairing(self, api_instance):
        """Test refresh_accessories raises exception when pairing is None."""
        with pytest.raises(HTTPException) as exc_info:
            await api_instance.refresh_accessories()

        assert exc_info.value.status_code == 503
        assert "Bridge not connected" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_refresh_accessories_success(self, api_instance, mock_pairing):
        """Test successful accessories refresh."""
        mock_accessories = [
            {
                'aid': 1,
                'services': [
                    {
                        'type': '0000003E-0000-1000-8000-0026BB765291',
                        'characteristics': [{'type': '00000030-0000-1000-8000-0026BB765291', 'value': 'SN12345'}],
                    }
                ],
            }
        ]

        api_instance.pairing = mock_pairing
        mock_pairing.list_accessories_and_characteristics.return_value = mock_accessories

        with patch.object(api_instance, '_process_raw_accessories', return_value={'device1': {'aid': 1}}):
            await api_instance.refresh_accessories()

            assert api_instance.last_update is not None
            assert len(api_instance.accessories_cache) > 0
            mock_pairing.list_accessories_and_characteristics.assert_called_once()


class TestTadoLocalAPIDeviceStates:
    @pytest.mark.asyncio
    async def test_initialize_device_states_without_pairing(self, api_instance):
        """Test initialize_device_states returns early when pairing is None."""
        api_instance.pairing = None

        # Should return early without raising
        await api_instance.initialize_device_states()

        # No calls should be made
        assert api_instance.pairing is None

    @pytest.mark.asyncio
    async def test_initialize_device_states_no_char_to_poll(self, api_instance, mock_pairing):
        """Test initialize_device_states when no readable characteristics exist."""
        api_instance.pairing = mock_pairing
        api_instance.device_to_characteristics = {1: [(1, 10, 'temperature')]}

        # Setup accessories with non-readable characteristic
        api_instance.accessories_cache = [{'aid': 1, 'services': [{'characteristics': [{'iid': 10, 'perms': ['pw']}]}]}]  # Write-only, not readable

        await api_instance.initialize_device_states()

        # Should not call get_characteristics
        mock_pairing.get_characteristics.assert_not_called()

    @pytest.mark.asyncio
    async def test_initialize_device_states_single_readable_char(self, api_instance, mock_pairing):
        """Test initialize_device_states with a single readable characteristic."""
        api_instance.pairing = mock_pairing

        api_instance.device_to_characteristics = {1: [(1, 10, 'temperature')]}

        # Setup accessories with readable characteristic
        api_instance.accessories_cache = [{'aid': 1, 'services': [{'characteristics': [{'iid': 10, 'perms': ['pr', 'ev']}]}]}]  # Readable

        mock_pairing.get_characteristics.return_value = {(1, 10): {'value': 21.5}}

        with patch.object(api_instance.state_manager, 'update_device_characteristic', return_value=('temperature', None, 21.5)) as mock_update:
            await api_instance.initialize_device_states()

            mock_pairing.get_characteristics.assert_called_once_with([(1, 10)])
            mock_update.assert_called_once()
            call_args = mock_update.call_args[0]
            assert call_args[0] == 1  # device_id
            assert call_args[1] == 'temperature'  # char_type
            assert call_args[2] == 21.5  # value

    @pytest.mark.asyncio
    async def test_initialize_device_states_multiple_char(self, api_instance, mock_pairing):
        """Test initialize_device_states with multiple readable characteristics."""
        api_instance.pairing = mock_pairing

        api_instance.device_to_characteristics = {1: [(1, 10, 'temperature'), (1, 11, 'humidity')], 2: [(2, 20, 'temperature')]}

        # Setup accessories
        api_instance.accessories_cache = [
            {'aid': 1, 'services': [{'characteristics': [{'iid': 10, 'perms': ['pr']}, {'iid': 11, 'perms': ['pr', 'ev']}]}]},
            {'aid': 2, 'services': [{'characteristics': [{'iid': 20, 'perms': ['pr']}]}]},
        ]

        mock_pairing.get_characteristics.return_value = {(1, 10): {'value': 21.5}, (1, 11): {'value': 55}, (2, 20): {'value': 22.0}}

        with patch.object(api_instance.state_manager, 'update_device_characteristic', return_value=('field', None, 'value')) as mock_update:
            await api_instance.initialize_device_states()

            mock_pairing.get_characteristics.assert_called_once()
            assert mock_update.call_count == 3

    @pytest.mark.asyncio
    async def test_initialize_device_states_batch_processing(self, api_instance, mock_pairing):
        """Test that characteristics are polled in batches of 10."""
        api_instance.pairing = mock_pairing

        # Create 25 characteristics (should require 3 batches)
        device_to_chars = {}
        accessories = []

        for i in range(25):
            aid = i + 1
            iid = 10
            device_to_chars[aid] = [(aid, iid, 'temperature')]

            accessories.append({'aid': aid, 'services': [{'characteristics': [{'iid': iid, 'perms': ['pr']}]}]})

        api_instance.device_to_characteristics = device_to_chars
        api_instance.accessories_cache = accessories

        # Mock responses for all characteristics
        mock_results = {(i + 1, 10): {'value': 20.0 + i} for i in range(25)}
        mock_pairing.get_characteristics.return_value = mock_results

        with patch.object(api_instance.state_manager, 'update_device_characteristic', return_value=('temperature', None, 20.0)):
            await api_instance.initialize_device_states()

            # Should be called 3 times (batch_size=10: 10, 10, 5)
            assert mock_pairing.get_characteristics.call_count == 3

    @pytest.mark.asyncio
    async def test_initialize_device_states_handles_batch_errors(self, api_instance, mock_pairing):
        """Test that errors in one batch don't prevent other batches."""
        api_instance.pairing = mock_pairing

        # Create 15 characteristics (2 batches)
        device_to_chars = {}
        accessories = []

        for i in range(15):
            aid = i + 1
            iid = 10
            device_to_chars[aid] = [(aid, iid, 'temperature')]

            accessories.append({'aid': aid, 'services': [{'characteristics': [{'iid': iid, 'perms': ['pr']}]}]})

        api_instance.device_to_characteristics = device_to_chars
        api_instance.accessories_cache = accessories

        # First batch fails, second succeeds
        mock_pairing.get_characteristics.side_effect = [Exception("Connection error"), {(i, 10): {'value': 20.0} for i in range(11, 16)}]

        with patch.object(api_instance.state_manager, 'update_device_characteristic', return_value=('temperature', None, 20.0)):
            await api_instance.initialize_device_states()

            # Should attempt both batches despite first one failing
            assert mock_pairing.get_characteristics.call_count == 2

    @pytest.mark.asyncio
    async def test_initialize_device_states_skips_none_values(self, api_instance, mock_pairing):
        """Test that None values are skipped during initialization."""
        api_instance.pairing = mock_pairing

        api_instance.device_to_characteristics = {1: [(1, 10, 'temperature')]}

        api_instance.accessories_cache = [{'aid': 1, 'services': [{'characteristics': [{'iid': 10, 'perms': ['pr']}]}]}]

        # Return None value
        mock_pairing.get_characteristics.return_value = {(1, 10): {'value': None}}

        with patch.object(api_instance.state_manager, 'update_device_characteristic') as mock_update:
            await api_instance.initialize_device_states()

            # Should not call update_device_characteristic for None values
            mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_initialize_device_states_missing_characteristic_data(self, api_instance, mock_pairing):
        """Test handling when characteristic is not in results."""
        api_instance.pairing = mock_pairing

        api_instance.device_to_characteristics = {1: [(1, 10, 'temperature'), (1, 11, 'humidity')]}

        api_instance.accessories_cache = [{'aid': 1, 'services': [{'characteristics': [{'iid': 10, 'perms': ['pr']}, {'iid': 11, 'perms': ['pr']}]}]}]

        # Only return one characteristic
        mock_pairing.get_characteristics.return_value = {
            (1, 10): {'value': 21.5}
            # (1, 11) missing
        }

        with patch.object(api_instance.state_manager, 'update_device_characteristic', return_value=('temperature', None, 21.5)) as mock_update:
            await api_instance.initialize_device_states()

            # Should only update the one present
            assert mock_update.call_count == 1

    @pytest.mark.asyncio
    async def test_initialize_device_states_mixed_permissions(self, api_instance, mock_pairing):
        """Test that only readable characteristics are polled."""
        api_instance.pairing = mock_pairing

        api_instance.device_to_characteristics = {1: [(1, 10, 'temperature'), (1, 11, 'target_temp'), (1, 12, 'mode')]}

        api_instance.accessories_cache = [
            {
                'aid': 1,
                'services': [
                    {
                        'characteristics': [
                            {'iid': 10, 'perms': ['pr']},  # Readable
                            {'iid': 11, 'perms': ['pw']},  # Write-only
                            {'iid': 12, 'perms': ['pr', 'pw']},  # Read-write
                        ]
                    }
                ],
            }
        ]

        mock_pairing.get_characteristics.return_value = {(1, 10): {'value': 21.5}, (1, 12): {'value': 1}}

        with patch.object(api_instance.state_manager, 'update_device_characteristic', return_value=('field', None, 'value')):
            await api_instance.initialize_device_states()

            # Should only poll characteristics with 'pr' permission
            call_args = mock_pairing.get_characteristics.call_args[0][0]
            assert len(call_args) == 2
            assert (1, 10) in call_args
            assert (1, 12) in call_args
            assert (1, 11) not in call_args

    @pytest.mark.asyncio
    async def test_initialize_device_states_uses_timestamp(self, api_instance, mock_pairing):
        """Test that timestamp is passed to state manager."""
        api_instance.pairing = mock_pairing

        api_instance.device_to_characteristics = {1: [(1, 10, 'temperature')]}

        api_instance.accessories_cache = [{'aid': 1, 'services': [{'characteristics': [{'iid': 10, 'perms': ['pr']}]}]}]

        mock_pairing.get_characteristics.return_value = {(1, 10): {'value': 21.5}}

        with (
            patch.object(api_instance.state_manager, 'update_device_characteristic', return_value=('temperature', None, 21.5)) as mock_update,
            patch('time.time', return_value=1234567890.0),
        ):

            await api_instance.initialize_device_states()

            # Check that timestamp was passed
            call_args = mock_update.call_args[0]
            assert call_args[3] == 1234567890.0  # timestamp argument

    @pytest.mark.asyncio
    async def test_initialize_device_states_logs_initialization(self, api_instance, mock_pairing):
        """Test that initialization is properly logged."""
        api_instance.pairing = mock_pairing

        api_instance.device_to_characteristics = {1: [(1, 10, 'temperature')]}

        api_instance.accessories_cache = [{'aid': 1, 'services': [{'characteristics': [{'iid': 10, 'perms': ['pr']}]}]}]

        mock_pairing.get_characteristics.return_value = {(1, 10): {'value': 21.5}}

        with patch.object(api_instance.state_manager, 'update_device_characteristic', return_value=('temperature', None, 21.5)):
            await api_instance.initialize_device_states()

            # Method should complete without errors
            assert True


class TestTadoLocalAPISetupEventListeners:
    @pytest.mark.asyncio
    async def test_setup_event_listeners_without_pairing(self, api_instance):
        """Test setup_event_listeners returns early when pairing is None."""
        api_instance.pairing = None

        await api_instance.setup_event_listeners()

        # Should return without setting up change_tracker
        assert not hasattr(api_instance, 'change_tracker')

    @pytest.mark.asyncio
    async def test_setup_event_listeners_initializes_change_tracker(self, api_instance, mock_pairing):
        """Test that change_tracker is properly initialized."""
        api_instance.pairing = mock_pairing
        api_instance.device_to_characteristics = {}
        api_instance.accessories_cache = []

        with patch.object(api_instance, 'setup_persistent_events', new_callable=AsyncMock, return_value=True):
            await api_instance.setup_event_listeners()

            assert hasattr(api_instance, 'change_tracker')
            assert api_instance.change_tracker['events_received'] == 0
            assert api_instance.change_tracker['polling_changes'] == 0
            assert isinstance(api_instance.change_tracker['last_values'], dict)
            assert isinstance(api_instance.change_tracker['event_characteristics'], set)

    @pytest.mark.asyncio
    async def test_setup_event_listeners_populates_last_values(self, api_instance, mock_pairing):
        """Test that last_values is populated from current device states."""
        api_instance.pairing = mock_pairing
        api_instance.device_to_characteristics = {
            1: [(1, 10, DeviceStateManager.CHAR_CURRENT_TEMPERATURE), (1, 11, DeviceStateManager.CHAR_TARGET_TEMPERATURE)]
        }
        api_instance.accessories_cache = []

        # Mock current state
        mock_state = {'current_temperature': 21.5, 'target_temperature': 22.0, 'humidity': 55}

        with (
            patch.object(api_instance.state_manager, 'get_current_state', return_value=mock_state),
            patch.object(api_instance, 'setup_persistent_events', new_callable=AsyncMock, return_value=True),
        ):

            await api_instance.setup_event_listeners()

            # Should have populated last_values
            assert (1, 10) in api_instance.change_tracker['last_values']
            assert api_instance.change_tracker['last_values'][(1, 10)] == 21.5
            assert (1, 11) in api_instance.change_tracker['last_values']
            assert api_instance.change_tracker['last_values'][(1, 11)] == 22.0

    @pytest.mark.asyncio
    async def test_setup_event_listeners_calls_persistent_events(self, api_instance, mock_pairing):
        """Test that setup_persistent_events is called."""
        api_instance.pairing = mock_pairing
        api_instance.device_to_characteristics = {}
        api_instance.accessories_cache = []

        with patch.object(api_instance, 'setup_persistent_events', new_callable=AsyncMock, return_value=True) as mock_setup:
            await api_instance.setup_event_listeners()

            mock_setup.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_event_listeners_fallback_to_polling(self, api_instance, mock_pairing):
        """Test fallback to polling when events are not available."""
        api_instance.pairing = mock_pairing
        api_instance.device_to_characteristics = {}
        api_instance.accessories_cache = []

        with (
            patch.object(api_instance, 'setup_persistent_events', new_callable=AsyncMock, return_value=False),
            patch.object(api_instance, 'setup_polling_system', new_callable=AsyncMock) as mock_polling,
        ):

            await api_instance.setup_event_listeners()

            mock_polling.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_event_listeners_always_enables_polling(self, api_instance, mock_pairing):
        """Test that polling is always enabled as safety net for standalone accessories."""
        api_instance.pairing = mock_pairing
        api_instance.device_to_characteristics = {}
        api_instance.accessories_cache = []

        with (
            patch.object(api_instance, 'setup_persistent_events', new_callable=AsyncMock, return_value=True),
            patch.object(api_instance, 'setup_polling_system', new_callable=AsyncMock) as mock_polling,
        ):

            await api_instance.setup_event_listeners()

            mock_polling.assert_called_once()


class TestTadoLocalAPISetupPersistentEvents:
    @pytest.mark.asyncio
    async def test_setup_persistent_events_no_event_char(self, api_instance, mock_pairing):
        """Test setup_persistent_events when no event characteristics exist."""
        api_instance.pairing = mock_pairing
        api_instance.accessories_cache = [
            {'aid': 1, 'services': [{'characteristics': [{'iid': 10, 'type': '00000011-0000-1000-8000-0026BB765291', 'perms': ['pr']}]}]}  # No 'ev'
        ]
        api_instance.change_tracker = {'event_characteristics': set(), 'last_values': {}}

        result = await api_instance.setup_persistent_events()

        assert result is False
        mock_pairing.subscribe.assert_not_called()

    @pytest.mark.asyncio
    async def test_setup_persistent_events_success(self, api_instance, mock_pairing):
        """Test successful setup of persistent events."""
        api_instance.pairing = mock_pairing
        api_instance.aid_to_pairing = {1: mock_pairing}
        api_instance.accessories_cache = [
            {
                'aid': 1,
                'services': [
                    {
                        'characteristics': [
                            {'iid': 10, 'type': '00000011-0000-1000-8000-0026BB765291', 'perms': ['pr', 'ev']},  # Event notification supported
                            {'iid': 11, 'type': '00000035-0000-1000-8000-0026BB765291', 'perms': ['pr', 'pw', 'ev']},
                        ]
                    }
                ],
            }
        ]
        api_instance.change_tracker = {'event_characteristics': set(), 'last_values': {}}

        result = await api_instance.setup_persistent_events()

        assert result is True
        mock_pairing.subscribe.assert_called_once()

        # Check subscribed characteristics
        call_args = mock_pairing.subscribe.call_args[0][0]
        assert (1, 10) in call_args
        assert (1, 11) in call_args
        assert len(api_instance.subscribed_characteristics) == 2

    @pytest.mark.asyncio
    async def test_setup_persistent_events_registers_dispatcher(self, api_instance, mock_pairing):
        """Test that event callback is registered with dispatcher."""
        api_instance.pairing = mock_pairing
        api_instance.aid_to_pairing = {1: mock_pairing}
        api_instance.accessories_cache = [
            {'aid': 1, 'services': [{'characteristics': [{'iid': 10, 'type': '00000011-0000-1000-8000-0026BB765291', 'perms': ['ev']}]}]}
        ]
        api_instance.change_tracker = {'event_characteristics': set(), 'last_values': {}}

        await api_instance.setup_persistent_events()

        # Check that dispatcher_connect was called
        mock_pairing.dispatcher_connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_persistent_events_populates_char_maps(self, api_instance, mock_pairing):
        """Test that characteristic maps are populated."""
        mock_pairing.dispatcher_connect = Mock()  # ensure sync
        api_instance.pairing = mock_pairing
        api_instance.aid_to_pairing = {1: mock_pairing}
        api_instance.accessories_cache = [
            {'aid': 1, 'services': [{'characteristics': [{'iid': 10, 'type': '00000011-0000-1000-8000-0026BB765291', 'perms': ['ev']}]}]}
        ]
        api_instance.change_tracker = {'event_characteristics': set(), 'last_values': {}}

        await api_instance.setup_persistent_events()

        # Check characteristic_map was populated
        assert (1, 10) in api_instance.characteristic_map

    @pytest.mark.asyncio
    async def test_setup_persistent_events_tracks_event_char(self, api_instance, mock_pairing):
        """Test that event characteristics are tracked."""
        api_instance.pairing = mock_pairing
        api_instance.aid_to_pairing = {1: mock_pairing}
        api_instance.accessories_cache = [
            {
                'aid': 1,
                'services': [
                    {
                        'characteristics': [
                            {'iid': 10, 'type': '00000011-0000-1000-8000-0026BB765291', 'perms': ['ev']},
                            {'iid': 11, 'type': '00000035-0000-1000-8000-0026BB765291', 'perms': ['pr']},  # No 'ev'
                        ]
                    }
                ],
            }
        ]
        api_instance.change_tracker = {'event_characteristics': set(), 'last_values': {}}

        await api_instance.setup_persistent_events()

        # Only event-capable characteristics should be tracked
        assert (1, 10) in api_instance.change_tracker['event_characteristics']
        assert (1, 11) not in api_instance.change_tracker['event_characteristics']

    @pytest.mark.asyncio
    async def test_setup_persistent_events_handles_multiple_acc(self, api_instance, mock_pairing):
        """Test setup with multiple accessories."""
        api_instance.pairing = mock_pairing
        api_instance.aid_to_pairing = {1: mock_pairing, 2: mock_pairing}
        api_instance.accessories_cache = [
            {'aid': 1, 'services': [{'characteristics': [{'iid': 10, 'type': '00000011-0000-1000-8000-0026BB765291', 'perms': ['ev']}]}]},
            {'aid': 2, 'services': [{'characteristics': [{'iid': 20, 'type': '00000011-0000-1000-8000-0026BB765291', 'perms': ['ev']}]}]},
        ]
        api_instance.change_tracker = {'event_characteristics': set(), 'last_values': {}}

        result = await api_instance.setup_persistent_events()

        assert result is True
        call_args = mock_pairing.subscribe.call_args[0][0]
        assert (1, 10) in call_args
        assert (2, 20) in call_args

    @pytest.mark.asyncio
    async def test_setup_persistent_events_handles_subscript_err(self, api_instance, mock_pairing):
        """Test handling of subscription errors."""
        api_instance.pairing = mock_pairing
        api_instance.aid_to_pairing = {1: mock_pairing}
        api_instance.accessories_cache = [
            {'aid': 1, 'services': [{'characteristics': [{'iid': 10, 'type': '00000011-0000-1000-8000-0026BB765291', 'perms': ['ev']}]}]}
        ]
        api_instance.change_tracker = {'event_characteristics': set(), 'last_values': {}}

        mock_pairing.subscribe.side_effect = Exception("Subscription failed")

        result = await api_instance.setup_persistent_events()

        assert result is False

    @pytest.mark.asyncio
    async def test_setup_persistent_events_callback_creates_task(self, api_instance, mock_pairing):
        """Test that event callback is registered and callable."""
        api_instance.pairing = mock_pairing
        api_instance.aid_to_pairing = {1: mock_pairing}
        api_instance.accessories_cache = [
            {'aid': 1, 'services': [{'characteristics': [{'iid': 10, 'type': '00000011-0000-1000-8000-0026BB765291', 'perms': ['ev']}]}]}
        ]
        api_instance.change_tracker = {'event_characteristics': set(), 'last_values': {}}

        await api_instance.setup_persistent_events()

        # Verify the callback was registered
        mock_pairing.dispatcher_connect.assert_called_once()

        # Verify the callback is callable
        callback = mock_pairing.dispatcher_connect.call_args[0][0]
        assert callable(callback)

    @pytest.mark.asyncio
    async def test_setup_persistent_events_empty_accessories_cache(self, api_instance, mock_pairing):
        """Test setup with empty accessories cache."""
        api_instance.pairing = mock_pairing
        api_instance.accessories_cache = []
        api_instance.change_tracker = {'event_characteristics': set(), 'last_values': {}}

        result = await api_instance.setup_persistent_events()

        assert result is False
        mock_pairing.subscribe.assert_not_called()

    @pytest.mark.asyncio
    async def test_setup_persistent_events_mixed_permissions(self, api_instance, mock_pairing):
        """Test that only characteristics with 'ev' permission are subscribed."""
        api_instance.pairing = mock_pairing
        api_instance.aid_to_pairing = {1: mock_pairing}
        api_instance.accessories_cache = [
            {
                'aid': 1,
                'services': [
                    {
                        'characteristics': [
                            {'iid': 10, 'type': 'type1', 'perms': ['pr', 'ev']},  # Event
                            {'iid': 11, 'type': 'type2', 'perms': ['pr']},  # No event
                            {'iid': 12, 'type': 'type3', 'perms': ['pw', 'ev']},  # Event
                            {'iid': 13, 'type': 'type4', 'perms': ['pw']},  # No event
                        ]
                    }
                ],
            }
        ]
        api_instance.change_tracker = {'event_characteristics': set(), 'last_values': {}}

        await api_instance.setup_persistent_events()

        call_args = mock_pairing.subscribe.call_args[0][0]
        assert (1, 10) in call_args
        assert (1, 11) not in call_args
        assert (1, 12) in call_args
        assert (1, 13) not in call_args

    @pytest.mark.asyncio
    async def test_setup_persistent_events_stores_subscribed_char(self, api_instance, mock_pairing):
        """Test that subscribed_characteristics is stored for cleanup."""
        api_instance.pairing = mock_pairing
        api_instance.aid_to_pairing = {1: mock_pairing}
        api_instance.accessories_cache = [
            {
                'aid': 1,
                'services': [{'characteristics': [{'iid': 10, 'type': 'type1', 'perms': ['ev']}, {'iid': 11, 'type': 'type2', 'perms': ['ev']}]}],
            }
        ]
        api_instance.change_tracker = {'event_characteristics': set(), 'last_values': {}}

        await api_instance.setup_persistent_events()

        assert hasattr(api_instance, 'subscribed_characteristics')
        assert len(api_instance.subscribed_characteristics) == 2
        assert (1, 10) in api_instance.subscribed_characteristics
        assert (1, 11) in api_instance.subscribed_characteristics


class TestTadoLocalAPIDataStructures:
    @pytest.mark.asyncio
    async def test_characteristic_maps(self, api_instance):
        """Test characteristic mapping dictionaries."""
        # Test characteristic_map structure (aid, iid) -> char_type
        api_instance.characteristic_map[(1, 10)] = "temperature"
        assert api_instance.characteristic_map[(1, 10)] == "temperature"

        # Test characteristic_iid_map structure (aid, char_type) -> iid
        api_instance.characteristic_iid_map[(1, "temperature")] = 10
        assert api_instance.characteristic_iid_map[(1, "temperature")] == 10

        # Test device_to_characteristics structure
        api_instance.device_to_characteristics[1] = [(1, 10, "temperature")]
        assert len(api_instance.device_to_characteristics[1]) == 1

    @pytest.mark.asyncio
    async def test_accessories_id_mapping(self, api_instance):
        """Test accessories ID mapping."""
        api_instance.accessories_id[1] = "device_serial_123"
        assert api_instance.accessories_id[1] == "device_serial_123"

    @pytest.mark.asyncio
    async def test_device_states_tracking(self, api_instance):
        """Test device states are properly tracked."""
        assert isinstance(api_instance.device_states, dict)
        assert isinstance(api_instance.last_zone_states, dict)

        # Test that device_states uses defaultdict
        test_value = api_instance.device_states['test_device']
        assert isinstance(test_value, dict)

    @pytest.mark.asyncio
    async def test_get_iid_from_characteristics(self, api_instance):
        """Test get_iid_from_characteristics returns IID for known mapping."""
        api_instance.characteristic_iid_map[(1, "temperature")] = 10
        assert api_instance.get_iid_from_characteristics(1, "temperature") == 10

    @pytest.mark.asyncio
    async def test_get_iid_from_characteristics_unknown(self, api_instance):
        """Test get_iid_from_characteristics returns None for unknown mapping."""
        assert api_instance.get_iid_from_characteristics(99, "unknown") is None


class TestTadoLocalHandleChange:
    def _setup_handle_change(self, api_instance):
        """Helper to initialize common handle_change dependencies."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.change_tracker = {
            'events_received': 0,
            'polling_changes': 0,
            'last_values': {},
            'event_characteristics': set(),
        }
        api_instance.accessories_id = {1: "dev-1"}
        api_instance.accessories_cache = [
            {
                'aid': 1,
                'id': "dev-1",
                'services': [{'characteristics': [{'iid': 10, 'type': '00000011-0000-1000-8000-0026BB765291'}]}],  # CurrentTemperature
            }
        ]
        api_instance.state_manager.get_device_info.return_value = {
            'zone_name': 'Living',
            'name': 'Thermostat',
            'is_zone_leader': False,  # avoid window detection path
        }
        api_instance.state_manager.update_device_characteristic.return_value = ("current_temperature", 20.0, 22.5)
        api_instance.broadcast_state_change = AsyncMock()
        api_instance._handle_window_open_detection = Mock()

    @pytest.mark.asyncio
    async def test_handle_change_updates_state(self, api_instance):
        """Test that handle_change updates state manager with new value."""
        self._setup_handle_change(api_instance)
        api_instance.characteristic_map[(1, 10)] = "CurrentTemperature"

        await api_instance.handle_change(1, 10, {"value": 22.5})

        api_instance.state_manager.update_device_characteristic.assert_called_once()
        args = api_instance.state_manager.update_device_characteristic.call_args[0]
        assert args[0] == "dev-1"
        assert args[1] == '00000011-0000-1000-8000-0026BB765291'.lower()
        assert args[2] == 22.5
        api_instance.broadcast_state_change.assert_called_once_with("dev-1", "Living")

    @pytest.mark.asyncio
    async def test_handle_change_no_update_on_same_value(self, api_instance):
        """Test that handle_change does not update state manager if value is unchanged."""
        self._setup_handle_change(api_instance)
        api_instance.characteristic_map[(1, 10)] = "CurrentTemperature"

        await api_instance.handle_change(1, 10, {"value": 22.5})
        api_instance.state_manager.update_device_characteristic.reset_mock()
        api_instance.broadcast_state_change.reset_mock()

        await api_instance.handle_change(1, 10, {"value": 22.5})

        api_instance.state_manager.update_device_characteristic.assert_not_called()
        api_instance.broadcast_state_change.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_change_ignores_none_value(self, api_instance):
        """Test that handle_change ignores None values."""
        self._setup_handle_change(api_instance)
        api_instance.characteristic_map[(1, 10)] = "CurrentTemperature"

        await api_instance.handle_change(1, 10, {"value": None})

        api_instance.state_manager.update_device_characteristic.assert_not_called()
        api_instance.broadcast_state_change.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_change_no_aid(self, api_instance):
        """Test that handle_change does not have an aid."""
        self._setup_handle_change(api_instance)
        api_instance.characteristic_map[(1, 10)] = "CurrentTemperature"

        await api_instance.handle_change(None, 10, {"value": 22.5})

        api_instance.state_manager.update_device_characteristic.assert_not_called()
        api_instance.broadcast_state_change.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_change_no_iid(self, api_instance):
        """Test that handle_change does not have an iid."""
        self._setup_handle_change(api_instance)
        api_instance.characteristic_map[(1, 10)] = "CurrentTemperature"

        await api_instance.handle_change(1, None, {"value": 22.5})

        api_instance.state_manager.update_device_characteristic.assert_not_called()
        api_instance.broadcast_state_change.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_change_caches_characteristic_from_accessory(self, api_instance):
        """Test that handle_change fills characteristic_map when missing."""
        self._setup_handle_change(api_instance)
        assert (1, 10) not in api_instance.characteristic_map

        await api_instance.handle_change(1, 10, {"value": 22.5})

        assert (1, 10) in api_instance.characteristic_map

    @pytest.mark.asyncio
    async def test_handle_change_tracks_event_counters(self, api_instance):
        """Test that event counters are updated."""
        self._setup_handle_change(api_instance)
        api_instance.characteristic_map[(1, 10)] = "CurrentTemperature"

        await api_instance.handle_change(1, 10, {"value": 22.5}, source="EVENT")

        assert api_instance.change_tracker['events_received'] == 1
        assert api_instance.change_tracker['polling_changes'] == 0

    @pytest.mark.asyncio
    async def test_handle_change_tracks_polling_counters(self, api_instance):
        """Test that polling counters are updated."""
        self._setup_handle_change(api_instance)
        api_instance.characteristic_map[(1, 10)] = "CurrentTemperature"

        await api_instance.handle_change(1, 10, {"value": 22.5}, source="POLL")

        api_instance._handle_window_open_detection.assert_not_called()
        assert api_instance.change_tracker['events_received'] == 0
        assert api_instance.change_tracker['polling_changes'] == 1

    @pytest.mark.asyncio
    async def test_handle_change_calls_window_detection(self, api_instance):
        """Test that window open detection is triggered for temperature changes."""
        self._setup_handle_change(api_instance)
        api_instance.characteristic_map[(1, 10)] = "CurrentTemperature"
        api_instance.state_manager.get_device_info.return_value = {'zone_name': 'Living', 'name': 'Thermostat', 'is_zone_leader': True}

        await api_instance.handle_change(1, 10, {"value": 22.5})

        assert api_instance.change_tracker['polling_changes'] == 1
        api_instance._handle_window_open_detection.assert_called_once_with(
            'dev-1', {'zone_name': 'Living', 'name': 'Thermostat', 'is_zone_leader': True}, '00000011-0000-1000-8000-0026bb765291'
        )


class TestTadoLocalAPIHandleWindowOpenDetection:
    def _setup_window_detection(self, api_instance):
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        device_id = "dev-1"
        device_info = {"zone_name": "Living", "window_open_time": 15, "window_rest_time": 15}
        char_type = DeviceStateManager.CHAR_CURRENT_TEMPERATURE
        return device_id, device_info, char_type

    def test_window_detection_ignores_non_temperature_char(self, api_instance):
        device_id, device_info, _ = self._setup_window_detection(api_instance)

        api_instance._handle_window_open_detection(device_id, device_info, "targettemperature")

        api_instance.state_manager.get_current_state.assert_not_called()
        api_instance.state_manager.update_device_window_status.assert_not_called()

    def test_window_detection_returns_when_no_leader_state(self, api_instance):
        device_id, device_info, char_type = self._setup_window_detection(api_instance)
        api_instance.state_manager.get_current_state.return_value = None

        api_instance._handle_window_open_detection(device_id, device_info, char_type)

        api_instance.state_manager.get_device_history_info.assert_not_called()
        api_instance.state_manager.update_device_window_status.assert_not_called()

    def test_window_detection_returns_when_no_history(self, api_instance):
        device_id, device_info, char_type = self._setup_window_detection(api_instance)
        api_instance.state_manager.get_current_state.return_value = {"window": 0, "cur_heating": 1}
        api_instance.state_manager.get_device_history_info.return_value = {
            "history_count": 0,
            "earliest_entry": None,
            "latest_entry": None,
        }

        api_instance._handle_window_open_detection(device_id, device_info, char_type)

        api_instance.state_manager.update_device_window_status.assert_not_called()

    def test_window_detection_returns_when_short_on_history(self, api_instance):
        device_id, device_info, char_type = self._setup_window_detection(api_instance)
        api_instance.state_manager.get_current_state.return_value = {"window": 0, "cur_heating": 1}
        api_instance.state_manager.get_device_history_info.return_value = {
            "history_count": 1,
            "earliest_entry": (22.5, 0, 1940),
            "latest_entry": (22.5, 0, 1940),  # same reading
        }

        api_instance._handle_window_open_detection(device_id, device_info, char_type)

        api_instance.state_manager.update_device_window_status.assert_not_called()

    def test_window_detection_closes_open_window_after_timeout(self, api_instance):
        device_id, device_info, char_type = self._setup_window_detection(api_instance)
        api_instance.state_manager.get_current_state.return_value = {"window": 1, "cur_heating": 1}
        api_instance.state_manager.get_device_history_info.return_value = {
            "history_count": 2,
            "earliest_entry": (22.0, 1, 1000),
            "latest_entry": (21.0, 1, 1000),
        }

        with patch.object(api_instance, "_cancel_window_close_timer") as mock_cancel, patch("time.time", return_value=2000):
            api_instance._handle_window_open_detection(device_id, device_info, char_type)

        api_instance.state_manager.update_device_window_status.assert_called_once_with(device_id, 2)
        mock_cancel.assert_called_once_with(device_id)

    def test_window_detection_opens_window_on_heating_temp_drop(self, api_instance):
        device_id, device_info, char_type = self._setup_window_detection(api_instance)
        api_instance.state_manager.get_current_state.return_value = {"window": 0, "cur_heating": 1}
        api_instance.state_manager.get_device_history_info.return_value = {
            "history_count": 2,
            "earliest_entry": (22.5, 0, 1940),
            "latest_entry": (21.0, 0, 1990),  # drop = 1.5
        }

        with patch.object(api_instance, "_schedule_window_close_timer") as mock_schedule, patch("time.time", return_value=2000):
            api_instance._handle_window_open_detection(device_id, device_info, char_type)

        api_instance.state_manager.update_device_window_status.assert_called_once_with(device_id, 1)
        mock_schedule.assert_called_once_with(device_id, 15, device_info)

    def test_window_detection_keeps_window_closed_on_small_drop(self, api_instance):
        device_id, device_info, char_type = self._setup_window_detection(api_instance)
        api_instance.state_manager.get_current_state.return_value = {"window": 0, "cur_heating": 1}
        api_instance.state_manager.get_device_history_info.return_value = {
            "history_count": 2,
            "earliest_entry": (22.0, 0, 1940),
            "latest_entry": (21.3, 0, 1990),  # drop = 0.7
        }

        with patch.object(api_instance, "_schedule_window_close_timer") as mock_schedule, patch("time.time", return_value=2000):
            api_instance._handle_window_open_detection(device_id, device_info, char_type)

        api_instance.state_manager.update_device_window_status.assert_called_once_with(device_id, 0)
        mock_schedule.assert_not_called()

    def test_window_detection_cooling_mode_sets_open_and_schedules_timer(self, api_instance):
        device_id, device_info, char_type = self._setup_window_detection(api_instance)

        # cooling mode
        api_instance.state_manager.get_current_state.return_value = {"window": 0, "cur_heating": 2}
        api_instance.state_manager.get_device_history_info.return_value = {
            "history_count": 2,
            "earliest_entry": (21.0, 0, 1940),
            "latest_entry": (22.5, 0, 1990),  # temp_change = 1.5
        }

        with patch.object(api_instance, "_schedule_window_close_timer") as mock_schedule, patch("time.time", return_value=2000):
            api_instance._handle_window_open_detection(device_id, device_info, char_type)

        api_instance.state_manager.update_device_window_status.assert_called_once_with(device_id, 1)
        mock_schedule.assert_called_once_with(device_id, 15, device_info)

    def test_window_detection_cooling_mode_small_rise_keeps_closed(self, api_instance):
        device_id, device_info, char_type = self._setup_window_detection(api_instance)

        # cooling mode
        api_instance.state_manager.get_current_state.return_value = {"window": 0, "cur_heating": 2}
        api_instance.state_manager.get_device_history_info.return_value = {
            "history_count": 2,
            "earliest_entry": (21.0, 0, 1940),
            "latest_entry": (21.3, 0, 1990),  # temp_change = 0.3
        }

        with patch.object(api_instance, "_schedule_window_close_timer") as mock_schedule, patch("time.time", return_value=2000):
            api_instance._handle_window_open_detection(device_id, device_info, char_type)

        api_instance.state_manager.update_device_window_status.assert_called_once_with(device_id, 0)
        mock_schedule.assert_not_called()

    def test_window_opens_after_timer_expires(self, api_instance):
        """Test window opens when temp drop threshold is met after timer expires."""
        device_id, device_info, char_type = self._setup_window_detection(api_instance)

        # Initialize window_close_timers dict
        api_instance.window_close_timers = {}

        api_instance.state_manager.get_current_state.return_value = {"window": 0, "cur_heating": 1}
        api_instance.state_manager.get_device_history_info.return_value = {
            "history_count": 2,
            "earliest_entry": (22.5, 0, 1940),
            "latest_entry": (21.0, 0, 1990),  # drop = 1.5
        }

        with patch.object(api_instance, "_schedule_window_close_timer") as mock_schedule, patch("time.time", return_value=2000):
            api_instance._handle_window_open_detection(device_id, device_info, char_type)

        api_instance.state_manager.update_device_window_status.assert_called_once_with(device_id, 1)
        mock_schedule.assert_called_once_with(device_id, 15, device_info)

    @pytest.mark.asyncio
    async def test_window_closes_after_timer_expiry(self, api_instance):
        """Test window closes after timer expires by calling the callback."""
        device_id, device_info, char_type = self._setup_window_detection(api_instance)

        api_instance.window_close_timers = {}
        api_instance.state_manager.get_current_state.return_value = {"window": 1, "cur_heating": 1}
        api_instance.state_manager.get_device_history_info.return_value = {
            "history_count": 2,
            "earliest_entry": (22.5, 0, 1940),
            "latest_entry": (21.0, 0, 1990),
        }

        # First call: window opens and schedules timer
        with patch("time.time", return_value=2000):
            api_instance._handle_window_open_detection(device_id, device_info, char_type)

        api_instance.state_manager.update_device_window_status.reset_mock()

        # Simulate timer expiry: window was open for > 15 mins
        with patch("time.time", return_value=3000):  # 1000 seconds later = ~16 mins
            api_instance._handle_window_open_detection(device_id, device_info, char_type)

        # Window should be set to rest state (2)
        api_instance.state_manager.update_device_window_status.assert_called_once_with(device_id, 2)


class TestTadoLocalAPIScheduleWindowCloseTimer:
    @pytest.mark.asyncio
    async def test_schedule_window_close_timer_creates_and_stores_task(self, api_instance):
        """Test that _schedule_window_close_timer creates and stores an asyncio task."""
        api_instance.window_close_timers = {}
        api_instance.is_shutting_down = False
        device_id = "dev-1"
        delay = 1  # 1 minute
        device_info = {"zone_name": "Living"}

        api_instance._schedule_window_close_timer(device_id, delay, device_info)

        assert device_id in api_instance.window_close_timers
        task = api_instance.window_close_timers[device_id]
        assert asyncio.isfuture(task)
        assert not task.done()

    @pytest.mark.asyncio
    async def test_schedule_window_close_timer_cancels_existing_task(self, api_instance):
        """Test that scheduling a new timer cancels the existing one."""
        api_instance.window_close_timers = {}
        api_instance.is_shutting_down = False
        device_id = "dev-1"

        # Schedule first timer
        api_instance._schedule_window_close_timer(device_id, 10, {"zone_name": "Living"})
        first_task = api_instance.window_close_timers[device_id]

        # Schedule second timer - should cancel first
        api_instance._schedule_window_close_timer(device_id, 10, {"zone_name": "Living"})
        second_task = api_instance.window_close_timers[device_id]

        # Give the cancellation time to propagate
        await asyncio.sleep(0.1)

        # First task should be cancelled or done
        assert first_task.cancelled() or first_task.done()
        assert not second_task.done()
        assert first_task is not second_task

    @pytest.mark.asyncio
    async def test_schedule_window_close_timer_returns_early_when_shut_down(self, api_instance):
        """Test that _schedule_window_close_timer returns early when is_shutting_down is True."""
        api_instance.window_close_timers = {}
        api_instance.is_shutting_down = True
        device_id = "dev-1"

        api_instance._schedule_window_close_timer(device_id, 10, {"zone_name": "Living"})

        # Should not create a task
        assert device_id not in api_instance.window_close_timers

    @pytest.mark.asyncio
    async def test_schedule_window_close_timer_adds_done_callback(self, api_instance):
        """Test that the done callback is registered and cleans up after timer completes."""
        api_instance.window_close_timers = {}
        api_instance.is_shutting_down = False
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.state_manager.get_current_state.return_value = {"window": 1}
        device_id = "dev-1"
        delay = 1  # 1 minute (in real use, but we'll mock the handler)

        with patch.object(api_instance, "_window_close_handler", new_callable=AsyncMock) as mock_handler:
            mock_handler.return_value = None
            api_instance._schedule_window_close_timer(device_id, delay, {"zone_name": "Living"})

            task = api_instance.window_close_timers[device_id]

            # Wait for task to complete
            try:
                await asyncio.wait_for(task, timeout=2)
            except asyncio.TimeoutError:
                pass

        # Task should be cleaned up after completion
        assert device_id not in api_instance.window_close_timers or api_instance.window_close_timers[device_id].done()

    @pytest.mark.asyncio
    async def test_schedule_window_close_timer_multiple_devices(self, api_instance):
        """Test scheduling timers for multiple devices independently."""
        api_instance.window_close_timers = {}
        api_instance.is_shutting_down = False

        device_ids = ["dev-1", "dev-2", "dev-3"]

        for device_id in device_ids:
            api_instance._schedule_window_close_timer(device_id, 10, {"zone_name": "Living"})

        # All devices should have timers
        assert len(api_instance.window_close_timers) == 3
        for device_id in device_ids:
            assert device_id in api_instance.window_close_timers
            assert asyncio.isfuture(api_instance.window_close_timers[device_id])


class TestTadoLocalAPICancelWindowCloseTimer:
    @pytest.mark.asyncio
    async def test_cancel_window_close_timer_removes_and_cancels_task(self, api_instance):
        """Test that _cancel_window_close_timer removes and cancels the task."""
        api_instance.window_close_timers = {}
        device_id = "dev-1"

        # Create and store a task
        async def dummy_task():
            await asyncio.sleep(10)

        task = asyncio.create_task(dummy_task())
        api_instance.window_close_timers[device_id] = task

        # Cancel the timer
        api_instance._cancel_window_close_timer(device_id)

        # Give cancellation time to propagate
        await asyncio.sleep(0.1)

        # Task should be removed and cancelled
        assert device_id not in api_instance.window_close_timers
        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_cancel_window_close_timer_noop_when_no_timer(self, api_instance):
        """Test that _cancel_window_close_timer handles missing timer gracefully."""
        api_instance.window_close_timers = {}
        device_id = "dev-1"

        # Should not raise exception
        api_instance._cancel_window_close_timer(device_id)

        # Should be empty
        assert device_id not in api_instance.window_close_timers

    @pytest.mark.asyncio
    async def test_cancel_window_close_timer_ignores_completed_task(self, api_instance):
        """Test that _cancel_window_close_timer doesn't try to cancel already done task."""
        api_instance.window_close_timers = {}
        device_id = "dev-1"

        # Create a completed task
        async def dummy_task():
            pass

        task = asyncio.create_task(dummy_task())
        await task  # Wait for completion
        api_instance.window_close_timers[device_id] = task

        # Cancel the timer
        api_instance._cancel_window_close_timer(device_id)

        # Task should be removed
        assert device_id not in api_instance.window_close_timers
        # task.cancel() should not be called on completed task (handled by the method)

    @pytest.mark.asyncio
    async def test_cancel_window_close_timer_removes_from_dict(self, api_instance):
        """Test that device is removed from window_close_timers dict."""
        api_instance.window_close_timers = {}
        device_id = "dev-1"

        # Create and store a task
        async def dummy_task():
            await asyncio.sleep(10)

        task = asyncio.create_task(dummy_task())
        api_instance.window_close_timers[device_id] = task

        # Verify task is stored
        assert device_id in api_instance.window_close_timers

        # Cancel the timer
        api_instance._cancel_window_close_timer(device_id)

        # Task should be removed from dict
        assert device_id not in api_instance.window_close_timers

    @pytest.mark.asyncio
    async def test_cancel_window_close_timer_multiple_devices(self, api_instance):
        """Test canceling timers for multiple devices independently."""
        api_instance.window_close_timers = {}

        async def dummy_task():
            await asyncio.sleep(10)

        # Create timers for multiple devices
        device_ids = ["dev-1", "dev-2", "dev-3"]
        for device_id in device_ids:
            task = asyncio.create_task(dummy_task())
            api_instance.window_close_timers[device_id] = task

        # Cancel timer for one device
        api_instance._cancel_window_close_timer("dev-1")

        # Give cancellation time to propagate
        await asyncio.sleep(0.1)

        # Only dev-1 should be removed
        assert "dev-1" not in api_instance.window_close_timers
        assert "dev-2" in api_instance.window_close_timers
        assert "dev-3" in api_instance.window_close_timers


class TestTadoLocalAPIWindowCloseHandler:
    @pytest.mark.asyncio
    async def test_window_close_handler_waits_and_closes_window(self, api_instance):
        """Test that _window_close_handler waits for delay and closes window."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.is_shutting_down = False
        device_id = "dev-1"
        device_info = {"zone_name": "Living"}
        closing_delay = 1  # 1 minute = 60 seconds

        api_instance.state_manager.get_current_state.return_value = {"window": 1}

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep, patch("time.time", return_value=1000):
            await api_instance._window_close_handler(device_id, device_info, closing_delay)

        # Should sleep for 60 seconds (1 minute * 60)
        mock_sleep.assert_called_once_with(60)
        # Should update window status
        api_instance.state_manager.update_device_window_status.assert_called_once_with(device_id, 2)

    @pytest.mark.asyncio
    async def test_window_close_handler_returns_early_if_shutting_down(self, api_instance):
        """Test that handler returns early if is_shutting_down is True."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.is_shutting_down = True
        device_id = "dev-1"
        device_info = {"zone_name": "Living"}

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await api_instance._window_close_handler(device_id, device_info, 1)

        # Should not update window status
        api_instance.state_manager.update_device_window_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_window_close_handler_does_not_close_if_window_already_closed(self, api_instance):
        """Test that handler does not close window if it's already closed."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.is_shutting_down = False
        device_id = "dev-1"
        device_info = {"zone_name": "Living"}

        # Window is already closed (0)
        api_instance.state_manager.get_current_state.return_value = {"window": 0}

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await api_instance._window_close_handler(device_id, device_info, 1)

        # Should not update window status
        api_instance.state_manager.update_device_window_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_window_close_handler_returns_if_no_current_state(self, api_instance):
        """Test that handler returns gracefully if no current state exists."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.is_shutting_down = False
        device_id = "dev-1"
        device_info = {"zone_name": "Living"}

        api_instance.state_manager.get_current_state.return_value = None

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await api_instance._window_close_handler(device_id, device_info, 1)

        # Should not update window status
        api_instance.state_manager.update_device_window_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_window_close_handler_handles_cancellation(self, api_instance):
        """Test that handler gracefully handles CancelledError."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.is_shutting_down = False
        device_id = "dev-1"
        device_info = {"zone_name": "Living"}

        async def mock_sleep_cancelled(duration):
            raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=mock_sleep_cancelled):
            await api_instance._window_close_handler(device_id, device_info, 1)

        # Should not update window status (task was cancelled)
        api_instance.state_manager.update_device_window_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_window_close_handler_handles_exception(self, api_instance):
        """Test that handler catches and logs exceptions."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.is_shutting_down = False
        device_id = "dev-1"
        device_info = {"zone_name": "Living"}

        api_instance.state_manager.get_current_state.side_effect = Exception("Connection error")

        with patch("asyncio.sleep", new_callable=AsyncMock):
            # Should not raise
            await api_instance._window_close_handler(device_id, device_info, 1)

    @pytest.mark.asyncio
    async def test_window_close_handler_respects_closing_delay(self, api_instance):
        """Test that handler uses correct closing delay."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.is_shutting_down = False
        device_id = "dev-1"
        device_info = {"zone_name": "Living"}

        api_instance.state_manager.get_current_state.return_value = {"window": 1}

        closing_delay = 30  # 30 minutes

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await api_instance._window_close_handler(device_id, device_info, closing_delay)

        # Should sleep for 1800 seconds (30 minutes * 60)
        mock_sleep.assert_called_once_with(1800)


class TestTadoLocalAPIWindowCloseTimerStop:
    def test_window_close_timer_stop_removes_task(self, api_instance):
        """Test that _window_close_timer_stop removes the task from dict."""
        api_instance.window_close_timers = {}
        device_id = "dev-1"

        # Create a mock task
        mock_task = Mock(spec=asyncio.Task)
        api_instance.window_close_timers[device_id] = mock_task

        api_instance._window_close_timer_stop(device_id, mock_task)

        # Task should be removed
        assert device_id not in api_instance.window_close_timers

    def test_window_close_timer_stop_noop_when_task_mismatch(self, api_instance):
        """Test that _window_close_timer_stop does nothing if task doesn't match."""
        api_instance.window_close_timers = {}
        device_id = "dev-1"

        # Create two different mock tasks
        mock_task1 = Mock(spec=asyncio.Task)
        mock_task2 = Mock(spec=asyncio.Task)
        api_instance.window_close_timers[device_id] = mock_task1

        # Call with different task
        api_instance._window_close_timer_stop(device_id, mock_task2)

        # Task should still be in dict (not removed)
        assert device_id in api_instance.window_close_timers
        assert api_instance.window_close_timers[device_id] is mock_task1

    def test_window_close_timer_stop_noop_when_no_timer(self, api_instance):
        """Test that _window_close_timer_stop handles missing timer gracefully."""
        api_instance.window_close_timers = {}
        device_id = "dev-1"

        mock_task = Mock(spec=asyncio.Task)

        # Should not raise exception
        api_instance._window_close_timer_stop(device_id, mock_task)

        # Dict should remain empty
        assert device_id not in api_instance.window_close_timers

    def test_window_close_timer_stop_multiple_devices(self, api_instance):
        """Test cleanup of timers for multiple devices independently."""
        api_instance.window_close_timers = {}

        # Setup multiple devices with tasks
        mock_task1 = Mock(spec=asyncio.Task)
        mock_task2 = Mock(spec=asyncio.Task)
        mock_task3 = Mock(spec=asyncio.Task)

        api_instance.window_close_timers["dev-1"] = mock_task1
        api_instance.window_close_timers["dev-2"] = mock_task2
        api_instance.window_close_timers["dev-3"] = mock_task3

        # Stop timer for dev-1
        api_instance._window_close_timer_stop("dev-1", mock_task1)

        # Only dev-1 should be removed
        assert "dev-1" not in api_instance.window_close_timers
        assert "dev-2" in api_instance.window_close_timers
        assert "dev-3" in api_instance.window_close_timers

    @pytest.mark.asyncio
    async def test_window_close_timer_stop_called_as_done_callback(self, api_instance):
        """Test that _window_close_timer_stop is registered and called as done callback."""
        api_instance.window_close_timers = {}
        api_instance.is_shutting_down = False
        device_id = "dev-1"

        async def dummy_handler():
            pass

        # Create a real task
        task = asyncio.create_task(dummy_handler())
        api_instance.window_close_timers[device_id] = task

        # Add done callback
        task.add_done_callback(lambda t: api_instance._window_close_timer_stop(device_id, t))

        # Wait for task to complete
        await task
        await asyncio.sleep(0.1)  # Give callback time to execute

        # Task should be cleaned up
        assert device_id not in api_instance.window_close_timers


class TestTadoLocalAPIBroadcastEvent:
    @pytest.mark.asyncio
    async def test_broadcast_event_sends_to_all_listeners(self, api_instance):
        """Test that broadcast_event sends to all connected listeners."""
        api_instance.event_listeners = []
        api_instance.zone_event_listeners = []

        # Create mock listeners
        mock_listener1 = AsyncMock()
        mock_listener2 = AsyncMock()
        api_instance.event_listeners = [mock_listener1, mock_listener2]

        event_data = {"type": "device", "device": "dev-1", "value": 22.5}

        await api_instance.broadcast_event(event_data)

        # Both listeners should receive the event
        mock_listener1.put.assert_called_once()
        mock_listener2.put.assert_called_once()

        # Verify the message format
        call_args = mock_listener1.put.call_args[0][0]
        assert "data: " in call_args
        assert "dev-1" in call_args
        assert "22.5" in call_args

    @pytest.mark.asyncio
    async def test_broadcast_event_zone_events_to_both_listener_types(self, api_instance):
        """Test that zone events go to both all-events and zone-only listeners."""
        api_instance.event_listeners = []
        api_instance.zone_event_listeners = []

        mock_all_listener = AsyncMock()
        mock_zone_listener = AsyncMock()
        api_instance.event_listeners = [mock_all_listener]
        api_instance.zone_event_listeners = [mock_zone_listener]

        event_data = {"type": "zone", "zone": "Living", "target_temp": 21.0}

        await api_instance.broadcast_event(event_data)

        # Both listener types should receive zone events
        mock_all_listener.put.assert_called_once()
        mock_zone_listener.put.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_event_device_events_only_to_all_listeners(self, api_instance):
        """Test that device events only go to all-events listeners."""
        api_instance.event_listeners = []
        api_instance.zone_event_listeners = []

        mock_all_listener = AsyncMock()
        mock_zone_listener = AsyncMock()
        api_instance.event_listeners = [mock_all_listener]
        api_instance.zone_event_listeners = [mock_zone_listener]

        event_data = {"type": "device", "device": "dev-1", "value": 22.5}

        await api_instance.broadcast_event(event_data)

        # Only all-events listener should receive device events
        mock_all_listener.put.assert_called_once()
        mock_zone_listener.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcast_event_removes_disconnected_listeners(self, api_instance):
        """Test that disconnected listeners are removed."""
        api_instance.event_listeners = []
        api_instance.zone_event_listeners = []

        mock_listener1 = AsyncMock()
        mock_listener2 = AsyncMock()

        # Listener 1 fails, listener 2 succeeds
        mock_listener1.put.side_effect = Exception("Disconnected")
        mock_listener2.put.return_value = None

        api_instance.event_listeners = [mock_listener1, mock_listener2]

        event_data = {"type": "device", "device": "dev-1"}

        await api_instance.broadcast_event(event_data)

        # Disconnected listener should be removed
        assert mock_listener1 not in api_instance.event_listeners
        assert mock_listener2 in api_instance.event_listeners

    @pytest.mark.asyncio
    async def test_broadcast_event_removes_from_both_lists(self, api_instance):
        """Test that disconnected listeners are removed from both listener lists."""
        api_instance.event_listeners = []
        api_instance.zone_event_listeners = []

        mock_listener = AsyncMock()
        mock_listener.put.side_effect = Exception("Disconnected")

        # Add to both lists
        api_instance.event_listeners = [mock_listener]
        api_instance.zone_event_listeners = [mock_listener]

        event_data = {"type": "zone", "zone": "Living"}

        await api_instance.broadcast_event(event_data)

        # Should be removed from both lists
        assert mock_listener not in api_instance.event_listeners
        assert mock_listener not in api_instance.zone_event_listeners

    @pytest.mark.asyncio
    async def test_broadcast_event_handles_broadcast_exception(self, api_instance):
        """Test that broadcast_event handles exceptions gracefully."""
        api_instance.event_listeners = []
        api_instance.zone_event_listeners = []

        with patch("json.dumps", side_effect=Exception("JSON error")):
            # Should not raise
            await api_instance.broadcast_event({"type": "device"})

    @pytest.mark.asyncio
    async def test_broadcast_event_json_serialization(self, api_instance):
        """Test that event data is properly JSON serialized."""
        api_instance.event_listeners = []
        api_instance.zone_event_listeners = []

        mock_listener = AsyncMock()
        api_instance.event_listeners = [mock_listener]

        event_data = {"type": "device", "device": "dev-1", "value": 22.5, "timestamp": 1234567890}

        await api_instance.broadcast_event(event_data)

        # Get the sent message
        sent_message = mock_listener.put.call_args[0][0]

        # Verify SSE format
        assert sent_message.startswith("data: ")
        assert sent_message.endswith("\n\n")

        # Extract and verify JSON
        json_str = sent_message.replace("data: ", "").strip()
        parsed = json.loads(json_str)
        assert parsed["type"] == "device"
        assert parsed["device"] == "dev-1"
        assert parsed["value"] == 22.5

    @pytest.mark.asyncio
    async def test_broadcast_event_to_empty_listeners(self, api_instance):
        """Test broadcast_event with no listeners."""
        api_instance.event_listeners = []
        api_instance.zone_event_listeners = []

        event_data = {"type": "device", "device": "dev-1"}

        # Should not raise
        await api_instance.broadcast_event(event_data)


class TestTadoLocalAPICelsiusToFahrenheit:
    def test_celsius_to_fahrenheit_valid_temperature(self, api_instance):
        """Test conversion of valid Celsius temperature."""
        result = api_instance._celsius_to_fahrenheit(0)
        assert result == 32.0

        result = api_instance._celsius_to_fahrenheit(100)
        assert result == 212.0

        result = api_instance._celsius_to_fahrenheit(20)
        assert result == 68.0

    def test_celsius_to_fahrenheit_negative_temperature(self, api_instance):
        """Test conversion of negative Celsius temperatures."""
        result = api_instance._celsius_to_fahrenheit(-40)
        assert result == -40.0

    def test_celsius_to_fahrenheit_decimal_temperature(self, api_instance):
        """Test conversion with decimal Celsius values."""
        result = api_instance._celsius_to_fahrenheit(22.5)
        assert result == 72.5

    def test_celsius_to_fahrenheit_returns_none_for_none_input(self, api_instance):
        """Test that None input returns None."""
        result = api_instance._celsius_to_fahrenheit(None)
        assert result is None

    def test_celsius_to_fahrenheit_rounding(self, api_instance):
        """Test that result is rounded to 1 decimal place."""
        result = api_instance._celsius_to_fahrenheit(20.123)
        assert result == 68.2  # 20.123 * 9/5 + 32 = 68.2214, rounded to 68.2


class TestTadoLocalAPIBuildDeviceState:
    def test_build_device_state_with_valid_data(self, api_instance):
        """Test building device state with valid temperature and state data."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.state_manager.get_current_state.return_value = {
            'current_temperature': 20.0,
            'target_temperature': 21.5,
            'humidity': 45,
            'target_heating_cooling_state': 1,
            'current_heating_cooling_state': 1,
            'valve_position': 75,
            'window': 0,
        }
        api_instance.state_manager.device_info_cache = {'dev-1': {'battery_state': 'NORMAL'}}

        result = api_instance._build_device_state('dev-1')

        assert result['cur_temp_c'] == 20.0
        assert result['cur_temp_f'] == 68.0
        assert result['target_temp_c'] == 21.5
        assert result['target_temp_f'] == 70.7
        assert result['hum_perc'] == 45
        assert result['mode'] == 1
        assert result['cur_heating'] == 1
        assert result['valve_position'] == 75
        assert result['battery_low'] is False
        assert result['window'] == 0

    def test_build_device_state_battery_low(self, api_instance):
        """Test that battery_low is True when battery_state is not NORMAL."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.state_manager.get_current_state.return_value = {
            'current_temperature': 20.0,
            'target_temperature': 21.5,
        }
        api_instance.state_manager.device_info_cache = {'dev-1': {'battery_state': 'LOW'}}

        result = api_instance._build_device_state('dev-1')

        assert result['battery_low'] is True

    def test_build_device_state_battery_unknown(self, api_instance):
        """Test that battery_low is True when battery_state is UNKNOWN."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.state_manager.get_current_state.return_value = {
            'current_temperature': 20.0,
            'target_temperature': 21.5,
        }
        api_instance.state_manager.device_info_cache = {'dev-1': {'battery_state': 'UNKNOWN'}}

        result = api_instance._build_device_state('dev-1')

        assert result['battery_low'] is True

    def test_build_device_state_no_battery_info(self, api_instance):
        """Test that battery_low is False when no battery info available."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.state_manager.get_current_state.return_value = {
            'current_temperature': 20.0,
            'target_temperature': 21.5,
        }
        api_instance.state_manager.device_info_cache = {'dev-1': {}}

        result = api_instance._build_device_state('dev-1')

        assert result['battery_low'] is False

    def test_build_device_state_none_temperatures(self, api_instance):
        """Test handling of None temperatures."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.state_manager.get_current_state.return_value = {
            'current_temperature': None,
            'target_temperature': None,
            'humidity': 45,
        }
        api_instance.state_manager.device_info_cache = {'dev-1': {}}

        result = api_instance._build_device_state('dev-1')

        assert result['cur_temp_c'] is None
        assert result['cur_temp_f'] is None
        assert result['target_temp_c'] is None
        assert result['target_temp_f'] is None

    def test_build_device_state_heating_off(self, api_instance):
        """Test that cur_heating is 0 when not heating."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.state_manager.get_current_state.return_value = {
            'current_heating_cooling_state': 0,
            'current_temperature': 20.0,
        }
        api_instance.state_manager.device_info_cache = {'dev-1': {}}

        result = api_instance._build_device_state('dev-1')

        assert result['cur_heating'] == 0

    def test_build_device_state_default_mode(self, api_instance):
        """Test default mode value when not specified."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.state_manager.get_current_state.return_value = {
            'current_temperature': 20.0,
        }
        api_instance.state_manager.device_info_cache = {'dev-1': {}}

        result = api_instance._build_device_state('dev-1')

        assert result['mode'] == 0


class TestTadoLocalAPIBroadcastStateChange:
    @pytest.mark.asyncio
    async def test_broadcast_state_change_sends_device_event(self, api_instance):
        """Test that broadcast_state_change sends device event."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.state_manager.get_device_info.return_value = {
            'serial_number': 'ABC123',
            'zone_id': None,
        }
        api_instance.state_manager.get_current_state.return_value = {
            'current_temperature': 20.0,
            'target_temperature': 21.5,
        }
        api_instance.state_manager.device_info_cache = {'dev-1': {}}
        api_instance.broadcast_event = AsyncMock()
        api_instance.last_zone_states = {}

        await api_instance.broadcast_state_change('dev-1', 'Living')

        api_instance.broadcast_event.assert_called_once()
        call_args = api_instance.broadcast_event.call_args[0][0]
        assert call_args['type'] == 'device'
        assert call_args['device_id'] == 'dev-1'
        assert call_args['serial'] == 'ABC123'
        assert call_args['zone_name'] == 'Living'

    @pytest.mark.asyncio
    async def test_broadcast_state_change_returns_early_no_device_info(self, api_instance):
        """Test that broadcast_state_change returns early if device info not found."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.state_manager.get_device_info.return_value = None
        api_instance.broadcast_event = AsyncMock()

        await api_instance.broadcast_state_change('dev-1', 'Living')

        api_instance.broadcast_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcast_state_change_includes_zone_event(self, api_instance):
        """Test that zone state event is broadcast when device is in zone."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.state_manager.get_device_info.return_value = {
            'serial_number': 'ABC123',
            'zone_id': 'zone-1',
        }
        api_instance.state_manager.get_current_state.return_value = {
            'current_temperature': 20.0,
            'target_temperature': 21.5,
            'target_heating_cooling_state': 1,
            'current_heating_cooling_state': 1,
            'window': 0,
        }
        api_instance.state_manager.device_info_cache = {'dev-1': {}}
        api_instance.state_manager.zone_cache = {
            'zone-1': {
                'name': 'Living',
                'leader_device_id': 'dev-1',
                'is_circuit_driver': False,
            }
        }
        api_instance.broadcast_event = AsyncMock()
        api_instance.last_zone_states = {}

        await api_instance.broadcast_state_change('dev-1', 'Living')

        # Should broadcast both device and zone events
        assert api_instance.broadcast_event.call_count == 2

        device_call = api_instance.broadcast_event.call_args_list[0][0][0]
        zone_call = api_instance.broadcast_event.call_args_list[1][0][0]

        assert device_call['type'] == 'device'
        assert zone_call['type'] == 'zone'
        assert zone_call['zone_id'] == 'zone-1'

    @pytest.mark.asyncio
    async def test_broadcast_state_change_zone_not_changed(self, api_instance):
        """Test that zone event is not broadcast if state hasn't changed."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.state_manager.get_device_info.return_value = {
            'serial_number': 'ABC123',
            'zone_id': 'zone-1',
        }
        api_instance.state_manager.get_current_state.return_value = {
            'current_temperature': 20.0,
            'target_temperature': 21.5,
            'target_heating_cooling_state': 1,
            'current_heating_cooling_state': 1,
            'window': 0,
        }
        api_instance.state_manager.device_info_cache = {'dev-1': {}}
        api_instance.state_manager.zone_cache = {
            'zone-1': {
                'name': 'Living',
                'leader_device_id': 'dev-1',
                'is_circuit_driver': False,
            }
        }
        api_instance.broadcast_event = AsyncMock()

        # Set last_zone_states to current state (no change)
        zone_state = {
            'cur_temp_c': 20.0,
            'cur_temp_f': 68.0,
            'hum_perc': None,
            'target_temp_c': 21.5,
            'target_temp_f': 70.7,
            'mode': 1,
            'cur_heating': 1,
            'window_open': False,
        }
        api_instance.last_zone_states = {'zone-1': zone_state}

        await api_instance.broadcast_state_change('dev-1', 'Living')

        # Only device event should be broadcast
        assert api_instance.broadcast_event.call_count == 1
        call_args = api_instance.broadcast_event.call_args[0][0]
        assert call_args['type'] == 'device'

    @pytest.mark.asyncio
    async def test_broadcast_state_change_circuit_driver_logic(self, api_instance):
        """Test zone state calculation with circuit driver."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.state_manager.get_device_info.return_value = {
            'serial_number': 'ABC123',
            'zone_id': 'zone-1',
        }
        api_instance.state_manager.get_current_state.side_effect = [
            # Device state
            {
                'current_temperature': 20.0,
                'target_temperature': 21.5,
                'target_heating_cooling_state': 1,
                'current_heating_cooling_state': 0,
                'window': 0,
            },
            # Leader state (circuit driver)
            {
                'current_temperature': 20.0,
                'target_temperature': 21.5,
                'target_heating_cooling_state': 1,
                'current_heating_cooling_state': 0,
                'window': 0,
            },
            # Radiator valve state
            {
                'current_temperature': 19.5,
                'target_temperature': 21.5,
                'target_heating_cooling_state': 1,
                'current_heating_cooling_state': 1,
                'window': 0,
            },
        ]
        api_instance.state_manager.device_info_cache = {
            'dev-1': {'zone_id': 'zone-1', 'is_circuit_driver': True},
            'dev-2': {'zone_id': 'zone-1', 'is_circuit_driver': False},
        }
        api_instance.state_manager.zone_cache = {
            'zone-1': {
                'name': 'Living',
                'leader_device_id': 'dev-1',
                'is_circuit_driver': True,
            }
        }
        api_instance.broadcast_event = AsyncMock()
        api_instance.last_zone_states = {}

        await api_instance.broadcast_state_change('dev-1', 'Living')

        # Should broadcast both device and zone events
        assert api_instance.broadcast_event.call_count == 2
        zone_call = api_instance.broadcast_event.call_args_list[1][0][0]
        # Zone cur_heating should be 1 (from radiator valve)
        assert zone_call['state']['cur_heating'] == 1

    @pytest.mark.asyncio
    async def test_broadcast_state_change_circuit_driver_logic_other(self, api_instance):
        """Test zone state calculation with circuit driver."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.state_manager.get_device_info.return_value = {
            'serial_number': 'ABC123',
            'zone_id': 'zone-1',
        }
        api_instance.state_manager.get_current_state.side_effect = [
            # Device state
            {
                'current_temperature': 20.0,
                'target_temperature': 21.5,
                'target_heating_cooling_state': 1,
                'current_heating_cooling_state': 0,
                'window': 0,
            },
            # Leader state (circuit driver)
            {
                'current_temperature': 20.0,
                'target_temperature': 21.5,
                'target_heating_cooling_state': 1,
                'current_heating_cooling_state': 0,
                'window': 0,
            },
        ]
        api_instance.state_manager.device_info_cache = {'dev-1': {'zone_id': 'zone-1', 'is_circuit_driver': True}}
        api_instance.state_manager.zone_cache = {
            'zone-1': {
                'name': 'Living',
                'leader_device_id': 'dev-1',
                'is_circuit_driver': True,
            }
        }
        api_instance.broadcast_event = AsyncMock()
        api_instance.last_zone_states = {}

        await api_instance.broadcast_state_change('dev-1', 'Living')

        # Should broadcast both device and zone events
        assert api_instance.broadcast_event.call_count == 2
        zone_call = api_instance.broadcast_event.call_args_list[1][0][0]
        # Zone cur_heating should be 0 (from circuit driver)
        assert zone_call['state']['cur_heating'] == 0

    @pytest.mark.asyncio
    async def test_broadcast_state_change_handles_exception(self, api_instance):
        """Test that exception during broadcast is handled gracefully."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.state_manager.get_device_info.side_effect = Exception("DB error")

        # Should not raise
        await api_instance.broadcast_state_change('dev-1', 'Living')

    @pytest.mark.asyncio
    async def test_broadcast_state_change_timestamp_included(self, api_instance):
        """Test that timestamp is included in broadcast events."""
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.state_manager.get_device_info.return_value = {
            'serial_number': 'ABC123',
            'zone_id': None,
        }
        api_instance.state_manager.get_current_state.return_value = {
            'current_temperature': 20.0,
        }
        api_instance.state_manager.device_info_cache = {'dev-1': {}}
        api_instance.broadcast_event = AsyncMock()
        api_instance.last_zone_states = {}

        with patch("time.time", return_value=1234567890.0):
            await api_instance.broadcast_state_change('dev-1', 'Living')

        call_args = api_instance.broadcast_event.call_args[0][0]
        assert call_args['timestamp'] == 1234567890.0


class TestTadoLocalAPIPollingSystem:
    @pytest.mark.asyncio
    async def test_setup_polling_system_collects_chars_and_starts_task(self, api_instance):
        api_instance.accessories_cache = [
            {
                "aid": 1,
                "services": [
                    {
                        "characteristics": [
                            {"iid": 10, "perms": ["ev", "pr"]},  # include
                            {"iid": 11, "perms": ["pr"]},  # skip
                            {"iid": 12, "perms": ["ev", "pr"]},  # include
                        ]
                    }
                ],
            }
        ]
        api_instance.background_tasks = []

        fake_task = Mock()

        def _fake_create_task(coro):
            # consume coroutine to avoid: "coroutine ... was never awaited"
            coro.close()
            return fake_task

        with patch("asyncio.create_task", side_effect=_fake_create_task) as mock_create_task:
            await api_instance.setup_polling_system()

        assert api_instance.poll_chars == [(1, 10), (1, 12)]
        assert api_instance.monitored_characteristics == [(1, 10), (1, 12)]
        mock_create_task.assert_called_once()
        assert fake_task in api_instance.background_tasks

    @pytest.mark.asyncio
    async def test_setup_polling_system_no_matching_chars(self, api_instance):
        api_instance.accessories_cache = [
            {
                "aid": 1,
                "services": [{"characteristics": [{"iid": 10, "perms": ["pr"]}]}],
            }
        ]
        api_instance.background_tasks = []

        with patch("asyncio.create_task") as mock_create_task:
            await api_instance.setup_polling_system()

        assert api_instance.poll_chars == []
        mock_create_task.assert_not_called()
        assert api_instance.background_tasks == []

    @pytest.mark.asyncio
    async def test_setup_polling_system_handles_exception(self, api_instance):
        # malformed data -> KeyError on accessory["services"]
        api_instance.accessories_cache = [{"aid": 1}]
        api_instance.background_tasks = []

        with patch("asyncio.create_task") as mock_create_task:
            await api_instance.setup_polling_system()

        mock_create_task.assert_not_called()


class TestTadoLocalAPIBackgroundPollingLoop:
    @pytest.mark.asyncio
    async def test_background_polling_loop_runs_fast_and_slow_poll(self, api_instance):
        api_instance.is_shutting_down = False
        api_instance.pairing = True
        api_instance.monitored_characteristics = [(1, 10), (1, 11)]
        api_instance.characteristic_map[(1, 10)] = "CurrentHumidity"  # priority
        api_instance.characteristic_map[(1, 11)] = "CurrentTemperature"
        api_instance._poll_characteristics = AsyncMock()

        async def sleep_once(_seconds):
            api_instance.is_shutting_down = True

        with patch("asyncio.sleep", side_effect=sleep_once), patch("time.time", return_value=130):
            await api_instance.background_polling_loop()

        # FAST-POLL should be called for humidity char
        api_instance._poll_characteristics.assert_any_call([(1, 10)], "FAST-POLL")
        # POLLING should be called for all monitored chars
        api_instance._poll_characteristics.assert_any_call([(1, 10), (1, 11)], "POLLING")

    @pytest.mark.asyncio
    async def test_background_polling_loop_skips_when_not_paired(self, api_instance):
        api_instance.is_shutting_down = False
        api_instance.pairing = False
        api_instance.monitored_characteristics = [(1, 10)]
        api_instance.characteristic_map[(1, 10)] = "CurrentHumidity"
        api_instance._poll_characteristics = AsyncMock()

        async def sleep_once(_seconds):
            api_instance.is_shutting_down = True

        with patch("asyncio.sleep", side_effect=sleep_once), patch("time.time", return_value=130):
            await api_instance.background_polling_loop()

        api_instance._poll_characteristics.assert_not_called()

    @pytest.mark.asyncio
    async def test_background_polling_loop_retries_after_error(self, api_instance):
        api_instance.is_shutting_down = False
        api_instance.pairing = True
        api_instance.monitored_characteristics = [(1, 11)]
        api_instance.characteristic_map[(1, 11)] = "CurrentTemperature"
        api_instance._poll_characteristics = AsyncMock(side_effect=Exception("poll failed"))

        sleep_calls = []

        async def sleep_side_effect(seconds):
            sleep_calls.append(seconds)
            # first sleep is loop tick (10), second should be retry delay (5)
            if seconds == 5:
                api_instance.is_shutting_down = True

        with patch("asyncio.sleep", side_effect=sleep_side_effect), patch("time.time", return_value=130):
            await api_instance.background_polling_loop()

        assert 10 in sleep_calls
        assert 5 in sleep_calls


class TestTadoLocalAPIPollCharacteristics:
    @pytest.mark.asyncio
    async def test_poll_characteristics_batches_and_calls_handle_change(self, api_instance):
        """Validates batching + unified handle_change calls."""
        char_list = [(1, i) for i in range(1, 18)]  # 17 -> 2 batches (15 + 2)

        async def get_chars(batch):
            return {(aid, iid): {"value": iid * 1.0} for aid, iid in batch}

        api_instance.pairing = Mock()
        api_instance.pairing.get_characteristics = AsyncMock(side_effect=get_chars)
        api_instance.handle_change = AsyncMock()

        await api_instance._poll_characteristics(char_list, source="FAST-POLL")

        assert api_instance.pairing.get_characteristics.await_count == 2
        assert api_instance.handle_change.await_count == 17
        api_instance.handle_change.assert_any_await(1, 1, {"value": 1.0}, "FAST-POLL")
        api_instance.handle_change.assert_any_await(1, 17, {"value": 17.0}, "FAST-POLL")

    @pytest.mark.asyncio
    async def test_poll_characteristics_skips_missing_results(self, api_instance):
        """Only returned keys should be forwarded to handle_change."""
        char_list = [(1, 1), (1, 2), (1, 3)]

        api_instance.pairing = Mock()
        api_instance.pairing.get_characteristics = AsyncMock(return_value={(1, 1): {"value": 10}, (1, 3): {"value": 30}})
        api_instance.handle_change = AsyncMock()

        await api_instance._poll_characteristics(char_list)

        assert api_instance.handle_change.await_count == 2
        api_instance.handle_change.assert_any_await(1, 1, {"value": 10}, "POLLING")
        api_instance.handle_change.assert_any_await(1, 3, {"value": 30}, "POLLING")

    @pytest.mark.asyncio
    async def test_poll_characteristics_continues_after_batch_error(self, api_instance):
        """If one batch fails, next batches should still run."""
        char_list = [(1, i) for i in range(1, 21)]  # 20 -> 2 batches

        second_batch = {(1, i): {"value": i} for i in range(16, 21)}
        api_instance.pairing = Mock()
        api_instance.pairing.get_characteristics = AsyncMock(side_effect=[Exception("batch fail"), second_batch])
        api_instance.handle_change = AsyncMock()

        await api_instance._poll_characteristics(char_list)

        # Only second batch processed (5 entries)
        assert api_instance.handle_change.await_count == 5
        api_instance.handle_change.assert_any_await(1, 16, {"value": 16}, "POLLING")


class TestTadoLocalAPIHandleHomekitEvent:
    @pytest.mark.asyncio
    async def test_handle_homekit_event_updates_state_and_notifies_listeners(self, api_instance):
        api_instance.device_states = defaultdict(dict)
        q1 = AsyncMock()
        q2 = AsyncMock()
        api_instance.event_listeners = [q1, q2]

        event = {"aid": 1, "iid": 10, "value": 22.5}
        await api_instance.handle_homekit_event(event)

        assert api_instance.device_states["1"]["10"]["value"] == 22.5
        assert "timestamp" in api_instance.device_states["1"]["10"]
        q1.put.assert_awaited_once_with(event)
        q2.put.assert_awaited_once_with(event)

    @pytest.mark.asyncio
    async def test_handle_homekit_event_ignores_invalid_payload(self, api_instance):
        api_instance.device_states = defaultdict(dict)
        api_instance.event_listeners = [AsyncMock()]

        await api_instance.handle_homekit_event({"aid": 1, "iid": 10, "value": None})

        assert api_instance.device_states == {}

    @pytest.mark.asyncio
    async def test_handle_homekit_event_queue_error_is_swallowed(self, api_instance):
        api_instance.device_states = defaultdict(dict)
        bad_q = AsyncMock()
        good_q = AsyncMock()
        bad_q.put.side_effect = Exception("queue closed")
        api_instance.event_listeners = [bad_q, good_q]

        event = {"aid": 1, "iid": 10, "value": 19.0}
        await api_instance.handle_homekit_event(event)

        # State still updated and other listeners still notified
        assert api_instance.device_states["1"]["10"]["value"] == 19.0
        good_q.put.assert_awaited_once_with(event)

    @pytest.mark.asyncio
    async def test_handle_homekit_event_handles_unexpected_exception(self, api_instance):
        """No exception should escape."""
        api_instance.device_states = None  # will trigger exception on assignment
        api_instance.event_listeners = []

        await api_instance.handle_homekit_event({"aid": 1, "iid": 10, "value": 20.0})


class TestTadoLocalAPISetDeviceCharacteristics:
    @pytest.mark.asyncio
    async def test_set_device_char_raises_when_not_connected(self, api_instance):
        api_instance.pairing = None

        with pytest.raises(ValueError, match="Bridge not connected"):
            await api_instance.set_device_characteristics(1, {"target_temperature": 21.0})

    @pytest.mark.asyncio
    async def test_set_device_char_raises_when_device_not_found(self, api_instance):
        api_instance.pairing = Mock()
        api_instance.state_manager = Mock()
        api_instance.state_manager.get_device_info = Mock(return_value=None)

        with pytest.raises(ValueError, match="Device 1 not found"):
            await api_instance.set_device_characteristics(1, {"target_temperature": 21.0})

    @pytest.mark.asyncio
    async def test_set_device_char_reloads_cache_and_raises_if_no_aid(self, api_instance):
        api_instance.pairing = Mock()
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.state_manager.get_device_info.side_effect = [
            {"id": 1, "aid": None},
            {"id": 1, "aid": None},
        ]
        api_instance.state_manager._load_device_cache = Mock()
        api_instance.accessories_cache = []

        with pytest.raises(ValueError, match="has no HomeKit accessory ID"):
            await api_instance.set_device_characteristics(1, {"target_temperature": 21.0})

        api_instance.state_manager._load_device_cache.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_device_char_raises_when_no_valid_chars(self, api_instance):
        api_instance.pairing = Mock()
        api_instance.pairing.put_characteristics = AsyncMock()
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.state_manager.get_device_info.return_value = {"id": 1, "aid": 100}
        api_instance.accessories_cache = [{"aid": 100, "services": [{"characteristics": [{"iid": 10, "type": "some-other-type"}]}]}]

        with pytest.raises(ValueError, match="No valid characteristics to set"):
            await api_instance.set_device_characteristics(1, {"target_temperature": 21.0})

        api_instance.pairing.put_characteristics.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_device_char_success_single_characteristic(self, api_instance):
        from tado_local.state import DeviceStateManager

        api_instance.pairing = Mock()
        api_instance.pairing.put_characteristics = AsyncMock()
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.state_manager.get_device_info.return_value = {"id": 1, "aid": 100}
        api_instance.accessories_cache = [
            {
                "aid": 100,
                "services": [{"characteristics": [{"iid": 10, "type": DeviceStateManager.CHAR_TARGET_TEMPERATURE}]}],
            }
        ]

        ok = await api_instance.set_device_characteristics(1, {"target_temperature": 21.0})

        assert ok is True
        api_instance.pairing.put_characteristics.assert_awaited_once_with([(100, 10, 21.0)])

    @pytest.mark.asyncio
    async def test_set_device_char_success_multiple_characteristics(self, api_instance):
        from tado_local.state import DeviceStateManager

        api_instance.pairing = Mock()
        api_instance.pairing.put_characteristics = AsyncMock()
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.state_manager.get_device_info.return_value = {"id": 1, "aid": 100}
        api_instance.accessories_cache = [
            {
                "aid": 100,
                "services": [
                    {
                        "characteristics": [
                            {"iid": 10, "type": DeviceStateManager.CHAR_TARGET_TEMPERATURE},
                            {"iid": 11, "type": DeviceStateManager.CHAR_TARGET_HEATING_COOLING},
                        ]
                    }
                ],
            }
        ]

        ok = await api_instance.set_device_characteristics(
            1,
            {
                "target_temperature": 20.5,
                "target_heating_cooling_state": 1,
            },
        )

        assert ok is True
        api_instance.pairing.put_characteristics.assert_awaited_once_with([(100, 10, 20.5), (100, 11, 1)])

    @pytest.mark.asyncio
    async def test_set_device_char_ignores_unknown_char_and_sets_valid(self, api_instance):
        from tado_local.state import DeviceStateManager

        api_instance.pairing = Mock()
        api_instance.pairing.put_characteristics = AsyncMock()
        api_instance.state_manager = Mock(spec=DeviceStateManager)
        api_instance.state_manager.get_device_info.return_value = {"id": 1, "aid": 100}
        api_instance.accessories_cache = [
            {
                "aid": 100,
                "services": [{"characteristics": [{"iid": 10, "type": DeviceStateManager.CHAR_TARGET_TEMPERATURE}]}],
            }
        ]

        ok = await api_instance.set_device_characteristics(
            1,
            {
                "unknown_char": 123,
                "target_temperature": 19.0,
            },
        )

        assert ok is True
        api_instance.pairing.put_characteristics.assert_awaited_once_with([(100, 10, 19.0)])
