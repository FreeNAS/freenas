import asyncio
import logging

from middlewared.service import CallError, Service
from middlewared.utils.disks import get_disks_with_identifiers
from middlewared.utils.version import parse_version_string

from .mixin import TNCAPIMixin
from .status_utils import Status
from .utils import get_account_id_and_system_id
from .urls import get_heartbeat_url


logger = logging.getLogger('truenas_connect')


class TNCHeartbeatService(Service, TNCAPIMixin):

    HEARTBEAT_INTERVAL = 5

    class Config:
        namespace = 'tn_connect.heartbeat'
        private = True

    async def call(self, url, mode, payload=None, **kwargs):
        config = await self.middleware.call('tn_connect.config_internal')
        return await self._call(url, mode, payload=payload, headers=await self.auth_headers(config), **(kwargs or {}))

    async def start(self):
        tnc_config = await self.middleware.call('tn_connect.config_internal')
        creds = get_account_id_and_system_id(tnc_config)
        if tnc_config['status'] != Status.CONFIGURED.name or creds is None:
            raise CallError('TrueNAS Connect is not configured properly')

        heartbeat_url = get_heartbeat_url(tnc_config).format(
            system_id=creds['system_id'],
            version=parse_version_string(await self.middleware.call('system.version_short')),
        )
        disk_mapping = await self.middleware.run_in_thread(get_disks_with_identifiers)
        while True:
            sleep_error = False
            resp = await self.call(heartbeat_url, 'post', await self.payload(disk_mapping), get_response=False)
            if resp['error'] is not None:
                logger.debug('TNC Heartbeat: Failed to connect to heart beat service (%s)', resp['error'])
                sleep_error = True
            else:
                match resp['status_code']:
                    case 202:
                        logger.debug('TNC Heartbeat: Received 202')
                    case 200:
                        logger.debug('TNC Heartbeat: Received 200')
                    case 400:
                        logger.debug('TNC Heartbeat: Received 400')
                    case 401:
                        logger.debug('TNC Heartbeat: Received 401')
                    case 500:
                        logger.debug('TNC Heartbeat: Received 500')
                        sleep_error = True
                    case _:
                        logger.debug('TNC Heartbeat: Received unknown status code %r', resp['status_code'])
                        sleep_error = True

            if sleep_error:
                pass
            else:
                await asyncio.sleep(self.HEARTBEAT_INTERVAL)


    async def payload(self, disk_mapping=None):
        return {}
        return await self.middleware.call('reporting.realtime.stats', disk_mapping)
