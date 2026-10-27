"""Meaningful endpoint mutations; no native application and no real queue writes."""
from copy import deepcopy
import io
import json
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib

import word_semantic_contract as sem

W = sem.W
R = sem.R
def x(text):
    return ET.fromstring('<root xmlns:w="' + W[1:-1] + '" xmlns:r="' + R[1:-1] + '">' + text + '</root>')[0]

def pack(body=None, footer=None, styles=None, notes=None):
    root = x('<w:document><w:body><w:p><w:r><w:t>正文Ab1</w:t></w:r></w:p></w:body></w:document>')
    if body is not None:
        root = x('<w:document><w:body>' + body + '</w:body></w:document>')
    default_styles = x('<w:styles><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="宋体"/><w:sz w:val="21"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr/></w:pPrDefault></w:docDefaults><w:style w:type="paragraph" w:styleId="Normal" w:default="1"><w:name w:val="Normal"/><w:pPr><w:ind w:firstLineChars="200" w:firstLine="420"/></w:pPr></w:style></w:styles>')
    parts = {'word/document.xml': root, 'word/styles.xml': styles if styles is not None else default_styles}
    parts['word/numbering.xml'] = x('<w:numbering><w:abstractNum w:abstractNumId="4"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum><w:num w:numId="9"><w:abstractNumId w:val="4"/></w:num></w:numbering>')
    if footer is not None:
        parts['word/footer2.xml'] = x('<w:ftr><w:p><w:pPr><w:ind w:firstLine="0" w:firstLineChars="0"/><w:jc w:val="center"/></w:pPr>' + footer + '</w:p></w:ftr>')
        root.find(W + 'body').append(x('<w:sectPr><w:footerReference w:type="default" r:id="rId1"/></w:sectPr>'))
        parts['word/_rels/document.xml.rels'] = ET.fromstring('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="' + R[1:-1] + '/footer" Target="footer2.xml"/></Relationships>')
    if notes is not None:
        parts['word/footnotes.xml'] = x('<w:footnotes>' + notes + '</w:footnotes>')
    return encode(parts)

def encode(parts):
    b = io.BytesIO()
    with ZipFile(b, 'w', ZIP_DEFLATED) as z:
        for name, tree in parts.items():
            z.writestr(name, ET.tostring(tree, encoding='utf-8') if isinstance(tree, ET.Element) else tree)
    return b.getvalue()

def mutate(data, fn, part='word/document.xml'):
    with ZipFile(io.BytesIO(data)) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    tree = ET.fromstring(parts[part])
    fn(tree)
    parts[part] = ET.tostring(tree, encoding='utf-8')
    return encode(parts)

def para(pr='', runpr='', text='正文Ab1'):
    return '<w:p><w:pPr>' + pr + '</w:pPr><w:r><w:rPr>' + runpr + '</w:rPr><w:t>' + text + '</w:t></w:r></w:p>'

def table():
    return '<w:tbl><w:tblPr><w:tblW w:w="4000" w:type="dxa"/></w:tblPr><w:tblGrid><w:gridCol w:w="2000"/><w:gridCol w:w="2000"/></w:tblGrid><w:tr><w:trPr/><w:tc><w:tcPr><w:tcW w:w="2000" w:type="dxa"/></w:tcPr>' + para(text='甲') + '</w:tc><w:tc><w:tcPr><w:tcW w:w="2000" w:type="dxa"/></w:tcPr>' + para(text='2') + '</w:tc></w:tr></w:tbl>'

PAGE_CODE = ' PAGE '
def page(display='', before=''):
    return before + '<w:r><w:fldChar w:fldCharType="begin" w:dirty="true"/></w:r><w:r><w:instrText xml:space="preserve">' + PAGE_CODE + '</w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>' + display + '</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>'

def policy():
    return {'mode': sem.MODE, 'page_cache_fields': {'accepted': [{'story': 'word/footer2.xml', 'ordinal': 0, 'instruction_sha256': hashlib.sha256(PAGE_CODE.encode()).hexdigest()}], 'rejected': []}}

