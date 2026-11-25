"""
YT Spectrum Analyzer (formerly bladeRF Workbench Mobile) -- the back end.

The radio, the sweep and every piece of DSP are the DESKTOP program's own
code (workbench_core.py is "bladeRF Workbench.py" copied in unchanged), run
headless on a small virtual Qt. This file only adds what a phone needs:

  * a controller that owns the Source + Worker and holds the settings,
  * frequency-range presets the user defines,
  * a signal list ("what is on the air here") built from the desktop's own
    carrier finder and band plan,
  * a local HTTP server on 127.0.0.1 that the WebView UI talks to.

Runs the same on a PC for testing:  python mobile_app.py --sim --port 8765
"""
from __future__ import annotations

import json
import math
import os
import struct
import sys
import threading
import time
import traceback
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
# The virtual PyQt5/pyqtgraph packages sit next to this file, so the desktop
# module imports exactly as it does on Windows.
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import numpy as np                       # noqa: E402

import workbench_core as wb              # noqa: E402
import survey as sv                      # noqa: E402

# Bands in use in Israel that the desktop band plan does not list yet
# (B5, B32 and NR n28). Added to the RUNNING copy only -- workbench_core.py
# stays byte-identical to the desktop file.
for _b in (("LTE B5 UL", 824e6, 849e6, "850 MHz"),
           ("LTE B5 DL", 869e6, 894e6, "850 MHz"),
           ("LTE B32 SDL", 1452e6, 1496e6, "1500 MHz L-band, downlink only"),
           ("5G NR n28 DL", 758e6, 803e6, "700 MHz")):
    if not any(x[0] == _b[0] for x in wb.BAND_PLAN):
        wb.BAND_PLAN.append(_b)

APP_NAME = "YT Spectrum Analyzer"
APP_VERSION = "1.2"
DESKTOP = False          # set by the Windows launcher (desktop edition)
NATIVE_LOG = [""]        # Android: libbladeRF/libusb messages land in this file
LAST_SEEN = [0.0]        # time of the UI's last request (desktop: close detection)

# The radio's reach. Desktop: 70 MHz - 6 GHz (bladeRF 2.0 micro).
F_MIN, F_MAX = 70e6, 6000e6

DEFAULT_PRESETS = [
    # name_en, name_he, start, stop.  Cellular entries are the DOWNLINKS --
    # what the base stations transmit, i.e. what a survey measures. Bands are
    # the ones Israeli operators use (B28/n28, B5, B8, B32, B3, B1, B7, n78).
    ("Full range", "כל התחום", 70e6, 6000e6),
    ("Cellular 700-960", "סלולר 700-960", 700e6, 960e6),
    ("Cellular 1.4-2.7 GHz", "סלולר 1.4-2.7 GHz", 1400e6, 2700e6),
    ("B28/n28 DL 700", "B28/n28 DL 700", 758e6, 803e6),
    ("B5 DL 850", "B5 DL 850", 869e6, 894e6),
    ("B8 DL 900", "B8 DL 900", 925e6, 960e6),
    ("B32 L-band 1500", "B32 L-band 1500", 1452e6, 1496e6),
    ("B3 DL 1800", "B3 DL 1800", 1805e6, 1880e6),
    ("B1 DL 2100", "B1 DL 2100", 2110e6, 2170e6),
    ("B7 DL 2600", "B7 DL 2600", 2620e6, 2690e6),
    ("5G n78 3.5 GHz", "5G n78 3.5 GHz", 3300e6, 3800e6),
    ("FM broadcast", "רדיו FM", 87.5e6, 108e6),
    ("VHF aviation", "תעופה VHF", 118e6, 137e6),
    ("ISM 433", "ISM 433", 432e6, 436e6),
    ("ISM 868", "ISM 868", 863e6, 870e6),
    ("GPS L1", "GPS L1", 1563e6, 1588e6),
    ("WiFi / BT 2.4 GHz", "WiFi / BT 2.4 GHz", 2400e6, 2483.5e6),
    ("WiFi 5 GHz", "WiFi 5 GHz", 5150e6, 5875e6),
]

TRACE_MODES = ("Live", "Max Hold", "Average", "Min Hold", "Off")
N_TRACES = 4


def _now_stamp():
    return time.strftime("%Y%m%d_%H%M%S")


