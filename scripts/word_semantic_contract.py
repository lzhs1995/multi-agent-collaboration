"""Source-bound DOCX endpoint semantics for explicitly opted-in new Word jobs.

No native operations and no serialization write. Both views include table structure;
unknown content remains in the canonical tree rather than being silently dropped.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re
import unicodedata
import xml.etree.ElementTree as ET
from zipfile import ZipFile
import posixpath

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
W15 = "{http://schemas.microsoft.com/office/word/2012/wordml}"
XML = "{http://www.w3.org/XML/1998/namespace}"
STORY = re.compile(r"^word/(document|footnotes|endnotes|header\d*|footer\d*)\.xml$")
CHANGES = {p + "Change": p for p in ("pPr", "rPr", "tblPr", "tblGrid", "trPr", "tcPr", "sectPr")}
TEXT_REVISIONS = {"ins": True, "del": False, "moveTo": True, "moveFrom": False}
UNSUPPORTED_REVISIONS = {
    "cellIns", "cellDel", "cellMerge", "numberingChange",
    "customXmlInsRangeStart", "customXmlInsRangeEnd", "customXmlDelRangeStart",
    "customXmlDelRangeEnd", "customXmlMoveFromRangeStart", "customXmlMoveFromRangeEnd",
    "customXmlMoveToRangeStart", "customXmlMoveToRangeEnd",
}
R_BOOL = {
    "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline",
    "shadow", "emboss", "imprint", "vanish", "webHidden", "rtl", "cs",
    "noProof", "snapToGrid", "specVanish", "oMath",
}
TOGGLES = {"b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike",
           "outline", "shadow", "emboss", "imprint", "vanish"}
P_BOOL = {"keepNext", "keepLines", "pageBreakBefore", "widowControl", "suppressLineNumbers",
          "suppressAutoHyphens", "contextualSpacing", "bidi", "adjustRightInd", "snapToGrid",
          "mirrorIndents", "suppressOverlap", "wordWrap", "overflowPunct", "topLinePunct",
          "autoSpaceDE", "autoSpaceDN"}
# ISO/IEC 29500 defaults reproduced in the Open XML SDK remarks for these
# elements. In particular snapToGrid is enabled for both paragraphs and runs.
P_DEFAULT_TRUE = {"widowControl", "adjustRightInd", "snapToGrid", "wordWrap",
                  "autoSpaceDE", "autoSpaceDN"}
# No proved default is normalized for this setting. Absence stays distinct
# from either explicit value rather than inventing a false equivalence.
P_UNSPECIFIED = {"overflowPunct"}
R_DEFAULT_TRUE = {"snapToGrid"}
PROP_CONTAINERS = {W + n for n in ("pPr", "rPr", "tblPr", "trPr", "tcPr", "sectPr")}
MAX_PACKAGE_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 4096
MODE = "DUAL_VIEW_SEMANTIC_V1"


class SemanticError(RuntimeError):
    pass


def revision_display_state(path):
    """Save-template state; separate from the two rendered endpoint semantics."""
    raw = Path(path).read_bytes() if not isinstance(path, bytes) else path
    with ZipFile(io.BytesIO(raw)) as z:
        root = ET.fromstring(z.read("word/settings.xml")) if "word/settings.xml" in z.namelist() else None
    track = None if root is None else root.find(W + "trackRevisions")
    view = None if root is None else root.find(W + "revisionView")
    markup = None if view is None else view.get(W + "markup")
    if markup is not None:
        need(markup in {"0", "1", "true", "false", "on", "off"}, "INVALID_REVISION_DISPLAY_BOOLEAN")
    return dict(track_revisions=False if track is None else boolean(track),
                markup_hidden=markup in {"0", "false", "off"},
                revision_view_attributes={} if view is None else dict(view.attrib))


def need(ok, why):
    if not ok:
        raise SemanticError(why)


def local(tag):
    return tag[len(W):] if tag.startswith(W) else tag


def boolean(node):
    value = node.get(W + "val", "1")
    need(value in {"1", "true", "on", "0", "false", "off"}, "INVALID_OOXML_BOOLEAN")
    return value in {"1", "true", "on"}


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def validate_policy(policy):
    need(isinstance(policy, dict) and set(policy) == {"mode", "page_cache_fields"},
         "EXACT_SEMANTIC_POLICY_REQUIRED")
    need(policy["mode"] == MODE and isinstance(policy["page_cache_fields"], dict)
         and set(policy["page_cache_fields"]) == {"accepted", "rejected"},
         "EXACT_SEMANTIC_VIEWS_REQUIRED")
    for fields in policy["page_cache_fields"].values():
        need(isinstance(fields, list) and len(fields) <= 32, "BOUNDED_PAGE_POLICY_REQUIRED")
        seen = set()
        for f in fields:
            need(isinstance(f, dict) and set(f) == {"story", "ordinal", "instruction_sha256"},
                 "EXACT_PAGE_POLICY_FIELDS_REQUIRED")
            need(isinstance(f["story"], str) and STORY.fullmatch(f["story"])
                 and re.fullmatch(r"word/(header|footer)\d*\.xml", f["story"]),
                 "PAGE_POLICY_RUNNING_STORY_REQUIRED")
            need(type(f["ordinal"]) is int and 0 <= f["ordinal"] < 4096
                 and isinstance(f["instruction_sha256"], str)
                 and re.fullmatch(r"[0-9a-f]{64}", f["instruction_sha256"]),
                 "PAGE_POLICY_IDENTITY_REQUIRED")
            key = (f["story"], f["ordinal"])
            need(key not in seen, "DUPLICATE_PAGE_POLICY")
            seen.add(key)
    return policy


def _parents(root):
    return {c: p for p in root.iter() for c in p}


def project(root, accept):
    """Resolve only supported revision shapes; retain all nonrevision structures."""
    root = deepcopy(root)
    for e in root.iter():
        need(local(e.tag) not in UNSUPPORTED_REVISIONS, "UNSUPPORTED_REVISION:" + local(e.tag))
    # A paragraph-mark deletion needs paragraph joining, not simple wrapper removal.
    for p in root.iter(W + "p"):
        mark = p.find(W + "pPr/" + W + "rPr")
        if mark is not None:
            need(not any(local(c.tag) in TEXT_REVISIONS for c in mark),
                 "UNSUPPORTED_PARAGRAPH_MARK_REVISION")
    # Range moves need a whole paired-range resolver; do not ignore them.
    need(not any(local(e.tag).startswith(("moveFromRange", "moveToRange"))
                 for e in root.iter()), "UNSUPPORTED_MOVE_RANGE")
    # Row insertion/deletion markers live in trPr, unlike run wrappers.
    for parent in list(root.iter()):
        for row in list(parent):
            if row.tag != W + "tr":
                continue
            pr = row.find(W + "trPr")
            markers = [] if pr is None else [c for c in pr if local(c.tag) in {"ins", "del"}]
            need(len(markers) <= 1, "AMBIGUOUS_ROW_REVISION")
            if markers and (local(markers[0].tag) == "ins") != accept:
                parent.remove(row)
            elif markers:
                pr.remove(markers[0])
    # Property change payloads restore the entire previous property container.
    def resolve_properties(current):
        changes = [c for c in current if local(c.tag) in CHANGES]
        need(len(changes) <= 1, "AMBIGUOUS_PROPERTY_REVISION")
        for e in changes:
            need(current.tag == W + CHANGES[local(e.tag)],
                 "PROPERTY_REVISION_PARENT_MISMATCH")
            need(len(e) == 1 and e[0].tag == current.tag, "PROPERTY_REVISION_PAYLOAD_REQUIRED")
            need(not any(local(c.tag) in CHANGES for c in e[0].iter()),
                 "NESTED_PROPERTY_REVISION_UNSUPPORTED")
        # Resolve independently tracked paragraph-mark/section properties first.
        # Historical payloads are not current content and are never traversed.
        for child in list(current):
            if child not in changes:
                resolve_properties(child)
        if not changes:
            return
        e = changes[0]
        if accept:
            current.remove(e)
            return
        previous = deepcopy(e[0])
        if current.tag == W + "pPr":
            # PPrChange contains PPrBase, excluding these separate properties.
            for retained in (W + "rPr", W + "sectPr"):
                existing = current.find(retained)
                if existing is not None and previous.find(retained) is None:
                    previous.append(deepcopy(existing))
        current.attrib.clear()
        current.attrib.update(previous.attrib)
        current.text = previous.text
        current[:] = list(previous)
    resolve_properties(root)
    # Content wrappers can occur within a kept content wrapper.
    def resolve_children(parent):
        for child in list(parent):
            name = local(child.tag)
            if name in TEXT_REVISIONS:
                keep = TEXT_REVISIONS[name] == accept
                if keep:
                    resolve_children(child)
                    at = list(parent).index(child)
                    for item in list(child):
                        child.remove(item)
                        parent.insert(at, item)
                        at += 1
                parent.remove(child)
            else:
                resolve_children(child)
    resolve_children(root)
    for e in root.iter():
        if e.tag in {W + "delText", W + "delInstrText"}:
            e.tag = W + ("t" if e.tag == W + "delText" else "instrText")
    # A completely deleted table has no rendered rows in that view.
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag == W + "tbl" and not child.findall(W + "tr"):
                parent.remove(child)
    need(not any(local(e.tag) in CHANGES or local(e.tag) in TEXT_REVISIONS
                 for e in root.iter()), "REVISION_RESIDUE_IN_PROJECTION")
    return root


class Package:
    def __init__(self, path, accept):
        raw = Path(path).read_bytes() if not isinstance(path, bytes) else path
        with ZipFile(io.BytesIO(raw)) as z:
            members = z.infolist()
            need(len(members) <= MAX_MEMBERS
                 and sum(m.file_size for m in members) <= MAX_PACKAGE_BYTES,
                 "BOUNDED_DOCX_PACKAGE_REQUIRED")
            names = [m.filename for m in members]
            need(len(names) == len(set(names)), "DUPLICATE_PACKAGE_MEMBER")
            need(all(not n.startswith("/") and ".." not in n.split("/") for n in names),
                 "CANONICAL_PACKAGE_MEMBERS_REQUIRED")
            self.parts = {n: z.read(n) for n in names}
        need("word/document.xml" in self.parts, "WORD_DOCUMENT_PART_REQUIRED")
        self.accept = accept
        self.trees = {n: project(ET.fromstring(b), accept) for n, b in self.parts.items()
                      if n.startswith("word/") and n.endswith(".xml")}
        self.stories = {n: r for n, r in self.trees.items() if STORY.fullmatch(n)}
        need(0 < len(self.stories) <= 64, "BOUNDED_STORY_SET_REQUIRED")
        self.parents = {n: _parents(r) for n, r in self.stories.items()}
        styles = self.trees.get("word/styles.xml", ET.Element(W + "styles"))
        self.styles = {}
        for s in styles.findall(W + "style"):
            sid = s.get(W + "styleId")
            need(sid and sid not in self.styles, "UNIQUE_STYLE_ID_REQUIRED")
            self.styles[sid] = s
        self.default_style = next((s.get(W + "styleId") for s in self.styles.values()
                                   if s.get(W + "type") == "paragraph"
                                   and s.get(W + "default") in {"1", "true", "on"}), None)
        self.default_p = styles.find(W + "docDefaults/" + W + "pPrDefault/" + W + "pPr")
        self.default_r = styles.find(W + "docDefaults/" + W + "rPrDefault/" + W + "rPr")
        self.numbering = self.trees.get("word/numbering.xml", ET.Element(W + "numbering"))
        self.note_ids = {}
        self._bind_notes()
        self.relationships = self._relationships()
        self.page_nodes = set()
        self.page_separators = {}
        self.page_begins = set()
        self.page_observations = []
        self._paragraph_cache = {}
        self._run_cache = {}
        self._mark_runs = {}

    def _relationships(self):
        result = {}
        for name, raw in self.parts.items():
            if not name.endswith(".rels"):
                continue
            tree = ET.fromstring(raw)
            table = {}
            source_dir = posixpath.dirname(posixpath.dirname(name))
            for r in tree:
                rid = r.get("Id")
                need(rid and rid not in table, "UNIQUE_RELATIONSHIP_ID_REQUIRED")
                target = r.get("Target", "")
                if r.get("TargetMode") != "External":
                    target = posixpath.normpath(posixpath.join(source_dir, target))
                table[rid] = (r.get("Type"), target, r.get("TargetMode", "Internal"))
            result[name] = table
        return result

    def _bind_notes(self):
        for kind in ("footnote", "endnote"):
            part = "word/" + kind + "s.xml"
            if part not in self.stories:
                need(not any(e.tag == W + kind + "Reference"
                             for root in self.stories.values() for e in root.iter()),
                     "REFERENCE_WITHOUT_NOTE_PART")
                continue
            root = self.stories[part]
            notes = {}
            for e in list(root):
                if e.tag != W + kind:
                    continue
                value = e.get(W + "id")
                need(value is not None and value not in notes, "UNIQUE_NOTE_ID_REQUIRED")
                notes[value] = e
            mapping = {}
            for name in sorted(self.stories):
                if name == part:
                    continue
                for e in self.stories[name].iter(W + kind + "Reference"):
                    value = e.get(W + "id")
                    need(value in notes and value not in mapping, "BIJECTIVE_NOTE_REFERENCES_REQUIRED")
                    mapping[value] = str(len(mapping) + 1)
            ordinary = {value for value, e in notes.items()
                        if e.get(W + "type") not in {"separator", "continuationSeparator", "continuationNotice"}}
            need(set(mapping) == ordinary, "COMPLETE_NOTE_PAYLOAD_BIJECTION_REQUIRED")
            self.note_ids[kind] = mapping
            special = [e for value, e in notes.items() if value not in ordinary]
            ordered = [notes[value] for value in mapping]
            for e in list(root):
                root.remove(e)
            root.extend(special + ordered)
            self.parents[part] = _parents(root)

    def attrs(self, node, story):
        attrs = {}
        for key, value in node.attrib.items():
            if key.startswith(W) and local(key).startswith("rsid"):
                continue
            if key in {W14 + "paraId", W14 + "textId", MC + "Ignorable", XML + "space"}:
                continue
            if key == W + "dirty" and node in self.page_begins:
                continue
            if key == W + "id" and local(node.tag) in {"footnote", "footnoteReference", "endnote", "endnoteReference"}:
                kind = "footnote" if local(node.tag).startswith("footnote") else "endnote"
                value = self.note_ids.get(kind, {}).get(value, value)
            if key.startswith(R):
                relname = posixpath.join(posixpath.dirname(story), "_rels",
                                        posixpath.basename(story) + ".rels")
                need(value in self.relationships.get(relname, {}), "RELATIONSHIP_TARGET_REQUIRED")
                value = self.relationships[relname][value]
            attrs[key] = value
        if local(node.tag) in R_BOOL | P_BOOL | {"cantSplit", "tblHeader", "bidiVisual"}:
            attrs[W + "val"] = boolean(node)
        return attrs

    def raw_node(self, node, story="word/document.xml"):
        if node is None:
            return None
        children = [self.raw_node(c, story) for c in node]
        if node.tag in PROP_CONTAINERS:
            children.sort(key=canonical_json)
        text = node.text or ""
        if children and not text.strip():
            text = ""
        attrs = self.attrs(node, story)
        if local(node.tag) in R_BOOL | P_BOOL | {"cantSplit", "tblHeader", "bidiVisual"}:
            attrs[W + "val"] = boolean(node)
        return [node.tag, attrs, text, children]

    def chain(self, sid):
        out, seen = [], set()
        while sid:
            need(sid not in seen and sid in self.styles, "STYLE_CHAIN_UNKNOWN_OR_CYCLIC")
            seen.add(sid)
            s = self.styles[sid]
            out.append(s)
            b = s.find(W + "basedOn")
            sid = b.get(W + "val") if b is not None else None
        return list(reversed(out))

    def p_layers(self, p):
        direct = p.find(W + "pPr")
        selector = None if direct is None else direct.find(W + "pStyle")
        sid = selector.get(W + "val") if selector is not None else self.default_style
        return [self.default_p] + [s.find(W + "pPr") for s in self.chain(sid)] + [direct]

    @staticmethod
    def merge_tabs(previous, current):
        """Tabs accumulate by position; clear removes only that inherited stop."""
        need(not current.attrib, "UNKNOWN_TABS_ATTRIBUTE")
        stops = {} if previous is None else {
            int(t.get(W + "pos")): deepcopy(t) for t in previous
        }
        seen = set()
        for tab in current:
            need(tab.tag == W + "tab" and not len(tab)
                 and not (tab.text or "").strip(), "UNKNOWN_TAB_CONTENT")
            need(set(tab.attrib) <= {W + "pos", W + "val", W + "leader"},
                 "UNKNOWN_TAB_ATTRIBUTE")
            raw = tab.get(W + "pos", "").strip()
            need(re.fullmatch(r"[+-]?[0-9]+", raw) is not None,
                 "INTEGER_TAB_POSITION_REQUIRED")
            position = int(raw)
            need(-31680 <= position <= 31680, "WORD_TAB_POSITION_OUT_OF_RANGE")
            need(position not in seen, "AMBIGUOUS_TAB_POSITION")
            seen.add(position)
            kind = tab.get(W + "val")
            need(kind in {"clear", "left", "start", "center", "right", "end",
                          "decimal", "bar", "num"}, "UNKNOWN_TAB_TYPE")
            if kind == "clear":
                stops.pop(position, None)
            else:
                stop = deepcopy(tab)
                stop.set(W + "pos", str(position))
                stops[position] = stop
        merged = ET.Element(W + "tabs")
        merged.extend(stops[p] for p in sorted(stops))
        return merged

    @staticmethod
    def merge_nodes(layers, excluded=()):
        result = {}
        for layer in layers:
            if layer is None:
                continue
            for child in layer:
                name = local(child.tag)
                if name in excluded:
                    continue
                if name == "tabs":
                    result[name] = Package.merge_tabs(result.get(name), child)
                elif name in {"ind", "spacing", "rFonts", "numPr"} and name in result:
                    previous = result[name]
                    previous.attrib.update(child.attrib)
                    for item in child:
                        prior = previous.find(item.tag)
                        if prior is not None:
                            previous.remove(prior)
                        previous.append(deepcopy(item))
                else:
                    result[name] = deepcopy(child)
        return result

    def num_level(self, properties):
        pr = properties.get("numPr")
        if pr is None:
            return None
        number = pr.find(W + "numId")
        need(number is not None, "NUMBERING_ID_REQUIRED")
        number_id = number.get(W + "val")
        if number_id == "0":
            return None
        level_node = pr.find(W + "ilvl")
        level_id = level_node.get(W + "val") if level_node is not None else "0"
        candidates = [n for n in self.numbering.findall(W + "num") if n.get(W + "numId") == number_id]
        need(len(candidates) == 1, "UNIQUE_NUMBERING_INSTANCE_REQUIRED")
        number = candidates[0]
        abstract_node = number.find(W + "abstractNumId")
        need(abstract_node is not None, "ABSTRACT_NUMBERING_REQUIRED")
        candidates = [n for n in self.numbering.findall(W + "abstractNum")
                      if n.get(W + "abstractNumId") == abstract_node.get(W + "val")]
        need(len(candidates) == 1, "UNIQUE_ABSTRACT_NUMBERING_REQUIRED")
        levels = [n for n in candidates[0].findall(W + "lvl") if n.get(W + "ilvl") == level_id]
        overrides = [n for n in number.findall(W + "lvlOverride") if n.get(W + "ilvl") == level_id]
        need(len(overrides) <= 1 and len(levels) == 1, "UNIQUE_NUMBERING_LEVEL_REQUIRED")
        override = overrides[0] if overrides else None
        replacement = None if override is None else override.find(W + "lvl")
        level = replacement if replacement is not None else levels[0]
        start = None if override is None else override.find(W + "startOverride")
        definition = {local(c.tag): self.raw_node(c) for c in level if c.tag != W + "pPr"}
        if start is not None:
            definition["startOverride"] = start.get(W + "val")
        return dict(num_id=number_id, level=level_id, properties=level.find(W + "pPr"),
                    definition=definition)

    @staticmethod
    def indent(node):
        if node is None:
            return {"left": ["twips", 0], "right": ["twips", 0], "first": ["twips", 0]}
        attrs = {local(k): int(v) for k, v in node.attrib.items()}
        need(set(attrs) <= {"left", "right", "start", "end", "leftChars", "rightChars",
             "startChars", "endChars", "firstLine", "hanging", "firstLineChars", "hangingChars"},
             "UNKNOWN_INDENT_ATTRIBUTE")
        def side(a, b):
            for key in (b + "Chars", a + "Chars"):
                if attrs.get(key, 0):
                    return ["chars", attrs[key]]
            return ["twips", attrs.get(b, attrs.get(a, 0))]
        if attrs.get("hangingChars", 0):
            first = ["chars", -attrs["hangingChars"]]
        elif attrs.get("hanging", 0):
            first = ["twips", -attrs["hanging"]]
        elif attrs.get("firstLineChars", 0):
            first = ["chars", attrs["firstLineChars"]]
        else:
            first = ["twips", attrs.get("firstLine", 0)]
        result = {"left": side("left", "start"), "right": side("right", "end"), "first": first}
        # MS-OI29500 2.1.44 proves zero/reset and nonzero inheritance for
        # start/end/firstLine/hanging Chars. Legacy left/right zero aliases
        # with nonzero lengths remain conservatively protected, not proved
        # interchangeable with the absence of those aliases.
        legacy_zero = {name: attrs[name] for name in ("left", "right")
                       if attrs.get(name + "Chars") == 0 and attrs.get(name, 0)}
        if legacy_zero:
            result["legacy_zero_lengths"] = legacy_zero
        return result

    def paragraph_properties(self, p, story):
        cached = self._paragraph_cache.get((id(p), story))
        if cached is not None:
            return cached
        layers = self.p_layers(p)
        direct = layers[-1]
        inherited = self.merge_nodes(layers[:-1], {"pStyle", "rPr", "sectPr", "pPrChange"})
        combined = self.merge_nodes(layers, {"pStyle", "rPr", "sectPr", "pPrChange"})
        number = self.num_level(combined)
        if number is not None:
            inherited = self.merge_nodes([self._container(inherited), number["properties"]],
                                         {"pStyle", "rPr", "sectPr", "numPr"})
        combined = self.merge_nodes([self._container(inherited), direct],
                                    {"pStyle", "rPr", "sectPr", "pPrChange", "numPr"})
        result = {}
        for name, node in combined.items():
            if name == "ind":
                continue
            if name == "tabs" and not len(node):
                continue
            result[name] = boolean(node) if name in P_BOOL else self.raw_node(node, story)
        # Missing alignment defaults to Word's left, not an unknown style.
        result.setdefault("jc", [W + "jc", {W + "val": "left"}, "", []])
        for name in P_BOOL:
            result.setdefault(name, "UNSPECIFIED" if name in P_UNSPECIFIED
                              else name in P_DEFAULT_TRUE)
        result["ind"] = self.indent(combined.get("ind"))
        if number is not None:
            result["numbering"] = {k: number[k] for k in ("num_id", "level", "definition")}
        self._paragraph_cache[(id(p), story)] = result
        return result

    @staticmethod
    def _container(properties):
        e = ET.Element(W + "pPr")
        e.extend(deepcopy(list(properties.values())))
        return e

    def run_properties(self, p, run, character, story):
        ordinary = character and unicodedata.bidirectional(character) not in {"R", "AL", "AN"}
        name = unicodedata.name(character, "") if character else ""
        ordinary = bool(ordinary and (ord(character) < 128 or name.startswith((
            "CJK ", "LATIN ", "GREEK ", "CYRILLIC ", "FULLWIDTH ", "IDEOGRAPHIC ",
            "LEFT ", "RIGHT ", "EM ", "EN ", "HYPHEN", "NO-BREAK ", "NON-BREAKING ",
            "MINUS ", "PLUS ", "MULTIPLICATION ", "DIVISION ", "LESS-THAN ",
            "GREATER-THAN ", "EQUALS ", "PERCENT ", "SOLIDUS", "COMMA", "SPACE",
            "DIGIT ", "FULL STOP", "LOW LINE", "RIGHTWARDS ", "PILCROW "))))
        cache_key = (id(p), id(run), story, ordinary)
        cached = self._run_cache.get(cache_key)
        if cached is not None:
            return cached
        p_layers = self.p_layers(p)
        direct_p = p_layers[-1]
        selector = None if direct_p is None else direct_p.find(W + "pStyle")
        sid = selector.get(W + "val") if selector is not None else self.default_style
        rpr = run.find(W + "rPr")
        rselector = None if rpr is None else rpr.find(W + "rStyle")
        rsid = rselector.get(W + "val") if rselector is not None else None
        style_layers = [s.find(W + "rPr") for s in self.chain(sid)]
        if rsid:
            style_layers += [s.find(W + "rPr") for s in self.chain(rsid)]
        properties = self.merge_nodes([self.default_r] + style_layers + [rpr], {"rStyle", "rPrChange"})
        bools = {k: k in R_DEFAULT_TRUE for k in R_BOOL}
        for index, layer in enumerate([self.default_r] + style_layers + [rpr]):
            if layer is None:
                continue
            is_style = 0 < index <= len(style_layers)
            for e in layer:
                name = local(e.tag)
                if name in R_BOOL:
                    value = boolean(e)
                    if is_style and name in TOGGLES:
                        if value:
                            bools[name] = not bools[name]
                    else:
                        bools[name] = value
        result = {name: self.raw_node(e, story) for name, e in properties.items() if name not in R_BOOL}
        result.update(bools)
        # bCs is inactive for demonstrated ordinary characters; forced complex
        # layout or an unrecognized script remains strictly compared.
        bidi = self.paragraph_properties(p, story).get("bidi", False)
        fonts = properties.get("rFonts")
        forced = bools["cs"] or bools["rtl"] or bidi or (fonts is not None and fonts.get(W + "hint") == "cs")
        if ordinary and not forced:
            result.pop("bCs", None)
        self._run_cache[cache_key] = result
        return result

    def nearest_p(self, node, story):
        parents = self.parents[story]
        while node is not None and node.tag != W + "p":
            node = parents.get(node)
        need(node is not None, "INLINE_FIELD_PARAGRAPH_REQUIRED")
        return node

    def prepare_page_cache(self, declarations, *, require_nonempty=False):
        requested = {(f["story"], f["ordinal"]): f for f in declarations}
        running = set()
        for story, root in self.stories.items():
            relname = posixpath.join(posixpath.dirname(story), "_rels",
                                    posixpath.basename(story) + ".rels")
            for e in root.iter():
                if e.tag in {W + "footerReference", W + "headerReference"}:
                    rel = self.relationships.get(relname, {}).get(e.get(R + "id"))
                    need(rel is not None and rel[2] == "Internal", "RUNNING_STORY_RELATIONSHIP_REQUIRED")
                    running.add(rel[1])
        need(all(story in running for story, _ordinal in requested),
             "DECLARED_PAGE_STORY_NOT_REFERENCED")
        seen = set()
        for story, root in self.stories.items():
            stack, ordinal = [], 0
            parents = self.parents[story]
            for e in root.iter():
                if e.tag == W + "fldChar":
                    kind = e.get(W + "fldCharType")
                    if kind == "begin":
                        if stack:
                            stack[-1]["nested"] = True
                        stack.append(dict(ordinal=ordinal, code="", texts=[], separated=False,
                                          begin=e, separator=None, nested=False, forbidden=False))
                        ordinal += 1
                    elif kind == "separate":
                        need(stack and not stack[-1]["separated"], "UNMATCHED_FIELD_SEPARATOR")
                        stack[-1]["separated"] = True
                        stack[-1]["separator"] = e
                    elif kind == "end":
                        need(stack, "UNMATCHED_FIELD_END")
                        f = stack.pop()
                        declaration = requested.get((story, f["ordinal"]))
                        if declaration is None:
                            continue
                        need(f["separated"] and not f["nested"] and not f["forbidden"],
                             "EXACT_COMPLEX_PAGE_FIELD_REQUIRED")
                        need(re.fullmatch(r"\s*PAGE\s*(?:\\\*\s*(?:MERGEFORMAT|Arabic)\s*)*", f["code"]),
                             "DECLARED_FIELD_IS_NOT_PAGE")
                        need(hashlib.sha256(f["code"].encode()).hexdigest() == declaration["instruction_sha256"],
                             "PAGE_INSTRUCTION_CHANGED")
                        display = "".join(t.text or "" for t in f["texts"])
                        need((not display and not require_nonempty)
                             or re.fullmatch(r"[1-9][0-9]{0,11}", display),
                             "PAGE_CACHE_NOT_POSITIVE_DECIMAL")
                        p = self.nearest_p(f["separator"], story)
                        need(self.nearest_p(f["begin"], story) is p and self.nearest_p(e, story) is p,
                             "PAGE_FIELD_MUST_STAY_IN_ONE_PARAGRAPH")
                        run = parents.get(f["separator"])
                        need(run is not None and run.tag == W + "r", "PAGE_SEPARATOR_RUN_REQUIRED")
                        profile = self.run_properties(p, run, "0", story)
                        for t in f["texts"]:
                            owner = parents.get(t)
                            need(owner is not None and owner.tag == W + "r"
                                 and self.nearest_p(t, story) is p
                                 and self.run_properties(p, owner, "0", story) == profile,
                                 "PAGE_CACHE_FORMAT_CHANGED")
                            self.page_nodes.add(t)
                        self.page_separators[f["separator"]] = profile
                        self.page_begins.add(f["begin"])
                        self.page_observations.append(dict(story=story, ordinal=f["ordinal"],
                              instruction_sha256=declaration["instruction_sha256"], cache=display))
                        seen.add((story, f["ordinal"]))
                    else:
                        raise SemanticError("UNKNOWN_FIELD_BOUNDARY")
                elif e.tag == W + "instrText":
                    need(stack and not stack[-1]["separated"], "FIELD_CODE_OUTSIDE_INSTRUCTION")
                    stack[-1]["code"] += e.text or ""
                elif stack and stack[-1]["separated"]:
                    if e.tag == W + "t":
                        stack[-1]["texts"].append(e)
                    elif e.tag in {W + "tab", W + "br", W + "cr", W + "drawing", W + "object",
                                   W + "sym", W + "fldSimple", W + "footnoteReference", W + "endnoteReference"}:
                        stack[-1]["forbidden"] = True
            need(not stack, "UNCLOSED_COMPLEX_FIELD")
        need(seen == set(requested), "DECLARED_PAGE_FIELD_MISSING")

    def node(self, e, story):
        if e.tag == W + "proofErr":
            return None
        if e.tag == W + "p":
            mark_run = self._mark_runs.get(id(e))
            if mark_run is None:
                mark_run = ET.Element(W + "r")
                mark = e.find(W + "pPr/" + W + "rPr")
                if mark is not None:
                    mark_run.append(deepcopy(mark))
                self._mark_runs[id(e)] = mark_run
            children = [["paragraph_mark", self.run_properties(e, mark_run, "\u00b6", story)]]
            for c in e:
                if c.tag == W + "pPr":
                    section = c.find(W + "sectPr")
                    if section is not None:
                        children.append(self.node(section, story))
                    continue
                item = self.node(c, story)
                if c.tag == W + "r":
                    children.extend(item)
                elif item is not None:
                    children.append(item)
            joined = []
            for item in children:
                if (joined and item[0] == "text" and joined[-1][0] == "text"
                        and item[1] == joined[-1][1]):
                    joined[-1][2] += item[2]
                else:
                    joined.append(item)
            return [e.tag, self.attrs(e, story), self.paragraph_properties(e, story), joined]
        if e.tag == W + "r":
            p = self.nearest_p(e, story)
            out = []
            for c in e:
                if c.tag == W + "rPr":
                    continue
                if c.tag == W + "t":
                    if c in self.page_nodes:
                        continue
                    for character in c.text or "":
                        profile = self.run_properties(p, e, character, story)
                        if out and out[-1][0] == "text" and out[-1][1] == profile:
                            out[-1][2] += character
                        else:
                            out.append(["text", profile, character])
                else:
                    out.append(["inline", self.run_properties(p, e, "0", story), self.raw_node(c, story)])
                    if c in self.page_separators:
                        out.append(["page_cache", self.page_separators[c], "<WORD_COMPUTED_PAGE>"])
            attrs = self.attrs(e, story)
            need(not attrs, "UNSUPPORTED_CONTENT_RUN_ATTRIBUTES")
            return out
        children = []
        if e.tag == W + "tbl":
            selector = e.find(W + "tblPr/" + W + "tblStyle")
            sid = selector.get(W + "val") if selector is not None else next((
                s.get(W + "styleId") for s in self.styles.values()
                if s.get(W + "type") == "table" and s.get(W + "default") in {"1", "true", "on"}), None)
            if sid:
                children.append(["table_style_dependencies", [self.raw_node(s, story) for s in self.chain(sid)]])
        for c in e:
            item = self.node(c, story)
            if item is not None:
                children.append(item)
        if e.tag in PROP_CONTAINERS:
            # Empty property containers are equivalent to absence only after
            # every effective paragraph/run property has been resolved.
            if not e.attrib and not children and not (e.text or "").strip():
                return None
            children.sort(key=canonical_json)
        return [e.tag, self.attrs(e, story), (e.text or "") if not children else "", children]

    def snapshot(self, page_fields, *, require_page_cache=False):
        self.prepare_page_cache(page_fields, require_nonempty=require_page_cache)
        stories = {n: self.node(r, n) for n, r in sorted(self.stories.items())}
        # Nonstory objects remain exact structural or binary evidence.
        # Only style/numbering dependencies have an effective-property resolver.
        # Settings (including compatibility, field updates and layout) and
        # webSettings remain protected. The exact save template controls tracking
        # and markup state, verified separately by the native driver.
        for part in ("word/settings.xml", "word/webSettings.xml"):
            root = self.trees.get(part)
            if root is not None:
                for child in list(root):
                    if child.tag in {W + "rsids", W + "proofState", W + "zoom",
                                     W + "trackRevisions", W + "revisionView",
                                     W14 + "docId", W15 + "docId"}:
                        root.remove(child)
        excluded = {"word/styles.xml", "word/numbering.xml"}
        extras = {n: self.raw_node(r, n) for n, r in sorted(self.trees.items())
                  if n not in self.stories and n not in excluded}
        # Custom properties/XML can carry Zotero preferences and bound content.
        # Retain every other XML payload as well; package indexes and ordinary
        # application/core statistics are outside this endpoint comparison.
        package_metadata = {"[Content_Types].xml", "_rels/.rels",
                            "docProps/core.xml", "docProps/app.xml"}
        extras.update({n: self.raw_node(ET.fromstring(b), n)
                       for n, b in sorted(self.parts.items())
                       if not n.startswith("word/") and n not in package_metadata
                       and n.endswith((".xml", ".rels"))})
        media = {n: hashlib.sha256(b).hexdigest() for n, b in sorted(self.parts.items())
                  if n not in package_metadata and not n.endswith((".xml", ".rels"))}
        return dict(stories=stories, nonstory=extras, media=media)


def snapshot(path, *, accept=True, page_fields=(), require_page_cache=False):
    return Package(path, accept).snapshot(page_fields, require_page_cache=require_page_cache)


def compare(expected, actual, *, expected_accept=True, actual_accept=True, page_fields=(),
            require_page_cache=False):
    expected_package, actual_package = Package(expected, expected_accept), Package(actual, actual_accept)
    before = expected_package.snapshot(page_fields)
    after = actual_package.snapshot(page_fields, require_page_cache=require_page_cache)
    changed = []
    for category in ("stories", "nonstory", "media"):
        old, new = before[category], after[category]
        for name in sorted(set(old) | set(new)):
            if name not in old or name not in new or old[name] != new[name]:
                changed.append(dict(category=category, part=name,
                                    expected_sha256=digest(old[name]) if name in old else None,
                                    actual_sha256=digest(new[name]) if name in new else None))
    return dict(all_pass=not changed, mode=MODE, changed=changed,
                expected_snapshot_sha256=digest(before), actual_snapshot_sha256=digest(after),
                expected_page_cache=expected_package.page_observations,
                actual_page_cache=actual_package.page_observations,
                actual_page_cache_required=require_page_cache,
                scope="ALL_STORIES_EFFECTIVE_FORMAT_FIELDS_ANCHORS_MEDIA_AND_FULL_TABLE_STRUCTURE",
                native_rendering_proven=False, scientific_acceptance=False)


def verify_views(tracked, validation_inputs, policy):
    validate_policy(policy)
    need(isinstance(validation_inputs, dict) and set(validation_inputs) == {"accepted", "rejected"},
         "EXACT_VALIDATION_INPUT_VIEWS_REQUIRED")
    results = {}
    for label in ("accepted", "rejected"):
        expected = validation_inputs[label]
        if isinstance(expected, dict):
            expected = expected["path"]
        results[label] = compare(expected, tracked, expected_accept=(label == "accepted"),
                                 actual_accept=(label == "accepted"),
                                 page_fields=policy["page_cache_fields"][label])
    return dict(all_pass=all(r["all_pass"] for r in results.values()), views=results,
                mode=MODE, raw_header_equality_gate=False,
                raw_headers_must_be_reported_separately=True, native_rendering_proven=False)
