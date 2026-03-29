#!/usr/bin/env python3
"""
RTF Tailwind: A Semantic Style Abstraction Layer for LLM Editing.

Usage:
    python rtftailwind.py decompile input.rtf [--doc out.json] [--styles out_styles.json]
    python rtftailwind.py compile doc.json styles.json [--output out.rtf]
"""

import json
import sys
import os
import re
import argparse
from collections import Counter
from copy import deepcopy


# ═══════════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════════

CP1252_EXTRAS = {
    0x80: 0x20AC, 0x82: 0x201A, 0x83: 0x0192, 0x84: 0x201E,
    0x85: 0x2026, 0x86: 0x2020, 0x87: 0x2021, 0x88: 0x02C6,
    0x89: 0x2030, 0x8A: 0x0160, 0x8B: 0x2039, 0x8C: 0x0152,
    0x8E: 0x017D, 0x91: 0x2018, 0x92: 0x2019, 0x93: 0x201C,
    0x94: 0x201D, 0x95: 0x2022, 0x96: 0x2013, 0x97: 0x2014,
    0x98: 0x02DC, 0x99: 0x2122, 0x9A: 0x0161, 0x9B: 0x203A,
    0x9C: 0x0153, 0x9E: 0x017E, 0x9F: 0x0178,
}

RTF_SPECIAL = {
    'bullet': '\u2022', 'emdash': '\u2014', 'endash': '\u2013',
    'lquote': '\u2018', 'rquote': '\u2019',
    'ldblquote': '\u201c', 'rdblquote': '\u201d',
    'tab': '\t', 'line': '\n',
    'emspace': '\u2003', 'enspace': '\u2002',
}

PARA_CONTROLS = {
    'ql', 'qc', 'qr', 'qj',
    'li', 'ri', 'fi', 'lin', 'rin',
    'sb', 'sa', 'sl', 'slmult',
    'tx', 'tqr', 'tqc', 'tql', 'tqdec',
    'brdrb', 'brdrt', 'brdrl', 'brdrr', 'brdrs', 'brdrw', 'brsp',
    'brdrth', 'brdrthtnmg', 'brdrtnthsg',
    'ls', 'ilvl',
    'keep', 'keepn',
    's', 'outlinelevel',
    'ltrpar', 'rtlpar',
    'widctlpar', 'nowidctlpar', 'wrapdefault',
    'aspalpha', 'aspnum', 'faauto', 'adjustright',
    'itap', 'contextualspace', 'pararsid',
    'jclisttab',
}

CHAR_CONTROLS = {
    'f', 'fs', 'b', 'i', 'ul', 'ulnone',
    'scaps', 'caps', 'super', 'sub', 'nosupersub',
    'strike', 'cf', 'chcbpat', 'highlight', 'chshdng', 'chcfpat',
}

IGNORE_CONTROLS = {
    'rtlch', 'ltrch', 'fcs', 'af', 'afs', 'alang',
    'ab', 'ai', 'aul', 'loch', 'hich', 'dbch',
    'lang', 'langfe', 'langnp', 'langfenp',
    'insrsid', 'charrsid', 'cgrid', 'cs',
    'sectd', 'ltrsect', 'sectlinegrid', 'sectdefaultcl',
    'sectrsid', 'sftnbj', 'linex', 'endnhere',
    'fbidi', 'fprq', 'fcharset',
    'snext', 'sbasedon', 'slink', 'spriority',
    'sqformat', 'ssemihidden', 'sunhideused', 'styrsid',
    'additive', 'lsdlocked', 'noqfpromote',
}


def cp1252_chr(byte_val):
    """Convert a Windows-1252 byte value to a Unicode character."""
    if byte_val in CP1252_EXTRAS:
        return chr(CP1252_EXTRAS[byte_val])
    return chr(byte_val)


# ═══════════════════════════════════════════════════════════════════════════════
# Tokenizer
# ═══════════════════════════════════════════════════════════════════════════════

def tokenize(raw):
    """Tokenize raw RTF string into (type, val1, val2) tuples."""
    tokens = []
    i = 0
    n = len(raw)

    while i < n:
        c = raw[i]

        if c == '{':
            tokens.append(('open', None, None))
            i += 1
        elif c == '}':
            tokens.append(('close', None, None))
            i += 1
        elif c == '\\':
            i += 1
            if i >= n:
                break
            c2 = raw[i]

            if c2 == "'":
                if i + 2 < n:
                    try:
                        val = int(raw[i + 1:i + 3], 16)
                        tokens.append(('hex', val, None))
                    except ValueError:
                        tokens.append(('text', "\\'" + raw[i + 1:i + 3], None))
                    i += 3
                else:
                    i += 1
            elif c2 in '{}\\':
                tokens.append(('text', c2, None))
                i += 1
            elif c2 == '~':
                tokens.append(('text', '\u00a0', None))
                i += 1
            elif c2 == '-':
                tokens.append(('text', '\u00ad', None))
                i += 1
            elif c2 == '_':
                tokens.append(('text', '\u2011', None))
                i += 1
            elif c2 == '*':
                tokens.append(('ignorable', None, None))
                i += 1
            elif c2 in '\r\n':
                tokens.append(('ctrl', 'par', None))
                i += 1
                if i < n and raw[i] == '\n':
                    i += 1
            elif c2.isalpha():
                j = i
                while j < n and raw[j].isalpha():
                    j += 1
                word = raw[i:j]
                param = None
                if j < n and (raw[j] == '-' or raw[j].isdigit()):
                    k = j
                    if raw[j] == '-':
                        k += 1
                    while k < n and raw[k].isdigit():
                        k += 1
                    try:
                        param = int(raw[j:k])
                    except ValueError:
                        pass
                    j = k
                if j < n and raw[j] == ' ':
                    j += 1
                tokens.append(('ctrl', word, param))
                i = j
            else:
                tokens.append(('ctrl', c2, None))
                i += 1
        elif c in '\r\n':
            i += 1
        else:
            j = i
            while j < n and raw[j] not in '{}\\\r\n':
                j += 1
            tokens.append(('text', raw[i:j], None))
            i = j

    return tokens


