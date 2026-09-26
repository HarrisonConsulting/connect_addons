# -*- coding: utf-8 -*-
"""connect_voicetel is a standalone provider: prove it structurally rather
than trusting the manifest's 'depends' list alone (a stray import survives a
manifest edit).

Comments and docstrings are allowed to name connect_twilio when explaining a
coexistence decision (several files do, on purpose — that is documentation,
not a dependency); what must never exist is an import of it, or a live
string reference to it (a model key, an xmlid, an _inherit target). This
walks the AST rather than grepping raw text, specifically to tell those two
apart: a blind text grep would also fail on the sentence you are reading in
this file's own source.
"""

import ast
import os
import unittest

import odoo.addons.connect_voicetel as connect_voicetel

FORBIDDEN = ('connect_twilio', 'connect.twilio')
SCANNED_DIRS = ('controllers', 'data', 'migrations', 'models', 'security', 'views')


def _is_docstring(node, parent_body):
    return bool(parent_body) and parent_body[0] is node and isinstance(
        node, ast.Expr) and isinstance(getattr(node, 'value', None), ast.Constant) \
        and isinstance(node.value.value, str)


def _iter_bodies(tree):
    """Every (node-list) body in the module: itself, every class, every
    function — this is where a docstring can legally sit."""
    yield tree.body
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.body


def _find_live_references(source, path):
    tree = ast.parse(source, filename=path)
    docstring_nodes = set()
    for body in _iter_bodies(tree):
        if body and _is_docstring(body[0], body):
            docstring_nodes.add(id(body[0].value))

    offenses = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [node.module] if isinstance(node, ast.ImportFrom) else []
            names += [alias.name for alias in node.names]
            for name in names:
                if name and any(f in name for f in FORBIDDEN):
                    offenses.append('import: {}'.format(name))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstring_nodes:
                continue  # documentation, not a dependency
            if any(f in node.value for f in FORBIDDEN):
                offenses.append('string literal: {!r}'.format(node.value))
    return offenses


class TestNoTwilioDependency(unittest.TestCase):

    def test_no_python_file_imports_or_references_connect_twilio(self):
        root = os.path.dirname(connect_voicetel.__file__)
        offenders = {}
        for sub in SCANNED_DIRS:
            sub_path = os.path.join(root, sub)
            if not os.path.isdir(sub_path):
                continue
            for dirpath, _dirnames, filenames in os.walk(sub_path):
                for name in filenames:
                    if not name.endswith('.py'):
                        continue
                    full_path = os.path.join(dirpath, name)
                    with open(full_path, encoding='utf-8') as f:
                        source = f.read()
                    offenses = _find_live_references(source, full_path)
                    if offenses:
                        offenders[os.path.relpath(full_path, root)] = offenses
        self.assertFalse(
            offenders,
            'connect_voicetel must not import or hold a live reference to '
            'connect_twilio; found: {}'.format(offenders))

    def test_manifest_depends_only_on_connect(self):
        root = os.path.dirname(connect_voicetel.__file__)
        with open(os.path.join(root, '__manifest__.py')) as f:
            manifest_dict = ast.literal_eval(f.read())
        self.assertEqual(manifest_dict.get('depends'), ['connect'])


if __name__ == '__main__':
    unittest.main()
