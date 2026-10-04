"""Parse literal peer-send inputs without treating message contents as commands.

This is an observation parser, not an interpreter. Dynamic expressions remain
unresolved and cannot authorize a consumption claim.
"""
from __future__ import annotations

import ast
import json
import re
import shlex
from pathlib import Path

_TARGET = re.compile(r"(?:surface:\d+|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\Z")
_KINDS = {"submit_task_pack": "task", "submit_completion_callback": "callback",
          "submit_text": "text", "send": "text", "send_key": "text"}

def _js_tokens(source):
    """Keep strings and comments opaque; never search their contents for calls."""
    tokens, pos = [], 0
    while pos < len(source):
        char = source[pos]
        if char.isspace():
            pos += 1
            continue
        if source.startswith('//', pos) or char == '#':
            end = source.find('\n', pos)
            pos = len(source) if end < 0 else end + 1
            continue
        if source.startswith('/*', pos):
            end = source.find('*/', pos + 2)
            pos = len(source) if end < 0 else end + 2
            continue
        if char in "\"'`":
            start, quote = pos, char
            pos += 1
            while pos < len(source):
                if source[pos] == '\\':
                    pos += 2
                elif source[pos] == quote:
                    pos += 1
                    break
                else:
                    pos += 1
            raw = source[start:pos]
            value = None
            try:
                if raw.endswith(quote) and len(raw) > 1:
                    if quote == '"':
                        value = json.loads(raw)
                    elif quote == "'":
                        value = ast.literal_eval(raw)
                    elif '${' not in raw and '\\' not in raw:
                        value = raw[1:-1]
            except (ValueError, SyntaxError):
                pass
            tokens.append(('string', value))
            continue
        match = re.match(r'[A-Za-z_$][\w$]*', source[pos:])
        if match:
            tokens.append(('name', match[0]))
            pos += len(match[0])
        else:
            tokens.append(('punct', char))
            pos += 1
    return tokens


def _js_commands(source):
    tokens = _js_tokens(source)
    found, commands = False, []
    for i, token in enumerate(tokens):
        if token != ('name', 'exec_command') or tokens[i+1:i+2] != [('punct', '(')]:
            continue
        # Only a direct call or the documented tools.exec_command form.
        if i and tokens[i-1] == ('punct', '.') and tokens[max(0, i-2):i] != [('name', 'tools'), ('punct', '.')]:
            continue
        found = True
        if tokens[i+2:i+3] != [('punct', '{')]:
            continue
        nesting, pos, values = ['{'], i+3, {}
        valid = True
        while pos < len(tokens) and nesting:
            kind, value = tokens[pos]
            if len(nesting) == 1 and (pos == i+3 or tokens[pos-1] == ('punct', ',')):
                if kind in ('name', 'string') and value in ('cmd', 'command'):
                    if (tokens[pos+1:pos+2] == [('punct', ':')]
                            and len(tokens) > pos+3 and tokens[pos+2][0] == 'string'
                            and isinstance(tokens[pos+2][1], str)
                            and tokens[pos+3] in (('punct', ','), ('punct', '}'))
                            and value not in values):
                        values[value] = tokens[pos+2][1]
                    else:
                        valid = False
                elif tokens[pos:pos+3] == [('punct', '.')] * 3:
                    valid = False  # spread may override the literal command
            if kind == 'punct':
                if value in ('{', '[', '('):
                    nesting.append(value)
                elif value in ('}', ']', ')'):
                    if not nesting or nesting[-1] != {'}':'{', ']':'[', ')':'('}[value]:
                        valid = False
                        break
                    nesting.pop()
            pos += 1
        if valid and not nesting and tokens[pos:pos+1] == [('punct', ')')] and len(values) == 1:
            commands.extend(values.values())
    return found, commands


def target(value):
    if not isinstance(value, str) or not _TARGET.fullmatch(value):
        return None
    return value if value.startswith("surface:") else value.upper()


