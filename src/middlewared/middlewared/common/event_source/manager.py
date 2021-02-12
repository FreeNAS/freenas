from collections import defaultdict, namedtuple
import functools

from middlewared.event import EventSource
from middlewared.utils import start_daemon_thread

IdentData = namedtuple("IdentData", ["app", "name", "arg"])


class EventSourceManager:
    def __init__(self, middleware):
        self.middleware = middleware

        self.event_sources = {}
        self.instances = defaultdict(dict)
        self.idents = {}
        self.subscriptions = defaultdict(lambda: defaultdict(set))

    def register(self, name, event_source):
        if not issubclass(event_source, EventSource):
            raise RuntimeError(f"{event_source} is not EventSource subclass")

        self.event_sources[name] = event_source

    async def subscribe(self, app, ident, name, arg):
        if ident in self.idents:
            raise ValueError(f"Ident {ident} is already used")

        self.idents[ident] = IdentData(app, name, arg)
        self.subscriptions[name][arg].add(ident)

        if arg not in self.instances[name]:
            self.middleware.logger.trace("Creating new instance of event source %r:%r", name, arg)
            self.instances[name][arg] = self.event_sources[name](
                self.middleware, name, arg,
                functools.partial(self._send_event, name, arg),
                functools.partial(self._unsubscribe_all, name, arg),
            )
            start_daemon_thread(target=self.instances[name][arg].process)
        else:
            self.middleware.logger.trace("Re-using existing instance of event source %r:%r", name, arg)

    async def unsubscribe(self, ident):
        ident_data = self.idents.pop(ident)

        idents = self.subscriptions[ident_data.name][ident_data.arg]
        idents.remove(ident)
        if not idents:
            self.middleware.logger.trace("Canceling instance of event source %r:%r as the last subscriber "
                                         "unsubscribed", ident_data.name, ident_data.arg)
            instance = self.instances[ident_data.name].pop(ident_data.arg)
            instance.cancel()

    async def unsubscribe_app(self, app):
        for ident, ident_data in list(self.idents.items()):
            if ident_data.app == app:
                await self.unsubscribe(ident)

    def _send_event(self, name, arg, event_type, **kwargs):
        for ident in list(self.subscriptions[name][arg]):
            try:
                ident_data = self.idents[ident]
            except KeyError:
                self.middleware.logger.trace("Ident %r is gone", ident)
                continue

            ident_data.app.send_event(ident_data.name, event_type, **kwargs)

    async def _unsubscribe_all(self, name, arg):
        for ident in self.subscriptions[name][arg]:
            self.idents.pop(ident)

        self.subscriptions[name][arg].clear()
