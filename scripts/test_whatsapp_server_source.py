"""Execute actual capability and stale-send source with controlled records.
This complements, and does not replace, the real ORM/rule tests in the addon.
"""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
from odoo.exceptions import AccessError, UserError, ValidationError

scope = dict(AccessError=AccessError, UserError=UserError, ValidationError=ValidationError)
methods = []
for file, names in {
    'connect_twilio/models/whatsapp_sender.py': {'_whatsapp_capabilities', 'send_whatsapp'},
    'connect_twilio/models/user.py': {'get_whatsapp_capabilities', '_get_usable_whatsapp_sender'},
}.items():
    for node in ast.walk(ast.parse(Path(file).read_text())):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            node.decorator_list = []
            methods.append(node)
exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])), '<actual capability methods>', 'exec'), scope)

class ConnectUser:
    active = True
    username = 'testactor'
    domain = True
    client_enabled = True
    sip_enabled = False
    def check_access(self, op): assert op == 'read'
class Sender:
    _whatsapp_capabilities = scope['_whatsapp_capabilities']
    send_whatsapp = scope['send_whatsapp']
    sid = 'XEtest'
    number = '+15550000000'
    status = 'ONLINE'
    no_sync = False
    voice_application = False
    readable = True
    def ensure_one(self): pass
    def check_access(self, op):
        assert op == 'read'
        if not self.readable: raise AccessError('Denied')
class SenderModel:
    def get_default_sender(self, actor): return self.sender
class Env:
    route = 'twilio'
    def __init__(self):
        self.user = NS(connect_user=ConnectUser(), has_group=lambda group: True)
        self.senders = SenderModel()
    def __getitem__(self, model):
        if model == 'connect.whatsapp_sender': return self.senders
        if model == 'connect.settings': return NS(_get_originate_provider=lambda: self.route)
        raise AssertionError('Reached side-effect dependency: ' + model)
class User:
    get_whatsapp_capabilities = scope['get_whatsapp_capabilities']
    _get_usable_whatsapp_sender = scope['_get_usable_whatsapp_sender']
env = Env()
user = User(); user.env = env
sender = Sender(); sender.env = env
none = dict(call=False, message=False)
env.senders.sender = False
assert user.get_whatsapp_capabilities() == none
env.senders.sender = sender
assert user.get_whatsapp_capabilities() == dict(call=False, message=True)
sender.voice_application = NS(sid='APtest', has_access=lambda operation: True)
assert user.get_whatsapp_capabilities() == dict(call=True, message=True)
env.route = 'other'
assert user.get_whatsapp_capabilities() == dict(call=False, message=True)
env.route = 'twilio'
for key, value in [('status', 'PENDING'), ('status', 'OFFLINE'), ('sid', False), ('no_sync', True), ('readable', False)]:
    old = getattr(sender, key)
    setattr(sender, key, value)
    assert user.get_whatsapp_capabilities() == none, key
    setattr(sender, key, old)
sender.status = 'OFFLINE'
for operation in ('call', 'message'):
    try: user._get_usable_whatsapp_sender(operation)
    except ValidationError: pass
    else: raise AssertionError('Admitted stale ' + operation)
try: sender.send_whatsapp('+15550000001', 'body')
except ValidationError: pass
else: raise AssertionError('Admitted stale message')
assert sender.send_whatsapp('+15550000001', 'body', raise_on_error=False) is False
print('PASS actual server source: actor/default resolution, independent voice/message, absent/pending/offline/unregistered/ignored/denied, stale action refusal before side-effect dependencies')
