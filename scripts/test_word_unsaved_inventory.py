"""Preserve raw foreign metadata without granting an unresolved name a path."""
import unittest

from resource_broker import ResourceBusy
from word_restricted_inventory import parse


NATIVE = '''WORD_RESTRICTED_INVENTORY_V1\t1\t1
DOC\t1\t文件1\t文件1\tfalse
WIN\t1\t文件1\t文件1  -  已復原\tfalse\twindow state minimize
ACTIVE\t文件1
AXAPP\tMicrosoft Word\t1
AXWIN\tMicrosoft Word\t文件1  -  已復原\tAXWindow\tAXDialog\tfalse\ttrue\t0
AXAPP\tZotero\t0
END
'''


class UnsavedInventoryTests(unittest.TestCase):
    def test_original_recovery_draft_stays_unresolved_nonwritable_and_raw(self):
        value = parse(NATIVE)
        self.assertEqual(value['documents'], [['1', '文件1', '文件1', 'false']])
        self.assertEqual(value['windows'][0][1], '文件1')
        self.assertEqual(value['active'], ['文件1'])
        self.assertEqual(value['unresolved_document_references'], [dict(
            document_index='1', raw_reference='文件1', reference_status='UNRESOLVED_UNSAVED_BARE_NAME',
            filesystem_path=None, ownership='UNKNOWN', writable=False)])
        self.assertEqual(parse(NATIVE), value)

    def test_absolute_and_missing_references_keep_the_original_schema(self):
        for ref in ('/private/foreign.docx', 'MISSING'):
            text = NATIVE.replace('DOC\t1\t文件1\t文件1\tfalse', 'DOC\t1\t文件1\t' + ref + '\tfalse')
            text = text.replace('WIN\t1\t文件1\t', 'WIN\t1\t' + ref + '\t')
            text = text.replace('ACTIVE\t文件1', 'ACTIVE\t' + ref)
            with self.subTest(reference=ref):
                value = parse(text)
                self.assertNotIn('unresolved_document_references', value)
                self.assertEqual(set(value), {'documents', 'windows', 'active', 'axapps', 'axwindows'})

    def test_saved_and_unknown_bare_references_are_refused(self):
        for saved in ('true', 'UNKNOWN'):
            text = NATIVE.replace('DOC\t1\t文件1\t文件1\tfalse', 'DOC\t1\t文件1\t文件1\t' + saved)
            with self.subTest(saved=saved), self.assertRaises(ResourceBusy):
                parse(text)

    def test_path_like_dot_and_control_references_are_refused(self):
        for ref in ('.', '..', 'folder/name', 'folder\\name', 'C:name', 'a\x1fb', 'a\x7fb', 'a\x85b', 'a\x9fb'):
            text = NATIVE.replace('文件1', ref)
            with self.subTest(reference=ref), self.assertRaises(ResourceBusy):
                parse(text)

    def test_different_display_name_does_not_identify_an_unsaved_path(self):
        text = NATIVE.replace('DOC\t1\t文件1\t文件1', 'DOC\t1\t另一份稿\t文件1')
        with self.assertRaises(ResourceBusy):
            parse(text)

    def test_bare_name_has_a_bounded_length(self):
        self.assertIsNone(parse(NATIVE.replace('文件1', '字' * 4096))['unresolved_document_references'][0]['filesystem_path'])
        with self.assertRaises(ResourceBusy):
            parse(NATIVE.replace('文件1', '字' * 4097))

    def test_ambiguous_document_reference_is_refused(self):
        text = NATIVE.replace('INVENTORY_V1\t1\t1', 'INVENTORY_V1\t2\t1')
        text = text.replace('WIN\t1', 'DOC\t2\t文件1\t文件1\tfalse\nWIN\t1')
        with self.assertRaisesRegex(ResourceBusy, 'UNRESOLVED_NATIVE_REFERENCE_NOT_UNIQUE'):
            parse(text)

    def test_ambiguous_or_unmatched_window_reference_is_refused(self):
        text = NATIVE.replace('INVENTORY_V1\t1\t1', 'INVENTORY_V1\t1\t2')
        text = text.replace('ACTIVE\t', 'WIN\t2\t文件1\tSecond window\tfalse\twindow state normal\nACTIVE\t')
        for value in (text, NATIVE.replace('WIN\t1\t文件1\t', 'WIN\t1\tMISSING\t')):
            with self.subTest(inventory=value), self.assertRaisesRegex(ResourceBusy, 'UNRESOLVED_NATIVE_REFERENCE_NOT_UNIQUE'):
                parse(value)

    def test_foreign_name_reference_saved_and_window_changes_remain_observable(self):
        before = parse(NATIVE)
        self.assertNotEqual(before, parse(NATIVE.replace('文件1', '文件2')))
        self.assertNotEqual(before, parse(NATIVE.replace('window state minimize', 'window state normal')))
        text = NATIVE.replace('DOC\t1\t文件1\t文件1\tfalse', 'DOC\t1\t文件1\t/private/new.docx\ttrue')
        self.assertNotEqual(before, parse(text))


if __name__ == '__main__':
    unittest.main()
