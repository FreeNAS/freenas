import contextlib
import errno
import os
import subprocess

from middlewared.schema import accepts, Bool, Dict, List, returns, Str
from middlewared.service import CallError, InstanceNotFound, job, private, Service
from middlewared.utils import run

from .utils import ZPOOL_CACHE_FILE


class PoolService(Service):

    class Config:
        cli_namespace = 'storage.pool'
        event_send = False

    @accepts()
    @returns(List(
        'pools_available_for_import',
        title='Pools Available For Import',
        items=[Dict(
            'pool_info',
            Str('name', required=True),
            Str('guid', required=True),
            Str('status', required=True),
            Str('hostname', required=True),
        )]
    ))
    @job()
    async def import_find(self, job):
        """
        Returns a job id which can be used to retrieve a list of pools available for
        import with the following details as a result of the job:
        name, guid, status, hostname.
        """

        existing_guids = [i['guid'] for i in await self.middleware.call('pool.query')]

        result = []
        for pool in await self.middleware.call('zfs.pool.find_import'):
            if pool['status'] == 'UNAVAIL':
                continue
            # Exclude pools with same guid as existing pools (in database)
            # It could be the pool is in the database but was exported/detached for some reason
            # See #6808
            if pool['guid'] in existing_guids:
                continue
            entry = {}
            for i in ('name', 'guid', 'status', 'hostname'):
                entry[i] = pool[i]
            result.append(entry)
        return result

    @private
    async def disable_shares(self, ds):
        await self.middleware.call('zfs.dataset.update', ds, {
            'properties': {
                'sharenfs': {'value': "off"},
                'sharesmb': {'value': "off"},
            }
        })

    @accepts(Dict(
        'pool_import',
        Str('guid', required=True),
        Str('name'),
        Bool('enable_attachments'),
    ))
    @returns(Bool('successful_import'))
    @job(lock='import_pool')
    async def import_pool(self, job, data):
        """
        Import a pool found with `pool.import_find`.

        If a `name` is specified the pool will be imported using that new name.

        If `enable_attachments` is set to true, attachments that were disabled during pool export will be
        re-enabled.

        Errors:
            ENOENT - Pool not found

        .. examples(websocket)::

          Import pool of guid 5571830764813710860.

            :::javascript
            {
                "id": "6841f242-840a-11e6-a437-00e04d680384",
                "msg": "method",
                "method": "pool.import_pool,
                "params": [{
                    "guid": "5571830764813710860"
                }]
            }
        """
        guid = data['guid']
        new_name = data.get('name')

        # validate
        imported_pools = await self.middleware.call('zfs.pool.query_imported_fast')
        if guid in imported_pools:
            raise CallError(f'Pool with guid: "{guid}" already imported', errno.EEXIST)
        elif new_name and new_name in imported_pools.values():
            err = f'Cannot import pool using new name: "{new_name}" because a pool is already imported with that name'
            raise CallError(err, errno.EEXIST)

        # import zpool
        opts = {'altroot': '/mnt', 'cachefile': ZPOOL_CACHE_FILE}
        any_host = True
        use_cachefile = None
        await self.middleware.call('zfs.pool.import_pool', guid, opts, any_host, use_cachefile, new_name)

        # get the zpool name
        if not new_name:
            pool_name = (await self.middleware.call('zfs.pool.query_imported_fast'))[guid]['name']
        else:
            pool_name = new_name

        # Let's umount any datasets if root dataset of the new pool is locked, and it has unencrypted datasets
        # beneath it. This is to prevent the scenario where the root dataset is locked and the child datasets
        # get mounted
        await self.handle_unencrypted_datasets_on_import(pool_name)

        # set acl properties correctly for given top-level dataset's acltype
        ds = await self.middleware.call(
            'pool.dataset.query',
            [['id', '=', pool_name]],
            {'get': True, 'extra': {'retrieve_children': False}}
        )
        if ds['acltype']['value'] == 'NFSV4':
            opts = {'properties': {
                'aclinherit': {'value': 'passthrough'}
            }}
        else:
            opts = {'properties': {
                'aclinherit': {'value': 'discard'},
                'aclmode': {'value': 'discard'},
            }}

        opts['properties'].update({
            'sharenfs': {'value': 'off'}, 'sharesmb': {'value': 'off'},
        })

        await self.middleware.call('zfs.dataset.update', pool_name, opts)

        # Recursively reset dataset mountpoints for the zpool.
        recursive = True
        for child in await self.middleware.call('zfs.dataset.child_dataset_names', pool_name):
            if child == os.path.join(pool_name, 'ix-applications'):
                # We exclude `ix-applications` dataset since resetting it will
                # cause PVC's to not mount because "mountpoint=legacy" is expected.
                continue
            try:
                # Reset all mountpoints
                await self.middleware.call('zfs.dataset.inherit', child, 'mountpoint', recursive)

            except CallError as e:
                if e.errno != errno.EPROTONOSUPPORT:
                    self.logger.warning('Failed to inherit mountpoints recursively for %r dataset: %r', child, e)
                    continue

                try:
                    await self.disable_shares(child)
                    self.logger.warning('%s: disabling ZFS dataset property-based shares', child)
                except Exception:
                    self.logger.warning('%s: failed to disable share: %s.', child, str(e), exc_info=True)

            except Exception as e:
                # Let's not make this fatal
                self.logger.warning('Failed to inherit mountpoints recursively for %r dataset: %r', child, e)

        # We want to set immutable flag on all of locked datasets
        for encrypted_ds in await self.middleware.call(
            'pool.dataset.query_encrypted_datasets', pool_name, {'key_loaded': False}
        ):
            encrypted_mountpoint = os.path.join('/mnt', encrypted_ds)
            if os.path.exists(encrypted_mountpoint):
                try:
                    await self.middleware.call('filesystem.set_immutable', True, encrypted_mountpoint)
                except Exception as e:
                    self.logger.warning('Failed to set immutable flag at %r: %r', encrypted_mountpoint, e)

        # update db
        for pool in await self.middleware.call('datastore.query', 'storage.volume', [['vol_name', '=', pool_name]]):
            await self.middleware.call('datastore.delete', 'storage.volume', pool['id'])
        pool_id = await self.middleware.call('datastore.insert', 'storage.volume', {
            'vol_name': pool_name,
            'vol_guid': guid,
        })
        await self.middleware.call('pool.scrub.create', {'pool': pool_id})

        # re-enable/restart any services dependent on this pool
        pool = await self.middleware.call('pool.query', [('id', '=', pool_id)], {'get': True})
        key = f'pool:{pool["name"]}:enable_on_import'
        if await self.middleware.call('keyvalue.has_key', key):
            for name, ids in (await self.middleware.call('keyvalue.get', key)).items():
                for delegate in await self.middleware.call('pool.dataset.get_attachment_delegates'):
                    if delegate.name == name:
                        attachments = await delegate.query(pool['path'], False)
                        attachments = [attachment for attachment in attachments if attachment['id'] in ids]
                        if attachments:
                            await delegate.toggle(attachments, True)
            await self.middleware.call('keyvalue.delete', key)

        await self.middleware.call_hook('pool.post_import', pool)
        await self.middleware.call('pool.dataset.sync_db_keys', pool['name'])
        self.middleware.create_task(self.middleware.call('disk.swaps_configure'))
        self.middleware.send_event('pool.query', 'ADDED', id=pool_id, fields=pool)

        return True

    @private
    def import_on_boot_impl(self, vol_name, vol_guid, set_cachefile=False, mount_datasets=True):
        cmd = [
            'zpool', 'import',
            vol_guid,  # the GUID of the zpool
            '-R', '/mnt',  # altroot
            '-m',  # import pool with missing log device(s)
            '-f',  # force import since hostid can change (upgrade from CORE to SCALE changes it, for example)
            '-o', f'cachefile={ZPOOL_CACHE_FILE}' if set_cachefile else 'cachefile=none',
        ] + (['-N'] if not mount_datasets else [])
        try:
            self.logger.debug('Importing %r with guid: %r', vol_name, vol_guid)
            cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if cp.returncode != 0:
                self.logger.error(
                    'Failed to import %r with guid: %r with error: %r',
                    vol_name, vol_guid, cp.stdout.decode()
                )
                return False
        except Exception:
            self.logger.error('Unhandled exception importing %r', vol_name, exc_info=True)
            return False

        self.logger.debug('SUCCESS importing %r with guid: %r', vol_name, vol_guid)
        return True

    @private
    def unlock_on_boot_impl(self, vol_name, guid, set_cachefile_property):
        if not self.middleware.call_sync('pool.handle_unencrypted_datasets_on_import', vol_name):
            self.import_on_boot_impl(vol_name, guid, set_cachefile_property, mount_datasets=False)

        zpool_info = self.middleware.call_sync('pool.dataset.get_instance_quick', vol_name, {'encryption': True})
        if not zpool_info:
            self.logger.error(
                'Unable to retrieve %r root dataset information required for unlocking any relevant encrypted datasets',
                vol_name
            )
            return

        umount_root_short_circuit = False
        if zpool_info['key_format']['parsed'] == 'passphrase':
            # passphrase encrypted zpools will _always_ fail to be unlocked at
            # boot time because we don't store the users passphrase on disk
            # anywhere.
            #
            # NOTE: To have a passphrase encrypted zpool (the root dataset is passphrase encrypted)
            # is considered an edge-case (or is someone upgrading from an old version of SCALE where
            # we mistakenly allowed this capability). There is also possibility to update existing
            # root dataset encryption from key based to passphrase based. Again, an edge-case but
            # documenting it here for posterity sake.
            self.logger.debug(
                'Passphrase encrypted zpool detected %r, passphrase required before unlock', vol_name
            )
            umount_root_short_circuit = True

        if not umount_root_short_circuit:
            # the top-level dataset could be unencrypted but there could be any number
            # of child datasets that are encrypted. This will try to recursively unlock
            # those datasets (including the parent if necessary).
            # If we fail to unlock the parent, then the method short-circuits and exits
            # early.
            opts = {'recursive': True, 'toggle_attachments': False}
            uj = self.middleware.call_sync('pool.dataset.unlock', vol_name, opts)
            uj.wait_sync()
            if uj.error:
                self.logger.error('FAILED unlocking encrypted dataset(s) for %r with error %r', vol_name, uj.error)
            elif uj.result['failed']:
                self.logger.error(
                    'FAILED unlocking the following datasets: %r for pool %r',
                    ', '.join(uj.result['failed']), vol_name
                )
            else:
                self.logger.debug('SUCCESS unlocking encrypted dataset(s) (if any) for %r', vol_name)

        if any((
            umount_root_short_circuit,
            self.middleware.call_sync(
                'pool.dataset.get_instance_quick', vol_name, {'encryption': True}
            )['locked']
        )):
            # We umount the zpool in the following scenarios:
            # 1. we came across a passphrase encrypted root dataset (i.e. /mnt/tank)
            # 2. we failed to unlock the key based encrypted root dataset
            #
            # It's important to understand how this operates at zfs level since this
            # can be painfully confusing.
            # 1. when system boots, we call zpool import
            # 2. zpool impot has no notion of encryption and will simply mount
            #   the datasets as necessary (INCLUDING ALL CHILDREN)
            # 3. if the root dataset is passphrase encrypted OR we fail to unlock
            #   the root dataset that is using key based encryption, then the child
            #   datasets ARE STILL MOUNTED DURING IMPORT PHASE (this includes
            #   encrypted children or unencrypted children)
            #
            # In the above scenario, the root dataset wouldn't be mounted but any number
            # of children would be. If the end-user is sharing one of the unencrypted children
            # via a sharing service, then what happens is that a parent DIRECTORY is created
            # in place of the root dataset and all files get written OUTSIDE of the zfs
            # mountpoint. That's an unpleasant experience because it is perceived as data loss
            # since mounting the dataset will just mount over-top of said directory.
            # (i.e. /mnt/tank/datasetA/datasetB/childds/, The "datasetA", "datasetB", "childds"
            # path components would be created as directories and I/O would continue without
            # any problems but the data is not going to that zfs dataset.
            #
            # To account for this edge-case (we now no longer allow the creation of unencrypted child
            # datasets where any upper path component is encrypted) (i.e. no more /mnt/zz/unencrypted/encrypted).
            # However, we still need to take into consideration the other users that manged to get themselves
            # into this scenario.
            if not umount_root_short_circuit:
                with contextlib.suppress(CallError):
                    self.logger.debug('Forcefully umounting %r', vol_name)
                    self.middleware.call_sync('zfs.dataset.umount', vol_name, {'force': True})
                    self.logger.debug('Successfully umounted %r', vol_name)

            pool_mount = f'/mnt/{vol_name}'
            if os.path.exists(pool_mount):
                try:
                    # setting the root path as immutable, in a perfect world, will prevent
                    # the scenario that is describe above
                    self.logger.debug('Setting immutable flag at %r', pool_mount)
                    self.middleware.call_sync('filesystem.set_immutable', True, pool_mount)
                except CallError as e:
                    self.logger.error('Unable to set immutable flag at %r: %s', pool_mount, e)

    @private
    @job()
    def import_on_boot(self, job):
        if self.middleware.call_sync('failover.licensed'):
            # HA systems pools are imported using the failover
            # event logic
            return

        if self.middleware.call_sync('truenas.is_ix_hardware'):
            # Attach NVMe/RoCE - wait up to 10 seconds
            self.logger.info('Start bring up of NVMe/RoCE')
            try:
                jbof_job = self.middleware.call_sync('jbof.configure_job')
                jbof_job.wait_sync(timeout=10)
                if jbof_job.error:
                    self.logger.error(f'Error attaching JBOFs: {jbof_job.error}')
                elif jbof_job.result['failed']:
                    self.logger.error(f'Failed to attach JBOFs:{jbof_job.result["message"]}')
                else:
                    self.logger.info(jbof_job.result['message'])
            except TimeoutError:
                self.logger.error('Timed out attaching JBOFs - will continue in background')
            except Exception:
                self.logger.error('Unexpected error', exc_info=True)

        set_cachefile_property = True
        dir_name = os.path.dirname(ZPOOL_CACHE_FILE)
        try:
            self.logger.debug('Creating %r (if it doesnt already exist)', dir_name)
            os.makedirs(dir_name, exist_ok=True)
        except Exception:
            self.logger.warning('FAILED unhandled exception creating %r', dir_name, exc_info=True)
            set_cachefile_property = False
        else:
            try:
                self.logger.debug('Creating %r (if it doesnt already exist)', ZPOOL_CACHE_FILE)
                with open(ZPOOL_CACHE_FILE, 'x'):
                    pass
            except FileExistsError:
                # cachefile already exists on disk which is fine
                pass
            except Exception:
                self.logger.warning('FAILED unhandled exception creating %r', ZPOOL_CACHE_FILE, exc_info=True)
                set_cachefile_property = False

        # We need to do as little zfs I/O as possible since this method
        # is being called by a systemd service at boot-up. First step of
        # doing this is to simply try to import all zpools that are in our
        # database. Handle each error accordingly instead of trying to be
        # fancy and determine which ones are "offline" since...in theory...
        # all zpools should be offline at this point.
        for i in self.middleware.call_sync('datastore.query', 'storage.volume'):
            name, guid = i['vol_name'], i['vol_guid']
            if not self.import_on_boot_impl(name, guid, set_cachefile_property):
                continue

            self.unlock_on_boot_impl(name, guid, set_cachefile_property)

        # no reason to wait on this to complete
        self.middleware.call_sync('disk.swaps_configure', background=True)

        # TODO: we need to fix this. There is 0 reason to do all this stuff
        # and block the entire boot-up process.
        self.logger.debug('Calling pool.post_import')
        self.middleware.call_hook_sync('pool.post_import', None)
        self.logger.debug('Finished calling pool.post_import')

    @private
    async def handle_unencrypted_datasets_on_import(self, pool_name):
        # If this returns true, it means `pool_name` was not exported which means there is no need to check
        # for import workflow
        # If this returns false, it means that `pool_name` was exported and now needs to be imported back without
        # mounting any unencrypted datasets as root dataset is locked
        try:
            root_ds = await self.middleware.call('pool.dataset.get_instance_quick', pool_name, {
                'encryption': True,
            })
        except InstanceNotFound:
            # We don't really care about this case, it means that pool did not get imported for some reason
            return True

        if not root_ds['encrypted']:
            return True

        # If root ds is encrypted, at this point we know that root dataset has not been mounted yet and neither
        # unlocked, so if there are any children it has which were unencrypted - we force umount them by exporting
        # the pool and then importing it back without mounting any datasets. We had 2 options here essentially:
        # 1) Umount the unencrypted datasets
        # 2) Export the pool and import it back with -N option to not mount any datasets
        # We got with (2), because with (1) unencrypted datasets paths are still leftover. This is not a problem
        # with (2) approach which is why we have decided to go with it.
        try:
            if (cp := await run('zpool', 'export', pool_name, check=False)).returncode:
                self.logger.error('Failed to export %r pool: %r', pool_name, cp.stderr.decode())
            else:
                self.logger.debug('Successfully exported %r pool as root dataset is locked', pool_name)
        except Exception:
            self.logger.error('Failed to umount any unencrypted datasets under %r dataset', pool_name, exc_info=True)

        return False
