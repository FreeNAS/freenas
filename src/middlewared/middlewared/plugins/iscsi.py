from middlewared.schema import accepts, Bool, Dict, IPAddr, Int, List, Patch, Str
from middlewared.validators import Range
from middlewared.service import CRUDService, SystemServiceService, ValidationErrors, private
from ipaddr import AddressValueError, IPAddress, IPNetwork, NetmaskValueError

import bidict
import errno
import ipaddress
import re

AUTHMETHOD_LEGACY_MAP = bidict.bidict({
    'None': 'NONE',
    'CHAP': 'CHAP',
    'CHAP Mutual': 'CHAP_MUTUAL',
})
RE_IP_PORT = re.compile(r'^(.+?)(:[0-9]+)?$')


class ISCSIGlobalService(SystemServiceService):

    class Config:
        datastore_extend = 'iscsi.global.config_extend'
        datastore_prefix = 'iscsi_'
        service = 'iscsitarget'
        service_model = 'iscsitargetglobalconfiguration'
        namespace = 'iscsi.global'

    @private
    def config_extend(self, data):
        data['isns_servers'] = data['isns_servers'].split()
        return data

    @accepts(Dict(
        'iscsiglobal_update',
        Str('basename'),
        List('isns_servers', items=[Str('server')]),
        Int('pool_avail_threshold', validators=[Range(min=1, max=99)]),
        Bool('alua', default=False),
    ))
    async def do_update(self, data):
        """
        `alua` is a no-op for FreeNAS.
        """
        old = await self.config()

        new = old.copy()
        new.update(data)

        verrors = ValidationErrors()

        servers = data.get('isns_servers') or []
        for server in servers:
            reg = RE_IP_PORT.search(server)
            if reg:
                ip = reg.group(1)
                if ip and ip[0] == '[' and ip[-1] == ']':
                    ip = ip[1:-1]
                try:
                    ipaddress.ip_address(ip)
                    continue
                except ValueError:
                    pass
            verrors.add('iscsiglobal_update.isns_servers', f'Server "{server}" is not a valid IP(:PORT)? tuple.')

        if verrors:
            raise verrors

        new['isns_servers'] = '\n'.join(servers)

        await self._update_service(old, new)

        if old['alua'] != new['alua']:
            await self.middleware.call('service.start', 'ix-loader')

        return await self.config()