def _python_calls(source):
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return []
    result = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else (
            node.func.id if isinstance(node.func, ast.Name) else "")
        if name not in _KINDS:
            continue
        def literal(value):
            try:
                value = ast.literal_eval(value)
                return value if isinstance(value, str) else None
            except (ValueError, TypeError, SyntaxError, RecursionError):
                return None
        values = {k.arg: literal(k.value) for k in node.keywords if k.arg}
        names = (("task_pack_path",) if name == "submit_completion_callback" else
                 ("surface", "text", "task_pack_path", "marker") if name == "submit_task_pack" else
                 ("surface", "text", "marker"))
        for key, value in zip(names, node.args):
            values[key] = literal(value)
        result.append({"kind": _KINDS[name], "surface": target(values.get("surface")),
                       "pack": values.get("task_pack_path"), "text": values.get("text"),
                       "marker": values.get("marker")})
    return result


def _comments(command):
    # Legacy '# target surface:N' annotations apply only outside quoted payloads.
    quote = None
    escape = False
    for i, char in enumerate(command):
        if escape:
            escape = False
        elif char == "\\" and quote != "'":
            escape = True
        elif quote:
            if char == quote:
                quote = None
        elif char in "\"'`":
            quote = char
        elif char == "#" and (i == 0 or command[i-1].isspace()):
            match = re.match(r"#\s*target\s+(surface:\d+)\b", command[i:])
            if match:
                yield match[1]


def delivery_calls(command, depth=0):
    if depth > 8:
        return []
    # Codex JS tool wrappers: decode only literal command arguments as data.
    found_js, commands = _js_commands(command)
    if found_js:
        return [call for value in commands for call in delivery_calls(value, depth+1)]

    # A direct Python invocation (including the body of a shell heredoc).
    direct = _python_calls(command)
    if direct:
        return direct
    heredoc = re.search(r"(^|\n)([^\n]*?)<<\s*['\"]?(\w+)['\"]?[^\n]*\n(.*?)\n\3(?:\n|$)", command, re.S)
    if heredoc:
        try:
            header = shlex.split(heredoc[2])
        except ValueError:
            header = []
        while header and header[0] in ('rtk', 'proxy'):
            header.pop(0)
        body = _python_calls(heredoc[4]) if header and Path(header[0]).name.startswith('python') else []
        return (delivery_calls(command[:heredoc.start()], depth+1) + body
                + delivery_calls(command[heredoc.end():], depth+1))
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []
    segments = [[]]
    for token in tokens:
        if token and all(c in ";&|\n" for c in token):
            segments.append([])
        else:
            segments[-1].append(token)
    result = []
    for args in segments:
        while args and args[0] in ("rtk", "proxy"):
            args = args[1:]
        if not args:
            continue
        executable = Path(args[0]).name
        if executable.startswith("python") and "-c" in args:
            index = args.index("-c")
            if len(args) > index+1:
                result.extend(_python_calls(args[index+1]))
            continue
        if executable.startswith("python") and len(args) > 1:
            args = args[1:]
            while args and args[0] in ("-B", "-u", "-E", "-I", "-s", "-S"):
                args = args[1:]
            if not args:
                continue
            executable = Path(args[0]).name
        if executable not in ("cmux-bridge-toolchain", "cmux_bridge.py", "cmux_bridge",
                              "cmux-bridge", "cmux", "cmux-agent") or len(args) < 2:
            continue
        name = args[1].replace("-", "_")
        if name == "ask" and len(args) >= 3:
            result.append({"kind": "text", "surface": target(args[2])})
            continue
        if name not in _KINDS:
            continue
        values = {}
        pos = 2
        while pos < len(args):
            option, sep, value = args[pos].partition("=")
            if option in ("--surface", "--target", "--target-surface", "--text", "--marker", "--task-pack"):
                if not sep and pos+1 < len(args):
                    pos += 1
                    value = args[pos]
                values[option] = value
            pos += 1
        result.append({"kind": _KINDS[name],
                       "surface": target(values.get("--surface") or values.get("--target") or values.get("--target-surface")),
                       "pack": values.get("--task-pack"), "text": values.get("--text"),
                       "marker": values.get("--marker")})
    if not any(call.get("surface") for call in result):
        result.extend({"kind": "legacy", "surface": ref} for ref in _comments(command))
    return result
