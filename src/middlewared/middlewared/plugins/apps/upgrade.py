from pkg_resources import parse_version

from middlewared.schema import accepts, Dict, List, Str, Ref, returns
from middlewared.service import CallError, job, private, Service, ValidationErrors

from .compose_utils import compose_action
from .ix_apps.upgrade import upgrade_config
from .version_utils import get_latest_version_from_app_versions


class AppService(Service):

    class Config:
        namespace = 'app'
        cli_namespace = 'app'

    @accepts(
        Str('app_name'),
        Dict(
            'options',
            Dict('values', additional_attrs=True, private=True),
            Str('app_version', empty=False, default='latest'),
        )
    )
    @returns(Ref('app_query'))
    @job(lock=lambda args: f'app_upgrade_{args[0]}')
    def upgrade(self, job, app_name, options):
        """
        Upgrade `app_name` app to `app_version`.
        """
        app = self.middleware.call_sync('app.get_instance', app_name)
        if app['upgrade_available'] is False:
            raise CallError(f'No upgrade available for {app_name!r}')

        job.set_progress(0, f'Retrieving versions for {app_name!r} app')
        versions_config = self.middleware.call_sync('app.get_versions', app, options)
        upgrade_version = versions_config['specified_version']

        job.set_progress(
            30, f'Upgrading {app_name!r} app to {upgrade_version["version"]!r} version'
        )
        # In order for upgrade to complete, following must happen
        # 1) New version should be copied over to app config's dir
        # 2) Metadata should be updated to reflect new version
        # 3) Docker should be notified to recreate resources and to let upgrade to commence
        # 4) Finally update collective metadata config to reflect new version
        upgrade_config(app_name, upgrade_version)
        self.middleware.call_sync('app.metadata.generate').wait_sync()
        return self.middleware.call_sync('app.get_instance', app_name)

    @accepts(
        Str('app_name'),
        Dict(
            'options',
            Str('app_version', empty=False, default='latest'),
        )
    )
    @returns(Dict(
        Str('latest_version', description='Latest version available for the app'),
        Str('latest_human_version', description='Latest human readable version available for the app'),
        Str('upgrade_version', description='Version user has requested to be upgraded at'),
        Str('upgrade_human_version', description='Human readable version user has requested to be upgraded at'),
        Str('changelog', max_length=None, null=True, description='Changelog for the upgrade version'),
        List('available_versions_for_upgrade', items=[
            Dict(
                'version_info',
                Str('version', description='Version of the app'),
                Str('human_version', description='Human readable version of the app'),
            )
        ], description='List of available versions for upgrade'),
    ))
    async def upgrade_summary(self, app_name, options):
        """
        Retrieve upgrade summary for `app_name`.
        """
        app = await self.middleware.call('app.get_instance', app_name)
        if app['upgrade_available'] is False:
            raise CallError(f'No upgrade available for {app_name!r}')

        versions_config = await self.get_versions(app, options)
        return {
            'latest_version': versions_config['latest_version']['version'],
            'latest_human_version': versions_config['latest_version']['human_version'],
            'upgrade_version': versions_config['specified_version']['version'],
            'upgrade_human_version': versions_config['specified_version']['human_version'],
            'changelog': versions_config['specified_version']['changelog'],
            'available_versions_for_upgrade': [
                {'version': v['version'], 'human_version': v['human_version']}
                for v in versions_config['versions'].values()
                if parse_version(v['version']) > parse_version(app['version'])
            ],
        }

    @private
    async def get_versions(self, app, options):
        if isinstance(app, str):
            app = await self.middleware.call('app.get_instance', app)
        metadata = app['metadata']
        app_details = await self.middleware.call(
            'catalog.get_app_details', metadata['name'], {'train': metadata['train']}
        )
        new_version = options['app_version']
        if new_version == 'latest':
            new_version = get_latest_version_from_app_versions(app_details['versions'])

        if new_version not in app_details['versions']:
            raise CallError(f'Unable to locate {new_version!r} version for {metadata["name"]!r} app')

        verrors = ValidationErrors()
        if parse_version(new_version) <= parse_version(app['version']):
            verrors.add('options.app_version', 'Upgrade version must be greater than current version')

        verrors.check()

        return {
            'specified_version': app_details['versions'][new_version],
            'versions': app_details['versions'],
            'latest_version': app_details['versions'][get_latest_version_from_app_versions(app_details['versions'])],
        }
