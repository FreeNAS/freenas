import aiohttp
import contextlib
import jwt
import json
import enum
import asyncio
import os
import time

from middlewared.service_exception import CallError, ValidationError
from middlewared.schema import Dict, Str, Bool, returns
from middlewared.service import (accepts, job, Service,
                                 private, ValidationErrors)
from .utils import GlusterConfig
from uuid import uuid4


SECRETS_FILE = GlusterConfig.SECRETS_FILE.value
LOCAL_WEBHOOK_URL = GlusterConfig.LOCAL_WEBHOOK_URL.value
EVENT_TIMEOUT = GlusterConfig.EVENT_TIMEOUT.value
LOCK = "LOCAL_EVENTS_LOCK"


class AllowedEvents(enum.Enum):
    VOLUME_START = 'VOLUME_START'
    VOLUME_STOP = 'VOLUME_STOP'
    CTDB_START = 'CTDB_START'
    CTDB_STOP = 'CTDB_STOP'
    SMB_STOP = 'SMB_STOP'
    CLJOBS_PROCESS = 'CLJOBS_PROCESS'
    SYSTEM_VOL_CHANGE = 'SYSTEM_VOL_CHANGE'
    CLUSTER_ACCOUNT = 'CLUSTER_ACCOUNT'


class GlusterLocalEventsService(Service):

    JWT_SECRET = None

    class Config:
        namespace = 'gluster.localevents'
        cli_namespace = 'service.gluster.localevents'

    @private
    async def validate(self, data):
        verrors = ValidationErrors()
        allowed = [i.value for i in AllowedEvents]

        if data['event'] not in allowed:
            verrors.add(
                f'localevent_send.{data["event"]}',
                f'event: "{data["event"]}" is not allowed',
            )

        vols = await self.middleware.call('gluster.volume.list')
        if data['name'] not in vols:
            verrors.add(
                f'localevent_send.{data["name"]}',
                f'gluster volume: "{data["name"]}" does not exist',
            )

        verrors.check()

    @accepts(Dict(
        'localevent_send',
        Str('event', required=True),
        Str('name', required=True),
        Bool('forward', default=True),
        additional_attrs=True,
    ))
    @private
    @job(lock=LOCK)
    async def send(self, job, data):
        await self.middleware.call('gluster.localevents.validate', data)
        secret = await self.middleware.call('gluster.localevents.get_set_jwt_secret')
        token = jwt.encode({'ts': int(time.time()), 'msg_id': uuid4().hex}, secret, algorithm='HS256')
        headers = {'JWTOKEN': token, 'content-type': 'application/json'}
        payload = await self.middleware.call('clpwenc.encrypt', json.dumps(data))

        async with aiohttp.ClientSession() as sess:
            status = reason = None
            try:
                res = await sess.post(
                    LOCAL_WEBHOOK_URL,
                    headers=headers,
                    json={'payload': payload},
                    timeout=EVENT_TIMEOUT
                )
            except asyncio.exceptions.TimeoutError:
                status = 500
                reason = 'Timed out waiting for a response'
            else:
                if res.status == 422:
                    msg = await res.json()
                    raise ValidationError('gluster.localevents.send', msg['message'], msg['errno'])

                elif res.status != 200:
                    status = res.status
                    reason = res.reason

            if status is not None:
                # something failed
                raise CallError(
                    f'Failed to send event: {data["event"]} with status code of: {status} '
                    f'with reason: {reason}'
                )

    @accepts()
    @returns(Str())
    def get_set_jwt_secret(self):
        """
        Return the secret key used to encode/decode
        JWT messages for sending/receiving gluster
        events.

        Note: this secret is only used for messages
        that are destined for the api endpoint at
        http://*:6000/_clusterevents for each peer
        in the trusted storage pool.

        WARNING: clustering APIs are not intended for 3rd-party consumption and may result
        in a misconfigured SCALE cluster, production outage, or data loss.
        """
        if self.JWT_SECRET is None:
            with contextlib.suppress(FileNotFoundError):
                with open(SECRETS_FILE, 'r') as f:
                    secret = f.read().strip()
                    if secret:
                        self.JWT_SECRET = secret

        return self.JWT_SECRET

    @accepts(Dict(
        'add_secret',
        Str('secret', required=True),
        Bool('force', default=False),
    ))
    @returns()
    def add_jwt_secret(self, data):
        """
        Add a `secret` key used to encode/decode
        JWT messages for sending/receiving gluster
        events.

        `secret` String representing the key to be used
                    to encode/decode JWT messages
        `force` Boolean if set to True, will forcefully
                    wipe any existing jwt key for this
                    peer. Note, if forcefully adding a
                    new key, the other peers in the TSP
                    will also need to be sent this key.

        Note: this secret is only used for messages
        that are destined for the api endpoint at
        http://*:6000/_clusterevents for each peer
        in the trusted storage pool.

        WARNING: clustering APIs are not intended for 3rd-party consumption and may result
        in a misconfigured SCALE cluster, production outage, or data loss.
        """

        if not data['force'] and self.JWT_SECRET is not None:
            verrors = ValidationErrors()
            verrors.add(
                'localevent_add_jwt_secret.{data["secret"]}',
                'An existing secret key already exists. Use force to ignore this error'
            )
            verrors.check()

        self.JWT_SECRET = data['secret']
        with open(SECRETS_FILE, 'w+') as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(data['secret'])
