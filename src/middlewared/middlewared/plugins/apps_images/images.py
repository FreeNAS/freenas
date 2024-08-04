from middlewared.plugins.apps.ix_apps.docker.images import delete_image, list_images
from middlewared.schema import accepts, Bool, Dict, Int, List, Str
from middlewared.service import CRUDService, filterable
from middlewared.utils import filter_list

from .utils import parse_tags


class AppImageService(CRUDService):

    class Config:
        cli_namespace = 'app.image'
        namespace = 'app.image'
        role_prefix = 'APPS'

    ENTRY = Dict(
        'app_image_entry',
        Str('id'),
        List('repo_tags', items=[Str('repo_tag')]),
        List('repo_digests', items=[Str('repo_digest')]),
        Int('size'),
        Bool('dangling'),
        Str('created'),
        Str('author'),
        Str('comment'),
        List(
            'parsed_repo_tags', items=[Dict(
                'parsed_repo_tag',
                Str('image'),
                Str('tag'),
                Str('registry'),
                Str('complete_tag'),
            )]
        ),
    )

    @filterable
    def query(self, filters, options):
        """
        Query all docker images with `query-filters` and `query-options`.

        `query-options.extra.parse_tags` is a boolean which when set will have normalized tags to be retrieved.
        """
        if not self.middleware.call_sync('docker.state.validate', False):
            return filter_list([], filters, options)

        update_cache = self.middleware.call_sync('app.image.op.image_update_cache')
        parse_all_tags = options['extra'].get('parse_tags')
        images = []
        for image in list_images():
            config = {
                k if isinstance(k, str) else k[0]: image.get(k) if isinstance(k, str) else image.get(*k) for k in (
                    'id', ('repo_tags', []), ('repo_digests', []), 'size', 'created', 'author', 'comment',
                )
            }
            config.update({
                'dangling': len(config['repo_tags']) == 1 and config['repo_tags'][0] == '<none>:<none>',
                'update_available': any(update_cache[r] for r in config['repo_tags']),
            })
            if parse_all_tags:
                config['parsed_repo_tags'] = parse_tags(config['repo_tags'])
            images.append(config)

        return filter_list(images, filters, options)

    @accepts(
        Str('image_id'),
        Dict(
            'options',
            Bool('force', default=False),
        )
    )
    def do_delete(self, image_id, options):
        """
        Delete docker image `image_id`.

        `options.force` when set will force delete the image regardless of the state of containers and should
        be used cautiously.
        """
        self.middleware.call_sync('docker.state.validate')
        image = self.get_instance__sync(image_id)
        delete_image(image_id, options['force'])
        self.middleware.call_sync('app.image.op.remove_image_from_cache', image)
        return True