class ISCSIPortalService(CRUDService):

    class Config:
        datastore = 'services.iscsitargetportal'
        datastore_extend = 'iscsi.portal.config_extend'
        datastore_prefix = 'iscsi_target_portal_'
        namespace = 'iscsi.portal'

    @private
    async def config_extend(self, data):
        data['listen'] = []
        for portalip in await self.middleware.call(
            'datastore.query',
            'services.iscsitargetportalip',
            [('portal', '=', data['id'])],
            {'prefix': 'iscsi_target_portalip_'}
        ):
            data['listen'].append({
                'ip': portalip['ip'],
                'port': portalip['port'],
            })
        data['discovery_authmethod'] = AUTHMETHOD_LEGACY_MAP.get(
            data.pop('discoveryauthmethod')
        )
        data['discovery_authgroup'] = data.pop('discoveryauthgroup')
        return data

    async def __validate(self, verrors, data, schema):
        if not data['listen']:
            verrors.add(f'{schema}.listen', 'At least one listen entry is required.')
        else:
            for i in data['listen']:
                filters = [
                    ('iscsi_target_portalip_ip', '=', i['ip']),
                    ('iscsi_target_portalip_port', '=', i['port']),
                ]
                if schema == 'iscsiportal_update':
                    filters.append(('iscsi_target_portalip_portal', '!=', data['id']))
                if await self.middleware.call(
                    'datastore.query', 'services.iscsitargetportalip', filters
                ):
                    verrors.add('{schema}.listen', f'{i["ip"]}:{i["port"]} already in use.')

        if data['discovery_authgroup']:
            if not await self.middleware.call(
                'datastore.query', 'services.iscsitargetauthcredential',
                [('iscsi_target_auth_tag', '=', data['discovery_authgroup'])]
            ):
                verrors.add(
                    f'{schema}.discovery_authgroup',
                    'Auth Group "{data["discovery_authgroup"]}" not found.',
                    errno.ENOENT,
                )
        elif data['discovery_authmethod'] in ('CHAP', 'CHAP_MUTUAL'):
            verrors.add(f'{schema}.discovery_authgroup', 'This field is required if discovery method is set to CHAP or CHAP Mutual.')

    @accepts(Dict(
        'iscsiportal_create',
        Str('comment'),
        Str('discovery_authmethod', default='NONE', enum=['NONE', 'CHAP', 'CHAP_MUTUAL']),
        Int('discovery_authgroup'),
        List('listen', required=True, items=[
            Dict(
                'listen',
                IPAddr('ip', required=True),
                Int('port', default=3260, validators=[Range(min=1, max=65535)]),
            ),
        ]),
        register=True,
    ))
    async def do_create(self, data):
        """
        Create a new iSCSI Portal.

        `discovery_authgroup` is required for CHAP and CHAP_MUTUAL.
        """
        verrors = ValidationErrors()
        await self.__validate(verrors, data, 'iscsiportal_create')
        if verrors:
            raise verrors

        # tag attribute increments sequentially
        data['tag'] = (await self.middleware.call(
            'datastore.query', self._config.datastore, [], {'count': True}
        )) + 1

        listen = data.pop('listen')
        data['discoveryauthgroup'] = data.pop('discovery_authgroup', None)
        data['discoveryauthmethod'] = AUTHMETHOD_LEGACY_MAP.inv.get(data.pop('discovery_authmethod'), 'None')
        pk = await self.middleware.call(
            'datastore.insert', self._config.datastore, data,
            {'prefix': self._config.datastore_prefix}
        )
        try:
            await self.__save_listen(pk, listen)
        except Exception as e:
            await self.middleware.call('datastore.delete', self._config.datastore, pk)
            raise e

        await self._service_change('iscsitarget', 'reload')

        return await self._get_instance(pk)

    async def __save_listen(self, pk, new, old=None):
        """
        Update database with a set new listen IP:PORT tuples.
        It will delete no longer existing addresses and add new ones.
        """
        new_listen_set = set([tuple(i.items()) for i in new])
        old_listen_set = set([tuple(i.items()) for i in old]) if old else set()
        for i in new_listen_set - old_listen_set:
            i = dict(i)
            await self.middleware.call(
                'datastore.insert',
                'services.iscsitargetportalip',
                {'portal': pk, 'ip': i['ip'], 'port': i['port']},
                {'prefix': 'iscsi_target_portalip_'}
            )

        for i in old_listen_set - new_listen_set:
            i = dict(i)
            portalip = await self.middleware.call(
                'datastore.query',
                'services.iscsitargetportalip',
                [('portal', '=', pk), ('ip', '=', i['ip']), ('port', '=', i['port'])],
                {'prefix': 'iscsi_target_portalip_'}
            )
            if portalip:
                await self.middleware.call(
                    'datastore.delete', 'services.iscsitargetportalip', portalip[0]['id']
                )

    @accepts(
        Int('id'),
        Patch(
            'iscsiportal_create',
            'iscsiportal_update',
            ('attr', {'update': True})
        )
    )
    async def do_update(self, pk, data):
        """
        Update iSCSI Portal `id`.
        """

        old = await self._get_instance(pk)

        new = old.copy()
        new.update(data)

        verrors = ValidationErrors()
        await self.__validate(verrors, new, 'iscsiportal_update')
        if verrors:
            raise verrors

        listen = new.pop('listen')
        new['discoveryauthgroup'] = new.pop('discovery_authgroup', None)
        new['discoveryauthmethod'] = AUTHMETHOD_LEGACY_MAP.inv.get(new.pop('discovery_authmethod'), 'None')

        await self.__save_listen(pk, listen, old['listen'])

        await self.middleware.call(
            'datastore.update', self._config.datastore, pk, new,
            {'prefix': self._config.datastore_prefix}
        )

        await self._service_change('iscsitarget', 'reload')

        return await self._get_instance(pk)

    @accepts(Int('id'))
    async def do_delete(self, id):
        """
        Delete iSCSI Portal `id`.
        """
        await self.middleware.call('datastore.delete', self._config.datastore, id)
        # service is currently restarted by datastore/django model


