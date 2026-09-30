"""
Discovery Agent - Handles device discovery and registration
Listens for device announcements and registers them in the semantic store.

Project: PINV01-24 - FIUNA
"""

import asyncio
import json
from typing import Dict, Any, Optional, List, Set
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import socket

from .base_agent import EdgeAgent, AgentConfig, AgentState


@dataclass
class DiscoveryConfig(AgentConfig):
    """Configuration for the Discovery Agent."""
    agent_type: str = "discovery"
    listen_port: int = 5000
    listen_host: str = "0.0.0.0"
    announcement_endpoint: str = "/api/devices/register"
    health_check_interval: float = 30.0  # seconds
    device_timeout: float = 120.0  # seconds before marking inactive
    enable_http_server: bool = True
    enable_multicast: bool = False
    multicast_group: str = "224.0.0.1"
    multicast_port: int = 5001


class DiscoveryAgent(EdgeAgent):
    """
    Agent responsible for discovering and registering IoT devices.

    Features:
    - HTTP endpoint for device announcements (like Flask server)
    - Optional multicast discovery
    - Health checking of registered devices
    - Automatic state management (active/inactive)
    """

    def __init__(self, config: Optional[DiscoveryConfig] = None):
        super().__init__(config or DiscoveryConfig())
        self.config: DiscoveryConfig = self.config

        self._registered_devices: Dict[str, Dict[str, Any]] = {}
        self._pending_registrations: asyncio.Queue = asyncio.Queue()
        self._http_server = None
        self._last_health_check = datetime.utcnow()

    async def setup(self) -> bool:
        """Setup the discovery agent."""
        self.logger.info("Setting up Discovery Agent")

        try:
            # Initialize HTTP server if enabled
            if self.config.enable_http_server:
                await self._setup_http_server()

            return True

        except Exception as e:
            self.logger.error(f"Setup failed: {e}")
            return False

    async def _setup_http_server(self):
        """Setup a simple HTTP server for device announcements."""
        # Using aiohttp for async HTTP - but keeping it optional
        try:
            from aiohttp import web

            app = web.Application()
            app.router.add_post(self.config.announcement_endpoint, self._handle_announcement)
            app.router.add_get('/api/devices', self._handle_get_devices)
            app.router.add_get('/api/devices/{device_id}', self._handle_get_device)
            app.router.add_get('/health', self._handle_health)

            runner = web.AppRunner(app)
            await runner.setup()

            self._http_server = web.TCPSite(
                runner,
                self.config.listen_host,
                self.config.listen_port
            )
            await self._http_server.start()

            self.logger.info(f"HTTP server listening on {self.config.listen_host}:{self.config.listen_port}")

        except ImportError:
            self.logger.warning("aiohttp not available, HTTP server disabled")
            self.config.enable_http_server = False

    async def _handle_announcement(self, request) -> 'web.Response':
        """Handle incoming device announcement."""
        from aiohttp import web

        try:
            data = await request.json()
            self.logger.info(f"Received announcement: {data.get('id', 'unknown')}")

            # Queue for processing
            await self._pending_registrations.put(data)

            return web.json_response({
                'status': 'accepted',
                'message': 'Device registration queued'
            })

        except Exception as e:
            self.logger.error(f"Announcement error: {e}")
            return web.json_response(
                {'status': 'error', 'message': str(e)},
                status=400
            )

    async def _handle_get_devices(self, request) -> 'web.Response':
        """Return all registered devices."""
        from aiohttp import web
        return web.json_response(self._registered_devices)

    async def _handle_get_device(self, request) -> 'web.Response':
        """Return a specific device."""
        from aiohttp import web
        device_id = request.match_info['device_id']

        if device_id in self._registered_devices:
            return web.json_response(self._registered_devices[device_id])
        else:
            return web.json_response(
                {'error': 'Device not found'},
                status=404
            )

    async def _handle_health(self, request) -> 'web.Response':
        """Health check endpoint."""
        from aiohttp import web
        return web.json_response({
            'status': 'healthy',
            'agent_id': self.agent_id,
            'devices_registered': len(self._registered_devices),
            'timestamp': datetime.utcnow().isoformat()
        })

    async def process(self) -> None:
        """Main processing loop."""
        # Process pending registrations
        await self._process_registrations()

        # Periodic health check
        if self._should_health_check():
            await self._check_device_health()
            self._last_health_check = datetime.utcnow()

    async def _process_registrations(self) -> None:
        """Process queued device registrations."""
        processed = 0
        max_per_cycle = 10  # Limit to prevent blocking

        while not self._pending_registrations.empty() and processed < max_per_cycle:
            try:
                device_data = self._pending_registrations.get_nowait()
                await self._register_device(device_data)
                processed += 1
            except asyncio.QueueEmpty:
                break

    async def _register_device(self, device_data: Dict[str, Any]) -> Dict[str, Any]:
        """Register or update a device."""
        # Normalize device ID
        device_id = device_data.get('id') or device_data.get('device_id')
        if not device_id:
            self.logger.warning("Device registration missing ID")
            return {'success': False, 'error': 'Missing device ID'}

        now = datetime.utcnow().isoformat()

        # Check if already registered
        is_new = device_id not in self._registered_devices

        # Build device record
        device_record = {
            'id': device_id,
            'ip': device_data.get('ip') or device_data.get('ip_address', ''),
            'hardware': device_data.get('hardware') or device_data.get('hardware_type', 'Unknown'),
            'state': 'active',
            'last-seen': now,
            'registered-at': self._registered_devices.get(device_id, {}).get('registered-at', now)
        }

        # Add optional fields
        for key in ['mac_address', 'firmware_version', 'location', 'role']:
            if key in device_data:
                device_record[key] = device_data[key]

        # Store
        self._registered_devices[device_id] = device_record

        # Emit event
        event_type = 'device_discovered' if is_new else 'device_updated'
        await self._emit(event_type, device_record)

        self.logger.info(f"{'Registered new' if is_new else 'Updated'} device: {device_id}")

        return {'success': True, 'device_id': device_id, 'is_new': is_new}

    def _should_health_check(self) -> bool:
        """Check if it's time for a health check."""
        elapsed = (datetime.utcnow() - self._last_health_check).total_seconds()
        return elapsed >= self.config.health_check_interval

    async def _check_device_health(self) -> None:
        """Check health of all registered devices."""
        self.logger.debug("Running device health check")
        now = datetime.utcnow()
        timeout = timedelta(seconds=self.config.device_timeout)

        for device_id, device in list(self._registered_devices.items()):
            try:
                last_seen = datetime.fromisoformat(device['last-seen'])
                if now - last_seen > timeout:
                    if device['state'] == 'active':
                        self.logger.info(f"Device {device_id} marked inactive (timeout)")
                        device['state'] = 'inactive'
                        await self._emit('device_inactive', device)
            except Exception as e:
                self.logger.error(f"Health check error for {device_id}: {e}")

    async def cleanup(self) -> None:
        """Cleanup resources."""
        if self._http_server:
            await self._http_server.stop()
            self.logger.info("HTTP server stopped")

    def get_registered_devices(self) -> Dict[str, Dict[str, Any]]:
        """Get all registered devices."""
        return self._registered_devices.copy()

    def get_active_devices(self) -> List[Dict[str, Any]]:
        """Get only active devices."""
        return [d for d in self._registered_devices.values() if d.get('state') == 'active']

    def get_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific device by ID."""
        return self._registered_devices.get(device_id)

    async def register_device_direct(self, device_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Register a device directly (without HTTP).
        Useful for programmatic registration or testing.
        """
        return await self._register_device(device_data)

    def get_status(self) -> Dict[str, Any]:
        """Get agent status with discovery-specific info."""
        status = super().get_status()
        status.update({
            'total_devices': len(self._registered_devices),
            'active_devices': len(self.get_active_devices()),
            'http_enabled': self.config.enable_http_server,
            'listen_port': self.config.listen_port
        })
        return status
