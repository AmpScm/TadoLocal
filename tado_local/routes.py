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

"""FastAPI route handlers for Tado Local."""

import asyncio
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.responses import StreamingResponse, FileResponse, PlainTextResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles

from .__version__ import __version__
from .homekit_uuids import enhance_accessory_data

# Configure logging
logger = logging.getLogger(__name__)

# Security
security = HTTPBearer(auto_error=False)

# API key configuration (from environment variable)
# Multiple keys can be specified, space-separated
API_KEYS_RAW = os.environ.get('TADO_API_KEYS', '').strip()
API_KEYS = set(key.strip() for key in API_KEYS_RAW.split() if key.strip()) if API_KEYS_RAW else set()

MODE_LABELS = {
    0: "OFF",
    1: "HEAT",
    2: "COOL"
}


def get_api_key(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> Optional[str]:
    """
    Validate API key from Authorization header.

    If API keys are configured (TADO_API_KEYS environment variable), checks Bearer token.
    If no API keys are configured, authentication is disabled (backward compatible).

    Returns:
        The validated API key, or None if authentication is disabled

    Raises:
        HTTPException 401 if authentication fails
    """
    # If no API keys configured, authentication is disabled
    if not API_KEYS:
        return None

    # API keys are configured, so authentication is required
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check if the provided token matches any configured key
    if credentials.credentials not in API_KEYS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return credentials.credentials


def create_app():
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Tado Local",
        description="Local REST API for Tado devices via HomeKit bridge",
        version=__version__
    )

    # Log authentication status
    if API_KEYS:
        logger.info(f"API authentication enabled ({len(API_KEYS)} key(s) configured)")
    else:
        logger.info("API authentication disabled (no TADO_API_KEYS configured)")

    # Mount static files
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


