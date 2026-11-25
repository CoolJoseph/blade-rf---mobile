"""
Back-end self-test: runs the phone engine on the simulator, headless, and
checks every API the app uses. No phone, no radio, no Android needed.

    python tests/test_backend.py          (needs numpy; Python 3.10+)
"""
import json
import os
import shutil
import struct
import sys
import tempfile
import time
import urllib.request as U

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, "android", "app", "src", "main", "python")
WEB = os.path.join(ROOT, "android", "app", "src", "main", "assets", "webui")
sys.path.insert(0, PY)
import mobile_app as m  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not cond:
        fails.append(name)


tmp = tempfile.mkdtemp(prefix="bladerf_mobile_test_")
port = m.start_server(tmp, WEB, 0, "", True)
base = f"http://127.0.0.1:{port}"


def get(p):
    return json.load(U.urlopen(base + p))


def post(op, o=None):
    req = U.Request(base + "/api/" + op, data=json.dumps(o or {}).encode(), method="POST")
    return json.load(U.urlopen(req))


try:
    time.sleep(2.5)
    st = get("/api/state")
    check("state answers", st["source"]["sim"] is True)
    check("default presets loaded", len(st["presets"]) >= 10, f"{len(st['presets'])}")
    check("UI page served", b"bladeRF" in U.urlopen(base + "/").read())

    raw = U.urlopen(base + "/api/frame?since=-1").read()
    hl = struct.unpack("<I", raw[:4])[0]
    h = json.loads(raw[4:4 + hl])
    n = h["n"]
    check("frame header aligned", (4 + hl) % 8 == 0)
    want = 4 + hl + 8 * n + 4 * n * sum(h["has"]) + (4 * n if h["wf"] else 0)
    check("frame length matches header", len(raw) == want, f"{len(raw)} vs {want}")
    check("simulator DUT carrier at 2.45 GHz, -30 dBm",
          abs(h["peak_hz"] - 2.45e9) < 50e3 and abs(h["peak_dbm"] + 30) < 1.0,
          f"{h['peak_hz']/1e6:.4f} MHz {h['peak_dbm']:.2f} dBm")
    since = h["seq"]
    r = U.urlopen(base + f"/api/frame?since={since}")
    check("no-new-frame answers 204 or a newer frame", r.status in (200, 204))

    # tune by start/stop and see it land
    post("set", {"start": 430e6, "stop": 440e6})
    time.sleep(1.5)
    st = get("/api/state")
    check("start/stop tuning", abs(st["settings"]["center"] - 435e6) < 1 and
          abs(st["settings"]["span"] - 10e6) < 1)
    raw = U.urlopen(base + "/api/frame?since=-1").read()
    h = json.loads(raw[4:4 + struct.unpack("<I", raw[:4])[0]])
    check("433.92 MHz ISM carrier found after retune",
          abs(h["peak_hz"] - 433.92e6) < 20e3, f"{h['peak_hz']/1e6:.4f} MHz")

    # clamps at the radio's edges
    post("set", {"center": 10e9, "span": 100e6})
    st = get("/api/state")
    check("centre clamped inside 6 GHz", st["settings"]["center"] + 50e6 <= 6e9 + 1)

    # presets: add, update, move, apply, delete, export/import, reset
    r = post("preset", {"op": "add", "name": "Test", "name_he": "בדיקה",
                        "start": 900e6, "stop": 920e6, "rbw": 30e3})
    p = r["presets"][-1]
    check("preset added", p["name"] == "Test" and p["name_he"] == "בדיקה" and p["rbw"] == 30e3)
    post("preset", {"op": "update", "id": p["id"], "stop": 930e6})
    post("preset", {"op": "move", "id": p["id"], "by": -1})
    pr = get("/api/state")["presets"]
    check("preset updated and moved", pr[-2]["id"] == p["id"] and pr[-2]["stop"] == 930e6)
    post("preset", {"op": "apply", "id": p["id"]})
    s = get("/api/state")["settings"]
    check("preset applied (range + RBW)", abs(s["center"] - 915e6) < 1 and s["rbw"] == 30e3
          and s["rbw_auto"] is False)
    ex = post("presets_export")
    check("presets exported", os.path.isfile(ex["path"]))
    n_before = len(get("/api/state")["presets"])
    im = post("presets_import", {"text": open(ex["path"], encoding="utf-8").read()})
    check("presets imported", im["n"] == n_before and len(im["presets"]) == 2 * n_before)
    post("preset", {"op": "reset"})
    check("presets reset to defaults", len(get("/api/state")["presets"]) == len(m.DEFAULT_PRESETS))
    check("presets survive on disk", os.path.isfile(os.path.join(tmp, "presets.json")))

    # signals + identify on the 2.4 GHz scene
    wifi = next(q for q in get("/api/state")["presets"] if q["name"].startswith("WiFi / BT 2.4"))
    post("preset", {"op": "apply", "id": wifi["id"]})
    post("set", {"traces": ["Live", "Max Hold", "Off", "Off"]})
    time.sleep(3)
    sg = get("/api/signals")
    hz = [round(i["hz"] / 1e6) for i in sg["items"]]
    check("signal list finds the 2450 MHz carrier", 2450 in hz, str(hz[:8]))
    check("signal list finds the WiFi channel", any(i["width"] > 15e6 for i in sg["items"]))
    idf = get("/api/identify?hz=2437e6")
    check("identify: 20 MHz channel at 2437 MHz",
          idf.get("carrier") and abs(idf["channel"] - 20e6) < 1, idf.get("summary", ""))

    # traces, restart, single, run
    post("set", {"traces": ["Live", "Max Hold", "Average", "Min Hold"], "avg_count": 5})
    time.sleep(1)
    raw = U.urlopen(base + "/api/frame?since=-1").read()
    h = json.loads(raw[4:4 + struct.unpack("<I", raw[:4])[0]])
    check("four traces delivered", h["has"] == [True, True, True, True])
    check("restart", post("restart")["ok"])
    check("single", post("single")["ok"] and get("/api/state")["settings"]["running"] is False)
    post("set", {"running": True})
    check("run again", get("/api/state")["settings"]["running"] is True)

    # captures
    c = post("csv")
    check("CSV written", c["ok"] and os.path.getsize(c["csv"]) > 1000)
    check("XLSX written", bool(c["xlsx"]) and os.path.getsize(c["xlsx"]) > 1000)
    req = U.Request(base + "/api/blob?name=shot.png", data=b"\x89PNG....", method="POST")
    check("PNG upload saved", json.load(U.urlopen(req))["ok"])
    check("capture list", len(get("/api/captures")) >= 3)
    check("band plan served", len(get("/api/bands")) > 20)

    # settings persist
    post("set", {"ui": {"lang": "en", "ref": -30}})
    time.sleep(1.5)
    saved = json.load(open(os.path.join(tmp, "settings.json"), encoding="utf-8"))
    check("UI settings persisted", saved["ui"].get("lang") == "en" and saved["ui"].get("ref") == -30)


    # ---------------------------------------------------- map / surveys (v1.1)
    post("set", {"sim_gps": True})
    time.sleep(1.5)
    loc = get("/api/location")
    check("simulated GPS gives a fix", loc and loc["src"] == "sim" and abs(loc["lat"] - 32.08) < 0.05)
    sv_ = post("survey", {"op": "create", "name": "Site A"})
    sid = sv_["id"]
    check("survey created and selected", get("/api/state")["survey"] == sid)
    r = post("point", {"name": "corner"})
    p = r["point"]
    check("point saved with position and levels", p["lat"] is not None and p["band_dbm"] is not None
          and p["peak_dbm"] is not None and p["sim"] is True)
    req = U.Request(base + f"/api/point_jpg?sid={sid}&id={p['id']}", data=b"\xff\xd8jpeg", method="POST")
    check("point JPEG stored", os.path.isfile(json.load(U.urlopen(req))["path"]))
    raw = U.urlopen(base + f"/api/point_data?sid={sid}&id={p['id']}").read()
    hd = json.loads(raw[4:4 + struct.unpack("<I", raw[:4])[0]])
    check("point spectrum stored (traces + mean)", hd["n"] > 100 and hd["has"][-1] is True)
    pm = post("point", {"name": "by hand", "lat": 32.1, "lon": 34.8})["point"]
    check("manual placement", pm["src"] == "manual" and pm["lat"] == 32.1)
    post("set", {"track_interval": 0.5})
    post("track", {"what": "start"})
    time.sleep(4)
    n = post("track", {"what": "stop"})["n"]
    check("walk recorded points", n >= 4, f"{n} points")
    s_ = get("/api/survey?id=" + sid)
    trk = [q for q in s_["points"] if q["kind"] == "track"]
    check("walk points carry track id and band power",
          len(trk) == n and all(q["track"] == s_["tracks"][0]["id"] and q["band_dbm"] is not None for q in trk))
    # The simulator's virtual site makes levels vary with position
    lv = [q["band_dbm"] for q in trk]
    check("levels vary along the walk (simulated site)", max(lv) - min(lv) > 0.2, f"{min(lv):.1f}..{max(lv):.1f}")
    post("survey", {"op": "metric", "id": sid, "kind": "band", "lo": 2.44e9, "hi": 2.46e9})
    p2 = post("point", {"name": "narrow"})["point"]
    check("custom band used for band power", abs(p2["band_lo"] - 2.44e9) < 1 and abs(p2["band_hi"] - 2.46e9) < 1)
    req = U.Request(base + f"/api/base_image?sid={sid}&name=a.png&w=800&h=600", data=b"\x89PNG", method="POST")
    check("area image stored", json.load(U.urlopen(req))["base"]["w"] == 800)
    post("survey", {"op": "calib", "id": sid, "ctrl": [
        {"px": 0, "py": 0, "lat": 32.09, "lon": 34.78}, {"px": 800, "py": 600, "lat": 32.08, "lon": 34.79}]})
    check("calibration stored", len(get("/api/survey?id=" + sid)["base"]["ctrl"]) == 2)
    ex = post("survey", {"op": "export", "id": sid})
    kml = open(ex["kml"], encoding="utf-8").read()
    check("KML export has points and the walk", kml.count("<Placemark>") >= 5 and "<LineString>" in kml)
    check("CSV export", open(ex["csv"], encoding="utf-8-sig").read().count("\n") >= 6)
    post("point_op", {"sid": sid, "id": pm["id"], "what": "rename", "name": "renamed"})
    post("point_op", {"sid": sid, "id": p2["id"], "what": "delete"})
    pts = get("/api/survey?id=" + sid)["points"]
    check("point rename / delete", any(q["name"] == "renamed" for q in pts) and not any(q["id"] == p2["id"] for q in pts))
    raw = U.urlopen(base + "/api/frame?since=-1").read()
    h = json.loads(raw[4:4 + struct.unpack("<I", raw[:4])[0]])
    check("live band power in frame header", h.get("band_dbm") is not None)
    post("set", {"sim_gps": False})

    # v1.2: name/version, recordings, missions
    st = get("/api/state")
    check("app name + version in state", st.get("app_name") == "YT Spectrum Analyzer" and st.get("version") == "1.2")
    post("location", {"lat": 32.1, "lon": 34.8, "acc": 6, "src": "gps"})
    rr = post("record", {"base": "REC test", "name": "Roof", "data": True, "log": True, "map": True,
                         "markers": [{"label": "M1", "hz": 2.45e9, "dbm": -40}]})
    check("record: csv + json written", rr["ok"] and len(rr["files"]) == 2 and all(os.path.isfile(f) for f in rr["files"]))
    csv_txt = open(rr["files"][0], encoding="utf-8").read()
    check("record: csv is geo-tagged", "# latitude,32.1" in csv_txt and "# longitude,34.8" in csv_txt)
    lg = json.load(open(rr["files"][1], encoding="utf-8"))
    check("record: full log content", lg["location"]["lat"] == 32.1 and lg["traces"] and len(lg["freqs_hz"]) > 100
          and lg["markers"][0]["label"] == "M1" and lg["app"] == "YT Spectrum Analyzer")
    check("record: map point with coordinates", rr["point"] and rr["point"]["lat"] == 32.1)
    rr2 = post("record", {"base": "REC only data", "data": True, "log": False, "map": False})
    check("record: data only", rr2["ok"] and len(rr2["files"]) == 1 and rr2["point"] is None)
    ms = post("mission", {"op": "save", "mission": {"name": "Azrieli roof", "presets": ["a", "b"], "dwell": "7",
                                                     "repeat": "continuous", "interval": 30,
                                                     "outputs": {"image": True, "video": True, "video_s": 99}}})["missions"]
    mm = ms[-1]
    check("mission saved + cleaned", mm["name"] == "Azrieli roof" and mm["dwell"] == 7 and mm["repeat"] == "continuous"
          and mm["outputs"]["video_s"] == 30 and mm["id"])
    check("mission listed", any(x["id"] == mm["id"] for x in get("/api/missions")))
    post("mission", {"op": "delete", "id": mm["id"]})
    check("mission deleted", not any(x["id"] == mm["id"] for x in get("/api/missions")))

    # USB fd path without a library does not crash; falls back to simulator
    r = post("usbfd", {"fd": -1, "connect": True})
    check("radio-less connect falls back to simulator", r["ok"] and not r["radio"])
finally:
    m.stop_server()
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