# ═══════════════════════════════════════════════════════════════════════════════
# Tree Builder
# ═══════════════════════════════════════════════════════════════════════════════

class Group:
    __slots__ = ('children', 'ignorable')
    def __init__(self):
        self.children = []
        self.ignorable = False

class CW:  # ControlWord
    __slots__ = ('word', 'param')
    def __init__(self, word, param=None):
        self.word = word
        self.param = param

class Text:
    __slots__ = ('text',)
    def __init__(self, text):
        self.text = text

class Hex:
    __slots__ = ('value',)
    def __init__(self, value):
        self.value = value


def build_tree(tokens):
    """Build a tree of Group/CW/Text/Hex nodes."""
    root = Group()
    stack = [root]
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t[0] == 'open':
            g = Group()
            if i + 1 < len(tokens) and tokens[i + 1][0] == 'ignorable':
                g.ignorable = True
                i += 1
            stack[-1].children.append(g)
            stack.append(g)
        elif t[0] == 'close':
            if len(stack) > 1:
                stack.pop()
        elif t[0] == 'ctrl':
            stack[-1].children.append(CW(t[1], t[2]))
        elif t[0] == 'text':
            if t[1]:
                stack[-1].children.append(Text(t[1]))
        elif t[0] == 'hex':
            stack[-1].children.append(Hex(t[1]))
        i += 1
    return root


# ═══════════════════════════════════════════════════════════════════════════════
# Raw String Helpers (for preamble extraction)
# ═══════════════════════════════════════════════════════════════════════════════

def find_matching_brace(raw, start):
    """Given position of '{', find its matching '}'."""
    depth = 0
    i = start
    while i < len(raw):
        c = raw[i]
        if c == '\\':
            i += 2
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def find_headerr_bounds(raw):
    """Find the byte positions of {\\headerr ...} group."""
    pat = re.compile(r'\{\\headerr\s')
    m = pat.search(raw)
    if not m:
        return None, None
    start = m.start()
    end = find_matching_brace(raw, start)
    return start, end


def find_body_pard(raw, search_from):
    """Find first \\pard at brace depth 0 (relative to search_from)."""
    depth = 0
    i = search_from
    n = len(raw)
    while i < n:
        c = raw[i]
        if c == '{':
            depth += 1
            i += 1
        elif c == '}':
            depth -= 1
            if depth < 0:
                return -1
            i += 1
        elif c == '\\':
            j = i + 1
            while j < n and raw[j].isalpha():
                j += 1
            word = raw[i + 1:j]
            if word == 'pard' and depth == 0:
                return i
            if j < n and (raw[j] == '-' or raw[j].isdigit()):
                while j < n and (raw[j].isdigit() or raw[j] == '-'):
                    j += 1
            if j < n and raw[j] == ' ':
                j += 1
            i = j
        else:
            i += 1
    return -1


# ═══════════════════════════════════════════════════════════════════════════════
# Font & Color Table Parsing
# ═══════════════════════════════════════════════════════════════════════════════

def _first_ctrl(group):
    """Get the first control word name in a group."""
    for c in group.children:
        if isinstance(c, CW):
            return c.word
    return None


def parse_font_table(group):
    """Extract {font_id: font_name} from a fonttbl group."""
    fonts = {}
    for child in group.children:
        if isinstance(child, Group):
            fid = None
            name_parts = []
            for item in child.children:
                if isinstance(item, CW) and item.word == 'f' and item.param is not None:
                    if fid is None:
                        fid = item.param
                elif isinstance(item, Text):
                    name_parts.append(item.text)
                elif isinstance(item, Group):
                    pass  # skip \*\falt etc.
            if fid is not None and name_parts:
                name = ''.join(name_parts).rstrip(';').strip()
                if name:
                    fonts[fid] = name
    return fonts


def parse_color_table(group):
    """Extract list of color dicts from colortbl group."""
    colors = []
    cur = {}
    for child in group.children:
        if isinstance(child, CW):
            if child.word == 'red':
                cur['red'] = child.param or 0
            elif child.word == 'green':
                cur['green'] = child.param or 0
            elif child.word == 'blue':
                cur['blue'] = child.param or 0
        elif isinstance(child, Text) and ';' in child.text:
            for _ in range(child.text.count(';')):
                colors.append(cur if cur else None)
                cur = {}
    return colors