class Controller:
    def __init__(self, files_dir: str, lib_path: str = "", force_sim=False):
        self.files_dir = os.path.abspath(files_dir)
        os.makedirs(self.files_dir, exist_ok=True)
        self.capture_dir = os.path.join(self.files_dir, "Captures")
        os.makedirs(self.capture_dir, exist_ok=True)
        self.lib_path = lib_path
        self.force_sim = force_sim
        self.usb_fd = -1
        self._lock = threading.RLock()
        self._res = None
        self._seq = 0
        self._msg = ""
        self._msg_t = 0.0
        self._radio_error = ""
        self._lost = False
        self._stalled = ""
        self.src = None
        self.worker = None
        self._wthread = None
        self._sig_cache = (0, None)
        self._save_timer = None
        # location + surveys
        self.surveys = sv.Surveys(self.files_dir)
        self.acc = sv.Accum()
        self.loc = None              # dict(lat, lon, acc, alt, t, src)
        self._sim_extra = 0.0        # simulator-only level change with position
        self.track = None            # dict while a walk is being recorded
        self._bg_run = True
        threading.Thread(target=self._background, daemon=True,
                         name="location-track").start()

        self.settings = {
            "center": 2.44e9, "span": 100e6, "rbw_auto": True, "rbw": 100e3,
            "gain": 30.0, "detector": "Peak", "window": "Gaussian",
            "points": 1201, "max_fs": 0.0, "avg_count": 10,
            "traces": ["Live", "Max Hold", "Off", "Off"],
            "cal_offset": 0.0, "running": True, "port": 0,
            "survey": "", "sim_gps": False,
            "track_interval": 2.0, "track_dist": 0.0,
            # display-only, kept here so it survives a restart
            "ui": {},
        }
        self._load_settings()
        self.presets = self._load_presets()
        self.missions = self._load_missions()

    # ------------------------------------------------------------------ files
    def _settings_path(self):
        return os.path.join(self.files_dir, "settings.json")

    def _presets_path(self):
        return os.path.join(self.files_dir, "presets.json")

    def _load_settings(self):
        try:
            with open(self._settings_path(), encoding="utf-8") as f:
                d = json.load(f)
            for k, v in d.items():
                if k in self.settings:
                    self.settings[k] = v
        except Exception:
            pass

    def save_settings_soon(self):
        # Debounced: a pinch-zoom sends dozens of changes a second.
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(1.0, self._save_settings)
        self._save_timer.daemon = True
        self._save_timer.start()

    def _save_settings(self):
        try:
            tmp = self._settings_path() + ".tmp"
            with self._lock:
                data = json.dumps(self.settings, indent=1)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, self._settings_path())
        except Exception:
            traceback.print_exc()

    def _load_presets(self):
        try:
            with open(self._presets_path(), encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, list):
                return [self._clean_preset(p) for p in d if isinstance(p, dict)]
        except Exception:
            pass
        out = []
        for i, (en, he, a, b) in enumerate(DEFAULT_PRESETS):
            out.append(self._clean_preset({"id": f"d{i}", "name": en,
                                           "name_he": he, "start": a,
                                           "stop": b}))
        return out

    @staticmethod
    def _clean_preset(p):
        a = float(p.get("start", F_MIN))
        b = float(p.get("stop", F_MAX))
        a, b = min(a, b), max(a, b)
        a = max(F_MIN, min(a, F_MAX - 1e3))
        b = max(a + 1e3, min(b, F_MAX))
        q = {"id": str(p.get("id") or f"u{int(time.time()*1000)}"),
             "name": str(p.get("name", "")).strip()[:40] or "Preset",
             "name_he": str(p.get("name_he", "")).strip()[:40],
             "start": a, "stop": b}
        for k in ("rbw", "ref", "gain"):
            v = p.get(k)
            if v not in (None, ""):
                try:
                    q[k] = float(v)
                except (TypeError, ValueError):
                    pass
        return q

    def _save_presets(self):
        tmp = self._presets_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.presets, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self._presets_path())

    # ------------------------------------------------------------------ radio
    def _cfg(self) -> "wb.Config":
        s = self.settings
        modes = list(s["traces"]) + ["Off"] * 6
        max_fs = float(s.get("max_fs") or 0.0)
        if max_fs <= 0:
            max_fs = self._auto_max_fs()
        return wb.Config(
            center=float(s["center"]), span=float(s["span"]),
            rbw=float(s["rbw"]), rbw_auto=bool(s["rbw_auto"]),
            window=str(s["window"]), detector=str(s["detector"]),
            points=int(s["points"]), gain=float(s["gain"]),
            cal_offset=float(s["cal_offset"]) + self._sim_extra, max_fs=max_fs,
            meas_on=True,          # power_mean for band power (bug 35)
            trace_modes=tuple(modes[:6]),
            trace_view=(False,) * 6,
            avg_count=int(s["avg_count"]), fps=15.0,
            continuous=True)

    def _auto_max_fs(self) -> float:
        """USB 2 (most phone ports) cannot carry 40 MS/s of sc16: ~40 MB/s
        is the practical USB 2 ceiling and 10 MS/s is 40 MB/s. On USB 3,
        keep the desktop default."""
        speed = getattr(self, "_usb_speed", None)
        if isinstance(self.src, wb.Simulated) or speed is None:
            return 40e6
        return 40e6 if speed >= 2 else 10e6

    def _read_usb_speed(self):
        self._usb_speed = None
        try:
            lib = self.src.lib
            fn = getattr(lib, "bladerf_device_speed", None)
            if fn is not None:
                fn.restype = wb.c_int
                fn.argtypes = [wb.c_void_p]
                self._usb_speed = int(fn(self.src._dev))
        except Exception:
            self._usb_speed = None

    def set_usb_fd(self, fd: int):
        """Android: the USB permission was granted and Kotlin opened the
        device. libbladeRF (patched) wraps this descriptor instead of
        scanning the bus, which an unrooted phone does not allow."""
        self.usb_fd = int(fd)
        if self.lib_path:
            try:
                lib = wb.load_libbladerf(self.lib_path)
                f = getattr(lib, "bladerf_set_usb_fd", None)
                if f is not None:
                    f.argtypes = [wb.c_int]
                    f.restype = wb.c_int
                    f(self.usb_fd)
            except Exception as exc:
                self._radio_error = str(exc)

    def _fpga_file(self):
        d = os.environ.get("BLADERF_SEARCH_DIR", "")
        p = os.path.join(d, "hostedxA4.rbf") if d else ""
        return p if p and os.path.isfile(p) else ""

    def _lib_warnings_on(self):
        """The engine sets libbladeRF to 'errors only'. On the phone the
        warnings are the useful part ("FPGA bitstream file not found"), so
        let them through to native.log (see start_server)."""
        try:
            lib = wb.load_libbladerf(self.lib_path or None)
            if not getattr(lib, "_mobile_verbosity", False):
                real = lib.bladerf_log_set_verbosity
                lib.bladerf_log_set_verbosity = lambda lvl: real(min(int(lvl), 3))
                lib._mobile_verbosity = True
        except Exception:
            pass

    def _open_source(self, want_radio: bool):
        if want_radio and not self.force_sim:
            if NATIVE_LOG[0]:
                self._lib_warnings_on()
            last = None
            for attempt in range(3):
                try:
                    src = wb.BladeRF(channel=int(self.settings.get("port", 0)) % 2,
                                     lib_path=self.lib_path or None)
                    self._radio_error = ""
                    return src
                except Exception as exc:
                    last = exc
                    # A board that has only just been granted / powered up can
                    # miss the first USB requests (-6 timed out). Try again
                    # before giving up; any other error is final.
                    if "timed out" not in str(exc).lower() and "(-6)" not in str(exc):
                        break
                    time.sleep(1.5)
            if last is not None:
                exc = last
                msg = str(exc)
                if "(-6)" in msg or "timed out" in msg.lower():
                    msg += ("\n\nThe board answered over USB but stopped responding. "
                            "Most often this is POWER: a phone port cannot feed the "
                            "bladeRF 2.0 -- use a powered USB hub / Y-cable or the "
                            "board's DC input. Also try another OTG cable.")
                if "firmware update" in msg.lower() or "(-13)" in msg:
                    msg += ("\n\nThe board's FX3 firmware is older than this FPGA/libbladeRF "
                            "needs. Update it once from a PC: download "
                            "nuand.com/fx3/bladeRF_fw_latest.img and run "
                            "bladeRF-cli -f bladeRF_fw_latest.img, then unplug/replug.")
                if not self._fpga_file() and self.usb_fd >= 0:
                    msg += ("\n\nNo FPGA image (hostedxA4.rbf) on the phone. If the board "
                            "does not load its FPGA from flash, it cannot start: "
                            "Radio -> Load hostedxA4.rbf.")
                self._radio_error = msg
        return wb.Simulated(channel=int(self.settings.get("port", 0)) % 2)

    def start(self, want_radio=True):
        with self._lock:
            self._stop_worker()
            if self.src is not None:
                try:
                    self.src.close()
                except Exception:
                    pass
            self.src = self._open_source(want_radio)
            if isinstance(self.src, wb.BladeRF):
                self._read_usb_speed()
            s = self.settings
            s["gain"] = float(np.clip(s["gain"], self.src.gain_min,
                                      self.src.gain_max))
            reopen = None
            if isinstance(self.src, wb.BladeRF):
                ch = self.src.channel
                reopen = (lambda: wb.BladeRF(channel=ch,
                                             lib_path=self.lib_path or None))
            self.worker = wb.Worker(self.src, self._cfg(), reopen=reopen)
            w = self.worker
            w.ready.connect(self._on_ready)
            w.failed.connect(lambda m: self._message(m))
            w.lost.connect(self._on_lost)
            w.back.connect(self._on_back)
            w.stalled.connect(self._on_stalled)
            self._lost = False
            self._res = None
            self._wthread = threading.Thread(target=w.run, daemon=True,
                                             name="sweep")
            self._wthread.start()
            if not s.get("running", True):
                self._apply_running(False)

    def _stop_worker(self):
        if self.worker is not None:
            self.worker.stop()
        if self._wthread is not None:
            self._wthread.join(4.0)
        self.worker = None
        self._wthread = None

    def shutdown(self):
        self._bg_run = False
        if self.track is not None:
            self.track_stop()
        with self._lock:
            self._stop_worker()
            if self.src is not None:
                try:
                    self.src.close()
                except Exception:
                    pass
                self.src = None
        self._save_settings()

    # worker callbacks (worker thread)
    def _on_ready(self, r):
        with self._lock:
            self._res = r
            self._seq += 1
        self.acc.add(r)

    def _on_lost(self, why):
        self._lost = True
        self._message("bladeRF disconnected - retrying")

    def _on_back(self, src):
        with self._lock:
            self.src = src
            self._lost = False
        self._message("bladeRF reconnected")

    def _on_stalled(self, n, why):
        self._stalled = why if n else ""

    def _message(self, m):
        self._msg = str(m)
        self._msg_t = time.time()

    # ------------------------------------------------------------ settings
    def apply(self, changes: dict):
        s = self.settings
        retune = False
        with self._lock:
            for k, v in changes.items():
                if k == "ui" and isinstance(v, dict):
                    s["ui"].update(v)
                elif k in ("center", "span", "rbw", "gain", "cal_offset",
                           "max_fs"):
                    s[k] = float(v)
                    retune = True
                elif k in ("track_interval", "track_dist"):
                    s[k] = float(max(0.0, min(3600.0, float(v))))
                elif k == "sim_gps":
                    s[k] = bool(v)
                elif k == "survey":
                    s[k] = str(v)
                elif k in ("rbw_auto",):
                    s[k] = bool(v)
                    retune = True
                elif k == "avg_count":
                    s[k] = int(max(1, min(1000, int(v))))
                    retune = True
                elif k == "points":
                    s[k] = int(max(201, min(4001, int(v))))
                    retune = True
                elif k == "detector" and v in wb.DETECTORS:
                    s[k] = v
                    retune = True
                elif k == "window" and v in wb.WINDOWS:
                    s[k] = v
                    retune = True
                elif k == "traces" and isinstance(v, list):
                    t = [m if m in TRACE_MODES else "Off" for m in v][:N_TRACES]
                    s[k] = t + ["Off"] * (N_TRACES - len(t))
                    retune = True
            if "start" in changes and "stop" in changes:
                a, b = float(changes["start"]), float(changes["stop"])
                a, b = min(a, b), max(a, b)
                s["center"], s["span"] = (a + b) / 2, max(b - a, 1e3)
                retune = True
            # keep the window inside what the radio can tune
            lo = getattr(self.src, "freq_min", F_MIN) if self.src else F_MIN
            hi = getattr(self.src, "freq_max", F_MAX) if self.src else F_MAX
            span = float(np.clip(s["span"], 1e3, hi - lo))
            s["span"] = span
            s["center"] = float(np.clip(s["center"], lo + span / 2,
                                        hi - span / 2))
            if self.src is not None:
                s["gain"] = float(np.clip(s["gain"], self.src.gain_min,
                                          self.src.gain_max))
            if retune and self.worker is not None:
                self.worker.apply(self._cfg())
            if "running" in changes:
                self._apply_running(bool(changes["running"]))
        self.save_settings_soon()

    def _apply_running(self, run: bool):
        self.settings["running"] = run
        if self.worker is not None:
            self.worker.apply(replace(self._cfg(), continuous=run))

    def single(self):
        with self._lock:
            self.settings["running"] = False
            if self.worker is not None:
                self.worker.apply(replace(self._cfg(), continuous=False))
                self.worker.request_single()

    def restart(self):
        """Throw away max hold / average and start accumulating again --
        the desktop's Restart key, which calls clear_traces() the same way."""
        with self._lock:
            if self.worker is not None:
                self.worker.clear_traces()
            self._sig_cache = (0, None)

    def set_port(self, port: int):
        with self._lock:
            self.settings["port"] = int(port) % 2
            if self.worker is not None:
                self.worker.request_channel(int(port) % 2)
        self.save_settings_soon()

    # -------------------------------------------------------------- presets
    def preset_op(self, op: str, d: dict):
        with self._lock:
            if op == "add":
                p = self._clean_preset(d)
                p["id"] = f"u{int(time.time() * 1000)}"
                self.presets.append(p)
            elif op == "update":
                for i, p in enumerate(self.presets):
                    if p["id"] == d.get("id"):
                        q = dict(p)
                        q.update(d)
                        self.presets[i] = self._clean_preset(q)
            elif op == "delete":
                self.presets = [p for p in self.presets if p["id"] != d.get("id")]
            elif op == "move":
                i = next((k for k, p in enumerate(self.presets)
                          if p["id"] == d.get("id")), -1)
                j = i + int(d.get("by", 0))
                if 0 <= i < len(self.presets) and 0 <= j < len(self.presets):
                    self.presets[i], self.presets[j] = self.presets[j], self.presets[i]
            elif op == "reset":
                try:
                    os.remove(self._presets_path())
                except OSError:
                    pass
                self.presets = self._load_presets()
            elif op == "apply":
                p = next((p for p in self.presets if p["id"] == d.get("id")), None)
                if p is None:
                    return
                ch = {"start": p["start"], "stop": p["stop"]}
                if "rbw" in p and p["rbw"] > 0:
                    ch.update(rbw=p["rbw"], rbw_auto=False)
                else:
                    ch["rbw_auto"] = True
                if "gain" in p:
                    ch["gain"] = p["gain"]
                self.apply(ch)
                return
            self._save_presets()

    # --------------------------------------------------------------- reads
    def state(self) -> dict:
        s = self.settings
        src = self.src
        with self._lock:
            r = self._res
        msg = self._msg if time.time() - self._msg_t < 6 else ""
        return {
            "app_name": APP_NAME,
            "version": APP_VERSION,
            "core_version": wb.version_line(),
            "settings": s,
            "presets": self.presets,
            "source": {
                "info": getattr(src, "info", "") if src else "",
                "radio": isinstance(src, wb.BladeRF),
                "sim": isinstance(src, wb.Simulated),
                "gain_min": getattr(src, "gain_min", 0),
                "gain_max": getattr(src, "gain_max", 60),
                "freq_min": getattr(src, "freq_min", F_MIN),
                "freq_max": getattr(src, "freq_max", F_MAX),
                "usb_speed": getattr(self, "_usb_speed", None),
                "error": self._radio_error,
                "lost": self._lost,
                "stalled": self._stalled,
                "usb_fd": self.usb_fd,
                "max_fs": self._cfg().max_fs,
                "fpga_file": self._fpga_file(),
                "native_log": _tail(NATIVE_LOG[0]),
            },
            "message": msg,
            "rbw_now": float(r.rbw) if r is not None else 0.0,
            "seq": self._seq,
            "trace_modes": list(TRACE_MODES),
            "detectors": list(wb.DETECTORS),
            "windows": list(wb.WINDOWS),
            "capture_dir": self.capture_dir,
            "location": self.location(),
            "survey": self.settings.get("survey", ""),
            "track": None if self.track is None else {
                k: self.track[k] for k in ("sid", "id", "n", "status", "started")},
            "force_sim": self.force_sim,
            "files_dir": self.files_dir,
            "desktop": DESKTOP,
        }

    def frame(self, since: int):
        """Binary frame for the UI, or None if nothing new since `since`."""
        with self._lock:
            r, seq = self._res, self._seq
        if r is None or seq == since:
            return None
        n = int(r.freqs.size)
        traces = []
        modes = self.settings["traces"]
        for i in range(N_TRACES):
            d = r.traces[i] if i < len(r.traces) else None
            on = i < len(modes) and modes[i] != "Off"
            traces.append(d if (on and d is not None and d.size == n) else None)
        head = {
            "seq": seq, "n": n,
            "rbw": float(r.rbw), "rbw_req": float(r.rbw_req),
            "segments": int(r.segments), "nfft": int(r.nfft),
            "rate": float(r.rate), "fs": float(r.fs),
            "overload": bool(r.overload), "fs_low": bool(r.fs_low),
            "cal_extrap": bool(r.cal_extrap),
            "peak_hz": float(r.peak_hz) if np.isfinite(r.peak_hz) else None,
            "peak_dbm": float(r.peak_dbm) if np.isfinite(r.peak_dbm) else None,
            "has": [t is not None for t in traces],
            "wf": r.wf_row is not None and r.wf_row.size == n,
            "center": self.settings["center"], "span": self.settings["span"],
            "lost": self._lost,
        }
        # live band power for the map strip, by the current survey's metric
        try:
            m = self._metric()
            pm = r.power_mean if r.power_mean is not None else r.power
            band, lo, hi, _phz, _pdb = sv.measure(r.freqs, pm, r.power, r.rbw,
                                                  m.get("lo"), m.get("hi"))
            head.update(band_dbm=band, band_lo=lo, band_hi=hi)
        except Exception:
            pass
        hj = json.dumps(head).encode()
        hj += b" " * ((-len(hj) - 4) % 8)
        parts = [struct.pack("<I", len(hj)), hj,
                 np.ascontiguousarray(r.freqs, dtype="<f8").tobytes()]
        for t in traces:
            if t is not None:
                parts.append(np.ascontiguousarray(t, dtype="<f4").tobytes())
        if head["wf"]:
            parts.append(np.ascontiguousarray(r.wf_row, dtype="<f4").tobytes())
        return b"".join(parts)

    def _analysis_trace(self):
        """Max hold when one is running (a burst is seen there), else live."""
        with self._lock:
            r = self._res
        if r is None:
            return None, None
        modes = self.settings["traces"]
        pick = None
        for want in ("Max Hold", "Average", "Live"):
            for i, m in enumerate(modes):
                if m == want and i < len(r.traces) and r.traces[i] is not None:
                    pick = r.traces[i]
                    break
            if pick is not None:
                break
        if pick is None:
            pick = r.power
        return r, np.asarray(pick, float)

    def identify(self, hz: float) -> dict:
        r, d = self._analysis_trace()
        out = {"hz": hz, "bands": [lab for lab, _n in wb.bands_at(hz)]}
        if r is None:
            return out
        stitched = int(r.segments) > 1
        c = wb.find_carrier(r.freqs, d, hz, stitched=stitched)
        if c is not None:
            cls = wb.classify_span(c["lo"], c["hi"])
            out.update(carrier=True, lo=c["lo"], hi=c["hi"],
                       level=c["level"], rise=c["rise"],
                       tech=cls.get("tech"), channel=cls.get("channel"),
                       band=cls.get("band"), occupied=cls.get("occupied"),
                       summary=cls.get("summary"))
        else:
            out["carrier"] = False
        return out

    def signals(self, max_n: int = 15) -> dict:
        """Strongest distinct signals in view, each identified."""
        with self._lock:
            seq = self._seq
        if self._sig_cache[0] == seq and self._sig_cache[1] is not None:
            return self._sig_cache[1]
        r, d = self._analysis_trace()
        if r is None or d is None or d.size < 32:
            return {"floor": None, "items": []}
        f = np.asarray(r.freqs, float)
        good = np.isfinite(d)
        if good.sum() < 32:
            return {"floor": None, "items": []}
        d = np.where(good, d, np.min(d[good]))
        x = (f - f[0]) / max(f[-1] - f[0], 1e-9)
        try:
            p, _q, sigma = wb._cf_floor(x, d)
            floor = np.polyval(p, x)
        except Exception:
            floor = np.full_like(d, np.median(d))
            sigma = 1.0
        rise = d - floor
        thr = max(6.0, 4.0 * float(sigma))
        # local maxima above threshold, strongest first
        k = max(2, d.size // 400)
        cand = []
        for i in range(k, d.size - k):
            if rise[i] < thr:
                continue
            seg = d[i - k:i + k + 1]
            if d[i] >= seg.max():
                cand.append(i)
        cand.sort(key=lambda i: -d[i])
        stitched = int(r.segments) > 1
        items, taken = [], []
        for i in cand:
            hz = float(f[i])
            if any(lo <= hz <= hi for lo, hi in taken):
                continue
            c = wb.find_carrier(f, d, hz, stitched=stitched)
            if c is not None and c["hi"] > c["lo"]:
                lo, hi = c["lo"], c["hi"]
                cls = wb.classify_span(lo, hi)
                items.append({"hz": hz, "dbm": float(d[i]),
                              "lo": lo, "hi": hi, "width": hi - lo,
                              "tech": cls.get("tech"),
                              "band": cls.get("band"),
                              "summary": cls.get("summary"),
                              "rise": float(c["rise"])})
                taken.append((lo, hi))
            else:
                bw = (f[-1] - f[0]) / max(d.size - 1, 1)
                items.append({"hz": hz, "dbm": float(d[i]), "lo": hz - bw,
                              "hi": hz + bw, "width": 0.0, "tech": None,
                              "band": " / ".join(lab for lab, _n in wb.bands_at(hz)),
                              "summary": "", "rise": float(rise[i])})
                taken.append((hz - 2 * k * bw, hz + 2 * k * bw))
            if len(items) >= max_n:
                break
        out = {"floor": float(np.median(floor)), "items": items}
        self._sig_cache = (seq, out)
        return out

    # ------------------------------------------------------------ location
    SIM_HOME = (32.0853, 34.7818)        # simulated walk starts here (Tel Aviv)

    def set_location(self, lat, lon, acc=None, alt=None, src="gps"):
        """A fix from the phone (Kotlin LocationManager) or the browser."""
        if lat is None or lon is None:
            return
        if self.settings.get("sim_gps") and src != "sim":
            self._last_real = (float(lat), float(lon))
            return
        self.loc = {"lat": float(lat), "lon": float(lon),
                    "acc": None if acc is None else float(acc),
                    "alt": None if alt is None else float(alt),
                    "t": time.time(), "src": str(src)}

    def location(self):
        loc = dict(self.loc) if self.loc else None
        if loc:
            loc["age"] = time.time() - loc["t"]
        return loc

    def _sim_step(self, k):
        """Simulated GPS: a slow loop around a virtual cell site, so the map,
        the track and the colours can be tried with no radio and no sky."""
        base = getattr(self, "_last_real", None) or self.SIM_HOME
        ang = k * 0.012
        r_m = 180.0 + 120.0 * math.sin(k * 0.031)
        dn, de = r_m * math.cos(ang), r_m * math.sin(ang)
        lat = base[0] + dn / 111_320.0
        lon = base[1] + de / (111_320.0 * math.cos(math.radians(base[0])))
        self.loc = {"lat": lat, "lon": lon, "acc": 5.0, "alt": None,
                    "t": time.time(), "src": "sim"}
        # Only on the SIMULATOR: levels fall off with distance from a virtual
        # site 150 m north-east of the start, so a demo map is not flat.
        if isinstance(self.src, wb.Simulated):
            site = (base[0] + 150 / 111_320.0,
                    base[1] + 150 / (111_320.0 * math.cos(math.radians(base[0]))))
            d = max(20.0, sv.distance_m((lat, lon), site))
            extra = float(np.clip(-25.0 * math.log10(d / 60.0), -30.0, 6.0))
        else:
            extra = 0.0
        if abs(extra - self._sim_extra) > 0.2:
            self._sim_extra = extra
            with self._lock:
                if self.worker is not None:
                    self.worker.apply(self._cfg())

    def _background(self):
        k = 0
        while self._bg_run:
            time.sleep(0.25)
            k += 1
            try:
                if self.settings.get("sim_gps") and k % 4 == 0:
                    self._sim_step(k // 4)
                elif not self.settings.get("sim_gps") and self._sim_extra:
                    self._sim_extra = 0.0
                    with self._lock:
                        if self.worker is not None:
                            self.worker.apply(self._cfg())
                if self.track is not None:
                    self._track_tick()
            except Exception:
                traceback.print_exc()

    # ------------------------------------------------------------- surveys
    def current_survey(self, create=True) -> str:
        sid = self.settings.get("survey") or ""
        ids = {x["id"] for x in self.surveys.list()}
        if sid not in ids:
            if not create:
                return ""
            sid = ids and sorted(ids)[-1] or self.surveys.create("").get("id")
            self.settings["survey"] = sid
            self.save_settings_soon()
        return sid

    def _metric(self):
        sid = self.settings.get("survey") or ""
        if not sid:
            return {}
        try:
            return self.surveys.load(sid).get("metric") or {}
        except Exception:
            return {}

    def _is_sim(self):
        return isinstance(self.src, wb.Simulated) or \
            bool(self.loc and self.loc.get("src") == "sim")

    def _point_common(self, r, loc):
        return {
            "t": time.time(),
            "lat": loc["lat"] if loc else None, "lon": loc["lon"] if loc else None,
            "acc": loc.get("acc") if loc else None,
            "src": loc.get("src") if loc else "none",
            "source": getattr(self.src, "info", ""),
            "sim": self._is_sim(),
            "center": float(self.settings["center"]),
            "span": float(self.settings["span"]),
            "rbw": float(r.rbw), "gain": float(self.settings["gain"]),
            "cal_offset": float(self.settings["cal_offset"]),
        }

    def snapshot(self, sid="", name="", lat=None, lon=None):
        """Save what is on screen now as a point of a survey."""
        with self._lock:
            r = self._res
        if r is None:
            raise RuntimeError("no spectrum yet")
        sid = sid or self.current_survey()
        s = self.surveys.load(sid)
        loc = self.location()
        if lat is not None and lon is not None:
            loc = {"lat": float(lat), "lon": float(lon), "acc": None,
                   "src": "manual"}
        elif loc and loc["age"] > 30:
            loc = None              # a stale fix is worse than none
        f = np.asarray(r.freqs, float)
        modes = self.settings["traces"]
        traces = []
        for i in range(N_TRACES):
            d = r.traces[i] if i < len(r.traces) else None
            on = i < len(modes) and modes[i] != "Off"
            traces.append(np.asarray(d, float) if on and d is not None and d.size == f.size else None)
        pm = r.power_mean if r.power_mean is not None else r.power
        f2, mx, mn = sv._pool(f, traces + [np.asarray(r.power, float)],
                              [np.asarray(pm, float)], sv.SNAP_POINTS_MAX)
        traces, live, mean = mx[:N_TRACES], mx[N_TRACES], mn[0]
        m = s.get("metric") or {}
        band, lo, hi, phz, pdb = sv.measure(f2, mean, live, r.rbw,
                                            m.get("lo"), m.get("hi"))
        sig = self.signals(max_n=10).get("items", [])
        point = self._point_common(r, loc)
        point.update(kind="snap", name=(name or "").strip()[:60],
                     band_lo=lo, band_hi=hi, band_dbm=band,
                     peak_hz=phz, peak_dbm=pdb, track=None)
        head = {"point": point, "modes": list(modes), "signals": sig,
                "center": point["center"], "span": point["span"],
                "rbw": point["rbw"]}
        blob = sv.pack(head, f2, traces + [mean])
        return self.surveys.add_point(sid, point, blob)

    # --------------------------------------------------------------- track
    def track_start(self, sid=""):
        sid = sid or self.current_survey()
        s = self.surveys.load(sid)
        tr = {"id": f"t{int(time.time() * 1000)}",
              "name": time.strftime("Walk %H:%M"), "started": time.time(),
              "ended": None}
        s["tracks"].append(tr)
        self.surveys.save(s)
        self.acc.reset()
        self.track = {"sid": sid, "id": tr["id"], "n": 0, "last_t": 0.0,
                      "last_pos": None, "status": "", "started": tr["started"]}
        return tr

    def track_stop(self):
        t, self.track = self.track, None
        if t is None:
            return None
        s = self.surveys.load(t["sid"])
        for tr in s["tracks"]:
            if tr["id"] == t["id"]:
                tr["ended"] = time.time()
                tr["n"] = t["n"]
        self.surveys.save(s)
        return t

    def _track_tick(self):
        t = self.track
        now = time.time()
        if now - t["last_t"] < float(self.settings.get("track_interval", 2.0)):
            return
        loc = self.location()
        if not loc or loc["age"] > 10:
            t["status"] = "waiting for location"
            return
        pos = (loc["lat"], loc["lon"])
        min_d = float(self.settings.get("track_dist", 0.0))
        if min_d > 0 and t["last_pos"] is not None and \
                sv.distance_m(pos, t["last_pos"]) < min_d:
            t["status"] = "standing still"
            return
        got = self.acc.take()
        with self._lock:
            r = self._res
        if got is None or r is None:
            t["status"] = "waiting for spectrum"
            return
        f, mx, mean, nsw = got
        f2, (mx2,), (mean2,) = sv._pool(f, [mx], [mean], sv.TRACK_POINTS_MAX)
        s = self.surveys.load(t["sid"])
        m = s.get("metric") or {}
        band, lo, hi, phz, pdb = sv.measure(f2, mean2, mx2, r.rbw,
                                            m.get("lo"), m.get("hi"))
        point = self._point_common(r, loc)
        point.update(kind="track", track=t["id"], name="", sweeps=int(nsw),
                     band_lo=lo, band_hi=hi, band_dbm=band,
                     peak_hz=phz, peak_dbm=pdb)
        head = {"point": point, "modes": ["Max Hold"], "signals": [],
                "center": point["center"], "span": point["span"],
                "rbw": point["rbw"]}
        self.surveys.add_point(t["sid"], point, sv.pack(head, f2, [mx2, None, None, None, mean2]))
        t["n"] += 1
        t["last_t"] = now
        t["last_pos"] = pos
        t["status"] = "recording"

    def survey_op(self, d: dict):
        op = d.get("op")
        sid = d.get("id") or ""
        if op == "create":
            s = self.surveys.create(d.get("name", ""))
            self.apply({"survey": s["id"]})
            return s
        if op == "select":
            self.apply({"survey": sid})
            return self.surveys.load(sid)
        if op == "rename":
            self.surveys.rename(sid, d.get("name", ""))
        elif op == "delete":
            if self.track and self.track["sid"] == sid:
                self.track_stop()
            self.surveys.delete(sid)
            if self.settings.get("survey") == sid:
                self.settings["survey"] = ""
        elif op == "metric":
            self.surveys.set_metric(sid, d.get("kind", "band"), d.get("lo"), d.get("hi"))
        elif op == "calib":
            self.surveys.set_calib(sid, d.get("ctrl"))
        elif op == "remove_base":
            self.surveys.remove_base(sid)
        elif op == "export":
            return self.surveys.export(sid)
        return {"ok": True}

    # ----------------------------------------------------------- captures
    def save_csv(self) -> dict:
        with self._lock:
            r = self._res
        if r is None:
            return {"ok": False, "error": "no data yet"}
        modes = self.settings["traces"]
        head = ["Frequency (Hz)"]
        cols = []
        for i, m in enumerate(modes):
            if m != "Off" and i < len(r.traces) and r.traces[i] is not None:
                head.append(f"Trace {i + 1} {m} (dBm)")
                cols.append(r.traces[i])
        stem = os.path.join(self.capture_dir, f"Spectrum {_now_stamp()}")
        meta = [f"center {wb.fmt_hz(self.settings['center'])}",
                f"span {wb.fmt_hz(self.settings['span'])}",
                f"RBW {wb.fmt_hz_short(r.rbw)}",
                f"gain {self.settings['gain']:.0f} dB",
                f"source {getattr(self.src, 'info', '')}",
                f"{APP_NAME} {APP_VERSION}"]
        rows = []
        for j in range(r.freqs.size):
            rows.append([f"{r.freqs[j]:.1f}"] +
                        [f"{c[j]:.2f}" if np.isfinite(c[j]) else "" for c in cols])
        with open(stem + ".csv", "w", encoding="utf-8", newline="") as fh:
            for m in meta:
                fh.write(f"# {m}\n")
            fh.write(",".join(head) + "\n")
            for row in rows:
                fh.write(",".join(row) + "\n")
        xlsx = ""
        try:
            # The desktop's own hand-rolled xlsx writer (no extra package).
            wb.write_xlsx(stem + ".xlsx", head, rows,
                          [(m.split(" ", 1)[0], m.split(" ", 1)[-1]) for m in meta],
                          len(head) + 1)
            xlsx = stem + ".xlsx"
        except Exception:
            traceback.print_exc()
            xlsx = ""
        return {"ok": True, "csv": stem + ".csv", "xlsx": xlsx}

    # ------------------------------------------------------------ missions
    # A mission is a saved recipe for hands-free work: which bands to visit,
    # how long to dwell on each, what to save there, how often to repeat.
    # The UI runs it (it draws the pictures and the video); this only keeps
    # the recipes.
    def _missions_path(self):
        return os.path.join(self.files_dir, "missions.json")

    def _load_missions(self):
        try:
            with open(self._missions_path(), encoding="utf-8") as fh:
                data = json.load(fh)
            return [self._clean_mission(m) for m in data if isinstance(m, dict)]
        except Exception:
            return []

    def _save_missions(self):
        tmp = self._missions_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.missions, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self._missions_path())

    @staticmethod
    def _clean_mission(m: dict) -> dict:
        def num(k, d, lo, hi):
            try:
                return float(min(max(float(m.get(k, d)), lo), hi))
            except (TypeError, ValueError):
                return float(d)
        out = m.get("outputs") or {}
        gain = m.get("gain")
        try:
            gain = None if gain in (None, "") else float(gain)
        except (TypeError, ValueError):
            gain = None
        try:
            vs = float(min(max(float(out.get("video_s", 5) or 5), 2), 30))
        except (TypeError, ValueError):
            vs = 5.0
        repeat = m.get("repeat") if m.get("repeat") in ("once", "count", "continuous") else "once"
        return {
            "id": str(m.get("id") or ("m" + str(int(time.time() * 1000)))),
            "name": str(m.get("name") or "Mission").strip()[:60],
            "presets": [str(x) for x in (m.get("presets") or [])][:40],
            "dwell": num("dwell", 5, 1, 600),
            "gain": gain,
            "outputs": {"image": bool(out.get("image", True)),
                        "video": bool(out.get("video", False)),
                        "video_s": vs,
                        "data": bool(out.get("data", True)),
                        "log": bool(out.get("log", False))},
            "map": bool(m.get("map", True)),
            "track": bool(m.get("track", False)),
            "repeat": repeat,
            "count": int(num("count", 3, 1, 1000)),
            "interval": num("interval", 0, 0, 3600),
            "created": float(m.get("created") or time.time()),
        }

    def mission_op(self, d: dict) -> list:
        op = d.get("op")
        if op == "save":
            m = self._clean_mission(d.get("mission") or {})
            for i, x in enumerate(self.missions):
                if x["id"] == m["id"]:
                    self.missions[i] = m
                    break
            else:
                self.missions.append(m)
        elif op == "delete":
            self.missions = [x for x in self.missions if x["id"] != d.get("id")]
        self._save_missions()
        return self.missions

    # ---------------------------------------------------------- recordings
    def record(self, d: dict) -> dict:
        """Save what is on screen now as a "spectrum recording": dry data
        (CSV of the traces as drawn), a full log (JSON with everything) and,
        if asked, a point on the map. Pictures and video are made by the UI
        and arrive through /api/blob under the same base name."""
        with self._lock:
            r = self._res
        if r is None:
            return {"ok": False, "error": "no spectrum yet"}
        base = os.path.basename(str(d.get("base") or f"REC {_now_stamp()}")).strip() or f"REC {_now_stamp()}"
        stem = os.path.join(self.capture_dir, base)
        loc = self.location()
        if loc and loc.get("age", 0) > 30:
            loc = None
        files = []
        f = np.asarray(r.freqs, float)
        modes = self.settings["traces"]
        cols = []
        for i, m in enumerate(modes):
            if m != "Off" and i < len(r.traces) and r.traces[i] is not None \
                    and np.asarray(r.traces[i]).size == f.size:
                cols.append((f"Trace {i + 1} {m} (dBm)", np.asarray(r.traces[i], float)))
        pm = r.power_mean if r.power_mean is not None else r.power
        band, lo, hi, phz, pdb = sv.measure(f, np.asarray(pm, float),
                                            np.asarray(r.power, float), r.rbw)
        when = time.time()
        meta = [
            ("app", f"{APP_NAME} {APP_VERSION}"),
            ("time", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when))),
            ("name", str(d.get("name") or "")),
            ("mission", str(d.get("mission") or "")),
            ("latitude", f"{loc['lat']:.7f}" if loc else ""),
            ("longitude", f"{loc['lon']:.7f}" if loc else ""),
            ("accuracy_m", f"{loc['acc']:.0f}" if loc and loc.get("acc") is not None else ""),
            ("location_source", loc["src"] if loc else "NONE - not geo-tagged"),
            ("center_hz", f"{self.settings['center']:.0f}"),
            ("span_hz", f"{self.settings['span']:.0f}"),
            ("rbw_hz", f"{float(r.rbw):.0f}"),
            ("gain_db", f"{self.settings['gain']:.0f}"),
            ("band_power_dbm", "" if band is None else f"{band:.2f}"),
            ("peak_dbm", "" if pdb is None else f"{pdb:.2f}"),
            ("peak_hz", "" if phz is None else f"{phz:.0f}"),
            ("overload", "yes" if r.overload else "no"),
            ("source", getattr(self.src, "info", "")),
            ("simulated", "yes" if self._is_sim() else "no"),
        ]
        if d.get("data", True):
            with open(stem + ".csv", "w", encoding="utf-8", newline="") as fh:
                for k, v in meta:
                    fh.write(f"# {k},{str(v).replace(',', ' ')}\n")
                fh.write(",".join(["Frequency (Hz)"] + [c[0] for c in cols]) + "\n")
                for j in range(f.size):
                    fh.write(",".join([f"{f[j]:.1f}"] + [
                        f"{c[1][j]:.2f}" if np.isfinite(c[1][j]) else "" for c in cols]) + "\n")
            files.append(stem + ".csv")
        point = None
        sid = ""
        if d.get("map"):
            try:
                sid = d.get("sid") or self.current_survey()
                point = self.snapshot(sid, str(d.get("name") or base))
            except Exception as exc:
                traceback.print_exc()
                point = None
        if d.get("log"):
            def clean(a):
                return [None if not np.isfinite(x) else round(float(x), 2) for x in a]
            log = {
                "app": APP_NAME, "version": APP_VERSION,
                "engine": wb.version_line(),
                "time": when, "time_text": dict(meta)["time"],
                "name": d.get("name") or "", "mission": d.get("mission") or "",
                "location": loc,
                "settings": {k: v for k, v in self.settings.items() if k != "ui"},
                "source": {"info": getattr(self.src, "info", ""),
                           "radio": isinstance(self.src, wb.BladeRF),
                           "simulated": self._is_sim(),
                           "usb_speed": getattr(self, "_usb_speed", None)},
                "frame": {"rbw": float(r.rbw), "rbw_req": float(r.rbw_req),
                          "segments": int(r.segments), "nfft": int(r.nfft),
                          "sweep_rate": float(r.rate), "fs": float(r.fs),
                          "overload": bool(r.overload)},
                "band_power": {"dbm": band, "lo": lo, "hi": hi},
                "peak": {"hz": phz, "dbm": pdb},
                "signals": self.signals(max_n=40).get("items", []),
                "markers": d.get("markers") or [],
                "display": d.get("display") or {},
                "map_point": {"sid": sid, "id": point.get("id")} if point else None,
                "freqs_hz": [round(float(x), 1) for x in f],
                "traces": {name: clean(a) for name, a in cols},
            }
            with open(stem + ".json", "w", encoding="utf-8") as fh:
                json.dump(log, fh, ensure_ascii=False, default=float)
            files.append(stem + ".json")
        return {"ok": True, "base": base, "files": files, "location": loc,
                "point": point, "sid": sid,
                "band_dbm": band, "peak_dbm": pdb, "peak_hz": phz}

    def save_blob(self, name: str, data: bytes) -> dict:
        base = os.path.basename(name) or f"capture {_now_stamp()}.png"
        path = os.path.join(self.capture_dir, base)
        with open(path, "wb") as fh:
            fh.write(data)
        return {"ok": True, "path": path}

    def list_captures(self):
        out = []
        for n in sorted(os.listdir(self.capture_dir), reverse=True):
            p = os.path.join(self.capture_dir, n)
            if os.path.isfile(p):
                out.append({"name": n, "path": p, "size": os.path.getsize(p)})
        return out[:200]

    def export_presets(self) -> str:
        p = os.path.join(self.capture_dir, f"presets {_now_stamp()}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"kind": "bladeRF-mobile-presets", "version": 1,
                       "presets": self.presets}, f, indent=1, ensure_ascii=False)
        return p

    def import_presets(self, text: str, replace_all=False) -> int:
        d = json.loads(text)
        lst = d.get("presets") if isinstance(d, dict) else d
        if not isinstance(lst, list):
            raise ValueError("not a presets file")
        new = []
        for i, p in enumerate(lst):
            if isinstance(p, dict):
                q = self._clean_preset(p)
                q["id"] = f"i{int(time.time()*1000)}_{i}"
                new.append(q)
        with self._lock:
            self.presets = new if replace_all else self.presets + new
            self._save_presets()
        return len(new)

    def band_plan(self):
        return [{"name": lab, "lo": lo, "hi": hi, "note": note}
                for lab, lo, hi, note in wb.BAND_PLAN]


