"""Form scripts (MuPDF JavaScript), behaviour settings, button actions, and form data."""
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO

import pymupdf as fitz

from pdflx import cli, document_features as features, document_tools as tools
from pdflx.form_buttons import configure, details, describe
from pdflx.ops import formbehaviour, formdata, formjs, forms, javascript
from pdflx.ops.common import OperationError


def invoice():
    doc = fitz.open()
    doc.new_page()
    price = tools.create_form_field(doc, 0, 'Price', 'text', (72, 72, 272, 92))
    quantity = tools.create_form_field(doc, 0, 'Qty', 'text', (72, 100, 272, 120))
    total = tools.create_form_field(doc, 0, 'Total', 'text', (72, 128, 272, 148))
    date = tools.create_form_field(doc, 0, 'Date', 'text', (72, 156, 272, 176))
    formbehaviour.apply(doc, price, {'format': 'number', 'decimals': 2, 'currency': '€ ', 'minimum': 0,
                                     'maximum': 1000})
    formbehaviour.apply(doc, quantity, {'format': 'number', 'decimals': 0})
    formbehaviour.apply(doc, total, {'format': 'number', 'decimals': 2, 'currency': '€ ', 'calculate': 'SUM',
                                     'sources': ['Price', 'Qty']})
    formbehaviour.apply(doc, date, {'format': 'date', 'date_format': 'dd/mm/yyyy'})
    return doc, price, quantity, total, date


def values(doc):
    return {w.field_name: w.field_value for page in doc for w in page.widgets()}


@unittest.skipUnless(formjs.available(), 'MuPDF JavaScript is not available')
class FormScriptTests(unittest.TestCase):
    def test_behaviour_scripts_round_trip_and_calculation_order(self):
        doc, price, _quantity, total, date = invoice()
        self.assertEqual(formbehaviour.parse(doc, price)['maximum'], 1000)
        self.assertEqual(formbehaviour.parse(doc, total)['sources'], ['Price', 'Qty'])
        self.assertEqual(formbehaviour.parse(doc, date)['date_format'], 'dd/mm/yyyy')
        self.assertEqual([name for _x, name in formbehaviour.calculation_order(doc)], ['Total'])
        scripts = [entry['event'] for entry in javascript.scripts(doc)]
        self.assertIn('Calculate', scripts)
        self.assertIn('Format', scripts)
        formbehaviour.apply(doc, total, {'format': 'none', 'calculate': 'none'})
        self.assertEqual(formbehaviour.calculation_order(doc), [])
        with self.assertRaises(OperationError):
            formbehaviour.build_scripts({'calculate': 'SUM', 'sources': []})
        with self.assertRaises(OperationError):
            formbehaviour.build_scripts({'minimum': 5, 'maximum': 1})

    def test_filling_runs_calculation_formatting_and_validation(self):
        doc, price, quantity, _total, date = invoice()
        features.update_form_fields(doc, {(0, price): '1.234,5' if False else '12.5', (0, quantity): '3'},
                                    run_scripts=True)
        self.assertEqual(values(doc)['Total'], '15.5')
        text = doc[0].get_text()
        self.assertIn('€ 12.50', text)
        self.assertIn('€ 15.50', text)
        with self.assertRaises(ValueError) as rejected:
            features.update_form_fields(doc, {(0, price): '5000'}, run_scripts=True)
        self.assertIn('0 to 1000', str(rejected.exception))
        with self.assertRaises(ValueError):
            features.update_form_fields(doc, {(0, date): '31/02/2026'}, run_scripts=True)
        features.update_form_fields(doc, {(0, date): '5/10/2026'}, run_scripts=True)
        self.assertEqual(values(doc)['Date'], '05/10/2026')
        saved = fitz.open('pdf', doc.tobytes())
        self.assertEqual(values(saved)['Total'], '15.5')
        # With scripts off, values are stored verbatim and nothing is recalculated.
        features.update_form_fields(doc, {(0, quantity): '10'}, run_scripts=False)
        self.assertEqual(values(doc)['Total'], '15.5')

    def test_value_checks_for_standard_formats(self):
        check = formbehaviour.check_value
        self.assertEqual(check({'format': 'number', 'separator': 2, 'currency': ' €'}, '1.234,50 €'), '1234.5')
        self.assertEqual(check({'format': 'number'}, '(12)'), '-12')
        self.assertEqual(check({'format': 'percent'}, '12.5%'), '0.125')
        self.assertEqual(check({'format': 'special', 'special': 2}, '(555) 123-4567'), '5551234567')
        self.assertEqual(check({'format': 'time', 'time_format': 0}, '13:45'), '13:45')
        for behaviour, value in (({'format': 'number'}, 'abc'), ({'format': 'special', 'special': 0}, '123'),
                                 ({'format': 'time'}, '25:99'), ({'format': 'date', 'date_format': 'yyyy-mm-dd'},
                                                                 '2026-13-01')):
            with self.assertRaises(OperationError):
                check(behaviour, value)
        self.assertEqual(formbehaviour.date_format_to_strftime('d mmm yyyy'), '%-d %b %Y')

    def test_button_actions_and_running_a_script(self):
        doc = fitz.open()
        doc.new_page()
        doc.new_page()
        target = tools.create_form_field(doc, 0, 'Out', 'text', (72, 72, 272, 92))
        expected = {
            'url': ({'button_url': 'example.org'}, 'https://example.org'),
            'print': ({}, None),
            'submit': ({'button_url': 'https://example.org/submit', 'button_format': 'xfdf'},
                       {'url': 'https://example.org/submit', 'format': 'xfdf'}),
            'javascript': ({'button_script': 'this.getField("Out").value = "clicked";'},
                           'this.getField("Out").value = "clicked";'),
            'goto': ({'button_page': 1}, 1),
            'reset': ({}, None),
        }
        for index, (action, (options, result)) in enumerate(expected.items()):
            xref = tools.create_form_field(doc, 0, f'B{index}', 'button', (300, 72 + index * 40, 400, 102 + index * 40),
                                           options=dict(options, button_action=action, button_caption=action))
            self.assertEqual(details(doc, xref), (action, result), action)
            self.assertTrue(describe(action, result))
        with self.assertRaises(ValueError):
            configure(doc, xref, {'button_action': 'submit', 'button_url': 'ftp://nope'})
        script_button = next(w.xref for w in doc[0].widgets() if w.field_name == 'B3')
        formjs.run_button(doc, 0, script_button)
        self.assertEqual(values(doc)['Out'], 'clicked')
        self.assertIsNotNone(target)


