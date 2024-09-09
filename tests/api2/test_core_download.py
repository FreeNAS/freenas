from os import stat

from urllib.request import urlretrieve
from middlewared.test.integration.utils.client import truenas_server
from middlewared.test.integration.utils import call


def test_get_download_for_config_dot_save():
    # set up core download
    job_id, url = call('core.download', 'config.save', [], 'freenas.db', job=True)

    # is the job running?
    assert call('core.get_jobs', [['id', '=', job_id]], {'get': True}, job=True)['state'] == 'SUCCESS'

    # download from URL
    rv = urlretrieve(f'http://{truenas_server.ip}{url}')
    assert stat(rv[0]).st_size > 0
