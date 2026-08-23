# -*- coding: utf-8 -*-
import os
import re
from odoo import models, fields, api, modules

DEFAULT_MODULE = 'connect'


class ConnectDocumentation(models.TransientModel):
    _name = 'connect.documentation'
    _description = 'Connect Documentation'

    module = fields.Char(
        default=lambda self: self.env.context.get('documentation_module', DEFAULT_MODULE),
        help='Technical name of the module whose doc/index.rst is rendered.')
    name = fields.Char(compute='_compute_content')
    content = fields.Html(compute='_compute_content')

    @api.depends('module')
    def _compute_content(self):
        for record in self:
            module = record.module or DEFAULT_MODULE
            title = self.env['ir.module.module'].sudo().search(
                [('name', '=', module)], limit=1).shortdesc
            record.name = '{} Documentation'.format(title or module)
            module_path = modules.get_module_path(module)
            rst_file_path = os.path.join(module_path, 'doc', 'index.rst') if module_path else ''
            if rst_file_path and os.path.exists(rst_file_path):
                with open(rst_file_path, 'r', encoding='utf-8') as file:
                    rst_content = file.read()
                record.content = record._rst_to_html(rst_content)
            else:
                record.content = '<div class="alert alert-warning">Documentation file not found</div>'

    def _rst_to_html(self, rst_content):
        """Convert RST content to HTML"""
        lines = rst_content.split('\n')
        html_lines = []
        i = 0
        in_list = False

        while i < len(lines):
            line = lines[i]
            next_line = lines[i + 1] if i + 1 < len(lines) else ''
            trimmed_line = line.strip()

            # Handle headers (underlined)
            if next_line and re.match(r'^[=\-~`#\*\+\^]{2,}$', next_line.strip()):
                header_char = next_line.strip()[0]
                level = self._get_header_level(header_char)

                if in_list:
                    html_lines.append('</ul>')
                    in_list = False

                html_lines.append(f'<h{level}>{self._escape_html(trimmed_line)}</h{level}>')
                i += 2  # Skip current line and underline
                continue

            # Handle empty lines
            if not trimmed_line:
                if in_list:
                    html_lines.append('</ul>')
                    in_list = False
                html_lines.append('<br>')
                i += 1
                continue

            # Handle bullet lists
            if re.match(r'^\s*[\*\-\+]\s+', line):
                if not in_list:
                    html_lines.append('<ul>')
                    in_list = True
                content = re.sub(r'^\s*[\*\-\+]\s*', '', line)
                html_lines.append(f'<li>{self._parse_inline_markup(self._escape_html(content))}</li>')
                i += 1
                continue

            # Handle numbered lists
            if re.match(r'^\s*\d+\.\s+', line):
                if not in_list:
                    html_lines.append('<ol>')
                    in_list = True
                content = re.sub(r'^\s*\d+\.\s*', '', line)
                html_lines.append(f'<li>{self._parse_inline_markup(self._escape_html(content))}</li>')
                i += 1
                continue

            # Handle code blocks (lines starting with 4+ spaces or ::)
            if trimmed_line == '::' or line.endswith('::') or (line.startswith('    ') and trimmed_line):
                if in_list:
                    html_lines.append('</ul>')
                    in_list = False

                code_lines = []
                if trimmed_line == '::':
                    i += 1  # Skip :: line

                # Collect indented lines
                while i < len(lines) and (lines[i].startswith('    ') or not lines[i].strip()):
                    if lines[i].strip():
                        code_lines.append(lines[i][4:])  # Remove 4-space indentation
                    else:
                        code_lines.append('')
                    i += 1

                if code_lines:
                    code_content = '\n'.join(code_lines).strip()
                    html_lines.append(f'<pre><code>{self._escape_html(code_content)}</code></pre>')
                continue

            # Regular paragraph
            if trimmed_line:
                if in_list:
                    html_lines.append('</ul>')
                    in_list = False
                html_lines.append(f'<p>{self._parse_inline_markup(self._escape_html(trimmed_line))}</p>')

            i += 1

        # Close any open lists
        if in_list:
            html_lines.append('</ul>')

        return '\n'.join(html_lines)

    def _get_header_level(self, char):
        levels = {
            '=': 1, '-': 2, '~': 3, '`': 4, '#': 5, '*': 6, '+': 6, '^': 6
        }
        return levels.get(char, 2)

    def _parse_inline_markup(self, text):
        """Parse RST inline markup like **bold**, *italic*, ``code``"""
        # Bold **text**
        text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)

        # Italic *text*
        text = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', text)

        # Inline code ``code``
        text = re.sub(r'``([^`]+)``', r'<code>\1</code>', text)

        return text

    def _escape_html(self, text):
        """Escape HTML characters"""
        return (text.replace('&', '&amp;')
                   .replace('<', '&lt;')
                   .replace('>', '&gt;')
                   .replace('"', '&quot;')
                   .replace("'", '&#x27;'))
