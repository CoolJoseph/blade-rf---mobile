"""
Cellular survey: one press samples every cellular DOWNLINK band of the chosen
country, in 100 MHz views, and keeps only what is worth keeping.

How "only what is relevant" is decided -- the logic in one place:

1.  COUNTRY BAND PLAN.  Only bands a country actually uses for cellular
    downlink are visited (base stations transmit continuously on the
    downlink; the uplink is phones, bursty and useless for coverage). Each
    country is a list of DL ranges, editable by the user. "All bands
    (discovery)" is the union of every plan, for a first visit to a site.

2.  FEWEST 100 MHz VIEWS.  Bands that fit together inside 100 MHz share one
    view (e.g. B5 869-894 + B8 925-960); a band wider than 100 MHz (n78) is
    cut into consecutive 100 MHz views. Israel = 11 views, not 60.

3.  DETECTION, so nothing active is missed:
      * each view is measured for >= N sweeps with Average AND Max Hold;
      * the receiver noise floor is taken per view, but a view that is FULL
        of signal (a 100 MHz 5G carrier filling a 100 MHz view) is compared
        with the quietest views of the same point, so it is not mistaken
        for "floor";
      * a carrier = a run of bins >= THR dB (default 6) above the floor,
        >= 1 MHz wide (narrow spurs are not cellular), small gaps bridged;
      * a carrier cut by the edge between two views is joined again.

4.  SAVING.  The data of every band is always written -- "no signal at
    700 MHz here" is a result too. A picture is saved only for a view in
    which a carrier was found (unless "save empty views" is on).

5.  LEVELS.  Channel power is integrated over the carrier; an RSRP estimate
    = channel power - 10*log10(sub-carriers) (15 kHz LTE / FDD NR, 30 kHz
    TDD NR, fully loaded cell assumed). Words for non-engineers come from
    the RSRP estimate: strong >= -85, good >= -100, weak >= -110, else
    very weak. Rough, uncalibrated antenna -- for comparing places.
"""
from __future__ import annotations

import math
import os
import time

import numpy as np

# ---------------------------------------------------------------- band plans
# (key, name, DL lo MHz, DL hi MHz, tech)   tech: lte | nr | lte/nr | 2g3g4g
_B = {
    "n71":  ("B71/n71 600", 617, 652, "lte/nr"),
    "b12":  ("B12/B17 700a", 729, 746, "lte"),
    "b13":  ("B13 700c", 746, 756, "lte"),
    "b14":  ("B14 FirstNet", 758, 768, "lte"),
    "b28":  ("B28/n28 700", 758, 803, "lte/nr"),
    "b28e": ("B28/n28 700 (EU)", 758, 788, "lte/nr"),
    "b20":  ("B20 800", 791, 821, "lte"),
    "b5":   ("B5 850", 869, 894, "2g3g4g"),
    "b8":   ("B8 900", 925, 960, "2g3g4g"),
    "b32":  ("B32 L-band 1500 SDL", 1452, 1496, "lte"),
    "b11":  ("B11/B21 1500 (JP)", 1475, 1511, "lte"),
    "b3":   ("B3 1800", 1805, 1880, "2g3g4g"),
    "b2":   ("B2/B25 1900 PCS", 1930, 1995, "lte/nr"),
    "b1":   ("B1 2100", 2110, 2170, "2g3g4g"),
    "b66":  ("B4/B66 AWS", 2110, 2200, "lte/nr"),
    "b40":  ("B40 2300 TDD", 2300, 2400, "lte/nr"),
    "b30":  ("B30 2300 WCS", 2350, 2360, "lte"),
    "b41":  ("B41/n41 2500 TDD", 2496, 2690, "lte/nr"),
    "b38":  ("B38 2600 TDD", 2570, 2620, "lte/nr"),
    "b7":   ("B7 2600", 2620, 2690, "lte"),
    "n78":  ("5G n78 3.5 GHz", 3300, 3800, "nr"),
    "n48":  ("5G n48 CBRS", 3550, 3700, "nr"),
    "n77":  ("5G n77 C-band", 3700, 3980, "nr"),
    "n79":  ("5G n79 4.5 GHz", 4400, 5000, "nr"),
}
COUNTRIES = {
    "IL": {"name": "Israel", "name_he": "ישראל",
           "bands": ["b28", "b5", "b8", "b32", "b3", "b1", "b7", "n78"]},
    "EU": {"name": "Europe (EU/UK)", "name_he": "אירופה",
           "bands": ["b28e", "b20", "b8", "b32", "b3", "b1", "b38", "b7", "n78"]},
    "US": {"name": "United States", "name_he": "ארה״ב",
           "bands": ["n71", "b12", "b13", "b14", "b5", "b2", "b66", "b30", "b41", "n48", "n77"]},
    "ALL": {"name": "All bands (discovery)", "name_he": "כל התחומים (גילוי)",
            "bands": list(_B.keys())},
}
WIN = 100e6


