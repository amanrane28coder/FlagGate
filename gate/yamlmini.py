#!/usr/bin/env python3
"""Minimal strict YAML-subset parser for FlagGate proposals.

Supports only what a proposal needs:
  - `key: value` mappings, nested by indent (2 spaces per level)
  - lists under a key: `- item` and `- key: value` (first key inline)
  - scalars: bare strings, "double-quoted", 'single-quoted', integers,
    floats, true/false, null (~ or an empty value)
  - `#` comments (outside quotes) and blank lines

Anything else — tabs, anchors, flow maps/sequences, multi-line
scalars, bad indentation, duplicate keys — raises ValueError with a
line number. This is deliberately not a YAML parser: proposals are a
fixed schema, and a loud error beats a silent misread. (stdlib has no
YAML parser; adding PyYAML would break the stdlib-only rule.)
"""

import re

_INT = re.compile(r"^-?\d+$")
_FLOAT = re.compile(r"^-?(\d+\.\d*|\.\d+)([eE][-+]?\d+)?$")
_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _strip_comment(line):
    """Remove a `#` comment outside quotes. `#` starts a comment only
    at the start of the line or after a space."""
    in_single = in_double = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_single:
            if ch == "'":
                in_single = False
        elif in_double:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_double = False
        elif ch == "'":
            in_single = True
        elif ch == '"':
            in_double = True
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
        i += 1
    return line


def _unquote_double(body, line_no):
    """Unescape a double-quoted scalar body (\" \\ \n \t only)."""
    out = []
    j = 0
    while j < len(body):
        c = body[j]
        if c == "\\":
            if j + 1 >= len(body):
                raise ValueError("line %d: trailing backslash in string"
                                 % line_no)
            nxt = body[j + 1]
            out.append({"n": "\n", "t": "\t", '"': '"',
                        "\\": "\\"}.get(nxt, nxt))
            j += 2
        else:
            out.append(c)
            j += 1
    return "".join(out)


def _scalar(text, line_no):
    """Parse one scalar value."""
    if text == "" or text == "~" or text == "null":
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return _unquote_double(text[1:-1], line_no)
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1].replace("''", "'")
    if _INT.match(text):
        return int(text)
    if _FLOAT.match(text):
        return float(text)
    return text


def parse(text):
    """Parse YAML-subset text into dicts/lists/scalars."""
    lines = []
    for n, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw:
            raise ValueError("line %d: tabs are not allowed" % n)
        stripped = _strip_comment(raw).rstrip()
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append((n, indent, stripped.strip()))
    if not lines:
        return {}
    if lines[0][1] != 0:
        raise ValueError("line %d: top level must start at indent 0"
                         % lines[0][0])
    node, i = _parse_block(lines, 0, 0)
    if i != len(lines):
        raise ValueError("line %d: unexpected content at indent %d"
                         % (lines[i][0], lines[i][1]))
    if not isinstance(node, dict):
        raise ValueError("a proposal must be a mapping at the top level")
    return node


def _parse_block(lines, i, indent):
    """Parse one block (dict or list) starting at lines[i]."""
    if lines[i][2] == "-" or lines[i][2].startswith("- "):
        return _parse_list(lines, i, indent)
    return _parse_dict(lines, i, indent)


def _parse_dict(lines, i, indent):
    node = {}
    while i < len(lines):
        n, ind, content = lines[i]
        if ind != indent or content == "-" or content.startswith("- "):
            break
        if ":" not in content:
            raise ValueError("line %d: expected 'key: value'" % n)
        key, _, rest = content.partition(":")
        key = key.strip()
        rest = rest.strip()
        if not _KEY.match(key):
            raise ValueError("line %d: bad key '%s'" % (n, key))
        if key in node:
            raise ValueError("line %d: duplicate key '%s'" % (n, key))
        if rest:
            node[key] = _scalar(rest, n)
            i += 1
        else:
            i += 1
            if i < len(lines) and lines[i][1] > indent:
                child, i = _parse_block(lines, i, lines[i][1])
                node[key] = child
            else:
                node[key] = None
    return node, i


def _parse_list(lines, i, indent):
    node = []
    while i < len(lines):
        n, ind, content = lines[i]
        if ind != indent or not (content == "-" or content.startswith("- ")):
            break
        item = content[2:].strip() if content.startswith("- ") else ""
        if not item:
            i += 1
            if i < len(lines) and lines[i][1] > indent:
                child, i = _parse_block(lines, i, lines[i][1])
                node.append(child)
            else:
                node.append(None)
            continue
        # "- key: value" — an inline mapping start. The rest of the
        # mapping lives on the following lines, indented past the dash.
        if ":" in item:
            first, _, rest = item.partition(":")
            if _KEY.match(first.strip()) and rest.strip() != "":
                sub = [(n, ind + 2, item)]
                j = i + 1
                while j < len(lines) and lines[j][1] > ind:
                    sub.append(lines[j])
                    j += 1
                child, _ = _parse_dict(sub, 0, ind + 2)
                node.append(child)
                i = j
                continue
        node.append(_scalar(item, n))
        i += 1
    return node, i