# ============================================================================
#  HTTP
# ============================================================================


class Handler(BaseHTTPRequestHandler):
    ctl: Controller = None
    web_root: str = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        LAST_SEEN[0] = time.time()
        u = urlparse(self.path)
        q = parse_qs(u.query)
        c = self.ctl
        try:
            if u.path == "/api/state":
                return self._json(c.state())
            if u.path == "/api/frame":
                since = int(q.get("since", ["-1"])[0])
                b = c.frame(since)
                if b is None:
                    return self._send(204, b"", "application/octet-stream")
                return self._send(200, b, "application/octet-stream")
            if u.path == "/api/signals":
                return self._json(c.signals())
            if u.path == "/api/identify":
                return self._json(c.identify(float(q["hz"][0])))
            if u.path == "/api/bands":
                return self._json(c.band_plan())
            if u.path == "/api/captures":
                return self._json(c.list_captures())
            if u.path == "/api/location":
                return self._json(c.location())
            if u.path == "/api/missions":
                return self._json(c.missions)
            if u.path == "/api/surveys":
                return self._json({"current": c.current_survey(create=False),
                                   "list": c.surveys.list()})
            if u.path == "/api/survey":
                sid = q.get("id", [""])[0] or c.current_survey()
                return self._json(c.surveys.load(sid))
            if u.path == "/api/point_data":
                return self._send(200, c.surveys.point_data(q["sid"][0], q["id"][0]),
                                  "application/octet-stream")
            if u.path == "/api/file":
                path = c.surveys.file_path(q["sid"][0], q["name"][0])
                with open(path, "rb") as f:
                    data = f.read()
                ext = os.path.splitext(path)[1].lower()
                return self._send(200, data, {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                                              ".png": "image/png", ".webp": "image/webp",
                                              ".gif": "image/gif", ".bmp": "image/bmp",
                                              ".kml": "application/vnd.google-earth.kml+xml",
                                              ".csv": "text/csv"}.get(ext, "application/octet-stream"))
            return self._static(u.path)
        except Exception as exc:
            traceback.print_exc()
            return self._json({"error": str(exc)}, 500)

    do_HEAD = do_GET

    def do_POST(self):
        LAST_SEEN[0] = time.time()
        u = urlparse(self.path)
        q = parse_qs(u.query)
        c = self.ctl
        try:
            if u.path == "/api/blob":
                sid = q.get("sid", [""])[0]
                if sid:   # a picture that belongs to a survey (map export)
                    d = c.surveys._dir(sid)
                    path = os.path.join(d, os.path.basename(q.get("name", ["map.jpg"])[0]))
                    with open(path, "wb") as f:
                        f.write(self._body())
                    return self._json({"ok": True, "path": path})
                return self._json(c.save_blob(q.get("name", [""])[0],
                                              self._body()))
            if u.path == "/api/point_jpg":
                path = c.surveys.point_jpg(q["sid"][0], q["id"][0], self._body(),
                                           q.get("name", [""])[0])
                return self._json({"ok": True, "path": path})
            if u.path == "/api/base_image":
                b = c.surveys.set_base(q["sid"][0], self._body(), q.get("name", [""])[0],
                                       int(q.get("w", ["0"])[0]), int(q.get("h", ["0"])[0]))
                return self._json({"ok": True, "base": b})
            d = json.loads(self._body() or b"{}")
            op = u.path.rsplit("/", 1)[-1]
            if op == "set":
                c.apply(d)
                return self._json({"ok": True})
            if op == "single":
                c.single()
                return self._json({"ok": True})
            if op == "restart":
                c.restart()
                return self._json({"ok": True})
            if op == "port":
                c.set_port(int(d.get("port", 0)))
                return self._json({"ok": True})
            if op == "connect":
                c.force_sim = False
                c.start(want_radio=True)
                return self._json({"ok": True, "radio": isinstance(c.src, wb.BladeRF),
                                   "error": c._radio_error})
            if op == "simulate":
                c.start(want_radio=False)
                return self._json({"ok": True})
            if op == "usbfd":
                c.set_usb_fd(int(d.get("fd", -1)))
                if d.get("connect", True):
                    c.start(want_radio=True)
                return self._json({"ok": True, "radio": isinstance(c.src, wb.BladeRF),
                                   "error": c._radio_error})
            if op == "preset":
                c.preset_op(str(d.get("op", "")), d)
                return self._json({"ok": True, "presets": c.presets})
            if op == "presets_export":
                return self._json({"ok": True, "path": c.export_presets()})
            if op == "presets_import":
                n = c.import_presets(d.get("text", ""), bool(d.get("replace")))
                return self._json({"ok": True, "n": n, "presets": c.presets})
            if op == "csv":
                return self._json(c.save_csv())
            if op == "record":
                return self._json(c.record(d))
            if op == "mission":
                return self._json({"ok": True, "missions": c.mission_op(d)})
            if op == "desktop":
                return self._json(desktop_op(c, d))
            if op == "location":
                c.set_location(d.get("lat"), d.get("lon"), d.get("acc"),
                               d.get("alt"), d.get("src", "browser"))
                return self._json({"ok": True})
            if op == "survey":
                return self._json(c.survey_op(d))
            if op == "point":
                pt = c.snapshot(d.get("sid", ""), d.get("name", ""),
                                d.get("lat"), d.get("lon"))
                return self._json({"ok": True, "point": pt,
                                   "sid": d.get("sid") or c.current_survey()})
            if op == "point_op":
                c.surveys.point_op(d["sid"], d["id"], d.get("what", ""), d)
                return self._json({"ok": True})
            if op == "track":
                if d.get("what") == "start":
                    return self._json({"ok": True, "track": c.track_start(d.get("sid", ""))})
                t = c.track_stop()
                return self._json({"ok": True, "n": t["n"] if t else 0})
            return self._json({"error": "unknown"}, 404)
        except Exception as exc:
            traceback.print_exc()
            return self._json({"error": str(exc)}, 500)

    _TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
              ".css": "text/css", ".png": "image/png", ".svg": "image/svg+xml",
              ".json": "application/json", ".ico": "image/x-icon"}

    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        full = os.path.abspath(os.path.join(self.web_root, path.lstrip("/")))
        if not full.startswith(os.path.abspath(self.web_root)) or \
                not os.path.isfile(full):
            return self._send(404, b"not found", "text/plain")
        with open(full, "rb") as f:
            data = f.read()
        ext = os.path.splitext(full)[1].lower()
        return self._send(200, data, self._TYPES.get(ext, "application/octet-stream"))


