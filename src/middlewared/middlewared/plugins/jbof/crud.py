import asyncio
import subprocess
import time

import middlewared.sqlalchemy as sa
from middlewared.plugins.jbof.redfish import (InvalidCredentialsError,
                                              RedfishClient)
from middlewared.schema import (Bool, Dict, Int, IPAddr, Password, Patch, Str,
                                accepts, returns)
from middlewared.service import (CallError, CRUDService, ValidationErrors, job,
                                 private)
from middlewared.utils.license import LICENSE_ADDHW_MAPPING

from .functions import (decode_static_ip, get_sys_class_nvme,
                        initiator_ip_from_jbof_static_ip, initiator_static_ip,
                        jbof_static_ip, jbof_static_ip_from_initiator_ip,
                        static_ip_netmask_int, static_ip_netmask_str,
                        static_mtu)


class JBOFModel(sa.Model):
    __tablename__ = 'storage_jbof'

    id = sa.Column(sa.Integer(), primary_key=True)

    jbof_description = sa.Column(sa.String(120), nullable=True)

    # When performing static (code-based) assignment of data-plane IPs, we
    # want each JBOD to have a deterministic unique index that counts up from
    # zero (without any gaps, which rules out using the id).  This will not be
    # part of the public API.
    jbof_index = sa.Column(sa.Integer(), unique=True)
    jbof_uuid = sa.Column(sa.Text(), nullable=False, unique=True)

    jbof_mgmt_ip1 = sa.Column(sa.String(45), nullable=False)
    jbof_mgmt_ip2 = sa.Column(sa.String(45))
    jbof_mgmt_username = sa.Column(sa.String(120))
    jbof_mgmt_password = sa.Column(sa.EncryptedText())