class iSCSITargetAuthCredentialService(CRUDService):
    class Config:
        namespace = 'iscsi.auth'
        datastore = 'services.iscsitargetauthcredential'
        datastore_prefix = 'iscsi_target_auth_'
        datastore_extend = 'iscsi.auth.extend'

    @accepts(Dict(
        'iscsi_auth_create',
        Int('tag'),
        Str('user'),
        Str('secret'),
        Str('peeruser'),
        Str('peersecret'),
        register=True
    ))
    async def do_create(self, data):
        verrors = ValidationErrors()
        await self.validate(data, 'iscsi_auth_create', verrors)

        if verrors:
            raise verrors

        await self.compress(data)
        data['id'] = await self.middleware.call(
            'datastore.insert', self._config.datastore, data,
            {'prefix': self._config.datastore_prefix})
        await self.extend(data)
        await self.middleware.call('service.reload', 'iscsitarget')

        return data

    @accepts(
        Int('id'),
        Patch(
            'iscsi_auth_create',
            'iscsi_auth_update',
            ('attr', {'update': True})
        )
    )
    async def do_update(self, id, data):
        verrors = ValidationErrors()
        old = await self.middleware.call(
            'datastore.query', self._config.datastore, [('id', '=', id)],
            {'extend': self._config.datastore_extend,
             'prefix': self._config.datastore_prefix,
             'get': True})

        new = old.copy()
        new.update(data)

        await self.validate(
            new, 'iscsi_auth_update', verrors)

        if verrors:
            raise verrors

        await self.compress(new)
        await self.middleware.call(
            'datastore.update', self._config.datastore, id, new,
            {'prefix': self._config.datastore_prefix})
        await self.extend(new)

        await self.middleware.call('service.reload', 'iscsitarget')

        return new

    @accepts(Int('id'))
    async def do_delete(self, id):
        return await self.middleware.call(
            'datastore.delete', self._config.datastore, id)

    @private
    async def validate(self, data, schema_name, verrors):
        secret = data.get('secret', '')
        peer_secret = data.get('peersecret', '')
        peer_user = data.get('peeruser', '')

        if len(peer_user) > 0:
            if len(peer_secret) == 0:
                verrors.add(
                    f'{schema_name}.peersecret',
                    'The peer secret is required if you set a peer user.')
            elif peer_secret == secret:
                verrors.add(
                    f'{schema_name}.peersecret',
                    'The peer secret cannot be the same as user secret.')
        else:
            if len(peer_secret) > 0:
                verrors.add(
                    f'{schema_name}.peersecret',
                    'The peer secret is required if you set a peer user.')

        if len(secret) < 12 or len(secret) > 16:
            verrors.add(f'{schema_name}.secret',
                        'Secret must be between 12 and 16 characters.')

        if len(peer_secret) < 12 or len(peer_secret) > 16:
            verrors.add(f'{schema_name}.peersecret',
                        'Secret must be between 12 and 16 characters.')

    @private
    async def extend(self, data):
        secret = data.get('secret', '')
        peersecret = data.get('peersecret', '')

        data['secret'] = await self.middleware.call(
            'notifier.pwenc_decrypt', secret)
        data['peersecret'] = await self.middleware.call(
            'notifier.pwenc_decrypt', peersecret)

        return data

    @private
    async def compress(self, data):
        secret = data.get('secret', '')
        peersecret = data.get('peersecret', '')

        data['secret'] = await self.middleware.call(
            'notifier.pwenc_encrypt', secret)
        data['peersecret'] = await self.middleware.call(
            'notifier.pwenc_encrypt', peersecret)

        return data


class iSCSITargetAuthorizedInitiator(CRUDService):
    class Config:
        namespace = 'iscsi.initiator'
        datastore = 'services.iscsitargetauthorizedinitiator'
        datastore_prefix = 'iscsi_target_initiator_'
        datastore_extend = 'iscsi.initiator.extend'

    @accepts(Dict(
        'iscsi_initiator_create',
        Int('tag'),
        Str('initiators', default='ALL'),
        Str('auth_network', default='ALL'),
        Str('comment'),
        register=True
    ))
    async def do_create(self, data):
        verrors = ValidationErrors()
        await self.compress(data)
        await self.validate(data, 'iscsi_initiator_create', verrors)

        if verrors:
            raise verrors

        await self.extend(data)
        data['tag'] = (await self.query())[-1]['tag'] + 1
        data['id'] = await self.middleware.call(
            'datastore.insert', self._config.datastore, data,
            {'prefix': self._config.datastore_prefix})
        await self.middleware.call('service.reload', 'iscsitarget')

        return data

    @accepts(
        Int('id'),
        Patch(
            'iscsi_initiator_create',
            'iscsi_initiator_update',
            ('attr', {'update': True})
        )
    )
    async def do_update(self, id, data):
        verrors = ValidationErrors()
        old = await self.middleware.call(
            'datastore.query', self._config.datastore, [('id', '=', id)],
            {'extend': self._config.datastore_extend,
             'prefix': self._config.datastore_prefix,
             'get': True})

        new = old.copy()
        new.update(data)

        await self.compress(new)
        await self.validate(
            new, 'iscsi_initiator_update', verrors)

        if verrors:
            raise verrors

        await self.extend(new)
        await self.middleware.call(
            'datastore.update', self._config.datastore, id, new,
            {'prefix': self._config.datastore_prefix})

        await self.middleware.call('service.reload', 'iscsitarget')

        return new

    @private
    async def compress(self, data):
        nets = data['auth_network'].split()
        data['auth_network'] = nets

        return data

    @private
    async def extend(self, data):
        nets = data['auth_network']

        if isinstance(nets, str):
            nets = nets.split()  # Query

        data['auth_network'] = '\n'.join(nets)

        return data

    @accepts(Int('id'))
    async def do_delete(self, id):
        return await self.middleware.call(
            'datastore.delete', self._config.datastore, id)

    @private
    async def validate(self, data, schema_name, verrors):
        auth_network = data['auth_network']
        for net in auth_network:
            net = net.strip().upper()

            if net == 'ALL':
                continue

            try:
                IPNetwork(net)
            except (NetmaskValueError, ValueError):
                try:
                    IPAddress(net)
                except (AddressValueError, ValueError):
                    verrors.add(
                        f'{schema_name}.auth_network',
                        'The field is a not a valid IP address or network.'
                        ' The keyword "ALL" can be used to allow '
                        'everything.'
                    )