# ============================================================================
#  Desktop edition: what Kotlin does on the phone, done with the PC's own
#  dialogs (tkinter, part of every python.org install) and Explorer.
# ============================================================================

_DLG_LOCK = threading.Lock()


def _tk_dialog(kind, **kw):
    """Run one native file dialog, on top of the app window. Returns a path or ''."""
    import tkinter as tk
    from tkinter import filedialog
    with _DLG_LOCK:
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        try:
            if kind == "save":
                return filedialog.asksaveasfilename(parent=root, **kw) or ""
            return filedialog.askopenfilename(parent=root, **kw) or ""
        finally:
            root.destroy()


def _reveal(path):
    import subprocess
    if sys.platform.startswith("win"):
        if os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            os.startfile(path if os.path.isdir(path) else os.path.dirname(path))  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path) if os.path.isfile(path) else path])


def _keep_awake(on: bool):
    """Windows: keep the PC from sleeping while measuring (like keep-screen-on)."""
    if not sys.platform.startswith("win"):
        return
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM, ES_DISPLAY = 0x80000000, 0x1, 0x2
    flags = ES_CONTINUOUS | (ES_SYSTEM | ES_DISPLAY if on else 0)
    ctypes.windll.kernel32.SetThreadExecutionState(flags)


def desktop_op(c, d):
    op = d.get("op")
    try:
        if op == "saveas":
            src = d.get("path", "")
            if not os.path.isfile(src):
                return {"ok": False, "error": "file not found"}
            name = d.get("name") or os.path.basename(src)
            ext = os.path.splitext(name)[1] or os.path.splitext(src)[1]
            dst = _tk_dialog("save", initialfile=name, defaultextension=ext,
                             initialdir=os.path.expanduser("~/Documents"),
                             filetypes=[(ext.upper().strip(".") or "file", "*" + ext),
                                        ("All files", "*.*")])
            if not dst:
                return {"ok": False, "cancelled": True}
            import shutil
            shutil.copyfile(src, dst)
            return {"ok": True, "path": dst}
        if op == "reveal":
            _reveal(d.get("path") or c.capture_dir)
            return {"ok": True}
        if op == "pick_fpga":
            p = _tk_dialog("open", title="hostedxA4.rbf",
                           filetypes=[("FPGA image", "*.rbf"), ("All files", "*.*")])
            if not p:
                return {"ok": False, "cancelled": True}
            import shutil
            dst_dir = os.environ.get("BLADERF_SEARCH_DIR") or os.path.join(c.files_dir, "fpga")
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copyfile(p, os.path.join(dst_dir, "hostedxA4.rbf"))
            return {"ok": True, "path": p}
        if op == "pick_presets":
            p = _tk_dialog("open", filetypes=[("Presets", "*.json"), ("All files", "*.*")])
            if not p:
                return {"ok": False, "cancelled": True}
            with open(p, encoding="utf-8") as f:
                return {"ok": True, "text": f.read()}
        if op == "keep_awake":
            _keep_awake(bool(d.get("on")))
            return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": "unknown op"}


