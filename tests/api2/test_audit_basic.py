from middlewared.test.integration.assets.account import user, unprivileged_user_client
from middlewared.test.integration.assets.pool import dataset
from middlewared.test.integration.assets.smb import smb_share
from middlewared.test.integration.utils import call, url
from middlewared.test.integration.utils.audit import get_audit_entry

from auto_config import ha
from protocols import smb_connection
from time import sleep

import os
import pytest
import requests
import secrets
import string

ha_test = pytest.mark.skipif(not (ha and "virtual_ip" in os.environ), reason="Skip HA tests")


SMBUSER = 'audit-smb-user'
PASSWD = ''.join(secrets.choice(string.ascii_letters + string.digits) for i in range(10))
AUDIT_DATASET_CONFIG = {
    # keyname : "audit"=audit only setting, "zfs"=zfs dataset setting, "ro"=read-only (not a setting)
    'retention': 'audit',
    'quota': 'zfs',
    'reservation': 'zfs',
    'quota_fill_warning': 'zfs',
    'quota_fill_critical': 'zfs',
    'remote_logging_enabled': 'other',
    'space': 'ro'
}
MiB = 1024**2
GiB = 1024**3


# =====================================================================
#                     Fixtures and utilities
# =====================================================================
class AUDIT_CONFIG():
    defaults = {
        'retention': 7,
        'quota': 0,
        'reservation': 0,
        'quota_fill_warning': 75,
        'quota_fill_critical': 95
    }


def get_zfs(data_type, key, zfs_config):
    """ Get the equivalent ZFS value associated with the audit config setting """

    types = {
        'zfs': {
            'reservation': zfs_config['properties']['refreservation']['parsed'] or 0,
            'quota': zfs_config['properties']['refquota']['parsed'] or 0,  # audit quota == ZFS refquota
            'refquota': zfs_config['properties']['refquota']['parsed'] or 0,
            'quota_fill_warning': zfs_config['org.freenas:quota_warning'],
            'quota_fill_critical': zfs_config['org.freenas:quota_critical']
        },
        'space': {
            'used': zfs_config['properties']['used']['parsed'],
            'used_by_snapshots': zfs_config['properties']['usedbysnapshots']['parsed'],
            'available': zfs_config['properties']['available']['parsed'],
            'used_by_dataset': zfs_config['properties']['usedbydataset']['parsed'],
            # We set 'refreservation' and there is no 'usedbyreservation'
            'used_by_reservation': zfs_config['properties']['usedbyrefreservation']['parsed']
        }
    }
    return types[data_type][key]


@pytest.fixture(scope='class')
def initialize_for_smb_tests():
    with dataset('audit-test-basic', data={'share_type': 'SMB'}) as ds:
        with smb_share(os.path.join('/mnt', ds), 'AUDIT_BASIC_TEST', {
            'purpose': 'NO_PRESET',
            'guestok': False,
            'audit': {'enable': True}
        }) as s:
            with user({
                'username': SMBUSER,
                'full_name': SMBUSER,
                'group_create': True,
                'password': PASSWD,
                'smb': True
            }) as u:
                yield {'dataset': ds, 'share': s, 'user': u}


@pytest.fixture(scope='class')
def init_audit():
    """ Provides the audit and dataset configs and cleans up afterward """
    try:
        dataset = call('audit.get_audit_dataset')
        config = call('audit.config')
        yield (config, dataset)
    finally:
        call('audit.update', AUDIT_CONFIG.defaults)


@pytest.fixture(scope="function")
def standby_user():
    """ HA system: Create a user on the BACKUP node
    This will generate a 'create' audit entry, yield,
    and on exit generate a 'delete' audit entry.
    """
    try:
        name = "StandbyUser" + PASSWD
        user_id = call('failover.call_remote', 'user.create', [{
            "username": name,
            "full_name": name + " Deleteme",
            "group": 100,
            "smb": False,
            "home_create": False,
            "password": "testing"
        }])
        yield name
    finally:
        call('failover.call_remote', 'user.delete', [user_id])


