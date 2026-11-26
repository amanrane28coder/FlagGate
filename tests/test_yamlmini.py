#!/usr/bin/env python3
"""Tests for gate/yamlmini.py — the strict YAML subset parser."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gate"))
import yamlmini


def main():
    passed = failed = 0

    def check(name, ok, detail=""):
        nonlocal passed, failed
        if ok:
            print("PASS %s" % name)
            passed += 1
        else:
            print("FAIL %s: %s" % (name, detail))
            failed += 1

    # 1. full proposal shape
    doc = yamlmini.parse("""
# comment line
flag: checkout-cta
environment: development
author: aman
approved: true
reason: "Roll it to 50% # not a comment"
change:
  enabled: true
  value: "Buy now"
  variants:
    - key: control
      weight: 50
    - key: variant_b
      weight: 50
""")
    check("parses a full proposal",
          doc["flag"] == "checkout-cta"
          and doc["approved"] is True
          and doc["reason"] == "Roll it to 50% # not a comment"
          and doc["change"]["variants"][1] == {"key": "variant_b",
                                               "weight": 50},
          "doc: %s" % doc)

    # 2. scalars
    doc = yamlmini.parse("a: 1\nb: 1.5\nc: true\nd: false\ne: null\nf: ~\n"
                         "g: bare words\nh: 'it''s'\n")
    check("scalar types",
          doc == {"a": 1, "b": 1.5, "c": True, "d": False, "e": None,
                  "f": None, "g": "bare words", "h": "it's"},
          "doc: %s" % doc)

    # 3. empty doc / empty values
    check("empty doc is {}", yamlmini.parse("\n# only a comment\n") == {})
    doc = yamlmini.parse("key:\n")
    check("empty value is None", doc == {"key": None}, "doc: %s" % doc)

    # 4. list of scalars
    doc = yamlmini.parse("flags:\n  - a\n  - b\n")
    check("scalar list", doc == {"flags": ["a", "b"]}, "doc: %s" % doc)

    # 5. errors
    bad = [
        ("tabs", "a:\n\tb: 1\n"),
        ("duplicate key", "a: 1\na: 2\n"),
        ("no colon", "just words\n"),
        ("bad key", "a b: 1\n"),
        ("top-level indent", "  a: 1\n"),
        ("top-level list", "- a\n- b\n"),
        ("trailing backslash", 'a: "x\\"\n'),
    ]
    for name, text in bad:
        try:
            yamlmini.parse(text)
            check("rejects %s" % name, False, "parsed without error")
        except ValueError as e:
            check("rejects %s" % name, True, str(e))

    print("test_yamlmini: %d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