# ═══════════════════════════════════════════════════════════════════════════════
# Tree Analysis – find preamble groups, headerr, body start
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_tree(tree):
    """Walk top-level tree children to extract tables, header, and body."""
    # Unwrap: build_tree returns a wrapper Group whose first child is {\rtf1 ...}
    root = tree.children[0] if tree.children and isinstance(tree.children[0], Group) else tree

    font_table = {}
    color_table = []
    headerr_group = None
    body_start_idx = None
    doc_defaults = {'font': 37, 'font_size': 22}  # Word defaults

    found_headerr = False
    skip_groups = {
        'fonttbl', 'colortbl', 'stylesheet', 'info', 'mmathPr',
        'listtable', 'listoverridetable', 'pgptbl', 'rsidtbl',
        'xmlnstbl', 'wgrffmtfilter', 'ftnsep', 'ftnsepc',
        'aftnsep', 'aftnsepc', 'generator',
    }

    for i, child in enumerate(root.children):
        if isinstance(child, Group):
            fc = _first_ctrl(child)

            if fc == 'fonttbl':
                font_table = parse_font_table(child)
                continue
            if fc == 'colortbl':
                color_table = parse_color_table(child)
                continue
            if child.ignorable and fc in skip_groups:
                continue
            if child.ignorable and fc == 'defchp':
                for sub in child.children:
                    if isinstance(sub, CW):
                        if sub.word == 'f' and sub.param is not None:
                            doc_defaults['font'] = sub.param
                        elif sub.word == 'fs' and sub.param is not None:
                            doc_defaults['font_size'] = sub.param
                continue
            if child.ignorable and fc == 'pnseclvl':
                continue
            if fc in ('stylesheet', 'info', 'mmathPr'):
                continue
            if fc == 'headerr':
                headerr_group = child
                found_headerr = True
                continue

        elif isinstance(child, CW):
            if child.word == 'pard' and found_headerr:
                body_start_idx = i
                break

    body_children = root.children[body_start_idx:] if body_start_idx else []
    return font_table, color_table, headerr_group, body_children, doc_defaults


# ═══════════════════════════════════════════════════════════════════════════════
# Content Walker – extract paragraphs with text runs and formatting
# ═══════════════════════════════════════════════════════════════════════════════

def _default_char(doc_defaults):
    return {
        'f': doc_defaults.get('font', 37),
        'fs': doc_defaults.get('font_size', 22),
        'b': False, 'i': False, 'ul': False,
        'scaps': False, 'caps': False,
        'super': False, 'sub': False,
        'strike': False, 'cf': 0,
        'chcbpat': None, 'chshdng': None, 'chcfpat': None,
    }


def _apply_char(state, word, param):
    """Apply a character formatting control word to a state dict."""
    if word == 'f':
        state['f'] = param
    elif word == 'fs':
        state['fs'] = param
    elif word == 'b':
        state['b'] = (param is None or param != 0)
    elif word == 'i':
        state['i'] = (param is None or param != 0)
    elif word == 'ul':
        state['ul'] = (param is None or param != 0)
    elif word == 'ulnone':
        state['ul'] = False
    elif word == 'scaps':
        state['scaps'] = (param is None or param != 0)
    elif word == 'caps':
        state['caps'] = (param is None or param != 0)
    elif word == 'super':
        state['super'] = True; state['sub'] = False
    elif word == 'sub':
        state['sub'] = True; state['super'] = False
    elif word == 'nosupersub':
        state['super'] = False; state['sub'] = False
    elif word == 'strike':
        state['strike'] = (param is None or param != 0)
    elif word == 'cf':
        state['cf'] = param or 0
    elif word == 'chcbpat':
        state['chcbpat'] = param
    elif word == 'highlight':
        state['chcbpat'] = param
    elif word == 'chshdng':
        state['chshdng'] = param
    elif word == 'chcfpat':
        state['chcfpat'] = param


