"""Command-line tool regressions."""
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

import pymupdf as fitz

from pdflx import cli


class CliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = lambda name: os.path.join(self.directory.name, name)
        for name, count in (('a.pdf', 3), ('b.pdf', 2)):
            doc = fitz.open()
            for number in range(count):
                doc.new_page().insert_text((72, 72), f'{name} page {number + 1}')
            doc.save(self.path(name))

    def run_cli(self, *args):
        output = StringIO()
        with redirect_stdout(output):
            code = cli.main(list(args))
        return code, output.getvalue()

    def test_merge_split_extract_and_overwrite_protection(self):
        code, _out = self.run_cli('merge', self.path('a.pdf'), self.path('b.pdf'), '-o', self.path('m.pdf'))
        self.assertEqual(code, 0)
        self.assertEqual(fitz.open(self.path('m.pdf')).page_count, 5)
        self.assertEqual(self.run_cli('merge', self.path('a.pdf'), '-o', self.path('m.pdf'))[0], 2)
        parts = self.path('parts')
        self.run_cli('split', self.path('m.pdf'), '-d', parts, '--every', '2')
        self.assertEqual(len(os.listdir(parts)), 3)
        self.run_cli('extract', self.path('m.pdf'), '--pages', '4-5', '-o', self.path('e.pdf'))
        self.assertIn('b.pdf page 1', fitz.open(self.path('e.pdf'))[0].get_text())

    def test_protect_bates_text_info_and_compare(self):
        self.run_cli('protect', self.path('a.pdf'), '--owner-password', 'own', '--user-password', 'open',
                     '-o', self.path('p.pdf'))
        self.assertTrue(fitz.open(self.path('p.pdf')).needs_pass)
        self.assertEqual(self.run_cli('info', self.path('p.pdf'))[0], 2)
        self.run_cli('bates', self.path('a.pdf'), '--prefix', 'X', '--digits', '4', '-o', self.path('n.pdf'))
        self.assertIn('X0003', fitz.open(self.path('n.pdf'))[2].get_text())
        self.run_cli('text', self.path('a.pdf'), '--format', 'json', '-o', self.path('a.json'))
        self.assertIn('a.pdf page 2', open(self.path('a.json'), encoding='utf-8').read())
        code, output = self.run_cli('info', self.path('a.pdf'))
        self.assertIn('"pages": 3', output)
        code, output = self.run_cli('compare', self.path('a.pdf'), self.path('b.pdf'))
        self.assertEqual(code, 1)
        self.assertIn('change', output)

    def test_compose_and_mail_merge(self):
        with open(self.path('doc.md'), 'w') as handle:
            handle.write('# Hello\n\nWorld')
        self.run_cli('compose', self.path('doc.md'), '-o', self.path('doc.pdf'), '--toc')
        self.assertIn('Hello', fitz.open(self.path('doc.pdf'))[1].get_text())
        template = fitz.open()
        template.new_page().insert_text((72, 72), 'Hi {{name}}')
        template.save(self.path('t.pdf'))
        with open(self.path('rows.csv'), 'w') as handle:
            handle.write('name\nAna\nBen\n')
        self.run_cli('mail-merge', self.path('t.pdf'), self.path('rows.csv'), '-d', self.path('out'),
                     '--name', '{name}')
        self.assertIn('Ben', fitz.open(self.path('out/Ben.pdf'))[0].get_text())


if __name__ == '__main__':
    unittest.main()