def plan_bands(country: str, disabled=(), custom=()):
    """The bands to visit: the country's list minus the ones the user
    switched off, plus the user's own (name, lo_hz, hi_hz, tech)."""
    keys = COUNTRIES.get(country, COUNTRIES["IL"])["bands"]
    out = []
    for k in keys:
        if k in disabled:
            continue
        n, lo, hi, tech = _B[k]
        out.append({"key": k, "name": n, "lo": lo * 1e6, "hi": hi * 1e6, "tech": tech})
    for c in custom or ():
        try:
            out.append({"key": "c:" + str(c["name"]), "name": str(c["name"]),
                        "lo": float(c["lo"]), "hi": float(c["hi"]),
                        "tech": str(c.get("tech") or "lte/nr")})
        except (KeyError, TypeError, ValueError):
            pass
    out.sort(key=lambda b: b["lo"])
    return out


def all_bands():
    return [{"key": k, "name": v[0], "lo": v[1] * 1e6, "hi": v[2] * 1e6, "tech": v[3]}
            for k, v in _B.items()]


def plan_windows(bands, f_min=70e6, f_max=6000e6, win=WIN):
    """Fewest 100 MHz views covering every band. A group of bands that fits
    in one view is centred in it; a wider band is cut into views."""
    iv = sorted([(max(b["lo"], f_min), min(b["hi"], f_max)) for b in bands if b["hi"] > b["lo"]])
    merged = []
    for lo, hi in iv:                       # overlapping bands -> one range
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    wins = []
    i = 0
    while i < len(merged):
        lo, hi = merged[i]
        if hi - lo > win:                   # wide: consecutive views, centred on it
            n = int(math.ceil((hi - lo) / win - 1e-9))
            a = max(f_min, min((lo + hi) / 2 - n * win / 2, f_max - n * win))
            for k in range(n):
                wins.append((a + k * win, a + (k + 1) * win))
            i += 1
            continue
        j = i
        while j + 1 < len(merged) and merged[j + 1][1] - lo <= win:
            j += 1
        top = merged[j][1]
        c = (lo + top) / 2
        a = max(f_min, min(c - win / 2, f_max - win))
        wins.append((a, a + win))
        i = j + 1
    wins = [(round(a / 1e6) * 1e6, round(a / 1e6) * 1e6 + win) for a, _b in wins]
    return [{"lo": a, "hi": b, "center": (a + b) / 2,
             "bands": [x["key"] for x in bands if x["lo"] < b and x["hi"] > a]}
            for a, b in wins]


# ------------------------------------------------------------- detection
LTE_BW = (1.4e6, 3e6, 5e6, 10e6, 15e6, 20e6)
NR_BW = (5e6, 10e6, 15e6, 20e6, 25e6, 30e6, 40e6, 50e6, 60e6, 70e6, 80e6, 90e6, 100e6)


def _db(mw):
    return 10.0 * math.log10(mw) if mw and mw > 0 else None


def floor_of(mean_dbm):
    """Per-bin floor of one view: a low percentile (most bins of a sparse
    view are noise)."""
    d = np.asarray(mean_dbm, float)
    d = d[np.isfinite(d)]
    return float(np.percentile(d, 20)) if d.size else None