class JBOFService(CRUDService):

    class Config:
        service = 'jbof'
        datastore = 'storage.jbof'
        datastore_prefix = "jbof_"
        cli_private = True
        role_prefix = 'JBOF'

    ENTRY = Dict(
        'jbof_entry',
        Int('id', required=True),

        Str('description'),

        # Redfish
        IPAddr('mgmt_ip1', required=True),
        IPAddr('mgmt_ip2', required=False),
        Str('mgmt_username', required=True),
        Password('mgmt_password', required=True),
    )

    # Number of seconds we need to wait for a ES24N to start responding on
    # a newly configured IP
    JBOF_CONFIG_DELAY_SECS = 20

    @private
    async def add_index(self, data):
        """Add a private unique index (0-255) to the entry if not already present."""
        if 'index' not in data:
            index = await self.middleware.call('jbof.next_index')
            if index is not None:
                data['index'] = index
            else:
                raise CallError('Could not generate an index.')
        return data

    @private
    async def validate(self, data, schema_name, old=None):
        verrors = ValidationErrors()

        # Check license
        license_count = await self.middleware.call("jbof.licensed")
        if license_count == 0:
            verrors.add(f"{schema_name}.mgmt_ip1", "This feature is not licensed")
        else:
            if old is None:
                # We're adding a new JBOF - have we exceeded the license?
                count = await self.middleware.call('jbof.query', [], {'count': True})
                if count >= license_count:
                    verrors.add(f"{schema_name}.mgmt_ip1",
                                f"Already configured the number of licensed emclosures: {license_count}")

        # Ensure redfish connects to mgmt1 (incl login)
        mgmt_ip1 = data.get('mgmt_ip1')
        if not RedfishClient.is_redfish(mgmt_ip1):
            verrors.add(f"{schema_name}.mgmt_ip1", "Not a redfish management interface")
        else:
            redfish1 = RedfishClient(f'https://{mgmt_ip1}')
            try:
                redfish1.login(data['mgmt_username'], data['mgmt_password'])
                RedfishClient.cache_set(mgmt_ip1, redfish1)
            except InvalidCredentialsError:
                verrors.add(f"{schema_name}.mgmt_username", "Invalid username or password")

        # If mgmt_ip2 was supplied, ensure it matches to the same system as mgmt_ip1
        mgmt_ip2 = data.get('mgmt_ip2')
        if mgmt_ip2:
            if not RedfishClient.is_redfish(mgmt_ip2):
                verrors.add(f"{schema_name}.mgmt_ip2", "Not a redfish management interface")
            else:
                redfish2 = RedfishClient(f'https://{mgmt_ip2}')
                if redfish1.product != redfish2.product:
                    verrors.add(f"{schema_name}.mgmt_ip2", "Product does not match other IP address.")
                if redfish1.uuid != redfish2.uuid:
                    verrors.add(f"{schema_name}.mgmt_ip2", "UUID does not match other IP address.")

        # When adding a new JBOF - do we have a UUID clash?
        if old is None:
            existing_uuids = [d['uuid'] for d in (await self.middleware.call('jbof.query', [], {'select': ['uuid']}))]
            if redfish1.uuid in existing_uuids:
                verrors.add(f"{schema_name}.mgmt_ip1", "Supplied JBOF already in database (UUID)")
            else:
                # Inject UUID
                data['uuid'] = redfish1.uuid

        await self.add_index(data)
        return verrors, data

    @accepts(
        Patch(
            'jbof_entry', 'jbof_create',
            ('rm', {'name': 'id'}),
            register=True
        )
    )
    async def do_create(self, data):
        """
        Create a new JBOF.

        This will use the supplied Redfish credentials to configure the data plane on
        the expansion shelf for direct connection to ROCE capable network cards on
        the TrueNAS head unit.

        `description` Optional description of the JBOF.

        `mgmt_ip1` IP of 1st Redfish management interface.

        `mgmt_ip2` Optional IP of 2nd Redfish management interface.

        `mgmt_username` Redfish administrative username.

        `mgmt_password` Redfish administrative password.
        """
        verrors, data = await self.validate(data, 'jbof_create')
        verrors.check()

        mgmt_ip = data['mgmt_ip1']
        shelf_index = data['index']

        # Everything looks good so far.  Attempt to hardwire the dataplane.
        try:
            await self.middleware.call('jbof.hardwire_dataplane', mgmt_ip, shelf_index, 'jbof_create.mgmt_ip1', verrors)
            if verrors:
                await self.middleware.call('jbof.unwire_dataplane', mgmt_ip, shelf_index)
        except Exception as e:
            self.logger.error('Failed to add JBOF', exc_info=True)
            # Try a cleanup
            try:
                await self.middleware.call('jbof.unwire_dataplane', mgmt_ip, shelf_index)
            except Exception:
                pass
            verrors.add('jbof_create.mgmt_ip1', f'Failed to add JBOF: {e}')
        verrors.check()

        # If the caller just supplied mgmt_ip1, let's fetch mgmt_ip2 to store in the DB
        if data.get('mgmt_ip2') in ['', None]:
            try:
                if ip := await self.middleware.call('jbof.alt_mgmt_ip', mgmt_ip):
                    data['mgmt_ip2'] = ip
                    self.logger.info('Detected additional JBOF mgmt IP %r', ip)
                else:
                    self.logger.warning('Unable to determine additional JBOF mgmt IP')
            except Exception:
                self.logger.warning('Unable to detect additional JBOF mgmt IP', exc_info=True)

        data['id'] = await self.middleware.call(
            'datastore.insert', self._config.datastore, data,
            {'prefix': self._config.datastore_prefix})

        return await self.get_instance(data['id'])

    @accepts(
        Int('id', required=True),
        Patch(
            'jbof_create', 'jbof_update',
            ('attr', {'update': True})
        )
    )
    async def do_update(self, id_, data):
        """
        Update JBOF of `id`
        """
        old = await self.get_instance(id_)
        new = old.copy()
        new.update(data)
        verrors, data = await self.validate(new, 'jbof_update', old)
        verrors.check()

        if old['uuid'] != new['uuid']:
            self.logger.debug('Changed UUID of JBOF from %s to %s', old['uuid'], new['uuid'])
            await self.middleware.call('jbof.unwire_dataplane', old['mgmt_ip1'], old['index'])
            await self.middleware.call('jbof.hardwire_dataplane', new['mgmt_ip1'], new['index'],
                                       'jbof_update.mgmt_ip1', verrors)

        await self.middleware.call(
            'datastore.update', self._config.datastore, id_, new,
            {'prefix': self._config.datastore_prefix})

        return await self.get_instance(id_)

    @accepts(Int('id'), Bool('force', default=False))
    async def do_delete(self, id_, force):
        """
        Delete a JBOF by ID.
        """
        # Will make a best-effort un tear down existing connections / wiring
        # To do that we first need to fetch the config.
        data = await self.get_instance(id_)

        try:
            await self.middleware.run_in_thread(self.ensure_redfish_client_cached, data)
        except Exception as e:
            if force:
                # If we have lost communication with the redfish interface for any reason
                # we might still want to proceed with removing the JBOF, even without tearing
                # down the shelf configuration.  However, we wil still want to undo the
                # host configuration.
                self.logger.debug('Unable to ensure redfish client for JBOF %r. Forcing.', data['id'])
            else:
                raise e

        try:
            await self.middleware.call('jbof.unwire_dataplane', data['mgmt_ip1'], data['index'])
        except Exception:
            self.logger.debug('Unable to unwire JBOF @%r', data['mgmt_ip1'])
            await self.middleware.call('alert.oneshot_create', 'JBOFTearDownFailure', None)

        # Now delete the entry
        response = await self.middleware.call('datastore.delete', self._config.datastore, id_)
        return response

    @private
    def get_mgmt_ips(self, mgmt_ip):
        redfish = RedfishClient.cache_get(mgmt_ip)
        return redfish.mgmt_ips()

    @private
    def alt_mgmt_ip(self, mgmt_ip):
        other_mgmt_ips = filter(lambda x: x != mgmt_ip, self.get_mgmt_ips(mgmt_ip))
        for ip in other_mgmt_ips:
            if RedfishClient.is_redfish(ip):
                return ip
        # If unable to talk to one, pick the first one.  Maybe connectivity will be restored later.
        if len(other_mgmt_ips):
            self.logger.info('Unable to validate connectivity to alternate JBOF mgmt IP %r', other_mgmt_ips[0])

    @private
    def ensure_redfish_client_cached(self, data):
        """Synchronous function to ensure we have a redfish client in cache."""
        mgmt_ip = data['mgmt_ip1']
        username = data.get('mgmt_username')
        password = data.get('mgmt_password')
        try:
            RedfishClient.cache_get(mgmt_ip)
        except KeyError:
            # This could take a while to login, etc ... hence synchronous wrapper.
            redfish = RedfishClient(f'https://{mgmt_ip}', username, password)
            RedfishClient.cache_set(mgmt_ip, redfish)

    @accepts(roles=['JBOF_READ'])
    @returns(Int())
    async def licensed(self):
        """Return a count of the number of JBOF units licensed."""
        result = 0
        # Do we have a license at all?
        license_ = await self.middleware.call('system.license')
        if not license_:
            return result

        # check if this node's system serial matches the serial in the license
        local_serial = (await self.middleware.call('system.dmidecode_info'))['system-serial-number']
        if local_serial not in (license_['system_serial'], license_['system_serial_ha']):
            return result

        # Check to see if we're licensed to attach a JBOF
        if license_['addhw']:
            for quantity, code in license_['addhw']:
                if code not in LICENSE_ADDHW_MAPPING:
                    self.logger.warning('Unknown additional hardware code %d', code)
                    continue
                name = LICENSE_ADDHW_MAPPING[code]
                if name == 'ES24N':
                    result += quantity
        return result

    @private
    async def next_index(self):
        existing_indices = [d['index'] for d in (await self.middleware.call('jbof.query', [], {'select': ['index']}))]
        for index in range(256):
            if index not in existing_indices:
                return index

    @private
    async def hardwire_dataplane(self, mgmt_ip, shelf_index, schema, verrors):
        """Hardware the dataplane interfaces of the specified JBOF.

        Configure the data plane network interfaces on the JBOF to
        previously determined subnets.

        Then attempt to connect using all the available RDMA capable
        interfaces.
        """
        await self.middleware.call('jbof.hardwire_shelf', mgmt_ip, shelf_index)
        await self.middleware.call('jbof.hardwire_host', mgmt_ip, shelf_index, schema, verrors)
        if not verrors:
            await self.middleware.call('jbof.attach_drives', mgmt_ip, shelf_index, schema, verrors)

    @private
    def fabric_interface_choices(self, mgmt_ip):
        redfish = RedfishClient.cache_get(mgmt_ip)
        return redfish.fabric_ethernet_interfaces()

    @private
    def fabric_interface_macs(self, mgmt_ip):
        """Return a dict keyed by IP address where the value is the corresponding MAC address."""
        redfish = RedfishClient.cache_get(mgmt_ip)
        macs = {}
        for uri in self.fabric_interface_choices(mgmt_ip):
            netdata = redfish.get_uri(uri)
            for address in netdata['IPv4Addresses']:
                macs[address['Address']] = netdata['MACAddress']
        return macs

    @private
    def hardwire_shelf(self, mgmt_ip, shelf_index):
        redfish = RedfishClient.cache_get(mgmt_ip)
        shelf_interfaces = redfish.fabric_ethernet_interfaces()

        # Let's record the link status for each interface
        up_before = set()
        for uri in shelf_interfaces:
            status = redfish.link_status(uri)
            if status == 'LinkUp':
                up_before.add(uri)

        # Modify all the interfaces
        for (eth_index, uri) in enumerate(shelf_interfaces):
            address = jbof_static_ip(shelf_index, eth_index)
            redfish.configure_fabric_interface(uri, address, static_ip_netmask_str(address), mtusize=static_mtu())

        # Wait for all previously up interfaces to come up again
        up_after = set()
        retries = 0
        while retries < JBOFService.JBOF_CONFIG_DELAY_SECS and up_before - up_after:
            for uri in up_before:
                if uri not in up_after:
                    status = redfish.link_status(uri)
                    if status == 'LinkUp':
                        up_after.add(uri)
            time.sleep(1)
            retries += 1
        if up_before - up_after:
            self.logger.debug('Timed-out waiting for interfaces to come up')
            # Allow this to continue as we still might manage to ping it.
        else:
            self.logger.debug('Configured JBOF #%r', shelf_index)

    @private
    def unwire_shelf(self, mgmt_ip):
        redfish = RedfishClient.cache_get(mgmt_ip)
        for uri in redfish.fabric_ethernet_interfaces():
            redfish.configure_fabric_interface(uri, '0.0.0.0', '255.255.255.0', True, mtusize=1500)

    @private
    async def hardwire_host(self, mgmt_ip, shelf_index, schema, verrors):
        """Discover which direct links exist to the specified expansion shelf."""
        # See how many interfaces are available on the expansion shelf
        shelf_ip_to_mac = await self.middleware.call('jbof.fabric_interface_macs', mgmt_ip)

        # Setup a dict with the expected IP pairs
        shelf_ip_to_host_ip = {}
        for idx, _ in enumerate((await self.middleware.call('jbof.fabric_interface_choices', mgmt_ip))):
            shelf_ip_to_host_ip[jbof_static_ip(shelf_index, idx)] = initiator_static_ip(shelf_index, idx)

        # Let's check that we have the expected hardwired IPs on the shelf
        if set(shelf_ip_to_mac) != set(shelf_ip_to_host_ip):
            # This should not happen
            verrors.add(schema, 'JBOF does not have expected IPs.'
                        f'Expected: {shelf_ip_to_host_ip}, has: {shelf_ip_to_mac}')
            return

        if await self.middleware.call('failover.licensed'):
            # HA system
            if not await self.middleware.call('failover.remote_connected'):
                verrors.add(schema, 'Unable to contact remote controller')
                return

            this_node = await self.middleware.call('failover.node')
            if this_node == 'MANUAL':
                verrors.add(schema, 'Unable to determine this controllers position in chassis')
                return

            connected_shelf_ips = []
            results = await asyncio.gather(
                *[self.hardwire_node(node, shelf_index, shelf_ip_to_mac) for node in ('A', 'B')]
            )
            for (node, connected_shelf_ips) in zip(('A', 'B'), results):
                if not connected_shelf_ips:
                    # Failed to connect any IPs => error
                    verrors.add(schema, f'Unable to communicate with the expansion shelf (node {node})')
                    return
                self.logger.debug('Configured node %r: %r', node, connected_shelf_ips)
        else:
            connected_shelf_ips = await self.hardwire_node('', shelf_index, shelf_ip_to_mac)
            if not connected_shelf_ips:
                # Failed to connect any IPs => error
                verrors.add(schema, 'Unable to communicate with the expansion shelf')
                return
            self.logger.debug('Configured node: %r', connected_shelf_ips)

    @private
    async def hardwire_node(self, node, shelf_index, shelf_ip_to_mac, skip_ips=[]):
        localnode = not node or node == await self.middleware.call('failover.node')
        # Next see what RDMA-capable links are available on the host
        # Also setup a map for frequent use below
        if localnode:
            links = await self.middleware.call('rdma.get_link_choices')
        else:
            try:
                links = await self.middleware.call('failover.call_remote', 'rdma.get_link_choices')
            except CallError as e:
                if e.errno != CallError.ENOMETHOD:
                    raise
                self.logger.warning('Cannot hardwire remote node')
                return []

        # First check to see if any interfaces that were previously configured
        # for this shelf are no longer applicable (they might have been moved to
        # a different port on the JBOF).
        connected_shelf_ips = set()
        dirty = False
        configured_interfaces = await self.middleware.call('rdma.interface.query')
        configured_interface_names = [interface['ifname'] for interface in configured_interfaces
                                      if interface['node'] == node]
        for interface in configured_interfaces:
            if node and node != interface['node']:
                continue
            host_ip = interface['address']
            shelf_ip = jbof_static_ip_from_initiator_ip(host_ip)
            value = decode_static_ip(host_ip)
            if value and value[0] == shelf_index:
                # This is supposed to be connected to our shelf.  Check connectivity.
                if await self.middleware.call('rdma.interface.ping', node, interface['ifname'],
                                              shelf_ip, shelf_ip_to_mac[shelf_ip]):
                    # This config looks good, keep it.
                    connected_shelf_ips.add(shelf_ip)
                    if node:
                        self.logger.info(f'Validated existing link on node {node}: {host_ip} -> {shelf_ip}')
                    else:
                        self.logger.info(f'Validated existing link: {host_ip} -> {shelf_ip}')
                else:
                    self.logger.info('Removing RDMA interface that cannot connect to JBOF')
                    await self.middleware.call('rdma.interface.delete', interface['id'])
                    dirty = True
        for shelf_ip in shelf_ip_to_mac:
            if shelf_ip in connected_shelf_ips or shelf_ip in skip_ips:
                continue
            # Try each remaining interface
            if dirty:
                configured_interfaces = await self.middleware.call('rdma.interface.query')
                configured_interface_names = [interface['ifname'] for interface in configured_interfaces
                                              if interface['node'] == node]
                dirty = False
            for link in links:
                ifname = link['rdma']
                if ifname not in configured_interface_names:
                    host_ip = initiator_ip_from_jbof_static_ip(shelf_ip)
                    payload = {
                        'ifname': ifname,
                        'address': host_ip,
                        'prefixlen': static_ip_netmask_int(),
                        'mtu': static_mtu(),
                        'check': {'ping_ip': shelf_ip,
                                  'ping_mac': shelf_ip_to_mac[shelf_ip]}
                    }
                    if node:
                        payload['node'] = node
                    if await self.middleware.call('rdma.interface.create', payload):
                        dirty = True
                        connected_shelf_ips.add(shelf_ip)
                        # break out of the ifname loop
                        if node:
                            self.logger.info(f'Created link on node {node}: {host_ip} -> {shelf_ip}')
                        else:
                            self.logger.info(f'Created link: {host_ip} -> {shelf_ip}')
                        break
        return list(connected_shelf_ips)

    @private
    async def attach_drives(self, mgmt_ip, shelf_index, schema, verrors):
        """Attach drives from the specified expansion shelf."""
        if await self.middleware.call('failover.licensed'):
            # HA system
            if not await self.middleware.call('failover.remote_connected'):
                verrors.add(schema, 'Unable to contact remote controller')
                return

            this_node = await self.middleware.call('failover.node')
            if this_node == 'MANUAL':
                verrors.add(schema, 'Unable to determine this controllers position in chassis')
                return

            await asyncio.gather(*[self.attach_drives_to_node(node, shelf_index) for node in ('A', 'B')])
        else:
            await self.attach_drives_to_node('', shelf_index)

    @private
    async def attach_drives_to_node(self, node, shelf_index):
        localnode = not node or node == await self.middleware.call('failover.node')
        configured_interfaces = await self.middleware.call('rdma.interface.query')
        if localnode:
            for interface in configured_interfaces:
                if interface['node'] != node:
                    continue
                jbof_ip = jbof_static_ip_from_initiator_ip(interface['address'])
                await self.middleware.call('jbof.nvme_connect', jbof_ip)
        else:
            for interface in configured_interfaces:
                if interface['node'] != node:
                    continue
                jbof_ip = jbof_static_ip_from_initiator_ip(interface['address'])
                try:
                    await self.middleware.call('failover.call_remote', 'jbof.nvme_connect', [jbof_ip])
                except CallError as e:
                    if e.errno != CallError.ENOMETHOD:
                        raise

    @private
    def nvme_connect(self, ip, nr_io_queues=16):
        command = ['nvme', 'connect-all', '-t', 'rdma', '-a', ip, '--persistent', '-i', f'{nr_io_queues}']
        ret = subprocess.run(command, capture_output=True)
        if ret.returncode:
            error = ret.stderr.decode() if ret.stderr else ret.stdout.decode()
            if not error:
                error = 'No error message reported'
            self.logger.debug('Failed to execute command: %r with error: %r', " ".join(command), error)
            raise CallError(f'Failed connect NVMe disks: {error}')
        return True

    @private
    def nvme_disconnect(self, ips):
        """Iterate through all nvme devices that have a transport protocol
        of RDMA and disconnect from this host"""
        nqns = []
        for nvme, info in get_sys_class_nvme().items():
            if info['transport_protocol'] == 'rdma' and any(ip == info['transport_address'] for ip in ips):
                nqns.append(info['subsysnqn'])

        len_nqns = len(nqns)
        if len_nqns > 0:
            self.logger.debug('Disconnecting %r NQNs', len_nqns)
            command = ['nvme', 'disconnect', '-n', ','.join(nqns)]
            ret = subprocess.run(command, capture_output=True)
            if ret.returncode:
                error = ret.stderr.decode() if ret.stderr else ret.stdout.decode()
                if not error:
                    error = 'No error message reported'
                raise CallError(f'Failed disconnect NVMe disks: {error}')

    @private
    async def shelf_interface_count(self, mgmt_ip):
        try:
            return len(await self.middleware.call('jbof.fabric_interface_choices', mgmt_ip))
        except Exception:
            # Really only expect 4, but we'll over-estimate for now, as we check them anyway
            return 6

    @private
    async def unwire_host(self, mgmt_ip, shelf_index):
        """Unware the dataplane interfaces of the specified JBOF."""
        possible_host_ips = []
        possible_shelf_ips = []
        shelf_interface_count = await self.shelf_interface_count(mgmt_ip)
        # If shelf_interface_count is e.g. 4 then we want to iterate over [0,1,2,3]
        for eth_index in range(shelf_interface_count):
            possible_host_ips.append(initiator_static_ip(shelf_index, eth_index))
            possible_shelf_ips.append(jbof_static_ip(shelf_index, eth_index))

        # Disconnect NVMe disks
        if await self.middleware.call('failover.licensed'):
            # HA system
            try:
                await asyncio.gather(
                    self.middleware.call('jbof.nvme_disconnect', possible_shelf_ips),
                    self.middleware.call('failover.call_remote', 'jbof.nvme_disconnect', [possible_shelf_ips])
                )
            except CallError as e:
                if e.errno != CallError.ENOMETHOD:
                    raise
                # If other controller is not updated to include this method then nothing to tear down
        else:
            await self.middleware.call('jbof.nvme_disconnect', possible_shelf_ips)

        # Disconnect interfaces
        for interface in await self.middleware.call('rdma.interface.query', [['address', 'in', possible_host_ips]]):
            await self.middleware.call('rdma.interface.delete', interface['id'])

    @private
    async def unwire_dataplane(self, mgmt_ip, shelf_index):
        """Unware the dataplane interfaces of the specified JBOF."""
        await self.middleware.call('jbof.unwire_host', mgmt_ip, shelf_index)
        await self.middleware.call('jbof.unwire_shelf', mgmt_ip)

    @private
    async def configure(self):
        interfaces = await self.middleware.call('rdma.interface.configure')

        for interface in interfaces:
            jbof_ip = jbof_static_ip_from_initiator_ip(interface['address'])
            await self.middleware.call('jbof.nvme_connect', jbof_ip)

    @private
    async def configure_jbof(self, node, shelf_index):
        """Bring up a particular previously-configured JBOF on this node."""
        possible_host_ips = []
        jbof_ips = []

        # Seeing as we're just going to be using possible_host_ips to filter a DB query,
        # don't bother checking with the JBOF for its shelf_interface_count ... just
        # assume a ridiculously high number (12) instead, so we interate over [0,1,11]
        shelf_interface_count = 12
        for eth_index in range(shelf_interface_count):
            possible_host_ips.append(initiator_static_ip(shelf_index, eth_index))

        # First bring up the interfaces on this host
        interfaces = await self.middleware.call('rdma.interface.query', [['address', 'in', possible_host_ips],
                                                                         ['node', '=', node]])
        for interface in interfaces:
            await self.middleware.call('rdma.interface.local_configure_interface',
                                       interface['ifname'],
                                       interface['address'],
                                       interface['prefixlen'],
                                       interface['mtu'])
            jbof_ips.append(jbof_static_ip_from_initiator_ip(interface['address']))

        # Next do the NVMe connect
        # Include some retry code, but expect it won't get used.
        retries = 5
        while retries:
            retries -= 1
            # Note that we iterate over a COPY of jbof_ips so that we can remove items
            for jbof_ip in jbof_ips[:]:
                try:
                    await self.middleware.call('jbof.nvme_connect', jbof_ip)
                    jbof_ips.remove(jbof_ip)
                    self.logger.debug(f'Connected NVMe/RoCE: {jbof_ip}')
                except CallError:
                    if retries:
                        self.logger.info(f'Failed to connect to {jbof_ip}, will retry')
                        await asyncio.sleep(1)
                    else:
                        raise
            if not jbof_ips:
                return

    @private
    @job(lock='configure_job')
    async def configure_job(self, job, reload_fenced=False):
        """Bring up any previously configured JBOF NVMe/RoCE configuration.

        Each JBOF will be brought up in parallel.

        Result will be a dict with keys 'failed' (boolean) and 'message' (str).
        """
        job.set_progress(0, 'Configure RDMA interfaces')
        failed = False

        if await self.middleware.call('failover.licensed'):
            node = await self.middleware.call('failover.node')
        else:
            node = ''

        jbofs = await self.middleware.call('jbof.query')
        if not jbofs:
            err = 'No JBOFs need to be configured'
            job.set_progress(100, err)
            return {'failed': failed, 'message': err}

        # Bring up the JBOFs in parallel.
        exceptions = await asyncio.gather(
            *[self.configure_jbof(node, jbof['index']) for jbof in jbofs],
            return_exceptions=True
        )
        failures = []
        for exc in exceptions:
            if isinstance(exc, Exception):
                failures.append(str(exc))

        # Report progress so far.
        if reload_fenced:
            percent_available = 90
        else:
            percent_available = 100
        if failures:
            # We know all_count is > 0 because of the return above.
            all_count = len(jbofs)
            fail_count = len(failures)
            percent = int((percent_available * (all_count - fail_count)) / all_count)
            err = f'Failure connecting {fail_count} JBOFs: {", ".join(failures)}'
            self.logger.error(err)
            job.set_progress(percent, err)
            failed = True
        else:
            percent = percent_available
            err = 'Completed boot-time bring up of NVMe/RoCE'
            job.set_progress(percent, err)

        # Reload fenced if requested
        if reload_fenced and (await self.middleware.call('failover.fenced.run_info'))['running']:
            try:
                await self.middleware.call('failover.fenced.signal', {'reload': True})
                self.logger.debug('Reloaded fenced')
                job.set_progress(percent + 10, err + ', reloaded fenced')
            except Exception:
                self.logger.error('Unhandled exception reloading fenced', exc_info=True)
                job.set_progress(percent, err + ', failed to reload fenced')
                failed = True
        else:
            job.set_progress(percent + 10, err)

        # This gets returned as the job.result
        return {'failed': failed, 'message': err}


async def setup(middleware):
    RedfishClient.setup()
