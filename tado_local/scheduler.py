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

"""Temperature scheduler service for zones."""

import asyncio
import datetime
import logging
import sqlite3
import time
from typing import Dict, List, Optional, Any

try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Fallback for Python < 3.9
    try:
        from backports.zoneinfo import ZoneInfo
    except ImportError:
        ZoneInfo = None

logger = logging.getLogger(__name__)


def get_timezone(db_path: str) -> str:
    """Get timezone from app_config table, defaulting to UTC."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT config_value FROM app_config WHERE config_key = 'timezone'")
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
    except Exception as e:
        logger.debug(f"Error getting timezone from database: {e}")
    return "UTC"


def get_local_now(db_path: str) -> datetime.datetime:
    """Get current local time based on configured timezone."""
    timezone_str = get_timezone(db_path)
    try:
        if ZoneInfo:
            tz = ZoneInfo(timezone_str)
            return datetime.datetime.now(tz)
        else:
            # Fallback: use UTC if zoneinfo not available
            logger.warning("zoneinfo not available, using UTC")
            return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
    except Exception as e:
        logger.warning(f"Invalid timezone '{timezone_str}', falling back to UTC: {e}")
        return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)


def round_to_5_minutes(dt: datetime.datetime) -> datetime.datetime:
    """Round datetime down to nearest 5-minute interval."""
    minutes = dt.minute
    rounded_minutes = (minutes // 5) * 5
    return dt.replace(minute=rounded_minutes, second=0, microsecond=0)


class SchedulerService:
    """Background service that applies scheduled temperatures to zones."""
    
    def __init__(self, db_path: str, tado_api):
        """Initialize scheduler service.
        
        Args:
            db_path: Path to SQLite database
            tado_api: TadoLocalAPI instance for applying temperatures
        """
        self.db_path = db_path
        self.tado_api = tado_api
        self._task: Optional[asyncio.Task] = None
        self._is_running = False
        self._timezone_cache: Optional[str] = None
        self._timezone_cache_time: float = 0
        self._timezone_cache_ttl = 3600  # Refresh timezone cache every hour
        self._last_applied_schedules: Dict[tuple, float] = {}  # Track (zone_id, schedule_id, time_str) -> timestamp
    
    def get_timezone(self) -> str:
        """Get timezone from database with caching."""
        now = time.time()
        if self._timezone_cache is None or (now - self._timezone_cache_time) > self._timezone_cache_ttl:
            self._timezone_cache = get_timezone(self.db_path)
            self._timezone_cache_time = now
        return self._timezone_cache
    
    def get_local_now(self) -> datetime.datetime:
        """Get current local time based on configured timezone."""
        timezone_str = self.get_timezone()
        try:
            if ZoneInfo:
                tz = ZoneInfo(timezone_str)
                return datetime.datetime.now(tz)
            else:
                logger.warning("zoneinfo not available, using UTC")
                return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
        except Exception as e:
            logger.warning(f"Invalid timezone '{timezone_str}', falling back to UTC: {e}")
            return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
    
    def start(self):
        """Start the background scheduler task."""
        if self._task and not self._task.done():
            logger.warning("Scheduler service already running")
            return
        
        self._is_running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        self.tado_api.background_tasks.append(self._task)
        logger.info("Temperature scheduler service started")
    
    async def stop(self):
        """Stop the scheduler service gracefully."""
        self._is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Temperature scheduler service stopped")
    
    async def _scheduler_loop(self):
        """Main scheduler loop that runs every minute to check for scheduled changes."""
        try:
            # On startup, check and apply current scheduled temperature for AUTO zones
            await self._check_and_apply_schedules(startup=True)
            
            # Align with minute boundaries (run at 0 seconds of each minute)
            # Calculate sleep time until next minute boundary
            local_now = self.get_local_now()
            # Get next minute boundary (current minute + 1, seconds = 0)
            next_minute = local_now.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
            sleep_seconds = (next_minute - local_now).total_seconds()
            
            if sleep_seconds > 0:
                logger.debug(f"Scheduler: Aligning with minute boundary, sleeping {sleep_seconds:.2f} seconds until {next_minute.strftime('%H:%M:%S')}")
                await asyncio.sleep(sleep_seconds)
            
            # Run every minute at the start of each minute (0 seconds)
            # This ensures we check at the exact 5-minute intervals (00, 05, 10, 15, etc.)
            while self._is_running and not self.tado_api.is_shutting_down:
                if not self.tado_api.is_shutting_down:
                    await self._check_and_apply_schedules()
                
                # Sleep until next minute boundary
                local_now = self.get_local_now()
                next_minute = local_now.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
                sleep_seconds = (next_minute - local_now).total_seconds()
                
                # Ensure we sleep at least 1 second (in case of timing issues)
                if sleep_seconds < 1.0:
                    sleep_seconds = 60.0
                
                await asyncio.sleep(sleep_seconds)
        except asyncio.CancelledError:
            logger.info("Scheduler loop cancelled")
        except Exception as e:
            logger.error(f"Error in scheduler loop: {e}", exc_info=True)
            # Continue running even on error
            if self._is_running:
                # On error, wait until next minute boundary before retrying
                try:
                    local_now = self.get_local_now()
                    next_minute = local_now.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
                    sleep_seconds = (next_minute - local_now).total_seconds()
                    if sleep_seconds < 0.1:
                        sleep_seconds = 60.0
                    elif sleep_seconds < 0.5:
                        sleep_seconds = 0.5
                    await asyncio.sleep(sleep_seconds)
                except Exception:
                    # Fallback to 60 seconds if calculation fails
                    await asyncio.sleep(60)
                if not self.tado_api.is_shutting_down:
                    asyncio.create_task(self._scheduler_loop())
    
    async def _check_and_apply_schedules(self, startup: bool = False):
        """Check all zones and apply matching schedules."""
        try:
            local_now = self.get_local_now()
            rounded_time = round_to_5_minutes(local_now)
            current_day = rounded_time.weekday()  # Monday=0, Sunday=6
            current_time_str = rounded_time.strftime("%H:%M")
            day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
            current_day_name = day_names[current_day]
            
            # Only process if we're at a 5-minute boundary (00, 05, 10, 15, etc.)
            # This prevents duplicate processing when checking every minute
            current_minute = local_now.minute
            if current_minute % 5 != 0 and not startup:
                return  # Skip if not at a 5-minute boundary (except on startup)
            
            if startup:
                logger.info(f"Scheduler: Checking schedules on startup at {current_time_str} ({current_day_name})")
            else:
                logger.info(f"Scheduler: Checking schedules at {current_time_str} ({current_day_name})")
            
            # Get all enabled schedules matching current time/day
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            
            # Query for matching schedules
            schedules = []
            
            # Type 3: all_day
            cursor = conn.execute("""
                SELECT schedule_id, zone_id, temperature
                FROM zone_schedules
                WHERE enabled = 1
                  AND schedule_type = 3
                  AND time = ?
            """, (current_time_str,))
            schedules.extend(cursor.fetchall())
            
            # Type 2: weekday_weekend
            is_weekday = current_day < 5  # Monday=0 to Friday=4
            day_type = 'weekday' if is_weekday else 'weekend'
            cursor = conn.execute("""
                SELECT schedule_id, zone_id, temperature
                FROM zone_schedules
                WHERE enabled = 1
                  AND schedule_type = 2
                  AND day_type = ?
                  AND time = ?
            """, (day_type, current_time_str))
            schedules.extend(cursor.fetchall())
            
            # Type 1: day_of_week
            cursor = conn.execute("""
                SELECT schedule_id, zone_id, temperature
                FROM zone_schedules
                WHERE enabled = 1
                  AND schedule_type = 1
                  AND day_of_week = ?
                  AND time = ?
            """, (current_day, current_time_str))
            schedules.extend(cursor.fetchall())
            
            conn.close()
            
            if not schedules:
                if startup:
                    logger.info("Scheduler: No matching schedules found on startup")
                else:
                    logger.debug(f"Scheduler: No matching schedules found at {current_time_str}")
                return
            
            logger.info(f"Scheduler: Found {len(schedules)} matching schedule(s) at {current_time_str}")
            
            # Apply schedules for zones in AUTO mode
            current_timestamp = time.time()
            applied_count = 0
            skipped_count = 0
            for schedule in schedules:
                zone_id = schedule['zone_id']
                schedule_id = schedule['schedule_id']
                temperature = schedule['temperature']
                
                # Check if we've already applied this schedule in the current 5-minute window
                schedule_key = (zone_id, schedule_id, current_time_str)
                last_applied = self._last_applied_schedules.get(schedule_key, 0)
                # Only apply if not applied in the last 4 minutes (to avoid duplicates)
                if current_timestamp - last_applied < 240:
                    continue
                
                # Check if zone is in AUTO mode
                tracked_mode = self.tado_api.state_manager.get_zone_tracked_mode(zone_id)
                if tracked_mode != 3:  # Not AUTO
                    if startup:
                        logger.debug(f"Zone {zone_id} not in AUTO mode (tracked_mode={tracked_mode}), skipping schedule")
                    continue
                
                # Get zone info from cache
                zone_info = self.tado_api.state_manager.zone_cache.get(zone_id)
                zone_name = zone_info.get('name') if zone_info else f'Zone {zone_id}'
                
                # Get zone leader device ID
                if not zone_info or not zone_info.get('leader_device_id'):
                    logger.warning(f"Scheduler: {zone_name} (zone {zone_id}) has no leader device, skipping schedule {schedule_id}")
                    skipped_count += 1
                    continue
                
                leader_device_id = zone_info['leader_device_id']
                
                # Mark as scheduled change BEFORE applying (with longer timeout to handle async updates)
                # Use a longer timeout (30 seconds) to account for async HomeKit updates
                self.tado_api.state_manager.mark_scheduled_change(zone_id)
                
                logger.info(f"Scheduler: Applying schedule {schedule_id} to {zone_name} (zone {zone_id}): {temperature}°C at {current_time_str}")
                
                # Apply temperature using the API method (which handles all HomeKit interaction)
                # This is marked as a scheduled change, so it won't trigger mode switching from AUTO to HEAT
                try:
                    logger.debug(f"Scheduler: Setting temperature {temperature}°C for device {leader_device_id} via API")
                    
                    # Use the API method which handles all the HomeKit complexity
                    # The scheduled change flag is already set above, so this won't exit AUTO mode
                    await self.tado_api.set_device_characteristics(
                        leader_device_id,
                        {'target_temperature': temperature}
                    )
                    
                    # Mark this schedule as applied
                    self._last_applied_schedules[schedule_key] = current_timestamp
                    
                    # Clean up old entries (older than 10 minutes)
                    cutoff_time = current_timestamp - 600
                    self._last_applied_schedules = {
                        k: v for k, v in self._last_applied_schedules.items()
                        if v > cutoff_time
                    }
                    
                    logger.info(f"Scheduler: Successfully applied {temperature}°C to {zone_name} (zone {zone_id}) from schedule {schedule_id}")
                    applied_count += 1
                except Exception as e:
                    logger.error(f"Scheduler: Error applying schedule {schedule_id} to {zone_name} (zone {zone_id}): {e}", exc_info=True)
                    skipped_count += 1
            
            # Log summary
            if applied_count > 0 or skipped_count > 0:
                logger.info(f"Scheduler: Completed check at {current_time_str} - Applied: {applied_count}, Skipped: {skipped_count}, Total: {len(schedules)}")
        
        except Exception as e:
            logger.error(f"Scheduler: Error checking schedules at {current_time_str}: {e}", exc_info=True)
    
    def get_current_schedule_temperature(self, zone_id: int, current_time: Optional[datetime.datetime] = None) -> Optional[float]:
        """Get current scheduled temperature for a zone.
        
        Args:
            zone_id: Zone ID to check
            current_time: Optional datetime to check (defaults to current local time)
        
        Returns:
            Temperature in °C if a schedule matches, None otherwise
        """
        try:
            if current_time is None:
                current_time = self.get_local_now()
            
            rounded_time = round_to_5_minutes(current_time)
            current_day = rounded_time.weekday()
            current_time_str = rounded_time.strftime("%H:%M")
            
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            
            # Check all schedule types
            temperature = None
            
            # Type 3: all_day
            cursor = conn.execute("""
                SELECT temperature
                FROM zone_schedules
                WHERE zone_id = ? AND enabled = 1
                  AND schedule_type = 3
                  AND time = ?
                ORDER BY schedule_id DESC
                LIMIT 1
            """, (zone_id, current_time_str))
            row = cursor.fetchone()
            if row:
                temperature = row['temperature']
            
            # Type 2: weekday_weekend
            if temperature is None:
                is_weekday = current_day < 5
                day_type = 'weekday' if is_weekday else 'weekend'
                cursor = conn.execute("""
                    SELECT temperature
                    FROM zone_schedules
                    WHERE zone_id = ? AND enabled = 1
                      AND schedule_type = 2
                      AND day_type = ?
                      AND time = ?
                    ORDER BY schedule_id DESC
                    LIMIT 1
                """, (zone_id, day_type, current_time_str))
                row = cursor.fetchone()
                if row:
                    temperature = row['temperature']
            
            # Type 1: day_of_week
            if temperature is None:
                cursor = conn.execute("""
                    SELECT temperature
                    FROM zone_schedules
                    WHERE zone_id = ? AND enabled = 1
                      AND schedule_type = 1
                      AND day_of_week = ?
                      AND time = ?
                    ORDER BY schedule_id DESC
                    LIMIT 1
                """, (zone_id, current_day, current_time_str))
                row = cursor.fetchone()
                if row:
                    temperature = row['temperature']
            
            conn.close()
            return temperature
        
        except Exception as e:
            logger.error(f"Error getting current schedule temperature for zone {zone_id}: {e}", exc_info=True)
            return None

    def get_latest_schedule_temperature(self, zone_id: int, current_time: Optional[datetime.datetime] = None) -> Optional[float]:
        """Get the most recent scheduled temperature for a zone, looking back through today and yesterday.
        
        This finds the most recent schedule that would have been active, even if it was hours ago
        or from the previous day. Used when switching to AUTO mode to apply the last scheduled temperature.
        
        Args:
            zone_id: Zone ID to check
            current_time: Optional datetime to check (defaults to current local time)
        
        Returns:
            Temperature in °C from the most recent schedule, None if no schedules found
        """
        try:
            if current_time is None:
                current_time = self.get_local_now()
            
            current_day = current_time.weekday()  # Monday=0, Sunday=6
            current_time_str = current_time.strftime("%H:%M")
            current_time_minutes = current_time.hour * 60 + current_time.minute
            
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            
            # Get all enabled schedules for this zone
            cursor = conn.execute("""
                SELECT schedule_id, schedule_type, day_of_week, day_type, time, temperature
                FROM zone_schedules
                WHERE zone_id = ? AND enabled = 1
                ORDER BY schedule_type, day_of_week, day_type, time
            """, (zone_id,))
            
            schedules = cursor.fetchall()
            conn.close()
            
            if not schedules:
                return None
            
            # Find the most recent schedule that would have been active
            # First check today, then yesterday if nothing found
            best_temperature = None
            best_time_minutes = -1
            best_schedule_id = None
            best_schedule_time = None
            
            # First, check schedules from today
            check_day = current_day
            is_weekday = check_day < 5
            day_type = 'weekday' if is_weekday else 'weekend'
            
            for schedule in schedules:
                schedule_type = schedule['schedule_type']
                schedule_time = schedule['time']
                schedule_time_parts = schedule_time.split(':')
                schedule_time_minutes = int(schedule_time_parts[0]) * 60 + int(schedule_time_parts[1])
                
                # Check if this schedule would apply to today
                matches = False
                
                if schedule_type == 3:  # all_day
                    matches = True
                elif schedule_type == 2:  # weekday_weekend
                    if schedule['day_type'] == day_type:
                        matches = True
                elif schedule_type == 1:  # day_of_week
                    if schedule['day_of_week'] == check_day:
                        matches = True
                
                if matches:
                    # Today - schedule must be before or equal to current time
                    if schedule_time_minutes <= current_time_minutes:
                        if schedule_time_minutes > best_time_minutes:
                            best_time_minutes = schedule_time_minutes
                            best_temperature = schedule['temperature']
                            best_schedule_id = schedule['schedule_id']
                            best_schedule_time = schedule_time
            
            # If nothing found from today, check yesterday
            if best_time_minutes < 0:
                yesterday_day = (current_day - 1) % 7
                is_weekday_yesterday = yesterday_day < 5
                day_type_yesterday = 'weekday' if is_weekday_yesterday else 'weekend'
                
                for schedule in schedules:
                    schedule_type = schedule['schedule_type']
                    schedule_time = schedule['time']
                    schedule_time_parts = schedule_time.split(':')
                    schedule_time_minutes = int(schedule_time_parts[0]) * 60 + int(schedule_time_parts[1])
                    
                    # Check if this schedule would apply to yesterday
                    matches = False
                    
                    if schedule_type == 3:  # all_day
                        matches = True
                    elif schedule_type == 2:  # weekday_weekend
                        if schedule['day_type'] == day_type_yesterday:
                            matches = True
                    elif schedule_type == 1:  # day_of_week
                        if schedule['day_of_week'] == yesterday_day:
                            matches = True
                    
                    if matches:
                        # Yesterday - find the most recent one (highest time)
                        if schedule_time_minutes > best_time_minutes:
                            best_time_minutes = schedule_time_minutes
                            best_temperature = schedule['temperature']
                            best_schedule_id = schedule['schedule_id']
                            best_schedule_time = schedule_time
            
            if best_temperature is not None:
                if best_schedule_time:
                    logger.info(f"Scheduler: Found latest schedule {best_schedule_id} for zone {zone_id}: {best_temperature}°C (from {best_schedule_time})")
                else:
                    logger.info(f"Scheduler: Found latest schedule {best_schedule_id} for zone {zone_id}: {best_temperature}°C")
            
            return best_temperature
        
        except Exception as e:
            logger.error(f"Error getting latest schedule temperature for zone {zone_id}: {e}", exc_info=True)
            return None
