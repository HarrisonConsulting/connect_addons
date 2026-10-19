# -*- coding: utf-8 -*-
import inspect

from odoo.tests import TransactionCase, tagged

SHARED_MODELS = (
    'connect.call',
    'connect.channel',
    'connect.message',
    'connect.recording',
    'connect.settings',
    'connect.tool',
    'connect.user',
    'res.partner',
)

# Fields this module may extend without a voiceml prefix: selection_add on
# core provider switches, which every provider module extends the same way.
SHARED_SELECTION_FIELDS = {
    'connect.settings': {'rest_provider'},
    'connect.tool': {'adapter'},
    'connect.user': {'originate_provider', 'message_provider'},
}


@tagged('post_install', '-at_install')
class TestProviderCoexistence(TransactionCase):
    """VoiceML shares the Connect ledger models with other provider modules.

    Odoo merges every module's class for a model into one MRO, so a method
    name VoiceML and another module both define, where either skips super(),
    runs only the version merged last, for both modules' callers. A field
    both declare is one column, and the last declaration sets its comodel.
    """

    def _module_classes(self, model_name):
        for cls in type(self.env[model_name]).__mro__:
            module = getattr(cls, '_module', None)
            if not module or module in ('connect', 'base') or '_name' in cls.__dict__:
                continue
            yield module, cls

    def _module_methods(self, model_name):
        by_module = {}
        for module, cls in self._module_classes(model_name):
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
            ours = by_module.get('connect_voiceml', {})
            for module, theirs in by_module.items():
                if module == 'connect_voiceml':
                    continue
                for name in sorted(set(ours) & set(theirs)):
                    sources = (inspect.getsource(ours[name]), inspect.getsource(theirs[name]))
                    if not all('super(' in source for source in sources):
                        clashes.append('%s.%s (connect_voiceml, %s)' % (model_name, name, module))
        self.assertFalse(
            clashes,
            'Same-named methods without super() on shared models; name the '
            'VoiceML one apart or chain both: %s' % ', '.join(clashes))

    def test_shared_model_fields_are_namespaced(self):
        unprefixed = []
        for model_name in SHARED_MODELS:
            if model_name not in self.env:
                continue
            allowed = SHARED_SELECTION_FIELDS.get(model_name, set())
            for name, field in self.env[model_name]._fields.items():
                if 'connect_voiceml' not in (getattr(field, '_modules', None) or ()):
                    continue
                if name in allowed or 'voiceml' in name:
                    continue
                unprefixed.append('%s.%s' % (model_name, name))
        self.assertFalse(
            unprefixed,
            'VoiceML fields on shared models must carry voiceml in the name, '
            'or another provider can own the same column: %s' % ', '.join(unprefixed))