class SemanticTests(unittest.TestCase):
    def assert_same(self, a, b, **kwargs):
        self.assertTrue(sem.compare(a, b, **kwargs)['all_pass'])
    def assert_changed(self, a, b, **kwargs):
        self.assertFalse(sem.compare(a, b, **kwargs)['all_pass'])

    def test_unchanged_package_and_changed_text(self):
        a = pack()
        self.assert_same(a, a)
        b = mutate(a, lambda r: setattr(r.find('.//' + W + 't'), 'text', '正文Ab2'))
        self.assert_changed(a, b)

    def test_both_views_preserve_deleted_and_inserted_decisions(self):
        a = pack(body='<w:p><w:del w:id="1" w:author="作者"><w:r><w:delText>旧</w:delText></w:r></w:del><w:ins w:id="2" w:author="作者"><w:r><w:t>新</w:t></w:r></w:ins></w:p>')
        expected_new, expected_old = pack(body=para(text='新')), pack(body=para(text='旧'))
        self.assert_same(expected_new, a, actual_accept=True)
        self.assert_same(expected_old, a, expected_accept=False, actual_accept=False)
        b = mutate(a, lambda r: r.find('.//' + W + 'p').remove(r.find('.//' + W + 'del')))
        self.assert_changed(expected_old, b, expected_accept=False, actual_accept=False)

    def test_grid_width_change_with_same_text_is_rejected(self):
        a = pack(body=table())
        b = mutate(a, lambda r: r.find('.//' + W + 'gridCol').set(W + 'w', '360'))
        self.assert_changed(a, b)

    def test_cell_width_change_with_same_text_is_rejected(self):
        a = pack(body=table())
        self.assert_changed(a, mutate(a, lambda r: r.find('.//' + W + 'tcW').set(W + 'w', '360')))

    def test_grid_span_change_is_rejected(self):
        a = pack(body=table())
        b = mutate(a, lambda r: r.find('.//' + W + 'tcPr').append(x('<w:gridSpan w:val="2"/>')))
        self.assert_changed(a, b)

    def test_grid_before_and_after_are_preserved(self):
        a = pack(body=table())
        for tag in ('gridBefore', 'gridAfter'):
            with self.subTest(tag=tag):
                b = mutate(a, lambda r: r.find('.//' + W + 'trPr').append(x('<w:' + tag + ' w:val="1"/>')))
                self.assert_changed(a, b)

    def test_vertical_merge_state_is_preserved(self):
        a = pack(body=table().replace('<w:tcW w:w="2000" w:type="dxa"/>', '<w:tcW w:w="2000" w:type="dxa"/><w:vMerge w:val="restart"/>', 1))
        self.assert_changed(a, mutate(a, lambda r: r.find('.//' + W + 'vMerge').set(W + 'val', 'continue')))

    def test_row_height_is_preserved(self):
        a = pack(body=table())
        self.assert_changed(a, mutate(a, lambda r: r.find('.//' + W + 'trPr').append(x('<w:trHeight w:val="720" w:hRule="exact"/>'))))

    def test_table_property_revision_restores_geometry_in_rejected_view(self):
        a = pack(body=table().replace('<w:tblW w:w="4000" w:type="dxa"/>',
            '<w:tblW w:w="4000" w:type="dxa"/><w:tblPrChange w:id="20" w:author="作者"><w:tblPr><w:tblW w:w="3000" w:type="dxa"/></w:tblPr></w:tblPrChange>'))
        expected_old = pack(body=table().replace('w:w="4000"', 'w:w="3000"'))
        self.assert_same(expected_old, a, expected_accept=False, actual_accept=False)
        self.assert_changed(pack(body=table()), a, expected_accept=False, actual_accept=False)

    def test_grid_history_restores_old_grid(self):
        a = pack(body=table().replace('</w:tblGrid>', '<w:tblGridChange w:id="21"><w:tblGrid><w:gridCol w:w="1500"/><w:gridCol w:w="2500"/></w:tblGrid></w:tblGridChange></w:tblGrid>'))
        b = pack(body=table().replace('<w:gridCol w:w="2000"/><w:gridCol w:w="2000"/>', '<w:gridCol w:w="1500"/><w:gridCol w:w="2500"/>'))
        self.assert_same(b, a, expected_accept=False, actual_accept=False)

    def test_cant_split_boolean_aliases(self):
        a = pack(body=table().replace('<w:trPr/>', '<w:trPr><w:cantSplit w:val="true"/></w:trPr>'))
        b = pack(body=table().replace('<w:trPr/>', '<w:trPr><w:cantSplit/></w:trPr>'))
        self.assert_same(a, b)
        self.assert_changed(a, pack(body=table().replace('<w:trPr/>', '<w:trPr><w:cantSplit w:val="0"/></w:trPr>')))

    def test_conditional_table_style_change_is_not_dropped(self):
        styles = x('<w:styles><w:style w:type="table" w:styleId="Grid"><w:tblStylePr w:type="firstRow"><w:rPr><w:b/></w:rPr></w:tblStylePr></w:style></w:styles>')
        a = pack(body=table().replace('<w:tblPr>', '<w:tblPr><w:tblStyle w:val="Grid"/>'), styles=styles)
        b = mutate(a, lambda r: r.find('.//' + W + 'b').set(W + 'val', '0'), 'word/styles.xml')
        self.assert_changed(a, b)

    def test_materialized_numbering_indent_is_equivalent(self):
        pr = '<w:numPr><w:numId w:val="9"/></w:numPr>'
        a = pack(body=para(pr=pr))
        b = pack(body=para(pr=pr + '<w:ind w:left="720" w:hanging="360"/>'))
        self.assert_same(a, b)
        self.assert_changed(a, pack(body=para(pr=pr + '<w:ind w:left="720" w:hanging="720"/>')))

    def test_first_line_character_units_take_precedence(self):
        a = pack(body=para(pr='<w:ind w:firstLineChars="200" w:firstLine="420"/>'))
        b = pack(body=para(pr='<w:ind w:firstLineChars="200" w:firstLine="360"/>'))
        self.assert_same(a, b)
        self.assert_changed(a, pack(body=para(pr='<w:ind w:firstLineChars="100" w:firstLine="420"/>')))

    def test_inherited_font_size_change_is_rejected(self):
        a = pack()
        b = mutate(a, lambda r: r.find('.//' + W + 'sz').set(W + 'val', '22'), 'word/styles.xml')
        self.assert_changed(a, b)

    def test_bold_false_aliases_and_real_bold_change(self):
        for val in ('0', 'false', 'off'):
            self.assert_same(pack(body=para()), pack(body=para(runpr='<w:b w:val="' + val + '"/>')))
        self.assert_changed(pack(body=para()), pack(body=para(runpr='<w:b/>')))

    def test_noncomplex_bcs_serialization_is_equivalent(self):
        self.assert_same(pack(body=para(runpr='<w:bCs/>')), pack(body=para(runpr='<w:bCs w:val="false"/>')))

    def test_complex_script_bcs_change_is_rejected(self):
        self.assert_changed(pack(body=para(runpr='<w:bCs/>', text='العربية')), pack(body=para(runpr='<w:bCs w:val="0"/>', text='العربية')))

    def test_forced_cs_and_rtl_protect_bcs_for_latin(self):
        for forced in ('cs', 'rtl'):
            self.assert_changed(pack(body=para(runpr='<w:' + forced + '/><w:bCs/>', text='abc')),
                                pack(body=para(runpr='<w:' + forced + '/><w:bCs w:val="0"/>', text='abc')))

    def test_complex_font_hint_protects_bcs(self):
        self.assert_changed(pack(body=para(runpr='<w:rFonts w:hint="cs"/><w:bCs/>')),
                            pack(body=para(runpr='<w:rFonts w:hint="cs"/><w:bCs w:val="0"/>')))

    def test_exact_page_cache_fill_is_allowed(self):
        a, b = pack(footer=page()), pack(footer=page('12'))
        self.assert_same(a, b, page_fields=policy()['page_cache_fields']['accepted'])
        self.assert_changed(a, b)

    def test_page_instruction_and_nonnumeric_cache_are_rejected(self):
        a = pack(footer=page())
        b = mutate(a, lambda r: setattr(r.find('.//' + W + 'instrText'), 'text', ' NUMPAGES '), 'word/footer2.xml')
        with self.assertRaisesRegex(sem.SemanticError, 'PAGE'):
            sem.compare(a, b, page_fields=policy()['page_cache_fields']['accepted'])
        with self.assertRaisesRegex(sem.SemanticError, 'PAGE_CACHE_NOT_POSITIVE_DECIMAL'):
            sem.compare(a, pack(footer=page('12X')), page_fields=policy()['page_cache_fields']['accepted'])

    def test_page_cache_must_be_positive_and_canonical(self):
        a = pack(footer=page())
        fields = policy()['page_cache_fields']['accepted']
        for invalid in ('0', '00', '01', '-1', '+1', '1.0', ' 1', '1 ', '１２', '1234567890123'):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(sem.SemanticError, 'PAGE_CACHE_NOT_POSITIVE_DECIMAL'):
                    sem.compare(a, pack(footer=page(invalid)), page_fields=fields)
        for valid in ('1', '12', '123456789012'):
            with self.subTest(valid=valid):
                result = sem.compare(a, pack(footer=page(valid)), page_fields=fields,
                                     require_page_cache=True)
                self.assertTrue(result['all_pass'])
                self.assertEqual(result['actual_page_cache'][0]['cache'], valid)

    def test_final_accepted_page_cache_cannot_remain_empty(self):
        a = pack(footer=page())
        fields = policy()['page_cache_fields']['accepted']
        self.assert_same(a, a, page_fields=fields)
        with self.assertRaisesRegex(sem.SemanticError, 'PAGE_CACHE_NOT_POSITIVE_DECIMAL'):
            sem.compare(a, a, page_fields=fields, require_page_cache=True)

    def test_true_paragraph_defaults_do_not_equal_explicit_false(self):
        for prop in sorted(sem.P_DEFAULT_TRUE):
            with self.subTest(prop=prop):
                self.assert_same(pack(body=para()), pack(body=para(pr='<w:' + prop + '/>')))
                self.assert_changed(pack(body=para()),
                                    pack(body=para(pr='<w:' + prop + ' w:val="0"/>')))

    def test_run_grid_default_is_enabled_and_style_inheritance_wins(self):
        self.assert_same(pack(body=para()), pack(body=para(runpr='<w:snapToGrid/>')))
        self.assert_changed(pack(body=para()),
                            pack(body=para(runpr='<w:snapToGrid w:val="0"/>')))
        inherited = x('<w:styles><w:docDefaults><w:rPrDefault><w:rPr><w:snapToGrid w:val="0"/></w:rPr></w:rPrDefault></w:docDefaults></w:styles>')
        self.assert_same(pack(body=para(), styles=inherited),
                         pack(body=para(runpr='<w:snapToGrid w:val="0"/>'), styles=inherited))
        self.assert_changed(pack(body=para(), styles=inherited),
                            pack(body=para(runpr='<w:snapToGrid/>'), styles=inherited))

    def test_unproved_paragraph_default_stays_distinct(self):
        self.assert_changed(pack(body=para()),
                            pack(body=para(pr='<w:overflowPunct w:val="0"/>')))

    def test_layout_and_field_settings_are_protected(self):
        with ZipFile(io.BytesIO(pack())) as z:
            base = {n: z.read(n) for n in z.namelist()}
        for setting in ('<w:updateFields w:val="0"/>',
                        '<w:compat><w:doNotUseHTMLParagraphAutoSpacing/></w:compat>',
                        '<w:defaultTabStop w:val="720"/>'):
            with self.subTest(setting=setting):
                a = dict(base, **{'word/settings.xml': x('<w:settings>' + setting + '</w:settings>')})
                b = dict(base, **{'word/settings.xml': x('<w:settings/>')})
                self.assert_changed(encode(a), encode(b))
        a = dict(base, **{'word/webSettings.xml': x('<w:webSettings><w:optimizeForBrowser/></w:webSettings>')})
        b = dict(base, **{'word/webSettings.xml': x('<w:webSettings/>')})
        self.assert_changed(encode(a), encode(b))

    def test_proof_and_revision_session_metadata_are_diagnostic(self):
        with ZipFile(io.BytesIO(pack())) as z:
            base = {n: z.read(n) for n in z.namelist()}
        a = dict(base, **{'word/settings.xml': x('<w:settings><w:proofState w:spelling="clean"/><w:rsids><w:rsid w:val="00000001"/></w:rsids><w:defaultTabStop w:val="720"/></w:settings>')})
        b = dict(base, **{'word/settings.xml': x('<w:settings><w:defaultTabStop w:val="720"/></w:settings>')})
        self.assert_same(encode(a), encode(b))

    def test_revision_display_is_separate_and_explicitly_observable(self):
        with ZipFile(io.BytesIO(pack())) as z:
            base = {n: z.read(n) for n in z.namelist()}
        tracked = dict(base, **{'word/settings.xml': x('<w:settings><w:trackRevisions/><w:revisionView w:markup="0"/></w:settings>')})
        clean = dict(base, **{'word/settings.xml': x('<w:settings><w:revisionView w:markup="0"/></w:settings>')})
        visible = dict(base, **{'word/settings.xml': x('<w:settings><w:revisionView w:markup="1"/></w:settings>')})
        self.assert_same(encode(tracked), encode(clean))
        self.assertTrue(sem.revision_display_state(encode(tracked))['track_revisions'])
        self.assertFalse(sem.revision_display_state(encode(clean))['track_revisions'])
        self.assertTrue(sem.revision_display_state(encode(clean))['markup_hidden'])
        self.assertFalse(sem.revision_display_state(encode(visible))['markup_hidden'])

    def test_ppr_restore_retains_separately_tracked_mark_and_section(self):
        current = ('<w:pPr><w:jc w:val="right"/><w:rPr><w:b/><w:rPrChange w:id="2">'
                   '<w:rPr><w:b w:val="0"/></w:rPr></w:rPrChange></w:rPr>'
                   '<w:sectPr><w:pgSz w:w="12000"/><w:sectPrChange w:id="3">'
                   '<w:sectPr><w:pgSz w:w="11000"/></w:sectPr></w:sectPrChange></w:sectPr>'
                   '<w:pPrChange w:id="1"><w:pPr><w:jc w:val="left"/></w:pPr></w:pPrChange></w:pPr>')
        a = pack(body='<w:p>' + current + '<w:r><w:t>x</w:t></w:r></w:p>')
        b = pack(body=para(pr='<w:jc w:val="left"/><w:rPr><w:b w:val="0"/></w:rPr><w:sectPr><w:pgSz w:w="11000"/></w:sectPr>', text='x'))
        self.assert_same(b, a, expected_accept=False, actual_accept=False)

    def test_ambiguous_or_nested_property_history_is_refused(self):
        ambiguous = pack(body=para(pr='<w:pPrChange w:id="1"><w:pPr/></w:pPrChange><w:pPrChange w:id="2"><w:pPr/></w:pPrChange>'))
        with self.assertRaisesRegex(sem.SemanticError, 'AMBIGUOUS_PROPERTY_REVISION'):
            sem.snapshot(ambiguous)
        nested = pack(body=para(pr='<w:pPrChange w:id="1"><w:pPr><w:pPrChange w:id="2"><w:pPr/></w:pPrChange></w:pPr></w:pPrChange>'))
        with self.assertRaisesRegex(sem.SemanticError, 'NESTED_PROPERTY_REVISION'):
            sem.snapshot(nested)

    def test_page_cache_font_is_protected(self):
        a = pack(footer=page())
        b = pack(footer=page('1').replace('<w:r><w:t>1', '<w:r><w:rPr><w:sz w:val="24"/></w:rPr><w:t>1'))
        with self.assertRaisesRegex(sem.SemanticError, 'PAGE_CACHE_FORMAT_CHANGED'):
            sem.compare(a, b, page_fields=policy()['page_cache_fields']['accepted'])

    def test_page_surrounding_text_is_protected(self):
        a = pack(footer=page('', '<w:r><w:t>前文</w:t></w:r>'))
        b = pack(footer=page('1', '<w:r><w:t>变更</w:t></w:r>'))
        self.assert_changed(a, b, page_fields=policy()['page_cache_fields']['accepted'])

    def test_zotero_field_result_and_instruction_remain_exact(self):
        citation = '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> ADDIN ZOTERO_ITEM CSL_CITATION {&quot;citationID&quot;:&quot;x&quot;} </w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>(作者, 2017)</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>'
        a = pack(body=para(text='正文') + '<w:p>' + citation + '</w:p>', footer=page())
        b = pack(body=para(text='正文') + '<w:p>' + citation.replace('2017', '2018') + '</w:p>', footer=page('1'))
        self.assert_changed(a, b, page_fields=policy()['page_cache_fields']['accepted'])
        b = pack(body=para(text='正文') + '<w:p>' + citation.replace('&quot;x&quot;', '&quot;y&quot;') + '</w:p>', footer=page('1'))
        self.assert_changed(a, b, page_fields=policy()['page_cache_fields']['accepted'])

    def test_page_field_boundaries_are_required(self):
        a = pack(footer=page())
        b = pack(footer=page().replace('<w:r><w:fldChar w:fldCharType="end"/></w:r>', ''))
        with self.assertRaisesRegex(sem.SemanticError, 'UNCLOSED_COMPLEX_FIELD'):
            sem.compare(a, b, page_fields=policy()['page_cache_fields']['accepted'])

    def test_page_exemption_requires_referenced_running_story(self):
        a = pack(footer=page())
        b = mutate(a, lambda r: r.find('.//' + W + 'sectPr').remove(r.find('.//' + W + 'footerReference')))
        with self.assertRaisesRegex(sem.SemanticError, 'NOT_REFERENCED'):
            sem.snapshot(b, page_fields=policy()['page_cache_fields']['accepted'])

    def test_note_renumbering_requires_same_reference_order_and_payloads(self):
        body = '<w:p><w:r><w:t>甲</w:t><w:footnoteReference w:id="4"/></w:r><w:r><w:t>乙</w:t><w:footnoteReference w:id="5"/></w:r></w:p>'
        notes = '<w:footnote w:id="4">' + para(text='甲注') + '</w:footnote><w:footnote w:id="5">' + para(text='乙注') + '</w:footnote>'
        a = pack(body=body, notes=notes)
        b = pack(body=body.replace('w:id="4"', 'w:id="1"').replace('w:id="5"', 'w:id="2"'),
                 notes=notes.replace('w:id="4"', 'w:id="1"').replace('w:id="5"', 'w:id="2"'))
        self.assert_same(a, b)
        self.assert_changed(a, pack(body=body, notes=notes.replace('甲注', '不同')))

    def test_unknown_content_nodes_are_preserved(self):
        a = pack(body='<w:p><w:r><w:sym w:font="Symbol" w:char="F061"/></w:r></w:p>')
        b = mutate(a, lambda r: r.find('.//' + W + 'sym').set(W + 'char', 'F062'))
        self.assert_changed(a, b)

    def test_custom_properties_and_bound_xml_payloads_are_preserved(self):
        with ZipFile(io.BytesIO(pack())) as z:
            parts = {n: z.read(n) for n in z.namelist()}
        parts.update({'docProps/custom.xml': b'<properties><property name="ZOTERO_PREF_1">keep</property></properties>',
                      'customXml/item1.xml': b'<bound><value>original</value></bound>',
                      'customXml/_rels/item1.xml.rels': b'<Relationships><Relationship Id="r1" Target="itemProps1.xml"/></Relationships>',
                      'customXml/payload.bin': b'original-payload'})
        original = encode(parts)
        for name in ('docProps/custom.xml', 'customXml/item1.xml',
                     'customXml/_rels/item1.xml.rels', 'customXml/payload.bin'):
            with self.subTest(part=name, mutation='changed'):
                changed = dict(parts)
                changed[name] = (parts[name].replace(b'original', b'changed').replace(b'keep', b'drop')
                                 .replace(b'itemProps1.xml', b'itemProps2.xml'))
                self.assert_changed(original, encode(changed))
            with self.subTest(part=name, mutation='missing'):
                missing = dict(parts); del missing[name]
                self.assert_changed(original, encode(missing))

    def test_custom_xml_prefix_serialization_does_not_change_payload(self):
        with ZipFile(io.BytesIO(pack())) as z:
            parts = {n: z.read(n) for n in z.namelist()}
        parts['customXml/item1.xml'] = b'<a:bound xmlns:a="urn:owned"><a:value>same</a:value></a:bound>'
        original = encode(parts)
        parts['customXml/item1.xml'] = b'<b:bound xmlns:b="urn:owned"><b:value>same</b:value></b:bound>'
        self.assert_same(original, encode(parts))

    def test_unsupported_cell_revision_fails_closed(self):
        a = pack(body=table().replace('<w:tcPr>', '<w:tcPr><w:cellDel w:id="23"/>', 1))
        with self.assertRaisesRegex(sem.SemanticError, 'UNSUPPORTED_REVISION'):
            sem.snapshot(a)

    def test_invalid_or_duplicate_page_policy_is_rejected(self):
        p = policy()
        p['page_cache_fields']['accepted'].append(deepcopy(p['page_cache_fields']['accepted'][0]))
        with self.assertRaisesRegex(sem.SemanticError, 'DUPLICATE_PAGE_POLICY'):
            sem.validate_policy(p)

if __name__ == '__main__':
    unittest.main()
