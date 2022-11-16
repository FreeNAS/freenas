import glob
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile

from datetime import datetime

from middlewared.schema import accepts, Bool, Dict, returns
from middlewared.service import CallError, Service, job, private
from middlewared.plugins.pwenc import PWENC_FILE_SECRET
from middlewared.utils.db import FREENAS_DATABASE
from middlewared.utils.python import get_middlewared_dir

CONFIG_FILES = {
    'pwenc_secret': PWENC_FILE_SECRET,
    'root_authorized_keys': '/root/.ssh/authorized_keys'
}
NEED_UPDATE_SENTINEL = '/data/need-update'
RE_CONFIG_BACKUP = re.compile(r'.*(\d{4}-\d{2}-\d{2})-(\d+)\.db$')
UPLOADED_DB_PATH = '/data/uploaded.db'


class ConfigService(Service):

    class Config:
        cli_namespace = 'system.config'

    @private
    def save_db_only(self, options, job):
        with open(FREENAS_DATABASE, 'rb') as f:
            shutil.copyfileobj(f, job.pipes.output.w)

    @private
    def save_tar_file(self, options, job):
        with tempfile.NamedTemporaryFile(delete=True) as ntf:
            with tarfile.open(ntf.name, 'w') as tar:
                files = {'freenas-v1.db': FREENAS_DATABASE}
                if options['secretseed']:
                    files['pwenc_secret'] = CONFIG_FILES['pwenc_secret']
                if options['root_authorized_keys'] and os.path.exists(CONFIG_FILES['root_authorized_keys']):
                    files['root_authorized_keys'] = CONFIG_FILES['root_authorized_keys']

                for arcname, path in files.items():
                    tar.add(path, arcname=arcname)

            with open(ntf.name, 'rb') as f:
                shutil.copyfileobj(f, job.pipes.output.w)

    @accepts(Dict(
        'configsave',
        Bool('secretseed', default=False),
        Bool('pool_keys', default=False),
        Bool('root_authorized_keys', default=False),
    ))
    @returns()
    @job(pipes=["output"])
    async def save(self, job, options):
        """
        Create a tar file of security-sensitive information. These options select which information
        is included in the tar file:

        `secretseed` bool: When true, include password secret seed.
        `pool_keys` bool: IGNORED and DEPRECATED as it does not apply on SCALE systems.
        `root_authorized_keys` bool: When true, include "/root/.ssh/authorized_keys" file for the root user.

        If none of these options are set, the tar file is not generated and the database file is returned.
        """
        options.pop('pool_keys')  # ignored, doesn't apply on SCALE

        method = self.save_db_only if not any(options.values()) else self.save_tar_file
        await self.middleware.run_in_thread(method, options, job)

    @accepts()
    @returns()
    @job(pipes=["input"])
    def upload(self, job):
        """
        Accepts a configuration file via job pipe.
        """
        chunk = 1024
        _10MB = 1048576 * 10  # if size is > 10MB, rolls over to disk instead of storing all in memory
        with tempfile.SpooledTemporaryFile(max_size=_10MB) as stf:
            with open(stf.name, 'wb') as f:
                while True:
                    data_in = job.pipes.inpur.r.read(chunk)
                    if data_in == b'':
                        break
                    else:
                        f.write(data_in)

            self.__upload(stf.name)

        self.middleware.run_coroutine(self.middleware.call('system.reboot', {'delay': 10}), wait=False)

    def __upload(self, config_file_name):
        tar_error = None
        try:
            """
            First we try to open the file as a tar file.
            We expect the tar file to contain at least the freenas-v1.db.
            It can also contain the pwenc_secret file.
            If we cannot open it as a tar, we try to proceed as it was the
            raw database file.
            """
            try:
                with tarfile.open(config_file_name) as tar:
                    bundle = True
                    tmpdir = tempfile.mkdtemp(dir='/var/tmp/firmware')
                    tar.extractall(path=tmpdir)
                    config_file_name = os.path.join(tmpdir, 'freenas-v1.db')
            except tarfile.ReadError as e:
                tar_error = str(e)
                bundle = False
            # Currently we compare only the number of migrations for south and django
            # of new and current installed database.
            # This is not bullet proof as we can eventually have more migrations in a stable
            # release compared to a older nightly and still be considered a downgrade, however
            # this is simple enough and works in most cases.
            alembic_version = None
            conn = sqlite3.connect(config_file_name)
            try:
                cur = conn.cursor()
                try:
                    cur.execute(
                        "SELECT version_num FROM alembic_version"
                    )
                    alembic_version = cur.fetchone()[0]
                except sqlite3.OperationalError as e:
                    if e.args[0] == "no such table: alembic_version":
                        # FN/TN < 12
                        # Let's just ensure it's not a random SQLite file
                        cur.execute("SELECT 1 FROM django_migrations")
                    else:
                        raise
                finally:
                    cur.close()
            except sqlite3.OperationalError as e:
                if tar_error:
                    raise CallError(
                        f"Uploaded file is neither a valid .tar file ({tar_error}) nor valid FreeNAS/TrueNAS database "
                        f"file ({e})."
                    )
                else:
                    raise CallError(f"Uploaded file is not a valid FreeNAS/TrueNAS database file ({e}).")
            finally:
                conn.close()
            if alembic_version is not None:
                for root, dirs, files in os.walk(os.path.join(get_middlewared_dir(), "alembic", "versions")):
                    found = False
                    for name in files:
                        if name.endswith(".py"):
                            with open(os.path.join(root, name)) as f:
                                if any(
                                    line.strip() == f"Revision ID: {alembic_version}"
                                    for line in f.read().splitlines()
                                ):
                                    found = True
                                    break
                    if found:
                        break
                else:
                    raise CallError('Uploaded config file version is newer than the currently installed.')
        except Exception as e:
            os.unlink(config_file_name)
            if isinstance(e, CallError):
                raise
            else:
                raise CallError(f'The uploaded file is not valid: {e}')

        upload = []

        def move(src, dst):
            shutil.move(src, dst)
            upload.append(dst)

        move(config_file_name, UPLOADED_DB_PATH)
        if bundle:
            for filename, destination in CONFIG_FILES.items():
                file_path = os.path.join(tmpdir, filename)
                if os.path.exists(file_path):
                    if filename == 'geli':
                        # Let's only copy the geli keys and not overwrite the entire directory
                        os.makedirs(CONFIG_FILES['geli'], exist_ok=True)
                        for key_path in os.listdir(file_path):
                            move(
                                os.path.join(file_path, key_path), os.path.join(destination, key_path)
                            )
                    elif filename == 'pwenc_secret':
                        move(file_path, '/data/pwenc_secret_uploaded')
                    else:
                        move(file_path, destination)

        # Now we must run the migrate operation in the case the db is older
        open(NEED_UPDATE_SENTINEL, 'w+').close()
        upload.append(NEED_UPDATE_SENTINEL)

        self.middleware.call_hook_sync('config.on_upload', UPLOADED_DB_PATH)

        if self.middleware.call_sync('failover.licensed'):
            try:
                for path in upload:
                    self.middleware.call_sync('failover.send_small_file', path)

                self.middleware.call_sync(
                    'failover.call_remote', 'core.call_hook', ['config.on_upload', [UPLOADED_DB_PATH]],
                )

                self.middleware.run_coroutine(
                    self.middleware.call('failover.call_remote', 'system.reboot'),
                    wait=False,
                )
            except Exception as e:
                raise CallError(
                    f'Config uploaded successfully, but remote node responded with error: {e}. '
                    f'Please use Sync to Peer on the System/Failover page to perform a manual sync after reboot.',
                    CallError.EREMOTENODEERROR,
                )

    @accepts(Dict('options', Bool('reboot', default=True)))
    @returns()
    @job(lock='config_reset', logs=True)
    def reset(self, job, options):
        """
        Reset database to configuration defaults.

        If `reboot` is true this job will reboot the system after its completed with a delay of 10
        seconds.
        """
        job.set_progress(0, 'Replacing database file')
        shutil.copy('/data/factory-v1.db', FREENAS_DATABASE)

        job.set_progress(10, 'Running database upload hooks')
        self.middleware.call_hook_sync('config.on_upload', FREENAS_DATABASE)

        if self.middleware.call_sync('failover.licensed'):
            job.set_progress(30, 'Sending database to the other node')
            try:
                self.middleware.call_sync('failover.send_small_file', FREENAS_DATABASE)

                self.middleware.call_sync(
                    'failover.call_remote', 'core.call_hook', ['config.on_upload', [FREENAS_DATABASE]],
                )

                if options['reboot']:
                    self.middleware.run_coroutine(
                        self.middleware.call('failover.call_remote', 'system.reboot'),
                        wait=False,
                    )
            except Exception as e:
                raise CallError(
                    f'Config reset successfully, but remote node responded with error: {e}. '
                    f'Please use Sync to Peer on the System/Failover page to perform a manual sync after reboot.',
                    CallError.EREMOTENODEERROR,
                )

        job.set_progress(50, 'Updating initramfs')
        self.middleware.call_sync('boot.update_initramfs')

        if options['reboot']:
            job.set_progress(95, 'Will reboot in 10 seconds')
            self.middleware.run_coroutine(
                self.middleware.call('system.reboot', {'delay': 10}), wait=False,
            )

    @private
    def backup(self):
        systemdataset = self.middleware.call_sync('systemdataset.config')
        if not systemdataset or not systemdataset['path']:
            return

        # Legacy format
        for f in glob.glob(f'{systemdataset["path"]}/*.db'):
            if not RE_CONFIG_BACKUP.match(f):
                continue
            try:
                os.unlink(f)
            except OSError:
                pass

        today = datetime.now().strftime("%Y%m%d")

        newfile = os.path.join(
            systemdataset["path"],
            f'configs-{systemdataset["uuid"]}',
            self.middleware.call_sync('system.version'),
            f'{today}.db',
        )

        dirname = os.path.dirname(newfile)
        if not os.path.exists(dirname):
            os.makedirs(dirname)

        shutil.copy(FREENAS_DATABASE, newfile)
