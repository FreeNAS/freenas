import os
import yaml

from middlewared.service import job, Service

from .ix_apps.metadata import get_app_metadata
from .ix_apps.path import get_app_parent_config_path, get_collective_metadata_path


class AppMetadataService(Service):

    class Config:
        namespace = 'app.metadata'
        private = True

    @job(lock='app_metadata_generate', lock_queue_size=1)
    def generate(self):
        metadata = {}
        with os.scandir(get_app_parent_config_path()) as scan:
            for entry in filter(lambda e: e.is_dir(), scan):
                if not (app_metadata := get_app_metadata(entry.name)):
                    # The app is malformed or something is seriously wrong with it
                    continue

                metadata[entry.name] = app_metadata

        with open(get_collective_metadata_path(), 'w') as f:
            f.write(yaml.safe_dump(metadata))