def register_routes(app: FastAPI, get_tado_api):
    """Register all API routes.

    Args:
        app: FastAPI application instance
        get_tado_api: Callable that returns the current TadoLocalAPI instance
    """

    @app.get("/", include_in_schema=False)
    async def root():
        """Serve the web UI."""
        static_dir = Path(__file__).parent / "static"
        index_file = static_dir / "index.html"

        if index_file.exists():
            return FileResponse(index_file, media_type="text/html")
        else:
            # Fallback to API info if web UI not found
            return {
                "service": "Tado Local",
                "description": "Local REST API for Tado devices via HomeKit bridge",
                "version": __version__,
                "documentation": "/docs",
                "api_info": "/api",
                "note": "Web UI not found. Install static/index.html or visit /api for API details"
            }

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        """Serve favicon."""
        static_dir = Path(__file__).parent / "static"
        favicon_svg = static_dir / "favicon.svg"

        if favicon_svg.exists():
            return FileResponse(favicon_svg, media_type="image/svg+xml")
        else:
            raise HTTPException(status_code=404, detail="Favicon not found")

    @app.get("/robots.txt", include_in_schema=False)
    async def robots():
        """Serve robots.txt."""
        static_dir = Path(__file__).parent / "static"
        robots_file = static_dir / "robots.txt"

        if robots_file.exists():
            return FileResponse(robots_file, media_type="text/plain")
        else:
            # Fallback if file not found (make sure is sent as plain text not json)
            return PlainTextResponse("User-agent: *\nDisallow: /\n")

    @app.get("/.well-known/{path:path}", include_in_schema=False)
    async def well_known(path: str):
        """Stub for .well-known requests to prevent 404 logs."""
        # Return 404 but gracefully (no need to log these)
        raise HTTPException(status_code=404, detail="Not found")

    @app.get("/api", tags=["Info"])
    async def api_info(api_key: Optional[str] = Depends(get_api_key)):
        """API root with diagnostics and navigation."""
        return {
            "service": "Tado Local",
            "description": "Local REST API for Tado devices via HomeKit bridge",
            "version": __version__,
            "documentation": "/docs",
            "web_ui": "/",
            "endpoints": {
                "status": "/status",
                "devices": "/devices",
                "zones": "/zones",
                "thermostats": "/thermostats",
                "events": "/events",
                "accessories": "/accessories",
                "refresh": "/refresh",
                "refresh_cloud": "/refresh/cloud"
            }
        }

    @app.get("/status", tags=["Status"])
    async def get_status(api_key: Optional[str] = Depends(get_api_key)):
        """Get overall system status."""
        tado_api = get_tado_api()
        if not tado_api or not tado_api.pairing:
            raise HTTPException(status_code=503, detail="Bridge not connected")

        try:
            # Test connection
            await tado_api.pairing.list_accessories_and_characteristics()

            devices = tado_api.state_manager.get_all_devices()

            status = {
                "status": "connected",
                "version": __version__,
                "bridge_connected": True,
                "last_update": tado_api.last_update,
                "cached_accessories": len(tado_api.accessories_cache),
                "tracked_devices": len(devices),
                "active_listeners": len(tado_api.event_listeners),
                "events_received": tado_api.change_tracker.get('events_received', 0),
                "polling_changes": tado_api.change_tracker.get('polling_changes', 0),
                "uptime": time.time() - (tado_api.last_update or time.time())
            }

            # Add cloud API status if available
            if hasattr(tado_api, 'cloud_api') and tado_api.cloud_api:
                cloud = tado_api.cloud_api
                cloud_status = {
                    "enabled": True,
                    "authenticated": cloud.is_authenticated(),
                    "home_id": cloud.home_id,
                }

                # Add token expiry info if authenticated
                if cloud.is_authenticated():
                    cloud_status["token_expires_at"] = cloud.token_expires_at
                    cloud_status["token_expires_in"] = int(cloud.token_expires_at - time.time()) if cloud.token_expires_at else None

                # Add rate limit info if available
                if cloud.rate_limit and cloud.rate_limit.granted_calls:
                    cloud_status["rate_limit"] = cloud.rate_limit.to_dict()

                # Add authentication info if currently authenticating
                if cloud.is_authenticating and cloud.auth_verification_uri:
                    cloud_status["authentication_required"] = True
                    cloud_status["verification_uri"] = cloud.auth_verification_uri
                    cloud_status["user_code"] = cloud.auth_user_code
                    cloud_status["auth_expires_at"] = cloud.auth_expires_at
                    cloud_status["auth_expires_in"] = int(cloud.auth_expires_at - time.time()) if cloud.auth_expires_at else None
                    cloud_status["message"] = f"Visit {cloud.auth_verification_uri} to authenticate"
                elif not cloud.is_authenticated():
                    cloud_status["authentication_required"] = True
                    cloud_status["message"] = "Authentication will start automatically"

                status["cloud_api"] = cloud_status
            else:
                status["cloud_api"] = {
                    "enabled": False,
                    "authenticated": False
                }

            return status
        except Exception as e:
            return {
                "status": "error",
                "bridge_connected": False,
                "error": str(e)
            }

    @app.get("/accessories", tags=["HomeKit"])
    async def get_accessories(enhanced: bool = True, api_key: Optional[str] = Depends(get_api_key)):
        """
        Get all HomeKit accessories and their characteristics.

        Args:
            enhanced: If True, include human-readable names for UUIDs (default: True)
        """
        tado_api = get_tado_api()
        accessories = await tado_api.refresh_accessories()

        if enhanced:
            return {
                "accessories": enhance_accessory_data(accessories),
                "enhanced": True,
                "note": "UUIDs have been enhanced with human-readable names. Use ?enhanced=false for raw data."
            }
        else:
            return {
                "accessories": accessories,
                "enhanced": False
            }

    @app.get("/accessories/{accessory_id}", tags=["HomeKit"])
    async def get_accessory(accessory_id: int, enhanced: bool = True, api_key: Optional[str] = Depends(get_api_key)):
        """
        Get specific accessory by ID.

        Args:
            accessory_id: The HomeKit accessory ID
            enhanced: If True, include human-readable names for UUIDs (default: True)
        """
        tado_api = get_tado_api()
        if not tado_api.accessories_cache:
            await tado_api.refresh_accessories()

        accessories = tado_api.accessories_cache
        for accessory in accessories:
            if accessory.get('id') == accessory_id:
                if enhanced:
                    enhanced_accessories = enhance_accessory_data([accessory])
                    return {
                        "accessory": enhanced_accessories[0] if enhanced_accessories else accessory,
                        "enhanced": True
                    }
                else:
                    return {
                        "accessory": accessory,
                        "enhanced": False
                    }

        raise HTTPException(status_code=404, detail=f"Accessory {accessory_id} not found")

    @app.get("/thermostats", tags=["Thermostats"])
    async def get_thermostats(api_key: Optional[str] = Depends(get_api_key)):
        """
        Get all thermostat devices with standardized state.

        Returns temperature, humidity, mode, and heating status for each thermostat.
        """
        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")

        if not tado_api.accessories_cache:
            await tado_api.refresh_accessories()

        thermostats = []
        accessories = tado_api.accessories_cache

        for accessory in accessories:
            services = accessory.get('services', [])
            for service in services:
                if service.get('type').upper() == '0000004A-0000-1000-8000-0026BB765291':  # Thermostat service

                    device_id = accessory.get('id')
                    if not device_id:
                        continue

                    # Get device info from cache
                    device_info = tado_api.state_manager.device_info_cache.get(device_id, {})

                    # Build standardized state
                    state = tado_api.state_manager.get_current_state(device_id)
                    cur_temp_c = state.get('current_temperature')
                    target_temp_c = state.get('target_temperature')

                    # Determine battery_low from Cloud API (cached)
                    battery_state = device_info.get('battery_state')
                    battery_low = battery_state is not None and battery_state != 'NORMAL'

                    thermostat = {
                        'device_id': device_id,
                        'aid': accessory.get('aid'),
                        'serial_number': accessory.get('serial_number'),
                        'zone_name': device_info.get('zone_name'),
                        'zone_id': device_info.get('zone_id'),
                        'device_type': device_info.get('device_type'),
                        'is_zone_leader': device_info.get('is_zone_leader', False),
                        'state': {
                            'cur_temp_c': cur_temp_c,
                            'cur_temp_f': round(cur_temp_c * 9/5 + 32, 1) if cur_temp_c is not None else None,
                            'hum_perc': state.get('humidity'),
                            'target_temp_c': target_temp_c,
                            'target_temp_f': round(target_temp_c * 9/5 + 32, 1) if target_temp_c is not None else None,
                            'mode': state.get('target_heating_cooling_state', 0),
                            'cur_heating': state.get('current_heating_cooling_state'),
                            'valve_position': state.get('valve_position'),
                            'battery_low': battery_low,
                        }
                    }

                    thermostats.append(thermostat)

        return {"thermostats": thermostats, "count": len(thermostats)}

    @app.get("/thermostats/{thermostat_id}", tags=["Thermostats"])
    async def get_thermostat(thermostat_id: int, api_key: Optional[str] = Depends(get_api_key)):
        """Get specific thermostat by device ID with standardized state."""
        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")

        if not tado_api.accessories_cache:
            await tado_api.refresh_accessories()

        # Find accessory by device ID
        accessory = None
        for acc in tado_api.accessories_cache:
            if acc.get('id') == thermostat_id:
                accessory = acc
                break

        if not accessory:
            raise HTTPException(status_code=404, detail=f"Device with ID {thermostat_id} not found")

        # Check if it's a thermostat
        is_thermostat = False
        for service in accessory.get('services', []):
            if service.get('type').upper() == '0000004A-0000-1000-8000-0026BB765291':
                is_thermostat = True
                break

        if not is_thermostat:
            raise HTTPException(status_code=400, detail=f"Device {thermostat_id} is not a thermostat")

        # Get device info from cache
        device_info = tado_api.state_manager.device_info_cache.get(thermostat_id, {})

        # Build standardized state
        state = tado_api.state_manager.get_current_state(thermostat_id)
        cur_temp_c = state.get('current_temperature')
        target_temp_c = state.get('target_temperature')

        # Determine battery_low from Cloud API (cached)
        battery_state = device_info.get('battery_state')
        battery_low = battery_state is not None and battery_state != 'NORMAL'

        thermostat = {
            'device_id': thermostat_id,
            'aid': accessory.get('aid'),
            'serial_number': accessory.get('serial_number'),
            'zone_name': device_info.get('zone_name'),
            'device_type': device_info.get('device_type'),
            'is_zone_leader': device_info.get('is_zone_leader'),
            'is_circuit_driver': device_info.get('is_circuit_driver'),
            'state': {
                'cur_temp_c': cur_temp_c,
                'cur_temp_f': round(cur_temp_c * 9/5 + 32, 1) if cur_temp_c is not None else None,
                'hum_perc': state.get('humidity'),
                'target_temp_c': target_temp_c,
                'target_temp_f': round(target_temp_c * 9/5 + 32, 1) if target_temp_c is not None else None,
                'mode': state.get('target_heating_cooling_state', 0),
                'cur_heating': state.get('current_heating_cooling_state'),
                'valve_position': state.get('valve_position'),
                'battery_low': battery_low,
            }
        }

        return thermostat

    @app.get("/zones", tags=["Zones"])
    async def get_zones(api_key: Optional[str] = Depends(get_api_key)):
        """
        Get all zones with aggregated state (no per-device details).

        Returns zone-level information:
        - Current temperature (°C and °F)
        - Current humidity (%)
        - Target temperature (°C and °F)
        - Mode (0=Off, 1=Heat, 2=Cool) - TargetHeatingCoolingState
        - Currently heating (0=Off, 1=Heating, 2=Cooling) - CurrentHeatingCoolingState

        Note: Mode values depend on device capabilities. Heating-only devices typically
        support 0 (Off) and 1 (Heat). Devices with cooling may support additional values.

        For individual device details, use /thermostats or /devices endpoints.

        Note: For zones where the leader is a circuit driver (e.g., RU02 controlling
        multiple rooms), the "cur_heating" status reflects the actual heating
        state from radiator valves in the zone, not the circuit driver state.
        """
        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")

        zones = []

        # Use cached zone info (no DB query)
        # Sort by order_id (treating None as 999, but 0 is valid), then by name
        for zone_id, zone_info in sorted(
                        tado_api.state_manager.zone_cache.items(),
                        key=lambda x: (999 if x[1].get('order_id') is None else x[1].get('order_id'), x[1].get('name'))
                ):
            name = zone_info['name']
            leader_device_id = zone_info['leader_device_id']
            order_id = zone_info['order_id']
            leader_serial = zone_info['leader_serial']
            leader_type = zone_info['leader_type']
            zone_type = zone_info['zone_type']
            is_circuit_driver = zone_info['is_circuit_driver']
            tado_zone_id = zone_info['tado_zone_id']
            window_open_time = zone_info['window_open_time']
            window_rest_time = zone_info['window_rest_time']

            # Get device count for this zone (quick loop through device cache)
            device_count = sum(
                    1 for dev_info in tado_api.state_manager.device_info_cache.values()
                    if dev_info.get('zone_id') == zone_id
                )

            # Get zone state from leader (with optimistic updates for UI responsiveness)
            # Note: Individual devices always show real state. Only zone aggregation uses optimistic state.
            zone_state = None
            if leader_device_id:
                zone_state = tado_api.state_manager.get_state_with_optimistic(leader_device_id)

            # If no leader state, try first device in zone
            if not zone_state:
                for dev_id, dev_info in tado_api.state_manager.device_info_cache.items():
                    if dev_info.get('zone_id') == zone_id:
                        zone_state = tado_api.state_manager.get_state_with_optimistic(dev_id)
                        break

            # Build zone summary state from zone leader:
            # - All values (temp, humidity, target_temp, mode) come from zone leader (with optimistic updates)
            # - Exception: cur_heating for circuit drivers with other devices uses radiator valve state
            if zone_state:
                current_temp = zone_state.get('current_temperature')
                humidity = zone_state.get('humidity')
                target_temp = zone_state.get('target_temperature')
                target_heating_cooling_state = zone_state.get('target_heating_cooling_state', 0)
                window_open = (zone_state.get('window') == 1)

                # Mode: Always from zone leader's target_heating_cooling_state (with optimistic updates)
                mode = target_heating_cooling_state

                # Currently heating: From zone leader, EXCEPT for circuit drivers with other devices
                cur_heating = 0
                if is_circuit_driver:
                    # Circuit driver - check if there are other devices (radiator valves) in zone
                    other_devices = [
                            dev_id for dev_id, dev_info in tado_api.state_manager.device_info_cache.items()
                            if dev_info.get('zone_id') == zone_id and not dev_info.get('is_circuit_driver')
                        ]

                    if other_devices:
                        # Circuit driver WITH other devices - use radiator valve heating state (real state)
                        for dev_id in other_devices:
                            dev_state = tado_api.state_manager.get_current_state(dev_id)
                            if dev_state and dev_state.get('current_heating_cooling_state') in (1, 2):
                                cur_heating = dev_state.get('current_heating_cooling_state')
                                break
                    else:
                        # Circuit driver ALONE in zone - use its own heating state
                        cur_heating = zone_state.get('current_heating_cooling_state')
                else:
                    # Regular zone leader (not circuit driver) - use its heating state
                    cur_heating = zone_state.get('current_heating_cooling_state')

                # Convert temperatures to Fahrenheit
                cur_temp_f = round(current_temp * 9/5 + 32, 1) if current_temp is not None else None
                target_temp_f = round(target_temp * 9/5 + 32, 1) if target_temp is not None else None

                state_summary = {
                    'cur_temp_c': current_temp,
                    'cur_temp_f': cur_temp_f,
                    'hum_perc': humidity,
                    'target_temp_c': target_temp,
                    'target_temp_f': target_temp_f,
                    'mode': mode,
                    'cur_heating': cur_heating,
                    'window_open': window_open,
                }
            else:
                state_summary = {
                    'cur_temp_c': None,
                    'cur_temp_f': None,
                    'hum_perc': None,
                    'target_temp_c': None,
                    'target_temp_f': None,
                    'mode': 0,
                    'cur_heating': 0,
                    'window_open': None,
                }

            zones.append({
                'zone_id': zone_id,
                'name': name,
                'uuid': zone_info.get('uuid'),
                'leader_device_id': leader_device_id,
                'leader_serial': leader_serial,
                'leader_type': leader_type,
                'zone_type': zone_type,
                'tado_zone_id': tado_zone_id,
                'is_circuit_driver': bool(is_circuit_driver),
                'order_id': order_id,
                'device_count': device_count,
                'window_open_time': window_open_time,
                'window_rest_time': window_rest_time,
                'state': state_summary
            })

        # Get home info if cloud API is available and authenticated
        homes = []
        if hasattr(tado_api, 'cloud_api') and tado_api.cloud_api and tado_api.cloud_api.is_authenticated():
            try:
                home_data = await tado_api.cloud_api.get_home_info()
                if home_data:
                    homes.append({
                        'id': home_data.get('id'),
                        'name': home_data.get('name')
                    })
            except Exception as e:
                logger.debug(f"Could not fetch home info: {e}")

        # Add home_id reference to each zone (from first/only home for now)
        home_id = homes[0]['id'] if homes else None
        for zone in zones:
            zone['home_id'] = home_id

        return {
            'homes': homes,
            'zones': zones,
            'count': len(zones)
        }

    @app.post("/zones/set", tags=["Zones"])
    async def set_all_zones(
                    heating_enabled: Optional[bool] = None,
                    persistant: Optional[bool] = False,
                    api_key: Optional[str] = Depends(get_api_key)
                ):
        """
        Set heating mode for all zones, with optional persistent cloud handling.

        Control a zone's heating via its leader device or via Cloud API for persistent control.

        Args:
            zone_id: Zone ID to control
            heating_enabled: Enable/disable heating mode (true/false) TRUE return to last know heating mode (heat/cool) FALSE turn off heating.
            persistant: If true, a Cloud API command will be sent to  enable/disable the SMART schedule for unlimited time.
                        This will cost a cloud API call and requires the cloud API to be enabled and authenticated.
                        If false, a temporary overlay will be applied that will automatically expire according the Tado App setting.

        Returns:
            Success status and applied values

        Notes:
            - Commands are sent to the zone's leader device
            - The leader propagates changes to other devices as needed
            - heating_enabled controls the heat/cool mode (OFF=0, HEAT=1/COOL=2 depending on last active mode)
            - if persistant is true, the SMART schedule will be enabled/disabled via Cloud API
              for unlimited time until manually changed again. This is useful for scenarios like
              "away mode" where you want to ensure the setting persists stop the SMART schedule from re-enabling heating.
              heating_enabled=true + persistant=true will enable the SMART schedule again.
              If persistant is false, a temporary overlay will be applied that will automatically expire
              according the Tado App setting.
        """

        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")

        if heating_enabled is None:
            raise HTTPException(status_code=400, detail="At heating mode should be given")

        conn = sqlite3.connect(tado_api.state_manager.db_path)
        cursor = conn.execute(
            """
            SELECT z.zone_id, z.name, z.tado_zone_id, z.leader_device_id, d.serial_number, z.zone_type
            FROM zones z
            LEFT JOIN devices d ON z.leader_device_id = d.device_id
            ORDER BY z.zone_id
            """
        )
        zones = cursor.fetchall()
        conn.close()

        if not zones:
            return {
                'success': True,
                'count': 0,
                'handling': 'noop',
                'zones': []
            }

        if persistant is True:
            cloud_api = getattr(tado_api, 'cloud_api', None)
            if not cloud_api:
                raise HTTPException(status_code=503, detail="Persistent mode requires cloud API to be enabled")
            if not cloud_api.is_authenticated():
                raise HTTPException(status_code=401, detail="Persistent mode requires cloud API authentication")

            missing_tado_zone_ids = [zone_name for _, zone_name, tado_zone_id, _, _, _ in zones if not tado_zone_id]
            if missing_tado_zone_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"Zones missing tado_zone_id required for cloud control: {', '.join(missing_tado_zone_ids)}"
                )

            tado_zone_ids = [tado_zone_id for zi, n, tado_zone_id, ldi, sn, zt in zones]

            try:
                if heating_enabled:
                    cloud_result = await cloud_api._switch_zones_to_smartschedule(tado_zone_ids)
                else:
                    cloud_result = await cloud_api._switch_zones_persistant_off(tado_zone_ids)

                if cloud_result is None:
                    raise HTTPException(status_code=502, detail="Cloud API command failed")

                logger.info("All zones: smart-schedule=%s via Cloud API", heating_enabled)

                return {
                    'success': True,
                    'count': len(zones),
                    'handling': 'cloud_call',
                    'applied': {
                        'target_temperature': None,
                        'heating_enabled': heating_enabled
                    },
                    'zones': [
                        {
                            'zone_id': zone_id,
                            'zone_name': zone_name,
                            'tado_zone_id': tado_zone_id
                        }
                        for zone_id, zone_name, tado_zone_id, id, sn, zt_ in zones
                    ]
                }
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed persistent control for all zones: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to set persistent cloud control for all zones: {str(e)}")

        if not tado_api.pairing:
            raise HTTPException(status_code=503, detail="Bridge not connected")

        char_updates = {
            'target_heating_cooling_state': None     # will be set per zone based on heating_enabled and zone type (HEATING vs AIR_CONDITIONING)
        }

        conn = sqlite3.connect(tado_api.state_manager.db_path)
        results = []
        errors = []
        for zone_id, zone_name, _, leader_device_id, leader_serial, zone_type in zones:
            # Fallback: if zone has no explicit leader, use first device in zone
            if not leader_device_id:
                cur = conn.execute(
                    "SELECT device_id, serial_number FROM devices WHERE zone_id = ? ORDER BY device_id LIMIT 1",
                    (zone_id,)
                )
                dev = cur.fetchone()
                if dev:
                    leader_device_id, leader_serial = dev
                    logger.warning(
                        "Zone %s ('%s') has no leader assigned; falling back to device %s (%s)",
                        zone_id, zone_name, leader_device_id, leader_serial
                    )
                else:
                    logger.warning("Zone %s ('%s') has no devices; skipping", zone_id, zone_name)
                    errors.append({'zone_id': zone_id, 'zone_name': zone_name, 'error': 'No devices in zone'})
                    continue

            if zone_type == 'HEATING' or heating_enabled is False:
                # 0 = OFF, 1 = HEAT (when heating_enabled is False always switch to OFF regardless of zone type)
                char_updates['target_heating_cooling_state'] = 1 if heating_enabled else 0
            else:
                # Zone type can be 'AIR_CONDITIONING' (or other none HEATING) so we need to kwown which mode to return to
                # get last mode from device_state_history if available, otherwise default to HEAT (0 = OFF, 1 = HEAT, 2 = COOL)
                char_updates['target_heating_cooling_state'] = tado_api.state_manager.get_last_active_heating_mode(leader_device_id)

            # Apply optimistic state for immediate UI feedback
            tado_api.state_manager.set_optimistic_state(
                leader_device_id,
                {'target_heating_cooling_state': char_updates['target_heating_cooling_state']}
            )

            try:
                await tado_api.set_device_characteristics(leader_device_id, char_updates)
                mode = MODE_LABELS.get(char_updates['target_heating_cooling_state'], f"UNKNOWN({char_updates['target_heating_cooling_state']})")
                logger.info("Zone %s (%s): heating mode=%s via local bridge", zone_id, zone_name, mode)
                results.append({
                    'success': True,
                    'zone_id': zone_id,
                    'zone_name': zone_name,
                    'leader_device_id': leader_device_id,
                    'leader_serial': leader_serial,
                    'mode': mode,
                    'applied': {'target_temperature': None, 'heating_enabled': heating_enabled}
                })
            except Exception as e:
                logger.error("Failed to control zone %s (%s): %s", zone_id, zone_name, e)
                errors.append({'zone_id': zone_id, 'zone_name': zone_name, 'error': str(e)})
        conn.close()

        return {
            'success': len(errors) == 0,
            'count': len(results),
            'error_count': len(errors),
            'handling': 'local_handling',
            'applied': {
                'target_temperature': None,
                'heating_enabled': heating_enabled
            },
            'zones': results,
            'errors': errors
        }

    @app.get("/zones/{zone_id}", tags=["Zones"])
    async def get_zone(zone_id: int, api_key: Optional[str] = Depends(get_api_key)):
        """
        Get zone level information

        Returns zone-level information:
        - Current temperature (°C and °F)
        - Current humidity (%)
        - Target temperature (°C and °F)
        - Mode (0=Off, 1=Heat) - TargetHeatingCoolingState
        - Currently heating (0=Off, 1=Heating, 2=Cooling) - CurrentHeatingCoolingState

        Note: Mode values depend on device capabilities. Heating-only devices typically
        support 0 (Off) and 1 (Heat). Devices with cooling may support additional values.

        For individual device details, use /thermostats or /devices endpoints.

        Note: For zones where the leader is a circuit driver (e.g., RU02 controlling
        multiple rooms), the "cur_heating" status reflects the actual heating
        state from radiator valves in the zone, not the circuit driver state.
        """
        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")

        zones = []

        if zone_id not in tado_api.state_manager.zone_cache:
            raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found")

        # Use cached zone info (no DB query)
        # Sort by order_id (treating None as 999, but 0 is valid), then by name
        zone_info = tado_api.state_manager.zone_cache[zone_id]

        name = zone_info['name']
        leader_device_id = zone_info['leader_device_id']
        order_id = zone_info['order_id']
        leader_serial = zone_info['leader_serial']
        leader_type = zone_info['leader_type']
        zone_type = zone_info['zone_type']
        is_circuit_driver = zone_info['is_circuit_driver']
        tado_zone_id = zone_info['tado_zone_id']
        window_open_time = zone_info['window_open_time']
        window_rest_time = zone_info['window_rest_time']

        # Get device count for this zone (quick loop through device cache)
        device_count = sum(
                            1 for dev_info in tado_api.state_manager.device_info_cache.values()
                            if dev_info.get('zone_id') == zone_id
                        )

        # Get zone state from leader (with optimistic updates for UI responsiveness)
        # Note: Individual devices always show real state. Only zone aggregation uses optimistic state.
        zone_state = None
        if leader_device_id:
            zone_state = tado_api.state_manager.get_state_with_optimistic(leader_device_id)

        # If no leader state, try first device in zone
        if not zone_state:
            for dev_id, dev_info in tado_api.state_manager.device_info_cache.items():
                if dev_info.get('zone_id') == zone_id:
                    zone_state = tado_api.state_manager.get_state_with_optimistic(dev_id)
                    break

        # Build zone summary state from zone leader:
        # - All values (temp, humidity, target_temp, mode) come from zone leader (with optimistic updates)
        # - Exception: cur_heating for circuit drivers with other devices uses radiator valve state
        if zone_state:
            current_temp = zone_state.get('current_temperature')
            humidity = zone_state.get('humidity')
            target_temp = zone_state.get('target_temperature')
            target_heating_cooling_state = zone_state.get('target_heating_cooling_state', 0)
            window_open = (zone_state.get('window') == 1)

            # Mode: Always from zone leader's target_heating_cooling_state (with optimistic updates)
            mode = target_heating_cooling_state

            # Currently heating: From zone leader, EXCEPT for circuit drivers with other devices
            cur_heating = 0
            if is_circuit_driver:
                # Circuit driver - check if there are other devices (radiator valves) in zone
                other_devices = [
                        dev_id for dev_id, dev_info in tado_api.state_manager.device_info_cache.items()
                        if dev_info.get('zone_id') == zone_id and not dev_info.get('is_circuit_driver')
                    ]

                if other_devices:
                    # Circuit driver WITH other devices - use radiator valve heating state (real state)
                    for dev_id in other_devices:
                        dev_state = tado_api.state_manager.get_current_state(dev_id)
                        if dev_state and dev_state.get('current_heating_cooling_state') == 1:
                            cur_heating = 1
                            break
                else:
                    # Circuit driver ALONE in zone - use its own heating state
                    cur_heating = 1 if zone_state.get('current_heating_cooling_state') == 1 else 0
            else:
                # Regular zone leader (not circuit driver) - use its heating state
                cur_heating = 1 if zone_state.get('current_heating_cooling_state') == 1 else 0

            # Convert temperatures to Fahrenheit
            cur_temp_f = round(current_temp * 9/5 + 32, 1) if current_temp is not None else None
            target_temp_f = round(target_temp * 9/5 + 32, 1) if target_temp is not None else None

            state_summary = {
                'cur_temp_c': current_temp,
                'cur_temp_f': cur_temp_f,
                'hum_perc': humidity,
                'target_temp_c': target_temp,
                'target_temp_f': target_temp_f,
                'mode': mode,
                'cur_heating': cur_heating,
                'window_open': window_open
            }
        else:
            state_summary = {
                'cur_temp_c': None,
                'cur_temp_f': None,
                'hum_perc': None,
                'target_temp_c': None,
                'target_temp_f': None,
                'mode': 0,
                'cur_heating': 0,
                'window_open': None,
            }

        zone = {
            'zone_id': zone_id,
            'name': name,
            'uuid': zone_info.get('uuid'),
            'leader_device_id': leader_device_id,
            'leader_serial': leader_serial,
            'leader_type': leader_type,
            'zone_type': zone_type,
            'tado_zone_id': tado_zone_id,
            'is_circuit_driver': bool(is_circuit_driver),
            'order_id': order_id,
            'device_count': device_count,
            'window_open_time': window_open_time,
            'window_rest_time': window_rest_time,
            'state': state_summary
        }

        # Get home info if cloud API is available and authenticated
        homes = []
        if hasattr(tado_api, 'cloud_api') and tado_api.cloud_api and tado_api.cloud_api.is_authenticated():
            try:
                home_data = await tado_api.cloud_api.get_home_info()
                if home_data:
                    homes.append({
                        'id': home_data.get('id'),
                        'name': home_data.get('name')
                    })
            except Exception as e:
                logger.debug(f"Could not fetch home info: {e}")

        # Add home_id reference to each zone (from first/only home for now)
        home_id = homes[0]['id'] if homes else None
        for zone in zones:
            zone['home_id'] = home_id

        return {
            'home': homes[0] if homes else None,
            'zone': zone,
        }

    @app.post("/zones", tags=["Zones"])
    async def create_zone(
                    name: str,
                    leader_device_id: Optional[int] = None,
                    order_id: Optional[int] = None,
                    api_key: Optional[str] = Depends(get_api_key)
                ):
        """Create a new zone."""
        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")

        conn = sqlite3.connect(tado_api.state_manager.db_path)
        cursor = conn.execute("""
            INSERT INTO zones (name, leader_device_id, order_id)
            VALUES (?, ?, ?)
        """, (name, leader_device_id, order_id))
        zone_id = cursor.lastrowid
        conn.commit()
        conn.close()

        # Reload device cache to pick up zone info
        tado_api.state_manager._load_device_cache()

        return {'zone_id': zone_id, 'name': name}

    @app.put("/zones/{zone_id}", tags=["Zones"])
    async def update_zone(
                zone_id: int,
                name: Optional[str] = None,
                leader_device_id: Optional[int] = None,
                order_id: Optional[int] = None,
                api_key: Optional[str] = Depends(get_api_key)
            ):
        """Update a zone."""
        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")

        updates = []
        params = []
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if leader_device_id is not None:
            updates.append("leader_device_id = ?")
            params.append(leader_device_id)
        if order_id is not None:
            updates.append("order_id = ?")
            params.append(order_id)

        if not updates:
            raise HTTPException(status_code=400, detail="No updates provided")

        conn = sqlite3.connect(tado_api.state_manager.db_path)
        params.append(zone_id)
        conn.execute(f"UPDATE zones SET {', '.join(updates)} WHERE zone_id = ?", params)
        updated = (conn.total_changes > 0)
        conn.commit()
        conn.close()

        if not updated:
            raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found or no changes made")

        # Reload device cache
        tado_api.state_manager._load_device_cache()

        return {'zone_id': zone_id, 'updated': updated}

    @app.post("/zones/{zone_id}/set", tags=["Zones"])
    async def set_zone(
                zone_id: int,
                temperature: Optional[float] = None,
                heating_enabled: Optional[bool] = None,
                no_implicit_mode: Optional[bool] = False,
                heating_mode: Optional[int] = None,
                persistant: Optional[bool] = False,
                api_key: Optional[str] = Depends(get_api_key)
             ):
        """
        Control a zone's heating via its leader device.

        Args:
            zone_id: Zone ID to control
            temperature: Target temperature in °C (-1, 0, or 5-30).
                        - -1 = resume schedule/auto mode (to last know heating mode HEAT/COOL without changing target temp)
                        - 0 = disable heating (without changing target temp)
                        - >= 5 = set temperature and enable heating
            heating_enabled: Enable/disable heating mode (true/false) TRUE return to last know heating mode (heat/cool) false turn off heating.
            heating_mode: 0=OFF, 1=HEAT, 2=COOL (if supported by devices in zone). Overrides heating_enabled if given.
            no_implicit_mode: If true, disable smart defaults (temperature values won't auto-enable/disable heating)
            persistant: If true, only valid for temperature=0 or temperature=-1 else ignored. If true, a Cloud API command will be sent to
                        enable/disable the SMART schedule for unlimited time. This will cost a cloud API call and requires the cloud API to be enabled and authenticated.
                        If false, a temporary overlay will be applied that will automatically expire according the Tado App setting.

        Returns:
            Success status and applied values

        Notes:
            - Smart defaults:
              - temperature = -1 implies heating_enabled=true (resume schedule)
              - temperature = 0 implies heating_enabled=false (off)
              - temperature >= 5°C <= 30°C implies heating_enabled=true
            - Explicitly set heating_enabled to override smart defaults
            - Commands are sent to the zone's leader device
            - The leader propagates changes to other devices as needed
            - heating_enabled controls the heat/cool mode (OFF=0, HEAT=1/COOL=2 depending on last active mode)
            - Both temperature=0 and temperature=-1 preserve the stored target temperature
            - This allows temporary on/off control without affecting your schedule
            - temperature=-1 is useful for automation: turn on without changing schedule (with persistant=true to stop temporaryoverlay stop and SMART schedule)
            - temperature=0 is useful for "away mode": turn off but remember setpoint (with persistant=true to set overlay forever until manually turned back on)
        """
        # Log the incoming request
        logger.info(f"POST /zones/{zone_id}/set temperature={temperature} heating_enabled={heating_enabled} heating_mode={heating_mode} no_implicit_mode={no_implicit_mode} persistant={persistant}")

        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")

        if not tado_api.pairing:
            raise HTTPException(status_code=503, detail="Bridge not connected")

        # Validate temperature range first
        if temperature is not None:
            if temperature < -1.0 or temperature > 30.0:
                raise HTTPException(status_code=400, detail="Temperature must be -1 (resume), 0 (off), or between 5 and 30°C")
            if temperature > 0 and temperature < 5.0:
                raise HTTPException(status_code=400, detail="Temperature must be -1, 0, or between 5 and 30°C")

        if temperature is None and heating_enabled is None and heating_mode is None:
            raise HTTPException(status_code=400, detail="At least the temp or heating mode should be given")

        resolved_temperature = temperature
        if heating_mode is None:
            resolved_heating_enabled = heating_enabled
        else:
            resolved_heating_enabled = None

        if temperature == -1:
            if heating_enabled is False or heating_mode == 0:
                raise HTTPException(status_code=400, detail="Can not switch on and off heating with one call")
            resolved_temperature = None
            if heating_mode is None:
                resolved_heating_enabled = True
        elif temperature == 0:
            if heating_enabled is True or heating_mode in (1, 2):
                raise HTTPException(status_code=400, detail="Can not switch on and off heating with one call")
            resolved_temperature = None
            if heating_mode is None:
                resolved_heating_enabled = False
        elif temperature is not None and temperature >= 5.0:
            if heating_enabled is None and heating_mode is None and no_implicit_mode is False:
                resolved_heating_enabled = True

        # Get zone info
        conn = sqlite3.connect(tado_api.state_manager.db_path)
        cursor = conn.execute("""
            SELECT z.name, z.leader_device_id, d.serial_number, z.tado_zone_id, z.zone_type
            FROM zones z
            LEFT JOIN devices d ON z.leader_device_id = d.device_id
            WHERE z.zone_id = ?
        """, (zone_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found")

        zone_name, leader_device_id, leader_serial, tado_zone_id, zone_type = row

        if not leader_device_id:
            # No explicit leader assigned - fall back to the first device in the zone
            conn = sqlite3.connect(tado_api.state_manager.db_path)
            cur = conn.execute(
                """
                SELECT device_id, serial_number, name
                FROM devices
                WHERE zone_id = ?
                ORDER BY device_id
                LIMIT 1
                """,
                (zone_id,)
            )
            dev = cur.fetchone()
            conn.close()

            if dev:
                leader_device_id = dev[0]
                leader_serial = dev[1]
                logger.warning(
                    "Zone %s ('%s') has no leader assigned; falling back to device %s (%s)",
                    zone_id, zone_name, leader_device_id, leader_serial
                )
            else:
                raise HTTPException(status_code=400, detail=f"Zone '{zone_name}' has no leader device assigned")

        # Persistent handling only applies to on/off mode changes without temperature updates
        use_cloud_persistant_call = (
            persistant is True and
            resolved_temperature is None and
            resolved_heating_enabled is not None
        )

        if use_cloud_persistant_call:
            cloud_api = getattr(tado_api, 'cloud_api', None)
            if not cloud_api:
                raise HTTPException(status_code=503, detail="Persistent mode requires cloud API to be enabled")
            if not cloud_api.is_authenticated():
                raise HTTPException(status_code=401, detail="Persistent mode requires cloud API authentication")
            if not tado_zone_id:
                raise HTTPException(status_code=400, detail="Zone is missing tado_zone_id required for cloud control")

            try:
                if resolved_heating_enabled:
                    cloud_result = await cloud_api._switch_zones_to_smartschedule([tado_zone_id])
                else:
                    cloud_result = await cloud_api._switch_zones_persistant_off([tado_zone_id])

                if cloud_result is None:
                    raise HTTPException(status_code=502, detail="Cloud API command failed")

                logger.info(f"Zone {zone_id} ({zone_name}): smart-schedule={resolved_heating_enabled} via Cloud API")

                return {
                    'success': True,
                    'zone_id': zone_id,
                    'zone_name': zone_name,
                    'leader_device_id': leader_device_id,
                    'leader_serial': leader_serial,
                    'handling': 'cloud_call',
                    'applied': {
                        'target_temperature': resolved_temperature,
                        'heating_enabled': resolved_heating_enabled,
                        'persistant': use_cloud_persistant_call
                    }
                }
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed cloud control for zone {zone_id}: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to set persistent cloud control: {str(e)}")

        # Build characteristic updates
        char_updates = {}

        if resolved_temperature is not None:
            char_updates['target_temperature'] = resolved_temperature

        if heating_mode is not None:
            # heating_mode overrides heating_enabled if given
            if zone_type == 'HEATING':
                if heating_mode in (0, 1):
                    char_updates['target_heating_cooling_state'] = heating_mode
                else:
                    raise HTTPException(status_code=400, detail="Invalid heating_mode value. Must be 0 (OFF) or 1 (HEAT)")
            elif heating_mode in (0, 1, 2):
                char_updates['target_heating_cooling_state'] = heating_mode
            else:
                raise HTTPException(status_code=400, detail="Invalid heating_mode value. Must be 0 (OFF), 1 (HEAT) or 2 (COOL)")
        else:
            if resolved_heating_enabled is not None:
                if zone_type == 'HEATING' or resolved_heating_enabled is False:
                    # 0 = OFF, 1 = HEAT, when resolved_heating_enabled is False always switch to OFF regardless of zone type
                    char_updates['target_heating_cooling_state'] = 1 if resolved_heating_enabled else 0
                else:
                    # Zone type can be 'AIR_CONDITIONING' (other none HEATING) so when need to kwown which mode to return to
                    # get last mode from device_state_history if available, otherwise default to HEAT (0 = OFF, 1 = HEAT, 2 = COOL)
                    char_updates['target_heating_cooling_state'] = tado_api.state_manager.get_last_active_heating_mode(leader_device_id)

        if not char_updates:
            raise HTTPException(status_code=400, detail="No control parameters provided")

        # Log what we're changing (single summary line)
        changes = []
        resolved_mode = None
        if 'target_temperature' in char_updates:
            changes.append(f"temperature={char_updates['target_temperature']}°C")
        if 'target_heating_cooling_state' in char_updates:
            resolved_mode = MODE_LABELS.get(
                char_updates['target_heating_cooling_state'],
                f"UNKNOWN({char_updates['target_heating_cooling_state']})"
            )
            changes.append(f"mode={resolved_mode}")
        logger.info(f"Zone {zone_id} ({zone_name}): {', '.join(changes)}")

        # Apply optimistic state prediction for immediate UI feedback
        optimistic_state = {}
        if 'target_temperature' in char_updates:
            optimistic_state['target_temperature'] = char_updates['target_temperature']
        if 'target_heating_cooling_state' in char_updates:
            optimistic_state['target_heating_cooling_state'] = char_updates['target_heating_cooling_state']

        if optimistic_state:
            tado_api.state_manager.set_optimistic_state(leader_device_id, optimistic_state)
            logger.debug(
                "Zone %s: Applied optimistic state prediction: %s",
                zone_id, optimistic_state
            )

        # Set the characteristics on the leader device
        try:
            await tado_api.set_device_characteristics(leader_device_id, char_updates)

            return {
                'success': True,
                'zone_id': zone_id,
                'zone_name': zone_name,
                'leader_device_id': leader_device_id,
                'leader_serial': leader_serial,
                'applied': {
                    'target_temperature': resolved_temperature,
                    'heating_enabled': resolved_heating_enabled,
                    'heating_mode': resolved_mode,
                    'persistant': use_cloud_persistant_call
                }
            }

        except Exception as e:
            logger.error(f"Failed to control zone {zone_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to set zone control: {str(e)}")

    @app.post("/zones/{zone_id}/windowtimeouts", tags=["Zones"])
    async def set_zone_window_timeouts(
                zone_id: int,
                window_open_time: Optional[int] = None,
                window_rest_time: Optional[int] = None,
                api_key: Optional[str] = Depends(get_api_key)
            ):
        """
        Set the open window timeout for a specific zone.
        Args:
            window_open_time: window open timeout in minutes (1-480, or -1 to reset to default: 30)
            window_rest_time: window rest timeout in minutes (1-480, or -1 to reset to default: 15)

        Returns:
            Success status and applied values
        """
        # Log the incoming request
        logger.info(f"POST /zones/{zone_id}/set window_open_time={window_open_time} window_rest_time={window_rest_time}")

        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")

        if not tado_api.pairing:
            raise HTTPException(status_code=503, detail="Bridge not connected")

        if zone_id not in tado_api.state_manager.zone_cache:
            raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found")

        # Apply smart defaults
        if window_open_time is None and window_rest_time is None:
            raise HTTPException(status_code=400, detail="No control parameters provided")

        updates = []
        params = []

        if window_open_time is not None:
            if window_open_time < -1 or window_open_time > 480:
                raise HTTPException(status_code=400, detail="window_open_time must be between 1 and 480 minutes, or -1 to reset to default")
            updates.append("window_open_time = ?")
            params.append(int(window_open_time) if window_open_time > 0 else 30)

        if window_rest_time is not None:
            if window_rest_time < -1 or window_rest_time > 480:
                raise HTTPException(status_code=400, detail="window_rest_time must be between 1 and 480 minutes, or -1 to reset to default")
            updates.append("window_rest_time = ?")
            params.append(int(window_rest_time) if window_rest_time > 0 else 15)

        # Update the zone's window timeout settings in the database
        params.append(zone_id)
        conn = sqlite3.connect(tado_api.state_manager.db_path)
        conn.execute(f"UPDATE zones SET {', '.join(updates)} WHERE zone_id = ?", params)
        conn.commit()
        conn.close()

        # Reload zone cache
        tado_api.state_manager._load_zone_cache()

        return {'zone_id': zone_id, 'updated': True}

    @app.get("/devices", tags=["Devices"])
    async def get_devices(api_key: Optional[str] = Depends(get_api_key)):
        """
        Get all registered devices with standardized state.

        Returns all devices (thermostats, valves, bridges, etc.) with:
        - Device metadata (serial, type, zone)
        - Standardized state format
        - Battery status (for battery-powered devices)
        """
        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")

        all_devices = tado_api.state_manager.get_all_devices()

        devices = []
        for device_info in all_devices:
            device_id = device_info['device_id']
            state = tado_api.state_manager.get_current_state(device_id)

            # Build standardized state
            cur_temp_c = state.get('current_temperature')
            target_temp_c = state.get('target_temperature')

            # Determine battery_low from Cloud API (cached in device_info, no extra DB query)
            battery_state = device_info.get('battery_state')
            battery_low = battery_state is not None and battery_state != 'NORMAL'

            device = {
                'device_id': device_id,
                'serial_number': device_info.get('serial_number'),
                'aid': device_info.get('aid'),
                'zone_id': device_info.get('zone_id'),
                'zone_name': device_info.get('zone_name'),
                'device_type': device_info.get('device_type'),
                'model': device_info.get('model'),
                'name': device_info.get('name'),
                'firmware_version': device_info.get('firmware_version'),
                'is_zone_leader': device_info.get('is_zone_leader'),
                'is_circuit_driver': device_info.get('is_circuit_driver'),
                'state': {
                    'cur_temp_c': cur_temp_c,
                    'cur_temp_f': round(cur_temp_c * 9/5 + 32, 1) if cur_temp_c is not None else None,
                    'hum_perc': state.get('humidity'),
                    'target_temp_c': target_temp_c,
                    'target_temp_f': round(target_temp_c * 9/5 + 32, 1) if target_temp_c is not None else None,
                    'mode': state.get('target_heating_cooling_state', 0),
                    'cur_heating': state.get('current_heating_cooling_state'),
                    'valve_position': state.get('valve_position'),
                    'battery_low': battery_low,
                }
            }

            devices.append(device)

        return {
            "devices": devices,
            "count": len(devices)
        }

    @app.get("/devices/{device_id}", tags=["Devices"])
    async def get_device(device_id: int, api_key: Optional[str] = Depends(get_api_key)):
        """
        Get specific device with standardized state by ID (database device_id).

        Args:
            device_id: Device ID (database ID)
        """
        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")

        all_devices = tado_api.state_manager.get_all_devices()
        device_info = next((d for d in all_devices if d['device_id'] == device_id), None)

        if not device_info:
            raise HTTPException(status_code=404, detail=f"Device {device_id} not found")

        state = tado_api.state_manager.get_current_state(device_id)

        # Build standardized state
        cur_temp_c = state.get('current_temperature')
        target_temp_c = state.get('target_temperature')

        # Determine battery_low from Cloud API (cached)
        battery_state = device_info.get('battery_state')
        battery_low = battery_state is not None and battery_state != 'NORMAL'

        device = {
            'device_id': device_id,
            'serial_number': device_info.get('serial_number'),
            'aid': device_info.get('aid'),
            'zone_id': device_info.get('zone_id'),
            'zone_name': device_info.get('zone_name'),
            'device_type': device_info.get('device_type'),
            'model': device_info.get('model'),
            'name': device_info.get('name'),
            'firmware_version': device_info.get('firmware_version'),
            'is_zone_leader': device_info.get('is_zone_leader'),
            'is_circuit_driver': device_info.get('is_circuit_driver'),
            'state': {
                'cur_temp_c': cur_temp_c,
                'cur_temp_f': round(cur_temp_c * 9/5 + 32, 1) if cur_temp_c is not None else None,
                'hum_perc': state.get('humidity'),
                'target_temp_c': target_temp_c,
                'target_temp_f': round(target_temp_c * 9/5 + 32, 1) if target_temp_c is not None else None,
                'mode': state.get('target_heating_cooling_state', 0),
                'cur_heating': state.get('current_heating_cooling_state'),
                'valve_position': state.get('valve_position'),
                'battery_low': battery_low,
            }
        }

        return device

    @app.get("/devices/{device_id}/history", tags=["Devices"])
    async def get_device_history(
        device_id: int,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        limit: int = 100,
        offset: int = 0,
        api_key: Optional[str] = Depends(get_api_key)
    ):
        """
        Get device state history.

        Args:
            device_id: Device ID
            start_time: Start timestamp (Unix epoch)
            end_time: End timestamp (Unix epoch)
            limit: Maximum number of records to return (default: 100)
            offset: Number of records to skip for pagination (default: 0)

        Returns:
            List of historical state snapshots with standardized state format.
            Each record contains a 'state' object (matching /devices format) and 'timestamp'.
        """
        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")

        history = tado_api.state_manager.get_device_history(
            device_id, start_time, end_time, limit, offset
        )

        return {
            "device_id": device_id,
            "history": history,
            "count": len(history),
            "limit": limit,
            "offset": offset
        }

    @app.post("/devices/{device_id}/set", tags=["Devices"])
    async def set_device(
        device_id: int,
        temperature: Optional[float] = None,
        heating_enabled: Optional[bool] = None,
        api_key: Optional[str] = Depends(get_api_key)
    ):
        """
        Standardized control endpoint for device heating.

        Args:
            device_id: Device ID to control
            temperature: Target temperature in °C (0-30).
                        - 0 = disable heating (implies heating_enabled=false)
                        - >= 5 = enable heating (implies heating_enabled=true unless explicitly set to false)
            heating_enabled: Enable/disable heating mode (true/false)

        Returns:
            Success status and applied values

        Notes:
            - Smart defaults:
              - temperature = 0 implies heating_enabled=false
              - temperature >= 5°C implies heating_enabled=true
            - Commands are forwarded to the device's zone leader
            - If device is not in a zone, direct control is attempted
        """
        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")

        # Get device's zone
        conn = sqlite3.connect(tado_api.state_manager.db_path)
        cursor = conn.execute("""
            SELECT d.zone_id, d.serial_number, d.name
            FROM devices d
            WHERE d.device_id = ?
        """, (device_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            raise HTTPException(status_code=404, detail=f"Device {device_id} not found")

        zone_id, serial, device_name = row

        if zone_id:
            # Forward to zone control
            logger.info(f"Device {device_id} ({device_name}): Forwarding control to zone {zone_id}")

            # Apply smart defaults
            if temperature is not None and heating_enabled is None:
                if temperature == 0:
                    heating_enabled = False
                elif temperature >= 5.0:
                    heating_enabled = True

            result = await set_zone(zone_id, temperature, heating_enabled)
            result['controlled_via'] = 'zone'
            result['device_id'] = device_id
            result['device_name'] = device_name
            return result
        else:
            raise HTTPException(status_code=400, detail=f"Device {device_id} is not assigned to a zone. Assign it to a zone first.")

    @app.put("/devices/{device_id}/zone", tags=["Devices"])
    async def assign_device_to_zone(device_id: int, zone_id: Optional[int] = None, api_key: Optional[str] = Depends(get_api_key)):
        """Assign a device to a zone (or remove from zone if zone_id is None)."""
        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")

        conn = sqlite3.connect(tado_api.state_manager.db_path)
        conn.execute("UPDATE devices SET zone_id = ? WHERE device_id = ?", (zone_id, device_id))
        conn.commit()
        conn.close()

        # Reload device cache
        tado_api.state_manager._load_device_cache()

        return {'device_id': device_id, 'zone_id': zone_id, 'updated': True}

    @app.get("/thermostats/{thermostat_id}/history", tags=["Thermostats"])
    async def get_thermostat_history(
        thermostat_id: int,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        limit: int = 100,
        offset: int = 0,
        api_key: Optional[str] = Depends(get_api_key)
    ):
        """
        Get thermostat state history (forwarded to device history).

        Args:
            thermostat_id: Thermostat device ID
            start_time: Start timestamp (Unix epoch)
            end_time: End timestamp (Unix epoch)
            limit: Maximum number of records to return (default: 100)
            offset: Number of records to skip for pagination (default: 0)
        """
        # Forward to device history
        return await get_device_history(thermostat_id, start_time, end_time, limit, offset)

    @app.post("/thermostats/{thermostat_id}/set", tags=["Thermostats"])
    async def set_thermostat(
        thermostat_id: int,
        temperature: Optional[float] = None,
        heating_enabled: Optional[bool] = None,
        api_key: Optional[str] = Depends(get_api_key)
    ):
        """
        Standardized control endpoint for thermostat heating.

        Args:
            thermostat_id: Thermostat device ID to control
            temperature: Target temperature in °C (0-30).
                        - 0 = disable heating (implies heating_enabled=false)
                        - >= 5 = enable heating (implies heating_enabled=true unless explicitly set to false)
            heating_enabled: Enable/disable heating mode (true/false)

        Returns:
            Success status and applied values

        Notes:
            - Smart defaults:
              - temperature = 0 implies heating_enabled=false
              - temperature >= 5°C implies heating_enabled=true
            - Commands are forwarded to the device's zone leader
            - Alias for /devices/{id}/set with thermostat-specific naming
        """
        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")

        # Get device's zone
        conn = sqlite3.connect(tado_api.state_manager.db_path)
        cursor = conn.execute("""
            SELECT d.zone_id, d.serial_number, d.name
            FROM devices d
            WHERE d.device_id = ?
        """, (thermostat_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            raise HTTPException(status_code=404, detail=f"Device {thermostat_id} not found")

        zone_id, serial, device_name = row

        if zone_id:
            # Forward to zone control
            logger.info(f"Device {thermostat_id} ({device_name}): Forwarding control to zone {zone_id}")

            # Apply smart defaults
            if temperature is not None and heating_enabled is None:
                if temperature == 0:
                    heating_enabled = False
                elif temperature >= 5.0:
                    heating_enabled = True

            result = await set_zone(zone_id, temperature, heating_enabled)
            result['controlled_via'] = 'zone'
            result['device_id'] = thermostat_id
            result['device_name'] = device_name
            return result
        else:
            raise HTTPException(status_code=400, detail=f"Device {thermostat_id} is not assigned to a zone. Assign it to a zone first.")

    @app.get("/zones/{zone_id}/history", tags=["Zones"])
    async def get_zone_history(
        zone_id: int,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        limit: int = 100,
        offset: int = 0,
        api_key: Optional[str] = Depends(get_api_key)
    ):
        """
        Get zone state history via the zone's leader device.

        Args:
            zone_id: Zone ID
            start_time: Start timestamp (Unix epoch)
            end_time: End timestamp (Unix epoch)
            limit: Maximum number of records to return (default: 100)
            offset: Number of records to skip for pagination (default: 0)

        Notes:
            - Returns history from the zone's leader device
            - Leader device represents the canonical state for the zone
            - For circuit drivers (e.g., RU02) with radiator valves:
              * Temperature/humidity history is accurate (from circuit driver sensor)
              * Heating history shows circuit driver state (may indicate heating for entire circuit,
                not just this zone)
              * This is a known limitation: showing circuit-wide heating is better than showing none
        """
        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")

        # Get zone's leader device
        conn = sqlite3.connect(tado_api.state_manager.db_path)
        cursor = conn.execute("""
            SELECT z.name, z.leader_device_id
            FROM zones z
            WHERE z.zone_id = ?
        """, (zone_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found")

        zone_name, leader_device_id = row

        if not leader_device_id:
            raise HTTPException(status_code=400, detail=f"Zone '{zone_name}' has no leader device assigned")

        # Get leader device history
        result = await get_device_history(leader_device_id, start_time, end_time, limit, offset)
        result['zone_id'] = zone_id
        result['zone_name'] = zone_name
        result['leader_device_id'] = leader_device_id

        return result

    @app.get("/events", tags=["Events"])
    async def get_events(refresh_interval: Optional[int] = None, types: Optional[str] = None, api_key: Optional[str] = Depends(get_api_key)):
        """
        Server-Sent Events (SSE) endpoint for real-time updates.

        Args:
            refresh_interval: Optional interval in seconds to send periodic refresh updates
                            even when state hasn't changed. Useful for clients like Domoticz
                            that need regular updates for statistics and "last seen" tracking.
                            Recommended: 300 (5 minutes). Default: None (only send on changes).
            types: Optional comma-separated list of event types to filter (e.g., "zone,device" or "zone").
                   If not specified, all event types are sent.

        Clients can maintain a persistent connection to receive live updates without polling.

        Event Types:

        1. Device State Change:
           {
               "type": "device",
               "device_id": 4,
               "serial": "RU1372921856",
               "zone_name": "Studeerkamer",
               "state": {
                   "cur_temp_c": 21.5,
                   "cur_temp_f": 70.7,
                   "hum_perc": 55,
                   "target_temp_c": 21.0,
                   "target_temp_f": 69.8,
                   "mode": 1,
                   "cur_heating": 1,
                   "valve_position": 45,
                   "battery_low": false
               },
               "timestamp": 1730477890.123
           }

        2. Zone State Change:
           {
               "type": "zone",
               "zone_id": 4,
               "zone_name": "Studeerkamer",
               "state": {
                   "cur_temp_c": 21.5,
                   "cur_temp_f": 70.7,
                   "hum_perc": 55,
                   "target_temp_c": 21.0,
                   "target_temp_f": 69.8,
                   "mode": 1,
                   "cur_heating": 1
               },
               "timestamp": 1730477890.123
           }

        3. Keepalive (every 90 seconds for connections without refresh_interval):
           {
               "type": "keepalive",
               "timestamp": 1730477890.123
           }

        Note: Connections with refresh_interval don't receive keepalives - refresh events
        act as keepalives. This reduces unnecessary traffic for clients like Domoticz.

        State Field Reference:
            mode: 0=Off, 1=Heat, 2=Cool, 3=Auto (TargetHeatingCoolingState)
            cur_heating: 0=not heating, 1=actively heating 2=actively cooling (CurrentHeatingCoolingState)
            battery_low: true if Cloud API battery_state != "NORMAL" (cached, no DB queries)
            Temperatures provided in both Celsius (_c) and Fahrenheit (_f)

        Usage Example:
            const eventSource = new EventSource('/events');
            eventSource.onmessage = (event) => {
                const data = JSON.parse(event.data);

                if (data.type === 'zone') {
                    // Update zone UI
                    updateZone(data.zone_id, data.state);
                } else if (data.type === 'device') {
                    // Update device UI (if showing device details)
                    updateDevice(data.device_id, data.state);
                }
            };
        """
        tado_api = get_tado_api()

        # Parse types filter
        allowed_types = set()
        if types:
            allowed_types = set(t.strip().lower() for t in types.split(','))

        async def event_publisher():
            # Create a queue for this client
            client_queue = asyncio.Queue()
            tado_api.event_listeners.append(client_queue)

            last_refresh = time.time() if refresh_interval else None
            last_keepalive = time.time()
            keepalive_interval = 90  # 90 seconds - works with most proxies/firewalls

            try:
                while True:
                    # Calculate timeout based on refresh_interval
                    if refresh_interval and last_refresh:
                        time_since_refresh = time.time() - last_refresh
                        timeout = max(1, refresh_interval - time_since_refresh)
                    else:
                        # Use keepalive interval when no refresh (for browsers)
                        time_since_keepalive = time.time() - last_keepalive
                        timeout = max(1, keepalive_interval - time_since_keepalive)

                    # Wait for events
                    try:
                        event_data = await asyncio.wait_for(client_queue.get(), timeout=timeout)

                        # Check for shutdown signal
                        if event_data is None:
                            logger.debug("SSE stream received shutdown signal")
                            break

                        # Filter by event type if types filter is specified
                        if allowed_types:
                            # Parse event to check type
                            try:
                                event_obj = json.loads(event_data.replace('data: ', '').strip())
                                event_type = event_obj.get('type', '')
                                if event_type not in allowed_types:
                                    continue  # Skip this event
                            except Exception as e:
                                logger.debug(f"Parsing fails, send it anyway ({e})")
                                pass  # If parsing fails, send it anyway

                        yield event_data

                        # Reset refresh timer on any event
                        if refresh_interval:
                            last_refresh = time.time()
                        else:
                            last_keepalive = time.time()

                    except asyncio.TimeoutError:
                        # Check if we should send a refresh update
                        # When refresh_interval is set, timeout aligns with it, so we always refresh on timeout
                        if refresh_interval:
                            # Send refresh updates for all zones (if zone type is allowed or no filter)
                            if not allowed_types or 'zone' in allowed_types:
                                conn = sqlite3.connect(tado_api.state_manager.db_path)
                                cursor = conn.execute("""
                                    SELECT zone_id, name
                                    FROM zones
                                    WHERE zone_id IS NOT NULL
                                    ORDER BY zone_id
                                """)
                                zones = cursor.fetchall()
                                conn.close()

                                for zone_id, zone_name in zones:
                                    # Get zone info
                                    zone_info = tado_api.state_manager.zone_cache.get(zone_id)
                                    if not zone_info:
                                        continue

                                    leader_device_id = zone_info['leader_device_id']

                                    # Get zone state from leader or first device (with optimistic overrides)
                                    zone_state = None
                                    if leader_device_id:
                                        zone_state = tado_api.state_manager.get_state_with_optimistic(leader_device_id)

                                    # If no leader state, try first device in zone
                                    if not zone_state:
                                        for dev_id, dev_info in tado_api.state_manager.device_info_cache.items():
                                            if dev_info.get('zone_id') == zone_id:
                                                zone_state = tado_api.state_manager.get_state_with_optimistic(dev_id)
                                                break

                                    if zone_state:
                                        # Build simplified state for SSE
                                        state = {
                                            'cur_temp_c': zone_state.get('current_temperature'),
                                            'hum_perc': zone_state.get('humidity'),
                                            'target_temp_c': zone_state.get('target_temperature'),
                                            'mode': zone_state.get('target_heating_cooling_state', 0),
                                            'cur_heating': zone_state.get('current_heating_cooling_state', 0),
                                            'battery_low': zone_state.get('battery_low', False)
                                        }

                                        # Include stable uuid for the zone if available so clients can use it
                                        zone_uuid = zone_info.get('uuid') if zone_info else None
                                        if zone_uuid:
                                            state['uuid'] = zone_uuid

                                        event_obj = {
                                            'type': 'zone',
                                            'zone_id': zone_id,
                                            'zone_name': zone_name,
                                            'state': state,
                                            'timestamp': time.time(),
                                            'refresh': True
                                        }
                                        yield f"data: {json.dumps(event_obj)}\n\n"

                            # Send refresh updates for devices (all devices) if device type is allowed
                            if not allowed_types or 'device' in allowed_types:
                                # Get all devices (let clients filter for leaders/non-leaders)
                                # Note: Devices use real state only - optimistic updates only apply to zones
                                for device_id, device_info in tado_api.state_manager.device_info_cache.items():
                                    device_state = tado_api.state_manager.get_current_state(device_id)

                                    if device_state:
                                        zone_id = device_info.get('zone_id')
                                        zone_info = tado_api.state_manager.zone_cache.get(zone_id) if zone_id else None
                                        zone_name = zone_info.get('name') if zone_info else f'Zone {zone_id}' if zone_id else 'Unknown'

                                        # Build simplified state for SSE
                                        state = {
                                            'cur_temp_c': device_state.get('current_temperature'),
                                            'hum_perc': device_state.get('humidity'),
                                            'battery_low': device_state.get('battery_low', False)
                                        }

                                        serial = device_info.get('serial_number', '')

                                        event_obj = {
                                            'type': 'device',
                                            'device_id': device_id,
                                            'zone_id': zone_id,
                                            'zone_name': zone_name,
                                            'serial_number': serial,
                                            'state': state,
                                            'timestamp': time.time(),
                                            'refresh': True
                                        }
                                        yield f"data: {json.dumps(event_obj)}\n\n"

                            last_refresh = time.time()
                        else:
                            # Only send keepalive if we're not doing refreshes
                            # (refresh events already act as keepalives)
                            if time.time() - last_keepalive >= keepalive_interval:
                                keepalive_obj = {'type': 'keepalive', 'timestamp': time.time()}
                                yield f"data: {json.dumps(keepalive_obj)}\n\n"
                                last_keepalive = time.time()

            except asyncio.CancelledError:
                logger.debug("SSE stream cancelled")
                pass
            finally:
                # Remove this client's queue
                if client_queue in tado_api.event_listeners:
                    tado_api.event_listeners.remove(client_queue)

        return StreamingResponse(
            event_publisher(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Content-Type": "text/event-stream"
            }
        )

    @app.post("/refresh", tags=["Admin"])
    async def refresh_data(api_key: Optional[str] = Depends(get_api_key)):
        """Manually refresh accessories data from HomeKit bridge."""
        tado_api = get_tado_api()
        return await tado_api.refresh_accessories()

    @app.post("/refresh/cloud", tags=["Admin"])
    async def refresh_cloud_data(battery_only: bool = False, api_key: Optional[str] = Depends(get_api_key)):
        """
        Manually refresh data from Tado Cloud API.

        Args:
            battery_only: If true, only refresh battery/status data (fast).
                         If false, refresh all data including config (slower).

        Returns:
            Summary of refreshed data

        This bypasses the cache and fetches fresh data from Tado's servers.
        Useful when you need immediate battery status updates or after making
        changes in the Tado app.
        """
        tado_api = get_tado_api()

        if not hasattr(tado_api, 'cloud_api') or not tado_api.cloud_api:
            raise HTTPException(status_code=503, detail="Cloud API not available")

        cloud_api = tado_api.cloud_api

        if not cloud_api.is_authenticated():
            raise HTTPException(status_code=401, detail="Not authenticated with Tado Cloud API")

        try:
            # Import sync module
            from .sync import TadoCloudSync
            sync = TadoCloudSync(tado_api.state_manager.db_path)

            result = {}

            if battery_only:
                # Fast refresh: battery and status data only
                logger.info("Refreshing battery/status data from cloud...")
                zone_states = await cloud_api.get_zone_states(force_refresh=True)
                devices = await cloud_api.get_device_list(force_refresh=True)

                if devices:
                    result['devices_synced'] = len(devices)
                    logger.info(f"Synced {len(devices)} devices (battery status)")

                # Sync to database
                await sync.sync_all(
                        cloud_api,
                        home_data=False,  # Skip
                        zones_data=False,  # Skip
                        zone_states_data=zone_states,
                        devices_data=devices
                    )

                result['refreshed'] = ['battery_status', 'device_status']
            else:
                # Full refresh: all data
                logger.info("Refreshing all cloud data...")
                home_info = await cloud_api.get_home_info(force_refresh=True)
                zones = await cloud_api.get_zones(force_refresh=True)
                zone_states = await cloud_api.get_zone_states(force_refresh=True)
                devices = await cloud_api.get_device_list(force_refresh=True)

                if home_info:
                    result['home_name'] = home_info.get('name')
                if zones:
                    result['zones_synced'] = len(zones)
                if devices:
                    result['devices_synced'] = len(devices)

                # Sync to database
                await sync.sync_all(
                        cloud_api,
                        home_data=home_info,
                        zones_data=zones,
                        zone_states_data=zone_states,
                        devices_data=devices
                    )

                result['refreshed'] = ['home_info', 'zones', 'battery_status', 'device_status']

            # Reload both device and zone caches to pick up changes
            tado_api.state_manager._load_device_cache()
            tado_api.state_manager._load_zone_cache()

            result['success'] = True
            result['timestamp'] = time.time()
            return result

        except Exception as e:
            logger.error(f"Error refreshing cloud data: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to refresh cloud data: {str(e)}")

    @app.get("/purgehistory/info", tags=["Admin"])
    async def get_history_status(api_key: Optional[str] = Depends(get_api_key)):
        """Get history records status, number of records, files size and oldest record."""

        tado_api = get_tado_api()
        return tado_api.state_manager.get_device_history_status_info(tado_api.cloud_api.purge_history_days)

    @app.post("/purgehistory/now", tags=["Admin"])
    async def purge_history_data(
        days: Optional[int] = None,
        api_key: Optional[str] = Depends(get_api_key)
    ):
        """Manually clean database from old history records."""

        tado_api = get_tado_api()
        if days is None:
            if tado_api.cloud_api.purge_history_days is not None:
                days = tado_api.cloud_api.purge_history_days
            else:
                days = 365  # Default to keeping 1 year of history OR from command line argument

        if not isinstance(days, int):
            raise HTTPException(status_code=400, detail="Days must be an integer")

        if days < 7:
            raise HTTPException(status_code=400, detail="Days must be greater than or equal to 7")

        return tado_api.state_manager.purge_device_history(days)

    return app