def _tail(path, n=14):
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 6000))
            lines = f.read().decode("utf-8", "replace").splitlines()
        return "\n".join(lines[-n:])
    except Exception:
        return ""


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # The WebView drops a poll when it pauses or reloads: not an error.
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


_SERVER = None
_CTL = None


def start_server(files_dir: str, web_root: str, port: int = 0,
                 lib_path: str = "", force_sim: bool = False,
                 usb_fd: int = -1, tz: str = "") -> int:
    """Entry point used by Android (and by the PC runner). Returns the port."""
    global _SERVER, _CTL
    if tz:
        # Android does not hand its time zone to native processes; without
        # this every timestamp (file names, survey times) would be UTC.
        os.environ["TZ"] = tz
        try:
            time.tzset()
        except AttributeError:
            pass
    os.environ.setdefault("HOME", files_dir)
    os.environ.setdefault("LOCALAPPDATA", files_dir)
    fpga_dir = os.path.join(files_dir, "fpga")
    os.makedirs(fpga_dir, exist_ok=True)
    # libbladeRF looks here for hostedxA4.rbf when the board's flash does
    # not autoload an FPGA image.
    os.environ["BLADERF_SEARCH_DIR"] = fpga_dir
    if "ANDROID_ROOT" in os.environ and not NATIVE_LOG[0]:
        # libbladeRF and libusb write to the C stderr, which on a phone goes
        # nowhere. Point it at a file the Radio panel shows.
        try:
            NATIVE_LOG[0] = os.path.join(files_dir, "native.log")
            fd = os.open(NATIVE_LOG[0], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            os.dup2(fd, 2)
        except OSError:
            NATIVE_LOG[0] = ""
    if lib_path:
        os.environ["BLADERF_LIB"] = lib_path
    ctl = Controller(files_dir, lib_path=lib_path, force_sim=force_sim)
    if usb_fd >= 0:
        ctl.set_usb_fd(usb_fd)
    ctl.start(want_radio=(usb_fd >= 0 or not lib_path) and not force_sim)
    Handler.ctl = ctl
    Handler.web_root = web_root
    srv = _Server(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True,
                     name="http").start()
    _SERVER, _CTL = srv, ctl
    return srv.server_address[1]


def set_usb_fd(fd: int) -> None:
    """Called by Android when a bladeRF is (re)attached and permitted."""
    if _CTL is not None:
        _CTL.set_usb_fd(fd)
        _CTL.start(want_radio=True)


def use_simulator() -> None:
    """The "bladeRF Simulator" icon was tapped while the app was running."""
    if _CTL is not None:
        _CTL.force_sim = True
        _CTL.start(want_radio=False)


def usb_detached() -> None:
    """The board was unplugged and Android closed its descriptor. Forget the
    number before anything reuses it -- the worker's reconnect attempts then
    simply find no device until set_usb_fd() hands over a fresh one."""
    if _CTL is not None:
        _CTL._message("bladeRF unplugged")
        _CTL.set_usb_fd(-1)


def stop_server() -> None:
    global _SERVER
    if _CTL is not None:
        _CTL.shutdown()
    if _SERVER is not None:
        _SERVER.shutdown()
        _SERVER = None


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--files", default=os.path.join(os.path.expanduser("~"), "bladeRF Mobile data"))
    ap.add_argument("--web", default=os.path.join(HERE, "..", "assets", "webui"))
    ap.add_argument("--lib", default="")
    a = ap.parse_args()
    p = start_server(a.files, os.path.abspath(a.web), a.port, a.lib, a.sim)
    print(f"http://127.0.0.1:{p}/", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_server()
