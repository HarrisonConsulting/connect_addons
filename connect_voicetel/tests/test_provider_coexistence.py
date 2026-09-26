# -*- coding: utf-8 -*-
import inspect

from odoo.tests import TransactionCase, tagged

SHARED_MODELS = (
    'connect.call',
    'connect.channel',
    'connect.message',
    'connect.recording',
    'connect.settings',
    'connect.user',
    'res.partner',
)


@tagged('post_install', '-at_install')
class TestProviderCoexistence(TransactionCase):
    """VoiceTel shares the Connect ledger models with other provider modules.

    Odoo merges every module's class for a model into one MRO, so a method
    name VoiceTel and another module both define, where either skips super(),
    runs only the version merged last, for both modules' callers.
    """

    def _module_methods(self, model_name):
        by_module = {}
        for cls in type(self.env[model_name]).__mro__:
            module = getattr(cls, '_module', None)
            if not module or module in ('connect', 'base') or '_name' in cls.__dict__:
                continue
            methods = {
                name: member for name, member in vars(cls).items()
                if inspect.isfunction(member)
            }
            by_module.setdefault(module, {}).update(methods)
        return by_module

    def test_shared_method_names_chain_through_super(self):
        clashes = []
        for model_name in SHARED_MODELS:
            if model_name not in self.env:
                continue
            by_module = self._module_methods(model_name)
            ours = by_module.get('connect_voicetel', {})
            for module, theirs in by_module.items():
                if module == 'connect_voicetel':
                    continue
                for name in sorted(set(ours) & set(theirs)):
                    sources = (inspect.getsource(ours[name]), inspect.getsource(theirs[name]))
                    if not all('super(' in source for source in sources):
                        clashes.append('%s.%s (connect_voicetel, %s)' % (model_name, name, module))
        self.assertFalse(
            clashes,
            'Same-named methods without super() on shared models; name the '
            'VoiceTel one apart or chain both: %s' % ', '.join(clashes))