class FormDataTests(unittest.TestCase):
    def build(self):
        doc = fitz.open()
        doc.new_page()
        street = tools.create_form_field(doc, 0, 'street', 'text', (50, 50, 250, 70), value='Via Roma 1')
        city = tools.create_form_field(doc, 0, 'city', 'text', (50, 80, 250, 100), value='Rome (RM)')
        forms.group_fields(doc, [street, city], 'address')
        tools.create_form_field(doc, 0, 'agree', 'checkbox', (50, 110, 70, 130))
        tools.create_form_field(doc, 0, 'size', 'radio', (50, 140, 250, 200), ['Small', 'Large'], value='Large')
        tools.create_form_field(doc, 0, 'tags', 'list', (50, 210, 250, 270), ['a', 'b', 'c'],
                                options={'multi_select': True}, value=['a', 'c'])
        tools.create_form_field(doc, 0, 'color', 'combo', (50, 280, 250, 300), ['Red', 'Blue'], value='Blue')
        return doc

    def test_round_trip_all_formats(self):
        doc = self.build()
        expected = formdata.collect(doc)
        self.assertEqual(expected['address.city'], 'Rome (RM)')
        self.assertEqual(expected['tags'], ['a', 'c'])
        for key, _label in formdata.FORMATS:
            parsed = formdata.parse(formdata.export(doc, key, 'form.pdf'), key)
            if key == 'csv':
                parsed['tags'] = parsed['tags'].split(';')
            self.assertEqual(parsed, expected, key)
        self.assertEqual(formdata.detect_format('x.bin', formdata.export(doc, 'fdf')), 'fdf')

    def test_import_fills_every_field_type(self):
        doc = self.build()
        changed, unmatched = formdata.import_values(doc, {
            'address.street': 'Main St', 'address.city': 'Oslo', 'agree': 'Yes', 'size': 'Small',
            'tags': 'b', 'color': 'Red', 'missing': 'x'}, run_scripts=False)
        self.assertEqual(unmatched, ['missing'])
        self.assertEqual(formdata.collect(doc), {'address.street': 'Main St', 'address.city': 'Oslo',
                                                 'agree': 'Yes', 'size': 'Small', 'tags': ['b'], 'color': 'Red'})
        with self.assertRaises(OperationError):
            formdata.import_values(doc, {'nothing': '1'})
        fdf = b'%FDF-1.2\n1 0 obj<</FDF<</Fields[<</T(address)/Kids[<</T(city)/V<FEFF004F0073006C006F00320030>>>]>>]>>>>endobj'
        self.assertEqual(formdata.parse_fdf(fdf), {'address.city': 'Oslo20'})

    def test_cli_form_export_and_import(self):
        doc = self.build()
        with tempfile.TemporaryDirectory() as directory:
            pdf = os.path.join(directory, 'form.pdf')
            doc.save(pdf)
            data = os.path.join(directory, 'values.json')
            with open(data, 'w') as handle:
                json.dump({'fields': {'address.city': 'Paris', 'agree': 'Yes'}}, handle)
            output = os.path.join(directory, 'filled.pdf')
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(cli.main(['form-import', pdf, data, '-o', output]), 0)
                self.assertEqual(cli.main(['form-export', output, '--format', 'xfdf', '-o',
                                           os.path.join(directory, 'out.xfdf')]), 0)
            with open(os.path.join(directory, 'out.xfdf'), 'rb') as handle:
                exported = formdata.parse_xfdf(handle.read())
            self.assertEqual((exported['address.city'], exported['agree']), ('Paris', 'Yes'))


if __name__ == '__main__':
    unittest.main()
