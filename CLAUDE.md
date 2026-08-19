# CLAUDE.md - Connect Addons (Community Edition)

**Repository**: /workspace/HarrisonConsulting/connect_addons/
**Product Family**: @/mnt/gdo/docs/product/odoo/telephony/CLAUDE.md
**Catalog**: @/mnt/gdo/docs/product/odoo/PRODUCT-CATALOG.md

---

## Context

Connect Addons is a Twilio-based telephony integration suite for Odoo. The `connect` module is the core application providing full Twilio integration (calls, SMS, WhatsApp, recordings, callflows). Additional modules extend it for CRM, Helpdesk, Website, ElevenLabs AI, and BYOC (Bring Your Own Carrier) scenarios. This is the Community Edition repository; Enterprise-only modules live in `connect_addons_ee`.

## Module Inventory

| Module | Version | License | Purpose |
|--------|---------|---------|---------|
| `connect` | 1.27.0 | OPL-1 | Core Twilio-Odoo integration, including read-only customer call history in `/my` |
| `connect_crm` | 1.0.4 | Other proprietary | CRM integration: lead creation from calls, UTM tracking |
| `connect_helpdesk` | 1.0.1 | Other proprietary | Helpdesk integration: ticket creation from calls |
| `connect_website` | 1.0.1 | Other proprietary | Website click-to-call snippet using Twilio |
| `connect_byoc` | 1.0.2 | Other proprietary | Bring Your Own Carrier: use external SIP trunks with Twilio |
| `connect_elevenlabs` | 1.4.2 | Other proprietary | ElevenLabs Conversational AI integration for voice agents |
| `connect_elevenlabs_sale` | 1.0.0 | Other proprietary | AI-powered sale management tools for ElevenLabs agents |

## Architecture

```
connect (Application - core Twilio integration)
    |
    +-- connect_crm (CRM lead/call linking, UTM campaigns)
    +-- connect_helpdesk (Helpdesk ticket/call linking)
    +-- connect_website (Website click-to-call snippet)
    +-- connect_byoc (SIP trunk / BYOC configuration)
    +-- connect_elevenlabs (ElevenLabs AI agent sync, telephony, conversations)
            |
            +-- connect_elevenlabs_sale (AI sale management tools)
```

**Key patterns:**
- `connect` is the main application with the top-level phone menu
- Core `connect` owns the ordinary customer `/my/calls` experience; access is read-only, globally opt-in, and scoped to calls linked directly to the portal contact
- OWL components in `connect/static/src/components/phone/` for the softphone UI
- Dark mode support via `web.assets_web_dark` bundle
- Webhook security groups for external Twilio callbacks
- TwiML templates for call routing and IVR
- `connect_elevenlabs` requires the `elevenlabs` Python SDK
- Active calls service pattern: each integration module patches `active_calls` service

**Note on authorship:** The `connect` base module and several addons list "Oduist" as author. Harrison Consulting maintains the fork. HC-authored modules (voice integration, etc.) live in `odoo_voice` and `connect_addons_ee`.

## Dependencies

**External Python packages:**
- `twilio` - Twilio SDK (connect)
- `openai` - OpenAI SDK (connect)
- `elevenlabs` - ElevenLabs SDK (connect_elevenlabs)

**Odoo module dependencies:**
- `mail`, `contacts`, `sms` - Core messaging (connect)
- `crm`, `utm` - CRM pipeline (connect_crm)
- `helpdesk` - Helpdesk tickets (connect_helpdesk) -- Enterprise module
- `website` - Website snippets (connect_website)
- `calendar` - Calendar integration (connect_elevenlabs)
- `sale_management` - Sale orders (connect_elevenlabs_sale)

**Cross-repo dependencies:**
- `connect_addons_ee` extends this repo with Enterprise-only modules (callout, enqueue, AI, enterprise features)
- `odoo_voice` provides the provider-agnostic voice framework that bridges with `connect`

## Module Registration

When creating a new Odoo module in this repository:
1. Add the module to @/mnt/gdo/docs/product/odoo/PRODUCT-CATALOG.md
2. Run `python /mnt/gdo/scripts/validate-catalog.py` to verify registration
3. If complex (>3 models, custom controllers, or JS components):
   create a CLAUDE.md following @/mnt/gdo/docs/product/odoo/.templates/MODULE-CLAUDE.md.template
