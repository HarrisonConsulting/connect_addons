# -*- coding: utf-8 -*-
import os
import re

from odoo.tests import TransactionCase, tagged

import odoo.addons.connect as connect

JS_ROOTS = ('static/src/components', 'static/src/services')
RAW_BUS_LISTENER = re.compile(r'\b(?:this\.bus|this\.props\.bus)\.addEventListener\(')
SUBSCRIBE = re.compile(r'busService\.subscribe\(\s*["\']([\w.]+)["\']')
UNSUBSCRIBE = re.compile(r'busService\.unsubscribe\(\s*["\']([\w.]+)["\']')


@tagged('post_install', '-at_install')
class TestJsBusListenerCleanup(TransactionCase):
    """The phone shares one page-lifetime EventBus with every component it
    mounts. A listener that outlives its component fires into a destroyed
    one and raises "Component is destroyed", so components attach with
    useBus (removed on unmount) and unsubscribe from the bus service."""

    def _js_files(self):
        root = os.path.dirname(connect.__file__)
        for sub in JS_ROOTS:
            for dirpath, _dirnames, filenames in os.walk(os.path.join(root, sub)):
                for name in filenames:
                    if name.endswith('.js'):
                        path = os.path.join(dirpath, name)
                        with open(path, encoding='utf-8') as f:
                            yield os.path.relpath(path, root), f.read()

    def test_components_attach_bus_listeners_with_use_bus(self):
        offenders = [
            path for path, source in self._js_files()
            if RAW_BUS_LISTENER.search(source)
        ]
        self.assertFalse(
            offenders,
            'Attach component bus listeners with useBus so they are removed '
            'on unmount: %s' % ', '.join(offenders))

    def test_bus_service_subscriptions_are_unsubscribed(self):
        offenders = []
        for path, source in self._js_files():
            missing = set(SUBSCRIBE.findall(source)) - set(UNSUBSCRIBE.findall(source))
            if missing:
                offenders.append('%s: %s' % (path, ', '.join(sorted(missing))))
        self.assertFalse(
            offenders,
            'Every busService.subscribe needs a matching unsubscribe on '
            'unmount: %s' % '; '.join(offenders))
