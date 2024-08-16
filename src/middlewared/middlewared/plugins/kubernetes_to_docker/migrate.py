import os.path
import shutil

from middlewared.plugins.apps.ix_apps.path import get_app_parent_volume_ds_name, get_installed_app_path
from middlewared.plugins.docker.state_utils import DATASET_DEFAULTS
from middlewared.schema import accepts, Bool, Dict, List, returns, Str
from middlewared.service import CallError, job, Service

from .migrate_config_utils import migrate_chart_release_config
from .utils import get_sorted_backups


class K8stoDockerMigrationService(Service):

    class Config:
        namespace = 'k8s_to_docker'
        cli_namespace = 'k8s_to_docker'

    @accepts(
        Str('kubernetes_pool'),
        Dict(
            'options',
            Str('backup_name', null=True, default=None),
        ),
        roles=['DOCKER_WRITE']
    )
    @returns(List(
        'app_migration_details',
        items=[Dict(
            'app_migration_detail',
            Str('name'),
            Bool('successfully_migrated'),
            Str('error', null=True),
        )]
    ))
    @job(lock='k8s_to_docker_migrate')
    def migrate(self, job, kubernetes_pool, options):
        """
        Migrate kubernetes backups to docker.
        """
        # The workflow for the migration would be
        # 1) Ensuring the specified backup exists
        # 2) Map apps which are supported atm and will actually reflect in the UI
        # 3) Setup filesystem appropriately for docker
        # 4) Migrate the config of apps
        # 5) Create relevant filesystem bits for apps and handle cases like ix-volumes
        # 6) Redeploy apps
        backup_config_job = self.middleware.call_sync('k8s_to_docker.list_backups', kubernetes_pool)
        backup_config_job.wait_sync()
        if backup_config_job.error:
            raise CallError(f'Failed to list backups: {backup_config_job.error}')

        backups = backup_config_job.result
        if backups['error']:
            raise CallError(f'Failed to list backups for {kubernetes_pool!r}: {backups["error"]}')

        if options['backup_name'] is None:
            # We will get latest backup now and execute it
            if not backups['backups']:
                raise CallError(f'No backups found for {kubernetes_pool!r}')

            sorted_backups = get_sorted_backups(backups)
            if not sorted_backups:
                raise CallError(
                    f'Latest backup for {kubernetes_pool!r} does not have any releases which can be migrated'
                )

            options['backup_name'] = sorted_backups[-1]['name']

        if options['backup_name'] not in backups['backups']:
            raise CallError(f'Backup {options["backup_name"]} not found')

        backup_config = backups['backups'][options['backup_name']]
        job.set_progress(10, f'Located {options["backup_name"]} backup')

        if not backup_config['releases']:
            raise CallError(f'No old apps found in {options["backup_name"]!r} backup which can be migrated')

        docker_config = self.middleware.call_sync('docker.config')
        if docker_config['pool'] and docker_config['pool'] != kubernetes_pool:
            # For good measure we stop docker service and unset docker pool if any configured
            self.middleware.call_sync('service.stop', 'docker')
            job.set_progress(15, 'Un-configuring docker service if configured')
            docker_job = self.middleware.call_sync('docker.update', {'pool': None})
            docker_job.wait_sync()
            if docker_job.error:
                raise CallError(f'Failed to un-configure docker: {docker_job.error}')

        if docker_config['pool'] is None or docker_config['pool'] != kubernetes_pool:
            # We will now configure docker service
            docker_job = self.middleware.call_sync('docker.update', {'pool': kubernetes_pool})
            docker_job.wait_sync()
            if docker_job.error:
                raise CallError(f'Failed to configure docker: {docker_job.error}')

        self.middleware.call_sync('catalog.sync').wait_sync()

        installed_apps = {app['id']: app for app in self.middleware.call_sync('app.query')}
        job.set_progress(25, f'Rolling back to {backup_config["snapshot_name"]!r} snapshot')
        self.middleware.call_sync(
            'zfs.snapshot.rollback', backup_config['snapshot_name'], {
                'force': True,
                'recursive': True,
                'recursive_clones': True,
                'recursive_rollback': True,
            }
        )
        job.set_progress(30, 'Starting migrating old apps to new apps')

        # We will now iterate over each chart release which can be migrated and try to migrate it's config
        # If we are able to migrate it's config, we will proceed with setting up relevant filesystem bits
        # for the app and finally redeploy it
        total_releases = len(backup_config['releases'])
        app_percentage = ((70 - 30) / total_releases)
        percentage = 30
        release_details = []
        migrate_context = {'gpu_choices': self.middleware.call_sync('app.gpu_choices_internal')}
        dummy_job = type('dummy_job', (object,), {'set_progress': lambda *args: None})()
        for chart_release in backup_config['releases']:
            percentage += app_percentage
            job.set_progress(percentage, f'Migrating {chart_release["release_name"]!r} app')

            release_config = {
                'name': chart_release['release_name'],
                'error': 'Unable to complete migration',
                'successfully_migrated': False,
            }
            release_details.append(release_config)

            if release_config['name'] in installed_apps:
                # Ideally we won't come to this case at all, but this case will only be true in the following case
                # User configured docker pool
                # Installed X app with same name
                # Unset docker pool
                # Tried restoring backup on the same pool
                # We will run into this case because when we were listing out chart releases which can be migrated
                # we were not able to deduce installed apps at all as pool was unset atm and docker wasn't running
                release_config['error'] = 'App with same name is already installed'
                continue

            new_config = migrate_chart_release_config(chart_release | migrate_context)
            if isinstance(new_config, str) or not new_config:
                release_config['error'] = f'Failed to migrate config: {new_config}'
                continue

            complete_app_details = self.middleware.call_sync('catalog.get_app_details', chart_release['app_name'], {
                'train': chart_release['train'],
            })

            try:
                self.middleware.call_sync(
                    'app.create_internal', dummy_job, chart_release['release_name'],
                    chart_release['app_version'], new_config, complete_app_details, True, True,
                )
            except Exception as e:
                release_config['error'] = f'Failed to create app: {e}'
                continue

            # At this point we have just not instructed docker to start the app and ix volumes normalization is left
            release_user_config = chart_release['helm_secret']['config']
            snapshot = backup_config['snapshot_name'].split('@')[-1]
            available_snapshots = set()
            for ix_volume in release_user_config.get('ixVolumes', []):
                ds_name = ix_volume.get('hostPath', '')[5:]  # remove /mnt/
                ds_snap = f'{ds_name}@{snapshot}'
                if not self.middleware.call_sync('zfs.snapshot.query', [['id', '=', ds_snap]]):
                    continue

                available_snapshots.add(ds_snap)

            if available_snapshots:
                self.middleware.call_sync('app.schema.action.update_volumes', chart_release['release_name'], [])

            try:
                app_volume_ds = get_app_parent_volume_ds_name(
                    os.path.join(kubernetes_pool, 'ix-apps'), chart_release['release_name']
                )
                for snapshot in available_snapshots:
                    # We will do a zfs clone and promote here
                    destination_ds = os.path.join(app_volume_ds, snapshot.split('@')[0].split('/')[-1])
                    self.middleware.call_sync('zfs.snapshot.clone', {
                        'snapshot': snapshot,
                        'dataset_dst': destination_ds,
                        'dataset_properties': DATASET_DEFAULTS.update_only(),
                    })
                    self.middleware.call_sync('zfs.dataset.promote', destination_ds)
                    self.middleware.call_sync('zfs.dataset.mount', destination_ds)
            except CallError as e:
                release_config['error'] = f'Failed to clone and promote ix-volumes: {e}'
                # We do this to make sure it does not show up as installed in the UI
                shutil.rmtree(get_installed_app_path(chart_release['release_name']), ignore_errors=True)
            else:
                release_config.update({
                    'error': None,
                    'successfully_migrated': True,
                })
                self.middleware.call_sync('app.metadata.generate').wait_sync(raise_error=True)

        job.set_progress(75, 'Deploying migrated apps')

        bulk_job = self.middleware.call_sync(
            'core.bulk', 'app.redeploy', [
                [r['name']] for r in filter(lambda r: r['error'] is None, release_details)
            ]
        )
        bulk_job.wait_sync()
        if bulk_job.error:
            raise CallError(f'Failed to redeploy apps: {bulk_job.error}')

        for index, status in enumerate(bulk_job.result):
            if status['error']:
                release_details[index].update({
                    'error': f'Failed to deploy app: {status["error"]}',
                    'successfully_migrated': False,
                })

        job.set_progress(100, 'Migration completed')

        return release_details