def _smooth(m, n):
    """Running mean over n bins (a modulated carrier is ragged bin to bin)."""
    if n <= 1:
        return m
    good = np.isfinite(m)
    x = np.where(good, m, np.nanmin(m[good]) if good.any() else -200.0)
    k = np.ones(n) / n
    pad = n // 2
    y = np.convolve(np.pad(x, (pad, n - 1 - pad), mode="edge"), k, mode="valid")
    return y


def find_carriers(f, mean_dbm, max_dbm, floor, thr_db=6.0, min_w=1.0e6, gap=1.5e6):
    """Runs of bins >= thr_db above floor (after 1 MHz smoothing), >= min_w
    wide, gaps up to `gap` bridged. Returns dict(lo, hi, width, mean_rise,
    peak_dbm, ia, ib) per run."""
    f = np.asarray(f, float)
    pk = np.asarray(max_dbm if max_dbm is not None else mean_dbm, float)
    if f.size < 4:
        return []
    df = (f[-1] - f[0]) / max(f.size - 1, 1)
    m = _smooth(np.asarray(mean_dbm, float), max(1, int(round(1.0e6 / max(df, 1.0)))))
    on = np.isfinite(m) & (m >= floor + thr_db)
    if not on.any():
        return []
    gap_n = max(1, int(round(gap / max(df, 1.0))))
    runs, i, n = [], 0, f.size
    while i < n:
        if not on[i]:
            i += 1
            continue
        j = i
        while True:
            k = j + 1
            while k < n and not on[k] and k - j <= gap_n:
                k += 1
            if k < n and on[k] and k - j <= gap_n:
                j = k
                continue
            break
        runs.append((i, j))
        i = j + 1
    raw = np.asarray(mean_dbm, float)
    out = []
    for a, b in runs:
        lo, hi = f[a] - df / 2, f[b] + df / 2
        if hi - lo < min_w:
            continue
        # smoothing widens a strong NARROW signal to ~1 MHz: require real
        # occupied bandwidth in the unsmoothed data too
        occ = np.count_nonzero(np.isfinite(raw[a:b + 1]) & (raw[a:b + 1] >= floor + thr_db / 2)) * df
        if occ < 0.7 * min_w:
            continue
        seg = m[a:b + 1]
        out.append({"lo": float(lo), "hi": float(hi), "width": float(hi - lo),
                    "mean_rise": float(np.nanmean(seg) - floor),
                    "peak_dbm": float(np.nanmax(pk[a:b + 1])),
                    "ia": int(a), "ib": int(b)})
    return out


def channel_power_dbm(f, mean_dbm, rbw, lo, hi):
    """Integrated power over [lo, hi] from a per-RBW trace (dBm)."""
    f = np.asarray(f, float)
    m = np.asarray(mean_dbm, float)
    sel = (f >= lo) & (f <= hi) & np.isfinite(m)
    if not sel.any():
        return None
    df = (f[-1] - f[0]) / max(f.size - 1, 1)
    mw = np.sum(10 ** (m[sel] / 10.0)) * (df / max(rbw, 1.0))
    return _db(mw)


def classify(width, band_tech, band_lo):
    """Tech and nominal bandwidth from the measured width. The measured
    width of a carrier (with its skirts) is close to the nominal channel:
    LTE 20 MHz measures ~19-22 MHz, so 5G is assumed only above 24 MHz
    (no LTE carrier is that wide) or in a 5G-only band."""
    occ = width
    nr_only = band_tech == "nr"
    if nr_only or occ > 24e6:
        bw = min(NR_BW, key=lambda b: abs(b - occ))
        tech = "5G NR"
    else:
        bw = min(LTE_BW, key=lambda b: abs(b - occ))
        tech = "LTE" if band_tech == "lte" else ("LTE/NR" if band_tech == "lte/nr" else
                                                ("UMTS/LTE" if bw == 5e6 else "LTE"))
    tdd = nr_only and band_lo >= 3e9
    scs = 30e3 if tdd else 15e3
    nsc = max(12, int(round(0.9 * bw / scs)))
    return tech, bw, nsc


