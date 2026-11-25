"""
YT Spectrum Analyzer -- desktop edition (Windows). Formerly "bladeRF Workbench".

The SAME engine and the SAME screens as the phone app: the desktop program's
sweep engine (workbench_core.py, byte-identical to "bladeRF Workbench.py")
with the phone app's interface -- spectrum, waterfall, markers, presets,
signal list, survey map, Hebrew/English -- in its own window.

How it runs
    1. the engine starts on 127.0.0.1 (never reachable from the network),
    2. the interface opens as an app window (Microsoft Edge --app, which every
       Windows 11 has; Chrome is used if Edge is missing),
    3. closing the window closes the program.

The radio is reached through Nuand's bladeRF.dll exactly as the original
Workbench does -- install the Nuand bladeRF Windows driver once.

    python bladeRF_Workbench_Desktop.py            real radio (simulator if none)
    python bladeRF_Workbench_Desktop.py --sim      simulator only
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser

# Imported here (not only inside the engine) so that the packaged .exe
# carries them: the engine is loaded from data files at run time and the
# packager cannot see what IT imports.
import collections, csv, ctypes, dataclasses, datetime, hashlib, heapq  # noqa: E401,F401
import http.server, inspect, itertools, json, math, platform, queue, re  # noqa: E401,F401
import shutil, socketserver, struct, threading, traceback, typing, uuid  # noqa: E401,F401
import urllib.parse, weakref, zipfile  # noqa: E401,F401
import numpy  # noqa: F401
try:
    import tkinter, tkinter.filedialog  # noqa: E401,F401
except Exception:
    pass

APP = "YT Spectrum Analyzer"
OLD_APP = "bladeRF Workbench"      # data folder of the earlier versions
PORT = 18765          # fixed, so a second start just brings up a window


def here(*parts):
    base = getattr(sys, "_MEIPASS", None)
    if base:                                   # packaged .exe
        return os.path.join(base, *parts)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, *parts)


def engine_dir():
    p = here("engine")
    return p if os.path.isdir(p) else here("android", "app", "src", "main", "python")


def web_dir():
    p = here("webui")
    return p if os.path.isdir(p) else here("android", "app", "src", "main", "assets", "webui")


def data_dir():
    docs = os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "Documents")
    if not os.path.isdir(docs):
        docs = os.path.expanduser("~")
    d = os.path.join(docs, APP)
    old = os.path.join(docs, OLD_APP)
    if not os.path.isdir(d) and os.path.isdir(old):
        # keep the recordings, surveys and presets of the earlier versions
        try:
            os.rename(old, d)
        except OSError:
            d = old
    os.makedirs(d, exist_ok=True)
    return d


def find_browser():
    pf = [os.environ.get(k) for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
    cands = []
    for base in filter(None, pf):
        cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                  os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def open_window(url, profile):
    exe = find_browser()
    if exe:
        try:
            return subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}",
                                     "--window-size=1500,940", "--no-first-run",
                                     "--no-default-browser-check", "--disable-features=Translate"])
        except OSError:
            pass
    webbrowser.open(url)
    return None


def running_version():
    try:
        import json
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/state", timeout=1.5) as r:
            return json.loads(r.read()).get("version", "")
    except Exception:
        return ""


def already_running():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/state", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(prog=APP)
    ap.add_argument("--sim", action="store_true", help="simulator only, do not open the radio")
    ap.add_argument("--lib", default="", help="path to bladeRF.dll if not in the default place")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-window", action="store_true", help="engine only (open the URL yourself)")
    a = ap.parse_args()

    ddir = data_dir()
    url = f"http://127.0.0.1:{a.port}/?desktop=1"
    sys.path.insert(0, engine_dir())
    import mobile_app
    if a.port == PORT and already_running():
        if running_version() == mobile_app.APP_VERSION:
            open_window(url, os.path.join(ddir, ".window"))
            return 0
        # an OLDER copy is still running on the usual port (an old .exe left
        # open): do not show its window -- run this version beside it.
        a.port = PORT + 1
        url = f"http://127.0.0.1:{a.port}/?desktop=1"

    # a windowed .exe has no console: keep a log next to the data
    if getattr(sys, "frozen", False):
        log = open(os.path.join(ddir, "log.txt"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    print(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} start ---", flush=True)

    mobile_app.DESKTOP = True
    mobile_app.start_server(ddir, web_dir(), a.port, a.lib, a.sim)
    print(url, flush=True)
    if a.no_window:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        mobile_app.stop_server()
        return 0

    proc = open_window(url, os.path.join(ddir, ".window"))
    t0 = time.time()
    seen = getattr(mobile_app, "LAST_SEEN", None)
    old_engine = seen is None               # older engine: follow the window only
    if old_engine:
        seen = [0.0]
    try:
        while True:
            time.sleep(1.0)
            idle = time.time() - (seen[0] or t0)
            if proc is not None and proc.poll() is not None and (idle > 4 or old_engine):
                break                       # app window closed
            if not old_engine and idle > (90 if seen[0] == 0 else 20):
                break                       # browser tab closed (no polling)
    except KeyboardInterrupt:
        pass
    print("closing", flush=True)
    mobile_app.stop_server()
    return 0


if __name__ == "__main__":
    sys.exit(main())