# =====================================================================
#                           Tests
# =====================================================================
class TestAuditConfig:
    def test_audit_config_defaults(self, init_audit):
        (config, dataset) = init_audit

        # Confirm existence of config entries
        for key in [k for k in AUDIT_DATASET_CONFIG]:
            assert key in config, str(config)

        # Confirm audit default config settings
        assert config['retention'] == AUDIT_CONFIG.defaults['retention']
        assert config['quota'] == AUDIT_CONFIG.defaults['quota']
        assert config['reservation'] == AUDIT_CONFIG.defaults['reservation']
        assert config['quota_fill_warning'] == AUDIT_CONFIG.defaults['quota_fill_warning']
        assert config['quota_fill_critical'] == AUDIT_CONFIG.defaults['quota_fill_critical']
        assert config['remote_logging_enabled'] is False
        for key in ['used', 'used_by_snapshots', 'used_by_dataset', 'used_by_reservation', 'available']:
            assert key in config['space'], str(config['space'])

        for service in ['MIDDLEWARE', 'SMB', 'SUDO']:
            assert service in config['enabled_services']

        # Confirm audit dataset settings
        for key in [k for k in AUDIT_DATASET_CONFIG if AUDIT_DATASET_CONFIG[k] == 'zfs']:
            assert get_zfs('zfs', key, dataset) == config[key], f"config[{key}] = {config[key]}"

    def test_audit_config_dataset_defaults(self, init_audit):
        """ Confirm Audit dataset uses Audit default settings """
        (unused, ds_config) = init_audit
        assert ds_config['org.freenas:refquota_warning'] == AUDIT_CONFIG.defaults['quota_fill_warning']
        assert ds_config['org.freenas:refquota_critical'] == AUDIT_CONFIG.defaults['quota_fill_critical']

    def test_audit_config_updates(self):
        """
        This test validates that setting values has expected results.
        """
        new_config = call('audit.update', {'retention': 10})
        assert new_config['retention'] == 10

        # quota are in units of GiB
        new_config = call('audit.update', {'quota': 1})
        assert new_config['quota'] == 1
        audit_dataset = call('audit.get_audit_dataset')

        # ZFS value is in units of bytes.  Convert to GiB for comparison.
        assert get_zfs('zfs', 'refquota', audit_dataset) // GiB == new_config['quota']

        # Confirm ZFS and audit config are in sync
        assert new_config['space']['available'] == get_zfs('space', 'available', audit_dataset)
        assert new_config['space']['used_by_dataset'] == get_zfs('space', 'used', audit_dataset)

        # Check that we're actually setting the quota by evaluating available space
        # Change the the quota to something more interesting
        new_config = call('audit.update', {'quota': 2})
        assert new_config['quota'] == 2

        audit_dataset = call('audit.get_audit_dataset')
        assert get_zfs('zfs', 'refquota', audit_dataset) == 2*GiB  # noqa (allow 2*GiB)

        used_in_dataset = get_zfs('space', 'used_by_dataset', audit_dataset)
        assert 2*GiB - new_config['space']['available'] == used_in_dataset  # noqa (allow 2*GiB)

        new_config = call('audit.update', {'reservation': 1})
        assert new_config['reservation'] == 1
        assert new_config['space']['used_by_reservation'] != 0

        new_config = call('audit.update', {
            'quota_fill_warning': 70,
            'quota_fill_critical': 80
        })

        assert new_config['quota_fill_warning'] == 70
        assert new_config['quota_fill_critical'] == 80

        # Test disable reservation
        new_config = call('audit.update', {'reservation': 0})
        assert new_config['reservation'] == 0

        # Test disable quota
        new_config = call('audit.update', {'quota': 0})
        assert new_config['quota'] == 0


