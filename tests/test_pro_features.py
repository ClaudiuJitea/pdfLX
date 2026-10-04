"""Sensitive-data patterns, page moves and find/replace expressions."""
import unittest

import pymupdf as fitz

from pdflx.ops import redact_patterns as rp
from pdflx.organize_pages import moved_order
from pdflx.find_replace import expression


class Patterns(unittest.TestCase):
    def test_checksums(self):
        self.assertTrue(rp.luhn('4111 1111 1111 1111'))
        self.assertFalse(rp.luhn('1234 5678 9012 3456'))
        self.assertTrue(rp.iban('DE89 3704 0044 0532 0130 00'))
        self.assertFalse(rp.iban('DE00 3704 0044 0532 0130 00'))

    def test_finds_each_kind_once(self):
        doc = fitz.open()
        page = doc.new_page()
        for index, line in enumerate((
                'Mail sarah.connor@example.com, call +1 (555) 345-6789',
                'IBAN DE89 3704 0044 0532 0130 00, card 4111 1111 1111 1111',
                'Due 30.05.2026, see https://example.org/x. Ref CA98765432',
                'Total 8750.28 EUR, not a card 1234 5678 9012 3456')):
            page.insert_text((40, 60 + index * 20), line, fontsize=9)
        found = {(key, text) for key, text, _quads in rp.find(page, set(rp.ORDER))}
        self.assertEqual(found, {('email', 'sarah.connor@example.com'), ('phone', '+1 (555) 345-6789'),
                                 ('iban', 'DE89 3704 0044 0532 0130 00'), ('card', '4111 1111 1111 1111'),
                                 ('date', '30.05.2026'), ('url', 'https://example.org/x'),
                                 ('number_id', 'CA98765432')})

    def test_only_chosen_kinds(self):
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((40, 60), 'a@b.co +1 555 123 4567', fontsize=9)
        self.assertEqual([key for key, _t, _q in rp.find(page, {'email'})], ['email'])


class PageMoves(unittest.TestCase):
    def test_moved_order(self):
        self.assertEqual(moved_order(5, [3], 0), [3, 0, 1, 2, 4])
        self.assertEqual(moved_order(5, [0, 1], 5), [2, 3, 4, 0, 1])
        self.assertEqual(moved_order(5, [1, 3], 3), [0, 2, 1, 3, 4])
        self.assertEqual(moved_order(3, [1], 1), [0, 1, 2])


class Expressions(unittest.TestCase):
    def test_options(self):
        self.assertEqual(expression('cat').sub('dog', 'Cat cat'), 'dog dog')
        self.assertEqual(expression('cat', case=True).sub('dog', 'Cat cat'), 'Cat dog')
        self.assertEqual(expression('cat', word=True).sub('dog', 'cat concat'), 'dog concat')
        self.assertEqual(expression('a.c').sub('x', 'abc a.c'), 'abc x')
        self.assertEqual(expression(r'(\d+) EUR', regex=True).sub(r'€\1', '12 EUR'), '€12')


if __name__ == '__main__':
    unittest.main()
