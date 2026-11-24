"""
Location surveys: spectrum samples tied to where they were taken.

A survey is a folder:
    Surveys/<id>/survey.json     name, points, tracks, base image + calibration
    Surveys/<id>/<pid>.bin       the spectrum of one point (same binary layout
                                 the live UI reads: header, freqs, traces)
    Surveys/<id>/<pid>.jpg       the picture saved with a point (optional)
    Surveys/<id>/base.<ext>      a drawing / satellite image of the area

Two ways points get in:
  * a SNAPSHOT: "save this point" -- full-resolution traces, a JPEG, a name;
  * a TRACK: a walk recorded automatically, one compact point every few
    seconds (and/or metres), each holding max-hold and mean power over the
    time since the previous point, so a burst between two points is not lost.

Levels for the map come from the mean-power trace (Result.power_mean)
through the desktop's own band_power_mw() -- the measurement, never the
drawn picture (desktop bug 35).
"""
from __future__ import annotations

import json
import math
import os
import shutil
import struct
import threading
import time

import numpy as np

import workbench_core as wb

TRACK_POINTS_MAX = 600        # stored resolution of an auto (track) point
SNAP_POINTS_MAX = 4001
R_EARTH = 6371008.8


def _now_id(prefix: str) -> str:
    return f"{prefix}{int(time.time() * 1000)}"