def level_word(rsrp):
    if rsrp is None:
        return "none"
    if rsrp >= -85:
        return "strong"
    if rsrp >= -100:
        return "good"
    if rsrp >= -110:
        return "weak"
    return "very_weak"


def analyse_view(f, mean_dbm, max_dbm, rbw, bands, floor, thr_db=6.0):
    """Carriers of one view, assigned to the bands that lie in it."""
    # in a 5G-only band one wide carrier has deep dips: bridge wider gaps
    gap = 5e6 if bands and all(b["tech"] == "nr" for b in bands) else 1.5e6
    found = find_carriers(f, mean_dbm, max_dbm, floor, thr_db, gap=gap)
    res = []
    for c in found:
        cf = (c["lo"] + c["hi"]) / 2
        band = next((b for b in bands if b["lo"] <= cf <= b["hi"]), None)
        if band is None:
            continue                         # outside every cellular band
        ch = channel_power_dbm(f, mean_dbm, rbw, c["lo"], c["hi"])
        tech, bw, nsc = classify(c["width"], band["tech"], band["lo"])
        rsrp = None if ch is None else ch - 10 * math.log10(nsc)
        res.append({"band": band["key"], "_btech": band["tech"], "center": cf, "lo": c["lo"], "hi": c["hi"],
                    "width": c["width"], "bw": bw, "tech": tech,
                    "ch_dbm": ch, "rsrp": rsrp, "snr": c["mean_rise"],
                    "peak_dbm": c["peak_dbm"], "level": level_word(rsrp)})
    return res


def join_edges(carriers, edges, tol=1.2e6):
    """A carrier cut by the boundary between two consecutive views appears
    twice; join the halves (powers add)."""
    cs = sorted(carriers, key=lambda c: c["lo"])
    out = []
    for c in cs:
        if out:
            p = out[-1]
            if p["band"] == c["band"] and abs(c["lo"] - p["hi"]) <= tol and \
                    any(abs(e - p["hi"]) <= tol for e in edges):
                mw = sum(10 ** (x / 10) for x in (p["ch_dbm"], c["ch_dbm"]) if x is not None)
                width = c["hi"] - p["lo"]
                p.update(hi=c["hi"], width=width, center=(p["lo"] + c["hi"]) / 2,
                         peak_dbm=max(p["peak_dbm"], c["peak_dbm"]),
                         snr=max(p["snr"], c["snr"]))
                p["ch_dbm"] = _db(mw)
                tech, bw, nsc = classify(width, p.get("_btech", "nr"), p["lo"])
                p.update(tech=tech, bw=bw)
                p["rsrp"] = None if p["ch_dbm"] is None else p["ch_dbm"] - 10 * math.log10(nsc)
                p["level"] = level_word(p["rsrp"])
                continue
        out.append(dict(c))
    return out


def summarise(bands, carriers):
    """Per band: active?, carriers, the strongest one."""
    out = []
    for b in bands:
        cs = sorted([c for c in carriers if c["band"] == b["key"]],
                    key=lambda c: -(c["rsrp"] if c["rsrp"] is not None else -999))
        best = cs[0] if cs else None
        out.append({"key": b["key"], "name": b["name"], "lo": b["lo"], "hi": b["hi"],
                    "tech": b["tech"], "active": bool(cs), "n": len(cs),
                    "carriers": cs,
                    "best_rsrp": best["rsrp"] if best else None,
                    "best_ch_dbm": best["ch_dbm"] if best else None,
                    "best_center": best["center"] if best else None,
                    "best_tech": best["tech"] if best else None,
                    "level": best["level"] if best else "none"})
    return out


def safe_name(s, n=60):
    s = "".join(ch for ch in str(s) if ch not in '\\/:*?"<>|').strip()
    return (s or "Site")[:n]


def stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def next_point_dir(site_dir, label):
    """'<label> N' with the next free N."""
    os.makedirs(site_dir, exist_ok=True)
    n = 1
    for d in os.listdir(site_dir):
        parts = d.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].isdigit():
            n = max(n, int(parts[1]) + 1)
    return n, os.path.join(site_dir, f"{label} {n}")
