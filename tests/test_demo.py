#!/usr/bin/env python3
"""Tests for demo-app/ — the static page serves and is wired to the
self-hosted env key + flag (live proof Nov 27: alice control, eve
variant_b, 50/50)."""

import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo-app"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


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

    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port),
         "--directory", str(DEMO)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        html = None
        for _ in range(50):
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/" % port, timeout=2) as resp:
                    html = resp.read().decode()
                    status = resp.status
                break
            except Exception:
                time.sleep(0.1)
        check("demo serves 200", html is not None and status == 200,
              "html: %s" % (html or "")[:100])
        if html is not None:
            check("demo points at the self-hosted API",
                  "http://localhost:8002/api/v1/" in html, html[:500])
            check("demo is wired to a real env key + flag",
                  "REPLACE_WITH_ENV_KEY" not in html
                  and "REPLACE_WITH_FLAG_NAME" not in html
                  and "checkout-cta" in html, html[:500])
            check("demo keeps the flip ids",
                  'id="cta"' in html and 'id="status"' in html
                  and 'id="log"' in html, html[:500])
            check("demo uses the scoped flagsmith SDK",
                  "@flagsmith/flagsmith@12.4.0/+esm" in html, html[:500])
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    print("test_demo: %d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