class TestAuditOps:
    def test_audit_query(self, initialize_for_smb_tests):
        # If this test has been run more than once on this VM, then
        # the audit DB _will_ record the creation.
        # Let's get the starting count.
        initial_ops_count = call('audit.query', {
            'services': ['SMB'],
            'query-filters': [['username', '=', SMBUSER]],
            'query-options': {'count': True}
        })

        share = initialize_for_smb_tests['share']
        with smb_connection(
            share=share['name'],
            username=SMBUSER,
            password=PASSWD,
        ) as c:
            fd = c.create_file('testfile.txt', 'w')
            for i in range(0, 3):
                c.write(fd, b'foo')
                c.read(fd, 0, 3)
            c.close(fd, True)

        retries = 2
        ops_count = initial_ops_count
        while retries > 0 and (ops_count - initial_ops_count) <= 0:
            sleep(5)
            ops_count = call('audit.query', {
                'services': ['SMB'],
                'query-filters': [['username', '=', SMBUSER]],
                'query-options': {'count': True}
            })
            retries -= 1
        assert ops_count > initial_ops_count, f"retries remaining = {retries}"

    def test_audit_order_by(self):
        entries_forward = call('audit.query', {'services': ['SMB'], 'query-options': {
            'order_by': ['audit_id']
        }})

        entries_reverse = call('audit.query', {'services': ['SMB'], 'query-options': {
            'order_by': ['-audit_id']
        }})

        head_forward_id = entries_forward[0]['audit_id']
        tail_forward_id = entries_forward[-1]['audit_id']

        head_reverse_id = entries_reverse[0]['audit_id']
        tail_reverse_id = entries_reverse[-1]['audit_id']

        assert head_forward_id == tail_reverse_id
        assert tail_forward_id == head_reverse_id

    def test_audit_export(self):
        for backend in ['CSV', 'JSON', 'YAML']:
            report_path = call('audit.export', {'export_format': backend}, job=True)
            assert report_path.startswith('/audit/reports/root/')
            st = call('filesystem.stat', report_path)
            assert st['size'] != 0, str(st)

            job_id, path = call(
                "core.download", "audit.download_report",
                [{"report_name": os.path.basename(report_path)}],
                f"report.{backend.lower()}"
            )
            r = requests.get(f"{url()}{path}")
            r.raise_for_status()
            assert len(r.content) == st['size']

    def test_audit_export_nonroot(self):
        with unprivileged_user_client(roles=['SYSTEM_AUDIT_READ', 'FILESYSTEM_ATTRS_READ']) as c:
            me = c.call('auth.me')
            username = me['pw_name']

            for backend in ['CSV', 'JSON', 'YAML']:
                report_path = c.call('audit.export', {'export_format': backend}, job=True)
                assert report_path.startswith(f'/audit/reports/{username}/')
                st = c.call('filesystem.stat', report_path)
                assert st['size'] != 0, str(st)

                job_id, path = c.call(
                    "core.download", "audit.download_report",
                    [{"report_name": os.path.basename(report_path)}],
                    f"report.{backend.lower()}"
                )
                r = requests.get(f"{url()}{path}")
                r.raise_for_status()
                assert len(r.content) == st['size']

    @pytest.mark.parametrize('svc', ["MIDDLEWARE", "SMB"])
    def test_audit_timestamps(self, svc):
        """
        NAS-130373
        Confirm the timestamps are processed as expected
        """
        audit_entry = get_audit_entry(svc)

        ae_ts_ts = int(audit_entry['timestamp'].timestamp())
        ae_msg_ts = int(audit_entry['message_timestamp'])
        assert abs(ae_ts_ts - ae_msg_ts) < 2, f"$date='{ae_ts_ts}, message_timestamp={ae_msg_ts}"

    @ha_test
    def test_audit_ha_query(self, standby_user):
        name = standby_user
        remote_user = call('failover.call_remote', 'user.query', [[["username", "=", name]]])
        assert remote_user != []

        # Handle delays in the audit database
        remote_audit_entry = []
        tries = 3
        while tries > 0 and remote_audit_entry == []:
            sleep(1)
            remote_audit_entry = call('audit.query', {
                "query-filters": [["event_data.description", "$", name]],
                "query-options": {"select": ["event_data", "success"]},
                "controller": "Standby"
            })
            if remote_audit_entry != []:
                break
            tries -= 1

        assert tries > 0, "Failed to get expected audit entry"
        assert remote_audit_entry != []
        params = remote_audit_entry[0]['event_data']['params'][0]
        assert params['username'] == name
