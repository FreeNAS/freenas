import os

from .utils import IX_APPS_MOUNT_PATH


def get_app_parent_config_path() -> str:
    return os.path.join(IX_APPS_MOUNT_PATH, 'app_configs')


def get_installed_app_version_path(app_name: str, version: str) -> str:
    return os.path.join(get_app_parent_config_path(), app_name, 'versions', version)


def get_installed_app_config_path(app_name: str, version: str) -> str:
    return os.path.join(get_installed_app_version_path(app_name, version), 'user_config.yaml')


def get_installed_app_rendered_dir_path(app_name: str, version: str) -> str:
    return os.path.join(get_installed_app_version_path(app_name, version), 'templates/rendered')