def distance_m(a, b) -> float:
    """Great-circle distance between (lat, lon) pairs, metres."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * R_EARTH * math.asin(min(1.0, math.sqrt(h)))


def _pool(freqs, arrays_max, arrays_mean, n_out):
    """Reduce to n_out buckets: max for peak traces, linear mean for power."""
    n = freqs.size
    if n <= n_out:
        return freqs, arrays_max, arrays_mean
    e = np.linspace(0, n, n_out + 1).astype(int)
    f = np.array([freqs[e[k]:e[k + 1]].mean() for k in range(n_out)])
    mx = [None if a is None else np.maximum.reduceat(a, e[:-1]) for a in arrays_max]
    mn = []
    for a in arrays_mean:
        if a is None:
            mn.append(None)
            continue
        lin = 10.0 ** (np.asarray(a, float) / 10.0)
        s = np.add.reduceat(lin, e[:-1]) / np.diff(e)
        mn.append(10.0 * np.log10(np.maximum(s, 1e-30)))
    return f, mx, mn


def pack(head: dict, freqs, traces) -> bytes:
    """Same layout as /api/frame: u32 len, JSON (padded to 8), f64 freqs, f32 traces."""
    head = dict(head)
    head["n"] = int(freqs.size)
    head["has"] = [t is not None for t in traces]
    head["wf"] = False
    hj = json.dumps(head).encode()
    hj += b" " * ((-len(hj) - 4) % 8)
    parts = [struct.pack("<I", len(hj)), hj,
             np.ascontiguousarray(freqs, dtype="<f8").tobytes()]
    for t in traces:
        if t is not None:
            parts.append(np.ascontiguousarray(t, dtype="<f4").tobytes())
    return b"".join(parts)


class Surveys:
    def __init__(self, root: str):
        self.root = os.path.join(root, "Surveys")
        os.makedirs(self.root, exist_ok=True)
        self._lock = threading.RLock()
        self._cache = {}

    # ------------------------------------------------------------ storage
    def _dir(self, sid):
        d = os.path.join(self.root, os.path.basename(str(sid)))
        return d

    def _path(self, sid):
        return os.path.join(self._dir(sid), "survey.json")

    def load(self, sid) -> dict:
        with self._lock:
            if sid in self._cache:
                return self._cache[sid]
            with open(self._path(sid), encoding="utf-8") as f:
                s = json.load(f)
            s.setdefault("points", [])
            s.setdefault("tracks", [])
            s.setdefault("base", None)
            s.setdefault("metric", {"kind": "band", "lo": None, "hi": None})
            self._cache[sid] = s
            return s

    def save(self, s: dict):
        with self._lock:
            d = self._dir(s["id"])
            os.makedirs(d, exist_ok=True)
            tmp = self._path(s["id"]) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(s, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._path(s["id"]))
            self._cache[s["id"]] = s

    def list(self):
        out = []
        for n in sorted(os.listdir(self.root)):
            if os.path.isfile(os.path.join(self.root, n, "survey.json")):
                try:
                    s = self.load(n)
                except Exception:
                    continue
                out.append({"id": s["id"], "name": s["name"],
                            "created": s.get("created", 0),
                            "n_points": len(s["points"]),
                            "n_tracks": len(s["tracks"]),
                            "has_base": bool(s.get("base"))})
        out.sort(key=lambda x: -x["created"])
        return out

    def create(self, name: str) -> dict:
        sid = _now_id("s")
        s = {"id": sid, "name": (name or "").strip()[:60] or
             time.strftime("Survey %Y-%m-%d %H:%M"),
             "created": time.time(), "points": [], "tracks": [], "base": None,
             "metric": {"kind": "band", "lo": None, "hi": None}}
        self.save(s)
        return s

    def rename(self, sid, name):
        s = self.load(sid)
        s["name"] = (name or "").strip()[:60] or s["name"]
        self.save(s)

    def delete(self, sid):
        with self._lock:
            self._cache.pop(sid, None)
            shutil.rmtree(self._dir(sid), ignore_errors=True)

    def set_metric(self, sid, kind, lo, hi):
        s = self.load(sid)
        s["metric"] = {"kind": kind if kind in ("band", "peak") else "band",
                       "lo": float(lo) if lo not in (None, "") else None,
                       "hi": float(hi) if hi not in (None, "") else None}
        self.save(s)

    # --------------------------------------------------------- base image
    def set_base(self, sid, data: bytes, name: str, w: int, h: int):
        s = self.load(sid)
        ext = os.path.splitext(name or "")[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"):
            ext = ".jpg"
        old = s.get("base")
        if old and old.get("file"):
            try:
                os.remove(os.path.join(self._dir(sid), old["file"]))
            except OSError:
                pass
        fn = f"base_{int(time.time())}{ext}"
        with open(os.path.join(self._dir(sid), fn), "wb") as f:
            f.write(data)
        s["base"] = {"file": fn, "name": os.path.basename(name or fn),
                     "w": int(w), "h": int(h), "ctrl": []}
        self.save(s)
        return s["base"]

    def set_calib(self, sid, ctrl):
        s = self.load(sid)
        if not s.get("base"):
            raise ValueError("no base image")
        good = []
        for c in ctrl or []:
            good.append({"px": float(c["px"]), "py": float(c["py"]),
                         "lat": float(c["lat"]), "lon": float(c["lon"])})
        s["base"]["ctrl"] = good[:6]
        self.save(s)

    def remove_base(self, sid):
        s = self.load(sid)
        b = s.get("base")
        if b and b.get("file"):
            try:
                os.remove(os.path.join(self._dir(sid), b["file"]))
            except OSError:
                pass
        s["base"] = None
        self.save(s)

    # -------------------------------------------------------------- points
    def add_point(self, sid, point: dict, blob: bytes) -> dict:
        with self._lock:
            s = self.load(sid)
            pid = point.get("id") or _now_id("p")
            point["id"] = pid
            with open(os.path.join(self._dir(sid), pid + ".bin"), "wb") as f:
                f.write(blob)
            s["points"].append(point)
            self.save(s)
            return point

    def point_op(self, sid, pid, op, d):
        with self._lock:
            s = self.load(sid)
            p = next((p for p in s["points"] if p["id"] == pid), None)
            if p is None:
                raise KeyError("no such point")
            if op == "rename":
                p["name"] = str(d.get("name", "")).strip()[:60]
            elif op == "move":
                p["lat"], p["lon"] = float(d["lat"]), float(d["lon"])
                p["src"] = "manual"
            elif op == "delete":
                s["points"] = [q for q in s["points"] if q["id"] != pid]
                for ext in (".bin", ".jpg"):
                    try:
                        os.remove(os.path.join(self._dir(sid), pid + ext))
                    except OSError:
                        pass
            self.save(s)

    def point_jpg(self, sid, pid, data: bytes, name: str = "") -> str:
        with self._lock:
            s = self.load(sid)
            p = next((p for p in s["points"] if p["id"] == pid), None)
            if p is None:
                raise KeyError("no such point")
            path = os.path.join(self._dir(sid), pid + ".jpg")
            with open(path, "wb") as f:
                f.write(data)
            p["jpg"] = pid + ".jpg"
            if name:
                p["name"] = name.strip()[:60]
            self.save(s)
            return path

    def point_data(self, sid, pid) -> bytes:
        with open(os.path.join(self._dir(sid), os.path.basename(pid) + ".bin"), "rb") as f:
            return f.read()

    def file_path(self, sid, name) -> str:
        p = os.path.join(self._dir(sid), os.path.basename(name))
        if not os.path.isfile(p):
            raise FileNotFoundError(name)
        return p

    # ------------------------------------------------------------- exports
    def export(self, sid) -> dict:
        """CSV + KML (Google Earth) of the points, written into the survey."""
        s = self.load(sid)
        d = self._dir(sid)
        stem = os.path.join(d, f"{_safe(s['name'])}")
        with open(stem + ".csv", "w", encoding="utf-8-sig", newline="") as f:
            f.write("time,name,kind,lat,lon,accuracy_m,location_source,source,"
                    "center_hz,span_hz,rbw_hz,band_lo_hz,band_hi_hz,"
                    "band_power_dbm,peak_hz,peak_dbm,simulated\n")
            for p in s["points"]:
                f.write(",".join(str(x) for x in (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p["t"])),
                    _csv(p.get("name", "")), p.get("kind", ""),
                    _f(p.get("lat")), _f(p.get("lon")), _f(p.get("acc")),
                    p.get("src", ""), _csv(p.get("source", "")),
                    _f(p.get("center")), _f(p.get("span")), _f(p.get("rbw")),
                    _f(p.get("band_lo")), _f(p.get("band_hi")),
                    _f(p.get("band_dbm")), _f(p.get("peak_hz")),
                    _f(p.get("peak_dbm")), int(bool(p.get("sim")))) ) + "\n")
        pm = [p for p in s["points"] if p.get("lat") is not None]
        k = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
             f"<name>{_x(s['name'])}</name>"]
        for p in pm:
            desc = (f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(p['t']))}<br/>"
                    f"band power {_db(p.get('band_dbm'))} dBm "
                    f"({_mhz(p.get('band_lo'))}-{_mhz(p.get('band_hi'))} MHz)<br/>"
                    f"peak {_db(p.get('peak_dbm'))} dBm at {_mhz(p.get('peak_hz'))} MHz"
                    + ("<br/>SIMULATED" if p.get("sim") else ""))
            if p.get("jpg"):
                desc += f'<br/><img src="{p["jpg"]}" width="480"/>'
            k.append(f"<Placemark><name>{_x(p.get('name') or '')}</name>"
                     f"<description><![CDATA[{desc}]]></description>"
                     f"<Point><coordinates>{p['lon']:.7f},{p['lat']:.7f},0</coordinates>"
                     f"</Point></Placemark>")
        for tr in s["tracks"]:
            pts = [p for p in pm if p.get("track") == tr["id"]]
            if len(pts) >= 2:
                coords = " ".join(f"{p['lon']:.7f},{p['lat']:.7f},0" for p in pts)
                k.append(f"<Placemark><name>{_x(tr.get('name', 'track'))}</name>"
                         f"<LineString><coordinates>{coords}</coordinates>"
                         f"</LineString></Placemark>")
        k.append("</Document></kml>")
        with open(stem + ".kml", "w", encoding="utf-8") as f:
            f.write("\n".join(k))
        return {"csv": stem + ".csv", "kml": stem + ".kml"}


def _safe(name):
    keep = "".join(c if c.isalnum() or c in " -_()." else "_" for c in name)
    return keep.strip() or "survey"


def _csv(t):
    t = str(t).replace('"', '""')
    return f'"{t}"' if any(c in t for c in ',"\n') else t


def _f(v):
    return "" if v is None or (isinstance(v, float) and not math.isfinite(v)) else f"{v}"


def _x(t):
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _db(v):
    return "?" if v is None else f"{v:.1f}"


def _mhz(v):
    return "?" if v is None else f"{v / 1e6:.3f}"


# ============================================================================
#  Accumulator: what happened between two track points
# ============================================================================


class Accum:
    """Max hold of the detected trace and the mean of the mean-power trace,
    over every sweep since the last reset. Fed from the worker thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self.freqs = None
            self.mx = None
            self.lin = None
            self.n = 0
            self.t0 = time.time()

    def add(self, r):
        f = r.freqs
        pm = r.power_mean if r.power_mean is not None else r.power
        with self._lock:
            if self.freqs is None or self.freqs.size != f.size or \
                    abs(float(self.freqs[0]) - float(f[0])) > 1 or \
                    abs(float(self.freqs[-1]) - float(f[-1])) > 1:
                self.freqs = np.array(f, float)
                self.mx = np.array(r.power, float)
                self.lin = 10.0 ** (np.asarray(pm, float) / 10.0)
                self.n = 1
                return
            np.maximum(self.mx, r.power, out=self.mx)
            self.lin += 10.0 ** (np.asarray(pm, float) / 10.0)
            self.n += 1

    def take(self):
        with self._lock:
            if self.freqs is None or self.n == 0:
                return None
            out = (self.freqs.copy(), self.mx.copy(),
                   10.0 * np.log10(np.maximum(self.lin / self.n, 1e-30)), self.n)
        self.reset()
        return out


def measure(freqs, mean_dbm, peak_dbm, rbw, lo=None, hi=None):
    """(band_dbm, lo, hi, peak_hz, peak_dbm) for one point."""
    f = np.asarray(freqs, float)
    lo = float(f[0]) if lo is None else max(float(lo), float(f[0]))
    hi = float(f[-1]) if hi is None else min(float(hi), float(f[-1]))
    band = None
    if hi > lo:
        mw, _partial = wb.band_power_mw(f, mean_dbm, rbw, lo, hi)
        if mw is not None and mw > 0:
            band = 10.0 * math.log10(mw)
    pk = np.asarray(peak_dbm, float)
    sel = (f >= lo) & (f <= hi) & np.isfinite(pk)
    if sel.any():
        i = int(np.flatnonzero(sel)[np.argmax(pk[sel])])
        phz, pdb = float(f[i]), float(pk[i])
    else:
        phz = pdb = None
    return band, lo, hi, phz, pdb
