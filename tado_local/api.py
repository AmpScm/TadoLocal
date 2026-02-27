#
# Copyright 2025 The TadoLocal and AmpScm contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""Tado Local - Main API class for managing HomeKit connections and device state."""

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Dict, List, Any, Optional

from fastapi import HTTPException
from aiohomekit.controller.ip.pairing import IpPairing

from .state import DeviceStateManager
from .homekit_uuids import get_characteristic_name

# Configure logging
logger = logging.getLogger(__name__)


class TadoLocalAPI:
    """Tado Local that leverages HomeKit for real-time data without cloud dependency."""
    accessories_cache: List[Any]
    accessories_dict: Dict[str, Any]
    accessories_id: Dict[int, str]
    characteristic_map: Dict[tuple[int, int], str]
    characteristic_iid_map: Dict[tuple[int, str], int]
    device_to_characteristics: Dict[int, List[tuple[int, int, str]]]  # device_id -> [(aid, iid, char_type)]

    def __init__(self, db_path: str):
        self.pairing: Optional[IpPairing] = None
        self.extra_pairings: List[IpPairing] = []
        self.aid_to_pairing: Dict[int, IpPairing] = {}
        self.accessories_cache = []
        self.accessories_dict = {}
        self.accessories_id = {}
        self.characteristic_map = {}
        self.characteristic_iid_map = {}
        self.device_to_characteristics = {}
        self.event_listeners: List[asyncio.Queue] = []
        self.zone_event_listeners: List[asyncio.Queue] = []  # Zone-only listeners
        self.last_update: Optional[float] = None
        self.device_states: Dict[str, Dict[str, Any]] = defaultdict(dict)
        self.last_zone_states: Dict[int, Dict[str, Any]] = {}  # Track zone states to deduplicate
        self.state_manager = DeviceStateManager(db_path)
        self.is_initializing = False  # Flag to suppress logging during startup

        # Cleanup tracking
        self.subscribed_characteristics: List[tuple[int, int]] = []
        self.pairing_subscriptions: Dict[int, List[tuple[int, int]]] = {}
        self.background_tasks: List[asyncio.Task] = []
        self.window_close_timers: Dict[int, asyncio.Task] = {}
        self.is_shutting_down = False

    async def initialize(self, pairing: IpPairing, *, extra_pairings: Optional[List[IpPairing]] = None):
        """Initialize the API with a HomeKit pairing and optional standalone accessories."""
        self.pairing = pairing
        self.extra_pairings = extra_pairings or []
        self.is_initializing = True
        await self.refresh_accessories()
        await self.initialize_device_states()
        self.is_initializing = False
        await self.setup_event_listeners()
        n = 1 + len(self.extra_pairings)
        logger.info(f"Tado Local initialized successfully ({n} pairing{'s' if n > 1 else ''})")

    async def cleanup(self):
        """Clean up resources and unsubscribe from events."""
        logger.info("Starting cleanup...")
        self.is_shutting_down = True

        # Cancel window recheck tasks
        if self.window_close_timers:
            logger.info(f"Cancelling {len(self.window_close_timers)} window closing timers")
            for task in self.window_close_timers.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self.window_close_timers.values(), return_exceptions=True)
            self.window_close_timers.clear()

        # Cancel all background tasks
        if self.background_tasks:
            logger.info(f"Cancelling {len(self.background_tasks)} background tasks")
            for task in self.background_tasks:
                if not task.done():
                    task.cancel()

            # Wait for tasks to complete cancellation
            await asyncio.gather(*self.background_tasks, return_exceptions=True)
            logger.info("Background tasks cancelled")

        # Unsubscribe from event characteristics on each pairing
        all_pairings = [self.pairing] + self.extra_pairings
        for pairing in all_pairings:
            if not pairing:
                continue
            chars = self.pairing_subscriptions.get(id(pairing), [])
            if not chars:
                continue
            try:
                logger.info(f"Unsubscribing {len(chars)} events from pairing {id(pairing)}")
                await pairing.unsubscribe(chars)
            except Exception as e:
                logger.warning(f"Error during unsubscribe: {e}")

        # Close standalone accessory pairings
        for extra in self.extra_pairings:
            try:
                await extra.close()
            except Exception as e:
                logger.warning(f"Error closing standalone pairing: {e}")

        # Close all event listener queues
        if self.event_listeners:
            logger.info(f"Closing {len(self.event_listeners)} event listener queues")
            for queue in self.event_listeners:
                try:
                    # Signal end of stream
                    await queue.put(None)
                except Exception as e:
                    logger.info(f"Event queue might already be closed: {e}")
                    pass  # Queue might already be closed
            self.event_listeners.clear()

        # Close zone-only event listener queues
        if self.zone_event_listeners:
            logger.info(f"Closing {len(self.zone_event_listeners)} zone event listener queues")
            for queue in self.zone_event_listeners:
                try:
                    # Signal end of stream
                    await queue.put(None)
                except Exception as e:
                    logger.info(f"Zone queue might already be closed: {e}")
                    pass  # Queue might already be closed
            self.zone_event_listeners.clear()

        logger.info("Cleanup complete")

    async def refresh_accessories(self):
        """Refresh accessories from HomeKit and cache them."""
        if not self.pairing:
            raise HTTPException(status_code=503, detail="Bridge not connected")

        try:
            raw_accessories = await self.pairing.list_accessories_and_characteristics()
            self.accessories_dict = self._process_raw_accessories(raw_accessories)
            for a in raw_accessories:
                aid = a.get('aid')
                if aid is not None:
                    self.aid_to_pairing[aid] = self.pairing

            for extra in self.extra_pairings:
                try:
                    extra_raw = await extra.list_accessories_and_characteristics()
                    extra_dict = self._process_raw_accessories(extra_raw)
                    self.accessories_dict.update(extra_dict)
                    for a in extra_raw:
                        aid = a.get('aid')
                        if aid is not None:
                            self.aid_to_pairing[aid] = extra
                    logger.info(f"Standalone accessory contributed {len(extra_dict)} device(s)")
                except Exception as e:
                    logger.error(f"Failed to refresh standalone accessory: {e}")

            self.accessories_cache = list(self.accessories_dict.values())
            self.last_update = time.time()
            logger.info(f"Refreshed {len(self.accessories_cache)} accessories total")
            return self.accessories_cache
        except Exception as e:
            logger.error(f"Failed to refresh accessories: {e}")
            raise HTTPException(status_code=503, detail=f"Failed to refresh accessories: {e}")

    def _process_raw_accessories(self, raw_accessories):
        accessories = {}

        for a in raw_accessories:
            aid = a.get('aid')
            # Try to find serial number from AccessoryInformation service
            serial_number = None

            for service in a.get('services', []):
                # AccessoryInformation service UUID
                if service.get('type') == '0000003E-0000-1000-8000-0026BB765291':
                    for char in service.get('characteristics', []):
                        # SerialNumber characteristic UUID
                        if char.get('type') == '00000030-0000-1000-8000-0026BB765291':
                            serial_number = char.get('value')
                            break
                if serial_number:
                    break

            # Use database device_id as primary key
            device_id = None

            # Register device and get device_id
            if serial_number:
                device_id = self.state_manager.get_or_create_device(serial_number, aid, a)

                # Map characteristics to device_id for efficient lookup
                char_list = []
                for service in a.get('services', []):
                    for char in service.get('characteristics', []):
                        char_type = char.get('type', '').lower()
                        iid = char.get('iid')
                        # Only track characteristics we care about
                        if char_type in [
                            DeviceStateManager.CHAR_CURRENT_TEMPERATURE,
                            DeviceStateManager.CHAR_TARGET_TEMPERATURE,
                            DeviceStateManager.CHAR_CURRENT_HEATING_COOLING,
                            DeviceStateManager.CHAR_TARGET_HEATING_COOLING,
                            DeviceStateManager.CHAR_HEATING_THRESHOLD,
                            DeviceStateManager.CHAR_COOLING_THRESHOLD,
                            DeviceStateManager.CHAR_TEMP_DISPLAY_UNITS,
                            DeviceStateManager.CHAR_BATTERY_LEVEL,
                            DeviceStateManager.CHAR_STATUS_LOW_BATTERY,
                            DeviceStateManager.CHAR_CURRENT_HUMIDITY,
                            DeviceStateManager.CHAR_TARGET_HUMIDITY,
                            DeviceStateManager.CHAR_ACTIVE,
                            DeviceStateManager.CHAR_VALVE_POSITION,
                        ]:
                            char_list.append((aid, iid, char_type))

                self.device_to_characteristics[device_id] = char_list

            # Use device_id as key (or fallback to aid if no serial)
            key = device_id if device_id else f'aid_{aid}'

            accessories[key] = {
                'id': device_id,  # Primary key for API
                'aid': aid,       # HomeKit accessory ID
                'serial_number': serial_number,
            } | a

            # Keep aid lookup for event handling
            if device_id:
                self.accessories_id[aid] = device_id

        return accessories

    async def initialize_device_states(self):
        """Poll all characteristics once on startup to establish baseline state."""
        if not self.pairing:
            logger.warning("No pairing available for initial state sync")
            return

        logger.info("Initializing device states from current values...")

        # Collect all readable characteristics we care about
        chars_to_poll = []

        for device_id, char_list in self.device_to_characteristics.items():
            for aid, iid, char_type in char_list:
                # Find the characteristic to check if it's readable
                for accessory in self.accessories_cache:
                    if accessory.get('aid') == aid:
                        for service in accessory.get('services', []):
                            for char in service.get('characteristics', []):
                                if char.get('iid') == iid:
                                    perms = char.get('perms', [])
                                    if 'pr' in perms:  # Readable
                                        chars_to_poll.append((aid, iid, device_id, char_type))
                                    break

        if not chars_to_poll:
            logger.warning("No characteristics found to poll for initialization")
            return

        logger.info(f"Polling {len(chars_to_poll)} characteristics for initial state...")

        # Group by pairing so each is polled via its own connection
        from collections import defaultdict as _dd
        by_pairing: Dict[int, List] = _dd(list)
        for item in chars_to_poll:
            aid = item[0]
            by_pairing[id(self.aid_to_pairing.get(aid, self.pairing))].append(item)

        batch_size = 10
        timestamp = time.time()

        for pairing_id, items in by_pairing.items():
            pairing = self.aid_to_pairing.get(items[0][0], self.pairing)
            for i in range(0, len(items), batch_size):
                batch = items[i:i+batch_size]
                char_keys = [(aid, iid) for aid, iid, _, _ in batch]

                try:
                    results = await pairing.get_characteristics(char_keys)

                    for (aid, iid, device_id, char_type) in batch:
                        if (aid, iid) in results:
                            char_data = results[(aid, iid)]
                            value = char_data.get('value')

                            if value is not None:
                                field_name, old_val, new_val = self.state_manager.update_device_characteristic(
                                    device_id, char_type, value, timestamp
                                )
                                if field_name:
                                    logger.debug(f"Initialized device {device_id} {field_name}: {value}")

                except Exception as e:
                    logger.error(f"Error polling batch during initialization: {e}")

        logger.info(f"Device state initialization complete - baseline established for {len(self.device_to_characteristics)} devices")

    async def setup_event_listeners(self):
        """Setup unified change detection with events + polling comparison."""
        if not self.pairing:
            return

        # Initialize change tracking
        self.change_tracker = {
            'events_received': 0,
            'polling_changes': 0,
            'last_values': {},  # Store last known values
            'event_characteristics': set(),  # Track which chars have events
        }

        # Populate last_values from current device states to avoid logging "None -> X" on startup
        for device_id, char_list in self.device_to_characteristics.items():
            current_state = self.state_manager.get_current_state(device_id)
            for aid, iid, char_type in char_list:
                # Map char_type to state field
                char_mapping = {
                    DeviceStateManager.CHAR_CURRENT_TEMPERATURE: 'current_temperature',
                    DeviceStateManager.CHAR_TARGET_TEMPERATURE: 'target_temperature',
                    DeviceStateManager.CHAR_CURRENT_HEATING_COOLING: 'current_heating_cooling_state',
                    DeviceStateManager.CHAR_TARGET_HEATING_COOLING: 'target_heating_cooling_state',
                    DeviceStateManager.CHAR_HEATING_THRESHOLD: 'heating_threshold_temperature',
                    DeviceStateManager.CHAR_COOLING_THRESHOLD: 'cooling_threshold_temperature',
                    DeviceStateManager.CHAR_TEMP_DISPLAY_UNITS: 'temperature_display_units',
                    DeviceStateManager.CHAR_BATTERY_LEVEL: 'battery_level',
                    DeviceStateManager.CHAR_STATUS_LOW_BATTERY: 'status_low_battery',
                    DeviceStateManager.CHAR_CURRENT_HUMIDITY: 'humidity',
                    DeviceStateManager.CHAR_TARGET_HUMIDITY: 'target_humidity',
                    DeviceStateManager.CHAR_ACTIVE: 'active_state',
                    DeviceStateManager.CHAR_VALVE_POSITION: 'valve_position',
                }
                field_name = char_mapping.get(char_type.lower())
                if field_name and field_name in current_state:
                    self.change_tracker['last_values'][(aid, iid)] = current_state[field_name]

        logger.info(f"Initialized change tracker with {len(self.change_tracker['last_values'])} known values from database")

        # Try to set up persistent event system for all pairings
        events_active = await self.setup_persistent_events()

        if not events_active:
            logger.warning("Events not available, falling back to polling")
            await self.setup_polling_system()
        else:
            logger.info("Events active, skipping polling (would just hit 3-hour cache)")

    async def setup_persistent_events(self):
        """Set up persistent event subscriptions across all pairings (bridge + standalone)."""
        try:
            logger.info("Setting up persistent event system...")

            def _make_event_callback(source_label: str):
                def event_callback(update_data: dict[tuple[int, int], dict]):
                    logger.debug(f"Event ({source_label}) received: {update_data}")
                    for k, v in update_data.items():
                        asyncio.create_task(self.handle_change(k[0], k[1], v, source="EVENT"))
                return event_callback

            all_pairings = [self.pairing] + self.extra_pairings
            total_subscribed = 0

            for idx, pairing in enumerate(all_pairings):
                label = "bridge" if idx == 0 else f"accessory-{idx}"
                pairing.dispatcher_connect(_make_event_callback(label))

                chars_for_pairing = []
                for accessory in self.accessories_cache:
                    aid = accessory.get('aid')
                    if self.aid_to_pairing.get(aid) is not pairing:
                        continue
                    for service in accessory.get('services', []):
                        for char in service.get('characteristics', []):
                            perms = char.get('perms', [])
                            if 'ev' in perms:
                                iid = char.get('iid')
                                char_type = char.get('type', '').lower()
                                chars_for_pairing.append((aid, iid))
                                self.characteristic_map[(aid, iid)] = get_characteristic_name(char_type)
                                self.characteristic_iid_map[(aid, get_characteristic_name(char_type))] = iid
                                self.change_tracker['event_characteristics'].add((aid, iid))

                if chars_for_pairing:
                    await pairing.subscribe(chars_for_pairing)
                    self.pairing_subscriptions[id(pairing)] = chars_for_pairing
                    self.subscribed_characteristics.extend(chars_for_pairing)
                    total_subscribed += len(chars_for_pairing)
                    logger.info(f"Subscribed to {len(chars_for_pairing)} events on {label}")

            if total_subscribed:
                logger.info(f"Total event subscriptions: {total_subscribed}")
                return True
            else:
                logger.warning("No event-capable characteristics found")
                return False

        except Exception as e:
            logger.warning(f"Event system setup failed: {e}")
            return False

    def get_iid_from_characteristics(self, aid: int, char_name: str) -> Optional[int]:
        """Helper to find IID from characteristic type in an accessory."""
        char_key = (aid, char_name)
        return self.characteristic_iid_map.get(char_key)

    async def handle_change(self, aid, iid, update_data, source="UNKNOWN"):
        """Unified handler for all characteristic changes (events AND polling)."""
        try:
            # Extract change information
            value = update_data.get('value')
            timestamp = time.time()

            if aid is None or iid is None:
                logger.debug(f"Invalid change data from {source}: {update_data}")
                return

            # Ignore None values - these typically indicate network/connection issues
            # Events will restore the actual values once connection is restored
            if value is None:
                logger.debug(f"[{source}] Ignoring None value for aid={aid} iid={iid} (likely connection issue)")
                return

            # Get characteristic info - try cached first, then lookup
            char_key = (aid, iid)
            char_name = self.characteristic_map.get(char_key)

            # If not in cache, look it up from the accessory data
            if not char_name:
                for accessory in self.accessories_cache:
                    if accessory.get('aid') == aid:
                        for service in accessory.get('services', []):
                            for char in service.get('characteristics', []):
                                if char.get('iid') == iid:
                                    char_type = char.get('type', '').lower()
                                    char_name = get_characteristic_name(char_type)
                                    # Cache it for next time
                                    self.characteristic_map[char_key] = char_name
                                    break
                            if char_name:
                                break
                        break

                # Fallback if still not found
                if not char_name:
                    char_name = f"{aid}.{iid}"

            # Check if this is actually a change
            last_value = self.change_tracker['last_values'].get(char_key)
            if last_value == value:
                return  # No actual change

            # Store new value
            self.change_tracker['last_values'][char_key] = value

            # Get device info for better logging
            device_id = self.accessories_id.get(aid)
            device_info = self.state_manager.get_device_info(device_id) if device_id else {}
            zone_name = device_info.get('zone_name', 'No Zone')
            device_name = device_info.get('name') or device_info.get('serial_number', f'Device {device_id}')
            is_zone_leader = device_info.get('is_zone_leader', False)

            # Update device state manager
            if device_id:
                # Find the accessory by aid (since events come with aid, not device_id)
                accessory = None
                for acc in self.accessories_cache:
                    if acc.get('aid') == aid:
                        accessory = acc
                        break

                if accessory and accessory.get('id'):
                    # Find the characteristic type for this aid/iid
                    char_type = None
                    for service in accessory.get('services', []):
                        for char in service.get('characteristics', []):
                            if char.get('iid') == iid:
                                char_type = char.get('type', '').lower()
                                break
                        if char_type:
                            break

                    if char_type:
                        field_name, old_val, new_val = self.state_manager.update_device_characteristic(
                            accessory['id'], char_type, value, timestamp
                        )
                        if field_name:
                            logger.debug(f"Updated device {accessory['id']} {field_name}: {old_val} -> {new_val}")

            # Skip logging during initialization
            if not self.is_initializing:
                # Track change by source and log with nice format
                src = "E" if source == "EVENT" else "P"
                if source == "EVENT":
                    self.change_tracker['events_received'] += 1
                else:
                    self.change_tracker['polling_changes'] += 1

                # Format log message: show zone name, only add device detail if not zone leader
                if is_zone_leader:
                    # Check for window open/close based on leader updates
                    self._handle_window_open_detection(device_id, device_info, char_type)
                    # Zone leader - just show zone name
                    logger.info(f"[{src}] {zone_name} | {char_name}: {last_value} -> {value}")
                else:
                    # Non-leader device - show zone + device to distinguish multiple devices
                    logger.info(f"[{src}] {zone_name} ({device_name}) | {char_name}: {last_value} -> {value}")

            # Send to event stream for clients (always, even during init)
            # Don't send raw characteristic events anymore - we'll send aggregated state changes

            # Broadcast aggregated state change for relevant characteristics
            if char_name in [
                        'TargetTemperature', 'CurrentTemperature', 'TargetHeatingCoolingState',
                        'CurrentHeatingCoolingState', 'CurrentRelativeHumidity', 'ValvePosition'
                    ]:
                await self.broadcast_state_change(device_id, zone_name)

        except Exception as e:
            logger.error(f"Error handling unified change: {e}")

    def _handle_window_open_detection(self, device_id, device_info, char_type):
        """Detect window open/close based on leader device temperature update."""
        try:
            # Only check for window open/close if the characteristic is relevant (temperature changes in leader)
            if char_type and char_type.lower() in [DeviceStateManager.CHAR_CURRENT_TEMPERATURE]:
                # Get current state for leader device
                leader_state = self.state_manager.get_current_state(device_id)
                if not leader_state:
                    # No state info available
                    return

                zone_name = device_info.get('zone_name', 'No Zone')

                # Simple heuristic: if temperature drops significantly within time threshold, a window open is asumed
                # temp_change_threshold = 2.0  # degrees Celsius
                # temp_drop_time_threshold = 10  # minutes
                temp_change_threshold = 1.0  # degrees Celsius
                temp_change_time_threshold = 20  # minutes

                # Get old values from database of the last temp_drop_time_threshold minutes to compare trends
                history = self.state_manager.get_device_history_info(device_id, age=temp_change_time_threshold)

                # history structure: history_count, earliest_entry (temp, window, window_lastupdate), latest_entry (temp, window, window_lastupdate)
                if not history or history['history_count'] < 1:
                    # No data, just ignore
                    return

                # calculate time difference from latest entry to see how long the window has been open/closed/rest
                time_diff = (time.time() - int(history['latest_entry'][2])) // 60
                current_window_state = leader_state.get('window')

                # If window is currently open (1) and has been open for longer than the open time threshold, set it to rest (2)
                if current_window_state == 1 and time_diff > device_info.get('window_open_time', 15):
                    logger.info(f"[Window] {zone_name} | Window set to close again, being open over {time_diff:.0f} mins")
                    # Consider it closed again -> put in rest (2) state to avoid rapid open/close detection
                    self.state_manager.update_device_window_status(device_id, 2)
                    self._cancel_window_close_timer(device_id)
                    return

                if history['history_count'] < 2:
                    # Temperature drop is to slow to call it an open window
                    logger.info(f"[Window] {zone_name} | Not enough readings (only {history['history_count']} " +
                                f"entry in last {temp_change_time_threshold} minutes)")
                    return

                mode = leader_state.get('cur_heating', 0)   # (0=Off, 1=Heating, 2=Cooling)
                if mode == 2:
                    # In cooling mode, calculate temp _rise_ (lastest - earlier) as temp_change
                    temp_change = history['latest_entry'][0] - history['earliest_entry'][0]
                    logger.info(f"[Window] {zone_name} | cooling | Window status {current_window_state} " +
                                f"for {time_diff:.0f} mins | Temp rise {temp_change:.1f}")
                else:
                    # In heating mode, calculate temp _drop_ (earlier - lastest) as temp_change
                    temp_change = history['earliest_entry'][0] - history['latest_entry'][0]
                    logger.info(f"[Window] {zone_name} | heating | Window status {current_window_state} " +
                                f"for {time_diff:.0f} mins | Temp drop {temp_change:.1f}")

                # check if window is currently closed (0) or in rest (2) long enough to consider it closed again
                if current_window_state == 0 or (current_window_state == 2 and time_diff > device_info.get('window_rest_time', 15)):
                    # A significant temp _rise_ is likely to be caused by an open window
                    window_status = 1 if temp_change >= temp_change_threshold else 0

                    # Update state manager with window status
                    self.state_manager.update_device_window_status(device_id, window_status)

                    # If window is set open (1), schedule a timer to close it again after 'window_open_time' minutes
                    if window_status == 1:
                        window_close_delay = device_info.get('window_open_time', 30)
                        self._schedule_window_close_timer(device_id, window_close_delay, device_info)

        except Exception as e:
            logger.error(f"Error in window open detection: {e}")
            import traceback
            print(traceback.format_exc())

    def _schedule_window_close_timer(self, device_id: int, window_close_delay: int, device_info: Dict[str, Any]):
        """" Schedule a timer to set the window status back to closed after a delay."""
        if self.is_shutting_down:
            return

        existing_task = self.window_close_timers.get(device_id)
        if existing_task and not existing_task.done():
            existing_task.cancel()

        task = asyncio.create_task(self._window_close_handler(device_id, device_info, window_close_delay))
        self.window_close_timers[device_id] = task
        task.add_done_callback(lambda t: self._window_close_timer_stop(device_id, t))

    def _cancel_window_close_timer(self, device_id: int):
        """ Cancel any existing window close timer for the device."""
        task = self.window_close_timers.pop(device_id, None)
        if task and not task.done():
            task.cancel()

    def _window_close_timer_stop(self, device_id: int, task: asyncio.Task):
        """ Callback to clean up after window close timer finishes or is cancelled."""
        if self.window_close_timers.get(device_id) is task:
            del self.window_close_timers[device_id]

    async def _window_close_handler(self, device_id: int, device_info: Dict[str, Any], closing_delay: int):
        """ Wait for the specified delay and then set the window status back to closed if it's still open."""
        interval = closing_delay * 60

        try:
            await asyncio.sleep(interval)
            if self.is_shutting_down:
                return

            current_state = self.state_manager.get_current_state(device_id)
            if not current_state or current_state.get('window') != 1:
                return

            zone_name = device_info.get('zone_name', 'No Zone')
            logger.info(f"[Window] {zone_name} | Window set to close again")
            # Consider it closed again -> put in rest state (2) to avoid rapid open/close detection
            self.state_manager.update_device_window_status(device_id, 2)

        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"Window closing error for device {device_id}: {e}")
            return

    async def broadcast_event(self, event_data):
        """Broadcast change event to all connected SSE clients."""
        try:
            event_json = json.dumps(event_data)
            event_message = f"data: {event_json}\n\n"

            # Determine which listeners should receive this event
            if event_data.get('type') == 'zone':
                # Zone events go to both all-events and zone-only listeners
                target_listeners = self.event_listeners + self.zone_event_listeners
            else:
                # Device and other events only go to all-events listeners
                target_listeners = self.event_listeners

            # Send to all connected event listeners
            disconnected_listeners = []
            for listener in target_listeners:
                try:
                    await listener.put(event_message)
                except Exception as e:
                    logger.info(f"Failed to add msg to queue: Disconnect listener {e}")
                    disconnected_listeners.append(listener)

            # Remove disconnected listeners
            for listener in disconnected_listeners:
                if listener in self.event_listeners:
                    self.event_listeners.remove(listener)
                if listener in self.zone_event_listeners:
                    self.zone_event_listeners.remove(listener)

        except Exception as e:
            logger.error(f"Error broadcasting event: {e}")

    def _celsius_to_fahrenheit(self, celsius: float) -> float:
        """Convert Celsius to Fahrenheit."""
        if celsius is None:
            return None
        return round(celsius * 9/5 + 32, 1)

    def _build_device_state(self, device_id: int) -> dict:
        """Build a standardized device state dictionary."""
        state = self.state_manager.get_current_state(device_id)
        device_info = self.state_manager.device_info_cache.get(device_id, {})

        cur_temp_c = state.get('current_temperature')
        target_temp_c = state.get('target_temperature')

        # Determine battery_low from Cloud API battery_state (cached, no extra DB query)
        battery_state = device_info.get('battery_state')
        battery_low = battery_state is not None and battery_state != 'NORMAL'

        return {
            'cur_temp_c': cur_temp_c,
            'cur_temp_f': self._celsius_to_fahrenheit(cur_temp_c) if cur_temp_c is not None else None,
            'hum_perc': state.get('humidity'),
            'target_temp_c': target_temp_c,
            'target_temp_f': self._celsius_to_fahrenheit(target_temp_c) if target_temp_c is not None else None,
            'mode': state.get('target_heating_cooling_state', 0),  # 0=Off, 1=Heat, 2=Cool, 3=Auto
            'cur_heating': 1 if state.get('current_heating_cooling_state') == 1 else 0,
            'valve_position': state.get('valve_position'),
            'battery_low': battery_low,
            'window': state.get('window'),
        }

    async def broadcast_state_change(self, device_id: int, zone_name: str):
        """
        Broadcast device and zone state change events.

        Sends standardized state updates for both the device and its zone (if assigned).
        """
        try:
            # Get device info from cache
            device_info = self.state_manager.get_device_info(device_id)
            if not device_info:
                return

            serial = device_info.get('serial_number')
            zone_id = device_info.get('zone_id')

            # Build device state
            device_state = self._build_device_state(device_id)

            # Broadcast device state change
            device_event = {
                'type': 'device',
                'device_id': device_id,
                'serial': serial,
                'zone_name': zone_name,
                'state': device_state,
                'timestamp': time.time()
            }
            await self.broadcast_event(device_event)

            # Also broadcast zone state if device is assigned to a zone
            if zone_id and zone_id in self.state_manager.zone_cache:
                zone_info = self.state_manager.zone_cache[zone_id]
                zone_name = zone_info['name']
                leader_device_id = zone_info['leader_device_id']
                is_circuit_driver = zone_info['is_circuit_driver']

                # Get leader state for zone
                if leader_device_id:
                    leader_state = self._build_device_state(leader_device_id)

                    # Build zone state using zone logic
                    zone_state = {
                        'cur_temp_c': leader_state['cur_temp_c'],
                        'cur_temp_f': leader_state['cur_temp_f'],
                        'hum_perc': leader_state['hum_perc'],
                        'target_temp_c': leader_state['target_temp_c'],
                        'target_temp_f': leader_state['target_temp_f'],
                        'mode': 0,
                        'cur_heating': 0,
                        'window_open': leader_state['window'] == 1,
                    }

                    # Apply circuit driver logic for heating states (using cache)
                    if is_circuit_driver:
                        # Circuit driver - check radiator valves in zone (from cache)
                        other_devices = [
                                    dev_id for dev_id, dev_info in self.state_manager.device_info_cache.items()
                                    if dev_info.get('zone_id') == zone_id and not dev_info.get('is_circuit_driver')
                                ]

                        if other_devices:
                            for valve_id in other_devices:
                                valve_state = self._build_device_state(valve_id)
                                if valve_state and valve_state.get('mode') == 1:
                                    zone_state['mode'] = 1
                                if valve_state and valve_state.get('cur_heating') == 1:
                                    zone_state['cur_heating'] = 1
                        else:
                            # Circuit driver alone in zone - use its own state
                            zone_state['mode'] = leader_state['mode']
                            zone_state['cur_heating'] = leader_state['cur_heating']
                    else:
                        # Regular device - use leader state
                        zone_state['mode'] = leader_state['mode']
                        zone_state['cur_heating'] = leader_state['cur_heating']

                    # Only broadcast if zone state actually changed
                    last_zone_state = self.last_zone_states.get(zone_id)
                    if last_zone_state != zone_state:
                        self.last_zone_states[zone_id] = zone_state.copy()

                        # Broadcast zone state change
                        zone_event = {
                            'type': 'zone',
                            'zone_id': zone_id,
                            'zone_name': zone_name,
                            'state': zone_state,
                            'timestamp': time.time()
                        }
                        await self.broadcast_event(zone_event)

        except Exception as e:
            logger.debug(f"Error broadcasting state change: {e}")

    async def setup_polling_system(self):
        """Setup polling system for comparison with events."""
        try:
            # Find all interesting characteristics for polling (not just temperature)
            self.poll_chars = []

            for accessory in self.accessories_cache:
                aid = accessory["aid"]
                for service in accessory["services"]:
                    for char in service["characteristics"]:
                        perms = char.get("perms", [])

                        # Poll the characteristics that support polling and events
                        if "ev" in perms and "pr" in perms:
                            iid = char["iid"]
                            self.poll_chars.append((aid, iid))

            if self.poll_chars:
                logger.info(f"Found {len(self.poll_chars)} characteristics for polling")
                # Store for the polling loop to use
                self.monitored_characteristics = self.poll_chars
                # Start background polling task and track it
                task = asyncio.create_task(self.background_polling_loop())
                self.background_tasks.append(task)
                logger.info("Background polling system started")
            else:
                logger.warning("No characteristics found for polling")

        except Exception as e:
            logger.warning(f"Failed to setup polling system: {e}")

    async def background_polling_loop(self):
        """Background task that polls all monitored characteristics.

        Uses intelligent polling intervals:
        - Fast poll (60s) for characteristics that have events but might not fire reliably
        - Slow poll (120s) as safety net for everything else
        """
        fast_poll_interval = 60  # 1 minute for priority characteristics
        slow_poll_interval = 120  # 2 minutes for everything

        # Track characteristics that need faster polling (like humidity)
        priority_chars = set()

        # Identify priority characteristics (humidity, battery, etc.)
        for aid, iid in self.monitored_characteristics:
            char_key = (aid, iid)
            char_name = self.characteristic_map.get(char_key, "")

            # Add humidity to priority list
            if 'humidity' in char_name.lower():
                priority_chars.add((aid, iid))

        if priority_chars:
            logger.info(f"Fast polling ({fast_poll_interval}s) for {len(priority_chars)} characteristics")
            logger.info(f"Normal polling ({slow_poll_interval}s) for {len(self.monitored_characteristics) - len(priority_chars)} characteristics")

        last_fast_poll = 0
        last_slow_poll = 0

        while not self.is_shutting_down:
            try:
                current_time = time.time()
                await asyncio.sleep(10)  # Check every 10 seconds

                if not self.pairing or not hasattr(self, 'monitored_characteristics'):
                    continue

                # Fast poll priority characteristics
                if current_time - last_fast_poll >= fast_poll_interval and priority_chars:
                    logger.debug(f"Fast polling {len(priority_chars)} priority characteristics")
                    await self._poll_characteristics(list(priority_chars), "FAST-POLL")
                    last_fast_poll = current_time

                # Slow poll all characteristics
                if current_time - last_slow_poll >= slow_poll_interval:
                    logger.debug(f"Slow polling {len(self.monitored_characteristics)} characteristics")
                    await self._poll_characteristics(self.monitored_characteristics, "POLLING")
                    last_slow_poll = current_time

            except Exception as e:
                logger.error(f"Background polling error: {e}")
                await asyncio.sleep(5)  # Short delay before retrying

    async def _poll_characteristics(self, char_list, source="POLLING"):
        """Poll a list of characteristics and process changes."""
        from collections import defaultdict as _dd
        by_pairing: Dict[int, List] = _dd(list)
        for item in char_list:
            by_pairing[id(self.aid_to_pairing.get(item[0], self.pairing))].append(item)

        batch_size = 15

        for pairing_id, items in by_pairing.items():
            pairing = self.aid_to_pairing.get(items[0][0], self.pairing)
            for i in range(0, len(items), batch_size):
                batch = items[i:i+batch_size]

                try:
                    results = await pairing.get_characteristics(batch)

                    for aid, iid in batch:
                        if (aid, iid) in results:
                            char_data = results[(aid, iid)]
                            value = char_data.get('value')
                            update_data = {'value': value}
                            await self.handle_change(aid, iid, update_data, source)

                except Exception as e:
                    logger.error(f"Error polling batch: {e}")

    async def handle_homekit_event(self, event_data):
        """Handle incoming HomeKit events and update device states."""
        try:
            # Update device states from HomeKit events (if any events still come through)
            aid = event_data.get('aid')
            iid = event_data.get('iid')
            value = event_data.get('value')

            if aid and iid and value is not None:
                self.device_states[str(aid)][str(iid)] = {
                    'value': value,
                    'timestamp': time.time()
                }

                # Notify event listeners (for SSE)
                for queue in self.event_listeners:
                    try:
                        await queue.put(event_data)
                    except Exception as e:
                        logger.info(f"Queue might be closed {e}")
                        pass  # Queue might be closed

                logger.debug(f"Updated device state from event: aid={aid}, iid={iid}, value={value}")

        except Exception as e:
            logger.error(f"Error handling HomeKit event: {e}")

    async def set_device_characteristics(self, device_id: int, char_updates: Dict[str, Any]) -> bool:
        """
        Set characteristics for a device.

        Args:
            device_id: Database device ID
            char_updates: Dict mapping characteristic UUIDs to values
                         e.g., {'target_temperature': 21.0, 'target_heating_cooling_state': 1}

        Returns:
            True if successful

        Raises:
            ValueError if device not found or characteristics not writable
        """
        if not self.pairing:
            raise ValueError("Bridge not connected")

        # Get device info from in-memory cache
        device_info = self.state_manager.get_device_info(device_id)
        if not device_info:
            raise ValueError(f"Device {device_id} not found")

        aid = device_info.get('aid')
        if not aid:
            # Cache might be stale, try reloading
            logger.info(f"Device {device_id} has no aid in cache, reloading device cache...")
            self.state_manager._load_device_cache()
            device_info = self.state_manager.get_device_info(device_id)
            aid = device_info.get('aid') if device_info else None

            if not aid:
                raise ValueError(f"Device {device_id} has no HomeKit accessory ID (aid)")

        # Map characteristic names to UUIDs
        char_uuid_map = {
            'target_temperature': DeviceStateManager.CHAR_TARGET_TEMPERATURE,
            'target_heating_cooling_state': DeviceStateManager.CHAR_TARGET_HEATING_COOLING,
            'target_humidity': DeviceStateManager.CHAR_TARGET_HUMIDITY,
        }

        # Find the characteristic IIDs in the accessory
        characteristics_to_set = []
        for char_name, value in char_updates.items():
            char_uuid = char_uuid_map.get(char_name)
            if not char_uuid:
                logger.warning(f"Unknown characteristic: {char_name}")
                continue

            # Find the IID for this characteristic
            for acc in self.accessories_cache:
                if acc.get('aid') == aid:
                    for service in acc.get('services', []):
                        for char in service.get('characteristics', []):
                            if char.get('type').lower() == char_uuid.lower():
                                iid = char.get('iid')
                                if iid:
                                    characteristics_to_set.append((aid, iid, value))
                                    logger.info(f"Setting {char_name} on device {device_id} (aid={aid}, iid={iid}) to {value}")
                                break

        if not characteristics_to_set:
            raise ValueError("No valid characteristics to set")

        # Set the characteristics via the correct pairing
        target_pairing = self.aid_to_pairing.get(aid, self.pairing)
        logger.debug(f"Sending to HomeKit: {characteristics_to_set}")
        await target_pairing.put_characteristics(characteristics_to_set)
        return True
