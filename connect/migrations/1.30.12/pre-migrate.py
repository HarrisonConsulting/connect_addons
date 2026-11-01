def migrate(cr, version):
    """Keep installed settings extensions valid while their parent view loads."""
    # arch_db is translated JSONB in Odoo 19; update every stored language.
    cr.execute("""
        UPDATE ir_ui_view AS view
           SET arch_db = (
               SELECT jsonb_object_agg(language, regexp_replace(
                   architecture, '\\s+invisible="is_registered == False"', '', 'g'
               ))
                 FROM jsonb_each_text(view.arch_db) AS translations(language, architecture)
           )
         WHERE view.id IN (
             SELECT res_id FROM ir_model_data
              WHERE model = 'ir.ui.view'
                AND (module, name) IN (
                    ('connect_stripe', 'connect_stripe_settings_form_inherit'),
                    ('connect_crm', 'connect_crm_settings_form'),
                    ('connect_website', 'connect_website_settings_form'),
                    ('connect_enterprise', 'connect_enterprise_settings_form')
                )
         )
    """)