class ContentWalker:
    """Walk RTF tree nodes and extract paragraphs with styled text runs."""

    def __init__(self, doc_defaults):
        self.doc_defaults = doc_defaults
        self.paragraphs = []
        self.runs = []
        self.char_state = _default_char(doc_defaults)
        self.state_stack = []
        self.par_fmt = []       # raw control word tuples for paragraph formatting
        self.par_props = {}     # structured paragraph properties
        self.in_par_header = True
        self.uc = 1
        self.skip_count = 0

    def walk(self, children):
        self._process(children)
        self._finish_par()

    def _process(self, nodes):
        for node in nodes:
            if isinstance(node, CW):
                self._on_ctrl(node.word, node.param)
            elif isinstance(node, Text):
                self._on_text(node.text)
            elif isinstance(node, Hex):
                self._on_text(cp1252_chr(node.value))
            elif isinstance(node, Group):
                self._on_group(node)

    # ── control words ────────────────────────────────────────────────────

    def _on_ctrl(self, word, param):
        if word == 'pard':
            self._finish_par()
            self.par_fmt = [('pard', None)]
            self.par_props = {}
            self.char_state = _default_char(self.doc_defaults)
            self.in_par_header = True
            return
        if word == 'plain':
            self.char_state = _default_char(self.doc_defaults)
            if self.in_par_header:
                self.par_fmt.append(('plain', None))
            return
        if word == 'par':
            self._finish_par()
            return
        if word == 'uc':
            self.uc = param if param is not None else 1
            return
        if word == 'u':
            if param is not None:
                cp = param if param >= 0 else param + 65536
                self._on_text(chr(cp))
                self.skip_count = self.uc
            return
        if word in RTF_SPECIAL:
            self._on_text(RTF_SPECIAL[word])
            return

        # Paragraph-level
        if self.in_par_header and word in PARA_CONTROLS:
            self.par_fmt.append((word, param))
            self._apply_par(word, param)
            return

        # Character-level
        if word in CHAR_CONTROLS:
            _apply_char(self.char_state, word, param)
            if self.in_par_header:
                self.par_fmt.append((word, param))
            return

        # Boilerplate / unknown – store in par header if still collecting
        if self.in_par_header and word not in IGNORE_CONTROLS:
            self.par_fmt.append((word, param))

    def _apply_par(self, w, p):
        """Extract structured paragraph properties."""
        if w == 'ql':   self.par_props['alignment'] = 'left'
        elif w == 'qc': self.par_props['alignment'] = 'center'
        elif w == 'qr': self.par_props['alignment'] = 'right'
        elif w == 'qj': self.par_props['alignment'] = 'justify'
        elif w == 'li': self.par_props['left_indent'] = p or 0
        elif w == 'ri': self.par_props['right_indent'] = p or 0
        elif w == 'fi': self.par_props['first_indent'] = p or 0
        elif w == 'sb': self.par_props['space_before'] = p or 0
        elif w == 'sa': self.par_props['space_after'] = p or 0
        elif w == 's':  self.par_props['style_num'] = p
        elif w == 'ls': self.par_props['list_id'] = p
        elif w == 'keep':  self.par_props['keep'] = True
        elif w == 'keepn': self.par_props['keepn'] = True
        elif w == 'outlinelevel': self.par_props['outline_level'] = p
        elif w == 'brdrb': self.par_props['border_bottom'] = True
        elif w == 'brdrt': self.par_props['border_top'] = True

    # ── text ─────────────────────────────────────────────────────────────

    def _on_text(self, text):
        if not text:
            return
        if self.skip_count > 0:
            skip = min(self.skip_count, len(text))
            text = text[skip:]
            self.skip_count -= skip
            if not text:
                return
        self.in_par_header = False
        self.runs.append({
            'text': text,
            '_state': dict(self.char_state),
        })

    # ── groups ───────────────────────────────────────────────────────────

    def _on_group(self, group):
        fc = _first_ctrl(group)

        # Skip preamble / structural groups
        if group.ignorable:
            skip = {'fldinst', 'datafield', 'pnseclvl', 'pgptbl', 'rsidtbl',
                    'xmlnstbl', 'wgrffmtfilter', 'ftnsep', 'ftnsepc',
                    'aftnsep', 'aftnsepc', 'fonttbl', 'colortbl', 'stylesheet',
                    'listtable', 'listoverridetable', 'info', 'mmathPr',
                    'generator', 'defchp', 'defpap',
                    'themedata', 'colorschememapping', 'latentstyles', 'datastore'}
            if fc in skip:
                return

        if fc in ('fonttbl', 'colortbl', 'stylesheet', 'headerr'):
            return
        if fc == 'listtext':
            return
        if fc in ('field', 'fldedit'):
            self._on_field(group)
            return

        # Regular group – push/pop char state
        self.in_par_header = False
        self.state_stack.append(dict(self.char_state))
        self._process(group.children)
        self.char_state = self.state_stack.pop() if self.state_stack else _default_char(self.doc_defaults)

    # ── fields (hyperlinks) ──────────────────────────────────────────────

    def _on_field(self, group):
        url = None
        vis_text = ''
        vis_state = dict(self.char_state)

        for child in group.children:
            if isinstance(child, Group):
                fc = _first_ctrl(child)
                if child.ignorable and fc == 'fldinst':
                    url = self._extract_url(child)
                elif fc == 'fldrslt':
                    vis_text, vis_state = self._extract_vis(child)

        if vis_text:
            self.in_par_header = False
            run = {'text': vis_text, '_state': vis_state}
            if url:
                run['url'] = url
            self.runs.append(run)

    @staticmethod
    def _extract_url(group):
        parts = []
        def collect(node):
            if isinstance(node, Text):
                parts.append(node.text)
            elif isinstance(node, Group):
                for c in node.children:
                    collect(c)
        for c in group.children:
            collect(c)
        full = ''.join(parts)
        m = re.search(r'HYPERLINK\s+"([^"]+)"', full)
        return m.group(1) if m else None

    def _extract_vis(self, group):
        parts = []
        state = dict(self.char_state)
        def walk(node):
            nonlocal state
            if isinstance(node, Text):
                parts.append(node.text)
            elif isinstance(node, Hex):
                parts.append(cp1252_chr(node.value))
            elif isinstance(node, CW):
                if node.word in RTF_SPECIAL:
                    parts.append(RTF_SPECIAL[node.word])
                elif node.word == 'u' and node.param is not None:
                    cp = node.param if node.param >= 0 else node.param + 65536
                    parts.append(chr(cp))
                elif node.word in CHAR_CONTROLS:
                    _apply_char(state, node.word, node.param)
            elif isinstance(node, Group):
                if not (node.ignorable and _first_ctrl(node) == 'datafield'):
                    for c in node.children:
                        walk(c)
        for c in group.children:
            walk(c)
        return ''.join(parts), state

    # ── paragraph finishing ──────────────────────────────────────────────

    def _finish_par(self):
        if not self.runs:
            return

        # Merge adjacent runs with identical formatting
        merged = []
        for run in self.runs:
            if merged and _state_key(merged[-1]['_state']) == _state_key(run['_state']) and 'url' not in merged[-1] and 'url' not in run:
                merged[-1]['text'] += run['text']
            else:
                merged.append(run)

        raw = self._fmt_ctrls(self.par_fmt)
        ptype = 'bullet' if self.par_props.get('list_id') else 'paragraph'
        para = {'type': ptype, 'runs': merged, '_raw_par_fmt': raw}

        for k in ('alignment', 'left_indent', 'right_indent', 'first_indent',
                   'space_before', 'space_after', 'border_bottom', 'border_top',
                   'list_id', 'style_num', 'outline_level', 'keep', 'keepn'):
            if k in self.par_props:
                para[k] = self.par_props[k]

        self.paragraphs.append(para)
        self.runs = []
        # Don't reset par_fmt/par_props here — \par just ends current paragraph,
        # next paragraph inherits formatting until \pard resets it.
        self.in_par_header = True

    @staticmethod
    def _fmt_ctrls(parts):
        return ''.join(
            f'\\{w}{p}' if p is not None else f'\\{w}'
            for w, p in parts
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Style Classification
# ═══════════════════════════════════════════════════════════════════════════════

def _state_key(state):
    """Hashable key for a char formatting state."""
    return (
        state.get('f'), state.get('fs'),
        bool(state.get('b')), bool(state.get('i')),
        bool(state.get('ul')), bool(state.get('scaps')),
        bool(state.get('caps')), bool(state.get('super')),
        bool(state.get('sub')), bool(state.get('strike')),
        state.get('cf', 0),
        state.get('chcbpat'), state.get('chshdng'), state.get('chcfpat'),
    )


def _state_to_controls(state):
    """Generate minimal RTF control words for a char state."""
    parts = []
    if state.get('f') is not None:
        parts.append(f'\\f{state["f"]}')
    if state.get('fs') is not None:
        parts.append(f'\\fs{state["fs"]}')
    if state.get('b'):
        parts.append('\\b')
    if state.get('i'):
        parts.append('\\i')
    if state.get('ul'):
        parts.append('\\ul')
    if state.get('scaps'):
        parts.append('\\scaps')
    if state.get('caps'):
        parts.append('\\caps')
    if state.get('super'):
        parts.append('\\super')
    if state.get('sub'):
        parts.append('\\sub')
    if state.get('strike'):
        parts.append('\\strike')
    if state.get('cf') and state['cf'] > 0:
        parts.append(f'\\cf{state["cf"]}')
    if state.get('chcbpat') is not None:
        parts.append(f'\\chcbpat{state["chcbpat"]}')
    if state.get('chshdng') is not None:
        parts.append(f'\\chshdng{state["chshdng"]}')
    if state.get('chcfpat') is not None:
        parts.append(f'\\chcfpat{state["chcfpat"]}')
    return ''.join(parts)


def _build_style_name(state, font_table, used_names):
    """Build a descriptive, human-readable style class name."""
    parts = []
    if state.get('b'): parts.append('bold')
    if state.get('i'): parts.append('italic')
    if state.get('ul'): parts.append('underline')
    if state.get('scaps'): parts.append('smallcaps')
    if state.get('caps'): parts.append('allcaps')
    if state.get('super'): parts.append('super')
    if state.get('sub'): parts.append('sub')
    if state.get('strike'): parts.append('strike')

    fid = state.get('f')
    if fid is not None and fid in font_table:
        parts.append(font_table[fid].lower().replace(' ', ''))

    fs = state.get('fs')
    if fs:
        pts = fs / 2
        parts.append(f'{int(pts)}pt' if pts == int(pts) else f'{pts}pt')

    if state.get('cf') and state['cf'] > 0:
        parts.append(f'color{state["cf"]}')
    if state.get('chcbpat') is not None:
        parts.append(f'bg{state["chcbpat"]}')

    name = '-'.join(parts) if parts else 'default'
    base = name
    counter = 2
    while name in used_names:
        name = f'{base}-{counter}'
        counter += 1
    return name


def classify_styles(paragraphs, font_table):
    """
    Assign human-readable style class names to unique char formatting states.
    Returns (style_registry, updated_paragraphs).
    """
    # Count character-weighted frequency of each state
    freq = Counter()
    for para in paragraphs:
        for run in para['runs']:
            key = _state_key(run['_state'])
            freq[key] += len(run['text'])

    ranked = freq.most_common()
    key_to_state = {}
    for para in paragraphs:
        for run in para['runs']:
            key = _state_key(run['_state'])
            if key not in key_to_state:
                key_to_state[key] = dict(run['_state'])

    # Assign names with heuristics
    name_map = {}
    used = set()

    if ranked:
        # Most common -> body-text
        most_key = ranked[0][0]
        name_map[most_key] = 'body-text'
        used.add('body-text')

    # Largest bold font -> heading-name
    max_fs = 0
    heading_key = None
    for key, _ in ranked:
        st = key_to_state[key]
        fs = st.get('fs') or 0
        if fs > max_fs and st.get('b'):
            max_fs = fs
            heading_key = key
    if heading_key and heading_key not in name_map:
        name_map[heading_key] = 'heading-name'
        used.add('heading-name')

    # Bold + small caps + border context -> section-title
    for key, _ in ranked:
        if key in name_map:
            continue
        st = key_to_state[key]
        if st.get('b') and st.get('scaps'):
            name_map[key] = 'section-title' if 'section-title' not in used else _build_style_name(st, font_table, used)
            used.add(name_map[key])
            break

    # Underline + color -> hyperlink
    for key, _ in ranked:
        if key in name_map:
            continue
        st = key_to_state[key]
        if st.get('ul') and st.get('cf') and st['cf'] > 0:
            name_map[key] = 'hyperlink' if 'hyperlink' not in used else _build_style_name(st, font_table, used)
            used.add(name_map[key])
            break

    # Bold variant of body-text -> body-bold
    if ranked:
        body_st = key_to_state.get(ranked[0][0], {})
        for key, _ in ranked:
            if key in name_map:
                continue
            st = key_to_state[key]
            if (st.get('b') and not st.get('scaps')
                    and st.get('f') == body_st.get('f')
                    and st.get('fs') == body_st.get('fs')
                    and not st.get('ul')):
                nm = 'body-bold' if 'body-bold' not in used else _build_style_name(st, font_table, used)
                name_map[key] = nm
                used.add(nm)
                break

    # Name remaining styles descriptively
    for key, _ in ranked:
        if key in name_map:
            continue
        st = key_to_state[key]
        nm = _build_style_name(st, font_table, used)
        name_map[key] = nm
        used.add(nm)

    # Build registry
    registry = {}
    for key, name in name_map.items():
        registry[name] = _state_to_controls(key_to_state[key])

    # Update paragraphs: replace _state with style name
    for para in paragraphs:
        new_runs = []
        for run in para['runs']:
            key = _state_key(run['_state'])
            nr = {'text': run['text'], 'style': name_map[key]}
            if 'url' in run:
                nr['url'] = run['url']
            new_runs.append(nr)
        para['runs'] = new_runs

    return registry, paragraphs


# ═══════════════════════════════════════════════════════════════════════════════
# Decompile: RTF → document.json + styles.json
# ═══════════════════════════════════════════════════════════════════════════════

def decompile(rtf_path):
    """Parse an RTF file and produce (document_dict, styles_dict)."""
    with open(rtf_path, 'rb') as f:
        raw = f.read().decode('latin-1')

    # ── tree parse ───────────────────────────────────────────────────
    tokens = tokenize(raw)
    tree = build_tree(tokens)
    font_table, color_table, headerr_group, body_children, doc_defaults = analyze_tree(tree)

    # ── raw string splits ────────────────────────────────────────────
    hdr_start, hdr_end = find_headerr_bounds(raw)
    if hdr_start is not None and hdr_end is not None:
        body_pos = find_body_pard(raw, hdr_end + 1)
    else:
        body_pos = find_body_pard(raw, 0)

    preamble = raw[:hdr_start] if hdr_start is not None else raw[:body_pos]
    mid_section = raw[hdr_end + 1:body_pos] if hdr_end is not None else ''

    # ── walk header content ──────────────────────────────────────────
    header_paras = []
    if headerr_group:
        walker = ContentWalker(doc_defaults)
        walker.walk(headerr_group.children)
        header_paras = walker.paragraphs

    # ── walk body content ────────────────────────────────────────────
    walker = ContentWalker(doc_defaults)
    walker.walk(body_children)
    body_paras = walker.paragraphs

    # ── classify styles ──────────────────────────────────────────────
    all_paras = header_paras + body_paras
    registry, all_paras = classify_styles(all_paras, font_table)

    n_hdr = len(header_paras)
    header_paras = all_paras[:n_hdr]
    body_paras = all_paras[n_hdr:]

    # ── build output ─────────────────────────────────────────────────
    document = []
    for p in header_paras:
        p['section'] = 'header'
        document.append(p)
    for p in body_paras:
        p['section'] = 'body'
        document.append(p)

    # Remove internal keys from paragraphs for clean output
    for p in document:
        p.pop('_state', None)

    styles = {
        'style_registry': registry,
        'font_table': {str(k): v for k, v in sorted(font_table.items())},
        'color_table': color_table,
        'doc_defaults': doc_defaults,
        'rtf_preamble': preamble,
        'rtf_mid_section': mid_section,
    }

    return {'document': document}, styles


# ═══════════════════════════════════════════════════════════════════════════════
# Compile: document.json + styles.json → RTF
# ═══════════════════════════════════════════════════════════════════════════════

def rtf_escape(text):
    """Escape text for RTF output."""
    result = []
    for ch in text:
        if ch == '{':
            result.append('\\{')
        elif ch == '}':
            result.append('\\}')
        elif ch == '\\':
            result.append('\\\\')
        elif ch == '\t':
            result.append('\\tab ')
        elif ch == '\n':
            result.append('\\line ')
        elif ord(ch) > 127:
            code = ord(ch)
            if code <= 255:
                result.append(f"\\'{code:02x}")
            else:
                signed = code if code <= 32767 else code - 65536
                result.append(f'\\u{signed}?')
        else:
            result.append(ch)
    return ''.join(result)


def compile_paragraph(para, styles):
    """Compile one paragraph dict to RTF string."""
    parts = []

    # Paragraph formatting
    raw_fmt = para.get('_raw_par_fmt', '\\pard\\plain \\ltrpar\\ql ')
    parts.append(raw_fmt)
    parts.append(' ')

    # Runs
    for run in para['runs']:
        style_name = run['style']
        controls = styles['style_registry'].get(style_name, '')
        text = rtf_escape(run['text'])
        # Space after controls is consumed as RTF delimiter, not part of text
        sep = ' '

        if 'url' in run:
            url = run['url']
            parts.append('{\\field{\\*\\fldinst HYPERLINK "')
            parts.append(url)
            parts.append('"}{\\fldrslt {\\plain')
            parts.append(controls)
            parts.append(sep)
            parts.append(text)
            parts.append('}}}')
        else:
            parts.append('{\\plain')
            parts.append(controls)
            parts.append(sep)
            parts.append(text)
            parts.append('}')

    parts.append('\\par\n')
    return ''.join(parts)


def compile_rtf(doc, styles):
    """Compile document + styles back to a complete RTF string."""
    parts = []

    # Preamble
    parts.append(styles['rtf_preamble'])

    # Header paragraphs
    header_paras = [p for p in doc['document'] if p.get('section') == 'header']
    if header_paras:
        parts.append('{\\headerr \\ltrpar\n')
        for para in header_paras:
            parts.append(compile_paragraph(para, styles))
        parts.append('}\n')

    # Mid-section
    parts.append(styles['rtf_mid_section'])

    # Body paragraphs
    body_paras = [p for p in doc['document'] if p.get('section') != 'header']
    for para in body_paras:
        parts.append(compile_paragraph(para, styles))

    # Close document
    parts.append('}')

    return ''.join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# Edit workflow – LLM-friendly view and apply
# ═══════════════════════════════════════════════════════════════════════════════

def make_edit_view(doc, styles):
    """
    Build a compact, LLM-friendly JSON view of the document.
    Strips _raw_par_fmt (compiler internals) and includes a style legend.
    """
    # Style legend: name -> human description
    font_table = {int(k): v for k, v in styles.get('font_table', {}).items()}
    legend = {}
    for name, ctrls in styles['style_registry'].items():
        legend[name] = _describe_style(ctrls, font_table)

    # Clean paragraphs — compact format
    clean_paras = []
    for p in doc['document']:
        cp = {}
        cp['type'] = p['type']
        cp['section'] = p.get('section', 'body')
        # Only include non-default paragraph properties
        if p.get('alignment') and p['alignment'] != 'left':
            cp['alignment'] = p['alignment']
        if p.get('list_id'):
            cp['list_id'] = p['list_id']
        if p.get('border_bottom'):
            cp['border_bottom'] = True

        # Compact runs: if all runs share the same style, use shorthand
        runs = p.get('runs', [])
        styles_used = set(r['style'] for r in runs)
        if len(styles_used) == 1 and not any('url' in r for r in runs):
            # Single-style paragraph: use compact text + style
            cp['style'] = runs[0]['style']
            cp['text'] = ''.join(r['text'] for r in runs)
        else:
            # Multi-style: use runs array
            cp['runs'] = []
            for r in runs:
                cr = {'text': r['text'], 'style': r['style']}
                if 'url' in r:
                    cr['url'] = r['url']
                cp['runs'].append(cr)

        clean_paras.append(cp)

    # Page estimate from RTF metadata + character count
    preamble = styles.get('rtf_preamble', '')
    page_info = _estimate_pages(preamble, clean_paras)

    result = {
        'page_info': page_info,
        'style_legend': legend,
        'available_styles': list(styles['style_registry'].keys()),
        'document': clean_paras,
    }
    return result


def _estimate_pages(preamble, paras):
    """Extract page metadata and estimate page count."""
    info = {}
    for kw, label in [('nofpages', 'pages_original'), ('nofwords', 'words_original')]:
        needle = '\\' + kw
        idx = preamble.find(needle)
        if idx >= 0:
            rest = preamble[idx + len(needle):]
            nums = ''
            for c in rest:
                if c.isdigit():
                    nums += c
                else:
                    break
            if nums:
                info[label] = int(nums)

    # Count current words/chars
    total_chars = 0
    total_words = 0
    for p in paras:
        if 'text' in p:
            total_chars += len(p['text'])
            total_words += len(p['text'].split())
        elif 'runs' in p:
            text = ''.join(r['text'] for r in p['runs'])
            total_chars += len(text)
            total_words += len(text.split())

    info['words_current'] = total_words
    info['chars_current'] = total_chars

    # Estimate pages: use original chars-per-page ratio if available
    orig_pages = info.get('pages_original', 0)
    orig_words = info.get('words_original', 0)
    if orig_pages and orig_words:
        words_per_page = orig_words / orig_pages
        info['pages_estimate'] = round(total_words / words_per_page, 1)
    else:
        # Fallback: ~400 words per page for a dense CV
        info['pages_estimate'] = round(total_words / 400, 1)

    return info


def _describe_style(ctrls, font_table):
    """Turn raw RTF controls into a readable description like 'Garamond 10.5pt, bold'."""
    parts = []
    font_id = None
    m = re.search(r'\\f(\d+)', ctrls)
    if m:
        font_id = int(m.group(1))
        parts.append(font_table.get(font_id, f'font{font_id}'))
    m = re.search(r'\\fs(\d+)', ctrls)
    if m:
        pts = int(m.group(1)) / 2
        parts.append(f'{int(pts)}pt' if pts == int(pts) else f'{pts}pt')
    if '\\b' in ctrls and '\\b0' not in ctrls:
        parts.append('bold')
    if '\\i' in ctrls and '\\i0' not in ctrls:
        parts.append('italic')
    if '\\ul' in ctrls and '\\ulnone' not in ctrls:
        parts.append('underline')
    if '\\scaps' in ctrls:
        parts.append('small-caps')
    if '\\super' in ctrls:
        parts.append('superscript')
    if '\\cf' in ctrls:
        parts.append('colored')
    return ', '.join(parts) if parts else 'default'


def apply_edits(edited_view, original_doc):
    """
    Merge an edited LLM view back into the full document.
    - Expands compact single-style paragraphs (text+style) into runs
    - Restores _raw_par_fmt from originals by matching (type, section, alignment, list_id)
    """
    # Build lookup: (type, section, alignment, list_id) -> _raw_par_fmt
    orig_fmts = {}
    for p in original_doc['document']:
        key = (p.get('type'), p.get('section'), p.get('alignment'), p.get('list_id'))
        if p.get('_raw_par_fmt') and key not in orig_fmts:
            orig_fmts[key] = p['_raw_par_fmt']
    orig_by_idx = [p.get('_raw_par_fmt', '') for p in original_doc['document']]

    merged = []
    for i, ep in enumerate(edited_view['document']):
        mp = dict(ep)

        # Expand compact format: {text, style} -> {runs: [{text, style}]}
        if 'text' in mp and 'style' in mp and 'runs' not in mp:
            mp['runs'] = [{'text': mp.pop('text'), 'style': mp.pop('style')}]
        elif 'runs' not in mp:
            mp['runs'] = []

        # Restore _raw_par_fmt
        if '_raw_par_fmt' not in mp or not mp['_raw_par_fmt']:
            key = (mp.get('type'), mp.get('section'), mp.get('alignment'), mp.get('list_id'))
            if key in orig_fmts:
                mp['_raw_par_fmt'] = orig_fmts[key]
            elif i < len(orig_by_idx):
                mp['_raw_par_fmt'] = orig_by_idx[i]
            else:
                mp['_raw_par_fmt'] = orig_by_idx[-1] if orig_by_idx else '\\pard\\plain \\ltrpar\\ql '

        merged.append(mp)

    return {'document': merged}


def _handle_edit(args):
    """Handle the 'edit' subcommand."""
    inp = args.input

    # Determine if input is RTF or already-decompiled JSON
    if inp.lower().endswith('.rtf'):
        doc, styles = decompile(inp)
        base = os.path.splitext(inp)[0]
        # Save full decompiled files (needed for compile later)
        doc_path = base + '.json'
        sty_path = base + '_styles.json'
        with open(doc_path, 'w', encoding='utf-8') as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
        with open(sty_path, 'w', encoding='utf-8') as f:
            json.dump(styles, f, indent=2, ensure_ascii=False)
    else:
        base = os.path.splitext(inp)[0]
        doc_path = inp
        sty_path = base + '_styles.json'
        # Try to strip _edit suffix for styles lookup
        if not os.path.exists(sty_path):
            sty_path = base.replace('_edit', '') + '_styles.json'
        with open(doc_path, 'r', encoding='utf-8') as f:
            doc = json.load(f)
        with open(sty_path, 'r', encoding='utf-8') as f:
            styles = json.load(f)

    if args.apply:
        # Apply mode: merge edited JSON back and compile to RTF
        with open(args.apply, 'r', encoding='utf-8') as f:
            edited = json.load(f)
        merged = apply_edits(edited, doc)
        out_path = args.output or base + '_edited.rtf'
        rtf = compile_rtf(merged, styles)
        with open(out_path, 'wb') as f:
            f.write(rtf.encode('latin-1', errors='replace'))

        # Show page estimate for the edited version
        view = make_edit_view(merged, styles)
        pi = view.get('page_info', {})
        pages_str = ''
        if pi.get('pages_original'):
            pages_str = f', ~{pi["pages_estimate"]} pages (was {pi["pages_original"]})'
        n = len(merged['document'])
        print(f'Compiled: {out_path}')
        print(f'  {n} paragraphs, {pi.get("words_current",0)} words{pages_str}')
    else:
        # View mode: produce LLM-friendly JSON
        view = make_edit_view(doc, styles)
        out_path = args.output or base + '_edit.json'
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(view, f, indent=2, ensure_ascii=False)
        n = len(view['document'])
        s = len(view['available_styles'])
        size_kb = os.path.getsize(out_path) / 1024
        pi = view.get('page_info', {})
        pages_str = ''
        if pi.get('pages_original'):
            pages_str = f', ~{pi["pages_estimate"]} pages (was {pi["pages_original"]})'
        print(f'Edit view: {out_path}')
        print(f'  {n} paragraphs, {s} styles, {pi.get("words_current",0)} words{pages_str}')
        print()
        print(f'Next: edit {out_path}, then apply:')
        print(f'  python rtftailwind.py edit "{doc_path}" --apply "{out_path}"')


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='RTF Tailwind – Semantic Style Abstraction Layer for LLM Editing')
    sub = parser.add_subparsers(dest='command')

    p_dec = sub.add_parser('decompile', help='RTF → document.json + styles.json')
    p_dec.add_argument('input', help='Input RTF file')
    p_dec.add_argument('--doc', default=None, help='Output document JSON path')
    p_dec.add_argument('--styles', default=None, help='Output styles JSON path')

    p_comp = sub.add_parser('compile', help='document.json + styles.json → RTF')
    p_comp.add_argument('doc', help='Document JSON file')
    p_comp.add_argument('styles', help='Styles JSON file')
    p_comp.add_argument('--output', default=None, help='Output RTF path')

    p_edit = sub.add_parser('edit', help='Produce LLM-friendly edit view + apply edits back')
    p_edit.add_argument('input', help='Input RTF file (or already-decompiled .json)')
    p_edit.add_argument('--apply', default=None,
                        help='Apply an edited JSON file back to RTF (instead of producing the view)')
    p_edit.add_argument('--output', default=None, help='Output path')

    args = parser.parse_args()

    if args.command == 'decompile':
        doc, styles = decompile(args.input)

        base = os.path.splitext(args.input)[0]
        doc_path = args.doc or base + '.json'
        sty_path = args.styles or base + '_styles.json'

        with open(doc_path, 'w', encoding='utf-8') as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
        with open(sty_path, 'w', encoding='utf-8') as f:
            json.dump(styles, f, indent=2, ensure_ascii=False)

        print(f'Decompiled: {doc_path}, {sty_path}')
        n_paras = len(doc['document'])
        n_styles = len(styles['style_registry'])
        print(f'  {n_paras} paragraphs, {n_styles} style classes')

    elif args.command == 'compile':
        with open(args.doc, 'r', encoding='utf-8') as f:
            doc = json.load(f)
        with open(args.styles, 'r', encoding='utf-8') as f:
            styles = json.load(f)

        out_path = args.output or os.path.splitext(args.doc)[0] + '_out.rtf'
        rtf = compile_rtf(doc, styles)

        with open(out_path, 'wb') as f:
            f.write(rtf.encode('latin-1', errors='replace'))

        print(f'Compiled: {out_path}')

    elif args.command == 'edit':
        _handle_edit(args)

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
