from . import audio
from . import agent
from . import agent_prompt
from . import agent_tool
from . import agent_template
from . import agent_transfer
from . import call
from . import exten
from . import phone_registration
from . import number
from . import settings
from . import recording

# Inject documentation page.
from odoo.addons.connect.models.documentation import PAGE_MAP
PAGE_MAP[2] = ['Connect Elevenlabs', 'connect_elevenlabs']
