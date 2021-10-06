from libzfs import ZFS
from middlewared.service import Service
from .disks_base import PoolDiskServiceBase


class ZFSPoolService(Service, PoolDiskServiceBase):

    class Config:
        namespace = 'zfs.pool'
        private = True
        process_pool = True

    def get_disks(self, name):
        try:
            with ZFS() as zfs:
                disks = [i.replace('/dev/', '').replace('.eli', '') for i in zfs.get(name).disks]
        except Exception:
            self.logger.error('Failed to retrieve disks for %r', name, exc_info=True)
            return []

        pool_disks = []
        cache = self.middleware.call_sync('disk.label_to_dev_disk_cache')
        for disk in disks:
            found_label = cache['label_to_dev'].get(disk)
            if found_label:
                found_disk = cache['dev_to_disk'].get(found_label)
                if found_disk:
                    pool_disks.append(found_disk)

        return pool_disks
