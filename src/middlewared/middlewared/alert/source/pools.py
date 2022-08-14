from middlewared.alert.base import AlertClass, AlertCategory, AlertLevel, Alert, AlertSource
from middlewared.alert.schedule import CrontabSchedule


class PoolUSBDisksAlertClass(AlertClass):
    category = AlertCategory.STORAGE
    level = AlertLevel.WARNING
    title = 'Pool consuming USB disks'
    text = '%(pool)r is consuming USB devices %(disks)r which is not recommended.'


class PoolDisksChecksAlertSource(AlertSource):
    schedule = CrontabSchedule(hour=0)  # every 24 hours
    run_on_backup_node = False

    async def check(self):
        alerts = []

        for pool in filter(
            lambda p: p['state'] == 'ONLINE',
            (await self.middleware.call('zfs.pool.query_imported_fast')).values()
        ):
            usb_disks = await self.middleware.call('pool.get_usb_disks', pool['name'])
            if usb_disks:
                alerts.append(Alert(
                    PoolUSBDisksAlertClass,
                    {'pool': pool['name'], 'disks': ', '.join(usb_disks)},
                ))

        return alerts
