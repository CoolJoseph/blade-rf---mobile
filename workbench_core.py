#!/usr/bin/env python3
"""
==============================================================================
 bladeRF Workbench  --  single file, nothing else needed
 a spectrum analyzer and a signal generator on one bladeRF
==============================================================================

HOW TO RUN
    py "bladeRF Workbench.py"

That is it. On first run it installs numpy, PyQt5 and pyqtgraph itself.
If no bladeRF is attached it opens on a simulated RF scene, so the whole
interface works with no hardware at all.

WHAT IT DOES
    - live FFT spectrum in dBm, with centre/span/start/stop
    - resolution bandwidth, auto-coupled to span or set by hand, with a
      Gaussian filter shape by default -- the shape a bench analyzer has.
      The RBW in the status bar is the RBW you set: the sweep solves for the
      sample rate that lands on it exactly, and says so plainly on the rare
      setting no rate can reach.
    - reference level and dB/division, like a graticule
    - six traces, each independently Live / Max Hold / Min Hold / Average,
      and each with a View tick that freezes it where it is
    - ten markers: peak search, next peak, delta, a marker put on the centre
      frequency or the centre moved onto the marker, a typed frequency, and
      one click to drop the spare ones on the harmonics of M1
    - spectrogram: frequency up the side, time across, newest at the right
    - zero span: the signal against time, for looking at waveforms directly
    - RX1 / RX2 / both at once -- two probe points with no re-cabling, and in
      Both the two ports are captured in the same instant and drawn together
    - hover any carrier to have it identified: LTE / NR / WiFi / GSM and the
      like, with its channel width, its measured edges and its band, boxed
      on the plot where it was measured
    - Preset: the whole instrument back to its defaults in one click
    - ten preset keys, each holding a whole configuration: one press
      recalls it, the key stays lit while the measurement still matches,
      and a set can be exported to a file and imported on another PC
    - Single and Restart on keys of their own, where a bench analyzer has
      them: one sweep at a time, or throw away everything accumulated and
      start again
    - a damage warning when more than -10 dBm arrives at an RX port. There
      is no mechanical attenuator on this board and no software can add
      one, so all it does is say so -- but it says so before the smoke
    - record the spectrum over time on one key: Start Recording, leave it,
      End Recording. Each run writes a folder holding a video of the window,
      the session's max hold as a spreadsheet, and every recorded sweep --
      built for taking a band away from a customer site
    - freeze a reference trace and view live-minus-reference: the fastest way
      to see what a firmware change did
    - channel power and occupied bandwidth, always averaged or max-held so
      the number is readable, and channel power can sit on its own centre
      frequency instead of following the span
    - a frequency counter with a ppm readout
    - screenshot and CSV capture, timestamped, no dialog in the way
    - spans wider than the radio's 56 MHz are stepped and stitched, so the
      whole 70 MHz - 6 GHz range is reachable
    - if the radio is unplugged mid-sweep it says so and picks up again by
      itself when you plug it back in
    - board health: version, PMIC and RFIC readouts
    - triggered zero-span capture with timestamps and overrun detection,
      master or slave, over the mini_exp[1] line
    - "Save settings for next launch": tick it once and the instrument
      comes back the way it was left

SIGNAL GENERATOR (Signal gen key, then tick "Signal generator")
    The same board transmits while it sweeps, standing in for a Signal
    Hound VSG60A. CW, two-tone, multitone comb, band-limited noise, chirp,
    stepped sweep, pulsed CW, AM/FM/PM, ASK/FSK/PSK/QAM/GMSK off a PRBS,
    and playback of any IQ file this app recorded. Burst mode times
    itself on the FPGA sample counter, so the gap repeats to a sample
    rather than to a millisecond.

    "Signal generator" only opens the panel. "RF OUT" is what transmits.
    Output level is an estimate until calibrated, the same as the RX side.

THE RADIO
    Talks to libbladeRF directly. No SoapySDR, no compiler, no extra package.
    The only prerequisite is the Nuand bladeRF Windows installer, which is
    what puts bladeRF.dll on the machine. If `bladeRF-cli -e info` works,
    this will too.

    One catch worth knowing: the DLL is 64-bit, so Python must be 64-bit as
    well. A 32-bit Python cannot load it -- Windows refuses with error 193.
    This script checks that first and tells you plainly.

AMPLITUDE
    Frequency is accurate out of the box. Absolute dBm is not, and no
    software can fix that: the number depends on your board and cabling.
    Feed in a known level and adjust "Cal offset" until the marker agrees.
    Everything relative -- deltas, harmonic suppression, before/after -- is
    already trustworthy.
==============================================================================
"""

from __future__ import annotations

import base64
import ctypes as C
import json
import os
import re
import struct
import subprocess
import threading
import time
import sys
from ctypes import (POINTER, byref, c_bool, c_char_p, c_float, c_int,
                    c_int64, c_uint, c_uint64, c_void_p)
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime
from typing import Dict, List, Optional, Tuple

APP_NAME = "bladeRF Workbench"
APP_AUTHOR = "Kfir D."      # shown in Help > About, asked for 2026-08-26
# Bump this once per shipped change, at the same time as running
# build\new_version.py. See build_id() below for the half of the version that
# maintains itself.
#
# 2026-09-14: this is the first OFFICIAL release. Kfir's words: "I want to
# set the product to an official version, call it 'Version 1'". The dated
# revisions that came before it (2026.08.19 through 2026.09.07) were builds,
# not releases, and the scheme changes here from a date to a number for that
# reason -- a date says when it was cut and a number says what it is. The
# next ordinary change is 1.1; a point fix is 1.0.1. Nothing else about the
# versioning moves: build_id() still fingerprints the running source, so a
# forgotten bump is still contradicted by the id beside it.
#
# 2026-09-16: Version 2 work started, so the stamp is "2.0-dev" -- in
# progress, not released. It says so rather than saying 2.0, because a build
# that calls itself 2.0 while Stage A is half measured is a claim nobody
# checked. It becomes "2.0" when the work in "Version 2\VERSION 2.md"
# section 5 is done and measured; an ordinary fix on top of the 1.0 release
# would have been 1.1.
APP_VERSION = "2.0-dev"

# A saved trace is named for what it IS rather than for the program that
# wrote it -- Kfir's own words, 2026-09-03: "bladeRF Spectrum" + date +
# incrementation. Deliberately NOT derived from APP_NAME: a screenshot is a
# picture of the application and carries its name, while a CSV is a
# measurement and carries the measurement's.
CSV_STEM = "bladeRF Spectrum"
# The metadata block starts here, column E in a spreadsheet, one item per row
# going DOWN. See _csv_table for why it is allowed to move right but never
# left. Both saved files -- the csv and the formatted xlsx beside it -- put
# the block in the same place, so a screenshot of one describes the other.
CSV_META_COL = 4

# ---------------------------------------------------------------- recorder
#
# Asked for 2026-09-20, for presale engineers on a customer site: press one
# key, leave it running, press it again, hand somebody the folder. Kfir's
# answer to "how long" was minutes, up to fifteen -- which is what sizes
# everything below. At one sweep a second that is 900 rows: 5 MB on a 20 MHz
# span and 48 MB at full span, so the whole session fits in memory at the
# end and the max hold and the picture are free.
#
# Recordings go in a subfolder of the capture folder rather than beside the
# screenshots, because a recording is a FOLDER and mixing those in with
# loose files makes both harder to find.
REC_DIRNAME = "Recordings"
# One row a second by default. This is the biggest lever on file size and
# nobody surveying a site needs twenty a second; "Every sweep" is offered
# for catching something brief. Ordered slowest-first would put the default
# in the middle of the list, so it is first and the list runs downwards.
REC_INTERVALS = (("1 s", 1.0), ("2 s", 2.0), ("5 s", 5.0), ("10 s", 10.0),
                 ("Every sweep", 0.0))
REC_DEFAULT_INTERVAL = 1.0
# Backstops, not expected to be reached at fifteen minutes. They exist so a
# recording somebody walked away from cannot fill a laptop: it stops itself,
# keeps everything written so far, and SAYS it stopped -- an unattended
# recorder that dies quietly is worse than one that refuses to start.
REC_MAX_SECONDS = 2 * 60 * 60
REC_MAX_BYTES = 2 * 1024 * 1024 * 1024
# A row whose predecessor is more than this far back is marked as following
# a gap, so a stall, a held sweep or an unplugged board is visible in the
# file rather than being smoothed over by the row spacing.
REC_GAP_FACTOR = 3.0
REC_GAP_MIN_S = 2.0
# Little-endian throughout, header and arrays alike. Format 1 wrote this
# header in network (big-endian) order and the arrays little-endian: it
# worked, and it was a trap for anybody writing a reader in MATLAB or C.
# Any explicit byte-order prefix also disables alignment padding, which is
# the other thing the prefix is for -- the native default can pad this to
# 40 bytes and a reader would silently read garbage.
REC_ROW_HEAD = "<dddIB3x"
# Format 2, 2026-09-23. Format 1 stored only the first and last frequency
# of each row, on the assumption that the points between were evenly
# spaced. They are NOT -- measured, rebuilding the axis that way labelled
# levels up to 3.2 kHz off at a 40 MHz span and 5.3 MHz off at full span,
# more than a whole point. So the exact axis now travels with the data
# (flag REC_AXIS), written on the first row and again whenever it changes.
REC_MAGIC = "bladeRF Workbench sweep recording 2"
REC_AXIS = 0x20

# The screen recording, asked for 2026-09-23: "I want you to record the
# actual screen as a video format as well." H.264 in an MP4 through
# Windows' own Media Foundation encoder (Mp4Writer), so it plays in the
# Windows player, drops into PowerPoint, and costs no new dependency.
REC_VIDEO_NAME = "screen recording.mp4"
# Measured on a 1500 x 900 window: 10 fps costs about 12 ms of the GUI
# thread per frame (grab 9.8, encode 2.0), 12% of it. Fewer frames makes a
# live trace look like a slide show; more buys nothing a reader can see.
REC_VIDEO_FPS = 10
# QUALITY-controlled, not bitrate-controlled, and that was learned the hard
# way. A fixed 1 Mb/s looked sharp on a test that swept every third frame,
# and on a realistic one -- the trace redrawn ~20 times a second, as the app
# does at a 100 MHz span -- it smeared every label and lost the graticule:
# the noise floor is redrawn whole on every sweep, and a fixed rate spends
# nearly all of itself on that and starves the text that never moves.
# Quality mode holds the picture instead and spends bits only where it
# changes, so a held sweep or a quiet band costs almost nothing. Measured
# under that realistic motion, 1408 x 864 at 10 fps, per 15 minutes:
#     q25 242 MB (all text readable, fine grid a little soft)
#     q35 264 MB    q42 296 MB    q50 326 MB (crisp to the minor grid)
# The size barely moves with the setting because most of it is the noise,
# which no setting makes cheap. 35 is q25 plus a margin for small fonts on
# a high-DPI screen, for 9% more.
REC_VIDEO_QUALITY = 35
# The fallback, for a Windows whose encoder refuses quality mode: a fixed
# rate generous enough to stay readable under the same motion -- 3 Mb/s at
# that window, measured as sharp as q50 and about as big (356 MB).
REC_VIDEO_BPP = 0.25
REC_VIDEO_MIN_BPS = 1_000_000
# Frames are scaled down to fit inside this. H.264 at 1920 x 1088 is
# universally playable; a 4K window would make a file most players and
# every mail server choke on, for detail nobody reads back.
REC_VIDEO_MAX = (1920, 1088)
# Longest stall the writer covers by repeating the last frame (Mp4Writer).
# Beyond it -- a laptop lid shut mid-recording, say -- filling would mean
# encoding thousands of frames in one tick and freezing the window, so the
# gap is left for the encoder, which shows the next frame early across it.
# The sweeps say the same thing exactly: their rows carry the gap flag.
REC_VIDEO_FILL_MAX_S = 10.0


_BUILD_ID: Optional[str] = None


def build_id() -> str:
    """
    Short fingerprint of the running source, for the title bar.

    The point is that it cannot go stale. APP_VERSION above is bumped by hand
    and could be forgotten; this is the md5 of the file actually executing, so
    two builds are never confused even if they claim the same version. It also
    means a screenshot or a CSV identifies exactly which source produced it,
    which matters when a measurement is being argued about a week later.

    Returns "" rather than raising if neither route works, because a missing
    fingerprint is not worth failing a launch over.
    """
    global _BUILD_ID
    if _BUILD_ID is None:
        _BUILD_ID = ""
        # Frozen build first. There is no .py on disk inside a PyInstaller
        # exe, so the id is stamped in at build time by
        # build\gen_buildstamp.py -- and it is the md5 of the SAME .py, so
        # the standalone, the small launcher and the script all report one
        # number for one source. Hashing sys.executable instead would have
        # cost ~0.25 s of startup on a 120 MB exe AND produced a different
        # number from the other two, which is worse than useless.
        if getattr(sys, "frozen", False):
            try:
                from _rfbw_buildstamp import BUILD_ID as _stamped
                _BUILD_ID = str(_stamped)[:8]
            except Exception:
                _BUILD_ID = ""
        if not _BUILD_ID:
            try:
                import hashlib
                with open(os.path.abspath(__file__), "rb") as fh:
                    _BUILD_ID = hashlib.md5(fh.read()).hexdigest()[:8]
            except Exception:
                _BUILD_ID = ""
    return _BUILD_ID


def version_line() -> str:
    """One line naming this build, for the title bar and every export."""
    bid = build_id()
    return f"v{APP_VERSION}" + (f" (build {bid})" if bid else "")


# Ceiling on one IQ capture: 64 Mi complex64 = 512 MB on disk. Enough for
# 1.09 s at the radio's full 61.44 MS/s, and far more than that at the rates
# firmware work actually uses.
IQ_MAX_SAMPLES = 64 * 1024 * 1024


# ============================================================================
#  Dependencies -- installed on first run so there is no separate setup step
# ============================================================================

def ensure_deps() -> None:
    try:
        import numpy, PyQt5, pyqtgraph          # noqa: F401
        return
    except ImportError:
        pass
    if getattr(sys, "frozen", False):
        # A packaged build carries its own numpy/PyQt5/pyqtgraph, so reaching
        # here means the bundle is damaged -- and pip cannot fix that. Worse,
        # sys.executable IS this application when frozen, so the call below
        # would relaunch the app with pip's arguments rather than install
        # anything. Say what is wrong and stop.
        print("This packaged build is missing part of itself, which "
              "means the install is damaged.\n"
              "Reinstall from the setup package.")
        input("\nPress Enter to close.")
        sys.exit(1)
    print("First run: installing numpy, PyQt5 and pyqtgraph ...")
    print("(one minute or so, only happens once)\n")
    try:
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "--disable-pip-version-check", "--no-warn-script-location",
            "numpy", "PyQt5", "pyqtgraph"])
    except subprocess.CalledProcessError as exc:
        print(f"\nInstall failed (pip exit {exc.returncode}).")
        print("Try running this by hand to see the error:")
        print(f'  "{sys.executable}" -m pip install numpy PyQt5 pyqtgraph')
        input("\nPress Enter to close.")
        sys.exit(1)
    print("\nDone.\n")


ensure_deps()

import numpy as np                                              # noqa: E402
import pyqtgraph as pg                                           # noqa: E402
from PyQt5 import QtCore, QtGui, QtWidgets                              # noqa: E402

pg.setConfigOption("imageAxisOrder", "row-major")
pg.setConfigOptions(antialias=False, useOpenGL=False)


# ============================================================================
#  libbladeRF, through ctypes
#
#  Constants and signatures come from libbladeRF.h. The frequency type is
#  uint64_t, sample rate and bandwidth are unsigned int, gain is int; getting
#  those wrong is the classic ctypes mistake. Calling convention is __cdecl
#  on Windows, hence CDLL rather than WinDLL.
# ============================================================================

SC16_Q11_SCALE = 2048.0          # [-2048, 2048) represents [-1.0, 1.0)
FMT_SC16_Q11 = 0
LAYOUT_RX_X1 = 0
LAYOUT_RX_X2 = 2                 # BLADERF_RX_X2 -- both RX ports at once
# Port selector value meaning "both", mirroring TX_BOTH on the transmit side.
RX_BOTH = 2
GAIN_MGC = 1                     # manual gain control
SERIAL_LEN = 33

# --- section 5 additions. Every value below was read out of the Nuand
# installer's own headers (Program Files\bladeRF\include\{libbladeRF,
# bladeRF2}.h) on this machine, not recalled from memory -- CLAUDE.md's
# first draft of some of these (the SC16_Q11_META/SC8_Q7 format numbers)
# turned out to not match the real header and would have silently streamed
# the wrong sample format.
FMT_SC16_Q11_META = 1
FMT_SC8_Q7        = 3
FMT_SC8_Q7_META   = 4
SC8_Q7_SCALE = 128.0             # [-128, 128) represents [-1.0, 1.0)

# Shortest rendered TX buffer worth streaming from. Several laps of a short
# waveform are laid end to end up to this, so every send is a full block --
# see BladeRF._tx_render_locked() for the measurement behind it.
TX_MIN_RENDER = 1 << 19

META_FLAG_RX_NOW         = 1 << 31
META_FLAG_RX_HW_MINIEXP1 = 1 << 16
META_FLAG_RX_HW_MINIEXP2 = 1 << 17
META_STATUS_OVERRUN      = 1 << 0
META_STATUS_UNDERRUN     = 1 << 1

RETUNE_NOW = 0

TRIG_SIGNAL_J71_4      = 0
TRIG_SIGNAL_J51_1      = 1
TRIG_SIGNAL_MINI_EXP_1 = 2
TRIG_SIGNAL_USER_0     = 128
TRIG_ROLE_DISABLED = 0
TRIG_ROLE_MASTER   = 1
TRIG_ROLE_SLAVE    = 2

LB_NONE      = 0
LB_FIRMWARE  = 1
LB_RFIC_BIST = 9

RX_MUX_BASEBAND         = 0x0
RX_MUX_12BIT_COUNTER    = 0x1
RX_MUX_32BIT_COUNTER    = 0x2
RX_MUX_DIGITAL_LOOPBACK = 0x4

FEATURE_DEFAULT    = 0
FEATURE_OVERSAMPLE = 1

GAIN_DEFAULT        = 0          # board AGC, whatever it defaults to
# GAIN_MGC = 1, above -- kept where it already was, other code refers to it
GAIN_FASTATTACK_AGC = 2
GAIN_SLOWATTACK_AGC = 3
GAIN_HYBRID_AGC     = 4
GAIN_MODE_NAMES = {
    GAIN_DEFAULT: "Default (board AGC)", GAIN_MGC: "Manual",
    GAIN_FASTATTACK_AGC: "Fast-attack AGC", GAIN_SLOWATTACK_AGC: "Slow-attack AGC",
    GAIN_HYBRID_AGC: "Hybrid AGC"}

PMIC_CONFIGURATION = 0
PMIC_VOLTAGE_SHUNT = 1
PMIC_VOLTAGE_BUS   = 2
PMIC_POWER         = 3
PMIC_CURRENT       = 4
PMIC_CALIBRATION   = 5
PMIC_UINT16_REGS = (PMIC_CONFIGURATION, PMIC_CALIBRATION)   # the rest are float

PS_UNKNOWN   = 0
PS_DC_BARREL = 1
PS_USB_VBUS  = 2
POWER_SOURCE_NAMES = {PS_UNKNOWN: "unknown", PS_DC_BARREL: "DC barrel",
                      PS_USB_VBUS: "USB bus"}


# --- phase 2: the transmit side. Values read out of the same installed
# header as the RX ones above (Program Files\bladeRF\include\libbladeRF.h),
# not recalled -- the RX format enums in the first draft of CLAUDE.md were
# wrong, and there is no reason to think TX would have been luckier.
LAYOUT_TX_X1 = 1                 # BLADERF_TX_X1
LAYOUT_TX_X2 = 3                 # BLADERF_TX_X2 -- both ports at once

# Port selector value meaning "both at the same time". 0 and 1 are TX1 and
# TX2; this is deliberately a third value rather than a separate flag, so
# there is exactly one place that says which ports are live.
TX_BOTH = 2
DIR_RX, DIR_TX = 0, 1            # bladerf_direction

META_FLAG_TX_BURST_START      = 1 << 0
META_FLAG_TX_BURST_END        = 1 << 1
META_FLAG_TX_NOW              = 1 << 2
META_FLAG_TX_UPDATE_TIMESTAMP = 1 << 3


def CH_RX(ch: int) -> int:
    return (ch << 1) | 0x0       # BLADERF_CHANNEL_RX(ch)


# Sweep trigger, the way a swept analyzer has it. Free Run takes every
# sweep; Video accepts a sweep only when its detected level crossed a
# threshold, which is the oldest trigger on any analyzer and the only one
# that needs no external wiring.
#
# Deliberately NOT the same thing as the hardware trigger constants below.
# Those arm the FPGA's own trigger over the mini_exp line and belong to
# zero-span triggered capture (section 5.1); this is a decision taken about a
# sweep after it has been measured.
TRIG_FREE_RUN = 0
TRIG_VIDEO = 1
TRIG_MODES = (("Free Run", TRIG_FREE_RUN), ("Video", TRIG_VIDEO))


def CH_TX(ch: int) -> int:
    return (ch << 1) | 0x1       # BLADERF_CHANNEL_TX(ch)


class BladerfRange(C.Structure):
    _fields_ = [("min", c_int64), ("max", c_int64),
                ("step", c_int64), ("scale", c_float)]


class BladerfVersion(C.Structure):
    _fields_ = [("major", C.c_uint16), ("minor", C.c_uint16),
                ("patch", C.c_uint16), ("describe", c_char_p)]


class BladerfMetadata(C.Structure):
    _fields_ = [("timestamp", c_uint64), ("flags", c_uint), ("status", c_uint),
                ("actual_count", c_uint), ("reserved", C.c_uint8 * 32)]


class BladerfTrigger(C.Structure):
    _fields_ = [("channel", c_int), ("role", c_int), ("signal", c_int),
                ("options", c_uint64)]


class BladerfQuickTune(C.Structure):
    """
    Opaque. struct bladerf_quick_tune's real layout is a union that differs
    between board families (bladeRF1 vs bladeRF2) -- libbladeRF.h says so
    explicitly. Handed to the library and back unread and unmodified; 64
    bytes is generous headroom (the real payload measured well under 16
    bytes on a bladeRF 2.0 micro, board+FPGA v2.5.0/2.4.0/0.15.0).
    """
    _fields_ = [("raw", C.c_uint8 * 64)]


# libbladeRF error codes (libbladeRF.h). Worth naming: an earlier version of
# this file guessed -6/-15/-8 for NODEV/PERMISSION/FPGA and every one of those
# was wrong, so the "what to do" hints pointed at the wrong problem.
ERR_UNEXPECTED  = -1
# -3. Worth naming because it is the code the device returns when metadata
# mode is asked for in one direction while the other direction is streaming
# in a plain format -- see Worker._time_domain and CLAUDE.md 5.5 fact 2.
ERR_INVAL       = -3
ERR_IO          = -5
ERR_TIMEOUT     = -6
ERR_NODEV       = -7
ERR_UPDATE_FPGA = -12
ERR_UPDATE_FW   = -13
ERR_PERMISSION  = -17

# A read failing with one of these means the board is physically gone, not
# that the host was slow -- so we can react at once instead of waiting for
# more timeouts.
FATAL_CODES = (ERR_IO, ERR_NODEV)


class RadioError(RuntimeError):
    def __init__(self, message: str, rc: int = 0) -> None:
        super().__init__(message)
        self.rc = rc


def host_bits() -> int:
    return C.sizeof(C.c_void_p) * 8


def pe_machine(path: str) -> Optional[str]:
    """
    Architecture a Windows binary targets, read from its PE header.

    Error 193 means "found it, wrong architecture", and the only way to say
    which side is wrong is to read the header and compare.
    """
    try:
        with open(path, "rb") as fh:
            if fh.read(2) != b"MZ":
                return None
            fh.seek(0x3C)
            fh.seek(int.from_bytes(fh.read(4), "little"))
            if fh.read(4) != b"PE\0\0":
                return None
            m = int.from_bytes(fh.read(2), "little")
    except Exception:
        return None
    return {0x014C: "32-bit", 0x8664: "64-bit", 0xAA64: "64-bit (ARM64)"}.get(
        m, f"unknown (0x{m:04x})")


def _lib_candidates() -> List[str]:
    if sys.platform.startswith("win"):
        names = ["bladeRF.dll", "libbladeRF.dll"]
        dirs = [r"C:\Program Files\bladeRF\x64",
                r"C:\Program Files\bladeRF\x86",
                r"C:\Program Files (x86)\bladeRF\x64",
                r"C:\Program Files\PothosSDR\bin"]
    elif sys.platform == "darwin":
        names = ["libbladeRF.dylib"]
        dirs = ["/usr/local/lib", "/opt/homebrew/lib"]
    else:
        names = ["libbladeRF.so.2", "libbladeRF.so"]
        dirs = ["/usr/lib", "/usr/local/lib", "/usr/lib/x86_64-linux-gnu"]
    out = list(names)
    env = os.environ.get("BLADERF_LIB")
    if env:
        out.insert(0, env)
    for d in dirs:
        for n in names:
            out.append(os.path.join(d, n))
    return out


_LIB = None
_LIB_PATH = ""


def load_libbladerf(explicit: Optional[str] = None):
    global _LIB, _LIB_PATH
    if _LIB is not None and explicit is None:
        return _LIB

    bits = host_bits()
    clash, failed, absent = [], [], []

    for path in ([explicit] if explicit else []) + _lib_candidates():
        if not path:
            continue
        bare = not os.path.dirname(path)
        if not bare and not os.path.isfile(path):
            absent.append(path)
            continue
        try:
            folder = os.path.dirname(path)
            # Python 3.8+ on Windows no longer searches PATH for a DLL's own
            # dependencies, and libbladeRF pulls in libusb.
            if (sys.platform.startswith("win") and folder
                    and os.path.isdir(folder)
                    and hasattr(os, "add_dll_directory")):
                try:
                    os.add_dll_directory(folder)
                except OSError:
                    pass
            lib = C.CDLL(path)
            _bind(lib)
            _bind_optional(lib)
            _LIB, _LIB_PATH = lib, path
            return lib
        except OSError as exc:
            arch = pe_machine(path) if not bare else None
            if getattr(exc, "winerror", None) == 193:
                clash.append((path, arch or "a different architecture"))
            else:
                failed.append(f"  {path}\n      {exc}")
        except AttributeError as exc:
            failed.append(f"  {path}\n      loaded but missing {exc}")

    if clash:
        p, a = clash[0]
        raise RadioError(
            "The bladeRF driver was FOUND but cannot be loaded.\n\n"
            f"    DLL        : {p}\n"
            f"                 {a}\n"
            f"    this Python: {bits}-bit\n"
            f"                 {sys.executable}\n\n"
            "Windows will not load a library into a process of a different\n"
            "architecture -- that is all error 193 means. Nothing is broken.\n\n"
            "Install 64-bit Python and run this script with it:\n"
            "    https://www.python.org/downloads/windows/\n"
            "    pick 'Windows installer (64-bit)'\n\n"
            "Then check with:\n"
            '    py -c "import struct;print(struct.calcsize(chr(80))*8)"')

    msg = ("Could not find the bladeRF driver (libbladeRF).\n\n"
           "It is installed by the Nuand bladeRF Windows installer -- the same\n"
           "library `bladeRF-cli` uses. SoapySDR is NOT needed.\n\n"
           f"This Python is {bits}-bit, so it needs the {bits}-bit DLL.\n\n"
           "  1. Install from nuand.com, then check:  bladeRF-cli -e info\n"
           "  2. Or point straight at the file:\n"
           "     set BLADERF_LIB=C:\\Program Files\\bladeRF\\x64\\bladeRF.dll\n")
    if failed:
        msg += "\nPresent but would not load:\n" + "\n".join(failed)
    if absent:
        msg += "\nNot present (normal for most):\n" + "\n".join(
            f"  {a}" for a in absent)
    raise RadioError(msg)


def _bind(lib) -> None:
    P = POINTER
    lib.bladerf_open.argtypes = [P(c_void_p), c_char_p]
    lib.bladerf_open.restype = c_int
    lib.bladerf_close.argtypes = [c_void_p]
    lib.bladerf_close.restype = None
    lib.bladerf_strerror.argtypes = [c_int]
    lib.bladerf_strerror.restype = c_char_p
    lib.bladerf_get_board_name.argtypes = [c_void_p]
    lib.bladerf_get_board_name.restype = c_char_p
    lib.bladerf_get_serial.argtypes = [c_void_p, c_char_p]
    lib.bladerf_get_serial.restype = c_int
    lib.bladerf_device_speed.argtypes = [c_void_p]
    lib.bladerf_device_speed.restype = c_int
    lib.bladerf_log_set_verbosity.argtypes = [c_int]
    lib.bladerf_log_set_verbosity.restype = None
    lib.bladerf_set_frequency.argtypes = [c_void_p, c_int, c_uint64]
    lib.bladerf_set_frequency.restype = c_int
    lib.bladerf_get_frequency.argtypes = [c_void_p, c_int, P(c_uint64)]
    lib.bladerf_get_frequency.restype = c_int
    lib.bladerf_set_sample_rate.argtypes = [c_void_p, c_int, c_uint, P(c_uint)]
    lib.bladerf_set_sample_rate.restype = c_int
    lib.bladerf_set_bandwidth.argtypes = [c_void_p, c_int, c_uint, P(c_uint)]
    lib.bladerf_set_bandwidth.restype = c_int
    lib.bladerf_set_gain.argtypes = [c_void_p, c_int, c_int]
    lib.bladerf_set_gain.restype = c_int
    lib.bladerf_get_gain.argtypes = [c_void_p, c_int, P(c_int)]
    lib.bladerf_get_gain.restype = c_int
    lib.bladerf_set_gain_mode.argtypes = [c_void_p, c_int, c_int]
    lib.bladerf_set_gain_mode.restype = c_int
    for fn in ("bladerf_get_gain_range", "bladerf_get_frequency_range",
               "bladerf_get_sample_rate_range"):
        f = getattr(lib, fn)
        f.argtypes = [c_void_p, c_int, P(P(BladerfRange))]
        f.restype = c_int
    lib.bladerf_sync_config.argtypes = [c_void_p, c_int, c_int,
                                        c_uint, c_uint, c_uint, c_uint]
    lib.bladerf_sync_config.restype = c_int
    lib.bladerf_enable_module.argtypes = [c_void_p, c_int, c_bool]
    lib.bladerf_enable_module.restype = c_int
    lib.bladerf_sync_rx.argtypes = [c_void_p, c_void_p, c_uint, c_void_p, c_uint]
    lib.bladerf_sync_rx.restype = c_int


# Symbols added after the ones above were first written. Which of these
# exist depends on the installed driver version, so each is looked up on
# its own and recorded in OPT rather than failing the whole load -- section
# 5's UI greys out a control with the reason shown, instead of crashing the
# first time it is used.
OPT: dict = {}


def _opt(lib, name: str, restype, argtypes):
    try:
        f = getattr(lib, name)
    except AttributeError:
        OPT[name] = False
        return None
    f.restype, f.argtypes = restype, argtypes
    OPT[name] = True
    return f


def _bind_optional(lib) -> None:
    P = POINTER
    _opt(lib, "bladerf_version", None, [P(BladerfVersion)])
    _opt(lib, "bladerf_fw_version", c_int, [c_void_p, P(BladerfVersion)])
    _opt(lib, "bladerf_fpga_version", c_int, [c_void_p, P(BladerfVersion)])
    _opt(lib, "bladerf_get_pmic_register", c_int, [c_void_p, c_int, c_void_p])
    _opt(lib, "bladerf_get_rfic_temperature", c_int, [c_void_p, P(c_float)])
    _opt(lib, "bladerf_get_power_source", c_int, [c_void_p, P(c_int)])
    _opt(lib, "bladerf_get_rfic_rssi", c_int,
         [c_void_p, c_int, P(C.c_int32), P(C.c_int32)])
    _opt(lib, "bladerf_get_bias_tee", c_int, [c_void_p, c_int, P(c_bool)])
    _opt(lib, "bladerf_set_bias_tee", c_int, [c_void_p, c_int, c_bool])
    _opt(lib, "bladerf_get_gain_mode", c_int, [c_void_p, c_int, P(c_int)])
    _opt(lib, "bladerf_get_gain_stages", c_int,
         [c_void_p, c_int, P(c_char_p), C.c_size_t])
    _opt(lib, "bladerf_get_gain_stage", c_int,
         [c_void_p, c_int, c_char_p, P(c_int)])
    _opt(lib, "bladerf_set_gain_stage", c_int,
         [c_void_p, c_int, c_char_p, c_int])
    _opt(lib, "bladerf_set_rx_mux", c_int, [c_void_p, c_int])
    _opt(lib, "bladerf_get_rx_mux", c_int, [c_void_p, P(c_int)])
    _opt(lib, "bladerf_is_loopback_mode_supported", c_bool, [c_void_p, c_int])
    _opt(lib, "bladerf_set_loopback", c_int, [c_void_p, c_int])
    _opt(lib, "bladerf_get_loopback", c_int, [c_void_p, P(c_int)])
    _opt(lib, "bladerf_get_quick_tune", c_int,
         [c_void_p, c_int, P(BladerfQuickTune)])
    _opt(lib, "bladerf_schedule_retune", c_int,
         [c_void_p, c_int, c_uint64, c_uint64, P(BladerfQuickTune)])
    _opt(lib, "bladerf_cancel_scheduled_retunes", c_int, [c_void_p, c_int])
    _opt(lib, "bladerf_enable_feature", c_int, [c_void_p, c_int, c_bool])
    _opt(lib, "bladerf_get_feature", c_int, [c_void_p, P(c_int)])
    _opt(lib, "bladerf_trigger_init", c_int,
         [c_void_p, c_int, c_int, P(BladerfTrigger)])
    _opt(lib, "bladerf_trigger_arm", c_int,
         [c_void_p, P(BladerfTrigger), c_bool, c_uint64, c_uint64])
    _opt(lib, "bladerf_trigger_fire", c_int, [c_void_p, P(BladerfTrigger)])
    _opt(lib, "bladerf_trigger_state", c_int,
         [c_void_p, P(BladerfTrigger), P(c_bool), P(c_bool), P(c_bool),
          P(c_uint64), P(c_uint64)])
    # Phase 2. Soft-bound like everything else: an older driver without
    # these greys the generator out with the reason shown, rather than
    # taking the whole app down the first time somebody keys it.
    _opt(lib, "bladerf_sync_tx", c_int,
         [c_void_p, c_void_p, c_uint, c_void_p, c_uint])
    _opt(lib, "bladerf_get_timestamp", c_int, [c_void_p, c_int, P(c_uint64)])
    _opt(lib, "bladerf_get_sample_rate", c_int, [c_void_p, c_int, P(c_uint)])


# ============================================================================
#  Sources.  Everything above works in normalised complex baseband where
#  |x| = 1 is digital full scale; full_scale_dbm turns that into dBm.
# ============================================================================

class Source:
    # True only where there is a physical connector to protect. The damage
    # warning is gated on it because "the Blade senses this high input
    # power" was the request, and a hazard popup over a synthetic scene
    # would be crying wolf about a signal that does not exist.
    real_hardware = False

    name = "base"
    freq_min, freq_max = 70e6, 6e9
    fs_min, fs_max = 0.52e6, 61.44e6
    gain_min, gain_max = 0.0, 60.0

    def __init__(self) -> None:
        self._freq, self._fs, self._gain = 2.44e9, 40e6, 30.0
        self._running = False

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        self.stop()

    def restart_stream(self) -> None:
        """
        Tear the receive stream down and bring it back, keeping everything
        that was set up on the device.

        Nothing to do where there is no stream. See BladeRF's override for
        why this exists at all.
        """
        pass

    def keepalive(self) -> bool:
        """
        Claim a small block so an idle stream keeps turning. See
        Worker._idle_keepalive().

        Returns whether a block was actually READ, because the caller
        treats a successful read as evidence the stream is alive (bug 51)
        and must not treat a decline as the same thing. There is no stream
        here, so nothing is proved: False.
        """
        return False

    @property
    def info(self) -> str:
        return self.name

    @property
    def channel(self) -> int:
        return 0

    @property
    def port(self) -> str:
        return "RX1"

    @property
    def dual_rx(self) -> bool:
        """True when both RX ports are being captured at once."""
        return False

    def read_dual(self, n: int):
        """n samples from both RX ports at the same instant."""
        raise RadioError("this source cannot receive on both ports at once")

    def set_channel(self, index: int) -> None:
        pass

    def set_freq(self, hz, discard: bool = True):
        self._freq = float(np.clip(hz, self.freq_min, self.freq_max))
        return self._freq

    def set_sample_rate(self, hz):
        self._fs = float(np.clip(hz, self.fs_min, self.fs_max))
        return self._fs

    def set_gain(self, db):
        self._gain = float(np.clip(db, self.gain_min, self.gain_max))
        return self._gain

    @property
    def sample_rate(self) -> float:
        return self._fs

    @property
    def full_scale_dbm(self) -> float:
        raise NotImplementedError

    def read(self, n: int) -> np.ndarray:
        raise NotImplementedError

    @property
    def usable_bandwidth(self) -> float:
        # Outer edges of any SDR's analog filter roll off, so stitched sweeps
        # only keep the trustworthy middle.
        return self._fs * 0.75

    # -- section 5: board health / self-test. Base defaults are all "not
    # available", so the UI can grey a control out from one check regardless
    # of which Source subclass is active, without an isinstance() per call.
    def lib_version(self) -> str: return "?"
    def fw_version(self) -> str: return "?"
    def fpga_version(self) -> str: return "?"
    def rfic_temperature(self) -> Optional[float]: return None
    def power_source(self) -> Optional[int]: return None
    def pmic(self, reg: int) -> Optional[float]: return None
    def rfic_rssi(self) -> Optional[Tuple[float, float]]: return None
    def get_bias_tee(self) -> Optional[bool]: return None
    def set_bias_tee(self, on: bool) -> None:
        raise RadioError("no bias tee on this source")
    def get_gain_mode(self) -> Optional[int]: return None
    def set_gain_mode(self, mode: int) -> None: pass
    def gain_stages(self) -> List[str]: return []
    def get_gain_stage(self, stage: str) -> Optional[int]: return None
    def set_gain_stage(self, stage: str, db: int) -> None: pass
    def loopback_supported(self, mode: int) -> bool: return False
    def set_loopback(self, mode: int) -> None:
        raise RadioError("no loopback control on this source")
    def get_loopback(self) -> Optional[int]: return None
    def counter_self_test(self, n_samples: int = 65536) -> Tuple[int, int]:
        raise RadioError("no digital-path self-test on this source")

    # -- section 5.1: triggered capture and timestamps ---------------------
    def trigger_init(self, signal: int) -> None:
        raise RadioError("no trigger support on this source")
    def trigger_arm(self, role: int) -> None:
        raise RadioError("no trigger support on this source")
    def trigger_fire(self) -> None:
        raise RadioError("no trigger support on this source")
    def trigger_state(self) -> Optional[Tuple[bool, bool, bool]]: return None

    def read_meta(self, n: int, rx_now: bool = True, timeout_ms: int = 300):
        """(iq, timestamp, overrun, actual_count, miniexp1, miniexp2)."""
        raise RadioError("no timestamped capture on this source")

    def _set_format_lazy(self, fmt: int) -> None:
        """Record the stream format to use next start(), without starting
        anything -- a no-op on any source without separate stream formats."""
        pass

    # -- section 5.2: quick tune and oversample -----------------------------
    def get_quick_tune_bytes(self) -> bytes:
        raise RadioError("no quick tune on this source")

    def schedule_retune(self, freq_hz: float, qt_bytes: bytes) -> float:
        raise RadioError("no quick tune on this source")

    def cancel_scheduled_retunes(self) -> None:
        pass

    def enable_oversample(self, on: bool) -> None:
        raise RadioError("no oversample feature on this source")

    # -- signal generator: the TX side ------------------------------------
    # Defaults are the bladeRF 2.0 micro's published TX numbers; a real
    # BladeRF replaces them by asking the board, since the ranges differ
    # between board revisions and move with the oversample feature.
    tx_freq_min, tx_freq_max = 47e6, 6e9
    tx_fs_min, tx_fs_max = 0.52e6, 61.44e6
    tx_gain_min, tx_gain_max = -23.75, 66.0

    @property
    def tx_supported(self) -> bool:
        return False

    @property
    def tx_port(self) -> str:
        return "TX1"

    @property
    def tx_reason(self) -> str:
        """Why the generator cannot run, for the UI to show. Empty = fine."""
        return "this source has no transmitter"

    def tx_set_channel(self, index: int) -> None:
        raise RadioError("no transmitter on this source")

    def tx_set_freq(self, hz) -> float:
        raise RadioError("no transmitter on this source")

    def tx_set_sample_rate(self, hz) -> float:
        raise RadioError("no transmitter on this source")

    def tx_set_bandwidth(self, hz) -> float:
        raise RadioError("no transmitter on this source")

    def tx_set_gain(self, db) -> float:
        raise RadioError("no transmitter on this source")

    @property
    def tx_sample_rate(self) -> float:
        return 0.0

    @property
    def tx_full_scale_dbm(self) -> float:
        """dBm out for a digital full-scale sine at the current gain."""
        return TX_FS0_DBM

    def tx_start(self) -> None:
        raise RadioError("no transmitter on this source")

    def tx_stop(self) -> None:
        pass

    def tx_force_off(self) -> None:
        """
        Assert the transmitter is off, whatever this object believes.

        Distinct from tx_stop(), which is a no-op when THIS object thinks it
        is not streaming. That belief is worthless about hardware we have
        just been handed: see BladeRF.tx_force_off().
        """
        self.tx_stop()

    # Dither before the converter, on by default. A property of the SOURCE
    # rather than of the waveform, because it is applied where the samples
    # are rounded into fixed point and one LSB is a different size at 12 bits
    # and at 8. See SG_DITHER_NOTE and BladeRF._tx_render_locked().
    tx_dither = True

    def tx_set_dither(self, on: bool) -> None:
        self.tx_dither = bool(on)

    def tx_set_loop(self, loop: np.ndarray) -> None:
        """
        Hand over the complete waveform once, when it is rebuilt.

        Real hardware renders it to fixed point here so the streaming path
        never has to convert anything (see BladeRF.tx_set_loop -- that is
        what keeps the transmitter fed). The simulated source needs the
        whole period for a different reason: it plays the loop on its own
        clock to feed the received scene.
        """
        self._tx_loop_ref = np.asarray(loop, dtype=np.complex64)

    def tx_write_loop(self, pos: int, n: int, timeout_ms: int = 2000) -> int:
        """Stream n samples of the loop from pos, wrapping. Returns the
        position for the next call. Generic fallback -- BladeRF overrides it
        with a version that copies nothing."""
        loop = getattr(self, "_tx_loop_ref", None)
        if loop is None or loop.size == 0:
            raise RadioError("no waveform loaded for the transmitter")
        total = loop.size
        n = int(max(n, 1))
        pos = int(pos) % total
        end = pos + n
        if end <= total:
            blk = loop[pos:end]
        else:
            # Wrap, possibly more than once if the caller asked for more than
            # a whole lap. Sending exactly n either way keeps the caller's
            # sample accounting honest.
            laps = -(-end // total)
            blk = np.tile(loop, laps)[pos:end]
        self.tx_write(np.ascontiguousarray(blk), timeout_ms)
        return end % total

    def tx_write(self, iq: np.ndarray, timeout_ms: int = 2000) -> bool:
        """Stream one block. Returns True if the hardware reported an
        underrun, i.e. the host did not keep up."""
        raise RadioError("no transmitter on this source")

    def tx_burst(self, iq: np.ndarray, at_ts: Optional[int] = None,
                 timeout_ms: int = 2000) -> Optional[int]:
        """One self-contained burst, keyed on and off around the samples.
        Returns the timestamp the burst ended at, or None."""
        raise RadioError("no timed bursts on this source")

    def tx_timestamp(self) -> Optional[int]:
        return None

    def tx_get_bias_tee(self) -> Optional[bool]:
        return None

    def tx_set_bias_tee(self, on: bool) -> None:
        raise RadioError("no bias tee on this source")

    def tx_trigger_init(self, signal: int) -> None:
        raise RadioError("no trigger support on this source")

    def tx_trigger_arm(self, role: int) -> None:
        raise RadioError("no trigger support on this source")

    def tx_trigger_fire(self) -> None:
        raise RadioError("no trigger support on this source")


class BladeRF(Source):
    """The real radio."""

    real_hardware = True
    name = "bladeRF"

    def __init__(self, channel: int = 0, lib_path: Optional[str] = None,
                 fs0_dbm: float = 0.0) -> None:
        super().__init__()
        self.lib = load_libbladerf(lib_path)
        self.lib.bladerf_log_set_verbosity(4)     # errors only
        self._ch_index = int(channel)
        self._ch = CH_RX(self._ch_index)
        self._dev = c_void_p()
        self._fs0 = fs0_dbm
        rc = self.lib.bladerf_open(byref(self._dev), None)
        if rc != 0:
            raise RadioError(self._open_error(rc))
        self._streaming = False
        self._buf: Optional[np.ndarray] = None
        self._fmt = FMT_SC16_Q11
        self._oversample = False
        # "Do nothing if it is already that" caches for the two per-segment
        # calls, added 2026-08-31. See set_gain() and set_freq() for what
        # each holds and why, and set_channel() for why both are dropped on
        # a port change rather than trusted across one.
        self._gain_set: Optional[Tuple[float, float, bool]] = None
        self._freq_seen: Dict[float, float] = {}
        self._trig: Optional[BladerfTrigger] = None
        self._last_ts: Optional[int] = None
        self._last_count = 0

        # Manual gain: an analyzer needs the gain the user set, not one an AGC
        # keeps moving underneath the measurement.
        try:
            self.lib.bladerf_set_gain_mode(self._dev, self._ch, GAIN_MGC)
        except Exception:
            pass

        for attr, fn in (("freq", "bladerf_get_frequency_range"),
                         ("fs", "bladerf_get_sample_rate_range"),
                         ("gain", "bladerf_get_gain_range")):
            try:
                lo, hi = self._range(fn)
                setattr(self, f"{attr}_min", lo)
                setattr(self, f"{attr}_max", hi)
            except RadioError:
                pass

        self.board = self._str(self.lib.bladerf_get_board_name)
        self.serial = self._serial()
        self.usb = {1: "USB 2.0", 2: "USB 3.0"}.get(
            self.lib.bladerf_device_speed(self._dev), "USB ?")
        self.name = f"{self.board} {self.serial[:8]}"
        # Settling allowance after a retune. Wide stepped sweeps live or die
        # on this number; raise it if segment edges look smeared.
        self.tune_settle_s = 0.0015

        # TX state first, then assert the transmitter is OFF -- BEFORE the
        # three writes below, because set_sample_rate() reprograms the shared
        # baseband PLL and doing that with a leftover TX channel enabled is
        # exactly what put a burst on the air. See tx_force_off().
        self._tx_state_init()
        self.tx_force_off()
        self._freq = self.set_freq(self._freq)
        self._fs = self.set_sample_rate(self._fs)
        self._gain = self.set_gain(self._gain)

    # -- helpers ---------------------------------------------------------
    def _err(self, rc: int) -> str:
        try:
            return self.lib.bladerf_strerror(rc).decode()
        except Exception:
            return f"error {rc}"

    def _open_error(self, rc: int) -> str:
        hint = {
            ERR_NODEV: "\n\nNo bladeRF found. Check the USB cable, and that\n"
                       "`bladeRF-cli -e info` sees the board.",
            ERR_IO: "\n\nI/O error reaching the board -- usually a cable or\n"
                    "port problem. Try a different USB port.",
            ERR_PERMISSION: "\n\nPermission denied -- the driver may not be\n"
                            "installed. Re-run the Nuand installer.",
            ERR_UPDATE_FPGA: "\n\nThe FPGA image needs loading:\n"
                             "  bladeRF-cli -l <image.rbf>",
            ERR_UPDATE_FW: "\n\nThe firmware needs updating -- see the Nuand\n"
                           "instructions for bladeRF-cli -f.",
        }.get(rc, "")
        return f"Could not open the bladeRF: {self._err(rc)} ({rc}){hint}"

    def _str(self, fn) -> str:
        try:
            return fn(self._dev).decode()
        except Exception:
            return "bladeRF"

    def _serial(self) -> str:
        buf = C.create_string_buffer(SERIAL_LEN + 1)
        if self.lib.bladerf_get_serial(self._dev, buf) == 0:
            return buf.value.decode(errors="replace")
        return "?"

    def _range(self, fn_name: str,
               channel: Optional[int] = None) -> Tuple[float, float]:
        ch = self._ch if channel is None else channel
        ptr = POINTER(BladerfRange)()
        rc = getattr(self.lib, fn_name)(self._dev, ch, byref(ptr))
        if rc != 0 or not ptr:
            raise RadioError(f"{fn_name}: {self._err(rc)}")
        r = ptr.contents
        s = float(r.scale) if r.scale else 1.0
        return r.min * s, r.max * s

    @property
    def channel(self) -> int:
        return self._ch_index

    @property
    def port(self) -> str:
        return "RX1+RX2" if self._ch_index == RX_BOTH \
            else f"RX{self._ch_index + 1}"

    @property
    def info(self) -> str:
        return f"{self.board}  sn {self.serial[:8]}  {self.usb}  {self.port}"

    def set_channel(self, index: int) -> None:
        """
        Move to the other RX port.

        A x1 stream carries whichever single RX channel is enabled, so the
        switch is: disable the current front end, re-apply the settings to the
        new channel, then re-arm the stream. Note that on the bladeRF 2.0 both
        RX channels share one LO, so tuning is common to both -- only the port
        (and its gain) changes.
        """
        index = int(index) if int(index) in (0, 1, RX_BOTH) else 0
        if index == self._ch_index:
            return
        was_streaming = self._streaming
        if self._streaming:
            self.stop()
        self._ch_index = index
        # In x2 the "current" channel for per-channel settings is RX1; gain
        # is applied to both explicitly in set_gain().
        self._ch = CH_RX(0 if index == RX_BOTH else index)
        # Drop both "already set" caches BEFORE re-applying anything. The
        # three calls below hand back the values already in hand, which is
        # precisely what a skip-if-unchanged cache swallows -- and gain is
        # genuinely per-channel on this board, so a swallowed re-apply
        # leaves the new front end on whatever the RFIC was holding and
        # every level measured through it is wrong with nothing to say so.
        #
        # The frequency readback is per-channel in the API too, so a cached
        # answer from the old channel is not evidence about the new one,
        # even though both RX channels share one LO here.
        self._gain_set = None
        self._freq_seen.clear()
        self._fs_pair = None
        # Ranges and settings are per-channel in the API, so re-apply them.
        self.set_sample_rate(self._fs)
        self.set_freq(self._freq)
        self.set_gain(self._gain)
        if was_streaming:
            self.start()

    @property
    def dual_rx(self) -> bool:
        return self._ch_index == RX_BOTH

    # -- settings --------------------------------------------------------
    def set_freq(self, hz, discard: bool = True):
        """
        discard=False skips the post-retune settle flush. The stepped
        spectrum sweep needs that flush for FFT accuracy; zero span does
        not need the same guarantee and must skip it when a trigger might
        be armed -- the flush goes through the plain SC16_Q11 read() path,
        which resets the stream format and, as a side effect of stop()'s
        own safety check, silently disarms whatever was just armed. That
        combination is what caused a real reproducible hang: the discard's
        read() call legitimately times out waiting for a slave trigger's
        gate (which it just tore down without meaning to), the trigger
        ends up disarmed without Worker's bookkeeping knowing it happened,
        and closing the app while that stale state is unwound can wedge
        the stream.
        """
        f = float(np.clip(hz, self.freq_min, self.freq_max))
        rc = self.lib.bladerf_set_frequency(self._dev, self._ch, c_uint64(int(f)))
        if rc != 0:
            raise RadioError(f"set frequency: {self._err(rc)} ({rc})", rc)
        # The READBACK is cached, not the tune itself. bladerf_set_frequency
        # has to run every time -- the LO really does have to move, and that
        # PLL relock is 82% of a wide sweep -- but bladerf_get_frequency is a
        # separate USB control transfer asking a question whose answer cannot
        # change for a given request. A stepped sweep visits exactly the same
        # segment centres on every pass, so from the second pass onward there
        # are 206 of these per sweep with nothing to learn from any of them.
        #
        # Keyed on the CLAMPED request and holding the hardware's own answer,
        # which is the same shape as the (request, readback) pair in
        # set_sample_rate and for the same reason: the radio rounds to its
        # own tuning step, so the request alone is not what we need to know.
        #
        # Cleared by set_channel and on reconnect, because the answer is
        # per-channel and per-handle. Deliberately NOT cleared by
        # enable_oversample even though that can move the sample rate
        # (bug 27): what it perturbs is the rate, not the frequency the LO
        # was asked for, and _oversample_inner re-tunes explicitly anyway.
        got = self._freq_seen.get(f)
        if got is None:
            v = c_uint64()
            self.lib.bladerf_get_frequency(self._dev, self._ch, byref(v))
            got = float(v.value)
            # A sweep's segment set is bounded (400 segments, see _sweep) but
            # panning around all day is not, so the cache is capped rather
            # than left to grow for the life of the process.
            if len(self._freq_seen) >= 4096:
                self._freq_seen.clear()
            self._freq_seen[f] = got
        self._freq = got
        if self._running and discard:
            self._discard(self.retune_discard_samples())
        return self._freq

    def retune_discard_samples(self) -> int:
        """
        How many samples to throw away after a retune.

        This has to cover TWO different things, and getting it wrong is what
        produced ghost signals in stitched sweeps:

        1. Samples already digitized and sitting in the USB pipeline when
           the retune command was issued. Those belong to the PREVIOUS LO.
           Measured on Kfir's board at 40 MS/s: content from the old LO
           persisted for exactly 65,536 samples after set_frequency, which
           is precisely RX_NUM_TRANSFERS * RX_BUFFER_SIZE -- the in-flight
           transfer depth, NOT the full num_buffers ring.
        2. Samples digitized after the retune but before the PLL relocked,
           which is a wall-clock time, hence tune_settle_s * fs.

        The old code discarded only tune_settle_s * fs (60,000 samples at
        40 MS/s), which is LESS than the 65,536-sample pipeline. The
        leftover stale samples were then FFT'd and labelled with the new
        centre frequency, so every signal got a copy one segment step
        (fs * 0.75, i.e. 30 MHz at 40 MS/s) away from where it really was:
        a 758 MHz carrier drew a phantom at 788 MHz, only 1-2 dB down.
        That is a duplicate-at-fixed-offset, which is what distinguishes it
        from an IQ image (mirrored about the segment centre) or a real
        harmonic (at an integer multiple).

        Deriving this from the stream constants rather than hard-coding a
        time is deliberate: change the buffering and this follows.
        """
        pipeline = self.RX_NUM_TRANSFERS * self.RX_BUFFER_SIZE
        return int(pipeline + self.tune_settle_s * self._fs)

    def set_sample_rate(self, hz, force: bool = False):
        """
        Set the RX sample rate, and DO NOTHING if it is already that.

        force=True writes it regardless of what we think the rate is. Exactly
        one caller needs that -- _oversample_inner(), where libbladeRF's own
        set_frequency can have moved the rate underneath us, so "we think it
        is already right" is not evidence of anything.

        That guard is not a micro-optimisation. Measured on this board,
        bladerf_set_sample_rate costs 106-120 ms even when handed the value
        the radio is already running at -- compare 5.7 ms for set_gain and
        14.8 ms for a same-frequency set_freq. It reprograms the baseband
        PLL every time it is called.

        _sweep() calls this once per pass, so the analyzer was spending
        ~110 ms of every 169 ms sweep re-programming a PLL to the setting it
        already had: 5.9 sweeps/s where the actual work takes about 59 ms.

        With the generator running it is worse than slow. RX and TX share
        that PLL, so every sweep was disturbing the transmitter, and the
        carrier flickered in and out of the trace. That is the bug behind
        "the carrier is not rock steady".

        The cached value is trustworthy because every path that can change
        the rate goes through one of our own methods -- this one,
        tx_set_sample_rate() (which re-reads RX afterwards, since setting
        one direction drags the other) and enable_oversample() -- and all of
        them store what the hardware actually reported back.
        """
        want = float(int(round(np.clip(hz, self.fs_min, self.fs_max))))
        if not force and self._fs and int(round(want)) == int(round(self._fs)):
            return self._fs
        # Second gate, and it is the one that matters since _plan_rbw() began
        # solving for the rate: the request and the readback are not always
        # the same number, so comparing them alone would miss on every sweep
        # and reprogram the PLL every time. Comparing the PAIR recognises a
        # repeated request while still noticing if anything else moved the
        # rate underneath us -- tx_set_sample_rate() and enable_oversample()
        # both do, and both leave _fs different from the second element.
        pair = getattr(self, "_fs_pair", None)
        if not force and pair is not None and want == pair[0]                 and self._fs == pair[1]:
            return self._fs
        act = c_uint()
        rc = self.lib.bladerf_set_sample_rate(self._dev, self._ch,
                                              c_uint(int(want)), byref(act))
        if rc != 0:
            raise RadioError(f"set sample rate: {self._err(rc)} ({rc})", rc)
        self._fs = float(act.value)
        self._fs_pair = (want, self._fs)
        bw = c_uint()
        self.lib.bladerf_set_bandwidth(self._dev, self._ch,
                                       c_uint(int(self._fs * RX_BW_FRAC)),
                                       byref(bw))
        return self._fs

    def set_gain(self, db, force: bool = False):
        """
        Set the RX gain, and do nothing if it is already that.

        Same argument as set_sample_rate above, and found the same way: by
        timing the call rather than assuming it was cheap. 5.7 ms measured
        on this board, and Worker._sweep() calls it once per pass whether
        anything moved or not. On a full span that is lost in 3.6 s of
        retuning; on a 20 MHz single-segment sweep it was about 10% of the
        whole frame, for no effect whatever.

        The cached value is the hardware's own READBACK, and it is compared
        against the CLAMPED request rather than the raw one -- the clamp is
        what the hardware would have applied anyway, so comparing the raw
        value would miss a repeat every time the caller asked for something
        out of range. Note this is a plain scalar rather than the (request,
        readback) PAIR set_sample_rate needs: the readback here is an int
        the hardware quantises to, so a repeat of the same request lands on
        the same integer, and nothing else in this class moves the gain
        behind our back the way enable_oversample moves the rate (bug 27).

        **The readback is only evidence while a stream is up.** Measured on
        the board 2026-09-02: with no RX module ever enabled,
        bladerf_set_gain returns 0 and bladerf_get_gain answers 60.0 -- the
        top of the range -- whatever was asked for. Three requests of 20, 30
        and 45 dB all read back 60.0, and the value the RFIC actually came
        up with at enable_module was 45, i.e. the last one WRITTEN. So
        before the stream exists the setter is queued and the getter is
        fiction, and believing it put the app 31 dB out (bug 45). Hence: no
        stream, no readback -- keep the request, and cache nothing.

        A readback that disagrees with what was written is not cached
        either, so the next caller retries rather than inheriting a wrong
        value for the life of the session. That is what turned one bad
        readback into a permanent 31 dB error.

        force=True writes regardless, which is what reconnect and start()
        use -- a fresh handle, or one whose stream has just come up, has
        whatever gain the RFIC was left holding, so the cache describes a
        device we are no longer talking to.
        """
        want = float(np.clip(db, self.gain_min, self.gain_max))
        if not force and self._gain_set == (want, self._gain, self.dual_rx):
            return self._gain
        # bladerf_set_gain takes an int, so this is the value the hardware
        # is actually being given, and the value a readback has to match.
        wrote = int(want)
        # Gain is per-channel, so in x2 both front ends have to be set or the
        # two traces would sit at different levels for no visible reason.
        for ch in ((CH_RX(0), CH_RX(1)) if self.dual_rx else (self._ch,)):
            self.lib.bladerf_set_gain(self._dev, ch, c_int(wrote))
        g, trust = float(wrote), False
        if self._streaming:
            v = c_int()
            if self.lib.bladerf_get_gain(self._dev, self._ch, byref(v)) == 0:
                back = float(v.value)
                # Disagreement means the write did not take. Keep what we
                # asked for as the working value -- it is what the RFIC will
                # hold once the stream is up -- and leave the cache empty so
                # the next call tries again.
                trust = abs(back - wrote) <= 0.5
                if trust:
                    g = back
        self._gain = g
        # dual_rx is part of the key: switching to RX1+RX2 has to program the
        # second front end even though the gain itself did not change, or
        # one trace sits at a different level with nothing to explain it.
        self._gain_set = (want, g, self.dual_rx) if trust else None
        return g

    @property
    def full_scale_dbm(self) -> float:
        return self._fs0 - self._gain

    # -- capability / board health (soft-bound, see OPT) ------------------
    def _has(self, name: str) -> bool:
        return OPT.get(name, False)

    def lib_version(self) -> str:
        # No _tx_lock, and this is the one that genuinely does not need it:
        # bladerf_version asks the LIBRARY its version and never touches
        # self._dev, so there is no device call to serialise. The two below
        # do take the handle, and Help > About reads all three from the GUI
        # thread.
        v = BladerfVersion()
        self.lib.bladerf_version(byref(v))
        return f"{v.major}.{v.minor}.{v.patch}"

    def fw_version(self) -> str:
        if not self._has("bladerf_fw_version"):
            return "?"
        v = BladerfVersion()
        with self._tx_lock:
            ok = self.lib.bladerf_fw_version(self._dev, byref(v)) == 0
        return f"{v.major}.{v.minor}.{v.patch}" if ok else "?"

    def fpga_version(self) -> str:
        if not self._has("bladerf_fpga_version"):
            return "?"
        v = BladerfVersion()
        with self._tx_lock:
            ok = self.lib.bladerf_fpga_version(self._dev, byref(v)) == 0
        return f"{v.major}.{v.minor}.{v.patch}" if ok else "?"

    def rfic_temperature(self) -> Optional[float]:
        if not self._has("bladerf_get_rfic_temperature"):
            return None
        v = c_float()
        rc = self.lib.bladerf_get_rfic_temperature(self._dev, byref(v))
        return float(v.value) if rc == 0 else None

    def power_source(self) -> Optional[int]:
        if not self._has("bladerf_get_power_source"):
            return None
        v = c_int()
        rc = self.lib.bladerf_get_power_source(self._dev, byref(v))
        return int(v.value) if rc == 0 else None

    def pmic(self, reg: int) -> Optional[float]:
        """
        Bus/shunt voltage, power and current are floats; configuration and
        calibration are uint16 -- the void* out-param means the caller has to
        already know which, there is no way to ask the DLL which type a
        given register is.
        """
        if not self._has("bladerf_get_pmic_register"):
            return None
        v = c_uint16() if reg in PMIC_UINT16_REGS else c_float()
        rc = self.lib.bladerf_get_pmic_register(self._dev, reg, byref(v))
        return float(v.value) if rc == 0 else None

    def rfic_rssi(self) -> Optional[Tuple[float, float]]:
        """(preamble, symbol) RSSI as the RFIC reports it -- not a calibrated
        dBm figure, see CLAUDE.md section 8."""
        if not self._has("bladerf_get_rfic_rssi"):
            return None
        pre, sym = C.c_int32(), C.c_int32()
        rc = self.lib.bladerf_get_rfic_rssi(self._dev, self._ch,
                                            byref(pre), byref(sym))
        return (float(pre.value), float(sym.value)) if rc == 0 else None

    def get_bias_tee(self) -> Optional[bool]:
        if not self._has("bladerf_get_bias_tee"):
            return None
        v = c_bool()
        rc = self.lib.bladerf_get_bias_tee(self._dev, self._ch, byref(v))
        return bool(v.value) if rc == 0 else None

    def set_bias_tee(self, on: bool) -> None:
        if not self._has("bladerf_set_bias_tee"):
            raise RadioError("this driver does not support bias tee control")
        rc = self.lib.bladerf_set_bias_tee(self._dev, self._ch, c_bool(bool(on)))
        if rc != 0:
            raise RadioError(f"bias tee: {self._err(rc)} ({rc})", rc)

    def get_gain_mode(self) -> Optional[int]:
        if not self._has("bladerf_get_gain_mode"):
            return None
        v = c_int()
        rc = self.lib.bladerf_get_gain_mode(self._dev, self._ch, byref(v))
        return int(v.value) if rc == 0 else None

    def set_gain_mode(self, mode: int) -> None:
        rc = self.lib.bladerf_set_gain_mode(self._dev, self._ch, c_int(mode))
        if rc != 0:
            raise RadioError(f"gain mode: {self._err(rc)} ({rc})", rc)

    def gain_stages(self) -> List[str]:
        if not self._has("bladerf_get_gain_stages"):
            return []
        n = self.lib.bladerf_get_gain_stages(self._dev, self._ch, None, 0)
        if n <= 0:
            return []
        buf = (c_char_p * n)()
        rc = self.lib.bladerf_get_gain_stages(self._dev, self._ch, buf, n)
        return [s.decode() for s in buf[:rc] if s] if rc > 0 else []

    def get_gain_stage(self, stage: str) -> Optional[int]:
        if not self._has("bladerf_get_gain_stage"):
            return None
        v = c_int()
        rc = self.lib.bladerf_get_gain_stage(self._dev, self._ch,
                                             stage.encode(), byref(v))
        return int(v.value) if rc == 0 else None

    def set_gain_stage(self, stage: str, db: int) -> None:
        if not self._has("bladerf_set_gain_stage"):
            raise RadioError("this driver does not support per-stage gain")
        rc = self.lib.bladerf_set_gain_stage(self._dev, self._ch,
                                             stage.encode(), c_int(int(db)))
        if rc != 0:
            raise RadioError(f"gain stage {stage}: {self._err(rc)} ({rc})", rc)

    def loopback_supported(self, mode: int) -> bool:
        if not self._has("bladerf_is_loopback_mode_supported"):
            return False
        return bool(self.lib.bladerf_is_loopback_mode_supported(self._dev, mode))

    def set_loopback(self, mode: int) -> None:
        """
        libbladeRF.h is explicit: "Loopback modes should only be enabled or
        disabled while the RX and TX channels are both disabled ... .
        Otherwise, unexpected behavior may occur." Found out what that
        meant in practice: calling this while RX was actively streaming
        corrupted the USB channel outright (LIBUSB_ERROR_NOT_FOUND, then a
        cascade of NIOS request failures) and wedged the app. So: stop the
        stream first, set loopback, then restart it -- the same pattern
        counter_self_test() already uses for its own mode switch.
        """
        if not self._has("bladerf_set_loopback"):
            raise RadioError("this driver does not support loopback control")
        # "RX and TX channels BOTH disabled" is what the header asks for, and
        # since this app grew a transmitter that now means something: the
        # generator has to be stopped here too, not just the sweep.
        with self._tx_lock:
            tx_was = self._tx_streaming
            if tx_was:
                self._tx_stop_locked()
            was_streaming = self._streaming
            if was_streaming:
                self.stop()
            try:
                rc = self.lib.bladerf_set_loopback(self._dev, c_int(mode))
                if rc != 0:
                    raise RadioError(f"loopback: {self._err(rc)} ({rc})", rc)
            finally:
                # TX BEFORE RX, and that order is not cosmetic. Measured on
                # Kfir's board, repeatably: with BLADERF_LB_RFIC_BIST
                # selected, if the RX stream is read while TX is not yet
                # streaming, the loopback path latches to all zeros and
                # stays there -- starting TX afterwards produces nothing at
                # all, for as long as that RX stream lives. Four trials with
                # TX up first all returned the tone at the right frequency;
                # two trials with an RX read first returned exact zeros
                # both times. Bringing TX up first makes the good ordering
                # the only one that can happen.
                if tx_was:
                    try:
                        self._tx_start_locked()
                    except Exception:
                        pass
                if was_streaming:
                    self.start()

    def get_loopback(self) -> Optional[int]:
        if not self._has("bladerf_get_loopback"):
            return None
        v = c_int()
        rc = self.lib.bladerf_get_loopback(self._dev, byref(v))
        return int(v.value) if rc == 0 else None

    # Measured on Kfir's bladeRF 2.0 micro (fw 2.4.0, FPGA 0.15.0): switching
    # into BLADERF_RX_MUX_32BIT_COUNTER produces exactly one discontinuity, a
    # large jump at a fixed ~19,456 samples after the mux switches and the
    # stream starts -- reproducible at that same sample count across many
    # trials, one big single-shot read, and a read split by an earlier
    # discard, so it is a fixed settling delay, not a per-call artifact. The
    # same shape of problem as tune_settle_s after a retune: discard enough
    # samples after the mode switch before trusting continuity.
    COUNTER_MUX_SETTLE_SAMPLES = 32768

    def counter_self_test(self, n_samples: int = 65536) -> Tuple[int, int]:
        """
        Digital-path self-test with no RF at all: swap the ADC data for a
        known 32-bit counter (low 16 bits I, high 16 bits Q) and check the
        stream for exact continuity. Proves the FPGA-to-USB-to-host path
        loses nothing, independent of anything RF. Restores baseband mode
        even if a read fails partway through.

        Returns (samples_checked, gaps_found).
        """
        if not self._has("bladerf_set_rx_mux") or not self._has("bladerf_get_rx_mux"):
            raise RadioError("this driver does not support the RX mux self-test")
        was_streaming = self._streaming
        try:
            if was_streaming:
                self.stop()
            rc = self.lib.bladerf_set_rx_mux(self._dev, c_int(RX_MUX_32BIT_COUNTER))
            if rc != 0:
                raise RadioError(f"RX mux: {self._err(rc)} ({rc})", rc)
            self.start()
            self._discard(self.COUNTER_MUX_SETTLE_SAMPLES)
            n = int(n_samples)
            buf = np.empty(2 * n, dtype=np.int16)
            # PRIME, then measure. The first read after the mux switch
            # cannot be continuous with what the settle discard consumed:
            # the stream runs on between two calls and the hardware drops
            # whatever nobody collected, which is 5.8's lesson and is
            # exactly what this test was walking into. Measured on the
            # board 2026-09-06 -- the priming read shows ONE jump, of about
            # 295,000 samples, on every run, and the read straight after it
            # shows none. So it reported "1 discontinuity in 65536 samples"
            # for ever, about a digital path that is in fact clean, which
            # is the worst kind of self-test.
            #
            # The buffer is reused rather than reallocated between the two:
            # a 256 KB first-touch allocation is itself long enough to lose
            # a buffer, and the ring is only RX_NUM_BUFFERS *
            # RX_BUFFER_SIZE = 131,072 samples deep -- 3.3 ms at 40 MS/s.
            for _ in range(2):
                rc = self.lib.bladerf_sync_rx(
                    self._dev, buf.ctypes.data_as(c_void_p),
                    c_uint(n), None, c_uint(3000))
                if rc != 0:
                    raise RadioError(f"receive: {self._err(rc)} ({rc})", rc)
            i = buf[0::2].astype(np.uint16).astype(np.uint32)
            q = buf[1::2].astype(np.uint16).astype(np.uint32)
            counter = (q << 16) | i
            diffs = np.diff(counter.astype(np.int64) & 0xFFFFFFFF)
            gaps = int(np.count_nonzero(diffs != 1))
            return n, gaps
        finally:
            self.stop()
            try:
                self.lib.bladerf_set_rx_mux(self._dev, c_int(RX_MUX_BASEBAND))
            except Exception:
                pass
            if was_streaming:
                self.start()

    # -- streaming -------------------------------------------------------
    # Stream buffering. Not free-floating numbers: retune_discard_samples()
    # is derived from RX_NUM_TRANSFERS * RX_BUFFER_SIZE, because that
    # product is how many already-digitized samples are in flight when a
    # retune is issued. Change these and the retune flush follows; hard-code
    # a flush size instead and stitched sweeps grow ghost signals.
    RX_NUM_BUFFERS   = 16
    RX_BUFFER_SIZE   = 8192      # must be a multiple of 1024
    RX_NUM_TRANSFERS = 8
    RX_TIMEOUT_MS    = 3500

    def start(self) -> None:
        if not self._streaming:
            # buffer_size must be a multiple of 1024 for SC16_Q11 (and its
            # META variant -- the 16-byte metadata header rides in the same
            # buffer, sized in samples either way).
            dual = self.dual_rx
            rc = self.lib.bladerf_sync_config(
                self._dev, LAYOUT_RX_X2 if dual else LAYOUT_RX_X1, self._fmt,
                c_uint(self.RX_NUM_BUFFERS), c_uint(self.RX_BUFFER_SIZE),
                c_uint(self.RX_NUM_TRANSFERS), c_uint(self.RX_TIMEOUT_MS))
            if rc != 0:
                raise RadioError(f"stream config: {self._err(rc)} ({rc})", rc)
            # x2 needs BOTH front ends enabled; enabling only one and then
            # reading an x2 stream is a good way to get half a picture.
            for ch in ((CH_RX(0), CH_RX(1)) if dual else (self._ch,)):
                rc = self.lib.bladerf_enable_module(self._dev, ch, True)
                if rc != 0:
                    raise RadioError(f"enable RX: {self._err(rc)} ({rc})", rc)
            self._streaming = True
            self._last_ts, self._last_count = None, 0
            # State the gain now that a module is enabled, because until one
            # is, the RFIC's gain control cannot be read and possibly is not
            # even written -- see set_gain() for the measurement. This is
            # the same rule as tx_force_off (bug 34): bookkeeping about
            # hardware we have just been handed is worthless, so say what
            # the state is instead of inferring it. Once per stream start,
            # not per sweep, so 2026-08-31's skip-if-unchanged saving in the
            # sweep loop is untouched.
            self.set_gain(self._gain, force=True)
        super().start()

    def _ensure_format(self, fmt: int) -> None:
        """Reconfigure the sync stream if it is not already in this format."""
        if self._fmt == fmt and self._streaming:
            return
        self.stop()
        self._fmt = fmt
        self.start()

    def stop(self) -> None:
        if self._streaming:
            # libbladeRF.h is explicit: disarm a trigger before disabling the
            # module, or the disable call blocks for the full stream timeout.
            if self._trig is not None and self._trig.role != TRIG_ROLE_DISABLED:
                try:
                    self.trigger_arm(TRIG_ROLE_DISABLED)
                except Exception:
                    pass
            try:
                # Both front ends, if both were enabled: start() turns two on
                # in x2 and turning only one off left RX2 enabled after
                # leaving RX1+RX2 -- bookkeeping that says "stopped" about a
                # channel still running.
                for ch in ((CH_RX(0), CH_RX(1)) if self.dual_rx
                           else (self._ch,)):
                    self.lib.bladerf_enable_module(self._dev, ch, False)
            except Exception:
                pass
            self._streaming = False
        super().stop()

    # A small block, a few times a second, is all it takes to keep the ring
    # turning while nothing is sweeping -- 4096 samples is 0.1 ms of air
    # time at 40 MS/s, so it cannot be mistaken for a measurement and
    # cannot cost a measurement anything.
    IDLE_DRAIN_SAMPLES = 4096

    def restart_stream(self) -> None:
        """
        Re-run bladerf_sync_config on a stream that has stopped delivering.

        This is the recovery for a `bladerf_sync_rx` timeout that is NOT a
        disconnect, and it exists because of what that timeout does and does
        not tell you. libbladeRF's synchronous interface sits on top of an
        asynchronous stream, and when that stream dies underneath it -- a USB
        hiccup, a link that suspended, buffers left unclaimed long enough for
        the worker to give up -- the ONLY symptom the sync API has is
        BLADERF_ERR_TIMEOUT. Every later call returns the same thing, for
        ever. So retrying the same call cannot cure it, which is precisely
        the freeze reported on 2026-09-06: the sweep timed out, the board
        still answered a register read, and the loop retried a stream that
        was never going to answer again.

        Much cheaper than the reopen the disconnect path does: the handle,
        the frequency, the sample rate and the gain all stay as they were,
        and start() re-states the gain anyway (bug 45). If this does not
        cure it, Worker escalates to that reopen. **Measured on the board
        2026-09-06: 14.5 ms**, against roughly a second for a reopen, so
        the ladder is affordable enough to try three times.

        Proved on the board rather than reasoned about: holding the stream
        30 s with nothing claiming it produced exactly the reported
        "receive: Operation timed out (-6)", with libusb printing
        "Transfer timed out for buffer" underneath it, and this call cured
        it -- the next read returned all 65,536 samples.
        """
        self.stop()
        self.start()

    def keepalive(self) -> bool:
        """
        Claim one small block, so an idle stream does not rot.

        Returns True only when a block was really read. The three declines
        below return False, and the difference matters to the caller: a
        read is evidence the stream is delivering and clears the recovery
        counter (bug 51), while declining says nothing about the stream at
        all. Reporting a decline as success would let a stall be forgiven
        by a call that never touched the device.

        Deliberately does NOTHING in two states, and both matter more than
        the keepalive does:

          * a META format means a triggered capture owns the stream, and
            sync_rx with the NULL metadata pointer read() passes would be
            dereferenced -- the same trap _discard()'s comment records;
          * an armed trigger has its own wait loop, and draining underneath
            it would consume the very samples it is waiting to hand over.

        It also must not force the format, because forcing it is a stop and
        a start (see _ensure_format), and restarting the stream to keep it
        alive is not a trade worth making.

        **What this does NOT do is keep up, and it never could.** The ring
        is RX_NUM_BUFFERS * RX_BUFFER_SIZE = 131,072 samples, 3.3 ms of air
        at 40 MS/s, so a host claiming 4096 samples four times a second is
        overrun continuously whatever it does. What it keeps turning is the
        sync layer's claim-and-return cycle underneath. Measured over 20 s
        holds at 40 MS/s, three settings tried: 4096/0.25 s and 8192/0.1 s
        each timed out 0 times in 2 trials, and **32768/0.05 s timed out
        once**, i.e. draining harder is not better and nothing removes the
        failure altogether. So the constants below are the cheapest of the
        three rather than the strongest, and the REAL cure is
        restart_stream(): what the keepalive buys is that an idle stream
        dies on this call, where Worker restarts it unseen, instead of on
        the user's next sweep, where it is a frozen trace and a popup.
        """
        if not self._streaming or self._fmt in (FMT_SC16_Q11_META,
                                                FMT_SC8_Q7_META):
            return False
        if self._trig is not None and self._trig.role != TRIG_ROLE_DISABLED:
            return False
        n = self.IDLE_DRAIN_SAMPLES
        stride = 4 if self.dual_rx else 2
        entries = 2 * n if self.dual_rx else n
        dtype = np.int8 if self._oversample else np.int16
        need = stride * n
        if (self._buf is None or self._buf.size < need
                or self._buf.dtype != dtype):
            self._buf = np.empty(need, dtype=dtype)
        rc = self.lib.bladerf_sync_rx(
            self._dev, self._buf.ctypes.data_as(c_void_p),
            c_uint(entries), None, c_uint(1500))
        if rc != 0:
            raise RadioError(f"receive: {self._err(rc)} ({rc})", rc)
        return True

    def close(self) -> None:
        self.stop()
        # Under _tx_lock, and before the handle goes: the generator thread
        # could be inside bladerf_sync_tx on this very pointer right now.
        try:
            with self._tx_lock:
                self._tx_stop_locked()
        except Exception:
            pass
        # Belt and braces on the way out: _tx_stop_locked() only acts if we
        # believe we were streaming, and leaving a TX channel enabled behind
        # us is what makes the next process inherit a live transmitter.
        try:
            self.tx_force_off()
        except Exception:
            pass
        if self._dev:
            try:
                self.lib.bladerf_close(self._dev)
            except Exception:
                pass
            self._dev = c_void_p()

    def alive(self) -> bool:
        """Cheap round trip to the board. False means it is gone."""
        if not self._dev:
            return False
        v = c_uint64()
        try:
            return self.lib.bladerf_get_frequency(self._dev, self._ch,
                                                  byref(v)) == 0
        except Exception:
            return False

    def read(self, n: int) -> np.ndarray:
        # A plain read needs a plain (non-META) format -- if a triggered
        # zero-span capture left the stream in SC16_Q11_META, bladerf_sync_rx
        # would be asked to extract metadata into the NULL pointer passed
        # below. Which of the two plain formats depends on oversample: it
        # requires 8-bit samples (SC8_Q7), full scale +/-128 rather than
        # +/-2048, so the dtype and scale below have to switch with it.
        plain_fmt = FMT_SC8_Q7 if self._oversample else FMT_SC16_Q11
        self._ensure_format(plain_fmt)
        if not self._streaming:
            self.start()
        # In x2 this still has to return n samples of ONE port, because every
        # caller wants that: zero span, the IQ recorder, and -- the one that
        # bit -- _discard(), which is how the post-retune flush is measured.
        # num_samples counts interleaved entries, so asking for n in x2 flushes
        # only n/2 samples per port and the retune flush silently halves,
        # which is how the ghost signals of section 4 bug 16 would come back
        # in RX1+RX2. Ask for 2n entries and keep RX1.
        dual = self.dual_rx
        stride = 4 if dual else 2
        entries = 2 * n if dual else n
        dtype = np.int8 if self._oversample else np.int16
        scale = SC8_Q7_SCALE if self._oversample else SC16_Q11_SCALE
        need = stride * n
        if self._buf is None or self._buf.size < need or self._buf.dtype != dtype:
            self._buf = np.empty(need, dtype=dtype)
        # 1.5 s is ample for any block we ask for (a 2M-sample capture at
        # 40 MS/s is 52 ms of air time). Keeping it short matters because a
        # wide sweep makes ~200 of these calls, so a long timeout on a dead
        # board would freeze the window for minutes.
        rc = self.lib.bladerf_sync_rx(
            self._dev, self._buf.ctypes.data_as(c_void_p), c_uint(entries),
            None, c_uint(1500))
        if rc != 0:
            raise RadioError(f"receive: {self._err(rc)} ({rc})", rc)
        v = self._buf[:need].astype(np.float32) * (1.0 / scale)
        return (v[0::stride] + 1j * v[1::stride]).astype(np.complex64)  # I first

    def read_dual(self, n: int):
        """
        n samples from BOTH RX ports, captured in the same instant.

        Returns (iq_rx1, iq_rx2).

        The trap, and it is the same one TX_BOTH hit (CLAUDE.md 5.6):
        bladerf_sync_rx's num_samples counts INTERLEAVED ENTRIES in x2, not
        samples per channel, and libbladeRF.h does not say which. Ask for n
        and you get n/2 per port while the call still succeeds -- the
        spectrum would look plausible and be built from half the samples.
        So ask for 2*n entries to get n per port.

        Buffer layout is one interleaved stream: ch0 I, ch0 Q, ch1 I, ch1 Q,
        repeating -- hence the stride of 4 int16 below.
        """
        if not self.dual_rx:
            raise RadioError("read_dual() needs the port set to RX1+RX2")
        self._ensure_format(FMT_SC8_Q7 if self._oversample else FMT_SC16_Q11)
        if not self._streaming:
            self.start()
        dtype = np.int8 if self._oversample else np.int16
        scale = SC8_Q7_SCALE if self._oversample else SC16_Q11_SCALE
        need = 4 * n                      # 2 channels x (I, Q)
        if self._buf is None or self._buf.size < need or self._buf.dtype != dtype:
            self._buf = np.empty(need, dtype=dtype)
        rc = self.lib.bladerf_sync_rx(
            self._dev, self._buf.ctypes.data_as(c_void_p), c_uint(2 * n),
            None, c_uint(1500))
        if rc != 0:
            raise RadioError(f"receive: {self._err(rc)} ({rc})", rc)
        v = self._buf[:need].astype(np.float32) * (1.0 / scale)
        rx1 = (v[0::4] + 1j * v[1::4]).astype(np.complex64)
        rx2 = (v[2::4] + 1j * v[3::4]).astype(np.complex64)
        return rx1, rx2

    # -- section 5.1: triggered capture and timestamps ----------------------
    def trigger_init(self, signal: int) -> None:
        if not self._has("bladerf_trigger_init"):
            raise RadioError("this driver does not support triggering")
        trig = BladerfTrigger()
        rc = self.lib.bladerf_trigger_init(self._dev, self._ch,
                                           c_int(signal), byref(trig))
        if rc != 0:
            raise RadioError(f"trigger init: {self._err(rc)} ({rc})", rc)
        self._trig = trig

    def trigger_arm(self, role: int) -> None:
        if self._trig is None:
            raise RadioError("trigger_init() was not called")
        self._trig.role = role
        rc = self.lib.bladerf_trigger_arm(
            self._dev, byref(self._trig), c_bool(role != TRIG_ROLE_DISABLED),
            c_uint64(0), c_uint64(0))
        if rc != 0:
            raise RadioError(f"trigger arm: {self._err(rc)} ({rc})", rc)
        # Arming deliberately creates a gap in the sample stream: the FPGA
        # counter keeps running while the trigger holds RX off. Forget the
        # continuity anchor so read_meta() does not read that intended gap
        # as a dropped-sample overrun -- it reported OVERRUN on the very
        # first triggered capture before this, which is worse than useless
        # on an instrument.
        self._last_ts, self._last_count = None, 0

    def trigger_fire(self) -> None:
        if self._trig is None:
            raise RadioError("trigger_init() was not called")
        rc = self.lib.bladerf_trigger_fire(self._dev, byref(self._trig))
        if rc != 0:
            raise RadioError(f"trigger fire: {self._err(rc)} ({rc})", rc)

    def trigger_state(self) -> Optional[Tuple[bool, bool, bool]]:
        if self._trig is None or not self._has("bladerf_trigger_state"):
            return None
        armed, fired, fire_req = c_bool(), c_bool(), c_bool()
        r1, r2 = c_uint64(), c_uint64()
        rc = self.lib.bladerf_trigger_state(
            self._dev, byref(self._trig), byref(armed), byref(fired),
            byref(fire_req), byref(r1), byref(r2))
        if rc != 0:
            return None
        return bool(armed.value), bool(fired.value), bool(fire_req.value)

    def _set_format_lazy(self, fmt: int) -> None:
        if self._streaming and self._fmt != fmt:
            self.stop()
        self._fmt = fmt

    def read_meta(self, n: int, rx_now: bool = True, timeout_ms: int = 300):
        """
        Timestamped read via BLADERF_FORMAT_SC16_Q11_META.

        Returns (iq, timestamp, overrun, actual_count, miniexp1, miniexp2).
        Raises RadioError(rc=ERR_TIMEOUT) if nothing arrives in time -- with
        a slave trigger armed and waiting, that is the *expected* return
        while nothing has fired yet, not a sign the board is gone. The
        caller (Worker._time_domain) is what decides that, by looping on it
        while the wait is normal and bailing out on any other error.

        mini_exp1/2 and the overrun flag come out of meta.status, not
        meta.flags -- flags is documented as an *input-only* field the API
        never writes back to, despite BLADERF_META_FLAG_RX_HW_MINIEXP1 being
        named as though it belonged there.
        """
        self._ensure_format(FMT_SC16_Q11_META)
        if not self._streaming:
            self.start()
        if self._buf is None or self._buf.size < 2 * n:
            self._buf = np.empty(2 * n, dtype=np.int16)
        meta = BladerfMetadata()
        meta.flags = META_FLAG_RX_NOW if rx_now else 0
        rc = self.lib.bladerf_sync_rx(
            self._dev, self._buf.ctypes.data_as(c_void_p), c_uint(n),
            byref(meta), c_uint(timeout_ms))
        if rc != 0:
            # A timeout while a trigger is armed is a normal wait, but the
            # sample counter ran on through it, so the next successful read
            # will legitimately be discontinuous. Drop the anchor rather
            # than let that show up as a phantom overrun.
            if rc == ERR_TIMEOUT:
                self._last_ts, self._last_count = None, 0
            raise RadioError(f"receive: {self._err(rc)} ({rc})", rc)
        actual = int(meta.actual_count)
        status = int(meta.status)
        # What "overrun" should mean to the user: THIS capture is damaged.
        #
        # Two tempting signals are both wrong on their own here, and both
        # were tried against the board first:
        #
        # 1. Comparing this timestamp against previous_timestamp +
        #    previous_count, as CLAUDE.md 5.1 suggested. Right for an app
        #    that reads the stream back to back; wrong for this one. Zero
        #    span deliberately grabs a short burst per frame and ignores the
        #    stream in between, so the timestamp jumps every single frame.
        #    Flagged ~every triggered capture.
        # 2. The hardware META_STATUS_OVERRUN bit by itself. It is telling
        #    the truth -- samples really are dropped between our bursts,
        #    because nobody is reading them -- but that says nothing about
        #    whether the block we just got is intact. Measured: 26 of 30
        #    triggered frames had the bit set while every block was whole.
        #
        # libbladeRF.h points at the right test: actual_count is "the actual
        # number of contiguous samples that have been populated", and is
        # short of the request only "in the event of a discontinuity". So a
        # full-length block is contiguous and good no matter what was
        # discarded between frames, and that is what gets reported.
        short_block = actual < n
        overrun = bool(status & META_STATUS_OVERRUN) and short_block
        ts = int(meta.timestamp)
        self._last_ts, self._last_count = ts, actual
        # On overrun, only actual_count samples are valid -- the rest of the
        # buffer is documented as undefined.
        k = max(actual, 0)
        v = self._buf[:2 * k].astype(np.float32) * (1.0 / SC16_Q11_SCALE)
        iq = (v[0::2] + 1j * v[1::2]).astype(np.complex64)
        miniexp1 = bool(status & META_FLAG_RX_HW_MINIEXP1)
        miniexp2 = bool(status & META_FLAG_RX_HW_MINIEXP2)
        return iq, ts, overrun, actual, miniexp1, miniexp2

    # -- section 5.2: quick tune -----------------------------------------
    def get_quick_tune_bytes(self) -> bytes:
        """
        Snapshot the fast-lock profile for whatever frequency is currently
        tuned. Caller must have just tuned there with a normal set_freq()
        first -- see schedule_retune()'s docstring for what this could and
        could not be confirmed to do on real hardware.
        """
        if not self._has("bladerf_get_quick_tune"):
            raise RadioError("this driver does not support quick tune")
        qt = BladerfQuickTune()
        rc = self.lib.bladerf_get_quick_tune(self._dev, self._ch, byref(qt))
        if rc != 0:
            raise RadioError(f"get quick tune: {self._err(rc)} ({rc})", rc)
        return bytes(qt.raw)

    def schedule_retune(self, freq_hz: float, qt_bytes: bytes) -> float:
        """
        Replay a cached fast-lock profile via BLADERF_RETUNE_NOW.

        UNVERIFIED on real hardware, and that is worth taking seriously
        rather than trusting the header comment: tested on Kfir's bladeRF
        2.0 micro (lib 2.5.0, fw 2.4.0, FPGA 0.15.0), bladerf_schedule_retune
        always returned 0 (success), but bladerf_get_frequency() never
        reflected the target frequency afterwards -- it kept reporting
        whatever the last plain set_frequency() call had set, even with an
        active RX stream being pumped throughout. A before/after spectral
        comparison (peak level and offset around a real ambient signal)
        also did not clearly match either the requested frequency or the
        frequency it should have stayed at if nothing happened. Whether the
        RF genuinely retuned could not be established either way without a
        known signal source or a second board to check against, which is
        why this stays off by default with an explicit warning rather than
        being trusted. Do not remove that warning without a real
        confirmed measurement.
        """
        if not self._has("bladerf_schedule_retune"):
            raise RadioError("this driver does not support quick tune")
        qt = BladerfQuickTune()
        C.memmove(qt.raw, qt_bytes, min(len(qt_bytes), len(qt.raw)))
        rc = self.lib.bladerf_schedule_retune(
            self._dev, self._ch, c_uint64(RETUNE_NOW), c_uint64(int(freq_hz)),
            byref(qt))
        if rc != 0:
            raise RadioError(f"schedule retune: {self._err(rc)} ({rc})", rc)
        self._freq = float(freq_hz)
        return self._freq

    def cancel_scheduled_retunes(self) -> None:
        if self._has("bladerf_cancel_scheduled_retunes"):
            try:
                self.lib.bladerf_cancel_scheduled_retunes(self._dev, self._ch)
            except Exception:
                pass

    # -- section 5.2: oversample -------------------------------------------
    def enable_oversample(self, on: bool) -> None:
        """
        122.88 MS/s via BLADERF_FEATURE_OVERSAMPLE -- confirmed on real
        hardware: sample_rate_range.max reads 122.88 MHz only while this is
        on, and reverts to the normal max when off. Requires 8-bit samples
        (BLADERF_FORMAT_SC8_Q7); a known libbladeRF quirk is that
        set_frequency can also change the sample rate while this feature is
        active, so frequency is (re)applied before sample rate here, and the
        achievable ranges are re-read afterward either way, since Max IBW's
        UI limits depend on them.
        """
        if not self._has("bladerf_enable_feature"):
            raise RadioError("this driver does not support the oversample feature")
        # Device-wide, and it changes the sample WIDTH for both directions,
        # so the generator has to come down with the sweep and go back up
        # afterwards in the new format. Holding _tx_lock across the whole
        # thing keeps the TX thread out of the driver while it happens.
        with self._tx_lock:
            tx_was = self._tx_streaming
            if tx_was:
                self._tx_stop_locked()
            try:
                self._oversample_inner(on)
            finally:
                if tx_was:
                    try:
                        self._tx_ensure_format_locked()
                        self._tx_start_locked()
                    except Exception:
                        pass

    def _oversample_inner(self, on: bool) -> None:
        was_streaming = self._streaming
        if was_streaming:
            self.stop()
        rc = self.lib.bladerf_enable_feature(
            self._dev, c_int(FEATURE_OVERSAMPLE if on else FEATURE_DEFAULT),
            c_bool(True))
        if rc != 0:
            raise RadioError(f"oversample: {self._err(rc)} ({rc})", rc)
        self._oversample = on
        self._set_format_lazy(FMT_SC8_Q7 if on else FMT_SC16_Q11)
        want_fs = self._fs            # before anything below can move it
        self.set_freq(self._freq, discard=False)     # frequency first, then rate
        try:
            lo, hi = self._range("bladerf_get_sample_rate_range")
            self.fs_min, self.fs_max = lo, hi
        except RadioError:
            pass
        # set_frequency is documented to be able to move the sample rate while
        # this feature is on, which means our cached copy of the rate is not
        # to be trusted right here -- and set_sample_rate() SKIPS the call
        # when it believes the rate is already right. Believing a stale copy
        # would leave the hardware at whatever set_frequency did while the
        # frequency axis was labelled from our number: every reading wrong,
        # nothing on screen to say so. So drop the cache and force the write.
        self._fs_pair = None
        v = c_uint()
        if self.lib.bladerf_get_sample_rate(self._dev, self._ch, byref(v)) == 0:
            self._fs = float(v.value)
        self.set_sample_rate(min(want_fs, self.fs_max), force=True)
        if was_streaming:
            self.start()

    def _discard(self, n: int) -> None:
        """
        Swallow n samples and look at none of them.

        This used to call read(), which is correct and wasteful: read()
        converts to float32 and assembles a complex64 array, and the whole
        point of a discard is that nothing ever looks at the result. At
        125,536 samples per retune and 206 retunes on a full span that is
        about 26 million samples per sweep converted for the bin.

        So it does the sync_rx and stops there. Everything else read() does
        that MATTERS here is kept and matters a lot: the format has to be
        forced back to a plain one (a triggered zero-span capture can have
        left the stream in META, where the NULL metadata pointer below would
        be dereferenced), the stream has to be running, and in x2 the count
        is INTERLEAVED ENTRIES rather than samples per port -- get that
        wrong and the flush silently halves, which brings back the ghost
        signals of bug 16 in RX1+RX2 (that is the trap 5.8 records, and the
        reason read()'s own comment calls the discard out by name).
        """
        n = int(n)
        if n <= 0:
            return
        try:
            plain_fmt = FMT_SC8_Q7 if self._oversample else FMT_SC16_Q11
            self._ensure_format(plain_fmt)
            if not self._streaming:
                self.start()
            stride = 4 if self.dual_rx else 2
            entries = 2 * n if self.dual_rx else n
            dtype = np.int8 if self._oversample else np.int16
            need = stride * n
            if (self._buf is None or self._buf.size < need
                    or self._buf.dtype != dtype):
                self._buf = np.empty(need, dtype=dtype)
            rc = self.lib.bladerf_sync_rx(
                self._dev, self._buf.ctypes.data_as(c_void_p),
                c_uint(entries), None, c_uint(1500))
            if rc != 0:
                raise RadioError(f"receive: {self._err(rc)} ({rc})", rc)
        except RadioError:
            pass

    # ======================================================================
    #  Signal generator -- TX on the same board, at the same time as RX
    #
    #  The bladeRF 2.0 is genuinely full duplex and the AD9361 has separate
    #  RX and TX synthesisers, so the generator can sit on one frequency
    #  while the analyzer sweeps somewhere else entirely. That is the whole
    #  reason this board was chosen over a HackRF back in the Phase 1
    #  write-up, and it is what makes "generate and measure at once" work
    #  with one box.
    #
    #  The catch is that both directions go through ONE device handle, and
    #  Worker already owns that handle from the sweep thread. libbladeRF is
    #  fine with sync_rx on one thread and sync_tx on another -- that is the
    #  documented full-duplex pattern -- but it is NOT fine with two threads
    #  reconfiguring the device underneath each other, which is exactly the
    #  class of bug that made the loopback toggle hang before. So:
    #
    #    * _tx_lock is held for every TX call and for every device-wide
    #      operation that has to disturb the TX stream (oversample, loopback,
    #      the counter self-test). Those three stop TX, do their work, and
    #      put it back.
    #    * The data calls themselves (sync_rx here, sync_tx there) never
    #      block each other, which is the one place concurrency is required.
    # ======================================================================

    # TX stream buffering, named for the same reason the RX ones are: an RX
    # overrun loses samples that were going to be averaged away anyway, but a
    # TX underrun puts a real hole in the signal a DUT is being tested
    # against -- so this side is buffered deeper on purpose.
    TX_NUM_BUFFERS   = 32
    TX_BUFFER_SIZE   = 16384     # must be a multiple of 1024
    TX_NUM_TRANSFERS = 16
    TX_TIMEOUT_MS    = 3500

    def _tx_state_init(self) -> None:
        self._tx_lock = threading.RLock()
        self._tx_ch_index = 0
        self._tx_ch = CH_TX(0)
        self._tx_streaming = False
        self._tx_configured = False
        self._tx_fmt = FMT_SC16_Q11
        self._tx_buf: Optional[np.ndarray] = None
        self._tx_flt: Optional[np.ndarray] = None
        # The waveform rendered to fixed point once, so streaming is a
        # memcpy. _tx_wrap assembles the single block per lap that straddles
        # the join.
        self._tx_packed: Optional[np.ndarray] = None
        self._tx_packed_n = 0
        self._tx_packed_fmt = -1
        self._tx_stride = 2
        self._tx_packed_dual = False
        self._tx_wrap: Optional[np.ndarray] = None
        self._tx_loop_ref: Optional[np.ndarray] = None
        self._tx_gain = 20.0
        self._tx_trig: Optional[BladerfTrigger] = None
        for attr, fn in (("tx_freq", "bladerf_get_frequency_range"),
                         ("tx_fs", "bladerf_get_sample_rate_range"),
                         ("tx_gain", "bladerf_get_gain_range")):
            try:
                lo, hi = self._range(fn, self._tx_ch)
                setattr(self, f"{attr}_min", lo)
                setattr(self, f"{attr}_max", hi)
            except RadioError:
                pass

    @property
    def tx_supported(self) -> bool:
        return bool(OPT.get("bladerf_sync_tx", False)) and bool(self._dev)

    @property
    def tx_reason(self) -> str:
        if not self._dev:
            return "the radio is not open"
        if not OPT.get("bladerf_sync_tx", False):
            return ("this libbladeRF does not export bladerf_sync_tx -- "
                    "update the Nuand driver")
        return ""

    @property
    def tx_port(self) -> str:
        if self._tx_ch_index == TX_BOTH:
            return "TX1+TX2"
        return f"TX{self._tx_ch_index + 1}"

    def tx_set_channel(self, index: int) -> None:
        """
        Pick TX1, TX2, or both at once.

        Both means BLADERF_TX_X2, where the driver expects one buffer with
        the two channels interleaved sample by sample. The rendered buffer
        changes shape, so it has to be rebuilt on the way in or out of dual
        mode -- see _tx_render_locked().
        """
        index = int(index)
        index = index if index in (0, 1, TX_BOTH) else 0
        with self._tx_lock:
            if index == self._tx_ch_index:
                return
            was = self._tx_streaming
            if was:
                self._tx_stop_locked()
            # Disable whatever was live, or a port keeps radiating whatever
            # the RFIC last had on it. In dual mode that is both of them.
            for ch in self._tx_channels():
                try:
                    self.lib.bladerf_enable_module(self._dev, ch, False)
                except Exception:
                    pass
            self._tx_ch_index = index
            self._tx_ch = CH_TX(0 if index == TX_BOTH else index)
            self._tx_configured = False
            # The rendered buffer is per-channel-interleaved or not; either
            # way the old one is now the wrong shape.
            self._tx_packed = None
            if was:
                self._tx_start_locked()

    def _tx_channels(self):
        """Every TX channel currently in use -- one, or both in dual mode."""
        if self._tx_ch_index == TX_BOTH:
            return (CH_TX(0), CH_TX(1))
        return (CH_TX(self._tx_ch_index),)

    @property
    def _tx_dual(self) -> bool:
        return self._tx_ch_index == TX_BOTH

    def _tx_check(self) -> None:
        if not self.tx_supported:
            raise RadioError(self.tx_reason or "no transmitter available")

    def _tx_guard(self) -> None:
        """
        Re-check the handle from INSIDE _tx_lock.

        Worker calls close() from the sweep thread the instant it decides the
        board has gone, and this thread could be sitting in bladerf_sync_tx
        on that very handle at the time. close() takes the same lock and
        stops TX first, so by the time it can free the handle this thread is
        out of the driver -- but it may then be waiting on the lock with a
        block still to send, and handing a freed pointer to libbladeRF is
        how a clean disconnect turns into a crash.
        """
        if not self._dev:
            raise RadioError("the radio has been closed", ERR_NODEV)

    def tx_set_freq(self, hz) -> float:
        self._tx_check()
        f = float(np.clip(hz, self.tx_freq_min, self.tx_freq_max))
        with self._tx_lock:
            for ch in self._tx_channels():
                rc = self.lib.bladerf_set_frequency(self._dev, ch,
                                                    c_uint64(int(f)))
                if rc != 0:
                    raise RadioError(
                        f"set TX frequency: {self._err(rc)} ({rc})", rc)
            v = c_uint64()
            self.lib.bladerf_get_frequency(self._dev, self._tx_ch, byref(v))
            return float(v.value)

    def tx_set_sample_rate(self, hz) -> float:
        """
        Set the TX rate and report what the board actually did.

        RX and TX rates are set per channel, but they are derived from the
        same baseband PLL, so one can move the other. Rather than assume
        either way, this re-reads the RX rate afterwards and keeps our own
        copy of it honest -- the sweep re-applies its rate every pass, so a
        disturbed RX rate corrects itself, but a stale cached number would
        have mislabelled the frequency axis in the meantime.
        """
        self._tx_check()
        want = float(np.clip(hz, self.tx_fs_min, self.tx_fs_max))
        with self._tx_lock:
            act = c_uint()
            rc = self.lib.bladerf_set_sample_rate(self._dev, self._tx_ch,
                                                  c_uint(int(want)), byref(act))
            if rc != 0:
                raise RadioError(f"set TX sample rate: {self._err(rc)} ({rc})", rc)
            got = float(act.value)
            v = c_uint()
            if self.lib.bladerf_get_sample_rate(self._dev, self._ch, byref(v)) == 0:
                self._fs = float(v.value)
            return got

    def tx_set_bandwidth(self, hz) -> float:
        self._tx_check()
        with self._tx_lock:
            bw = c_uint()
            for ch in self._tx_channels():
                rc = self.lib.bladerf_set_bandwidth(self._dev, ch,
                                                    c_uint(int(hz)), byref(bw))
                if rc != 0:
                    raise RadioError(
                        f"set TX bandwidth: {self._err(rc)} ({rc})", rc)
            return float(bw.value)

    def tx_set_gain(self, db) -> float:
        self._tx_check()
        g = float(np.clip(db, self.tx_gain_min, self.tx_gain_max))
        with self._tx_lock:
            for ch in self._tx_channels():
                self.lib.bladerf_set_gain(self._dev, ch, c_int(int(round(g))))
            v = c_int()
            if self.lib.bladerf_get_gain(self._dev, self._tx_ch, byref(v)) == 0:
                g = float(v.value)
            self._tx_gain = g
            return g

    @property
    def tx_sample_rate(self) -> float:
        if not self._dev:
            return 0.0
        v = c_uint()
        try:
            if self.lib.bladerf_get_sample_rate(self._dev, self._tx_ch,
                                                byref(v)) == 0:
                return float(v.value)
        except Exception:
            pass
        return 0.0

    @property
    def tx_full_scale_dbm(self) -> float:
        # Full-scale digital sine out, at the gain currently set. Anchored on
        # Nuand's +8 dBm typical CW figure at maximum gain and sliding down
        # with the gain from there. An ESTIMATE -- see SG_LEVEL_NOTE.
        return TX_FS0_DBM - (self.tx_gain_max - self._tx_gain)

    # -- stream ------------------------------------------------------------
    def _tx_start_locked(self) -> None:
        """Caller holds _tx_lock."""
        if self._tx_streaming:
            return
        # Reconfigured on every start, the same as RX's start() does, because
        # the format or the channel may have changed since last time and a
        # stale config is what silently sends the wrong sample width.
        dual = self._tx_dual
        # In x2 the driver reads two channels out of one buffer, so a buffer
        # of a given sample count holds half as many samples PER CHANNEL --
        # doubled here so the buffered depth in seconds stays what the rest
        # of the code assumes.
        rc = self.lib.bladerf_sync_config(
            self._dev, LAYOUT_TX_X2 if dual else LAYOUT_TX_X1, self._tx_fmt,
            c_uint(self.TX_NUM_BUFFERS),
            c_uint(self.TX_BUFFER_SIZE * (2 if dual else 1)),
            c_uint(self.TX_NUM_TRANSFERS), c_uint(self.TX_TIMEOUT_MS))
        if rc != 0:
            raise RadioError(f"TX stream config: {self._err(rc)} ({rc})", rc)
        self._tx_configured = True
        # Both ports must be enabled for x2, and the FIRST failure has to
        # undo the ones already enabled -- otherwise a half-enabled dual
        # stream leaves a port radiating with nothing feeding it.
        done = []
        for ch in self._tx_channels():
            rc = self.lib.bladerf_enable_module(self._dev, ch, True)
            if rc != 0:
                for d in done:
                    try:
                        self.lib.bladerf_enable_module(self._dev, d, False)
                    except Exception:
                        pass
                raise RadioError(f"enable TX: {self._err(rc)} ({rc})", rc)
            done.append(ch)
        self._tx_streaming = True

    def _tx_stop_locked(self) -> None:
        if not self._tx_streaming:
            return
        # Same ordering trap as RX: disarm before disabling, or the disable
        # blocks for the whole stream timeout waiting on a gate.
        if self._tx_trig is not None and self._tx_trig.role != TRIG_ROLE_DISABLED:
            try:
                self.tx_trigger_arm(TRIG_ROLE_DISABLED)
            except Exception:
                pass
        for ch in self._tx_channels():
            try:
                self.lib.bladerf_enable_module(self._dev, ch, False)
            except Exception:
                pass
        self._tx_streaming = False

    def tx_force_off(self) -> None:
        """
        Disable BOTH TX channels on the hardware, unconditionally.

        This exists because of a real fault Kfir hit on the bench: with the
        generator switched OFF, interacting with the spectrum display put a
        brief carrier out of the TX port at 2440 MHz -- SgConfig's own
        default TX frequency, not a coincidence.

        The AD9361 keeps whatever enable state it was left in, and NOTHING in
        this app used to clear it. Any of a crash, a kill, a previous run that
        did not exit cleanly, or a bladeRF-cli session can leave a TX channel
        enabled; open the board afterwards and the transmitter is live before
        a line of our code has asked for anything. tx_stop() cannot rescue it
        -- it returns immediately when `self._tx_streaming` is False, which it
        always is on a fresh handle -- so the app had no way at all to turn
        off a transmitter it had not itself turned on.

        The "briefly" in the report is the second half. RX and TX come off one
        baseband PLL (section 5.5 fact 1), so panning or zooming the spectrum
        changes the span, which changes the sample rate, which reprograms that
        PLL underneath an enabled TX channel -- and out comes a burst. The
        display interaction was never sending anything; it was disturbing a
        transmitter that should not have been on.

        So: never infer the hardware's TX state from our own bookkeeping.
        State it. Called when the device is opened, before the clocks are
        touched, and again on the way out so the NEXT process does not inherit
        the same fault from us.
        """
        if not self._dev:
            return
        with self._tx_lock:
            for ch in (CH_TX(0), CH_TX(1)):
                try:
                    self.lib.bladerf_enable_module(self._dev, ch, False)
                except Exception:
                    pass
            self._tx_streaming = False

    def tx_start(self) -> None:
        self._tx_check()
        with self._tx_lock:
            self._tx_guard()
            self._tx_ensure_format_locked()
            self._tx_start_locked()

    def tx_stop(self) -> None:
        if not self._dev:
            return
        with self._tx_lock:
            self._tx_stop_locked()
            self._tx_clear_meta_locked()

    def _tx_clear_meta_locked(self) -> None:
        """
        Put the TX stream back into a plain format on the way out.

        Metadata mode is device-wide, and it locks BOTH ways: measured here,
        with the TX stream configured for metadata, bringing the plain RX
        stream back up fails with BLADERF_ERR_INVAL (-3), exactly as a
        metadata TX config fails while RX is streaming. Nothing in the
        generator panel uses metadata bursts (see tx_burst), so the app
        cannot reach that state today -- but leaving a stopped transmitter
        holding the device in metadata mode would be a quiet way to wedge
        the analyzer later, and clearing it costs one call.
        """
        if self._tx_fmt not in (FMT_SC16_Q11_META, FMT_SC8_Q7_META):
            return
        self._tx_fmt = FMT_SC8_Q7 if self._oversample else FMT_SC16_Q11
        self._tx_configured = False
        try:
            self.lib.bladerf_sync_config(
                self._dev, LAYOUT_TX_X1, self._tx_fmt,
                c_uint(self.TX_NUM_BUFFERS), c_uint(self.TX_BUFFER_SIZE),
                c_uint(self.TX_NUM_TRANSFERS), c_uint(self.TX_TIMEOUT_MS))
        except Exception:
            pass

    def _tx_ensure_format_locked(self) -> None:
        """Oversample runs the whole converter in 8-bit, so TX has to follow
        RX into SC8_Q7 or the samples going out are read as the wrong width."""
        want = FMT_SC8_Q7 if self._oversample else FMT_SC16_Q11
        if want != self._tx_fmt:
            was = self._tx_streaming
            if was:
                self._tx_stop_locked()
            self._tx_fmt = want
            self._tx_configured = False
            if was:
                self._tx_start_locked()

    def _tx_fixed(self):
        """(dtype, scale, min, max) for whichever sample width is in force."""
        if self._tx_fmt in (FMT_SC8_Q7, FMT_SC8_Q7_META):
            return np.int8, SC8_Q7_SCALE, -128.0, 127.0
        return np.int16, SC16_Q11_SCALE, -2048.0, 2047.0

    def _tx_pack(self, iq: np.ndarray) -> np.ndarray:
        """complex64 in normalised units -> interleaved fixed point, into a
        reused scratch buffer. Only for one-off sends; the streaming path
        uses the pre-rendered loop instead, see tx_set_loop()."""
        dtype, scale, lo, hi = self._tx_fixed()
        n = iq.size
        if self._tx_flt is None or self._tx_flt.size < 2 * n:
            self._tx_flt = np.empty(2 * n, dtype=np.float32)
        if (self._tx_buf is None or self._tx_buf.size < 2 * n
                or self._tx_buf.dtype != dtype):
            self._tx_buf = np.empty(2 * n, dtype=dtype)
        v = self._tx_flt[:2 * n]
        v[0::2] = iq.real
        v[1::2] = iq.imag
        np.multiply(v, scale, out=v)
        # Deliberately NOT dithered, unlike the streaming path: the one-off
        # sends are bursts and the flush of zeros that unkeys the
        # transmitter, and dithering silence would radiate noise instead of
        # nothing.
        np.rint(v, out=v)
        np.clip(v, lo, hi, out=v)
        out = self._tx_buf[:2 * n]
        np.copyto(out, v, casting="unsafe")
        return out

    def tx_set_loop(self, loop: np.ndarray) -> None:
        """
        Render the whole waveform to fixed point ONCE, here, so the streaming
        path never converts anything again.

        This is not a micro-optimisation, it is the difference between the
        generator working and not. Converting float samples to fixed point
        per block cost 66-101 us, and at 20 MS/s a block is only 819 us of
        air time -- measured end to end, the host had 5 to 11 percent
        headroom at 5-40 MS/s and could NOT keep up at all at 61.44 MS/s
        (0.86x). At that margin any hiccup -- a garbage collection, the
        sweep thread taking the interpreter lock, Windows scheduling --
        starves the transmitter, and a starved transmitter is a signal that
        visibly blinks off and on. Kfir reported exactly that.

        Rendering once and replaying is also simply what an arbitrary
        waveform generator IS. After this the streaming path is a memcpy.
        """
        iq = np.asarray(loop, dtype=np.complex64)
        with self._tx_lock:
            self._tx_render_locked(iq)
        super().tx_set_loop(iq)

    def tx_set_dither(self, on: bool) -> None:
        """Toggling dither has to re-render: the dither is baked into the
        fixed-point buffer the streaming path replays, so nothing would
        change until the next waveform rebuild otherwise."""
        on = bool(on)
        if on == self.tx_dither:
            return
        self.tx_dither = on
        with self._tx_lock:
            loop = getattr(self, "_tx_loop_ref", None)
            if loop is not None:
                self._tx_render_locked(loop)

    def _tx_render_locked(self, iq: np.ndarray) -> None:
        """Caller holds _tx_lock. Renders at the CURRENT sample width."""
        dtype, scale, lo, hi = self._tx_fixed()
        n = iq.size
        v = np.empty(2 * n, dtype=np.float32)
        v[0::2] = iq.real
        v[1::2] = iq.imag
        np.multiply(v, scale, out=v)
        if self.tx_dither:
            # Triangular one-LSB dither, and it belongs HERE rather than in
            # build_waveform, because one LSB is 1/2048 at 12 bits and 1/128
            # in the 8-bit oversample format -- the amount depends on the
            # sample width the stream is actually in.
            #
            # What it buys: rounding error that repeats with the signal comes
            # out as discrete spurs at multiples of the carrier offset, and a
            # tone whose period is a short whole number of samples (2.5 MHz
            # at 20 MS/s is eight) repeats exactly. Measured on that case
            # with 12 dB of backoff: worst spur -62.5 dBc plain, -94.4 dBc
            # dithered, for about 5 dB more broadband noise. Kfir reported
            # those spurs; this is the fix.
            #
            # Seeded, not random per render: still uncorrelated with any
            # signal, but the same waveform renders to the same bytes twice,
            # which keeps the tests exact.
            rng = getattr(self, "_tx_dither_rng", None)
            if rng is None:
                rng = self._tx_dither_rng = np.random.default_rng(0xD17E)
            d = (rng.random(v.size, dtype=np.float32)
                 + rng.random(v.size, dtype=np.float32) - 1.0)
            np.add(v, d, out=v)
        np.rint(v, out=v)
        np.clip(v, lo, hi, out=v)
        packed = np.empty(2 * n, dtype=dtype)
        np.copyto(packed, v, casting="unsafe")
        if self._tx_dual:
            # x2 wants the two channels interleaved sample by sample:
            # I0 Q0 I1 Q1 I0 Q0 ... where the subscript is the CHANNEL, not
            # the sample index. Both ports carry the same waveform, so each
            # IQ pair simply goes out twice. Doing it once here keeps the
            # streaming path a memcpy in dual mode too.
            packed = np.repeat(packed.reshape(-1, 2), 2, axis=0).ravel()
        # A short loop would mean short sends: min(BLOCK, loop) samples per
        # call, so a 32k loop at 61.44 MS/s doubles the number of driver
        # calls and the per-call overhead with it -- measured as a 21%
        # real-time shortfall at the top rate, which is a transmitter that
        # blinks. The loop is seamless by construction, so laying several
        # laps end to end is exactly the same signal, and then every send is
        # a full block. Costs a few MB at most; short loops are small.
        if 0 < n < TX_MIN_RENDER:
            laps = -(-TX_MIN_RENDER // n)
            packed = np.tile(packed, laps)
            n *= laps
        self._tx_packed = packed
        self._tx_packed_n = n
        self._tx_packed_fmt = self._tx_fmt
        # int16 entries per per-channel sample: I and Q, doubled again when
        # both ports are interleaved into the same buffer.
        self._tx_stride = 4 if self._tx_dual else 2
        self._tx_packed_dual = self._tx_dual
        self._tx_wrap = None

    def tx_write_loop(self, pos: int, n: int, timeout_ms: int = 2000) -> int:
        """
        Stream n samples of the pre-rendered loop, starting at pos, wrapping
        at the end. Returns the position to carry into the next call.

        The common case hands libbladeRF a pointer straight into the
        rendered buffer with no copy at all. Only the one block per lap that
        straddles the join needs assembling, and that is a memcpy.
        """
        self._tx_check()
        with self._tx_lock:
            self._tx_guard()
            self._tx_ensure_format_locked()
            if (self._tx_packed is None
                    or self._tx_packed_fmt != self._tx_fmt
                    or self._tx_packed_dual != self._tx_dual):
                # Oversample flipped the sample width underneath us, so the
                # rendered copy is the wrong shape now. Re-render it here
                # rather than send samples of the wrong width.
                loop = getattr(self, "_tx_loop_ref", None)
                if loop is None:
                    raise RadioError("no waveform loaded for the transmitter")
                self._tx_render_locked(loop)
            if not self._tx_streaming:
                self._tx_start_locked()
            total = self._tx_packed_n
            st = self._tx_stride
            n = int(min(n, total))
            pos = int(pos) % total
            end = pos + n
            if end <= total:
                buf = self._tx_packed[st * pos:st * end]
            else:
                head = total - pos
                if self._tx_wrap is None or self._tx_wrap.size < st * n \
                        or self._tx_wrap.dtype != self._tx_packed.dtype:
                    self._tx_wrap = np.empty(st * n, dtype=self._tx_packed.dtype)
                buf = self._tx_wrap[:st * n]
                buf[:st * head] = self._tx_packed[st * pos:]
                buf[st * head:] = self._tx_packed[:st * (n - head)]
            # num_samples counts INTERLEAVED entries, not samples per channel.
            # libbladeRF.h does not say which, and it matters: measured on the
            # board, passing the per-channel count in x2 made the driver
            # consume only half of each buffer -- 2.31x real time, so every
            # other half-window of the waveform was skipped. The frequency
            # still looked right (the loop is periodic, so alternating halves
            # of it are still the same tone) which is exactly how this would
            # have shipped unnoticed. The rate is what gives it away.
            rc = self.lib.bladerf_sync_tx(
                self._dev, buf.ctypes.data_as(c_void_p),
                c_uint(n * (2 if self._tx_dual else 1)),
                None, c_uint(int(timeout_ms)))
            if rc != 0:
                raise RadioError(f"transmit: {self._err(rc)} ({rc})", rc)
            return end % total

    def tx_write(self, iq: np.ndarray, timeout_ms: int = 2000) -> bool:
        self._tx_check()
        with self._tx_lock:
            self._tx_guard()
            self._tx_ensure_format_locked()
            if not self._tx_streaming:
                self._tx_start_locked()
            buf = self._tx_pack(iq)
            rc = self.lib.bladerf_sync_tx(
                self._dev, buf.ctypes.data_as(c_void_p), c_uint(iq.size),
                None, c_uint(int(timeout_ms)))
            if rc != 0:
                raise RadioError(f"transmit: {self._err(rc)} ({rc})", rc)
        return False

    def tx_burst(self, iq: np.ndarray, at_ts: Optional[int] = None,
                 timeout_ms: int = 2000) -> Optional[int]:
        """
        One burst, keyed on for exactly these samples and off afterwards.

        BURST_START and BURST_END in the same call is the supported way to
        send a self-contained burst: the FPGA holds the transmitter off
        outside it instead of streaming zeros, so the off state is genuinely
        off rather than a very quiet carrier.

        NOT used by the generator panel, and the reason is worth recording.
        Metadata format is a property of the DEVICE, not of one direction:
        measured here, configuring a META TX stream while the RX stream is
        running fails outright with BLADERF_ERR_INVAL (-3), and starts
        working the moment RX is stopped or is itself put into a META
        format. Since the sweep runs continuously alongside the generator,
        that rules it out, and burst mode gates the samples instead (see
        _burst_gate). This is kept because it does work when RX is idle,
        it is covered by the hardware suite, and it is the only route to a
        genuinely keyed-off transmitter.
        """
        self._tx_check()
        with self._tx_lock:
            self._tx_guard()
            want = FMT_SC8_Q7_META if self._oversample else FMT_SC16_Q11_META
            if want != self._tx_fmt:
                was = self._tx_streaming
                if was:
                    self._tx_stop_locked()
                self._tx_fmt = want
                self._tx_configured = False
                if was:
                    self._tx_start_locked()
            if not self._tx_streaming:
                self._tx_start_locked()
            meta = BladerfMetadata()
            meta.flags = META_FLAG_TX_BURST_START | META_FLAG_TX_BURST_END
            if at_ts is None:
                meta.flags |= META_FLAG_TX_NOW
                meta.timestamp = 0
            else:
                meta.timestamp = int(at_ts)
            buf = self._tx_pack(iq)
            rc = self.lib.bladerf_sync_tx(
                self._dev, buf.ctypes.data_as(c_void_p), c_uint(iq.size),
                byref(meta), c_uint(int(timeout_ms)))
            if rc != 0:
                raise RadioError(f"transmit burst: {self._err(rc)} ({rc})", rc)
            end = (int(meta.timestamp) if at_ts is None else int(at_ts)) + iq.size
            return end

    def tx_timestamp(self) -> Optional[int]:
        if not OPT.get("bladerf_get_timestamp", False) or not self._dev:
            return None
        v = c_uint64()
        try:
            if self.lib.bladerf_get_timestamp(self._dev, c_int(DIR_TX),
                                              byref(v)) == 0:
                return int(v.value)
        except Exception:
            pass
        return None

    # Both of these hold _tx_lock, and that is not belt-and-braces: the TX
    # bias tee is the ONE device reconfiguration this application performs
    # from the GUI THREAD. _sg_toggle_bias is a checkbox handler, so a tick
    # ran bladerf_set_bias_tee straight down the shared handle while the
    # sweep thread sat in bladerf_sync_rx and the generator thread in
    # bladerf_sync_tx -- which is bug 17 exactly, and against the invariant
    # stated at the top of this section ("_tx_lock is held for every TX call
    # and for every device-wide operation"). Bug 17 measured what that class
    # of race costs when it does bite: LIBUSB_ERROR_NOT_FOUND and then a
    # NIOS error cascade, i.e. the USB channel gone rather than one failed
    # call. Every other tx_* WRITE already took the lock; this one was
    # missed because it arrived with the generator panel rather than with
    # the streaming code.
    #
    # The cost is that a tick can wait for the block tx_write_loop is
    # holding the lock across -- TX_BUFFER_SIZE/fs, so tens of
    # milliseconds at a low sample rate. That is a bounded pause on one
    # deliberate click, against an unbounded one when the channel dies.
    def tx_get_bias_tee(self) -> Optional[bool]:
        if not self._has("bladerf_get_bias_tee") or not self._dev:
            return None
        with self._tx_lock:
            v = c_bool()
            rc = self.lib.bladerf_get_bias_tee(self._dev, self._tx_ch,
                                               byref(v))
            return bool(v.value) if rc == 0 else None

    def tx_set_bias_tee(self, on: bool) -> None:
        if not self._has("bladerf_set_bias_tee"):
            raise RadioError("this driver does not support bias tee control")
        with self._tx_lock:
            rc = self.lib.bladerf_set_bias_tee(self._dev, self._tx_ch,
                                               c_bool(bool(on)))
        if rc != 0:
            raise RadioError(f"TX bias tee: {self._err(rc)} ({rc})", rc)

    def tx_trigger_init(self, signal: int) -> None:
        if not self._has("bladerf_trigger_init"):
            raise RadioError("this driver does not support triggering")
        trig = BladerfTrigger()
        rc = self.lib.bladerf_trigger_init(self._dev, self._tx_ch,
                                           c_int(signal), byref(trig))
        if rc != 0:
            raise RadioError(f"TX trigger init: {self._err(rc)} ({rc})", rc)
        self._tx_trig = trig

    def tx_trigger_arm(self, role: int) -> None:
        if self._tx_trig is None:
            raise RadioError("tx_trigger_init() was not called")
        self._tx_trig.role = role
        rc = self.lib.bladerf_trigger_arm(
            self._dev, byref(self._tx_trig), c_bool(role != TRIG_ROLE_DISABLED),
            c_uint64(0), c_uint64(0))
        if rc != 0:
            raise RadioError(f"TX trigger arm: {self._err(rc)} ({rc})", rc)

    def tx_trigger_fire(self) -> None:
        if self._tx_trig is None:
            raise RadioError("tx_trigger_init() was not called")
        rc = self.lib.bladerf_trigger_fire(self._dev, byref(self._tx_trig))
        if rc != 0:
            raise RadioError(f"TX trigger fire: {self._err(rc)} ({rc})", rc)


@dataclass
class Emitter:
    kind: str                    # cw | drift | burst | modulated | comb
    freq: float
    power_dbm: float
    bandwidth: float = 0.0
    label: str = ""
    period_s: float = 0.10
    duty: float = 0.06
    drift_hz: float = 50e3
    drift_period_s: float = 12.0
    harmonics: int = 0
    rolloff_db: float = 4.0


class Simulated(Source):
    """
    A synthetic bench so the interface is usable with no radio attached:
    ISM carriers, BLE advertising bursts, a wideband WiFi-ish channel, a
    drifting beacon, and the harmonic comb of a 38.4 MHz reference clock.
    The comb is deliberate -- reference harmonics landing in band is exactly
    what this instrument gets pointed at.
    """

    name = "Simulated (no radio attached)"
    FS0_DBM = 10.0               # full scale at 0 dB gain
    NOISE_DBFS = -68.0           # converter noise across fs, ~10 dB NF

    # The two ports see different antennas in reality, so give them
    # different levels -- otherwise the RX1/RX2 button looks broken when
    # there is no radio attached.
    RX2_OFFSET_DB = -4.0

    def __init__(self, channel: int = 0) -> None:
        super().__init__()
        self._ch_index = int(channel) if int(channel) in (0, 1, RX_BOTH) else 0
        self.emitters = [
            Emitter("cw", 433.92e6, -42, label="433.92 ISM"),
            Emitter("modulated", 868.3e6, -55, bandwidth=250e3),
            Emitter("modulated", 915e6, -48, bandwidth=1.2e6),
            Emitter("drift", 1575.42e6, -68, drift_hz=120e3, drift_period_s=9),
            Emitter("burst", 2402e6, -38, bandwidth=1e6, period_s=0.08, duty=0.05),
            Emitter("burst", 2426e6, -41, bandwidth=1e6, period_s=0.11, duty=0.05),
            Emitter("modulated", 2437e6, -38, bandwidth=18e6, label="WiFi ch6"),
            Emitter("cw", 2450e6, -30, label="DUT carrier"),
            Emitter("modulated", 5800e6, -52, bandwidth=20e6),
            Emitter("comb", 38.4e6, -70, harmonics=160, rolloff_db=3),
        ]
        self._rng = np.random.default_rng(0)
        self._t0 = QtCore.QElapsedTimer()
        self._t0.start()
        self._ph = {}
        self._tx_state_init()

    @property
    def channel(self) -> int:
        return self._ch_index

    @property
    def port(self) -> str:
        return ("RX1+RX2" if self._ch_index == RX_BOTH
                else f"RX{self._ch_index + 1}")

    @property
    def info(self) -> str:
        return f"{self.name}  {self.port}"

    @property
    def dual_rx(self) -> bool:
        return self._ch_index == RX_BOTH

    def read_dual(self, n: int):
        """
        Both ports at once. RX2 is generated as the same scene at
        RX2_OFFSET_DB, which is what _amp() already does per port -- so the
        two traces differ visibly and the banners have something to label.
        """
        if not self.dual_rx:
            raise RadioError("read_dual() needs the port set to RX1+RX2")
        keep = self._ch_index
        try:
            self._ch_index = 0
            rx1 = self.read(n)
            self._ch_index = 1
            rx2 = self.read(n)
        finally:
            self._ch_index = keep
        return rx1, rx2

    def set_channel(self, index: int) -> None:
        self._ch_index = int(index) if int(index) in (0, 1, RX_BOTH) else 0

    @property
    def full_scale_dbm(self) -> float:
        return self.FS0_DBM - self._gain

    # -- section 5: fake but labelled-as-fake board health, so the health
    # panel and self-tests are demonstrable with no hardware at all. Numbers
    # here are made up to look plausible -- never trust them as real
    # readings, which is exactly why every UI string that shows them also
    # shows "(simulated)".
    def lib_version(self) -> str: return "2.5.0 (simulated)"
    def fw_version(self) -> str: return "2.4.0 (simulated)"
    def fpga_version(self) -> str: return "0.15.0 (simulated)"

    def rfic_temperature(self) -> Optional[float]:
        return 27.0 + 2.0 * np.sin(self._t0.nsecsElapsed() / 1e9 / 20.0)

    def power_source(self) -> Optional[int]:
        return PS_USB_VBUS

    def pmic(self, reg: int) -> Optional[float]:
        return {PMIC_VOLTAGE_BUS: 4.9, PMIC_VOLTAGE_SHUNT: 0.02,
                PMIC_CURRENT: 0.35, PMIC_POWER: 1.7}.get(reg, 0.0)

    def rfic_rssi(self) -> Optional[Tuple[float, float]]:
        return (0.0, -70.0 + self._gain)

    def get_bias_tee(self) -> Optional[bool]:
        return getattr(self, "_bias_tee", False)

    def set_bias_tee(self, on: bool) -> None:
        self._bias_tee = bool(on)

    def get_gain_mode(self) -> Optional[int]:
        return getattr(self, "_gain_mode", GAIN_MGC)

    def set_gain_mode(self, mode: int) -> None:
        self._gain_mode = int(mode)

    def gain_stages(self) -> List[str]:
        return ["full"]

    def get_gain_stage(self, stage: str) -> Optional[int]:
        return int(self._gain) if stage == "full" else None

    def set_gain_stage(self, stage: str, db: int) -> None:
        if stage == "full":
            self.set_gain(db)

    def loopback_supported(self, mode: int) -> bool:
        return mode in (LB_NONE, LB_RFIC_BIST)

    def set_loopback(self, mode: int) -> None:
        if not self.loopback_supported(mode):
            raise RadioError("loopback mode not supported (simulated)")
        self._loopback = int(mode)

    def get_loopback(self) -> Optional[int]:
        return getattr(self, "_loopback", LB_NONE)

    def counter_self_test(self, n_samples: int = 65536) -> Tuple[int, int]:
        # Nothing to actually corrupt in software -- reports a clean pass so
        # the panel is exercisable, but this proves nothing about a real
        # FPGA-to-USB path. Section 6's mock library is what would actually
        # inject a gap to prove the *checking* code works.
        return int(n_samples), 0

    # -- section 5.1: a fake trigger that fires every ~300 ms, so triggered
    # capture is demonstrable with no hardware and no DUT wired up.
    TRIG_FIRE_PERIOD_S = 0.3

    def trigger_init(self, signal: int) -> None:
        self._trig_signal = int(signal)
        self._trig_role = TRIG_ROLE_DISABLED
        self._trig_armed_at = None
        self._trig_fired_manually = False

    def trigger_arm(self, role: int) -> None:
        if not hasattr(self, "_trig_signal"):
            raise RadioError("trigger_init() was not called")
        self._trig_role = int(role)
        self._trig_armed_at = (self._t0.nsecsElapsed() / 1e9
                               if role != TRIG_ROLE_DISABLED else None)
        self._trig_fired_manually = False

    def trigger_fire(self) -> None:
        if getattr(self, "_trig_role", TRIG_ROLE_DISABLED) != TRIG_ROLE_MASTER:
            raise RadioError("trigger is not armed as master")
        self._trig_fired_manually = True

    def _trig_has_fired(self) -> bool:
        role = getattr(self, "_trig_role", TRIG_ROLE_DISABLED)
        if role == TRIG_ROLE_DISABLED or self._trig_armed_at is None:
            return False
        if role == TRIG_ROLE_MASTER:
            return self._trig_fired_manually
        # SLAVE: fires on its own every TRIG_FIRE_PERIOD_S, like a DUT toggling
        # a GPIO on a fixed schedule -- enough to demonstrate the feature.
        elapsed = self._t0.nsecsElapsed() / 1e9 - self._trig_armed_at
        return elapsed >= self.TRIG_FIRE_PERIOD_S

    def trigger_state(self) -> Optional[Tuple[bool, bool, bool]]:
        if not hasattr(self, "_trig_signal"):
            return None
        role = self._trig_role
        armed = role != TRIG_ROLE_DISABLED
        fired = self._trig_has_fired()
        return armed, fired, fired

    def read_meta(self, n: int, rx_now: bool = True, timeout_ms: int = 300):
        # Real hardware gates on the trigger's own arm state regardless of
        # RX_NOW -- see the comment in Worker._time_domain for why RX_NOW
        # is always on in practice, and match that here.
        role = getattr(self, "_trig_role", TRIG_ROLE_DISABLED)
        if role != TRIG_ROLE_DISABLED and not self._trig_has_fired():
            raise RadioError("receive: timeout (-6)", ERR_TIMEOUT)
        iq = self.read(n)
        ts = getattr(self, "_sim_ts", 0)
        self._sim_ts = ts + n
        return iq, ts, False, n, False, False

    # -- section 5.2: quick tune and oversample, trivially -- there is no
    # real fast-lock table or ADC overclock to fake here, so these just do
    # the equivalent plain operation. Real behaviour (whether it is worth
    # anything at all) is only checkable against real hardware -- see the
    # BladeRF implementation's docstring for what testing found.
    def get_quick_tune_bytes(self) -> bytes:
        return b"sim"

    def schedule_retune(self, freq_hz: float, qt_bytes: bytes) -> float:
        return self.set_freq(freq_hz)

    def cancel_scheduled_retunes(self) -> None:
        pass

    def enable_oversample(self, on: bool) -> None:
        pass

    # -- signal generator, simulated ---------------------------------------
    # The simulated transmitter does something the real one cannot be asked
    # to do on demand: it feeds its own output back into the received scene.
    # So with no radio attached at all, switching the generator on makes the
    # signal appear in the analyzer view, at the right frequency and roughly
    # the right level -- which is what makes the whole SG panel usable, and
    # testable, before anything is plugged in.
    #
    # The coupling figure stands in for a bit of cable and a bit of leakage.
    # It is a made-up number, like everything else in this class.
    SIM_TX_COUPLING_DB = -30.0

    @property
    def tx_supported(self) -> bool:
        return True

    @property
    def tx_reason(self) -> str:
        return ""

    @property
    def tx_port(self) -> str:
        i = getattr(self, "_tx_ch_index", 0)
        return "TX1+TX2" if i == TX_BOTH else f"TX{i + 1}"

    def _tx_state_init(self) -> None:
        self._tx_lock = threading.RLock()
        self._tx_ch_index = 0
        self._tx_on = False
        self._tx_freq = 2.44e9
        self._tx_fs = 20e6
        self._tx_gain = 20.0
        self._tx_bw = 16e6
        self._tx_loop: Optional[np.ndarray] = None
        self._tx_pos = 0
        self._tx_phase = 0.0
        self._tx_ts = 0
        self._tx_bias = False

    def tx_set_channel(self, index: int) -> None:
        index = int(index)
        self._tx_ch_index = index if index in (0, 1, TX_BOTH) else 0

    def tx_set_freq(self, hz) -> float:
        self._tx_freq = float(np.clip(hz, self.tx_freq_min, self.tx_freq_max))
        return self._tx_freq

    def tx_set_sample_rate(self, hz) -> float:
        self._tx_fs = float(np.clip(hz, self.tx_fs_min, self.tx_fs_max))
        return self._tx_fs

    def tx_set_bandwidth(self, hz) -> float:
        self._tx_bw = float(hz)
        return self._tx_bw

    def tx_set_gain(self, db) -> float:
        self._tx_gain = float(np.clip(db, self.tx_gain_min, self.tx_gain_max))
        return self._tx_gain

    @property
    def tx_sample_rate(self) -> float:
        return self._tx_fs

    @property
    def tx_full_scale_dbm(self) -> float:
        return TX_FS0_DBM - (self.tx_gain_max - self._tx_gain)

    def tx_start(self) -> None:
        with self._tx_lock:
            self._tx_on = True

    def tx_stop(self) -> None:
        with self._tx_lock:
            self._tx_on = False

    def tx_force_off(self) -> None:
        self.tx_stop()

    def tx_set_loop(self, loop: np.ndarray) -> None:
        """Take the whole waveform, not the block being sent right now --
        _tx_inject() plays it round and round on its own clock, so it needs
        the complete period rather than whichever slice happens to be in
        flight."""
        with self._tx_lock:
            self._tx_loop = np.ascontiguousarray(
                np.asarray(loop, dtype=np.complex64))
            self._tx_pos = 0.0
        # Also keep the base class's reference: tx_write_loop() streams from
        # it, so overriding this without chaining up left the simulated
        # generator with nothing to send at all.
        super().tx_set_loop(loop)

    def tx_write(self, iq: np.ndarray, timeout_ms: int = 2000) -> bool:
        with self._tx_lock:
            self._tx_on = True
            self._tx_ts += int(iq.size)
        # Pace it the way the hardware would, so the generator thread does
        # not spin a core flat out pretending to be a 20 MS/s converter.
        time.sleep(min(iq.size / max(self._tx_fs, 1.0), 0.25))
        return False

    def tx_burst(self, iq: np.ndarray, at_ts: Optional[int] = None,
                 timeout_ms: int = 2000) -> Optional[int]:
        self.tx_write(iq, timeout_ms)
        return int(self._tx_ts)

    def tx_timestamp(self) -> Optional[int]:
        return int(self._tx_ts)

    def tx_get_bias_tee(self) -> Optional[bool]:
        return bool(getattr(self, "_tx_bias", False))

    def tx_set_bias_tee(self, on: bool) -> None:
        self._tx_bias = bool(on)

    def tx_trigger_init(self, signal: int) -> None:
        self._tx_trig_signal = int(signal)
        self._tx_trig_role = TRIG_ROLE_DISABLED

    def tx_trigger_arm(self, role: int) -> None:
        self._tx_trig_role = int(role)

    def tx_trigger_fire(self) -> None:
        pass

    def _tx_inject(self, n: int, out: np.ndarray) -> None:
        """
        Add whatever the generator is transmitting into the received block.

        Resampled by linear interpolation when the two rates differ, which is
        crude -- it rolls the top of the band off and folds anything above
        the RX rate back in -- but exact in the case that matters most for
        checking a measurement, where both sides are at the same rate. The
        real radio does none of this; it goes out of the SMA and comes back
        in whatever way it is cabled.
        """
        with self._tx_lock:
            if not self._tx_on or self._tx_loop is None or self._tx_loop.size < 2:
                return
            loop = self._tx_loop
            tx_fs, tx_f = self._tx_fs, self._tx_freq
            pos = self._tx_pos
            tx_fs0 = self.tx_full_scale_dbm
            off = tx_f - self._freq
            # Out of the window this radio is looking at: nothing to add, but
            # keep the playback position moving so the waveform does not
            # freeze in time while the analyzer is tuned elsewhere.
            if abs(off) > self._fs / 2.0:
                self._tx_pos = float(
                    pos + n * tx_fs / max(self._fs, 1.0)) % loop.size
                return
            step = tx_fs / max(self._fs, 1.0)
            idx = pos + step * np.arange(n)
            self._tx_pos = float(idx[-1] + step) % loop.size
            i0 = np.floor(idx).astype(np.int64) % loop.size
            i1 = (i0 + 1) % loop.size
            frac = (idx - np.floor(idx)).astype(np.float64)
            seg = (loop[i0] * (1.0 - frac) + loop[i1] * frac)
            rms = float(np.sqrt(np.mean(np.abs(seg) ** 2)))
            if rms <= 0:
                return
            dbm = tx_fs0 + 20.0 * np.log10(rms) + self.SIM_TX_COUPLING_DB
            scale = self._amp(dbm) / rms
            ph = self._tx_phase
            t = np.arange(n) / self._fs
            out += seg * scale * np.exp(1j * (2 * np.pi * off * t + ph))
            self._tx_phase = float((ph + 2 * np.pi * off * n / self._fs)
                                   % (2 * np.pi))

    def _amp(self, dbm: float) -> float:
        if self._ch_index:
            dbm += self.RX2_OFFSET_DB
        return float(10.0 ** ((dbm - self.full_scale_dbm) / 20.0))

    def read(self, n: int) -> np.ndarray:
        fs, fc = self._fs, self._freq
        half = fs / 2.0
        now = self._t0.nsecsElapsed() / 1e9

        amp_n = 10.0 ** (self.NOISE_DBFS / 20.0)
        out = (self._rng.standard_normal(n)
               + 1j * self._rng.standard_normal(n)).astype(np.complex128)
        out *= amp_n / np.sqrt(2.0)

        t = np.arange(n) / fs
        spec = None
        for em in self.emitters:
            tones = []
            if em.kind == "cw":
                tones.append((em.freq, self._amp(em.power_dbm)))
            elif em.kind == "drift":
                ph = 2 * np.pi * now / max(em.drift_period_s, 1e-6)
                tones.append((em.freq + em.drift_hz * np.sin(ph),
                              self._amp(em.power_dbm)))
            elif em.kind == "comb":
                k0 = max(int((fc - half) // em.freq), 1)
                k1 = int((fc + half) // em.freq) + 1
                for k in range(k0, min(k1, em.harmonics) + 1):
                    tones.append((em.freq * k,
                                  self._amp(em.power_dbm
                                            - em.rolloff_db * np.log2(max(k, 1)))))
            else:
                if em.kind == "burst":
                    frac = (now % max(em.period_s, 1e-6)) / max(em.period_s, 1e-6)
                    if frac > em.duty:
                        continue
                bw = max(em.bandwidth, fs / n * 4)
                off = em.freq - fc
                if abs(off) - bw / 2 > half:
                    continue
                if spec is None:
                    spec = np.zeros(n, dtype=np.complex128)
                    self._fbins = np.fft.fftfreq(n, d=1.0 / fs)
                sel = np.abs(self._fbins - off) <= bw / 2.0
                k = int(sel.sum())
                if k == 0:
                    continue
                # Scaled so total baseband power is amp**2:
                #   E|ifft(spec)|^2 = (1/n^2) * k * (amp^2/k) * n^2
                a = self._amp(em.power_dbm)
                v = (self._rng.standard_normal(k)
                     + 1j * self._rng.standard_normal(k)) / np.sqrt(2.0)
                spec[sel] += v * (a / np.sqrt(k)) * n
                continue

            for f_abs, a in tones:
                off = f_abs - fc
                if abs(off) > half * 0.999:
                    continue
                key = round(f_abs)
                p0 = self._ph.get(key, 0.0)
                out += a * np.exp(1j * (2 * np.pi * off * t + p0))
                self._ph[key] = float((p0 + 2 * np.pi * off * n / fs) % (2 * np.pi))

        if spec is not None:
            out += np.fft.ifft(spec)

        # Whatever the simulated generator is transmitting is part of the
        # scene too -- that is what makes the SG panel usable with no radio.
        self._tx_inject(n, out)

        mag = np.abs(out)                     # hard clip like a converter
        over = mag > 1.0
        if np.any(over):
            out[over] /= mag[over]
        return out.astype(np.complex64)


# ============================================================================
#  DSP
#
#  RBW <-> FFT length:  ENBW_bins = N*sum(w^2)/(sum w)^2 depends only on the
#  window shape, so N = fs*ENBW_bins/RBW. This is what makes the noise floor
#  drop ~10*log10(RBW2/RBW1) while CW tone levels hold still, exactly as on a
#  hardware analyzer.
#
#  Amplitude: CW-calibrated.  dBFS = 20*log10(|X[k]|/sum(w)), then + an
#  absolute offset that has to be measured once (see "Cal offset").
# ============================================================================

WINDOWS = ["Gaussian", "Flat Top", "Blackman-Harris", "Nuttall", "Hann",
           "Rectangular"]
# Gaussian, as a fraction of the half-length. 0.25 was chosen by measuring,
# not by taste: it puts ENBW/-3 dB at 1.066, which is the textbook Gaussian
# relation a swept analyzer's IF filter obeys, and holds the first sidelobe
# at -96 dB so the skirts do not invent signals. Wider (0.4) drops the
# sidelobes to -43 dB, narrower buys nothing.
GAUSSIAN_ALPHA = 0.25
DETECTORS = ["Peak", "Average", "Min", "Sample"]
TRACE_MODES = ["Live", "Max Hold", "Min Hold", "Average", "Off"]
_ENBW: dict = {}


def make_window(name: str, n: int) -> np.ndarray:
    k = np.arange(n)
    if name == "Rectangular":
        return np.ones(n)
    if name == "Hann":
        return 0.5 - 0.5 * np.cos(2 * np.pi * k / n)
    if name == "Gaussian":
        # The shape a bench analyzer's RBW filter actually has. Measured
        # against the others: scallop loss -0.67 dB (better than
        # Blackman-Harris at -0.83), sidelobes -96 dB, ENBW 2.257 bins.
        # Flat Top is still the one to use for absolute CW amplitude --
        # -0.01 dB scallop against this one's -0.67 -- and the tooltip on
        # "RBW shape" says so rather than leaving it to be discovered.
        m = (n - 1) / 2.0
        return np.exp(-0.5 * ((k - m) / (GAUSSIAN_ALPHA * m)) ** 2)
    coeff = {
        "Blackman-Harris": (0.35875, 0.48829, 0.14128, 0.01168),
        # minimum-sidelobe Nuttall, ENBW 1.976 bins
        "Nuttall": (0.3635819, 0.4891775, 0.1365995, 0.0106411),
        # flat top: lowest scallop loss, so a tone reads true wherever it
        # falls between bins -- which is why it is the default
        "Flat Top": (0.21557895, 0.41663158, 0.277263158,
                     0.083578947, 0.006947368),
    }[name]
    w = np.zeros(n)
    for i, a in enumerate(coeff):
        w += ((-1) ** i) * a * np.cos(2 * np.pi * i * k / n)
    return w


def enbw_bins(name: str, n: int = 8192) -> float:
    """
    Equivalent noise bandwidth in bins, for the window LENGTH actually used.

    Keyed by length because it matters at short lengths. Every cosine-sum
    window here has an ENBW independent of n to the last digit, but the
    Gaussian does not: defined against (n-1)/2 it runs 3.2% high at n=32 and
    1.6% high at n=64. Since the whole point of the RBW plumbing below is
    that the number in the status bar is the number you set, a 3% error in
    the constant it is derived from is not acceptable.
    """
    key = (name, int(n))
    if key not in _ENBW:
        w = make_window(name, int(n))
        _ENBW[key] = float(n * np.sum(w ** 2) / np.sum(w) ** 2)
    return _ENBW[key]


# FFT lengths with no prime factor above 7. numpy will transform ANY length,
# but a prime one goes through Bluestein and costs 3-5x: measured 15.2 ms at
# n=65521 against 2.8 ms at 65536, and 319 ms against 62 ms at ~1M. Picking
# the nearest smooth length instead keeps the cost while giving a grid dense
# enough (1758 lengths below 4M) to hit any RBW closely.
# 16, lowered from 64 on 2026-08-31. This constant is what sets the
# COARSEST RBW the instrument can offer, and Kfir asked for a higher one:
# "I'm not sure if max RBW is 1MHz like you set, but if there's an option
# to higher RBW, add it as well."
#
# The ceiling is fs*ENBW/N_MIN, because RBW comes from how much TIME the
# window covers and a window cannot be shorter than N_MIN samples. At
# 40 MS/s with the Gaussian that was 40e6*2.2928/64 = 1.433 MHz, so the
# 1/3 menu stopped at 1 MHz -- exactly what he was seeing. Measured for
# each candidate, 40 MS/s Gaussian:
#
#     N_MIN=64  ENBW 2.2928  ceiling  1.433 MHz   menu tops out at 1 MHz
#     N_MIN=32  ENBW 2.3297  ceiling  2.912 MHz   STILL 1 MHz (3 > 2.912)
#     N_MIN=16  ENBW 2.4073  ceiling  6.018 MHz   menu gains 3 MHz
#
# So 32 buys nothing at all -- it lands just under 3 MHz and the menu is
# unchanged, which would have looked like a fix and been none. 16 is the
# first value that actually adds an entry.
#
# What it costs: a 16-sample window is a crude spectral estimate, and its
# ENBW is 5% away from the 64-sample figure. Neither is a problem here,
# because enbw_bins() is keyed by (name, length) -- that keying went in
# for the Gaussian in 5.8 precisely because its ENBW is NOT
# length-independent, and it is what makes a short window still deliver an
# exact RBW rather than one 5% out.
#
# It does NOT make wide sweeps faster, and it is worth being clear about
# that because the two requests arrived together. A full-span sweep is
# 82% bladerf_set_frequency; the capture itself is 64 samples, 1.6 us. A
# coarser RBW shortens something that was never the cost.
N_MIN_FFT = 16
# The shortest window the RBW MENU may be sized from when the sample rate
# cannot move, which is 64 -- what N_MIN_FFT itself was until 2026-08-31.
#
# With fs free, _plan_rbw nudges the rate and any listed RBW is delivered
# exactly (measured 0.000% at 3 MHz). With fs pinned by the generator the
# length is the only knob, and consecutive lengths are about 1/n apart in
# RBW: 6% at n=17, which is a listed value the instrument would miss by
# more than the amber annotation's own threshold. The bug-30 guard caught
# exactly that, at 2.59% against its 1% bound.
#
# So the coarse end of the menu is offered only when the rate can move to
# meet it. Pinned, the menu is what it always was.
N_MIN_EXACT = 64
# The rate BLADERF_FEATURE_OVERSAMPLE runs the AD9361 at. 122.88 MS/s is
# Nuand's number, not a guess, and it is exactly 3x the 40.96 the plain path
# tops out near -- which is where "about a third as many segments" comes
# from.
OVERSAMPLE_FS = 122.88e6
N_MAX_FFT = 1 << 21
_FAST_N: Optional[np.ndarray] = None


def fast_lengths() -> np.ndarray:
    global _FAST_N
    if _FAST_N is None:
        vals = {1}
        for p in (2, 3, 5, 7):
            grown = set()
            for v in vals:
                x = v
                while x <= N_MAX_FFT:
                    grown.add(x)
                    x *= p
            vals |= grown
        _FAST_N = np.array(sorted(v for v in vals
                                  if N_MIN_FFT <= v <= N_MAX_FFT), dtype=np.int64)
    return _FAST_N


def fast_len(want: float, up: bool = True) -> int:
    """Nearest smooth length at or above (or at or below) `want`."""
    s = fast_lengths()
    i = int(np.searchsorted(s, max(want, 1.0)))
    if up:
        return int(s[min(i, s.size - 1)])
    return int(s[max(i - 1, 0)])


# A second, denser grid, for the one case where the rate cannot move.
#
# The 7-smooth grid above is right when fs is ours to nudge: any length will
# do, because the rate closes the gap exactly. When the generator pins the
# rate (5.5 fact 1) the length is the only knob, and a grid whose steps are
# up to 9.4% apart cannot land within 1% of an arbitrary RBW.
#
# Allowing primes up to 19 is a measured trade, not a guess. numpy's cost
# against the nearest smooth length, at three sizes:
#
#   largest prime factor  <= 19        1.2 - 1.8x
#   a large prime factor              8 - 11x   (2.4 ms at n=14000)
#
# so 19 is comfortably inside the cheap region while tripling the density:
# 11518 lengths against 1482, steps at most 4.4% apart, and taking the
# NEAREST member instead of the next one up halves that again. Measured over
# five pinned rates times seven RBWs, the worst realised error is 0.79% --
# under the 1% at which the status bar starts complaining, which is the point.
_CHEAP_N: Optional[np.ndarray] = None


def cheap_lengths() -> np.ndarray:
    global _CHEAP_N
    if _CHEAP_N is None:
        vals = {1}
        for p in (2, 3, 5, 7, 11, 13, 17, 19):
            grown = set()
            for v in vals:
                x = v
                while x <= N_MAX_FFT:
                    grown.add(x)
                    x *= p
            vals |= grown
        _CHEAP_N = np.array(sorted(v for v in vals
                                   if N_MIN_FFT <= v <= N_MAX_FFT),
                            dtype=np.int64)
    return _CHEAP_N


def cheap_len(want: float) -> int:
    """Nearest cheap length to `want`, in either direction."""
    s = cheap_lengths()
    i = int(np.argmin(np.abs(s - max(want, 1.0))))
    return int(s[i])


def rbw_to_n_fixed_fs(rbw: float, fs: float, window: str) -> int:
    """
    Window length for a wanted RBW when the rate is NOT ours to move.

    Same two passes as rbw_to_n() -- the Gaussian's ENBW depends on the
    length, so the first answer has to be re-solved -- but off the denser
    grid and rounding to the nearest member either way.
    """
    n = cheap_len(fs * enbw_bins(window) / max(rbw, 1e-9))
    return cheap_len(fs * enbw_bins(window, n) / max(rbw, 1e-9))


def rbw_to_n(rbw: float, fs: float, window: str, up: bool = True) -> int:
    """
    Window length for a wanted RBW.

    Two passes because enbw_bins() depends on the length for a Gaussian:
    guess with the asymptotic value, then re-solve with the real one.
    """
    n = fast_len(fs * enbw_bins(window) / max(rbw, 1e-9), up)
    return fast_len(fs * enbw_bins(window, n) / max(rbw, 1e-9), up)


def n_to_rbw(n: int, fs: float, window: str) -> float:
    return fs * enbw_bins(window, n) / n


# Smallest interpolation factor the transform is allowed to run at, and it
# is an AMPLITUDE setting rather than a cosmetic one.
#
# Zero-padding does not change a level -- but the absence of it does, because
# without it the trace is only sampled every fs/n hertz and a tone landing
# between two of those samples is read from the skirt of the window rather
# than from its top. That is scallop loss, and it is the whole of the
# span-dependent level error Kfir reported on 2026-09-01: measured over nine
# sub-bin offsets per span, worst 0.53 dB at 1 MHz through 0.57 dB at full
# span with no padding, against 0.14 dB at x2 and 0.035 dB at x4.
#
# x4 costs one FFT of 4n instead of n per segment. Measured warmed, min of
# three: a 201-segment full sweep goes 47.5 -> 52.7 ms of DSP, and that sweep
# spends 3.6 SECONDS retuning the radio (5.24), so it is +0.14% of the sweep.
# A single-segment 20 MHz frame goes 1.4 -> 4.0 ms, which against that frame's
# measured 56 ms is +4.6%. Half a decibel of amplitude honesty is worth 4.6%
# of a frame; this instrument's whole claim about relative levels rests on it.
#
# Note this is NOT the same knob as the RBW. Padding buys display samples,
# never resolution -- see display_nfft.
NFFT_PAD_MIN = 4


def display_nfft(n: int, fs: float, span: float, points: int) -> int:
    """
    Zero-padded FFT length, for DISPLAY resolution only.

    RBW is set by the WINDOW length -- how much time the window covers --
    not by the FFT length. Zero-padding to a longer transform interpolates
    the same spectrum onto a finer grid: it does not change the resolution
    and it does not change a level.

    That separation is what lets a coarse RBW be honoured exactly and still
    draw a smooth trace. Without it, a genuine 3 MHz RBW on a 61 MHz segment
    is a 77-point staircase, which is the reason the old code quietly floored
    the length at 256 and reported an RBW five times finer than the one that
    was asked for.

    NFFT_PAD_MIN is the floor, and it is there for AMPLITUDE rather than for
    looks: at a wide span "points * fs / span" comes out smaller than n, so
    this used to return n and pad nothing at all, leaving up to 0.57 dB of
    scallop loss on any tone that did not happen to land on a bin. See the
    constant.
    """
    if span <= 0.0 or fs <= 0.0 or points <= 0:
        return int(n)
    want = max(int(np.ceil(points * fs / span)), int(n) * NFFT_PAD_MIN)
    if want <= n:
        return int(n)
    return int(min(fast_len(max(want, n)), max(n, 1 << 18)))


def rbw_choices(fs: float, window: Optional[str] = None,
                n_min: int = N_MIN_FFT) -> List[float]:
    """
    The 1 / 3 / 10 menu, capped at what can actually be delivered.

    The window cannot be shorter than N_MIN_FFT samples, so the coarsest
    realisable RBW is fs*ENBW/N_MIN. The old cap of fs/4 promised a 10 MHz
    RBW that the sweep then met with 1.4 MHz. Offering only what can be hit
    is half of making the setting and the readout agree.
    """
    top = (fs * enbw_bins(window, n_min) / n_min) if window else fs / 4
    out, dec = [], 1.0
    while dec <= 1e7:
        # (1, 3) per decade, NOT (1, 3, 10): the decade step already
        # supplies the 10, so 10.0 * dec is the same number as 1.0 * the
        # next dec and the menu listed every decade twice -- 1, 3, 10, 10,
        # 30, 100, 100, ... Reported from the bench 2026-08-30 as "each RBW
        # BW is written twice", which is exactly what it was. The sequence
        # this produces is unchanged; only the repeats are gone.
        for s in (1.0, 3.0):
            v = s * dec
            if 1.0 <= v <= top:
                out.append(v)
        dec *= 10
    return out


# Auto never chooses an RBW coarser than this, whatever the span.
#
# Added 2026-08-31 with the N_MIN_FFT change above, and it exists to stop
# that change moving a behaviour nobody asked to move. Auto aims at
# span/1000, which on a full 5.93 GHz span is 5.93 MHz; before today the
# menu stopped at 1 MHz so Auto got 1 MHz by running out of choices. Raise
# the ceiling and Auto would have silently jumped to 3 MHz -- a 4.8 dB
# higher noise floor and a visibly coarser trace, on the same setting,
# with nothing on screen to explain why.
#
# A coarser filter is a real choice with a real cost, so it stays a manual
# one. 1 MHz is not a guess: it is exactly what Auto delivered yesterday,
# so this constant makes today's Auto bit-for-bit the same as yesterday's.
AUTO_RBW_MAX = 1e6


def auto_rbw(span: float, fs: float, window: Optional[str] = None) -> float:
    target = min(max(span / 1000.0, 1.0), AUTO_RBW_MAX)
    # N_MIN_EXACT rather than N_MIN_FFT: auto_rbw is called with whatever
    # rate the sweep has settled on, including a pinned one, and an Auto
    # that picked an RBW the sweep then missed by 6% would be the worst
    # version of this -- nobody chose it, so nobody would think to doubt it.
    menu = rbw_choices(fs, window, N_MIN_EXACT) or [target]
    below = [v for v in menu if v <= target]
    return below[-1] if below else menu[0]


def spectrum_dbfs(iq: np.ndarray, w: np.ndarray, wsum: float,
                  nfft: int = 0) -> np.ndarray:
    """nfft > len(iq) zero-pads: finer bin spacing, same RBW, same levels."""
    s = np.fft.fftshift(np.fft.fft(iq * w, n=int(nfft) if nfft else None))
    return 20.0 * np.log10(np.maximum(np.abs(s) / wsum, 1e-10))


def _buckets(n_in: int, n_out: int):
    """
    Bucket boundaries, in ONE place so the trace on screen and the mean-power
    trace that measurements integrate line up point for point. Two copies of
    this arithmetic would drift, and a measurement reading a different point
    from the one drawn is the kind of disagreement nobody would ever spot.
    """
    edges = np.linspace(0, n_in, int(n_out) + 1).astype(np.int64)
    starts, counts = edges[:-1], np.diff(edges)
    return starts, counts, starts + counts // 2


def mean_power(freqs: np.ndarray, power: np.ndarray, n_out: int):
    """
    Per-bucket MEAN power, in dB, on the same axis detect() produces.

    This is what channel power and OBW integrate, and it is deliberately NOT
    the trace on screen. A detector picks one bin out of each bucket -- Peak
    by default, and for a good reason, since it is what stops a narrow
    carrier being walked past -- and the bin it picks does not represent the
    bucket's power. Summing maxima over-counts, and it over-counts MORE the
    wider the bucket is, which is to say the wider the span is. So the
    channel power reading was a function of the zoom level: measured 2.8 dB
    of drift between a 2 MHz and a 100 MHz span with the band held perfectly
    still, which is exactly what Kfir reported on 2026-08-30.

    A mean in the LINEAR domain is the bucket's real mean power density, so
    summing it times the bucket width is a Riemann sum of the PSD and does
    not care how the bins were grouped. Averaging in dB would bias low, the
    same trap as everywhere else in this file.

    Note the divisor is the AVERAGE bucket size, not each bucket's own
    count, and that is not a shortcut. n_in is rarely a whole multiple of
    n_out, so counts come out as a mix of (say) 1 and 2. Divide each bucket
    by its own count and every point is a true mean -- but the caller then
    integrates them all against ONE uniform bin width, which quietly gives a
    1-bin bucket the same weight as a 2-bin one. Measured with a CW tone
    parked still: 0.5 to 1.0 dB of error, varying with span, which is the
    very zoom-dependence this function exists to remove. Dividing by the
    average makes the sum telescope back to the raw sum exactly, at any
    ratio. The two agree wherever the buckets are equal.

    The side effect is that this trace is not something to DRAW: on a flat
    noise floor an odd-sized bucket sits a fraction of a dB off its
    neighbours. It is never drawn -- Peak detection is what keeps a narrow
    carrier visible, which is a different job from carrying its power.
    """
    n_in = power.size
    if n_out <= 0 or n_in < 2 * n_out:
        return freqs, power
    starts, counts, mids = _buckets(n_in, n_out)
    lin = np.add.reduceat(10.0 ** (power / 10.0), starts) * (float(n_out)
                                                             / float(n_in))
    return freqs[mids], 10.0 * np.log10(np.maximum(lin, 1e-30))


def detect(freqs: np.ndarray, power: np.ndarray, n_out: int, mode: str):
    """
    Bucket FFT bins down to screen resolution.

    Not cosmetic: with far more bins than pixels, plain subsampling walks
    right past a narrow carrier. Peak detection is the default for that
    reason. Buckets span the whole input -- an earlier version trimmed to a
    round number of buckets and silently lost the top end of the span.
    """
    n_in = power.size
    if n_out <= 0 or n_in < 2 * n_out:
        return freqs, power
    starts, counts, mids = _buckets(n_in, n_out)
    if mode == "Peak":
        out = np.maximum.reduceat(power, starts)
    elif mode == "Min":
        out = np.minimum.reduceat(power, starts)
    elif mode == "Average":
        # power domain -- averaging dB biases low
        lin = np.add.reduceat(10.0 ** (power / 10.0), starts) / counts
        out = 10.0 * np.log10(np.maximum(lin, 1e-30))
    else:
        out = power[mids]
    return freqs[mids], out


def _grid_slots(freqs: np.ndarray, n_out: int, lo: float, hi: float):
    """The EXACT point grid a bench analyzer uses: n_out points, the first ON
    lo and the last ON hi, (hi - lo) / (n_out - 1) apart. Kfir, 2026-09-23:
    "let the user decide the number, even if he wants 1001 points".

    detect() above cannot give that: its buckets are cut by bin INDEX, so the
    points land on bucket centres (700.044 MHz, not 700.000), and it passes
    the raw bins straight through below 2 bins per point, so asking for 1001
    gave 1015 or 1809. A csv laid beside a Keysight one row for row then
    compares different frequencies on every row.

    Each raw bin belongs to the slot of its NEAREST grid point, so the slots
    partition the bins in order and reduceat applies. The two end slots are
    half width (the grid ends on the band edge). A slot can be EMPTY where
    the raw bins are coarser than the grid; callers fill those from the
    nearest raw bin rather than inventing a level.
    """
    grid = np.linspace(lo, hi, int(n_out))
    step = (hi - lo) / (int(n_out) - 1)
    bounds = lo + (np.arange(1, int(n_out)) - 0.5) * step
    starts = np.concatenate(([0], np.searchsorted(freqs, bounds)))
    counts = np.diff(np.concatenate((starts, [freqs.size])))
    return grid, starts.astype(np.int64), counts


def detect_grid(freqs: np.ndarray, power: np.ndarray, n_out: int, mode: str,
                lo: float, hi: float):
    """detect(), on the exact grid of _grid_slots(). Same detector meanings:
    Peak keeps the largest bin in each slot, so a narrow carrier still
    cannot fall between two points."""
    grid, starts, counts = _grid_slots(freqs, n_out, lo, hi)
    full = counts > 0
    out = np.empty(grid.size)
    s = starts[full]
    if mode == "Peak":
        out[full] = np.maximum.reduceat(power, s)
    elif mode == "Min":
        out[full] = np.minimum.reduceat(power, s)
    elif mode == "Average":
        lin = np.add.reduceat(10.0 ** (power / 10.0), s) / counts[full]
        out[full] = 10.0 * np.log10(np.maximum(lin, 1e-30))
    else:
        full[:] = False
    if not full.all():
        # Nearest raw bin -- for "Sample" everywhere, otherwise only in the
        # empty slots.
        near = np.clip(np.round(np.interp(grid, freqs, np.arange(freqs.size))),
                       0, freqs.size - 1).astype(np.int64)
        out[~full] = power[near[~full]]
    return grid, out


def mean_power_grid(freqs: np.ndarray, power: np.ndarray, n_out: int,
                    lo: float, hi: float):
    """mean_power(), on detect_grid()'s axis -- the two must share an axis
    point for point (bug 35), hence one _grid_slots() helper.

    The divisor is the average bins per INTERIOR slot, n_in / (n_out - 1),
    for mean_power()'s reason: channel power integrates these against one
    uniform point spacing, and this makes that sum telescope back to the raw
    sum exactly. The half-width end slots therefore read about half; they
    also stand for half a slot inside the span, so the integral is right.
    """
    grid, starts, counts = _grid_slots(freqs, n_out, lo, hi)
    lin_raw = 10.0 ** (power / 10.0)
    per = max(freqs.size / max(int(n_out) - 1, 1), 1e-30)
    lin = np.empty(grid.size)
    full = counts > 0
    lin[full] = np.add.reduceat(lin_raw, starts[full]) / per
    if not full.all():
        lin[~full] = np.interp(grid[~full], freqs, lin_raw)
    return grid, 10.0 * np.log10(np.maximum(lin, 1e-30))


class GridAccumulator:
    """
    detect_grid() and mean_power_grid(), fed ONE SEGMENT AT A TIME.

    Why it exists (2026-10-07, Kfir: "at Preset it is perfect, but after
    playing with it, it gets really, really slow"). The sweep used to build
    the whole sweep at full FFT resolution -- every bin of every segment, in
    dB -- then sort it and only then reduce it to the ~1400 display points.
    With a hand-set RBW that is millions of bins: full span at 1 kHz RBW is
    39 million, and the log, the shift, the selection, the concatenation and
    the sort over all of them cost 5.8 s per sweep in Python, on top of the
    radio's own retune time. At Preset (Auto RBW) it is a few thousand bins,
    which is why Preset never showed it.

    Here each segment's bins go straight into the display slots and are
    dropped: per slot the max, the min, the linear sum and the count are all
    a reduction can need, and they combine across segments exactly. Nothing
    is ever converted to dB except the 1400 results. The two functions above
    stay as the reference -- tests run both on the same sweep -- and are
    still what a one-shot caller should use.

    Same slot rule as _grid_slots (a bin belongs to the slot of its nearest
    grid point), same empty-slot and Sample rules: a grid point no bin fell
    into takes the nearest bin (detector) or the linear interpolation
    (mean), including across the seam between two segments, which is what
    the per-segment endpoints kept in `ends` are for.

    Input is LINEAR power per bin, already restricted to [lo, hi] and in
    ascending frequency. Segments may arrive in any order.
    """

    def __init__(self, n_out: int, lo: float, hi: float, detector: str,
                 want_mean: bool):
        n_out = int(n_out)
        self.n_out, self.detector, self.want_mean = n_out, detector, want_mean
        self.grid = np.linspace(lo, hi, n_out)
        step = (hi - lo) / (n_out - 1)
        self.bounds = lo + (np.arange(1, n_out) - 0.5) * step
        self.mx = np.zeros(n_out)
        self.mn = np.full(n_out, np.inf)
        self.sm = np.zeros(n_out)
        self.cnt = np.zeros(n_out, dtype=np.int64)
        self.near = np.zeros(n_out)
        self.near_d = np.full(n_out, np.inf)
        self.interp = np.zeros(n_out)
        self.have = np.zeros(n_out, dtype=bool)
        self.n_in = 0
        self.ends: List[Tuple[float, float, float, float]] = []

    def add(self, f: np.ndarray, lin: np.ndarray) -> None:
        if f.size == 0:
            return
        starts = np.empty(self.n_out, dtype=np.int64)
        starts[0] = 0
        starts[1:] = np.searchsorted(f, self.bounds)
        counts = np.diff(np.append(starts, f.size))
        nz = counts > 0
        s = starts[nz]
        if self.detector == "Peak":
            self.mx[nz] = np.maximum(self.mx[nz], np.maximum.reduceat(lin, s))
        elif self.detector == "Min":
            self.mn[nz] = np.minimum(self.mn[nz], np.minimum.reduceat(lin, s))
        if self.detector == "Average" or self.want_mean:
            self.sm[nz] += np.add.reduceat(lin, s)
        self.cnt += counts
        self.n_in += f.size
        # Grid points that fall within this segment's own bins: their
        # nearest bin and their interpolated value are both in hand now.
        g0 = int(np.searchsorted(self.grid, f[0], "left"))
        g1 = int(np.searchsorted(self.grid, f[-1], "right"))
        if g1 > g0:
            gg = self.grid[g0:g1]
            k = np.clip(np.round(np.interp(gg, f, np.arange(f.size))),
                        0, f.size - 1).astype(np.int64)
            # Two segments can overlap -- the last one's centre is clamped
            # at the radio's top frequency -- and then the nearest bin is
            # whichever of the two is nearer, as it was in the merged array.
            dist = np.abs(f[k] - gg)
            take = dist < self.near_d[g0:g1]
            sl = np.arange(g0, g1)[take]
            self.near[sl] = lin[k[take]]
            self.near_d[sl] = dist[take]
            if self.want_mean:
                self.interp[sl] = np.interp(gg[take], f, lin)
            self.have[g0:g1] = True
        self.ends.append((float(f[0]), float(lin[0]),
                          float(f[-1]), float(lin[-1])))

    def _fill_seams(self) -> None:
        """Grid points between two segments' bins, or past the outermost
        ones: resolved against the segment endpoints, which are exactly the
        two raw bins the full-resolution version would have interpolated
        between."""
        miss = ~self.have
        if not miss.any() or not self.ends:
            return
        e = sorted(self.ends)
        ef = np.array([v for a in e for v in (a[0], a[2])])
        el = np.array([v for a in e for v in (a[1], a[3])])
        o = np.argsort(ef, kind="stable")
        ef, el = ef[o], el[o]
        gg = self.grid[miss]
        k = np.clip(np.round(np.interp(gg, ef, np.arange(ef.size))),
                    0, ef.size - 1).astype(np.int64)
        self.near[miss] = el[k]
        self.interp[miss] = np.interp(gg, ef, el)

    def finish(self):
        """(grid, detected dB, mean-power dB or None). Before any offset."""
        self._fill_seams()
        full = self.cnt > 0
        if self.detector == "Peak":
            out = np.where(full, self.mx, self.near)
        elif self.detector == "Min":
            out = np.where(full, self.mn, self.near)
        elif self.detector == "Average":
            out = np.where(full, self.sm / np.maximum(self.cnt, 1), self.near)
        else:
            out = self.near.copy()
        det = 10.0 * np.log10(np.maximum(out, 1e-30))
        mean = None
        if self.want_mean:
            per = max(self.n_in / max(self.n_out - 1, 1), 1e-30)
            lm = np.where(full, self.sm / per, self.interp)
            mean = 10.0 * np.log10(np.maximum(lm, 1e-30))
        return self.grid, det, mean


@dataclass
class Trace:
    mode: str = "Off"
    avg_count: int = 10
    # "View" on a bench analyzer: stop writing to the trace but keep drawing
    # it. Held HERE rather than in the GUI on purpose -- skipping the draw
    # instead would leave what is on screen disagreeing with what a marker,
    # a CSV export or Identify signal reads out of the same trace, which is
    # the sort of lie this app is careful not to tell. The mode is kept, so
    # unfreezing a Max Hold carries on from where it stopped.
    view: bool = False
    _d: Optional[np.ndarray] = field(default=None, repr=False)
    _n: int = 0

    def reset(self):
        self._d, self._n = None, 0

    def update(self, p: np.ndarray) -> Optional[np.ndarray]:
        if self.mode == "Off":
            return None
        if self.view:
            # Frozen. Return what is already held, so every reader of this
            # trace sees exactly what is drawn -- including None, before the
            # first sweep or after Clear traces.
            return self._d
        if self._d is None or self._d.size != p.size:
            self._d, self._n = p.copy(), 1
            return self._d
        if self.mode == "Live":
            self._d = p.copy()
        elif self.mode == "Max Hold":
            np.maximum(self._d, p, out=self._d)
        elif self.mode == "Min Hold":
            np.minimum(self._d, p, out=self._d)
        elif self.mode == "Average":
            self._n = min(self._n + 1, max(self.avg_count, 1))
            a = 1.0 / self._n
            lin = (1 - a) * 10.0 ** (self._d / 10.0) + a * 10.0 ** (p / 10.0)
            self._d = 10.0 * np.log10(np.maximum(lin, 1e-20))
        return self._d


def fmt_hz(hz: float, digits: int = 6) -> str:
    a = abs(hz)
    if a >= 1e9:
        return f"{hz/1e9:.{digits}f} GHz"
    if a >= 1e6:
        return f"{hz/1e6:.{digits}f} MHz"
    if a >= 1e3:
        return f"{hz/1e3:.{max(digits-3,0)}f} kHz"
    return f"{hz:.0f} Hz"


def _harm_list(ks) -> str:
    """
    "2f, 3f, 4f" -> "2f-4f". Consecutive runs collapsed, gaps kept.

    Ten markers means up to nine harmonics in one message, and a list of
    nine is a wall. A run of three or more reads better as a range; two is
    left alone, since "2f-3f" is no shorter than "2f, 3f".
    """
    ks = sorted(int(k) for k in ks)
    out, i = [], 0
    while i < len(ks):
        j = i
        while j + 1 < len(ks) and ks[j + 1] == ks[j] + 1:
            j += 1
        if j - i >= 2:
            out.append(f"{ks[i]}f-{ks[j]}f")
        else:
            out += [f"{k}f" for k in ks[i:j + 1]]
        i = j + 1
    return ", ".join(out)


def fmt_hz_short(hz: float) -> str:
    a = abs(hz)
    if a >= 1e9:
        return f"{hz/1e9:.4g} GHz"
    if a >= 1e6:
        return f"{hz/1e6:.4g} MHz"
    if a >= 1e3:
        return f"{hz/1e3:.4g} kHz"
    return f"{hz:.4g} Hz"


def parse_hz(text: str) -> Optional[float]:
    """
    '2.44G', '915 MHz', '100k' or a bare number. A bare number at or above
    100 kHz is read as Hz, below that as MHz -- so 2450 means 2450 MHz and
    1000000 means 1 MHz, in both cases what was meant.
    """
    if not text:
        return None
    t = text.strip().lower().replace("hz", "").replace(",", "").strip()
    if not t:
        return None
    mult = None
    for sfx, m in (("g", 1e9), ("m", 1e6), ("k", 1e3)):
        if t.endswith(sfx):
            mult, t = m, t[:-1].strip()
            break
    try:
        v = float(t)
    except ValueError:
        return None
    if mult is not None:
        return v * mult
    return v if abs(v) >= 1e5 else v * 1e6


# ============================================================================
#  Signal classification -- "what is that lump?"
#
#  HOW MUCH TO TRUST THIS: it is an inference from two things only, the
#  measured occupied bandwidth and where in the spectrum it sits. Nothing
#  here demodulates anything. LTE and 5G NR of the same width in the same
#  band are genuinely indistinguishable this way, so both are named when
#  that is the case rather than picking one to look clever. Treat it as a
#  well-informed hint that saves you looking up a band table, not as an
#  identification.
#
#  The one piece of real domain detail that makes it useful rather than
#  naive: an LTE carrier does NOT occupy its channel bandwidth. 100 resource
#  blocks x 180 kHz = 18 MHz inside a 20 MHz channel, i.e. 0.9x, and the
#  same ratio holds for 3/5/10/15 MHz (1.4 MHz is 1.08). So a measured
#  ~18 MHz is a 20 MHz LTE channel, and matching 18 against "20" naively
#  would report the wrong channel every time.
# ============================================================================

# (label, lo_hz, hi_hz, note). Common European/Israeli allocations plus the
# ISM and GNSS ranges this bench actually gets pointed at. Not exhaustive,
# and deliberately not presented as if it were.
BAND_PLAN = [
    ("LTE/NR B28 DL",   758e6,  803e6, "700 MHz"),
    ("LTE/NR B28 UL",   703e6,  748e6, "700 MHz"),
    ("LTE/NR B20 DL",   791e6,  821e6, "800 MHz digital dividend"),
    ("LTE/NR B20 UL",   832e6,  862e6, "800 MHz digital dividend"),
    ("GSM900 / B8 UL",  880e6,  915e6, "900 MHz"),
    ("GSM900 / B8 DL",  925e6,  960e6, "900 MHz"),
    ("GSM1800 / B3 UL", 1710e6, 1785e6, "1800 MHz"),
    ("GSM1800 / B3 DL", 1805e6, 1880e6, "1800 MHz"),
    ("UMTS/LTE B1 UL",  1920e6, 1980e6, "2100 MHz"),
    ("UMTS/LTE B1 DL",  2110e6, 2170e6, "2100 MHz"),
    ("LTE B40 TDD",     2300e6, 2400e6, "2300 MHz TDD"),
    ("LTE B41 TDD",     2496e6, 2690e6, "2500 MHz TDD"),
    ("LTE B7 UL",       2500e6, 2570e6, "2600 MHz"),
    ("LTE B38 TDD",     2570e6, 2620e6, "2600 MHz TDD"),
    ("LTE B7 DL",       2620e6, 2690e6, "2600 MHz"),
    ("5G NR n78 TDD",   3300e6, 3800e6, "3.5 GHz C-band"),
    ("5G NR n79 TDD",   4400e6, 5000e6, "4.7 GHz"),
    ("SRD 433 ISM",     433.05e6, 434.79e6, "remotes, sensors"),
    ("SRD 868 ISM",     863e6,  870e6, "LoRa, sub-GHz radios"),
    ("ISM 915 (US)",    902e6,  928e6, "LoRa, sub-GHz radios"),
    ("GPS L2",          1227.6e6 - 12e6, 1227.6e6 + 12e6, "GNSS"),
    ("GPS L1",          1575.42e6 - 12e6, 1575.42e6 + 12e6, "GNSS"),
    ("ISM 2.4 GHz",     2400e6, 2483.5e6, "WiFi, Bluetooth, BLE"),
    ("U-NII-1/2 5 GHz", 5150e6, 5350e6, "WiFi"),
    ("U-NII-2e 5 GHz",  5470e6, 5725e6, "WiFi"),
    ("U-NII-3 / ISM",   5725e6, 5875e6, "WiFi"),
]

# (technology, channel bandwidth, occupied bandwidth actually on air).
# Ordered widest-first only for readability; matching picks the nearest.
CHANNEL_TYPES = [
    ("GSM",          200e3,   200e3),
    ("Bluetooth/BLE", 1e6,      1e6),
    ("BLE 2M",        2e6,      2e6),
    ("UMTS/WCDMA",    5e6,    4.2e6),
    ("LTE",         1.4e6,   1.08e6),
    ("LTE",           3e6,    2.7e6),
    ("LTE",           5e6,    4.5e6),
    ("LTE",          10e6,    9.0e6),
    ("LTE",          15e6,   13.5e6),
    ("LTE",          20e6,   18.0e6),
    ("5G NR",        25e6,   23.9e6),
    ("5G NR",        30e6,   28.9e6),
    ("5G NR",        40e6,   38.9e6),
    ("5G NR",        50e6,   48.9e6),
    ("5G NR",        60e6,   58.9e6),
    ("5G NR",        80e6,   78.9e6),
    ("5G NR",       100e6,   98.9e6),
    ("WiFi",         20e6,   17.8e6),
    ("WiFi",         40e6,   36.8e6),
    ("WiFi",         80e6,   76.8e6),
    ("WiFi",        160e6,  156.8e6),
]

# LTE and NR share these channel widths, so a width match cannot separate
# them and the readout says so instead of guessing.
_SHARED_LTE_NR = (5e6, 10e6, 15e6, 20e6)

# Where each technology can physically be. Width alone is not enough: a
# 942 kHz carrier at 743 MHz was being called "Bluetooth/BLE" purely
# because 1 MHz is the closest width, and Bluetooth only exists at 2.4 GHz.
# Naming a technology that cannot be there is the fastest way to make the
# whole readout untrustworthy. Anything absent from this table (LTE, NR) is
# treated as possible anywhere the radio can tune.
TECH_RANGES = {
    "Bluetooth/BLE": ((2400e6, 2483.5e6),),
    "BLE 2M":        ((2400e6, 2483.5e6),),
    "WiFi":          ((2400e6, 2483.5e6), (5150e6, 5895e6)),
    "GSM":           ((824e6, 960e6), (1710e6, 1910e6)),
    "UMTS/WCDMA":    ((824e6, 900e6), (1710e6, 2170e6)),
}


def _tech_plausible(tech: str, hz: float) -> bool:
    spans = TECH_RANGES.get(tech)
    if not spans:
        return True                     # no constraint recorded
    return any(lo <= hz <= hi for lo, hi in spans)


def bands_at(hz: float) -> List[Tuple[str, str]]:
    """Every band plan entry containing hz, as (label, note)."""
    return [(lab, note) for lab, lo, hi, note in BAND_PLAN if lo <= hz <= hi]


def classify_span(lo_hz: float, hi_hz: float, tol: float = 0.15) -> dict:
    """
    Best guess at what occupies lo_hz..hi_hz.

    tol is the fractional slack allowed when matching the measured width to
    a standard channel. 15%: loose enough for an edge found off a live trace
    (noise, fading and the detector all move it, and a threshold crossing
    sits on the shoulder rather than exactly on the channel edge), tight
    enough not to call a 7 MHz lump a 10 MHz channel -- which 22% did.

    Returns a dict with: occupied, centre, tech, channel, band, note,
    summary. tech is None when nothing plausible matches.
    """
    lo, hi = (float(lo_hz), float(hi_hz)) if hi_hz >= lo_hz else (float(hi_hz), float(lo_hz))
    occupied = hi - lo
    centre = (lo + hi) / 2.0

    # Nearest plausible channel, and separately whether it is near ENOUGH to
    # call a match. Keeping the nearest even when it misses lets the readout
    # say "closest is a 10 MHz channel, 16% off" instead of shrugging --
    # which is what happens in practice on a real carrier, where max-hold
    # picks up the shoulders and reads a little wide.
    nearest = None
    for tech, chan, occ in CHANNEL_TYPES:
        if not _tech_plausible(tech, centre):
            continue
        err = abs(occupied - occ) / occ
        if nearest is None or err < nearest[0]:
            nearest = (err, tech, chan, occ)
    best = nearest if (nearest is not None and nearest[0] <= tol) else None

    # Bands overlap for real: 791-803 MHz is both B20 DL and B28 DL, and
    # picking whichever came first in the table would be an arbitrary lie.
    # Name them all and let the user apply what they know about the site.
    hits = bands_at(centre)
    band = " / ".join(lab for lab, _ in hits)
    note = hits[0][1] if hits else ""

    out = {"occupied": occupied, "centre": centre, "start": lo, "stop": hi,
           "tech": None, "channel": 0.0, "band": band, "note": note,
           "summary": ""}
    if best is None:
        near = ""
        if nearest is not None:
            err, tech_n, chan_n, _ = nearest
            near = (f" (closest: {tech_n} {fmt_hz_short(chan_n)} channel, "
                    f"{err*100:.0f}% off)")
            out["nearest"] = tech_n
            out["nearest_channel"] = chan_n
            out["nearest_err"] = err
        out["summary"] = (f"unrecognised width {fmt_hz_short(occupied)}{near}"
                          + (f" -- in {band}" if band else ""))
        return out

    # No band-based fudging needed beyond TECH_RANGES above: WiFi is already
    # excluded outside ISM/U-NII, and where LTE and WiFi both COULD be (a
    # 20 MHz channel in 2.4 GHz) their occupied widths differ enough
    # (17.8 vs 18.0 MHz) for the nearest match to pick correctly on its own.
    _, tech, chan, _occ = best
    label = tech
    if tech in ("LTE", "5G NR") and chan in _SHARED_LTE_NR:
        label = "LTE or 5G NR"
    out.update(tech=label, channel=chan)
    out["summary"] = (
        f"{label}, {fmt_hz_short(chan)} channel"
        f"  ({fmt_hz_short(occupied)} occupied)"
        + (f"  |  {band}" if band else ""))
    return out


# -- finding the carrier under the cursor (2026-09-26) ----------------------
#
# Kfir: "the signal needs to be pretty strong for you to do it". Measured on
# the simulator before changing anything, with the default Max Hold trace:
# the old finder needed an 18 MHz carrier 8-10 dB above the noise, missed it
# at ANY level once it filled most of the span (its floor was the median of
# the whole trace, which is then the carrier), missed a weak carrier beside
# two strong ones for the same reason, and on a live trace named bare noise
# as a signal in about 1 look in 10. What replaced it, measured the same way:
# wide carriers found from ~3 dB, zoomed-in and crowded cases from ~4 dB,
# widths within ~2%, and 0 false finds in 1,600 looks at noise (including
# right beside a strong carrier). Three ideas, each of which had to be right:
#
#  - the FLOOR comes from quiet blocks of the trace, never from all of it: a
#    straight line sunk onto the lowest block medians, then refitted to the
#    blocks near it. Anything fitted over all the points -- a median, a clip
#    -- is walked up onto a wide or weak carrier, one iteration at a time.
#  - DETECTION is multi-scale, and each scale's noise is MEASURED on the
#    quiet blocks. An 18 MHz carrier 3 dB up is invisible point by point and
#    obvious averaged over 60 points; and neighbouring points are not
#    independent (the RBW spans several), so sigma/sqrt(k) would overstate
#    every bump and invent signals.
#  - the EDGE WALK keeps the old rule -- half the plateau, in dB -- so strong
#    carriers measure as they always did (a CW reads 93 kHz both ways), but
#    walks through a fade that stays above the floor and stops where it
#    climbs into a much stronger neighbour.
#
# Not hardware-verified: the simulator has no stitching ripple and no
# calibration tilt. The floor follows a tilt (it is a line, nudged by the
# quiet points near the cursor) and a stitched sweep needs 1 dB more rise,
# because the segment flatness measured in VERSION 2.md 6.3 bumps the floor.

CARRIER_Z = 6.0                  # detection, in measured noise sigmas
CARRIER_MIN_RISE_DB = 3.0        # plateau above the floor, one segment
CARRIER_MIN_RISE_STITCHED_DB = 4.0


def _cf_mean(x: np.ndarray, k: int) -> np.ndarray:
    """Centred running mean over k points; the window shrinks at the ends."""
    if k <= 1:
        return x.astype(float)
    c = np.concatenate(([0.0], np.cumsum(x, dtype=float)))
    i = np.arange(x.size)
    a = np.clip(i - k // 2, 0, x.size)
    b = np.clip(i + k // 2 + 1, 0, x.size)
    return (c[b] - c[a]) / (b - a)


def _cf_mad(v: np.ndarray) -> float:
    return 1.4826 * float(np.median(np.abs(v - np.median(v))))


def _cf_floor(x: np.ndarray, d: np.ndarray):
    """(line, quiet-point mask, noise sigma) -- see the note above."""
    n = d.size
    nb = int(np.clip(n // 20, 8, 48))
    e = np.linspace(0, n, nb + 1).astype(int)
    mb = np.array([np.median(d[e[k]:e[k + 1]]) for k in range(nb)])
    xb = np.array([np.mean(x[e[k]:e[k + 1]]) for k in range(nb)])

    def line(sel):
        # A slope only when the quiet blocks spread across the screen; from
        # a cluster at one end it would be extrapolated over everything.
        if np.ptp(xb[sel]) < 0.3:
            return np.array([0.0, float(np.median(mb[sel]))])
        return np.polyfit(xb[sel], mb[sel], 1)

    def points(sel):
        m = np.zeros(n, bool)
        for k in np.flatnonzero(sel):
            m[e[k]:e[k + 1]] = True
        return m

    kb = np.ones(nb, bool)
    pb = line(kb)
    for _ in range(4):                           # sink onto the quiet blocks
        new = mb <= np.polyval(pb, xb)
        if new.sum() < 3:
            break
        kb, pb = new, line(new)
    s = 0.05
    for _ in range(3):                           # widen to every quiet block
        m = points(kb)
        s = max(_cf_mad(d[m] - np.polyval(pb, x[m])), 0.05)
        # A block median's scatter, doubled because neighbouring points of a
        # trace are not independent.
        tol = max(3.0 * 2.0 * 1.2533 * s / np.sqrt(n / nb), 0.5)
        new = np.abs(mb - np.polyval(pb, xb)) <= tol
        if new.sum() < 3:
            break
        kb, pb = new, line(new)
    return pb, points(kb), s


def find_carrier(freqs, trace, hz: float, stitched: bool = False):
    """The carrier under hz on a trace in dBm: dict(lo, hi, level, rise) or
    None. Pure -- no Qt, no window -- so it is tested straight, the way
    rx_hazard() is. rise is the plateau's mean height above the floor, dB."""
    f = np.asarray(freqs, float)
    d = np.asarray(trace, float)
    n = d.size
    if n < 16 or f.size != n or not (f[0] <= hz <= f[-1]):
        return None
    good = np.isfinite(d)
    if good.sum() < 16:
        return None
    d = np.where(good, d, np.min(d[good]))
    x = (f - f[0]) / max(f[-1] - f[0], 1e-9)
    i0 = int(np.argmin(np.abs(f - hz)))

    p, quiet, _s = _cf_floor(x, d)
    floor = np.polyval(p, x)
    r = d - floor
    # Nudge by the quiet points NEAR the cursor, which follows a floor that
    # bends (the board's calibration curve is not a line). Quiet points only:
    # everything near the cursor includes the carrier being measured.
    hw = max(n // 5, 20)
    a, b = max(i0 - hw, 0), min(i0 + hw + 1, n)
    near = r[a:b][quiet[a:b]]
    if near.size >= 20:
        off = float(np.median(near))
        floor, r = floor + off, r - off

    # Hovering a shoulder still finds the carrier: look a little either side.
    reach = max(int(n * 0.02), 3)
    lo_r, hi_r = max(i0 - reach, 0), min(i0 + reach + 1, n)
    cq = np.concatenate(([0], np.cumsum(quiet)))
    idx = np.arange(n)
    scales, smooth, sigma = [], {}, {}
    k = 1
    while k <= max(n // 3, 1):
        a = np.clip(idx - k // 2, 0, n)
        b = np.clip(idx + k // 2 + 1, 0, n)
        clean = (cq[b] - cq[a]) == (b - a)      # window wholly quiet
        sm = _cf_mean(r, k)
        pts = sm[clean]
        if pts.size >= max(30, 4 * k):
            scales.append(k)
            smooth[k], sigma[k] = sm, max(_cf_mad(pts), 1e-3)
        k = 2 * k + 1
    best = None
    for k in scales:
        j = lo_r + int(np.argmax(smooth[k][lo_r:hi_r]))
        z = smooth[k][j] / sigma[k]
        if best is None or z > best[0]:
            best = (z, k, j)
    if best is None or best[0] < CARRIER_Z:
        return None
    _z, kd, j = best

    # Walk on the finest smoothing whose noise sits well under half the
    # plateau -- sharp edges on a strong carrier, no false gaps on a weak
    # one -- and never finer than the old fixed kernel.
    old_k = max(int(n * 0.004) | 1, 5)
    kw = kd
    for k in scales:
        if k > kd:
            break
        if sigma[k] <= smooth[kd][j] / 6.0:
            kw = k
            break
    if kw < old_k <= kd:
        kw = old_k
    sm = smooth[kw] if kw in smooth else _cf_mean(r, kw)
    i = lo_r + int(np.argmax(sm[lo_r:hi_r]))
    slack = max(2, kw // 2)
    look = max(3 * slack, n // 60)

    def walk(th, top, step):
        # A dip that stays well above the floor is a fade INSIDE the carrier
        # and is walked through; the edge is where the level is back at the
        # floor (th / 2.5). Climbing well above this carrier's own plateau
        # (top) is a stronger NEIGHBOUR, which is not part of it.
        jj, last, below, deep, up = i, i, 0, 0, 0
        while 0 <= jj + step < n:
            jj += step
            v = sm[jj]
            if v > top:
                up += 1
                if up > slack:
                    break
                continue
            up = 0
            if v >= th:
                last, below, deep = jj, 0, 0
                continue
            below += 1
            deep += v < th / 2.5
            if deep > slack or below > look:
                break
        return last

    plateau = float(sm[i])
    lo = hi = i
    for _ in range(3):                           # re-centre on the mean
        lo = walk(0.5 * plateau, 2.0 * plateau + 6.0, -1)
        hi = walk(0.5 * plateau, 2.0 * plateau + 6.0, +1)
        if hi <= lo:
            return None
        plateau = float(np.mean(r[lo:hi + 1]))
    rise = plateau
    if rise < (CARRIER_MIN_RISE_STITCHED_DB if stitched
               else CARRIER_MIN_RISE_DB):
        return None
    # Re-test what was FOUND, at its own width: a strong carrier nearby lifts
    # the wide smoothing at the cursor enough to pass the test above, and the
    # walk then lands on a noise spike a few points wide. Measured: 7 such
    # phantoms in 720 looks beside a strong carrier before this, 0 after.
    w = hi - lo + 1
    kk = max([k for k in scales if k <= w] or [scales[0]])
    if rise / (sigma[kk] * np.sqrt(kk / max(w, kk))) < CARRIER_Z:
        return None
    return {"lo": float(f[lo]), "hi": float(f[hi]),
            "level": float(floor[i] + sm[i]), "rise": rise}


# -- band power and ACLR (2026-09-26) ----------------------------------------

def band_power_mw(f, pw, rbw: float, lo: float, hi: float):
    """Power in lo..hi off the mean-power trace: (mW or None, partial).

    THE integrator -- channel power and ACLR both call it, so the two can
    never disagree about the same band. The arithmetic is bug 35's: it reads
    power_mean (linear per-bucket means), never the trace on screen; the two
    buckets straddling the edges count by the fraction of them inside, or
    the answer steps with the zoom; and the RBW correction is bin spacing
    over RBW, since sum_k p[k] = sigma^2 * ENBW_bins. partial is True when
    the band runs off the span, so the reading is of what is on screen.
    """
    f = np.asarray(f, float)
    partial = bool(f.size and (lo < f[0] - 1.0 or hi > f[-1] + 1.0))
    sel = (f >= lo) & (f <= hi)
    if sel.sum() < 2 or f.size < 2 or not rbw > 0:
        return None, True
    bin_hz = float(np.mean(np.diff(f)))
    wgt = np.clip((np.minimum(f + bin_hz / 2, hi)
                   - np.maximum(f - bin_hz / 2, lo)) / bin_hz, 0.0, 1.0)
    return (float(np.sum(10.0 ** (np.asarray(pw, float) / 10.0) * wgt)
                  * (bin_hz / rbw)), partial)


# ACLR presets: (label, channel bandwidth, integration bandwidth). The
# integration width is the LTE transmission bandwidth -- resource blocks x
# 180 kHz, 0.9x the channel (1.08 MHz for 1.4) -- and the offsets are whole
# channel spacings, which is how 36.104 / 36.141 define E-UTRA ACLR and what
# an analyzer's LTE ACLR preset sets up. The list is Kfir's.
ACLR_PRESETS = [("LTE 20 MHz", 20e6, 18e6),
                ("LTE 15 MHz", 15e6, 13.5e6),
                ("LTE 10 MHz", 10e6, 9e6),
                ("LTE 5 MHz",  5e6,  4.5e6),
                ("LTE 1.4 MHz", 1.4e6, 1.08e6)]
ACLR_MANUAL = "Manual"
ACLR_OFFSETS_MAX = 3
ACLR_OFFSET_NAMES = ("Adjacent", "Alternate", "3rd")


def aclr_bands(cf: float, integ: float, spacing: float, n_off: int):
    """[(k, lo, hi)] for the carrier (k = 0) and offsets -n..-1, 1..n."""
    out = [(0, cf - integ / 2, cf + integ / 2)]
    for k in range(1, int(n_off) + 1):
        for s in (-1, 1):
            c = cf + s * k * spacing
            out.append((s * k, c - integ / 2, c + integ / 2))
    return out


# ============================================================================
#  Signal generator -- waveform synthesis
#
#  Phase 2 of the bench: the bladeRF's TX side standing in for a Signal Hound
#  VSG60A. The VSG60A's own numbers are the target to measure against:
#  50 MHz - 6 GHz, -55 to +7 dBm, 40 MHz modulation bandwidth, 12.5 kS/s to
#  51.2 MS/s arbitrary I/Q. The bladeRF 2.0 micro reaches 47 MHz - 6 GHz and
#  61.44 MS/s, so the coverage is comparable; the output power is not (+8 dBm
#  typical CW, and only an estimate until calibrated -- see SG_LEVEL_NOTE).
#
#  Everything here builds ONE loop buffer that the TX thread streams round and
#  round. That is how an arbitrary waveform generator actually works, and it
#  matters for three separate reasons:
#
#    1. Cost. Re-synthesising every block in Python could not keep up at tens
#       of MS/s. Building once and replaying costs nothing per block.
#    2. Seamlessness. A buffer that is an exact whole number of cycles long
#       joins to itself with no phase step, so a CW tone stays a clean CW tone
#       instead of being sprayed across the band by a discontinuity every
#       block boundary.
#    3. Exact frequencies. _loop_len() picks a length that makes every
#       requested tone offset land on a whole cycle, so the output frequency
#       is the one that was asked for rather than one quantised to an FFT bin.
#       When that is impossible the offset is snapped instead and the ACTUAL
#       value is reported back and displayed -- never silently wrong.
# ============================================================================

SG_WAVEFORMS = ["CW", "Two-tone", "Multitone", "AWGN", "Chirp",
                "Stepped sweep", "Pulsed CW", "AM", "FM", "PM",
                "ASK", "FSK", "PSK", "QAM", "GMSK", "LTE", "Arb file"]

# Waveforms whose whole point is a repeating envelope rather than a steady
# carrier -- the UI shows the timing controls only for these.
SG_PULSED = ("Pulsed CW",)
SG_DIGITAL = ("ASK", "FSK", "PSK", "QAM", "GMSK")
SG_SWEEPING = ("Chirp", "Stepped sweep")
SG_CELLULAR = ("LTE",)

# ---------------------------------------------------------------------------
#  LTE downlink
#
#  Channel bandwidth -> (resource blocks, FFT size, sample rate). Straight out
#  of 3GPP TS 36.104 table 5.6-1 and the 15 kHz subcarrier spacing: a resource
#  block is 12 subcarriers, so the occupied width is n_rb * 180 kHz, which is
#  always comfortably inside the nominal channel. 20 MHz nominal really is
#  18.0 MHz occupied, and that gap is the guard band, not an error.
#
#  The sample rate is NOT a free choice here. Each bandwidth has exactly one
#  rate that makes 15 kHz spacing come out right, so picking a bandwidth sets
#  the rate, and the UI follows it rather than letting the two disagree.
# ---------------------------------------------------------------------------
LTE_BW = {
    "1.4 MHz": (6, 128, 1.92e6),
    "3 MHz": (15, 256, 3.84e6),
    "5 MHz": (25, 512, 7.68e6),
    "10 MHz": (50, 1024, 15.36e6),
    "15 MHz": (75, 1536, 23.04e6),
    "20 MHz": (100, 2048, 30.72e6),
}
LTE_BW_ORDER = ["1.4 MHz", "3 MHz", "5 MHz", "10 MHz", "15 MHz", "20 MHz"]

# Every rate that is an exact multiple of 15 kHz * a power-of-two-ish
# FFT, which is what a composite multi-carrier signal has to run at:
# one transform carries every cell, so its length must still put
# subcarriers exactly 15 kHz apart. 46.08 = 3072 * 15 kHz and
# 61.44 = 4096 * 15 kHz; both are inside the board's range.
LTE_RATES = (1.92e6, 3.84e6, 7.68e6, 15.36e6, 23.04e6, 30.72e6,
             46.08e6, 61.44e6)
# The AD9361's widest analog channel. Past this the outer carriers are
# filtered by the transmitter itself, so a plan that needs more than
# this is refused rather than sent out lopsided.
LTE_MAX_BW = 56e6
LTE_MAX_CARRIERS = 8

LTE_MODS = [("QPSK", 4), ("16-QAM", 16), ("64-QAM", 64), ("256-QAM", 256)]

# What this generator's LTE is and is not. Shown in the panel, because
# "LTE" on a control is a promise and this one has limits worth stating.
LTE_FIDELITY_NOTE = (
    "Structurally correct LTE downlink, not a conformance signal.\n\n"
    "RIGHT: channel bandwidth and occupied bandwidth, 15 kHz subcarrier "
    "spacing, resource-block granularity, normal or extended cyclic prefix, "
    "slot and subframe and frame timing, the primary synchronisation signal "
    "as a proper Zadoff-Chu sequence, cell-specific reference signals at "
    "their correct resource elements, and a realistic peak-to-average ratio.\n\n"
    "NOT RIGHT: it is not scrambled, coded, or laid out to the letter of "
    "36.211 across every channel, and the secondary synchronisation signal "
    "is a stand-in rather than the exact m-sequence pair. A handset will "
    "not attach to it and a protocol analyser will not decode it.\n\n"
    "So it is the right tool for filters, amplifiers, ACLR, occupied "
    "bandwidth, receiver AGC and front-end compression -- anything that "
    "cares what the signal looks like. It is the wrong tool for testing a "
    "protocol stack.")

# Nuand's own datasheet figure for the bladeRF 2.0 micro: +8 dBm typical CW
# output. Used as the starting point for the level estimate, and deliberately
# adjustable, because the real number depends on the board and the cabling in
# exactly the same way the RX side's absolute dBm does.
TX_FS0_DBM = 8.0

SG_LEVEL_NOTE = (
    "Output level is an ESTIMATE, not a calibrated figure. It starts from "
    "Nuand's +8 dBm typical CW number and follows the gain and the digital "
    "backoff from there. Feed the output into a power meter (or into this "
    "analyzer through a known attenuator) and set 'TX cal offset' until it "
    "agrees. Relative changes -- a 10 dB backoff step, one tone against "
    "another -- are already right without doing that.")

# Digital headroom kept in hand when the level is being solved for. Not zero:
# the pulse shaping, the impairments and the rounding into fixed point can all
# push a sample slightly past the peak the normalisation aimed at, and a
# clipped generator looks exactly like a compressing DUT.
SG_HEADROOM_DB = 1.0

# Crest factor of each waveform at its defaults, MEASURED with
# build_waveform() rather than taken from theory, so the level solver has a
# sensible papr to work with before the first buffer has been built. Once one
# has, the real figure from info["papr_db"] replaces it -- these are only the
# starting point, and "Arb file" cannot be known at all until it is loaded.
SG_PAPR_NOMINAL = {
    "CW": 0.0, "Two-tone": 3.0, "Multitone": 2.5, "AWGN": 11.2,
    "Chirp": 0.0, "Stepped sweep": 0.0, "Pulsed CW": 10.1, "AM": 3.0,
    "FM": 0.0, "PM": 0.0, "ASK": 6.0, "FSK": 0.0, "PSK": 3.9,
    "QAM": 3.6, "GMSK": 0.0, "LTE": 10.6, "Arb file": 6.0,
}

SG_LEVEL_PLAN_NOTE = (
    "Set the level you want out and the gain and digital backoff are worked "
    "out for you. Gain is used first and the digital signal kept as close to "
    "full scale as the headroom allows -- that is the quietest, cleanest way "
    "to reach a level. Only when the level is below what the gain range can "
    "reach on its own does digital backoff take over, and the panel says so, "
    "because backing off digitally costs dynamic range and raises the "
    "quantisation spurs.")

SG_SPUR_NOTE = (
    "Carrier offset is 0 by default, which is the spur-free setting.\n\n"
    "With an offset the carrier no longer sits on the LO, and three things "
    "appear that were invisible before:\n"
    "  - LO leakage, at the tuned frequency (offset below the carrier). A "
    "mixer artifact; typically 30 to 45 dB down.\n"
    "  - The quadrature image, mirrored about the tuned frequency, at minus "
    "the offset. Set by how well I and Q match in the RFIC.\n"
    "  - Quantisation harmonics, at whole multiples of the offset. These "
    "ones were ours: rounding a tone whose period is a short whole number "
    "of samples makes the rounding error periodic too, so it lands as "
    "discrete lines instead of noise. Measured at -62 dBc for a 2.5 MHz "
    "offset with 12 dB of digital backoff. Dither fixes them -- see the "
    "Dither control -- and they are worst when the digital signal is small, "
    "which is the other reason to reach a level with gain rather than with "
    "backoff.\n\n"
    "The first two are the radio, not the software. If you need the carrier "
    "away from the LO, expect them, and read the frequencies off the 'Spurs "
    "at' line so they can be told apart from something the DUT is doing.")

SG_DITHER_NOTE = (
    "Adds a triangular one-LSB dither before the samples are rounded into "
    "fixed point.\n\n"
    "Why: rounding error that repeats with the signal shows up as discrete "
    "spurs at multiples of the carrier offset. Dither decorrelates it, so "
    "the same energy spreads into a flat noise floor instead. Measured on "
    "the worst case here (2.5 MHz offset, 12 dB backoff): the worst "
    "discrete spur drops from -62 dBc to -94 dBc, while the broadband noise "
    "rises about 5 dB. In any narrow resolution bandwidth that is a large "
    "net win, which is why it is on by default.\n\n"
    "Turn it off if you want the last few dB of noise floor and can live "
    "with the spurs -- or when the offset is 0 or exactly a quarter of the "
    "sample rate, where the samples land on exact values and there is no "
    "rounding error to dither in the first place.")

# One PRBS period each, from the standard polynomials. Period is 2^n - 1.
PRBS_TAPS = {"PRBS7": (7, 6), "PRBS9": (9, 5), "PRBS11": (11, 9),
             "PRBS15": (15, 14), "PRBS23": (23, 18)}

# Ceiling on one loop buffer: 8 Mi complex64 = 64 MB. Long enough for a 0.4 s
# repeat at 20 MS/s, and it bounds what a careless dwell time can allocate.
SG_MAX_LOOP = 8 * 1024 * 1024
SG_MIN_LOOP = 1 << 15

# Ceiling on how much PRBS to run out. prbs_bits() is an honest bit-at-a-time
# shift register, which is the readable way to write it but costs a Python
# iteration per bit -- a full PRBS23 period is 8,388,607 of them, about a
# minute, for a pattern nobody needs the whole of on a bench. Past this the
# sequence is truncated and the UI says so.
SG_MAX_PRBS_BITS = 1 << 18


def _lcm(a: int, b: int) -> int:
    from math import gcd
    return abs(a * b) // gcd(a, b) if a and b else 0


def _loop_len(fs: float, offsets, min_len: int = SG_MIN_LOOP,
              max_len: int = SG_MAX_LOOP):
    """
    Shortest buffer length in which every offset in `offsets` is a whole
    number of cycles, padded up past min_len.

    Returns (length, exact). exact=False means no such length fits inside
    max_len, and the caller must snap its offsets to the returned length's
    own grid instead -- see snap_offsets().
    """
    from math import gcd
    f = int(round(fs))
    if f <= 0:
        return min_len, False
    g = f
    for o in offsets:
        o = int(round(abs(float(o))))
        if o:
            g = gcd(g, o)
    if g <= 0:
        return min_len, False
    base = f // g                      # samples in one common period
    if base <= 0 or base > max_len:
        return min(max(min_len, 1 << 20), max_len), False
    n = base * max(1, -(-min_len // base))          # ceil to >= min_len
    if n > max_len:
        n = base * max(1, max_len // base)
    return int(n), True


def snap_offsets(offsets, fs: float, n: int):
    """Move each offset onto the loop buffer's own frequency grid (fs/n), so
    the buffer still joins to itself. Returns the snapped list."""
    step = fs / float(n)
    return [float(round(o / step) * step) for o in offsets]


def _tones(n: int, fs: float, offsets, amps=None, phases=None) -> np.ndarray:
    """Sum of complex exponentials, sampled over one loop buffer."""
    t = np.arange(n, dtype=np.float64) / fs
    out = np.zeros(n, dtype=np.complex128)
    amps = [1.0] * len(offsets) if amps is None else amps
    phases = [0.0] * len(offsets) if phases is None else phases
    for o, a, p in zip(offsets, amps, phases):
        out += a * np.exp(1j * (2.0 * np.pi * float(o) * t + p))
    return out


def prbs_bits(kind: str, n_bits: int) -> np.ndarray:
    """
    Maximal-length shift register sequence, as 0/1 bytes.

    Fibonacci form with the standard taps. The register is never allowed to
    be all-zero (that state is a fixed point that would output a constant).
    """
    order, tap = PRBS_TAPS.get(kind, PRBS_TAPS["PRBS9"])
    reg = np.ones(order, dtype=np.uint8)
    out = np.empty(int(n_bits), dtype=np.uint8)
    for i in range(int(n_bits)):
        bit = reg[order - 1] ^ reg[tap - 1]
        out[i] = reg[order - 1]
        reg[1:] = reg[:-1]
        reg[0] = bit
    return out


def prbs_period(kind: str) -> int:
    order, _ = PRBS_TAPS.get(kind, PRBS_TAPS["PRBS9"])
    return (1 << order) - 1


def _bits_to_symbols(bits: np.ndarray, bits_per_sym: int) -> np.ndarray:
    """Pack bits MSB-first into symbol indices."""
    k = int(bits_per_sym)
    n = bits.size // k
    b = bits[:n * k].reshape(n, k).astype(np.int64)
    idx = np.zeros(n, dtype=np.int64)
    for j in range(k):
        idx = (idx << 1) | b[:, j]
    return idx


def _gray(idx: np.ndarray) -> np.ndarray:
    return idx ^ (idx >> 1)


def constellation(kind: str, order: int) -> np.ndarray:
    """
    Unit-average-power constellation for the given modulation.

    ASK is real and unipolar (2-ASK is plain OOK), PSK is on the unit circle,
    QAM is the square grid. Each is scaled so the mean symbol power is 1, so
    the level readout means the same thing whichever is selected.
    """
    m = int(order)
    if kind == "PSK":
        c = np.exp(2j * np.pi * np.arange(m) / m)
    elif kind == "ASK":
        c = np.arange(m, dtype=np.float64) / max(m - 1, 1) + 0j
    elif kind == "QAM":
        side = int(round(np.sqrt(m)))
        if side * side != m:
            side = 4
        lv = np.arange(side) * 2 - (side - 1)
        i, q = np.meshgrid(lv, lv)
        c = (i + 1j * q).ravel().astype(np.complex128)
    else:                                   # FSK/GMSK carry no amplitude map
        c = np.ones(max(m, 1), dtype=np.complex128)
    p = float(np.mean(np.abs(c) ** 2))
    return c / np.sqrt(p) if p > 0 else c


def rrc_taps(sps: int, alpha: float, span_syms: int = 12) -> np.ndarray:
    """
    Root raised cosine, in the time domain, normalised to unit energy.

    The closed form has removable singularities at t=0 and t = +/- Ts/(4a);
    both are filled in with their limits rather than left to divide by zero.
    """
    a = float(np.clip(alpha, 1e-4, 1.0))
    n = int(sps) * int(span_syms)
    n += (n + 1) % 2                        # odd, so it has a centre tap
    t = (np.arange(n) - (n - 1) / 2.0) / float(sps)
    h = np.empty(n, dtype=np.float64)
    for i, ti in enumerate(t):
        if abs(ti) < 1e-10:
            h[i] = 1.0 - a + 4.0 * a / np.pi
        elif abs(abs(ti) - 1.0 / (4.0 * a)) < 1e-10:
            h[i] = (a / np.sqrt(2.0)) * (
                (1 + 2 / np.pi) * np.sin(np.pi / (4 * a))
                + (1 - 2 / np.pi) * np.cos(np.pi / (4 * a)))
        else:
            num = (np.sin(np.pi * ti * (1 - a))
                   + 4 * a * ti * np.cos(np.pi * ti * (1 + a)))
            den = np.pi * ti * (1 - (4 * a * ti) ** 2)
            h[i] = num / den
    e = np.sqrt(np.sum(h ** 2))
    return h / e if e > 0 else h


def _circular_filter(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    """
    Filter that wraps around the end of the buffer.

    Ordinary convolution would leave a transient at the join, which is
    exactly the discontinuity the whole loop-buffer scheme exists to avoid.
    Doing it as a multiply in the frequency domain makes it circular for
    free, and is far quicker than a direct convolution at these lengths.
    """
    n = x.size
    if h.size > n:
        h = h[:n]
    hp = np.zeros(n, dtype=np.float64)
    hp[:h.size] = h
    hp = np.roll(hp, -(h.size // 2))        # zero-phase: centre tap at index 0
    return np.fft.ifft(np.fft.fft(x) * np.fft.fft(hp))


def _cpm_phase(inc: np.ndarray) -> np.ndarray:
    """
    Integrate a per-sample phase increment into a phase that loops cleanly.

    A CPM waveform (FSK, GMSK) carries its data in accumulated phase, so
    after one buffer the phase has moved by however much the data said. The
    loop only joins up if that total is a whole number of turns, and random
    data does not oblige -- so the buffer would click once per lap.

    Only the REMAINDER past the nearest whole turn is removed, spread evenly
    over the buffer. That is the whole trick: taking out the entire ramp
    instead (which an earlier version of this did) would also cancel any
    genuine mean frequency the data was carrying, quietly moving an
    unbalanced FSK stream off the frequency it was asked for. What is left
    behind is at most half a cycle spread across the whole buffer -- for the
    default 40,000-sample loop that is under 250 Hz of pull at 20 MS/s, and
    it shrinks as the buffer grows.
    """
    n = inc.size
    if n < 2:
        return np.cumsum(inc)
    total = float(np.sum(inc))
    resid = total - 2.0 * np.pi * round(total / (2.0 * np.pi))
    return np.cumsum(inc) - resid * (np.arange(n) + 1.0) / float(n)


def _raised_edges(n: int, high: int, rise: int) -> np.ndarray:
    """One pulse period: `high` samples on, with raised-cosine edges `rise`
    long, inside a buffer of `n`. Soft edges keep the spectral skirts down."""
    g = np.zeros(n, dtype=np.float64)
    high = int(np.clip(high, 0, n))
    if high <= 0:
        return g
    g[:high] = 1.0
    rise = int(np.clip(rise, 0, max(high // 2, 0)))
    if rise > 0:
        e = 0.5 * (1.0 - np.cos(np.pi * (np.arange(rise) + 0.5) / rise))
        g[:rise] = e
        g[high - rise:high] = e[::-1]
    return g


def _gaussian_taps(sps: int, bt: float, span_syms: int = 4) -> np.ndarray:
    """Gaussian frequency pulse for GMSK, normalised to unit area so the
    modulation index stays exactly h=1/2 however wide it is spread."""
    b = float(np.clip(bt, 0.05, 2.0))
    n = int(sps) * int(span_syms)
    n += (n + 1) % 2
    t = (np.arange(n) - (n - 1) / 2.0) / float(sps)
    sigma = np.sqrt(np.log(2.0)) / (2.0 * np.pi * b)
    h = np.exp(-(t ** 2) / (2.0 * sigma ** 2))
    s = float(np.sum(h))
    return h / s if s > 0 else h


@dataclass
class SgConfig:
    """
    The whole signal-generator state, mirroring Config's role on the RX side.

    `on` is the one that actually keys the transmitter. Showing the panel and
    transmitting are deliberately two different things: the checkbox that
    reveals these controls does not put anything on the coax.
    """
    on: bool = False
    channel: int = 0                    # TX1 / TX2
    freq: float = 2.44e9
    fs: float = 20e6
    bandwidth: float = 0.0              # 0 = auto (0.8 * fs)
    gain: float = 20.0
    cal_offset: float = 0.0
    backoff_db: float = 3.0             # digital peak below full scale
    # Say what you want OUT, and let gain and backoff be worked out from it.
    # The other way round -- pick a gain, pick a backoff, read off what that
    # came to -- is how this started, and it is the wrong way round for a
    # generator: on a bench you know the level the DUT needs.
    level_direct: bool = True
    level_dbm: float = -20.0
    # Dither before the converter. See SG_DITHER_NOTE -- it is what stops a
    # non-zero carrier offset throwing quantisation harmonics.
    dither: bool = True
    waveform: str = "CW"
    # Carrier offset within the baseband. ZERO by default, deliberately:
    # any non-zero offset puts LO leakage and the quadrature image at
    # visible, separate frequencies, and used to add quantisation harmonics
    # on top of those. See SG_SPUR_NOTE.
    offset: float = 0.0
    # tones
    tone_spacing: float = 1e6
    n_tones: int = 8
    # noise
    noise_bw: float = 0.0               # 0 = the whole sample rate
    # chirp / stepped sweep
    sweep_span: float = 10e6
    sweep_time: float = 1e-3
    sweep_steps: int = 11
    # pulse
    pulse_period: float = 1e-3
    pulse_width: float = 100e-6
    pulse_rise: float = 1e-6
    # analog modulation
    am_depth: float = 50.0              # percent
    fm_dev: float = 75e3
    pm_dev: float = 1.0                 # radians
    mod_rate: float = 1e3
    # digital modulation
    sym_rate: float = 1e6
    mod_order: int = 4
    rrc_alpha: float = 0.35
    prbs: str = "PRBS9"
    gmsk_bt: float = 0.3
    fsk_dev: float = 0.0                # 0 = auto, sym_rate / 2
    # Impairments. The panel that set these was removed on 2026-08-30 --
    # Kfir does not need them -- so every field below now stays at its
    # default for the life of the process: nothing in the window writes
    # one, and _sg_preset_apply forces them back on recall so a setup
    # saved earlier cannot strand one either.
    #
    # The fields and the engine that honours them stay, deliberately. They
    # are the same shape as Config.quick_tune and Config.oversample after
    # 5.12: a zero here is exactly the state the untouched boxes produced,
    # so removing the controls changes no waveform. And the engine has
    # closed-form tests (image level from a gain imbalance, spur level
    # from a DC offset) that go on proving the maths is right, which is
    # worth more than the few lines deleting them would save.
    imp_dc_i: float = 0.0               # percent of full scale
    imp_dc_q: float = 0.0
    imp_gain_imb: float = 0.0           # dB between I and Q
    imp_quad_err: float = 0.0           # degrees away from 90
    imp_freq_off: float = 0.0           # Hz, on top of the carrier offset
    imp_invert: bool = False            # conjugate: swap the spectrum over
    imp_snr: float = 0.0                # 0 = add no noise
    # LTE downlink. The sample rate is not free here -- see LTE_BW.
    lte_bw: str = "10 MHz"
    lte_data: bool = True               # PDSCH filled, or an idle cell
    lte_mod: int = 4                    # 4 = QPSK, then 16/64/256-QAM
    lte_rb_load: float = 100.0          # percent of resource blocks allocated
    lte_cell_id: int = 0                # 0..503
    lte_ext_cp: bool = False            # extended cyclic prefix
    lte_carriers: int = 1               # cells transmitted at once
    lte_spacing: float = 0.0            # Hz between them, 0 = the channel bandwidth
    # arbitrary waveform
    arb_path: str = ""
    # burst timing / trigger
    burst: bool = False
    burst_on_ms: float = 1.0
    burst_off_ms: float = 9.0
    trigger_role: int = TRIG_ROLE_DISABLED
    trigger_signal: int = TRIG_SIGNAL_MINI_EXP_1


def level_from(gain: float, backoff_db: float, papr_db: float, cal_db: float,
               gain_max: float, fs0_dbm: float = TX_FS0_DBM) -> float:
    """
    Average output power for a given gain and digital backoff.

    The one place this arithmetic is written down. TxWorker._emit_state()
    measures the same thing from the waveform's actual RMS, which is the
    better number once a waveform exists -- this is for the panel to show
    before anything has been built, and for plan_level() to invert.
    """
    return (float(fs0_dbm) - float(gain_max) + float(gain)
            - float(backoff_db) - float(papr_db) + float(cal_db))


def plan_level(target_dbm: float, papr_db: float, cal_db: float,
               gain_min: float, gain_max: float,
               fs0_dbm: float = TX_FS0_DBM,
               headroom_db: float = SG_HEADROOM_DB,
               max_backoff_db: float = 60.0) -> dict:
    """
    Work backwards from a wanted output level to a gain and a digital backoff.

    The forward model, which is exactly what TxWorker._emit_state() reports:

        out_dBm = fs0 - (gain_max - gain) - backoff - papr + cal

    fs0 is full-scale-sine output at maximum gain, papr is the waveform's own
    crest factor (so the answer is average power, which is what a power meter
    reads), and cal is the user's measured correction.

    Inverting it has one free choice -- how to split the attenuation between
    the gain stage and the digital signal -- and the split is not arbitrary:

      * Gain first, always. Digital backoff throws away converter bits, and
        the quantisation spurs measured at a non-zero carrier offset are
        worst exactly when the digital signal is small (-76 dBc at 3 dB of
        backoff, -62 dBc at 12 dB).
      * Only when the gain range runs out at the bottom does backoff take
        over, and the caller is told, because past that point the level is
        being bought with dynamic range.

    Gain is rounded to whole dB because bladerf_set_gain takes an int, and
    the backoff then absorbs the rounding so the level itself stays exact.

    Pure function, so it is unit-tested against the forward model rather than
    against itself.
    """
    papr_db = float(papr_db)
    cal_db = float(cal_db)
    head = max(0.0, float(headroom_db))
    span = float(fs0_dbm) - float(gain_max)          # gain-independent part

    def out_for(gain, backoff):
        return level_from(gain, backoff, papr_db, cal_db, gain_max, fs0_dbm)

    gain = round(float(target_dbm) - cal_db + papr_db + head - span)
    # Clamped to the whole decibels INSIDE the range, not to the range
    # itself: the hardware minimum is -23.75 dB, and clipping to that would
    # hand back a fractional gain that the whole-dB control then rounds to
    # -24 -- so the level would come out a quarter of a dB low with nothing
    # on screen to explain it.
    gain = float(np.clip(gain, np.ceil(gain_min), np.floor(gain_max)))
    backoff = span + gain - papr_db + cal_db - float(target_dbm)

    note = None
    if backoff < head:
        # Asked for more than the transmitter has. Sit at the ceiling and say
        # what the ceiling is -- silently running 6 dB low is the failure
        # mode that gets blamed on the DUT.
        backoff = head
        note = (f"cannot reach {target_dbm:+.1f} dBm -- the most this "
                f"waveform can do is {out_for(gain, head):+.1f} dBm "
                f"(gain is at its {gain_max:.0f} dB maximum)")
    elif backoff > max_backoff_db:
        backoff = max_backoff_db
        note = (f"cannot reach {target_dbm:+.1f} dBm -- the quietest this "
                f"can go is {out_for(gain, max_backoff_db):+.1f} dBm. Put an "
                f"attenuator on the output for anything lower")
    elif backoff > head + 0.5:
        note = (f"{backoff - head:.1f} dB of it is digital backoff: the gain "
                f"stage bottoms out at {gain_min:.0f} dB. Costs dynamic "
                f"range and raises the quantisation spurs -- an attenuator "
                f"on the output would be cleaner")
    return {"gain": gain, "backoff_db": float(backoff),
            "achieved_dbm": float(out_for(gain, backoff)), "note": note}


def build_waveform(c: SgConfig):
    """
    Turn an SgConfig into one seamless loop buffer.

    Returns (samples complex64, info dict). `info` carries what actually
    came out as opposed to what was asked for -- snapped offsets, the real
    symbol rate, PAPR, the estimated output level -- because for several of
    these the exact request cannot be honoured and quietly rounding it would
    be the worst of the options.
    """
    fs = float(max(c.fs, 1.0))
    warn = []
    exact = True
    off = float(c.offset)
    kind = c.waveform

    if kind == "CW":
        n, exact = _loop_len(fs, [off])
        if not exact:
            off, = snap_offsets([off], fs, n)
        x = _tones(n, fs, [off])

    elif kind == "Two-tone":
        sp = abs(float(c.tone_spacing))
        offs = [off - sp / 2.0, off + sp / 2.0]
        n, exact = _loop_len(fs, offs)
        if not exact:
            offs = snap_offsets(offs, fs, n)
        # Two equal tones peak 6 dB above either one on its own; the
        # normalisation below takes care of it, but it is the reason a
        # two-tone test always reads lower per tone than a single CW.
        x = _tones(n, fs, offs)

    elif kind == "Multitone":
        k = int(np.clip(c.n_tones, 1, 512))
        sp = abs(float(c.tone_spacing))
        offs = [off + (i - (k - 1) / 2.0) * sp for i in range(k)]
        n, exact = _loop_len(fs, offs)
        if not exact:
            offs = snap_offsets(offs, fs, n)
        # Newman phases. Equal-phase tones would all peak together and give a
        # crest factor of sqrt(k) -- 18 dB at 64 tones -- which would force
        # the whole comb down to nothing. This spreads them out instead.
        ph = [np.pi * (i ** 2) / k for i in range(k)]
        x = _tones(n, fs, offs, phases=ph)

    elif kind == "AWGN":
        n = int(np.clip(1 << 20, SG_MIN_LOOP, SG_MAX_LOOP))
        rng = np.random.default_rng(12345)      # repeatable on purpose
        x = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / np.sqrt(2.0)
        bw = float(c.noise_bw)
        if 0.0 < bw < fs:
            f = np.fft.fftfreq(n, d=1.0 / fs)
            x = np.fft.ifft(np.fft.fft(x) * (np.abs(f - off) <= bw / 2.0))
        warn.append(f"noise pattern repeats every {n / fs * 1e3:.1f} ms")

    elif kind == "Chirp":
        n = int(np.clip(round(float(c.sweep_time) * fs), SG_MIN_LOOP, SG_MAX_LOOP))
        span = abs(float(c.sweep_span))
        t = np.arange(n, dtype=np.float64) / fs
        T = n / fs
        # Snap both the centre and the span so the phase accumulated over one
        # sweep is a whole number of cycles: the linear term contributes
        # off*T cycles and the quadratic term span*T/2, and both have to be
        # integers or the buffer clicks once per retrace.
        off = round(off * T) / T
        span = round(span * T / 2.0) * 2.0 / T
        ph = 2 * np.pi * ((off - span / 2.0) * t + (span / (2.0 * T)) * t * t)
        x = np.exp(1j * ph)
        exact = False
        warn.append(f"sweeps {fmt_hz_short(span)} in {T * 1e3:.3f} ms, "
                    f"then retraces")

    elif kind == "Stepped sweep":
        steps = int(np.clip(c.sweep_steps, 2, 4096))
        dwell = int(max(round(float(c.sweep_time) * fs / steps), 16))
        n = dwell * steps
        if n > SG_MAX_LOOP:
            steps = max(int(SG_MAX_LOOP // dwell), 2)
            n = dwell * steps
            warn.append(f"shortened to {steps} steps to fit the buffer")
        span = abs(float(c.sweep_span))
        # Each step's frequency is snapped to the dwell's own grid, so every
        # dwell holds a whole number of cycles. That makes the phase land
        # back at zero on each boundary, so the steps join with no click at
        # all -- better than the ~200 us switching glitch a real swept
        # generator shows.
        grid = fs / dwell
        x = np.empty(n, dtype=np.complex128)
        td = np.arange(dwell, dtype=np.float64) / fs
        fl = []
        for i in range(steps):
            fi = off + (-span / 2.0 + span * i / max(steps - 1, 1))
            fi = round(fi / grid) * grid
            fl.append(fi)
            x[i * dwell:(i + 1) * dwell] = np.exp(2j * np.pi * fi * td)
        exact = False
        warn.append(f"{steps} steps, {dwell / fs * 1e6:.1f} us dwell, "
                    f"{fmt_hz_short(fl[0])} to {fmt_hz_short(fl[-1])}")

    elif kind == "Pulsed CW":
        per = int(max(round(float(c.pulse_period) * fs), 32))
        reps = max(1, -(-SG_MIN_LOOP // per))
        n = per * reps
        if n > SG_MAX_LOOP:
            n = per * max(1, SG_MAX_LOOP // per)
        off, = snap_offsets([off], fs, per)     # whole cycles inside a period
        exact = False
        wid = int(round(float(c.pulse_width) * fs))
        rise = int(round(float(c.pulse_rise) * fs))
        gate = np.tile(_raised_edges(per, wid, rise), n // per)
        x = _tones(n, fs, [off]) * gate
        duty = 100.0 * min(wid, per) / per
        warn.append(f"{duty:.2f}% duty, {wid / fs * 1e6:.2f} us on, "
                    f"PRI {per / fs * 1e6:.2f} us")

    elif kind in ("AM", "FM", "PM"):
        rate = abs(float(c.mod_rate))
        n, exact = _loop_len(fs, [off, rate])
        if not exact:
            off, rate = snap_offsets([off, rate], fs, n)
        t = np.arange(n, dtype=np.float64) / fs
        m = 2 * np.pi * rate * t
        if kind == "AM":
            d = float(np.clip(c.am_depth, 0.0, 100.0)) / 100.0
            x = (1.0 + d * np.cos(m)) * np.exp(2j * np.pi * off * t)
            warn.append(f"{d * 100:.0f}% depth at {fmt_hz_short(rate)}")
        elif kind == "FM":
            dev = abs(float(c.fm_dev))
            beta = dev / rate if rate > 0 else 0.0
            x = np.exp(1j * (2 * np.pi * off * t + beta * np.sin(m)))
            warn.append(f"+/-{fmt_hz_short(dev)} deviation, beta {beta:.2f}, "
                        f"Carson BW {fmt_hz_short(2 * (dev + rate))}")
        else:
            dev = float(c.pm_dev)
            x = np.exp(1j * (2 * np.pi * off * t + dev * np.sin(m)))
            warn.append(f"+/-{dev:.2f} rad at {fmt_hz_short(rate)}")

    elif kind in SG_DIGITAL:
        x, off, n, det = _build_digital(c, fs)
        exact = False
        warn.extend(det)

    elif kind == "LTE":
        # Built at the rate its bandwidth demands, which may not be the rate
        # the box was left on -- the panel keeps them in step, and the note
        # says so if they ever disagree.
        x, fs, det = _build_lte(c, fs)
        n = x.size
        off = 0.0
        exact = False
        warn.extend(det)

    elif kind == "Arb file":
        x, n, det = _load_arb(c.arb_path)
        off = 0.0
        exact = False
        warn.extend(det)

    else:
        raise RadioError(f"unknown waveform '{kind}'")

    # ---- impairments, all multiplicative or additive on the baseband ----
    t = np.arange(n, dtype=np.float64) / fs
    if c.imp_invert:
        x = np.conj(x)
    fo = float(c.imp_freq_off)
    if fo:
        fo, = snap_offsets([fo], fs, n)         # keep the loop seamless
        x = x * np.exp(2j * np.pi * fo * t)
        warn.append(f"frequency offset {fmt_hz_short(fo)}")
    if c.imp_gain_imb or c.imp_quad_err:
        # Textbook I/Q modulator errors: unequal arm gains, and the two arms
        # not quite 90 degrees apart. Both throw an image at -f, which is the
        # thing worth being able to produce on purpose.
        gi = 10.0 ** (float(c.imp_gain_imb) / 40.0)
        th = np.deg2rad(float(c.imp_quad_err))
        i0, q0 = np.real(x), np.imag(x)
        x = (gi * i0) + 1j * ((q0 * np.cos(th) + i0 * np.sin(th)) / gi)
    if c.imp_snr:
        rng = np.random.default_rng(999)
        sp = float(np.mean(np.abs(x) ** 2))
        npow = sp / (10.0 ** (float(c.imp_snr) / 10.0)) if sp > 0 else 0.0
        x = x + np.sqrt(npow / 2.0) * (rng.standard_normal(n)
                                       + 1j * rng.standard_normal(n))
        warn.append(f"noise added at {c.imp_snr:.1f} dB SNR")

    # ---- level: normalise to the requested digital backoff ---------------
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    backoff = float(np.clip(c.backoff_db, 0.0, 60.0))
    target = 10.0 ** (-backoff / 20.0)
    if peak > 0:
        x = x * (target / peak)
    dc = complex(float(c.imp_dc_i), float(c.imp_dc_q)) / 100.0
    if dc != 0:
        x = x + dc
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    clipped = False
    if peak > 0.999:
        # Only reachable by adding a DC offset on top of a near-full-scale
        # waveform. Back everything off rather than let the converter clip,
        # and say so, because a clipped generator output looks like a
        # distorted DUT.
        x = x * (0.999 / peak)
        peak = 0.999
        clipped = True
        warn.append("backed off further to stay inside full scale")

    rms = float(np.sqrt(np.mean(np.abs(x) ** 2))) if x.size else 0.0
    papr = 20.0 * np.log10(peak / rms) if rms > 0 else 0.0

    # Burst gating comes last, and the level figures above are deliberately
    # taken BEFORE it -- "Est. output" should say what the DUT sees while the
    # burst is on, not an average watered down by the silence between them.
    if c.burst:
        x, n, burst_notes = _burst_gate(x, fs, c)
        warn.extend(burst_notes)

    return np.ascontiguousarray(x.astype(np.complex64)), {
        "n": int(n), "loop_s": n / fs, "exact": bool(exact),
        "offset": float(off), "peak": peak, "rms": rms, "papr_db": papr,
        "backoff_db": -20.0 * np.log10(peak) if peak > 0 else 99.0,
        # Which waveform this crest factor belongs to. The level solver reads
        # papr_db back out of here, and a papr from the PREVIOUS waveform
        # would put the level out by the difference between them -- 10 dB
        # between CW and LTE. So it is stamped, and the solver checks it.
        "waveform": kind,
        "clipped": clipped, "notes": warn,
    }


def _burst_gate(x: np.ndarray, fs: float, c: SgConfig):
    """
    Key the waveform on and off by building the gate into the loop buffer.

    The obvious way to do this is bladerf_sync_tx in METADATA format with
    BURST_START/BURST_END, which holds the transmitter genuinely off between
    bursts. That is what libbladeRF documents, and it works -- but ONLY when
    the receiver is not streaming. Measured here: configuring a META TX
    stream while RX is running fails outright with BLADERF_ERR_INVAL (-3),
    and it succeeds the moment RX is either stopped or itself put into a
    META format. The metadata mode is a property of the device, not of one
    direction. Since this app's whole point is that the sweep keeps running
    while the generator does, META bursts are not available to us.

    Putting the gate in the samples instead costs nothing in timing accuracy
    -- the sample stream IS the clock, so the on and off times are exact to
    the sample either way. What it costs is that the transmitter stays keyed
    through the off period, radiating LO leakage and whatever DC offset the
    modulator has, instead of being truly silent. For bench work that is
    almost always fine, and it is said plainly in the panel rather than left
    to be discovered.
    """
    n_on = int(max(round(float(c.burst_on_ms) * 1e-3 * fs), 16))
    n_off = int(max(round(float(c.burst_off_ms) * 1e-3 * fs), 0))
    period = n_on + n_off
    if period < 32 or x.size < 2:
        return x, x.size, []
    if period > SG_MAX_LOOP:
        return x, x.size, ["burst period too long for the buffer -- "
                           "not gated"]
    body = (np.tile(x, -(-n_on // x.size))[:n_on] if n_on > x.size
            else x[:n_on])
    rise = int(min(max(round(1e-6 * fs), 1), max(n_on // 4, 1)))
    gate = _raised_edges(period, n_on, rise)
    one = np.zeros(period, dtype=np.complex128)
    one[:n_on] = body * gate[:n_on]
    reps = max(1, -(-SG_MIN_LOOP // period))
    if period * reps > SG_MAX_LOOP:
        reps = max(1, SG_MAX_LOOP // period)
    out = np.tile(one, reps)
    duty = 100.0 * n_on / period
    return out, out.size, [
        f"burst {n_on / fs * 1e3:.3f} ms on, {n_off / fs * 1e3:.3f} ms off "
        f"({duty:.1f}% duty), exact to the sample",
        "the transmitter stays keyed between bursts (LO leakage remains) -- "
        "true off needs the receiver stopped, see the code note"]


def lte_plan(c: "SgConfig") -> dict:
    """
    Work out how many cells fit, how far apart, and at what rate.

    Multi-carrier is not a matter of building N signals and adding them: the
    composite has to be ONE transform, or the carriers would each need their
    own fractional resampling to share a clock. So the rate comes off the LTE
    ladder (every entry an exact multiple of 15 kHz), the FFT grows with it,
    and each cell is mapped at a whole number of subcarriers from the middle.
    Nothing is resampled and nothing is shifted by a fraction of a bin, which
    is what keeps the 10 ms frame exactly 10 ms with three cells up.

    Each carrier is then snapped to its NEAREST subcarrier bin rather
    than the spacing being tidied up to make the arithmetic land: most
    channel bandwidths are not a whole number of 15 kHz steps (10 MHz is
    666.67 of them), so the realised spacing is reported and the panel
    says when it is not the one asked for. Same rule as _loop_len().

    The rate has to hold the whole span with room to spare -- 0.95 of it,
    which is what the reconstruction filter and the TX analog filter want --
    and the span itself is bounded by the AD9361's 56 MHz channel. Three
    20 MHz cells need 58 MHz edge to edge and are therefore impossible on
    this board; the planner says so rather than quietly dropping one.
    """
    n_rb, nfft_car, fs_car = LTE_BW[c.lte_bw]
    want = int(np.clip(int(getattr(c, "lte_carriers", 1) or 1),
                       1, LTE_MAX_CARRIERS))
    occupied = n_rb * 12 * 15e3
    nominal = float(c.lte_bw.split()[0]) * 1e6
    spacing = float(getattr(c, "lte_spacing", 0.0) or 0.0)
    if spacing <= 0.0:
        spacing = nominal
    spacing = max(spacing, occupied)

    def span_of(k):
        return (k - 1) * spacing + occupied

    def rate_for(k):
        need = span_of(k)
        for r in LTE_RATES:
            if r >= fs_car and need <= r * 0.95:
                return r
        return 0.0

    n = want
    while n > 1 and (span_of(n) > LTE_MAX_BW or rate_for(n) <= 0.0):
        n -= 1
    fs = rate_for(n) or fs_car
    nfft = int(round(fs / 15e3))
    # Centred on the tuned frequency, so a single carrier still sits where
    # it always did and adding a second one does not move the first.
    #
    # Then snapped to whole subcarriers, because a composite transform can
    # only place a carrier on a bin. Most channel bandwidths are not a whole
    # number of 15 kHz steps -- 10 MHz is 666.67 of them -- so an exactly
    # nominal spacing is not available at any rate, and the honest thing is
    # to land on the nearest bin (7.5 kHz at worst) and SAY where that is,
    # rather than to move the spacing until the arithmetic looks tidy.
    # Same rule as the tone offsets in _loop_len(): report the actual.
    ideal = [(i - (n - 1) / 2.0) * spacing for i in range(n)]
    offsets = [round(o / 15e3) * 15e3 for o in ideal]
    got = ((offsets[-1] - offsets[0]) / (n - 1)) if n > 1 else spacing
    span = (offsets[-1] - offsets[0]) + occupied
    return {
        "carriers": n, "asked": want, "spacing": got, "wanted": spacing,
        "exact": all(abs(a - b) < 1.0 for a, b in zip(ideal, offsets)),
        "occupied": occupied, "span": span, "fs": fs, "nfft": nfft,
        "n_rb": n_rb, "offsets": offsets,
        # What the transmitter's analog filter has to pass. 0 leaves the
        # radio on its own 0.8 * fs default, which is right for one carrier
        # and would clip the outer ones of three.
        "bandwidth": (min(LTE_MAX_BW, span * 1.02) if n > 1 else 0.0),
    }


def _lte_zc_pss(nid2: int) -> np.ndarray:
    """
    Primary synchronisation signal: the length-62 Zadoff-Chu sequence from
    36.211 section 6.11.1. Roots 25/29/34 for the three NID2 values, and the
    middle element is skipped because the centre subcarrier is never used.
    This one is exact -- it is short, unambiguous, and it is what lets a
    receiver find the cell at all.
    """
    u = {0: 25, 1: 29, 2: 34}[int(nid2) % 3]
    n = np.arange(31)
    lo = np.exp(-1j * np.pi * u * n * (n + 1) / 63.0)
    hi = np.exp(-1j * np.pi * u * (n + 1) * (n + 2) / 63.0)
    return np.concatenate([lo, hi]).astype(np.complex128)


def _lte_sss(cell_id: int) -> np.ndarray:
    """
    Secondary synchronisation signal -- a STAND-IN, not the real thing.

    The genuine SSS is two interleaved length-31 m-sequences scrambled by
    sequences derived from NID1 and NID2 (36.211 6.11.2). What is here is a
    single m-sequence seeded from the cell id, BPSK, in the right 62
    subcarriers of the right symbol. It occupies the correct resource
    elements with the correct power so the spectrum and the timing are
    right; it will not decode. Said plainly rather than dressed up -- see
    LTE_FIDELITY_NOTE.
    """
    x = np.ones(5, dtype=np.int8)
    seed = int(cell_id) % 168
    for i in range(5):
        x[i] = (seed >> i) & 1 or 1
    out = np.empty(62, dtype=np.float64)
    for i in range(62):
        fb = x[4] ^ x[2]
        out[i] = 1.0 - 2.0 * x[4]
        x[1:] = x[:-1]
        x[0] = fb
    return out.astype(np.complex128)


def _lte_prbs_qpsk(n: int, seed: int) -> np.ndarray:
    """QPSK symbols for the reference signals, from a repeatable generator
    keyed on the cell id. Not 36.211's Gold sequence -- see the note."""
    rng = np.random.default_rng(0x1E7E + int(seed))
    b = rng.integers(0, 2, size=(n, 2))
    return ((1 - 2 * b[:, 0]) + 1j * (1 - 2 * b[:, 1])) / np.sqrt(2.0)


def _build_lte(c: SgConfig, fs: float):
    """
    One LTE downlink radio frame, built OFDM symbol by OFDM symbol.

    The structure is what makes this worth having: 15 kHz subcarriers, 12 to
    a resource block, 7 symbols to a slot with normal cyclic prefix (6 with
    extended), 2 slots to a 1 ms subframe, 10 subframes to a 10 ms frame.
    Get that right and the occupied bandwidth, the spectral shape, the
    peak-to-average ratio and the 1 ms structure a zero-span view shows are
    all right too.

    The first cyclic prefix of each slot is longer than the other six (160
    against 144 samples at 30.72 MS/s, scaled by FFT size). That is not a
    quirk to tidy away -- it is what makes a slot come to exactly 0.5 ms.
    """
    plan = lte_plan(c)
    n_rb, nfft, fs_lte = plan["n_rb"], plan["nfft"], plan["fs"]
    n_car = plan["carriers"]
    notes = []
    if abs(fs - fs_lte) > 1.0:
        notes.append(
            f"sample rate must be {fs_lte/1e6:.2f} MS/s for "
            f"{n_car} x {c.lte_bw} LTE, not {fs/1e6:.3f} -- the panel "
            f"normally sets this for you")
    if n_car < plan["asked"]:
        notes.append(
            f"{plan['asked']} carriers of {c.lte_bw} need "
            f"{((plan['asked'] - 1) * plan['wanted'] + plan['occupied'])/1e6:.1f}"
            f" MHz edge to edge, "
            f"past this radio's {LTE_MAX_BW/1e6:.0f} MHz channel -- "
            f"transmitting {n_car}")
    fs = fs_lte
    scale = nfft / 2048.0
    if c.lte_ext_cp:
        n_sym_slot, cp0, cpr = 6, int(512 * scale), int(512 * scale)
    else:
        n_sym_slot, cp0, cpr = 7, int(160 * scale), int(144 * scale)
    n_sc = n_rb * 12
    half = n_sc // 2

    order = int(c.lte_mod) if int(c.lte_mod) in (4, 16, 64, 256) else 4
    cmap = constellation("QAM", order) if order > 4 else constellation("PSK", 4)
    rng = np.random.default_rng(0x1E7E)

    load = float(np.clip(c.lte_rb_load, 0.0, 100.0)) / 100.0
    n_rb_on = int(round(n_rb * load))
    # Allocated blocks sit in the middle of the channel, which is where a
    # lightly loaded cell actually puts them, and keeps the shape symmetric.
    rb_lo = (n_rb - n_rb_on) // 2
    rb_on = np.zeros(n_rb, dtype=bool)
    rb_on[rb_lo:rb_lo + n_rb_on] = True
    sc_on = np.repeat(rb_on, 12)

    # One set of sequences per carrier, and consecutive cell IDs, because
    # three copies of the same cell is not three cells -- the CRS would line
    # up and the sum would have a periodicity no real network has.
    cells, syncs, rngs, off_bins = [], [], [], []
    for i in range(n_car):
        cell_i = (int(c.lte_cell_id) + i) % 504
        cells.append(cell_i)
        syncs.append((_lte_zc_pss(cell_i % 3), _lte_sss(cell_i // 3),
                      _lte_prbs_qpsk(n_sc, cell_i), cell_i % 6))
        rngs.append(np.random.default_rng(0x1E7E + i))
        off_bins.append(int(round(plan["offsets"][i] / 15e3)))
    cell = cells[0]
    nid2 = cell % 3
    v_shift = cell % 6
    # Where each subcarrier of a carrier lands in the composite transform.
    # Straight out of the single-carrier mapping below it: subcarriers sit
    # either side of the carrier's own centre and its DC bin stays empty.
    rel = np.arange(n_sc) - half
    rel[half:] += 1
    bin_of = [(o + rel) % nfft for o in off_bins]
    # Power is shared, not multiplied: three cells at the level one used to
    # be would be 4.8 dB more total power and would clip.
    amp = 1.0 / np.sqrt(float(n_car))

    n_slots = 20
    out = []
    for slot in range(n_slots):
        for sym in range(n_sym_slot):
            bins = np.zeros(nfft, dtype=np.complex128)
            for i in range(n_car):
                pss_i, sss_i, crs_i, vs_i = syncs[i]
                grid = np.zeros(n_sc, dtype=np.complex128)
                if c.lte_data and n_rb_on:
                    d = cmap[rngs[i].integers(0, cmap.size, size=n_sc)]
                    grid[sc_on] = d[sc_on]
                # Cell-specific reference signals: symbol 0 and the third
                # from last of each slot, every sixth subcarrier, offset by
                # cell id.
                if sym == 0 or sym == n_sym_slot - 3:
                    k = np.arange((vs_i + (3 if sym else 0)) % 6, n_sc, 6)
                    grid[k] = crs_i[k] * np.sqrt(2.0)   # RS are boosted
                # Sync signals: last two symbols of slots 0 and 10, centre
                # 62 subcarriers, DC left empty.
                if slot in (0, 10) and n_sc >= 62:
                    c0 = half - 31
                    if sym == n_sym_slot - 1:
                        grid[c0:c0 + 31] = pss_i[:31]
                        grid[c0 + 31:c0 + 62] = pss_i[31:]
                    elif sym == n_sym_slot - 2:
                        grid[c0:c0 + 31] = sss_i[:31]
                        grid[c0 + 31:c0 + 62] = sss_i[31:]
                # Map into the composite transform. For one carrier this is
                # the textbook mapping -- subcarriers either side of DC, DC
                # itself never transmitted -- shifted by that carrier's own
                # whole number of subcarriers.
                bins[bin_of[i]] += grid * amp
            t = np.fft.ifft(bins) * np.sqrt(nfft)
            cp = cp0 if sym == 0 else cpr
            out.append(np.concatenate([t[nfft - cp:], t]))
    x = np.concatenate(out)

    occupied = n_sc * 15e3
    if n_car > 1:
        notes.append(
            f"{n_car} x {c.lte_bw} downlink, {plan['spacing']/1e3:,.1f} kHz "
            f"apart, {plan['span']/1e6:.2f} MHz edge to edge in a "
            f"{nfft}-point FFT at {fs_lte/1e6:.2f} MS/s")
        if not plan["exact"]:
            notes.append(
                f"spacing asked was {plan['wanted']/1e6:.3f} MHz; a carrier "
                f"can only sit on a whole subcarrier, so they are at "
                + ", ".join(f"{o/1e6:+.3f}" for o in plan["offsets"])
                + " MHz -- 7.5 kHz at worst from where you asked")
        notes.append(
            "cells " + ", ".join(str(x) for x in cells)
            + f"; each {n_rb} RB, {occupied/1e6:.2f} MHz occupied; power "
              f"shared, so each is {10*np.log10(n_car):.1f} dB below the "
              f"total")
    else:
        notes.append(f"{c.lte_bw} downlink, {n_rb} RB, {nfft}-point FFT at "
                     f"{fs_lte/1e6:.2f} MS/s, {occupied/1e6:.2f} MHz occupied")
    notes.append(f"{'extended' if c.lte_ext_cp else 'normal'} CP, "
                 f"{n_sym_slot} symbols/slot, 10 ms frame "
                 f"({x.size:,} samples)")
    if c.lte_data:
        nm = dict((v, k) for k, v in LTE_MODS)
        notes.append(f"data ON: {nm.get(order, order)}, {n_rb_on}/{n_rb} RB "
                     f"loaded ({load*100:.0f}%)")
    else:
        notes.append("data OFF: sync and reference signals only -- what an "
                     "idle cell looks like")
    if n_car == 1:
        notes.append(f"cell id {cell} (NID2 {nid2}, CRS shift {v_shift})")
    return x, fs, notes


def _build_digital(c: SgConfig, fs: float):
    """
    Linear (ASK/PSK/QAM) and CPM (FSK/GMSK) modulation off a PRBS.

    The symbol count is a whole number of PRBS periods, and the pulse shaping
    wraps around the buffer, so the modulated stream loops with no seam and
    no repeated-preamble artefact.
    """
    kind, notes = c.waveform, []
    order = int(c.mod_order) if kind != "GMSK" else 2
    bps = max(int(round(np.log2(max(order, 2)))), 1)
    sps = int(max(round(fs / max(float(c.sym_rate), 1.0)), 2))
    sym_rate = fs / sps
    if abs(sym_rate - c.sym_rate) > 1.0:
        notes.append(f"symbol rate is {fmt_hz_short(sym_rate)} "
                     f"({sps} samples/symbol -- it has to divide the "
                     f"sample rate exactly)")

    # How many symbols to lay down: a whole PRBS period if that fits in the
    # buffer and is quick enough to generate, otherwise as much of one as
    # does. Short periods are repeated up to a decent buffer length. Either
    # way the buffer loops seamlessly -- whole periods only decide whether
    # what repeats is a true PRBS or a fixed slice of one, which is why the
    # truncated case is called out rather than left to be noticed.
    per = prbs_period(c.prbs)
    n_sym = max(1, min(per, int(SG_MAX_LOOP // sps), SG_MAX_PRBS_BITS // bps))
    idx = _bits_to_symbols(prbs_bits(c.prbs, n_sym * bps), bps)
    if n_sym < per:
        notes.append(f"{c.prbs} truncated to {n_sym:,} symbols of its "
                     f"{per:,}-bit period")
    reps = max(1, -(-SG_MIN_LOOP // max(idx.size * sps, 1)))
    while reps > 1 and idx.size * reps * sps > SG_MAX_LOOP:
        reps -= 1
    if reps > 1:
        idx = np.tile(idx, reps)
    n = idx.size * sps

    off = float(c.offset)
    off, = snap_offsets([off], fs, n)
    t = np.arange(n, dtype=np.float64) / fs

    if kind in ("ASK", "PSK", "QAM"):
        c_map = constellation(kind, order)
        sym = c_map[_gray(idx) % c_map.size]
        up = np.zeros(n, dtype=np.complex128)
        up[::sps] = sym
        h = rrc_taps(sps, float(c.rrc_alpha))
        bb = _circular_filter(up, h)
        # Rs*(1+alpha) is the absolute width of an RRC-shaped channel, which
        # is what a channel plan is written in. The 99% occupied bandwidth
        # this app's own OBW measurement reports is a few percent narrower,
        # because almost nothing lives out in the roll-off -- so the two
        # numbers disagreeing is correct, not a fault in either.
        notes.append(f"{order}-{kind}, RRC alpha {float(c.rrc_alpha):.2f}, "
                     f"channel BW Rs x (1+alpha) = "
                     f"{fmt_hz_short(sym_rate * (1 + float(c.rrc_alpha)))}")
    elif kind == "FSK":
        lv = (2.0 * idx - (order - 1)) / max(order - 1, 1)      # -1 .. +1
        # Auto deviation gives h = 0.5, the same modulation index MSK and
        # Bluetooth GFSK use -- the most compact spectrum that still stays
        # orthogonal. Rs/2 would be h = 1.0 and twice as wide for nothing.
        dev = float(c.fsk_dev) if c.fsk_dev > 0 else sym_rate / 4.0
        f_inst = np.repeat(lv, sps) * dev
        bb = np.exp(1j * _cpm_phase(2.0 * np.pi * f_inst / fs))
        notes.append(f"{order}-FSK, +/-{fmt_hz_short(dev)} deviation, "
                     f"h = {2 * dev / sym_rate:.2f}")
    else:                                                        # GMSK
        nrz = np.repeat(2.0 * idx.astype(np.float64) - 1.0, sps)
        g = _gaussian_taps(sps, float(c.gmsk_bt))
        shaped = np.real(_circular_filter(nrz.astype(np.complex128), g))
        bb = np.exp(1j * _cpm_phase((np.pi / 2.0) * shaped / sps))
        notes.append(f"GMSK BT {float(c.gmsk_bt):.2f}, h = 0.5 "
                     f"(Bluetooth-style)")

    notes.append(f"{c.prbs}, pattern repeats every "
                 f"{n / fs * 1e3:.3f} ms")
    return bb * np.exp(2j * np.pi * off * t), off, n, notes


def _load_arb(path: str):
    """
    Play back a bare complex64 'cfile' -- the exact format this app's own
    'Record IQ' button writes, so a signal captured off the air can be
    replayed straight back out. GNU Radio and inspectrum write the same
    thing.
    """
    if not path:
        raise RadioError("no arbitrary waveform file chosen")
    if not os.path.isfile(path):
        raise RadioError(f"arbitrary waveform file not found:\n{path}")
    size = os.path.getsize(path)
    if size < 8:
        raise RadioError("that file is too small to hold any IQ samples")
    if size % 8:
        raise RadioError("not a complex64 IQ file: its length is not a "
                         "multiple of 8 bytes (float32 I + float32 Q)")
    n_avail = size // 8
    n = int(min(n_avail, SG_MAX_LOOP))
    x = np.fromfile(path, dtype=np.complex64, count=n).astype(np.complex128)
    if x.size < 2:
        raise RadioError("that file holds no usable samples")
    notes = [f"{os.path.basename(path)}: {x.size:,} samples"]
    if n_avail > n:
        notes.append(f"only the first {n:,} of {n_avail:,} samples fit "
                     f"in the loop buffer")
    peak = float(np.max(np.abs(x)))
    if peak <= 0:
        raise RadioError("that file is all zeros")
    return x, x.size, notes


def _sg_sidecar_rate(path: str) -> Optional[float]:
    """
    Dig the sample rate out of the .txt this app writes beside a capture.

    Replaying a recording at the wrong rate shifts every frequency in it and
    stretches every timing, so the recorded rate is worth going and finding
    rather than leaving to whatever the box was last set to.
    """
    for cand in (path + ".txt", os.path.splitext(path)[0] + ".txt"):
        try:
            with open(cand, "r", encoding="utf-8") as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) >= 3 and parts[0] == "sample" and parts[1] == "rate":
                        return float(parts[2])
        except Exception:
            continue
    return None


# ============================================================================
#  Acquisition thread
#
#  One FFT covers only the radio's instantaneous bandwidth. Wider spans are
#  swept: step the LO, capture, keep the good middle of each segment, stitch.
#  Traces accumulate here rather than in the GUI so Max Hold and Average see
#  every sweep even though the screen refreshes at a fixed rate.
# ============================================================================

# RX amplitude correction applied by default. **Back to 0.0 on
# 2026-09-02, and the round trip is the lesson**, so it is written down
# rather than reverted silently:
#
#   2026-09-01, from the bench: "The amplitude is currently off by 8dB.
#   Please add 8db amplitude offset by default." So this became 8.0.
#
#   2026-09-02: bug 45 turned up -- the app believed 60 dB of RX gain
#   while the board ran 30, so every level read about 31 dB low until an
#   RX port switch. That fault was live when the 8 dB was measured. With
#   the gain right, the board reads true: "so now indeed the default
#   should go back to 0dB."
#
# So a measured correction is only as good as the chain it was measured
# through, and a DEFAULT carrying somebody's bench number is a place for a
# bug elsewhere to hide. Any future non-zero value here wants the date and
# the signal it was measured against, and a re-check after anything in the
# gain or full-scale path moves.
#
# The constant stays even at 0.0. It is what the Cal offset box is seeded
# from, what the tooltip quotes and what _apply_setup compares a recalled
# setup against, so those three cannot drift apart -- and a setup saved on
# 2026-09-01 carries 8.0, which is still HONOURED and announced.
#
# It is deliberately NOT hidden inside full_scale_dbm: keeping it in
# cal_offset means the number is visible in the Amplitude panel, in the CSV
# header, in the IQ sidecar and in the diagnostics, and one glance says how
# much correction is in force. Folded into the source's full scale, every
# level would carry a fudge nothing on screen mentions.
RX_CAL_DEFAULT_DB = 0.0

# The typical-board RX correction, measured 2026-09-17 on Kfir's xA4
# (serial 823ba402) with a signal generator injecting -40.00 dBm through one
# cable, at RX1, 4 MHz span, 30 kHz RBW, Flat Top, cal offset pinned to 0.
# Full write-up and the raw numbers: Version 2\VERSION 2.md section 6.4, raw
# output in Version 2\Bench\.
#
#     offset = injected - read
#
# so it is what the application has to ADD to be right, the same sign the
# Cal offset control uses. It is applied per FREQUENCY, not per sweep: a
# stitched sweep crosses 10 dB of this curve, so one scalar for the whole
# pass would be wrong nearly everywhere on a wide span.
#
# WHY THIS IS NOT FOLDED INTO full_scale_dbm, and not into cal_offset
# either. Inside full_scale_dbm it would be a correction of up to 12 dB that
# nothing on screen mentions -- the exact fudge RX_CAL_DEFAULT_DB's own
# comment above refuses. Inside cal_offset it would overwrite the user's own
# knob, which is a per-bench number and has to stay theirs. So it is a third
# term, stated in the Amplitude panel, in the CSV, in the IQ sidecar and in
# the overpower dialog.
#
# WHAT IT IS AND IS NOT. Each row is the median of two uncompressed gains
# (20 and 30 dB), which agreed to 0.03-0.09 dB at almost every frequency and
# 0.5 dB at worst -- that agreement is what says the offset does NOT move
# with gain, which is what makes a table indexed by frequency alone the
# right shape. Gain 40 was compressed at this drive level and is excluded:
# it read the same +1.1 dB at every frequency below 1.5 GHz, which is a
# clamp rather than a response. Repeatability at 1 GHz across two separate
# runs was 0.03 dB.
#
# It is ONE BOARD and ONE SET OF CABLES. What should transfer to another
# xA4 is the SHAPE -- the AD9361 gain model, the front-end and balun
# roll-off -- and what will not is the absolute offset, the connectors and
# the generator's own flatness. Board-to-board spread is UNMEASURED and
# cannot be measured with one board; the panel says "typical" for that
# reason, and the Cal offset control is still how a user closes the last
# decibel or two against a known source.
#
# THE 3 GHz ROW IS THE ONE TO DISTRUST: +12.02 dB against +3.55 at 2.4 and
# +7.46 at 3.5. An 8 dB feature over 600 MHz is either real front-end
# structure, in which case this ladder under-samples it, or the generator's
# own output flatness. Kfir was offered a five-point pass around it on
# 2026-09-17 and declined ("Let's skip it, take only 11 points"), so it
# stands as measured and is recorded here as the largest known doubt.
RX_CAL_TABLE = (
    (  100e6, -6.31),
    (  250e6, -4.71),
    (  500e6, -2.84),
    (  750e6, -3.65),
    ( 1000e6, -2.99),
    ( 1500e6, -0.06),
    ( 2000e6, +3.97),
    ( 2400e6, +3.55),
    ( 3000e6, +12.02),
    ( 3500e6, +7.46),
    ( 4000e6, +4.54),
)
_RX_CAL_F = np.array([r[0] for r in RX_CAL_TABLE], dtype=np.float64)
_RX_CAL_D = np.array([r[1] for r in RX_CAL_TABLE], dtype=np.float64)
RX_CAL_LO_HZ = float(_RX_CAL_F[0])
RX_CAL_HI_HZ = float(_RX_CAL_F[-1])


def rx_table_db(hz):
    """
    The board table's correction at one frequency, or at an array of them.

    Linear in frequency between measured points, and HELD FLAT outside them
    (np.interp's own behaviour, which is what is wanted): the radio tunes
    from 70 MHz to 6 GHz and the ladder stops at 100 MHz and 4 GHz, because
    that is as far as the generator reached. Holding the nearest measured
    value is the least-wrong thing to do there, and it is not silent --
    Result.cal_extrap is set for any sweep that reaches outside, and the
    status bar carries a permanent amber line while it does. Extrapolating
    the local slope instead would put a made-up number on screen that looks
    exactly like a measurement, which is the shape of half the faults in
    this file's history.
    """
    return np.interp(hz, _RX_CAL_F, _RX_CAL_D)


@dataclass
class Config:
    center: float = 2.44e9
    span: float = 100e6
    rbw: float = 100e3
    rbw_auto: bool = True
    # Gaussian is what a bench analyzer's RBW filter is, so it is the default
    # here too. It costs 0.67 dB of worst-case scallop loss against Flat
    # Top's 0.01 dB -- see the "RBW shape" tooltip, which says so where
    # someone reading a CW level will actually see it.
    window: str = "Gaussian"
    detector: str = "Peak"
    points: int = 1400
    gain: float = 30.0
    cal_offset: float = RX_CAL_DEFAULT_DB
    # There was an ext_atten field here for one day, 2026-08-30 to -08-31.
    # It described a pad screwed onto the RX cable and added it back into
    # every level. Kfir had it removed the next day: "useless since there's
    # no mechanical attenuator on the board".
    #
    # DELETED rather than left at a default, which is the opposite of the
    # call made for quick_tune, oversample and Detector -- and deliberately
    # so. Those are radio settings whose default IS the behaviour of the
    # removed control. This one is an ADDEND on every displayed level, so a
    # setup file carrying ext_atten=30 would silently put every reading
    # 30 dB out with no control anywhere able to clear it. That is bug 33's
    # trap exactly. A field nothing reads cannot spring it.
    max_fs: float = 40e6
    zero_span: bool = False
    zero_time: float = 200e-6
    trace_modes: tuple = ("Live", "Max Hold", "Off", "Off", "Off", "Off")
    trace_view: tuple = (False, False, False, False, False, False)
    # True while channel power or OBW is on. The sweep builds its mean-power
    # trace only then -- see Result.power_mean.
    meas_on: bool = False
    avg_count: int = 10
    fps: float = 25.0
    # Set non-zero while the generator is keyed, and then it wins over
    # everything below that would otherwise pick a sample rate.
    #
    # Measured on Kfir's bladeRF 2.0 micro: RX and TX sample rates are NOT
    # independent. Setting either one drags the other to match, in both
    # directions -- set RX to 10 MS/s and TX follows to 10 MS/s, and the
    # reverse. They come off the same baseband PLL.
    #
    # That matters because the sweep re-applies its own rate every single
    # pass. Without this pin the two would fight: the sweep would drag the
    # transmitter to the span's rate, the waveform in the buffer would still
    # be the one built for the rate the user asked for, and everything in it
    # would come out at the wrong frequency and the wrong bandwidth -- with
    # nothing on screen to say so. Pinning is not a limitation invented
    # here; it is the hardware being described honestly.
    fs_pin: float = 0.0
    # Triggered capture (section 5.1) -- meaningful only in zero span.
    #
    # No control arms these any more: the Trigger popover offers Free Run and
    # Video (the two below), on request, and the mini_exp hardware trigger
    # went with the Role and Signal selectors. The fields and the worker path
    # behind them are left intact and DISABLED, which is exactly the
    # behaviour the selectors had sitting on "Off".
    trigger_role: int = TRIG_ROLE_DISABLED
    trigger_signal: int = TRIG_SIGNAL_MINI_EXP_1
    # Sweep trigger. Free Run takes every sweep. Video holds the display
    # until a sweep's own detected level crosses trig_level, which is what
    # makes it useful for catching a burst without a wire.
    trig_mode: int = TRIG_FREE_RUN
    trig_level: float = -50.0
    # Sweep time. Auto is what the sweep naturally takes -- segments, retune
    # settling and the FFT -- and is the default because it is the fastest
    # the hardware can go. Manual only ever makes a sweep SLOWER: it paces
    # the pass to the time asked for, the way a bench analyzer's sweep-time
    # control does when you want a slow look at a drifting signal.
    sweep_auto: bool = True
    sweep_time: float = 0.05
    # Acquisition: Continuous sweeps forever (the default, what a bench SA
    # does with Sweep set to Cont), Single stops after each armed sweep.
    # Distinct from the Freeze button, which only holds the DISPLAY while
    # acquisition carries on underneath.
    continuous: bool = True
    # Quick tune (section 5.2) -- experimental, see the checkbox tooltip:
    # bladerf_get_frequency() never reflected a schedule_retune() target in
    # testing on this board/driver, so whether the RF genuinely retunes is
    # unconfirmed. Off by default for that reason, not as a style choice.
    quick_tune: bool = False
    # Oversample (section 5.2) -- Nuand call this an overclock. Opt-in only.
    oversample: bool = False

    @property
    def start(self):
        return self.center - self.span / 2

    @property
    def stop(self):
        return self.center + self.span / 2


@dataclass
class Result:
    freqs: np.ndarray
    power: np.ndarray
    rbw: float
    segments: int
    nfft: int
    traces: tuple = ()
    wf_row: Optional[np.ndarray] = None
    folded: int = 1
    rate: float = 0.0
    overload: bool = False
    # True when this sweep ran at or below FS_MIN_LINEAR, where the AD9361
    # adds about 11 dB that full_scale_dbm does not model. The planner keeps
    # the rate above it, so the only way to be here is the generator pinning
    # a low rate (5.5 fact 1), where raising it is not ours to do. Levels in
    # such a sweep are about 11 dB high and the window says so.
    fs_low: bool = False
    # True when this sweep reached outside the measured band of
    # RX_CAL_TABLE, where the board correction is held flat at the nearest
    # measured point rather than extrapolated. Levels there carry whatever
    # the edge of the table said, which is a guess; the status bar says so.
    cal_extrap: bool = False
    # Peak found on the FULL-resolution spectrum, before the trace is bucketed
    # down to screen width, then refined between bins. Doing it here is what
    # makes the frequency readout finer than one display point.
    peak_hz: float = float("nan")
    peak_dbm: float = float("nan")
    # Triggered zero-span capture (section 5.1) -- None outside that mode.
    timestamp: Optional[int] = None
    overrun: bool = False
    miniexp1: bool = False
    miniexp2: bool = False
    # Quick tune (section 5.2) -- how many segments this sweep actually
    # replayed from cache vs how many segments used quick tune at all.
    qt_hits: int = 0
    qt_attempted: int = 0
    # Per-bucket MEAN power in dBm, on the same axis as `power`, or None
    # when nothing is measuring. Channel power and OBW integrate THIS rather
    # than the displayed trace, because the display trace has been through a
    # detector and a detected trace does not carry the bucket's power -- see
    # mean_power(). Not drawn anywhere: it would hide narrow carriers, which
    # is the whole reason the display uses Peak.
    power_mean: Optional[np.ndarray] = None
    # RX1+RX2 mode: the second port's trace, on the same frequency axis.
    # None whenever a single port is selected. Markers, measurements and the
    # spectrogram all stay on `power` (RX1) -- see _box_radio's tooltip.
    power2: Optional[np.ndarray] = None
    # Triggered capture: samples actually returned vs asked for. A short
    # block is normal for the first capture or two after a trigger fires,
    # while the stream resyncs -- reporting the numbers beats reporting the
    # word "OVERRUN" at someone.
    n_actual: int = 0
    n_requested: int = 0
    # What was ASKED for, next to `rbw` which is what came out. They now
    # agree to a fraction of a percent whenever the sample rate is free to
    # move; when it is not (the generator pins it, or the length hit a
    # limit) the status bar shows both rather than quietly showing one.
    rbw_req: float = 0.0
    # Zero-padded transform length. Equals nfft (the WINDOW length) unless a
    # coarse RBW needed padding to draw a smooth trace -- see display_nfft().
    nfft_pad: int = 0
    # The rate the segment was actually captured at. Worth carrying because
    # _plan_rbw() moves it to land on the requested RBW, so it is no longer
    # a function of the span alone -- and because the simulated source's
    # noise is a fixed total across fs, which makes a noise-floor prediction
    # depend on it.
    fs: float = 0.0


class Worker(QtCore.QObject):
    ready  = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)
    lost   = QtCore.pyqtSignal(str)      # the board vanished
    back   = QtCore.pyqtSignal(object)   # ... and has been reopened
    portChanged = QtCore.pyqtSignal(int)  # RX port actually in use now
    iqDone = QtCore.pyqtSignal(str, str)  # path (empty on failure), detail
    healthReady = QtCore.pyqtSignal(dict)     # board health readout (5.3)
    selftestReady = QtCore.pyqtSignal(object)  # (n_samples, gaps) or an error str
    sweepDone = QtCore.pyqtSignal()            # one armed single sweep finished
    # True while a Video trigger is holding the display, False when it lets
    # go. Edge-driven on purpose: a signal per rejected sweep would arrive
    # hundreds of times a second and say nothing new.
    trigWait = QtCore.pyqtSignal(bool)
    # (consecutive failures, why). Edge-driven like trigWait: 0 means the
    # stream is delivering again. The GUI keeps a PERMANENT label from this,
    # because a transient message fades and leaves a still trace with
    # nothing on screen explaining it -- which is bug 43's shape, and is
    # what made the 2026-09-06 report read as a freeze rather than as a
    # fault being worked on.
    stalled = QtCore.pyqtSignal(int, str)

    RETRY_SECONDS = 1.5
    # Stream restarts to try before falling back to a full reopen. Three,
    # because a restart that has not worked three times is not going to:
    # past that the fault is likelier to be the handle or the link than the
    # stream, and the reopen path is the heavier remedy that covers those.
    STREAM_RETRY_MAX = 3
    # Full-resolution bins _sweep keeps aside for _reduce_full's rare cases.
    # Past this it stops keeping them -- the point of GridAccumulator is
    # never to hold millions. A class attribute so a test can raise it and
    # run both reductions on the same big sweep.
    RAW_CAP = 200_000
    # How often the held sweep claims a block. Four times a second is
    # frequent enough that no ring can sit unclaimed for long, and rare
    # enough to be invisible next to a 30 ms idle tick.
    IDLE_DRAIN_SECONDS = 0.25

    def __init__(self, src: Source, cfg: Config, reopen=None) -> None:
        super().__init__()
        self.src = src
        self.cfg = replace(cfg)
        # Called on this thread to build a fresh radio object after a
        # disconnect. Reconnecting here rather than in the GUI is the whole
        # point: opening a USB device can block for a second or two.
        self.reopen = reopen
        self._lost = False
        self._next_try = 0.0
        self._tries = 0
        self._want_ch: Optional[int] = None
        self._want_iq: Optional[Tuple[float, str]] = None
        self._want_fire = False
        # Single-sweep arming. In Single mode the loop idles until armed,
        # so this is what the Sweep button sets.
        self._want_single = False
        self._armed = False
        # Consecutive receive failures that were NOT a disconnect, and the
        # running total of stream restarts -- the total is diagnostic and is
        # shown in Help > About, because "sometimes, after a while" is only
        # arguable with a number attached to it.
        self._stream_fails = 0
        self._stream_restarts = 0
        self._next_drain = 0.0
        # Board health / self-test (5.3) requests. self.src is owned by
        # this thread's run() loop -- any GUI-thread code that touched it
        # directly (as an earlier version of this did) raced the sweep
        # loop's own start()/stop()/read() calls. bladerf_sync_config
        # failing with "stream config" and the loopback toggle hanging
        # were both exactly that: two threads driving the same device
        # handle at once. Everything here goes through the same
        # request-now / apply-between-sweeps pattern as request_channel.
        self._want_bias_tee: Optional[bool] = None
        self._want_gain_mode: Optional[int] = None
        self._want_loopback: Optional[int] = None
        self._want_health_read = False
        self._want_selftest = False
        self._run = False
        self._lock = QtCore.QMutex()
        self._new: Optional[Config] = None
        self.traces = [Trace(m) for m in cfg.trace_modes]
        self._wf = None
        self._folded = 0
        self._cnt = 0
        self._t_rate = 0.0
        # Triggered capture (5.1): what the source is actually armed with
        # right now, so _ensure_trigger() only calls init/arm on a change
        # instead of every single sweep.
        self._trig_role = TRIG_ROLE_DISABLED
        self._trig_signal = None
        # The signal actually passed to the source's trigger_init(), i.e.
        # what the DEVICE has been set up for. Must be cleared whenever the
        # source object changes, since a fresh BladeRF has no trigger.
        self._trig_inited = None
        # True while the Video trigger is holding the display. Edge state,
        # so the signal fires on a transition rather than per sweep.
        self._trig_waiting = False
        # Say the metadata-vs-generator conflict once, not every sweep.
        self._meta_conflict_said = False
        self._zs_freq: Optional[float] = None
        # Quick tune (5.2): frequency (Hz, rounded) -> cached profile bytes.
        self._qt_cache: dict = {}
        # Oversample (5.2): last value actually applied, and the value that
        # failed if it did, so _ensure_oversample() does not retry it every
        # single sweep.
        self._oversample_on = False
        self._oversample_failed_for = None

    def apply(self, cfg: Config) -> None:
        self._lock.lock()
        self._new = replace(cfg)
        self._lock.unlock()

    def request_channel(self, index: int) -> None:
        """Ask for an RX port change; applied between sweeps on this thread."""
        self._lock.lock()
        self._want_ch = 1 if int(index) else 0
        self._lock.unlock()

    def request_iq(self, ms: float, path: str) -> None:
        """Ask for a raw IQ capture; taken between sweeps on this thread."""
        self._lock.lock()
        self._want_iq = (float(ms), str(path))
        self._lock.unlock()

    def _apply_iq(self) -> None:
        """
        Record raw IQ straight to disk.

        Written in chunks rather than gathered in memory first, so a long
        capture costs a fixed buffer instead of a few hundred megabytes. The
        file is bare interleaved complex64 -- what GNU Radio, inspectrum and
        baudline all call a "cfile", and what numpy reads in one line. The
        sidecar text file records everything needed to interpret it.
        """
        self._lock.lock()
        want = self._want_iq
        self._want_iq = None
        self._lock.unlock()
        if want is None:
            return
        ms, path = want
        try:
            fs = self.src.set_sample_rate(self._pin_fs(
                self.cfg, min(self.cfg.max_fs, self._src_fs_max())))
            self.src.set_freq(self.cfg.center)
            want_n = int(max(1024, round(ms * 1e-3 * fs)))
            n = min(want_n, IQ_MAX_SAMPLES)
            got = 0
            with open(path, "wb") as fh:
                while got < n:
                    k = int(min(262144, n - got))
                    self.src.read(k).astype(np.complex64).tofile(fh)
                    got += k
            with open(path + ".txt", "w", encoding="utf-8") as fh:
                fh.write(
                    f"{APP_NAME} {version_line()} -- raw IQ capture\n\n"
                    f"file            {os.path.basename(path)}\n"
                    f"format          interleaved complex64 "
                    f"(float32 I, float32 Q), no header\n"
                    f"samples         {got}\n"
                    f"sample rate     {fs:.0f} Hz\n"
                    f"centre freq     {self.cfg.center:.0f} Hz\n"
                    f"bandwidth       {fs:.0f} Hz "
                    f"({self.cfg.center - fs/2:.0f} .. "
                    f"{self.cfg.center + fs/2:.0f} Hz)\n"
                    f"duration        {got / fs * 1e3:.3f} ms\n"
                    f"port            {getattr(self.src, 'port', 'RX1')}\n"
                    f"gain            {getattr(self.src, 'gain', 0):.0f} dB\n"
                    f"source          {getattr(self.src, 'name', '?')}\n"
                    f"scale           1.0 = converter full scale "
                    f"({getattr(self.src, 'full_scale_dbm', 0.0):+.1f} dBm)\n"
                    f"board table     "
                    f"{float(rx_table_db(self.cfg.center)):+.2f} dB at this "
                    f"centre (typical xA4, measured 2026-09-17)\n"
                    f"recorded        {datetime.now():%Y-%m-%d %H:%M:%S}\n\n"
                    f"Read it back with:\n"
                    f"    import numpy as np\n"
                    f"    iq = np.fromfile(r\"{path}\", dtype=np.complex64)\n")
            trunc = " (capped)" if want_n > n else ""
            self.iqDone.emit(path, f"{got:,} samples, "
                                   f"{got / fs * 1e3:.1f} ms at "
                                   f"{fs / 1e6:.3f} MS/s{trunc}")
        except Exception as exc:
            self.iqDone.emit("", str(exc))

    def _apply_channel(self) -> None:
        self._lock.lock()
        want = self._want_ch
        self._want_ch = None
        self._lock.unlock()
        if want is None:
            return
        try:
            self.src.set_channel(want)
        except Exception as exc:
            self.failed.emit(f"could not switch port: {exc}")
            return
        # bladerf_trigger_init() binds the trigger to a channel, so a port
        # change invalidates it -- re-init on the next arm.
        self._trig_inited = None
        self.clear_traces()
        self.portChanged.emit(int(getattr(self.src, "channel", 0)))

    def request_fire(self) -> None:
        """Ask for the armed master trigger to fire; applied between sweeps
        on this thread -- self.src belongs to the worker, not the GUI."""
        self._lock.lock()
        self._want_fire = True
        self._lock.unlock()

    def _apply_fire(self) -> None:
        self._lock.lock()
        want, self._want_fire = self._want_fire, False
        self._lock.unlock()
        if want:
            try:
                self.src.trigger_fire()
            except Exception as exc:
                self.failed.emit(f"trigger fire failed: {exc}")

    def request_single(self) -> None:
        """Arm exactly one sweep (Single mode). Harmless in Continuous."""
        self._lock.lock()
        self._want_single = True
        self._lock.unlock()

    def _apply_single(self) -> None:
        self._lock.lock()
        if self._want_single:
            self._armed = True
            self._want_single = False
        self._lock.unlock()

    # -- board health / self-test (5.3), all applied on this thread ------
    def request_bias_tee(self, on: bool) -> None:
        self._lock.lock()
        self._want_bias_tee = bool(on)
        self._lock.unlock()

    def request_gain_mode(self, mode: int) -> None:
        self._lock.lock()
        self._want_gain_mode = int(mode)
        self._lock.unlock()

    def request_loopback(self, mode: int) -> None:
        self._lock.lock()
        self._want_loopback = int(mode)
        self._lock.unlock()

    def request_health_read(self) -> None:
        self._lock.lock()
        self._want_health_read = True
        self._lock.unlock()

    def request_selftest(self) -> None:
        self._lock.lock()
        self._want_selftest = True
        self._lock.unlock()

    def _apply_health_actions(self) -> None:
        self._lock.lock()
        bt, gm, lb = self._want_bias_tee, self._want_gain_mode, self._want_loopback
        do_health, do_selftest = self._want_health_read, self._want_selftest
        self._want_bias_tee = self._want_gain_mode = self._want_loopback = None
        self._want_health_read = self._want_selftest = False
        self._lock.unlock()

        if bt is not None:
            try:
                self.src.set_bias_tee(bt)
            except Exception as exc:
                self.failed.emit(f"bias tee failed: {exc}")
        if gm is not None:
            try:
                self.src.set_gain_mode(gm)
            except Exception as exc:
                self.failed.emit(f"gain mode failed: {exc}")
        if lb is not None:
            try:
                self.src.set_loopback(lb)
            except Exception as exc:
                self.failed.emit(f"loopback failed: {exc}")
        if do_selftest:
            try:
                n, gaps = self.src.counter_self_test()
                self.selftestReady.emit((n, gaps))
            except Exception as exc:
                self.selftestReady.emit(str(exc))
        if do_health:
            try:
                self.healthReady.emit({
                    "lib_version": self.src.lib_version(),
                    "fw_version": self.src.fw_version(),
                    "fpga_version": self.src.fpga_version(),
                    "temp": self.src.rfic_temperature(),
                    "power_source": self.src.power_source(),
                    "bus_v": self.src.pmic(PMIC_VOLTAGE_BUS),
                    "current": self.src.pmic(PMIC_CURRENT),
                    "rssi": self.src.rfic_rssi(),
                    "gain_mode": self.src.get_gain_mode(),
                    "bias_tee": self.src.get_bias_tee(),
                    "loopback_supported": self.src.loopback_supported(LB_RFIC_BIST),
                })
            except Exception as exc:
                self.failed.emit(f"health read failed: {exc}")

    def clear_traces(self) -> None:
        for t in self.traces:
            t.reset()
        self._wf = None
        self._zs_freq = None    # force a retune next zero-span sweep
        self._qt_cache = {}     # segment frequencies changed; profiles are stale

    def _pull(self) -> None:
        self._lock.lock()
        new = self._new
        self._new = None
        self._lock.unlock()
        if new is None:
            return
        old, self.cfg = self.cfg, new
        if (old.span, old.center, old.rbw, old.rbw_auto, old.window,
                old.detector, old.points, old.max_fs, old.zero_span) != \
           (new.span, new.center, new.rbw, new.rbw_auto, new.window,
                new.detector, new.points, new.max_fs, new.zero_span):
            self.clear_traces()
        while len(self.traces) < len(new.trace_modes):
            self.traces.append(Trace("Off"))
        for i, (t, m) in enumerate(zip(self.traces, new.trace_modes)):
            if t.mode != m:
                t.mode = m
                t.reset()
            t.avg_count = new.avg_count
            t.view = bool(new.trace_view[i]) if i < len(new.trace_view) \
                else False

    @QtCore.pyqtSlot()
    def stop(self) -> None:
        self._run = False

    @QtCore.pyqtSlot()
    def run(self) -> None:
        self._run = True
        try:
            self.src.start()
        except Exception as exc:
            self.failed.emit(str(exc))
            return

        clock = QtCore.QElapsedTimer()
        clock.start()
        last = 0.0
        while self._run:
            if self._lost:
                self._attempt_reopen(clock)
                continue

            self._pull()
            self._apply_channel()
            self._apply_iq()
            self._apply_fire()
            self._apply_health_actions()
            self._apply_single()
            # Single mode: idle until the Sweep button arms one. Everything
            # above still runs while idling, so config edits, port changes,
            # health reads and IQ captures all keep working with
            # acquisition stopped -- which is the point of Single on a
            # bench analyzer, not a frozen instrument.
            if not self.cfg.continuous and not self._armed:
                # Held, but the front end is still enabled and still
                # producing. See _idle_keepalive().
                try:
                    self._idle_keepalive(clock)
                except RadioError as exc:
                    self._on_radio_error(exc, clock)
                    continue
                except Exception as exc:
                    self.failed.emit(str(exc))
                QtCore.QThread.msleep(30)
                continue
            single = not self.cfg.continuous
            t_sweep = clock.nsecsElapsed() / 1e9
            try:
                res = self._sweep(self.cfg)
            except RadioError as exc:
                self._on_radio_error(exc, clock)
                continue
            except Exception as exc:
                self.failed.emit(str(exc))
                QtCore.QThread.msleep(300)
                continue
            # A sweep that got its samples is the only evidence there is
            # that the stream is healthy, so this is where the recovery
            # counter clears and the notice comes off the status bar. Note
            # res is None for a sweep the Video trigger rejected -- the
            # samples still arrived, so that counts.
            if self._stream_fails:
                self._stream_fails = 0
                self.stalled.emit(0, "")
            if res is None:
                continue

            # Manual sweep time: pace the pass to what was asked for. This
            # can only ever ADD delay -- a sweep that already takes longer
            # than the setting is reported as it is rather than being
            # pretended about, which is the same honesty the RBW readout has
            # to have (bug 24).
            if not self.cfg.sweep_auto:
                spare = self.cfg.sweep_time - (clock.nsecsElapsed() / 1e9
                                               - t_sweep)
                if spare > 0:
                    QtCore.QThread.msleep(int(spare * 1000))

            # Video trigger. The sweep is taken either way -- the radio keeps
            # running, config edits keep working -- and then ACCEPTED only if
            # its own detected level crossed the threshold. Rejecting it here
            # rather than before the measurement is what makes the trace,
            # every trace mode, the markers and the measurements all see one
            # consistent set of sweeps: a rejected sweep never existed as far
            # as anything downstream is concerned.
            #
            # The `continue` also skips the `_armed = False` at the bottom of
            # the loop, so an armed SINGLE sweep keeps re-sweeping until the
            # level appears and then stops on the sweep that carried it --
            # which is what "single triggered sweep" means on a bench
            # analyzer, and is deliberate rather than a leak.
            if self.cfg.trig_mode == TRIG_VIDEO:
                pk = float(np.nanmax(res.power)) if res.power.size else -300.0
                if pk < self.cfg.trig_level:
                    if not self._trig_waiting:
                        self._trig_waiting = True
                        self.trigWait.emit(True)
                    continue
                if self._trig_waiting:
                    self._trig_waiting = False
                    self.trigWait.emit(False)
            elif self._trig_waiting:
                self._trig_waiting = False
                self.trigWait.emit(False)

            tr = [t.update(res.power) for t in self.traces]
            if self._wf is None or self._wf.size != res.power.size:
                self._wf = res.power.copy()
            else:
                np.maximum(self._wf, res.power, out=self._wf)
            self._folded += 1
            self._cnt += 1

            now = clock.nsecsElapsed() / 1e9
            # A single sweep must reach the screen whatever the frame-rate
            # throttle says -- otherwise pressing Sweep could take a whole
            # frame interval to show anything, or at 1 fps appear to do
            # nothing at all.
            if single or now - last >= 1.0 / max(self.cfg.fps, 1.0):
                rate = self._cnt / (now - self._t_rate) if now > self._t_rate else 0
                self.ready.emit(replace(res, traces=tuple(tr),
                                        wf_row=self._wf.copy(),
                                        folded=self._folded, rate=rate))
                last, self._t_rate = now, now
                self._cnt = self._folded = 0
                self._wf = None
            if single:
                self._armed = False
                self.sweepDone.emit()
        try:
            self.src.stop()
        except Exception:
            pass

    def _idle_keepalive(self, clock) -> None:
        """
        Keep the receive stream drained while the sweep is HELD in Single.

        The idle branch above stops reading entirely, which is the whole
        point of Single -- but stopping READING is not stopping RECEIVING.
        The front end stays enabled and the FPGA goes on filling the
        stream's buffers, and a sync stream whose buffers are never claimed
        can stop delivering altogether. Its only symptom then is a timeout
        on the next sweep, minutes later, long after the hold that caused
        it -- which is exactly the shape of "sometimes after a while of
        playing the program". Bug 39 made Single a two-state key you can
        easily be parked in, so being held for minutes is now ordinary.

        So a small block is claimed a few times a second and thrown away:
        enough to keep the ring turning, far too little to be a
        measurement. BladeRF.keepalive() is where the two states it must
        not do this in are documented.
        """
        now = clock.nsecsElapsed() / 1e9
        if now < self._next_drain:
            return
        self._next_drain = now + self.IDLE_DRAIN_SECONDS
        # A keepalive that CLAIMED A BLOCK is evidence the stream is
        # healthy, and it has to be allowed to count -- otherwise nothing
        # clears the recovery counter while the sweep is held, which is the
        # one state this method exists for. The run loop clears it after a
        # successful _sweep, and a held sweep never reaches that line, so
        # three stalls cured minutes apart escalated to a full device
        # reopen with "the receive stream would not restart" -- untrue, it
        # restarted every time -- and the amber notice could never be
        # withdrawn. Bug 51.
        #
        # Only a real read counts: keepalive() declines in a META format,
        # with a trigger armed, and when there is no stream, and declining
        # proves nothing at all. That is why it reports whether it read
        # rather than returning None.
        if self.src.keepalive() and self._stream_fails:
            self._stream_fails = 0
            self.stalled.emit(0, "")

    def _on_radio_error(self, exc: Exception, clock) -> None:
        """
        One place deciding what a failed read MEANS.

        Three outcomes, in the order the evidence arrives:

          * an I/O or no-device code says the board is physically gone --
            straight to the reopen machinery, with no round trips first,
            because bug 4's measured 0.01 s detection depends on not
            adding any;
          * the board not answering a cheap register read says the same
            thing about a code that does not say it outright;
          * anything else with a live handle is NOT a disconnect, and until
            2026-09-06 it was retried against the same stream every 300 ms
            for ever. That is the freeze. It goes to _recover_stream().
        """
        gone = getattr(exc, "rc", 0) in FATAL_CODES
        if not gone and isinstance(self.src, BladeRF):
            gone = not self.src.alive()
        if gone:
            self._stream_fails = 0
            self._enter_lost(str(exc), clock)
            return
        self._recover_stream(str(exc), clock)

    def _recover_stream(self, why: str, clock) -> None:
        """
        The board answers but the stream does not: restart the stream.

        See BladeRF.restart_stream() for why this is the cure and why
        retrying the read is not. Escalates to the reopen path after
        STREAM_RETRY_MAX goes, so a fault the restart cannot reach still
        gets the heavier remedy rather than looping here instead.

        Every attempt is announced through `stalled`, which the GUI keeps on
        screen until a sweep lands. A recovery nobody can see is the same
        thing as a freeze from where the user is standing.
        """
        self._stream_fails += 1
        self.stalled.emit(self._stream_fails, why)
        # Not gated on isinstance(self.src, BladeRF): restart_stream() is
        # declared on Source and is a no-op where there is no stream, so
        # this machinery is the same code on every source -- which is what
        # lets a stub prove it without a board attached.
        if self._stream_fails > self.STREAM_RETRY_MAX:
            self._stream_fails = 0
            self._enter_lost(f"{why} -- the receive stream would not "
                             f"restart, reopening the device", clock)
            return
        try:
            self.src.restart_stream()
            self._stream_restarts += 1
            # stop() disarms a trigger on the way through and start() brings
            # the stream back with none initialised, so what the device is
            # set up for is no longer what we think it is. That is bug 18's
            # trap exactly -- a stale _trig_inited makes the next arm skip
            # its own trigger_init() and arm nothing -- and it is why
            # _attempt_reopen clears the same three.
            self._trig_role, self._trig_signal = TRIG_ROLE_DISABLED, None
            self._trig_inited = None
        except Exception as exc:
            self.failed.emit(f"could not restart the receive stream: {exc}")
            QtCore.QThread.msleep(300)
            return
        # Long enough that a link which needs a moment gets one, short
        # enough that a cured stream is back inside a frame or two.
        QtCore.QThread.msleep(50)

    def stream_restarts(self) -> int:
        """How many times the receive stream has had to be restarted."""
        return self._stream_restarts

    def _enter_lost(self, why: str, clock) -> None:
        """Release the dead handle and go into retry mode."""
        if isinstance(self.src, BladeRF):
            try:
                self.src.close()
            except Exception:
                pass
        self._lost = True
        self._tries = 0
        # A capture asked for just before the cable went would otherwise sit
        # pending for ever, leaving the button stuck on "recording...".
        self._lock.lock()
        pending = self._want_iq is not None
        self._want_iq = None
        self._lock.unlock()
        if pending:
            self.iqDone.emit("", "radio disconnected during the capture")
        self._next_try = clock.nsecsElapsed() / 1e9      # try again at once
        self.clear_traces()
        self.lost.emit(why)

    def _attempt_reopen(self, clock) -> None:
        now = clock.nsecsElapsed() / 1e9
        if now < self._next_try:
            QtCore.QThread.msleep(150)                   # stay responsive
            return
        self._next_try = now + self.RETRY_SECONDS
        self._tries += 1
        if self.reopen is None:
            self._lost = False
            return
        try:
            fresh = self.reopen()
        except Exception:
            self.failed.emit(f"bladeRF disconnected - waiting for it to come "
                             f"back (attempt {self._tries})")
            return
        self.src = fresh
        # A fresh device object has no trigger initialised on it, and the
        # stream is back in a plain format -- forget what the old handle was
        # set up for or the next arm would skip its own trigger_init().
        self._trig_role, self._trig_signal = TRIG_ROLE_DISABLED, None
        self._trig_inited = None
        try:
            self.src.start()
        except Exception as exc:
            self.failed.emit(f"reopened but could not start: {exc}")
            try:
                self.src.close()
            except Exception:
                pass
            return
        self._lost = False
        self._tries = 0
        self.clear_traces()
        self.back.emit(fresh)

    def _ensure_oversample(self, c: Config) -> None:
        want = bool(c.oversample)
        if want == self._oversample_on or want == self._oversample_failed_for:
            return
        try:
            self.src.enable_oversample(want)
            self._oversample_on, self._oversample_failed_for = want, None
        except RadioError as exc:
            self.failed.emit(f"oversample {'on' if want else 'off'} failed: {exc}")
            self._oversample_failed_for = want

    def _plan_rbw(self, c: Config, rbw: float, fs_want: float):
        """
        Pick the window length AND the sample rate together, so the RBW that
        comes out is the RBW that was asked for.

        This exists because the old code picked them separately and lost
        badly. It rounded the length up to a power of two and floored it at
        256, so the realised RBW ran between 0.5x and 0.2x of the setting:
        ask for 100 kHz on a 1 MHz span and you measured with 19.6 kHz; ask
        for 3 MHz at full span and you measured with 905 kHz. Nothing was
        wrong with the number in the status bar -- it was the honest realised
        value -- but the two readouts disagreed, and the setting was the one
        lying.

        RBW = fs * ENBW_bins / n, so with n on a discrete grid the only way
        to land on an exact RBW is to move fs the last fraction of a percent
        to meet it. That is free: the bladeRF's rate is fractional-N, and
        nudging it by under 1% changes nothing else that matters. Preference
        goes to nudging fs UP (a wider segment, never fewer than before);
        down is tried only when the rate ceiling blocks that.

        Returns (n, fs) and does not touch the radio -- the caller sets the
        rate and re-derives the realised RBW from what the board actually
        took, so hardware quantisation cannot make the readout a lie either.
        """
        # An RBW wider than a quarter of the span is not a measurement --
        # it is two or three resolution cells across the whole screen, and
        # solving for it drives the sample rate to many times the span, at
        # which point the span holds barely a bin and the trace is empty.
        # Bench analyzers limit RBW by span for the same reason. Clamped
        # here rather than in the menu because the menu cannot see the span,
        # and the caller still reports what was ASKED for, so the clamp shows
        # up in the status bar instead of happening silently.
        rbw = min(rbw, max(c.span, 1.0) / 4.0)
        if c.fs_pin > 0.0:
            # The generator owns the rate (5.5 fact 1) and it is not ours to
            # move, so the length is the only knob left. Off the denser grid,
            # nearest member either way: measured worst 0.79% against 5.8% for
            # the 7-smooth round-up, which is the difference between the amber
            # "(set X)" meaning something and firing on half of all settings.
            return rbw_to_n_fixed_fs(rbw, fs_want, c.window), fs_want
        # Raise the starting rate to the floor BEFORE choosing a length, not
        # just bound the search by it. The length comes off fs_want, so a
        # fs_want below FS_MIN_LINEAR produces a short n whose exact rate is
        # also below the floor -- out of bounds, both directions refused, and
        # the function falls through to the fixed-rate branch and hands back
        # the very rate the floor exists to avoid. Found by a suite: the
        # whole-hertz assertion failed, because that fallback returns
        # fs_want unrounded.
        fs_want = max(fs_want, self._fs_min_linear())
        # Only the DOWNWARD move is bounded, and the asymmetry is the point.
        # Raising the rate widens each segment, so it can only reduce the
        # segment count -- never slower, and the length stays as short as it
        # was. Lowering it does the opposite: without a bound, 1 Hz RBW on a
        # 100 MHz span would drag fs from 40 MS/s to 0.93 MS/s, buying an
        # exact RBW at the price of a four-minute sweep. Better to miss that
        # RBW and say so. (Bounding the upward move too was the first attempt
        # and it broke the ordinary case: 100 kHz on a 1 MHz span needs fs to
        # go 1.33 -> 2.79 MS/s, and refusing that put 47.8 kHz on screen
        # under a setting of 100 kHz -- the exact fault being fixed here.)
        fs_hi = min(c.max_fs, self.src.fs_max)
        # _fs_min_linear(), not src.fs_min: solving for an exact RBW must not
        # be allowed to drop the rate under FS_MIN_LINEAR, where the level
        # gains about 11 dB the amplitude model knows nothing about. An exact
        # RBW at a wrong level is not the trade this function exists to make.
        fs_lo = max(self._fs_min_linear(), fs_want / 1.5)
        for up in (True, False):
            n = rbw_to_n(rbw, fs_want, c.window, up)
            fs_exact = float(int(round(rbw * n / enbw_bins(c.window, n))))
            if fs_lo <= fs_exact <= fs_hi:
                return n, fs_exact
        # Neither move was allowed, so the rate is effectively stuck and
        # only the length is left -- the pinned problem again, same answer.
        return rbw_to_n_fixed_fs(rbw, fs_want, c.window), fs_want

    def _sweep(self, c: Config) -> Optional[Result]:
        self.src.set_gain(c.gain)
        self._ensure_oversample(c)
        if not c.zero_span and self._trig_role != TRIG_ROLE_DISABLED:
            # A trigger stays armed across a format switch -- leaving zero
            # span without disarming it would make every stepped-sweep
            # read() block waiting for a signal nothing will ever fire
            # outside that mode.
            try:
                self.src.trigger_arm(TRIG_ROLE_DISABLED)
            except Exception:
                pass
            self._trig_role, self._trig_signal = TRIG_ROLE_DISABLED, None
        if c.zero_span:
            return self._time_domain(c)

        # The floor is _fs_min_linear() rather than the radio's own minimum:
        # a narrow span would otherwise plan a rate under FS_MIN_LINEAR and
        # every level in that sweep would read about 11 dB high. A wider
        # segment than the span needs costs nothing -- the sweep already
        # selects the middle 0.75 * fs out of whatever it got.
        fs_want = self._pin_fs(c, min(max(c.span / 0.75,
                                          self._fs_min_linear()),
                                      min(c.max_fs, self.src.fs_max)))
        rbw_req = (auto_rbw(c.span, fs_want, c.window) if c.rbw_auto
                   else c.rbw)
        n, fs_want = self._plan_rbw(c, rbw_req, fs_want)
        fs = self.src.set_sample_rate(fs_want)
        usable = self.src.usable_bandwidth
        w, wsum = self._window(c.window, n)
        # Bin spacing for the DISPLAY. Padding never changes the RBW; it only
        # stops a legitimately coarse RBW from drawing as a staircase.
        nfft = display_nfft(n, fs, c.span, c.points)

        nseg = max(1, min(int(np.ceil(c.span / usable)) if c.span > usable else 1, 400))
        if nseg == 1:
            centres, keep = [c.center], min(c.span, usable)
        else:
            keep = usable
            centres = [c.start + keep / 2 + i * keep for i in range(nseg)]

        # The bins each segment keeps, worked out ONCE per sweep rather than
        # per segment: offsets m * fs/nfft from the LO with m in [m_lo, m_hi],
        # i.e. the middle `keep` of the transform. Read straight out of the
        # UNshifted FFT as two slices (negative offsets live at the top), so
        # no fftshift and no boolean mask over the whole transform per
        # segment -- that, the dB conversion of every bin, and the sort
        # afterwards were most of what a narrow RBW cost (see
        # GridAccumulator). Offsets are (k - nfft//2) * df, which is also
        # right for an ODD nfft; the linspace this replaced put an odd
        # transform half a padded bin off.
        df = fs / nfft
        ms = np.arange(-(nfft // 2), nfft - nfft // 2)
        moff = ms * df
        ksel = np.nonzero((moff >= -keep / 2) & (moff < keep / 2))[0]
        if ksel.size == 0:
            return None
        m_lo, m_hi = int(ms[ksel[0]]), int(ms[ksel[-1]])
        offs = moff[ksel[0]:ksel[-1] + 1]
        n_neg = (min(m_hi, -1) - m_lo + 1) if m_lo < 0 else 0
        inv_w2 = 1.0 / (wsum * wsum)

        def seg_power(iq_: np.ndarray) -> np.ndarray:
            X = np.fft.fft(iq_ * w, n=nfft)
            lin = np.empty(offs.size)
            if m_lo < 0:
                neg = X[nfft + m_lo:nfft + min(m_hi, -1) + 1]
                np.add(neg.real * neg.real, neg.imag * neg.imag,
                       out=lin[:n_neg])
            if m_hi >= 0:
                pos = X[max(m_lo, 0):m_hi + 1]
                np.add(pos.real * pos.real, pos.imag * pos.imag,
                       out=lin[n_neg:])
            lin *= inv_w2
            # The old 20*log10(max(|X|/wsum, 1e-10)) floor, in power.
            np.maximum(lin, 1e-20, out=lin)
            return lin

        real_hw = bool(getattr(self.src, "real_hardware", False))
        grid = c.points >= 2
        acc = (GridAccumulator(c.points, c.start, c.stop, c.detector,
                               bool(c.meas_on)) if grid else None)
        dual = bool(getattr(self.src, "dual_rx", False))
        acc2 = (GridAccumulator(c.points, c.start, c.stop, c.detector, False)
                if grid and dual else None)
        # Full-resolution copies are kept only while they are small: they
        # serve the two rare cases the grid cannot (fewer than two points, or
        # a span holding fewer than five bins), and those are small by
        # construction. Never millions of bins, which is the whole point.
        raw: List[Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]] = []
        raw_n = 0
        # Strongest bin so far: (its linear power, segment index, that
        # segment's frequencies and powers, index within them).
        best = None
        segs_in = 0
        f_first = f_last = None
        over = False
        qt_hits = qt_attempted = 0
        for fc in centres:
            if not self._run:
                return None
            if c.quick_tune:
                # See BladeRF.schedule_retune()'s docstring: unverified
                # whether this actually retunes on real hardware. Cache
                # is keyed by exact segment frequency; a segment set stays
                # fixed for a given span/center, so it fills in once and
                # replays from then on. Falls back to the normal tune
                # whenever the cache misses or a replay errors.
                key = round(fc)
                qt_attempted += 1
                cached = self._qt_cache.get(key)
                if cached is not None:
                    try:
                        actual = self.src.schedule_retune(fc, cached)
                        qt_hits += 1
                    except RadioError:
                        self._qt_cache.pop(key, None)
                        actual = self.src.set_freq(fc)
                else:
                    actual = self.src.set_freq(fc)
                    try:
                        self._qt_cache[key] = self.src.get_quick_tune_bytes()
                    except RadioError:
                        pass    # this driver/board combo just doesn't support it
            else:
                actual = self.src.set_freq(fc)
            # RX1+RX2 captures both ports in the same instant, so the second
            # port is stitched alongside the first rather than swept twice.
            if dual:
                iq, iq2 = self.src.read_dual(n)
            else:
                iq, iq2 = self.src.read(n), None
            if not over and iq.size:
                # |iq|^2 against 0.999^2: the same test as max(|iq|) >= 0.999
                # without a square root per sample.
                if float(np.max(iq.real * iq.real + iq.imag * iq.imag)) \
                        >= 0.999 * 0.999:
                    over = True
            fseg = offs + actual
            lin = seg_power(iq)
            lin2 = seg_power(iq2) if iq2 is not None else None
            if real_hw:
                # The board table, per frequency rather than per sweep -- a
                # full span crosses 10 dB of this curve -- and per BIN, before
                # the peak search, the detector and the mean, so every reader
                # of this sweep sees one corrected set of numbers. Applying it
                # per display point instead was tried on 2026-10-07 and is
                # 0.4 dB out at 101 points on a full span, because the table
                # moves that much across one point.
                #
                # ONLY ON REAL HARDWARE. The table describes a physical
                # receive chain; the simulated scene places its carriers at
                # exact levels and has no front end to correct for, so
                # applying it there would make the application misreport its
                # own synthetic scene -- and every measurement suite injects
                # a known level and reads it back, which is precisely that.
                # Same test the overpower warning uses (5.22).
                g = np.exp(rx_table_db(fseg) * (np.log(10.0) / 10.0))
                lin *= g
                if lin2 is not None:
                    lin2 *= g
            if raw is not None:
                raw.append((fseg, lin, lin2))
                raw_n += fseg.size
                if raw_n > self.RAW_CAP and grid:
                    raw = None
            if grid:
                i0 = int(np.searchsorted(fseg, c.start, "left"))
                i1 = int(np.searchsorted(fseg, c.stop, "right"))
                if i1 > i0:
                    fi, li = fseg[i0:i1], lin[i0:i1]
                    acc.add(fi, li)
                    if acc2 is not None:
                        acc2.add(fi, lin2[i0:i1])
                    # The table is already in `li`, so the strongest bin is
                    # judged by its corrected level.
                    k = int(np.argmax(li))
                    db = float(li[k])
                    if best is None or db > best[0]:
                        best = (db, segs_in, fi, li, k)
                    segs_in += 1
                    f_first = fi[0] if f_first is None else min(f_first, fi[0])
                    f_last = fi[-1] if f_last is None else max(f_last, fi[-1])

        offset = self.src.full_scale_dbm + c.cal_offset
        if grid and acc.n_in > 4:
            fd, pd, pm = acc.finish()
            pd2 = acc2.finish()[1] if acc2 is not None else None
            pk_hz, pk_db = self._peak_from(best, acc.ends)
            cal_extrap = bool(real_hw and f_first is not None and (
                float(f_first) < RX_CAL_LO_HZ or float(f_last) > RX_CAL_HI_HZ))
            if pm is not None:
                pm = pm + offset
            if pd2 is not None:
                pd2 = pd2 + offset
        else:
            if not raw:
                return None
            fd, pd, pm, pd2, pk_hz, pk_db, cal_extrap = self._reduce_full(
                c, raw, real_hw)
            if pm is not None:
                pm = pm + offset
            if pd2 is not None:
                pd2 = pd2 + offset
        return Result(fd, pd + offset,
                      n_to_rbw(n, fs, c.window), nseg, n, overload=over,
                      fs_low=fs <= FS_MIN_LINEAR, cal_extrap=cal_extrap,
                      peak_hz=pk_hz,
                      peak_dbm=pk_db + offset if np.isfinite(pk_db) else pk_db,
                      qt_hits=qt_hits, qt_attempted=qt_attempted,
                      power2=pd2, rbw_req=rbw_req, nfft_pad=nfft, fs=fs,
                      power_mean=pm)

    def _window(self, name: str, n: int):
        """(window, its sum), built once per (shape, length) rather than
        once per sweep -- a 90,000-point Gaussian is an exp per sample, and
        the sweep asks for the same one on every pass."""
        key = (name, int(n))
        hit = getattr(self, "_win_cache", None)
        if hit is None or hit[0] != key:
            w = make_window(name, int(n))
            hit = (key, w, float(np.sum(w)))
            self._win_cache = hit
        return hit[1], hit[2]

    def _peak_from(self, best, ends):
        """
        _interp_peak()'s answer from the per-segment search: the strongest
        bin, refined by a parabola through it and its two neighbours, in
        dBFS with the board table already in it.

        A neighbour can sit in the NEXT or PREVIOUS segment when the peak is
        a segment's edge bin; `ends` holds each segment's outermost bins in
        the order they were added, which is ascending frequency -- the same
        neighbour the sorted full-resolution array used to supply.
        """
        if best is None:
            return float("nan"), float("nan")
        _lin, si, fi, li, k = best
        y1 = 10.0 * np.log10(li[k])
        left = right = None
        if k > 0:
            left = (float(fi[k - 1]), float(li[k - 1]))
        elif si > 0:
            left = (ends[si - 1][2], ends[si - 1][3])
        if k < fi.size - 1:
            right = (float(fi[k + 1]), float(li[k + 1]))
        elif si < len(ends) - 1:
            right = (ends[si + 1][0], ends[si + 1][1])
        if left is None or right is None:
            return float(fi[k]), float(y1)
        f3 = np.array([left[0], float(fi[k]), right[0]])
        p3 = 10.0 * np.log10(np.array([left[1], li[k], right[1]]))
        return self._interp_peak(f3, p3)

    def _reduce_full(self, c: Config, raw, real_hw: bool):
        """
        The full-resolution reduction, for the two cases GridAccumulator
        does not take: fewer than two display points, or a span holding so
        few bins (five or fewer) that there is no honest grid to put them
        on. Both are small by construction. This is the arithmetic the
        sweep ran for every case until 2026-10-07, unchanged.

        Returns (freqs, detected dB, mean dB or None, second port dB or
        None, peak Hz, peak dB, cal_extrap), all before the offset.
        """
        f = np.concatenate([r[0] for r in raw])
        p = 10.0 * np.log10(np.concatenate([r[1] for r in raw]))
        p2 = (10.0 * np.log10(np.concatenate([r[2] for r in raw]))
              if raw[0][2] is not None else None)
        order = np.argsort(f)
        f, p = f[order], p[order]
        if p2 is not None:
            p2 = p2[order]
        inside = (f >= c.start) & (f <= c.stop)
        if inside.sum() > 4:
            f, p = f[inside], p[inside]
            if p2 is not None:
                p2 = p2[inside]
        # The board table is already in `raw` (applied per bin in _sweep).
        cal_extrap = bool(real_hw and f.size and (
            float(f[0]) < RX_CAL_LO_HZ or float(f[-1]) > RX_CAL_HI_HZ))
        pk_hz, pk_db = self._interp_peak(f, p)
        # Exactly c.points points from start to stop (_grid_slots), unless
        # the band filter above had to be skipped -- then there is no honest
        # start/stop to put the grid on, and the old bucketing stands.
        grid = inside.sum() > 4 and c.points >= 2
        if grid:
            fd, pd = detect_grid(f, p, c.points, c.detector, c.start, c.stop)
        else:
            fd, pd = detect(f, p, c.points, c.detector)
        pm = None
        if c.meas_on:
            pm = (mean_power_grid(f, p, c.points, c.start, c.stop)[1] if grid
                  else mean_power(f, p, c.points)[1])
        pd2 = None
        if p2 is not None:
            _, pd2 = (detect_grid(f, p2, c.points, c.detector, c.start, c.stop)
                      if grid else detect(f, p2, c.points, c.detector))
        return fd, pd, pm, pd2, pk_hz, pk_db, cal_extrap

    @staticmethod
    def _interp_peak(f: np.ndarray, p: np.ndarray):
        """
        Strongest bin, refined by fitting a parabola through it and its two
        neighbours. A windowed tone has a smooth log-magnitude peak, so this
        lands well inside one bin instead of snapping to the bin centre.
        """
        if p.size < 3:
            return float("nan"), float("nan")
        k = int(np.argmax(p))
        if k == 0 or k == p.size - 1:
            return float(f[k]), float(p[k])
        y0, y1, y2 = float(p[k - 1]), float(p[k]), float(p[k + 1])
        denom = y0 - 2.0 * y1 + y2
        if denom == 0.0:
            return float(f[k]), y1
        d = 0.5 * (y0 - y2) / denom
        if not np.isfinite(d) or abs(d) > 1.0:
            return float(f[k]), y1
        # Local bin spacing -- the stitched array is not perfectly uniform at
        # the joins between sweep segments.
        step = (float(f[k + 1]) - float(f[k - 1])) / 2.0
        return float(f[k]) + d * step, y1

    # Samples per time-domain capture. The old code always ran at the full
    # sample rate and then clipped the count, which had two consequences: a
    # 10 ms request silently showed only 6.55 ms, and the GUI was handed
    # 262144 points to redraw every frame, which is what locked the window up.
    ZERO_SPAN_SAMPLES = 120_000

    def _ensure_trigger(self, c: Config) -> None:
        """
        (Re)arm the source's trigger to match cfg, but only on a change.

        Order matters and is easy to get backwards: arming gates the *next*
        bladerf_enable_module(true), not an already-running stream. Measured
        on real hardware -- arming a slave trigger after the stream was
        already flowing returned real samples immediately even though
        trigger_state() correctly still reported "not fired", because the
        gate had nothing left to hold back. So: stop the stream, set the
        format, arm, and only then let it restart.
        """
        if c.trigger_role == self._trig_role and c.trigger_signal == self._trig_signal:
            return
        self.src.stop()
        if c.trigger_role == TRIG_ROLE_DISABLED:
            if self._trig_role != TRIG_ROLE_DISABLED:
                self.src.trigger_arm(TRIG_ROLE_DISABLED)
            self.src._set_format_lazy(FMT_SC16_Q11)
        else:
            self.src._set_format_lazy(FMT_SC16_Q11_META)
            # Keyed off what has actually been INITIALISED on the source,
            # not off the signal we merely intend to use. Those two are not
            # the same thing, and conflating them broke triggered capture
            # from the GUI completely: entering zero span runs this with
            # role=DISABLED first, which recorded _trig_signal without ever
            # calling trigger_init(), so the first real arm decided the
            # signal was "unchanged", skipped the init, and every sweep then
            # failed with "trigger_init() was not called". The worker-level
            # test never caught it because it arms on the very first call,
            # where _trig_signal is still None.
            if c.trigger_signal != self._trig_inited:
                self.src.trigger_init(c.trigger_signal)
                self._trig_inited = c.trigger_signal
            self.src.trigger_arm(c.trigger_role)
            self.src.start()      # now that it's armed, let it gate this
        self._trig_role, self._trig_signal = c.trigger_role, c.trigger_signal

    def _time_domain(self, c: Config) -> Result:
        """
        LO parked; envelope against time.

        The time span sets the sample rate, the way a scope's timebase does.
        That keeps the whole requested duration on screen at a sample count
        the display can actually cope with.
        """
        span_s = max(c.zero_time, 1e-9)
        # _fs_min_linear() for the same reason the stepped sweep uses it: a
        # slow timebase asks for a low rate, and under FS_MIN_LINEAR the
        # envelope would sit about 11 dB high with nothing to say so. The
        # sample COUNT is clipped below, so a higher rate here costs samples
        # that get decimated for the display rather than a longer capture.
        want = min(c.max_fs, self._src_fs_max(),
                   max(self.ZERO_SPAN_SAMPLES / span_s,
                       self._fs_min_linear()))
        fs = self.src.set_sample_rate(self._pin_fs(c, want))
        # discard=False: the tune-settle flush goes through plain read(),
        # which forces SC16_Q11 and, as a side effect of stop()'s own safety
        # check, silently disarms whatever trigger was just armed -- found
        # the hard way, as a reproducible hang (the flush's own read() times
        # out waiting on the gate it just tore down, and closing the app
        # while that stale state unwinds can wedge the stream). Zero span
        # does not need the same settle guarantee the stepped FFT sweep
        # does, so skip it outright here rather than just skip it sometimes.
        # Also only retune when the centre actually moved, since _pull()
        # only clears _zs_freq on an actual config change.
        if getattr(self, "_zs_freq", None) != c.center:
            self.src.set_freq(c.center, discard=False)
            self._zs_freq = c.center
        n = int(np.clip(round(span_s * fs), 256, 400_000))

        if c.trigger_role == TRIG_ROLE_DISABLED:
            self._ensure_trigger(c)
            iq = self.src.read(n)
            ts, overrun, miniexp1, miniexp2 = None, False, False, False
            n_actual = int(iq.size)
        else:
            self._ensure_trigger(c)
            # Always RX_NOW: "give me whatever is next" is exactly what
            # blocks correctly here, because it is the trigger's own arm
            # state that gates the FPGA stream, not this flag. The metadata
            # timestamp field is a *different* mechanism (schedule a read
            # for an absolute FPGA-counter tick) -- leaving RX_NOW off and
            # a zero timestamp asks for tick 0, which is always already in
            # the past and fails with -14, not the expected -6 timeout.
            #
            # With a trigger armed -- master or slave, both block on their
            # own RX until fired, per libbladeRF.h's own worked example --
            # bladerf_sync_rx blocks until it fires and returns a timeout if
            # it doesn't. That is normal while waiting, not a disconnect, so
            # loop on it here with a short timeout rather than letting it
            # reach run()'s except block, which would otherwise call alive()
            # and post a "failed" message every 300 ms purely because
            # nothing has fired yet.
            while True:
                try:
                    iq, ts, overrun, n_actual, miniexp1, miniexp2 = \
                        self.src.read_meta(n, rx_now=True, timeout_ms=300)
                    break
                except RadioError as exc:
                    rc = getattr(exc, "rc", 0)
                    if rc == ERR_INVAL:
                        # Metadata mode is device-wide and locks BOTH ways.
                        # Measured on the board: configuring the RX stream
                        # for metadata returns -3 while the transmitter is
                        # streaming in a plain format, exactly as a metadata
                        # TX config fails while plain RX runs. So a
                        # triggered capture and the generator genuinely
                        # cannot both be up. The UI keeps them apart, but if
                        # that is ever bypassed, say so ONCE and fall back
                        # to an untriggered capture rather than failing this
                        # sweep over and over with a driver error nobody can
                        # act on.
                        if not self._meta_conflict_said:
                            self._meta_conflict_said = True
                            self.failed.emit(
                                "triggered capture needs the radio in "
                                "metadata mode, which it cannot enter while "
                                "the generator is transmitting -- unkey "
                                "RF OUT to use the trigger. Capturing "
                                "untriggered for now.", )
                        try:
                            self.src.trigger_arm(TRIG_ROLE_DISABLED)
                        except Exception:
                            pass
                        self._trig_role, self._trig_signal = \
                            TRIG_ROLE_DISABLED, None
                        iq = self.src.read(n)
                        ts, overrun, miniexp1, miniexp2 = None, False, False, False
                        n_actual = int(iq.size)
                        break
                    waiting = rc == ERR_TIMEOUT
                    if not waiting:
                        raise
                    if not self._run:
                        return None
                    # A Fire click queued while blocked here has to be
                    # serviced from inside this loop -- run()'s own call to
                    # _apply_fire() never gets a turn until this returns,
                    # and a master trigger waits on exactly this call.
                    self._apply_fire()
                    # Likewise a config edit. Turning the trigger back OFF
                    # while a slave is armed and waiting is the case that
                    # matters: without this the worker sits here for ever
                    # waiting for a trigger the user has just cancelled, and
                    # the display looks frozen with no way out. Returning
                    # None sends run() round again, where _pull() picks the
                    # new config up.
                    self._lock.lock()
                    pending = self._new is not None
                    self._lock.unlock()
                    if pending:
                        return None
                    continue

        over = bool(iq.size and np.max(np.abs(iq)) >= 0.999)
        env = (20.0 * np.log10(np.maximum(np.abs(iq), 1e-10))
              if iq.size else np.array([], dtype=np.float64))
        t = np.arange(iq.size) / fs

        # Same bucketing as the spectrum path, so a short burst inside the
        # window still shows up at Peak detection instead of being skipped.
        td, ed = detect(t, env, c.points, c.detector)
        # One LO, one frequency, so the board table is a scalar here --
        # and real hardware only, for the reason _sweep gives.
        real = bool(getattr(self.src, "real_hardware", False))
        tab = float(rx_table_db(c.center)) if real else 0.0
        return Result(td, ed + self.src.full_scale_dbm + c.cal_offset + tab,
                      fs, 1, n, overload=over, fs_low=fs <= FS_MIN_LINEAR,
                      cal_extrap=real and not (RX_CAL_LO_HZ <= c.center
                                               <= RX_CAL_HI_HZ),
                      # fs as a KEYWORD as well: the positional above
                      # lands in the rbw slot, which is what the zero
                      # span readout wants, and left Result.fs at 0 --
                      # so nothing could ask this sweep what rate it
                      # ran at.
                      fs=fs,
                      timestamp=ts, overrun=overrun,
                      miniexp1=miniexp1, miniexp2=miniexp2,
                      n_actual=n_actual, n_requested=n)

    def _pin_fs(self, c: Config, want: float) -> float:
        """The rate to actually ask for: the generator's, if it is keyed.

        See Config.fs_pin -- RX and TX cannot hold different sample rates on
        this board, so while the transmitter is running the analyzer follows
        it rather than the two overwriting each other every sweep."""
        if c.fs_pin > 0:
            return float(np.clip(c.fs_pin, self._src_fs_min(), self._src_fs_max()))
        return want

    def _src_fs_max(self) -> float:
        return float(getattr(self.src, "fs_max", 61.44e6))

    def _src_fs_min(self) -> float:
        return float(getattr(self.src, "fs_min", 0.52e6))

    def _fs_min_linear(self) -> float:
        """
        The lowest rate the sweep may PLAN, as opposed to the lowest the
        radio will accept.

        See FS_MIN_LINEAR: below 25e6/12 the AD9361 adds about 11 dB that
        full_scale_dbm does not model, so a rate chosen down there produces a
        level that is confidently wrong. Kept separate from _src_fs_min()
        because _pin_fs() must NOT be floored -- while the generator is keyed
        the transmitter owns the rate.
        """
        return max(self._src_fs_min(), FS_MIN_LINEAR)


class TxWorker(QtCore.QObject):
    """
    The signal generator's own thread.

    Deliberately separate from Worker rather than folded into it. Worker's
    loop is a stepped sweep that can spend a good fraction of a second in one
    pass at a wide span; feeding TX from inside that loop would leave the
    transmitter starved between segments, which shows up as a hole in the
    output every sweep. A transmitter has to be fed steadily or it is not a
    signal generator.

    It shares the device handle with Worker. Everything that touches the
    board goes through the source's _tx_lock, and the two data calls
    (bladerf_sync_rx over there, bladerf_sync_tx here) are the only pair that
    genuinely run at the same time -- which is exactly the arrangement
    libbladeRF documents for full duplex.
    """

    state = QtCore.pyqtSignal(dict)      # periodic status for the panel
    failed = QtCore.pyqtSignal(str)

    # One block handed to the driver per call. Big enough that Python is not
    # in the way at tens of MS/s (16k samples is 0.8 ms at 20 MS/s, so ~1200
    # calls a second at worst), small enough to stop promptly when asked.
    # 64k samples per call: 1.07 ms of air time at 61.44 MS/s, so even the
    # fastest rate needs under a thousand calls a second. Smaller blocks were
    # measured leaving only 5-11% headroom, which is not enough to survive a
    # garbage collection.
    BLOCK = 65536

    def __init__(self, src: Source, cfg: SgConfig) -> None:
        super().__init__()
        self.src = src
        self.cfg = replace(cfg)
        self._run = False
        self._lock = QtCore.QMutex()
        self._new: Optional[SgConfig] = None
        self._loop: Optional[np.ndarray] = None
        self._info: dict = {}
        self._pos = 0
        self._applied: Optional[SgConfig] = None
        self._trig_applied: Optional[Tuple[int, int]] = None
        self._keyed = False
        self._err_shown = ""
        self._written = 0
        self._ts0: Optional[int] = None
        self._t_key: Optional[float] = None
        self._clock = QtCore.QElapsedTimer()
        self._clock.start()
        self._gap = 0
        self._blocks = 0

    # -- from the GUI thread ----------------------------------------------
    def apply(self, cfg: SgConfig) -> None:
        self._lock.lock()
        self._new = replace(cfg)
        self._lock.unlock()

    @QtCore.pyqtSlot()
    def stop(self) -> None:
        self._run = False

    def set_source(self, src: Source) -> None:
        """Swap in a reopened radio. Called from the GUI thread only while
        this worker is stopped, the same way Worker's own src is replaced."""
        self.src = src
        self._applied = None
        self._trig_applied = None
        self._keyed = False

    # -- worker thread -----------------------------------------------------
    def _pull(self) -> None:
        self._lock.lock()
        new, self._new = self._new, None
        self._lock.unlock()
        if new is None:
            return
        old, self.cfg = self.cfg, new
        # Only a change to what the waveform IS costs a rebuild; level and
        # frequency are applied to the radio without touching the buffer.
        if _sg_waveform_changed(old, new):
            self._loop = None

    def _rebuild(self) -> bool:
        try:
            self._loop, self._info = build_waveform(self.cfg)
            self._pos = 0
            self.src.tx_set_loop(self._loop)
            self._err_shown = ""
            return True
        except Exception as exc:
            self._loop = None
            msg = f"waveform: {exc}"
            if msg != self._err_shown:
                self._err_shown = msg
                self.failed.emit(msg)
            return False

    def _apply_radio(self) -> None:
        """Push frequency/rate/gain to the board, only where they moved."""
        c, a = self.cfg, self._applied
        if a is None or c.channel != a.channel:
            self.src.tx_set_channel(c.channel)
            a = None                       # everything is per-channel again
        if a is None or c.fs != a.fs or c.bandwidth != a.bandwidth:
            if a is None or c.fs != a.fs:
                self.src.tx_set_sample_rate(c.fs)
            self.src.tx_set_bandwidth(c.bandwidth if c.bandwidth > 0
                                      else c.fs * 0.8)
        if a is None or c.freq != a.freq:
            self.src.tx_set_freq(c.freq)
        if a is None or c.gain != a.gain:
            self.src.tx_set_gain(c.gain)
        if a is None or c.dither != a.dither:
            # Cheap here even though it re-renders the fixed-point buffer:
            # the source re-renders from the loop it already holds, so the
            # waveform itself is not rebuilt.
            self.src.tx_set_dither(c.dither)
        if a is None or c != a:
            # Only when something actually moved. This runs once per block,
            # and a dataclass copy per block is real work at 3,750 blocks a
            # second.
            self._applied = replace(c)

    def _apply_trigger(self) -> None:
        """
        Arm or disarm the TX trigger, only on a change.

        Same ordering trap the RX side documents in Worker._ensure_trigger():
        arming gates the NEXT bladerf_enable_module(true), not a stream that
        is already flowing. So the stream has to be down while this happens,
        and run() keys it back up afterwards.
        """
        c = self.cfg
        want = (int(c.trigger_role), int(c.trigger_signal))
        if want == self._trig_applied:
            return
        prev = self._trig_applied
        try:
            self._unkey()
            if c.trigger_role == TRIG_ROLE_DISABLED:
                if prev is not None and prev[0] != TRIG_ROLE_DISABLED:
                    self.src.tx_trigger_arm(TRIG_ROLE_DISABLED)
            else:
                if prev is None or want[1] != prev[1]:
                    self.src.tx_trigger_init(c.trigger_signal)
                self.src.tx_trigger_arm(c.trigger_role)
        except RadioError as exc:
            self.failed.emit(f"TX trigger: {exc}")
        # Recorded either way: a driver that cannot do this must not be
        # asked again on every single block.
        self._trig_applied = want

    def _unkey(self) -> None:
        if self._keyed:
            try:
                self.src.tx_stop()
            except Exception:
                pass
        self._keyed = False
        self._ts0 = None
        self._t_key = None

    @QtCore.pyqtSlot()
    def run(self) -> None:
        self._run = True
        clock = self._clock
        clock.restart()
        last_status = -1.0
        while self._run:
            self._pull()
            c = self.cfg
            if not c.on:
                self._unkey()
                QtCore.QThread.msleep(60)
                now = clock.nsecsElapsed() / 1e9
                if now - last_status > 0.5:
                    last_status = now
                    self.state.emit({"on": False})
                continue

            if self._loop is None and not self._rebuild():
                self._unkey()
                QtCore.QThread.msleep(400)      # do not spin on a bad setting
                continue

            try:
                self._apply_radio()
                self._apply_trigger()
                if not self._keyed:
                    self.src.tx_start()
                    self._keyed = True
                    self._written, self._gap, self._blocks = 0, 0, 0
                    self._ts0 = self.src.tx_timestamp()
                    self._t_key = clock.nsecsElapsed() / 1e9
                self._send_continuous()
            except RadioError as exc:
                # A dead board is Worker's problem to detect and recover
                # from -- it owns the reopen. All this thread can do is stop
                # keying and say so once, rather than repeat the same
                # message every 800 microseconds.
                self._unkey()
                msg = str(exc)
                if msg != self._err_shown:
                    self._err_shown = msg
                    self.failed.emit(f"generator stopped: {msg}")
                self._applied = None
                QtCore.QThread.msleep(400)
                continue
            except Exception as exc:
                self._unkey()
                msg = str(exc)
                if msg != self._err_shown:
                    self._err_shown = msg
                    self.failed.emit(f"generator stopped: {msg}")
                QtCore.QThread.msleep(400)
                continue

            now = clock.nsecsElapsed() / 1e9
            if now - last_status > 0.5:
                last_status = now
                self._emit_state()
        self._unkey()

    def _send_continuous(self) -> None:
        """Stream the loop buffer round and round, wrapping mid-block."""
        # A full block every time. Deliberately NOT capped by the waveform's
        # own length: the source lays several laps end to end when the loop
        # is short, so it can serve a whole block from one contiguous run,
        # and capping here would throw that away and halve the send size at
        # exactly the high rates that need it most.
        # Nothing is converted or copied -- the source streams straight out
        # of the buffer it rendered when the waveform was built.
        self._pos = self.src.tx_write_loop(self._pos, self.BLOCK)
        self._written += self.BLOCK
        self._blocks += 1

    def _emit_state(self) -> None:
        """
        Status for the panel, including whether the transmitter is keeping up.

        Two checks, because the cheap one is not always available. The FPGA
        sample counter would say exactly how many samples were missed -- the
        mirror image of the RX overrun check in read_meta() -- but on this
        board it reads zero unless the stream is in a metadata format, and
        the generator's is not.

        So here is the one that always works: compare the air time of
        everything handed over against the wall-clock time it took to hand
        it over. If the host cannot keep up that ratio climbs above 1, and
        the output fills with holes -- which on a spectrum display is a
        signal blinking off and on. Measured on this host: real time up to
        about 40 MS/s, 1.03x at 45, 1.26x at 61.44, where 245 MB/s of
        samples is more than the USB link will carry. That is a link limit,
        not something software fixes, so the honest thing is to say so
        rather than let it look like a fault in the generator.
        """
        info = dict(self._info)
        fs_now = 0.0
        try:
            fs_now = float(self.src.tx_sample_rate)
        except Exception:
            pass
        ts = self.src.tx_timestamp()
        if ts is not None and self._ts0 is not None:
            behind = (ts - self._ts0) - self._written
            if behind > 0:
                self._gap = int(behind)
        realtime, sustained = 1.0, 0.0
        if self._t_key is not None and fs_now > 0 and self._written > 0:
            wall = (self._clock.nsecsElapsed() / 1e9) - self._t_key
            air = self._written / fs_now
            if air > 0.4 and wall > 0:        # ignore the first moments
                realtime = wall / air
                sustained = self._written / wall
        est = float("nan")
        try:
            est = (self.src.tx_full_scale_dbm
                   + 20.0 * np.log10(max(info.get("rms", 0.0), 1e-12))
                   + self.cfg.cal_offset)
        except Exception:
            pass
        self.state.emit({
            "on": True, "port": getattr(self.src, "tx_port", "TX1"),
            "fs_actual": fs_now, "est_dbm": est, "gap": self._gap,
            "blocks": self._blocks, "written": self._written, "info": info,
            "burst": bool(self.cfg.burst), "fs_wanted": float(self.cfg.fs),
            "realtime": realtime, "sustained": sustained,
        })


# Which SgConfig fields change the samples themselves, as opposed to just the
# radio's frequency or gain. Getting this list wrong in the "too small"
# direction would leave the old waveform playing after a parameter change --
# so anything not obviously a radio setting belongs here.
_SG_RADIO_ONLY = ("on", "channel", "freq", "gain", "cal_offset",
                  "trigger_role", "trigger_signal", "bandwidth",
                  # Derived, not built: the level solver turns these two into
                  # a gain and a backoff, and it is the backoff that rebuilds.
                  "level_direct", "level_dbm",
                  # Applied at the render step, which the source redoes from
                  # the loop it is already holding -- see tx_set_dither().
                  "dither")


def _sg_waveform_changed(a: SgConfig, b: SgConfig) -> bool:
    for f in fields(SgConfig):
        if f.name in _SG_RADIO_ONLY:
            continue
        if getattr(a, f.name) != getattr(b, f.name):
            return True
    return False


# ============================================================================
#  UI
# ============================================================================

# Six traces since 2026-08-30, and the colours are his: 1 and 2 unchanged,
# 3 and 4 replaced, 5 red and 6 blue. Blue is a royal blue rather than a
# primary one because a pure #0000ff is nearly unreadable on this
# background, and it has to stay clearly apart from trace 2's cyan.
TRACE_COLORS = ["#ffd24a",   # amber
                "#4ac6ff",   # cyan
                "#ff8a3d",   # orange
                "#35e07a",   # green
                "#ff4b4b",   # red
                "#5b8cff"]   # blue
REF_COLOR = "#ffffff"
# RX1+RX2 mode. RX1 keeps the first trace colour it already had so nothing
# changes when you are on one port; RX2 gets a colour used by no trace, and
# is drawn dashed as well, so the two are told apart by shape and not by
# colour alone.
RX1_COLOR = TRACE_COLORS[0]
RX2_COLOR = "#00e5c0"
N_DIV = 10
# Ten, on request 2026-09-01 (was four). Everything that touches a marker
# is written against this constant, so the count is genuinely one number --
# the combo, the label items, the table, the CSV and the harmonic fill all
# size themselves off it. Two things did NOT come for free and are worth
# knowing before raising it again: the peak table is a fixed height and
# scrolls past about six rows, and only the SELECTED marker draws a full
# readout on the plot (see _draw_markers) -- ten full labels would be an
# unreadable heap, which is a display limit rather than an arithmetic one.
N_MARKERS = 10
# How many channel powers can be measured at once. Asked for on 2026-09-14:
# "There's only one Ch Power measurement option. Can you add an option to
# measure 10 channel powers simultaneously?" Ten rather than an unbounded
# list, for the same two reasons N_MARKERS is ten: the results table has to
# stay a fixed height or the panel grows past the do-not-cover-the-trace
# ceiling in _place_panel, and ten shaded bands is already about as many as
# one graticule can carry before they stop being tellable apart.
N_CHP = 10
# How many preset SLOTS sit on the front panel as keys of their own. Asked
# for on 2026-09-17: "I want users to be able to perform configurations,
# then save them as preset windows so they can import and export. That way,
# when the program opens, they have (let's say 5 for now) buttons where they
# can press and toggle between spectrum configurations." Five is his number.
#
# They are a different thing from the NAMED setups in the Setups panel,
# which are an unbounded list you pick out of a combo. A slot is a KEY: one
# press recalls it, and what a bench instrument's numbered save/recall keys
# buy is that switching between two configurations is one press rather than
# open-panel, pick-name, press-Recall. The named list is still there and
# both are written by the same _gather_setup(), so neither can drift from
# the other.
#
# 5 -> 6 on 2026-09-24, 6 -> 10 on 2026-11-22 (Kfir: "add 10 slots to the
# preset keys"). A slots file written with fewer is padded with empty slots
# on load (_slots_parse), so nobody's stored presets move or vanish.
N_PRESETS = 10
# What a slot key says about itself. One constant rather than one f-string
# per key: _sync_preset_keys() re-states the tooltip every time a slot is
# stored, renamed or cleared (so it can put the stored NAME at the top of
# it), and a second copy of this text would drift from the first.
PRESET_KEY_TIP = (
    "Recall this preset: the whole instrument goes back to the\n"
    "settings stored in the slot -- frequency, span, RBW,\n"
    "amplitude, traces, measurements, the port and the generator.\n\n"
    "The key stays lit while the measurement still matches what\n"
    "was recalled, so toggling between two slots shows which one\n"
    "you are on. The lamp goes out as soon as you change the\n"
    "measurement.\n\n"
    "An empty slot does nothing but say so. Store, rename, clear,\n"
    "import and export are all in the Setups panel.\n\n"
    "Recalling never keys the transmitter: it goes down exactly\n"
    "the same path a setup recalled by name does.")
# What goes in a slots file, and the only value of "kind" this will read.
# The file the application keeps and the file Export writes are the SAME
# format, deliberately -- so a slots.json can be handed to somebody
# directly, and an exported file can be dropped into the settings folder by
# hand. A version number because a format that cannot say what it is leaves
# the next change with no way to refuse an older file politely.
PRESET_FILE_KIND = "preset-slots"
PRESET_FILE_VERSION = 1

# The macro (2026-09-23): a list of steps -- settings / wait / capture --
# run top to bottom, MACRO_PASSES_MAX times at most. Kfir's example is a site
# procedure: set a screen, wait 10 s, save PNG + CSV as 1_750_MH, change the
# detector, wait, save as 1_750_RMS, next frequency... See _macro_run.
# Called the SEQUENCER on screen since 2026-09-26 (Kfir's pick). The code
# keeps the word "macro" in its own names -- renaming ~250 identifiers buys
# nothing a user can see -- but every string a user reads says Sequencer.
# Files written before the rename say "macro", so both kinds are read; the
# kept copy in the settings folder is still macro.json so the steps somebody
# was building survive the upgrade.
MACRO_FILE_KIND = "sequence"
MACRO_FILE_KINDS_READ = ("sequence", "macro")
MACRO_FILE_VERSION = 1
MACRO_DIRNAME = "Sequences"
# A run records itself (Kfir, 2026-11-22: "when the sequencer has done
# performing I want you to save that recording"): the ordinary recorder,
# started with the run into this subfolder of the run's own folder, and
# stopped and saved when the run finishes or is stopped -- so the captures
# and the recording of how the screen got there sit side by side.
MACRO_REC_DIRNAME = "Recording"
MACRO_NAME = "Sequence"                # a new sequence's name, and the fallback
MACRO_TICK_MS = 100
MACRO_PASSES_MAX = 999
MACRO_DEFAULT_NAME = "{pass}_{center_mhz}"
# The selected step's marker in the list. Written as an escape so the source
# stays plain ASCII; the unselected prefix is spaces of about the same width
# so the numbers do not jump sideways when the selection moves.
MACRO_ARROW = "▶ "
MACRO_NO_ARROW = "    "
# What a capture name may say. Anything else in braces is left as typed, so
# a typo shows up in the file name instead of stopping a run on site.
MACRO_FIELDS = ("pass", "n", "center_mhz", "span_mhz", "rbw_khz",
                "detector", "date", "time")
# Marker glyph size in pixels. Diamond rather than a round dot (2026-08-31,
# on request) -- see the ScatterPlotItem in _build_plots() for why the size
# went up at the same time as the shape changed.
MARKER_SIZE = 13

LUT_STOPS = [(0.00, (0, 0, 12)), (0.18, (20, 16, 90)), (0.38, (75, 20, 130)),
             (0.56, (150, 40, 110)), (0.72, (215, 75, 60)),
             (0.86, (245, 150, 30)), (1.00, (255, 245, 200))]


def build_lut(n: int = 256) -> np.ndarray:
    pos = np.array([s[0] for s in LUT_STOPS])
    col = np.array([s[1] for s in LUT_STOPS], dtype=float)
    x = np.linspace(0, 1, n)
    lut = np.empty((n, 3), dtype=np.uint8)
    for ch in range(3):
        lut[:, ch] = np.clip(np.interp(x, pos, col[:, ch]), 0, 255)
    return lut


class HzAxis(pg.AxisItem):
    """
    Frequency ticks whose precision follows the tick spacing.

    A fixed 4 significant figures made every label read "2.45 GHz" once the
    span dropped below a few MHz, which is useless exactly when you are zoomed
    in on something. Deriving the decimals from the spacing keeps neighbouring
    ticks distinguishable at any span.
    """

    def tickStrings(self, values, scale, spacing):
        if not values:
            return []
        mag = max(abs(v) for v in values)
        unit, div = ("GHz", 1e9) if mag >= 1e9 else \
                    ("MHz", 1e6) if mag >= 1e6 else \
                    ("kHz", 1e3) if mag >= 1e3 else ("Hz", 1.0)
        step = abs(spacing) / div if spacing else 0.0
        if step > 0:
            dec = int(np.ceil(-np.log10(step))) + 1
        else:
            dec = 3
        dec = int(np.clip(dec, 0, 9))
        return [f"{v / div:.{dec}f} {unit}" for v in values]


class SecAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):
        out = []
        for v in values:
            a = abs(v)
            if a >= 1:
                out.append(f"{v:.4g} s")
            elif a >= 1e-3:
                out.append(f"{v*1e3:.4g} ms")
            elif a >= 1e-6:
                out.append(f"{v*1e6:.4g} us")
            else:
                out.append(f"{v*1e9:.4g} ns")
        return out


class SpectrumPlot(pg.PlotWidget):
    """
    Plot area that reports wheel and drag as tuning intents.

    pyqtgraph's own mouse handling is left switched off: it would rescale the
    view without telling the radio, so the picture and the actual sweep would
    drift apart. Instead the gestures are turned into span/centre changes and
    the sweep is retuned properly.
    """

    zoom = QtCore.pyqtSignal(float, float)   # factor, frequency under cursor
    pan = QtCore.pyqtSignal(float)           # shift in Hz
    markerAt = QtCore.pyqtSignal(float)      # put the selected marker here

    def __init__(self, **kw):
        super().__init__(**kw)
        self._drag_from = None
        self._mark_drag = False
        # Set by the "Move with mouse" tick in the Markers panel. Ctrl
        # INVERTS it, whichever way round it is, so both gestures are always
        # available and neither has to be hunted for: with the tick off,
        # Ctrl-drag moves the marker; with it on, Ctrl-drag pans.
        self.marker_mouse = False

    def _x_at(self, pos) -> Optional[float]:
        vb = self.getPlotItem().vb
        if not self.sceneBoundingRect().contains(pos):
            return None
        return float(vb.mapSceneToView(pos).x())

    def wheelEvent(self, ev):
        x = self._x_at(ev.pos())
        if x is None:
            return
        steps = ev.angleDelta().y() / 120.0
        if steps:
            # 0.8 per notch: a few clicks move an order of magnitude, which is
            # about right for hunting across a band.
            self.zoom.emit(0.8 ** steps, x)
        ev.accept()

    def _marker_gesture(self, ev) -> bool:
        """Marker mode XOR Ctrl. See marker_mouse."""
        ctrl = bool(ev.modifiers() & QtCore.Qt.ControlModifier)
        return bool(self.marker_mouse) != ctrl

    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            x = self._x_at(ev.pos())
            if x is not None:
                if self._marker_gesture(ev):
                    # The marker follows the cursor from the press onwards,
                    # so a single click places it and a drag slides it --
                    # which is what "move the marker, like zooming with the
                    # cursor" means. It is only honest because a marker is
                    # anchored to a FREQUENCY (bug 38): dropping one at a
                    # screen position would have it slide off at the next
                    # zoom.
                    self._mark_drag = True
                    self.setCursor(QtCore.Qt.SizeHorCursor)
                    self.markerAt.emit(x)
                else:
                    self._drag_from = x
                    self.setCursor(QtCore.Qt.ClosedHandCursor)
                ev.accept()
                return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._mark_drag:
            x = self._x_at(ev.pos())
            if x is not None:
                self.markerAt.emit(x)
            ev.accept()
            return
        if self._drag_from is not None:
            x = self._x_at(ev.pos())
            if x is not None:
                # Drag right = look at lower frequencies, like dragging paper.
                self.pan.emit(self._drag_from - x)
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._mark_drag or self._drag_from is not None:
            self._mark_drag = False
            self._drag_from = None
            self.unsetCursor()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)


class HzEdit(QtWidgets.QLineEdit):
    changed = QtCore.pyqtSignal(float)

    def __init__(self, v: float) -> None:
        super().__init__()
        self._v = float(v)
        self.setAlignment(QtCore.Qt.AlignRight)
        self.setMaximumWidth(148)
        self._show()
        self.editingFinished.connect(self._commit)

    def value(self) -> float:
        return self._v

    def setValue(self, v: float) -> None:
        self._v = float(v)
        self._show()

    def _show(self) -> None:
        self.setText(fmt_hz(self._v))

    def _commit(self) -> None:
        p = parse_hz(self.text())
        if p is None or abs(p - self._v) < 1e-6:
            self._show()
            return
        self._v = p
        self._show()
        self.changed.emit(p)


def rule() -> QtWidgets.QFrame:
    """A thin divider, for grouping controls inside one box."""
    ln = QtWidgets.QFrame()
    ln.setFrameShape(QtWidgets.QFrame.HLine)
    ln.setStyleSheet("color:#2b3a47;")
    return ln


PANEL_BG = "#12161a"

# Accent per family of controls. Cool colours are receive and analyse, the
# one warm colour is transmit -- so the eye can tell at a glance whether a
# box measures something or emits it, which is the same split section 5.5
# had to learn the hard way. Colours are taken from the trace palette so
# the panel and the plot look like one instrument.
A_RADIO   = "#4ac6ff"      # what the radio is doing: source, sweep, health
A_TUNE    = "#ffd24a"      # where to look: frequency, bandwidth, trigger
A_VIEW    = "#b79cff"      # how it is drawn: amplitude, traces, display
A_MEASURE = "#7CFF6B"      # numbers out: markers, counter, power, limits
A_FILE    = "#8fa6bb"      # housekeeping: capture, setups -- deliberately
                           # the quietest colour, it is not a measurement
A_TX      = "#ff7a5c"      # transmit. Warm on purpose, see above.

# The graticule. Kfir asked for it whiter, "like a real SA", with a photo of
# one -- and he is right that it was too faint to read a level off: it was
# the axis colour (#3a4854) at 0.28 alpha, which lands at about #10141a on
# the near-black plot background. That is barely a line.
#
# It has to be set in TWO places or it does not change, and that is worth
# knowing before touching one of them: pyqtgraph draws its grid with the
# AXIS pen at the grid alpha, so the colour comes from _theme()'s
# setPen(GRID_PEN) and only the strength comes from showGrid(alpha=...).
# Raising the alpha alone just makes a dim blue-grey slightly less dim.
#
# GRID_PEN is also the frame and the tick marks, so it is a light neutral
# rather than a tinted one -- a bench analyzer's graticule is grey, and a
# blue-tinted grid under a yellow trace reads as a colour cast.
# Where the Identify-signal verdict hangs, as a fraction of the plot
# height below the top of the view, and how much bigger than the app font
# it is drawn. Both were asked for on 2026-08-30: at 0.02 and the plain
# font it read as part of the window furniture rather than as a reading
# about the carrier under it. 0.30 puts it in the clear space above a
# trace at a sensible reference level, which is where the eye already is,
# and still leaves the whole lower two thirds of the graticule untouched.
CLS_LABEL_DROP = 0.30
CLS_LABEL_PT = 1.5
GRID_PEN = "#b4c0cc"       # axis line, ticks, and what the grid is drawn in
GRID_ALPHA = 0.55          # ~#5c6570 on black: visible, still behind the trace
AXIS_TEXT = "#c3d1de"      # tick labels, lifted to match the brighter frame


def mix_hex(a: str, b: str, t: float) -> str:
    """Blend colour a towards b by t (0 = all a, 1 = all b)."""
    ca = tuple(int(a.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    cb = tuple(int(b.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    return "#" + "".join(f"{int(round(x + (y - x) * t)):02x}"
                         for x, y in zip(ca, cb))


def vgrad(top: str, bottom: str) -> str:
    """A vertical two-stop gradient, as a Qt stylesheet value.

    Qt stylesheets have no box-shadow, so depth has to come from a gradient
    plus per-side border colours. Top lighter than bottom reads as lit from
    above, which is the convention every physical instrument's keys follow;
    swapping them is what makes a pressed key look pressed.
    """
    return (f"qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            f"stop:0 {top},stop:1 {bottom})")


def key_qss(accent: str, radius: int = 4, bold: bool = True) -> str:
    """
    The instrument-key look, for one accent colour.

    ONE function, used by the soft keys, by Preset and by RF OUT, so those
    three cannot drift apart -- they were three hand-written stylesheets
    before and had three different border radii between them.

    What each state has to say, because a key that does not say it is a key
    the operator has to guess at:
      normal   faintly raised, accent-coloured text on a near-black face
      hover    the same key, lit a step -- "this is what you are about to
               press", nothing more
      pressed  gradient inverted, border darkened: recessed
      checked  the panel behind this key is OPEN. Accent border at 2px and a
               brighter inner edge stand in for a glow, since Qt stylesheets
               cannot draw one
      disabled flat and desaturated, no gradient at all, so "cannot" never
               looks like "off"

    Deliberately NO font-size here. A stylesheet rule beats setFont(), and
    SoftKey shrinks its own font at runtime to fit a long label -- putting a
    size back in this string is what made that shrink silently inert once
    before (5.13).
    """
    face_t = mix_hex(accent, PANEL_BG, 0.86)
    face_b = mix_hex(accent, PANEL_BG, 0.95)
    edge = mix_hex(accent, PANEL_BG, 0.62)
    edge_lo = mix_hex(accent, PANEL_BG, 0.80)
    hot_t = mix_hex(accent, PANEL_BG, 0.76)
    hot_b = mix_hex(accent, PANEL_BG, 0.88)
    on_t = mix_hex(accent, PANEL_BG, 0.44)
    on_b = mix_hex(accent, PANEL_BG, 0.62)
    weight = "bold" if bold else "normal"
    return (
        "QPushButton{"
        f"color:{accent};font-weight:{weight};"
        f"border:1px solid {edge};border-bottom-color:{edge_lo};"
        f"border-radius:{radius}px;background:{vgrad(face_t, face_b)};"
        "}"
        "QPushButton:hover{"
        f"background:{vgrad(hot_t, hot_b)};border-color:{accent};"
        "}"
        "QPushButton:pressed{"
        f"background:{vgrad(face_b, face_t)};border-color:{edge_lo};"
        "}"
        "QPushButton:checked{"
        f"background:{vgrad(on_t, on_b)};color:#ffffff;"
        f"border:2px solid {accent};"
        "}"
        "QPushButton:checked:hover{"
        f"background:{vgrad(mix_hex(accent, PANEL_BG, 0.34), on_t)};"
        "}"
        "QPushButton:disabled{"
        f"color:{mix_hex(accent, PANEL_BG, 0.55)};"
        f"background:{mix_hex(accent, PANEL_BG, 0.94)};"
        f"border:1px solid {mix_hex(accent, PANEL_BG, 0.86)};"
        "}")


class Box(QtWidgets.QGroupBox):
    """
    One group of controls, framed and colour-coded by what it does.

    The colour is not decoration, and it matters MORE since the boxes moved
    into popovers behind soft keys: with only one group on screen at a time
    the accent is what says which family you are in, and it matches the key
    you pressed to get there. It started as a fix for a different problem
    -- nineteen identical grey frames down one 2800 px column were tiring
    to scan -- and it survived the layout that caused it.
    """

    def __init__(self, title: str, accent: str = A_FILE) -> None:
        super().__init__(title)
        self.accent = accent
        edge = mix_hex(accent, PANEL_BG, 0.58)     # frame: accent, subdued
        tint = mix_hex(accent, PANEL_BG, 0.94)     # fill: barely a wash
        self.setStyleSheet(
            "QGroupBox{"
            f"border:1px solid {edge};"
            f"border-left:3px solid {accent};"
            "border-radius:7px;margin-top:10px;padding-top:8px;"
            f"background:{tint};font-weight:bold;color:{accent};"
            "}"
            "QGroupBox::title{subcontrol-origin:margin;"
            "subcontrol-position:top left;left:9px;padding:1px 6px;"
            f"background:{PANEL_BG};border-radius:4px;color:{accent};"
            "}")
        self.f = QtWidgets.QFormLayout(self)
        self.f.setContentsMargins(9, 8, 9, 9)
        self.f.setSpacing(5)
        self.f.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.f.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        # Stack a label above its field rather than force the whole panel
        # wider when the pair will not fit. Without this the right-hand panel
        # wanted 541 px inside a 304 px viewport and grew a HORIZONTAL
        # scrollbar, which is a miserable thing to have on a control panel
        # and a good part of why it felt cluttered.
        self.f.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)

    def row(self, label: str, w):
        # The label is kept on the widget so a row can be hidden whole. A
        # QFormLayout leaves the label behind if you only hide the field,
        # and Qt 5 has no setRowVisible() to do it for us.
        lab = QtWidgets.QLabel(label)
        self.f.addRow(lab, w)
        w._row_label = lab
        return w

    def full(self, w):
        self.f.addRow(w)
        return w


# Key metrics and the three-across grid they sit in. Sized from the
# reference image: near-square keys in a block, not a stretched row.
KEY_W, KEY_H, KEY_COLS = 78, 40, 3
# Extra space above the SIGNAL GENERATOR block, on top of the ordinary gap
# between blocks. Asked for 2026-08-30: "take all the Signal Generator view
# a bit down, so it'll be separated from the SAs. But not too much down."
# Colour already separates transmit from receive (section 5.5); this gives
# the eye a gap to go with it. Deliberately modest -- pushing it to the
# bottom of the column would cost the association with the keys above it.
SG_BLOCK_GAP = 16

# What counts as too much power into an RX port, and how far it has to fall
# before the warning re-arms. Asked for 2026-08-30: "add a damage hazard
# popup, in case the Rx ports receive power greater than -10dBm ... I just
# want the user to be aware not to inject high signals."
#
# -10 dBm is Kfir's number and it is a sensible working limit for the
# AD9361 front end -- the absolute maximum is higher, but the point of a
# warning is to fire before the damage, not with it.
#
# The hysteresis is not decoration. Without it a signal sitting on the
# threshold re-triggers the popup on every sweep, which is 20-30 times a
# second, and a warning that cannot be dismissed is a warning people learn
# to work around.
# The RX analog channel filter, as a fraction of the sample rate. This is
# the OTHER half of the span-dependent level error of 2026-09-01, and the
# half that cannot be finished without the board.
#
# A stitched sweep keeps the middle 0.75*fs of every segment
# (Source.usable_bandwidth), so the kept band reaches +-0.375*fs. At the old
# 0.8 the filter's own -3 dB corner was at +-0.4*fs -- so a signal landing
# near a segment edge sat at 94% of the corner and read LOW, by roughly 2 dB
# for a third-order response, while the same signal at a narrow span sits at
# the segment centre and reads true. That is exactly the shape of what Kfir
# saw: the level depends on where in its segment a signal happens to fall,
# and that depends on the span.
#
# 0.9 moves the corner out to +-0.45*fs, so the kept edge is at 83% of it
# instead of 94%. It cannot go much past 1.0: the corner has to stay at or
# inside Nyquist or energy above fs/2 folds back into the top of the band,
# and the AD9361's digital decimation chain -- not this filter -- is what
# does the sharp anti-aliasing.
#
# NOT hardware-verified: the board was absent when this was written. It is a
# named constant so it is one edit to put back, and the real fix is a
# measured flatness correction per rate, which needs a bench pass.
RX_BW_FRAC = 0.9

# The lowest sample rate at which a level means anything.
#
# MEASURED on the board 2026-09-16, against a signal generator injecting a
# known -40.2 dBm, and it is the missing two thirds of bug 41. The SAME tone,
# the same gain, the same offset from the LO, with only the sample rate
# changing:
#
#     fs <= 2.083333 MS/s   tone reads  -2.4 dBFS
#     fs >  2.083333 MS/s   tone reads -13.6 dBFS   ... and stays there, flat
#                                                       all the way to 40 MS/s
#
# An 11.2 dB step, at a boundary that is exactly 25e6/12 -- measured at
# 2.083299 (step present) and 2.083999 (step gone), so it is the AD9361
# bringing in another decimation stage and not anything of ours. It holds at
# every gain that is not compressed: +10.48, +10.89 and +10.88 dB at 10, 20
# and 30 dB.
#
# full_scale_dbm is _fs0 - gain and knows nothing about the sample rate, so
# every level below that boundary read about 11 dB HIGH. The sweep plans its
# rate from the span (span / 0.75), so any span under roughly 1.6 MHz landed
# there -- which is to say, zooming in on a carrier. That is exactly what
# Kfir reported on 2026-09-01: "In large spans I read -50dBm, but zooming in,
# the power increases to more than -40dBm". Bug 41 found 0.5 dB of scallop
# loss, fixed it, and said plainly that the rest "is not reproducible in
# software". It is not a software effect; it needed the board.
#
# The fix is to stay out of that regime rather than to correct for it. A
# correction would bake a bench number with 0.7 dB of spread into every
# level, and the exact step may well move with the FIR configuration. Staying
# above the boundary costs a longer FFT at a narrow span -- the same RBW at a
# higher rate -- which is a few milliseconds of numpy on spans that sweep in
# one segment anyway.
#
# NOT applied when the generator has pinned the rate (5.5 fact 1): the
# transmitter owns it there, and raising it would drag TX off the rate its
# waveform was built for, which is a worse fault than the one being fixed.
# That case is reported instead -- see Result.fs_low.
FS_MIN_LINEAR = 25e6 / 12.0

RX_DAMAGE_DBM = -10.0
RX_DAMAGE_CLEAR_DB = 3.0


def rx_hazard(level_dbm: float, armed: bool, live: bool = True,
              loopback: bool = False):
    """
    Should the overpower warning fire? Returns (verdict, still_armed).

    verdict is "" (nothing to say), "note" (over the limit, but the dialog
    has already been shown and dismissed) or "warn" (over the limit and the
    dialog is owed).

    Pure, and module-level, so the whole decision can be tested without a
    window -- the same reason classify_span() is pure. It has to be, as it
    turns out: QMessageBox.show() under the Qt OFFSCREEN platform is an
    access violation, not an exception, exactly like the exec_() trap in
    5.9. So no test can drive the real dialog, and the logic would have gone
    unguarded if it lived inside the method that raises it.

    `level_dbm` is the level at the CONNECTOR, not the level on screen --
    see Analyzer._rx_hazard_dbm.

    The hysteresis governs the DIALOG only, never the readout. The readout
    answers "is the input over the limit right now", which is a question
    about the present, and holding it up while the level is back under would
    be a small lie of exactly the kind this file keeps finding.
    """
    if not live or loopback or not np.isfinite(level_dbm):
        return "", armed
    if level_dbm >= RX_DAMAGE_DBM:
        return ("warn" if armed else "note"), False
    if level_dbm < RX_DAMAGE_DBM - RX_DAMAGE_CLEAR_DB:
        return "", True
    # Inside the band: under the limit, so nothing to show, but not yet far
    # enough clear to re-arm. Without this a signal parked on the limit
    # reopens the dialog on every sweep -- 20-30 times a second.
    return "", armed


# ------------------------------------------------------------------ xlsx
# A saved measurement is written twice since 2026-09-07, and the second copy
# exists for one reason: a .csv is TEXT and cannot carry a column width, a
# fill or a border. Kfir asked for the saved file to be "more comfortable for
# the eye", with a screenshot of what he had done by hand in Excel -- column E
# widened until the labels read in full, and the metadata block framed and
# shaded. None of that survives in a csv, and Excel re-guesses every column
# width on open, so the only way to deliver the picture is a real workbook.
#
# It is hand-rolled with the standard library's own zipfile, deliberately:
# openpyxl or xlsxwriter would be a fourth runtime dependency on a program
# whose whole shape is one file that bootstraps three, and section 2's rule
# about Pillow -- a build-time dependency the application must never acquire
# -- is the same argument. An xlsx is a zip of XML, and enough of it for
# numbers, titles, widths, a fill and a border fits in about 120 lines.
XLSX_FILL = "FFCCFFFF"      # the pale cyan of his screenshot
XLSX_LINE = "FF3D8A8A"      # the frame around the block, a shade darker
XLSX_PAD = 2                # characters of air beyond the widest cell
XLSX_COL_MAX = 46           # a width past which a column stops being read
XLSX_SHEET = "Spectrum"


def _xlsx_col(i: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA. Spreadsheet column letters."""
    out = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(65 + r) + out
    return out


def _xq(t: str) -> str:
    """XML text escape. & first, or the escapes escape each other."""
    return (str(t).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _xlsx_num(t: str) -> bool:
    """Is this string something Excel should hold as a NUMBER?

    The data columns are formatted to a fixed number of decimals before they
    reach either writer, so the csv and the workbook carry the same digits.
    What the workbook adds is that they go in as numbers rather than as text,
    or a saved trace could not be charted or averaged in the spreadsheet --
    which is most of what somebody opens one for. "nan" and "inf" are real
    possibilities in a level column and are NOT numbers to Excel.
    """
    try:
        v = float(t)
    except (TypeError, ValueError):
        return False
    return v == v and abs(v) != float("inf")


def write_xlsx(path: str, head: List[str], data: List[List[str]],
               meta: List[Tuple[str, str]], mcol: int) -> None:
    """Write the same table the csv gets, laid out for a reader.

    `head` is the title row, `data` the formatted numbers under it, `meta`
    the (label, value) provenance block and `mcol` the 0-based column it
    starts in -- the same value the csv uses, so the two files put the block
    in the same place and a screenshot of one describes the other.

    Three things are done that a csv cannot do, and they are the whole
    request: every column is widened to its own widest cell (capped, since a
    column wider than the screen is not more readable than a narrow one), the
    title row is bold and frozen so it stays on screen while the trace is
    scrolled, and the metadata block is filled and boxed.

    Raises on failure like any other write; the caller reports it.
    """
    import zipfile

    ncol = mcol + 2
    for r in data:
        ncol = max(ncol, len(r))
    nrow = max(len(data) + 1, len(meta))

    # Style indices, matching the cellXfs order built below.
    S_TITLE, S_FREQ, S_LEVEL, S_LABEL, S_VALUE, S_TEXT = 1, 2, 3, 4, 5, 6

    # Widths come off the content rather than being guessed, which is the
    # half of this he asked for by name ("look at how I expanded the
    # columns"). The metadata pair is measured against its own text, so a
    # long build string widens F and not the level column beside it.
    wide = [0] * ncol
    for c, t in enumerate(head):
        wide[c] = max(wide[c], len(t))
    for r in data:
        for c, t in enumerate(r):
            wide[c] = max(wide[c], len(t))
    if meta:
        wide[mcol] = max(wide[mcol], max(len(m[0]) for m in meta))
        wide[mcol + 1] = max(wide[mcol + 1], max(len(m[1]) for m in meta))

    cols = "".join(
        '<col min="%d" max="%d" width="%.2f" customWidth="1"/>'
        % (c + 1, c + 1, min(XLSX_COL_MAX, wide[c] + XLSX_PAD))
        for c in range(ncol) if wide[c])

    def cell(c, r, text, style):
        ref = "%s%d" % (_xlsx_col(c), r)
        if _xlsx_num(text):
            return '<c r="%s" s="%d"><v>%s</v></c>' % (ref, style, text)
        # A non-number in a level column must not carry that column's
        # decimal format, or Excel shows a formatted blank for "nan".
        return ('<c r="%s" s="%d" t="inlineStr"><is><t>%s</t></is></c>'
                % (ref, S_TEXT if style in (S_FREQ, S_LEVEL) else style,
                   _xq(text)))

    # Assembled per row so the metadata lands beside the trace exactly as it
    # does in the csv -- one blank column between them, and the block moving
    # right rather than over the data when the traces are wide.
    body = []
    for r in range(1, nrow + 1):
        cs = []
        if r == 1:
            cs += [cell(c, 1, t, S_TITLE) for c, t in enumerate(head)]
        elif r - 2 < len(data):
            cs += [cell(c, r, t, S_FREQ if c == 0 else S_LEVEL)
                   for c, t in enumerate(data[r - 2])]
        if r - 1 < len(meta):
            label, value = meta[r - 1]
            cs.append(cell(mcol, r, label, S_LABEL))
            cs.append(cell(mcol + 1, r, value, S_VALUE))
        if cs:
            body.append('<row r="%d">%s</row>' % (r, "".join(cs)))

    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"><sheetViews><sheetView tabSelected="1"'
        ' workbookViewId="0"><pane ySplit="1" topLeftCell="A2"'
        ' activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        + ("<cols>%s</cols>" % cols if cols else "")
        + "<sheetData>" + "".join(body) + "</sheetData></worksheet>")

    # fills 0 and 1 are not decoration and must not be dropped: Excel
    # requires "none" and "gray125" in those two slots, and REPAIRS a
    # workbook that omits them -- which means rewriting it, losing every
    # width and fill this function exists to set.
    edge = ('<left style="thin"><color rgb="%(c)s"/></left>'
            '<right style="thin"><color rgb="%(c)s"/></right>'
            '<top style="thin"><color rgb="%(c)s"/></top>'
            '<bottom style="thin"><color rgb="%(c)s"/></bottom>'
            % {"c": XLSX_LINE})
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main">'
        '<numFmts count="2">'
        '<numFmt numFmtId="164" formatCode="0.000000"/>'
        '<numFmt numFmtId="165" formatCode="0.000"/></numFmts>'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="' + XLSX_FILL +
        '"/><bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="2"><border/><border>' + edge + '</border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0"'
        ' borderId="0"/></cellStyleXfs>'
        '<cellXfs count="7">'
        '<xf xfId="0" numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
        '<xf xfId="0" numFmtId="0" fontId="1" fillId="0" borderId="0"'
        ' applyFont="1"/>'
        '<xf xfId="0" numFmtId="164" fontId="0" fillId="0" borderId="0"'
        ' applyNumberFormat="1"/>'
        '<xf xfId="0" numFmtId="165" fontId="0" fillId="0" borderId="0"'
        ' applyNumberFormat="1"/>'
        '<xf xfId="0" numFmtId="0" fontId="1" fillId="2" borderId="1"'
        ' applyFont="1" applyFill="1" applyBorder="1"/>'
        '<xf xfId="0" numFmtId="0" fontId="0" fillId="2" borderId="1"'
        ' applyFill="1" applyBorder="1"/>'
        '<xf xfId="0" numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
        '</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0"'
        ' builtinId="0"/></cellStyles></styleSheet>')

    parts = {
        "[Content_Types].xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
            'content-types"><Default Extension="rels" ContentType='
            '"application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType='
            '"application/vnd.openxmlformats-officedocument.spreadsheetml.'
            'worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType='
            '"application/vnd.openxmlformats-officedocument.spreadsheetml.'
            'styles+xml"/></Types>',
        "_rels/.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
            '2006/relationships"><Relationship Id="rId1" Type="http://'
            'schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main" xmlns:r="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="' + _xq(XLSX_SHEET) + '" sheetId="1"'
            ' r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
            '2006/relationships"><Relationship Id="rId1" Type="http://'
            'schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'worksheet" Target="worksheets/sheet1.xml"/><Relationship '
            'Id="rId2" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '</Relationships>',
        "xl/styles.xml": styles,
        "xl/worksheets/sheet1.xml": sheet,
    }
    # Written to a temp name and moved into place, so a half-written workbook
    # never sits where a finished one is expected -- a full-span sweep is
    # 13,000 rows and this is not instant.
    tmp = path + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts.items():
            z.writestr(name, text)
    os.replace(tmp, path)


def reveal_folder(path: str) -> None:
    """Open a folder in Explorer for the user.

    A module-level function rather than an os.startfile inline, so the test
    loader can swap it for a recorder: every GUI walk presses every key, and
    a suite that popped an Explorer window per Record press would be
    unusable on a desktop.
    """
    os.startfile(path)                                     # noqa: S606


# -- the macro's pure half: the file format, names, the step list's text ----
# No Qt here, so it is tested straight, the way rx_hazard() is.

def _num(x: float) -> str:
    """750.0 -> "750", 2440.5 -> "2440.5": a number as a person types it."""
    s = f"{float(x):.6f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def macro_parse(d):
    """Read a macro document, or say why not: (macro, reason).

    A step that cannot be understood REFUSES the whole file, naming the step,
    rather than being dropped: on site a dropped step is a capture that is
    silently missing from the customer's report, found a week later.
    """
    if not isinstance(d, dict):
        return None, "it is not a sequence file"
    if d.get("kind") not in MACRO_FILE_KINDS_READ:
        return None, f"it is not a {APP_NAME} sequence file"
    try:
        ver = int(d.get("version", 1))
    except (TypeError, ValueError):
        ver = 1
    if ver > MACRO_FILE_VERSION:
        return None, (f"it was written by a newer version (format {ver}, "
                      f"this one reads {MACRO_FILE_VERSION})")
    raw = d.get("steps")
    if not isinstance(raw, list):
        return None, "it carries no steps"
    steps = []
    for k, e in enumerate(raw, 1):
        kind = e.get("kind") if isinstance(e, dict) else None
        if kind == "settings" and isinstance(e.get("setup"), dict):
            steps.append({"kind": "settings", "setup": e["setup"]})
        elif kind == "wait":
            try:
                v = float(e.get("value"))
            except (TypeError, ValueError):
                v = -1.0
            unit = e.get("unit", "seconds")
            if not (v > 0 and np.isfinite(v)) or unit not in ("seconds",
                                                               "sweeps"):
                return None, f"step {k} is a wait with no usable length"
            steps.append({"kind": "wait", "value": v, "unit": unit})
        elif kind == "capture":
            steps.append({"kind": "capture",
                          "name": str(e.get("name") or MACRO_DEFAULT_NAME),
                          "png": bool(e.get("png", True)),
                          "csv": bool(e.get("csv", True))})
        else:
            return None, f"step {k} is not a step this version understands"
    try:
        passes = int(d.get("passes", 1))
    except (TypeError, ValueError):
        passes = 1
    return {"name": str(d.get("name") or MACRO_NAME),
            "passes": int(np.clip(passes, 1, MACRO_PASSES_MAX)),
            "steps": steps}, ""


def macro_name(template: str, fields: dict) -> str:
    """A capture's file name (no extension) from its template.

    Known {fields} are filled; anything else in braces is left as typed, so
    a typo lands in the file name where it will be noticed rather than
    stopping a run on site. Characters Windows refuses become "_".
    """
    def sub(mo):
        k = mo.group(1)
        return str(fields[k]) if k in fields else mo.group(0)
    s = re.sub(r"\{(\w+)\}", sub, str(template))
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s).strip().rstrip(".")
    return s or f"capture {fields.get('n', 1)}"


def _macro_brief(d: dict) -> Dict[str, str]:
    """The handful of settings a person thinks of as "the screen"."""
    out = {"Center": fmt_hz_short(float(d.get("center", 0.0))),
           "Span": ("zero span" if d.get("zero_span")
                    else fmt_hz_short(float(d.get("span", 0.0)))),
           "RBW": ("auto" if d.get("rbw_auto", True)
                   else fmt_hz_short(float(d.get("rbw", 0.0)))),
           "Detector": str(d.get("detector", "Peak")),
           "Points": str(int(d.get("points", 1400)))}
    modes = [m for m in d.get("trace_modes", []) or []]
    on = [f"T{i + 1} {m}" for i, m in enumerate(modes) if m != "Off"]
    out["Traces"] = ", ".join(on) if on else "all off"
    if "Average" in modes:
        out["Averages"] = str(int(d.get("avg_count", 10)))
    return out


def macro_describe(steps) -> List[str]:
    """One line per step, for the list in the panel.

    A settings step is described by what it CHANGES from the settings step
    before it -- which is how Kfir's own procedure is written ("New
    Configuration: Center frequency 850 MHz") -- and the first one by the
    whole screen. Everything else it carries is counted, not listed.
    """
    lines, prev, prev_full = [], None, None
    for k, s in enumerate(steps, 1):
        kind = s.get("kind")
        if kind == "settings":
            d = s.get("setup", {})
            now = _macro_brief(d)
            if prev is None:
                txt = ", ".join(f"{a} {b}" for a, b in now.items())
            else:
                ch = [f"{a} {b}" for a, b in now.items() if prev.get(a) != b]
                shown = set(_macro_brief({}).keys()) | {"Averages",
                    "center", "span", "rbw", "rbw_auto", "detector",
                    "points", "trace_modes", "avg_count", "zero_span"}
                more = sum(1 for key in set(d) | set(prev_full)
                           if key not in shown
                           and d.get(key) != prev_full.get(key))
                txt = ", ".join(ch) if ch else "no change"
                if more:
                    txt += f" (+{more} more)"
            prev, prev_full = now, d
            lines.append(f"{k}. Settings: {txt}")
        elif kind == "wait":
            v = s.get("value", 0)
            unit = s.get("unit", "seconds")
            lines.append(f"{k}. Wait {_num(v)} s" if unit == "seconds"
                         else f"{k}. Wait {int(v)} sweeps")
        elif kind == "capture":
            what = " + ".join(x for x, on in (("PNG", s.get("png", True)),
                                               ("CSV", s.get("csv", True)))
                              if on) or "nothing"
            lines.append(f"{k}. Capture {what} as {s.get('name', '')}")
        else:
            lines.append(f"{k}. ?")
    return lines


class SweepRecorder:
    """
    Sweeps to disk while somebody leaves the instrument running.

    Deliberately OUTSIDE the window, and with no Qt in it at all, for the
    reason rx_hazard() is a pure function: everything here can be driven
    from a test, and the half that cannot (a key, a lamp, a status line)
    stays in Analyzer where there is nothing to get wrong.

    It is append-and-flush rather than buffer-and-write-at-the-end, which
    at fifteen minutes is not about memory -- the whole session would fit
    easily. It is that a recording lost to a crash, a yanked cable or a
    closed laptop at minute fourteen is exactly the failure this feature
    must not have. What is on disk is always a complete, readable file of
    everything up to the last row.

    `spectrum.bin` is one JSON header line, then one record per recorded
    sweep, all little-endian:

        REC_ROW_HEAD    t (s from start), first Hz, last Hz, n, flags
        n float64       the frequency axis -- ONLY when flags has REC_AXIS
        n float32       the displayed trace, dBm
        n float32       the per-bucket linear mean, dBm -- only with 0x08

    The mean is kept beside the trace because they answer different
    questions and bug 35 is what says so: the trace is the picture and the
    mean is what may be integrated afterwards. Storing only the first would
    leave anyone recomputing channel power from the file walking straight
    back into it.

    The axis is written whenever it CHANGES, so the span and the centre may
    move mid-recording. Telling a presale engineer not to touch anything
    for a quarter of an hour is not realistic on a customer site; the file
    just has to be honest about what moved. It is the whole axis, never
    first-and-last: the display points are not evenly spaced (see
    REC_MAGIC), and format 1 put levels at the wrong frequency by assuming
    they were.
    """

    def __init__(self, folder: str, meta: dict, interval_s: float,
                 max_seconds: float = REC_MAX_SECONDS,
                 max_bytes: int = REC_MAX_BYTES) -> None:
        self.folder = folder
        self.interval = max(0.0, float(interval_s))
        self.max_seconds = float(max_seconds)
        self.max_bytes = int(max_bytes)
        self.rows = 0
        self.bytes = 0
        self.gaps = 0
        self.t0 = time.time()
        self.t_last = None          # None until the first row lands
        self.stopped_why = ""       # non-empty once a backstop has fired
        self.axes = 0               # how many times the axis was written
        self._axis = None           # the axis the file last recorded
        self._rows = []             # kept for the max hold
        os.makedirs(folder, exist_ok=True)
        self.path = os.path.join(folder, "spectrum.bin")
        head = dict(meta)
        head["format"] = REC_MAGIC
        head["row_struct"] = REC_ROW_HEAD
        head["interval_s"] = self.interval
        head["started"] = datetime.now().isoformat(timespec="seconds")
        self._fh = open(self.path, "wb")
        self._fh.write((json.dumps(head) + "\n").encode("utf-8"))
        self._fh.flush()
        self.bytes = self._fh.tell()

    # -- writing ---------------------------------------------------------
    def due(self, now: float) -> bool:
        """Is this sweep one of the ones to keep?"""
        if self.stopped_why:
            return False
        if self.t_last is None or self.interval <= 0.0:
            return True
        return (now - self.t_last) >= self.interval

    def add(self, freqs, power, mean=None, flags: int = 0,
            now: Optional[float] = None) -> bool:
        """Append one sweep. False once a backstop has stopped the run."""
        if self.stopped_why:
            return False
        now = time.time() if now is None else now
        n = int(np.asarray(power).size)
        if n <= 0 or np.asarray(freqs).size != n:
            return False
        # A row far from its predecessor means the sweep was not arriving:
        # held in Single, the stream restarting (bug 46), the board away.
        # Marking it is what stops a fifteen-minute file reading as evenly
        # spaced when four of those minutes were a gap.
        gap = self.interval * REC_GAP_FACTOR if self.interval > 0 \
            else REC_GAP_MIN_S
        if self.t_last is not None and (now - self.t_last) > max(gap,
                                                                REC_GAP_MIN_S):
            flags |= 0x10
            self.gaps += 1
        # Explicit '<' dtypes rather than float32/float64, so the bytes on
        # disk are little-endian because this line says so, not because the
        # machine happens to be.
        f = np.asarray(freqs, dtype="<f8")
        p = np.asarray(power, dtype="<f4")
        m = None if mean is None else np.asarray(mean, dtype="<f4")
        if m is not None and m.size == n:
            flags |= 0x08
        else:
            m = None
        new_axis = self._axis is None or not np.array_equal(f, self._axis)
        if new_axis:
            flags |= REC_AXIS
        rec = struct.pack(REC_ROW_HEAD, now - self.t0,
                          float(f[0]), float(f[-1]), n, flags & 0xFF)
        self._fh.write(rec)
        if new_axis:
            self._fh.write(f.tobytes())
            self._axis = f.copy()
            self.axes += 1
        self._fh.write(p.tobytes())
        if m is not None:
            self._fh.write(m.tobytes())
        # Flushed every row on purpose -- see the class docstring. At one row
        # a second this is nothing, and it is what makes the file on disk
        # true at every instant rather than only at the end.
        self._fh.flush()
        self.rows += 1
        self.bytes = self._fh.tell()
        self.t_last = now
        self._rows.append((f, p))
        # Backstops. Checked AFTER the row is written, so the row that
        # crosses the line is kept rather than being the one thing lost.
        if (now - self.t0) >= self.max_seconds:
            self.stopped_why = (f"the {self.max_seconds / 3600:.0f} hour "
                                f"limit was reached")
        elif self.bytes >= self.max_bytes:
            self.stopped_why = (f"the {self.max_bytes / 1024**3:.0f} GB "
                                f"limit was reached")
        return True

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    # -- what the session came to ----------------------------------------
    def elapsed(self, now: Optional[float] = None) -> float:
        return (time.time() if now is None else now) - self.t0

    def summary(self):
        """(freqs, max hold, count) over every row that shares one axis.

        A recording whose span was changed partway has rows of different
        lengths and different edges, and there is no honest single max hold
        across those -- so the LAST axis wins and only the rows sharing it
        exactly are folded in. The count is reported in the max hold file,
        or a 900-row recording could quietly produce a 40-row summary.
        """
        if not self._rows:
            return None, None, 0
        f_ref, _ = self._rows[-1]
        keep = [p for (f, p) in self._rows if np.array_equal(f, f_ref)]
        if not keep:
            return None, None, 0
        stack = np.vstack(keep)
        return f_ref, np.nanmax(stack, axis=0), len(keep)


# ---------------------------------------------------------------------------
#  Screen video through Windows Media Foundation, pure ctypes
# ---------------------------------------------------------------------------
#
# Asked for 2026-09-23. Media Foundation ships with Windows and carries an
# H.264 encoder, so a real .mp4 needs neither ffmpeg nor OpenCV -- which
# matters for the same reason Pillow is build-time only: the application
# must not acquire a dependency. It is COM driven through its vtables, the
# way libbladeRF is driven through ctypes, and every vtable index below is
# counted from mfobjects.h / mfreadwrite.h. IMFAttributes has THIRTY methods
# after IUnknown, so an off-by-one there calls a different setter entirely
# and fails, if at all, somewhere unrelated.
#
# Checked by decoding the output back with Windows' own decoder, the one the
# player uses -- tests\test_recorder.py does it on every run.

class _GUID(C.Structure):
    _fields_ = [("Data1", C.c_uint32), ("Data2", C.c_uint16),
                ("Data3", C.c_uint16), ("Data4", C.c_ubyte * 8)]


def _guid(s: str) -> "_GUID":
    import uuid
    u = uuid.UUID(s)
    g = _GUID(u.fields[0], u.fields[1], u.fields[2])
    for i, b in enumerate(u.bytes[8:]):
        g.Data4[i] = b
    return g


def _com(ptr, index, *argtypes):
    """Method `index` of the COM object at `ptr`, as a callable."""
    vt = C.cast(C.c_void_p(ptr), C.POINTER(C.c_void_p))[0]
    addr = C.cast(C.c_void_p(vt), C.POINTER(C.c_void_p))[index]
    return C.WINFUNCTYPE(C.c_long, C.c_void_p, *argtypes)(addr)


def _hr(hr, what):
    if hr < 0:
        raise OSError(f"{what} failed (HRESULT 0x{hr & 0xFFFFFFFF:08X})")


def _com_release(ptr):
    if ptr:
        _com(ptr, 2)(ptr)


class Mp4Writer:
    """An H.264 .mp4 of frames handed to it as raw RGB32.

    Raises OSError from the constructor if Media Foundation or its encoder
    is not there -- Windows "N" editions ship without it -- and the caller
    is expected to go on recording everything else and SAY there is no
    video, the soft-binding rule of section 5 applied to a system codec.

    Two measured facts shape it:

      * **Width and height must be multiples of 16**, or the picture comes
        out sheared -- every row displaced a little further than the last.
        The encoder works in 16 x 16 blocks and its handling of the ragged
        edge is wrong. Measured on 1500 x 900, 1504 x 900 and 1502 x 902:
        all sheared; 1488 x 896 straight. The constructor refuses anything
        else rather than write a file that looks like a broken screen.
      * **The input stride is stated, not left to the default.** Media
        Foundation's default for uncompressed RGB is bottom-up; QImage is
        top-down. MF_MT_DEFAULT_STRIDE positive says so, and a decoded
        frame is the right way up.
      * **The encoder is constant-frame-rate and does not honour irregular
        timestamps.** Measured with frames tagged by their index: across a
        gap it repeats the frame AFTER the gap, so a frame written at 2.0 s
        was on screen from 0.9 s -- the video shows things before they
        happened -- and two frames landing in one slot lose one. So this
        class puts every frame on the fps grid itself, by wall-clock time,
        and fills skipped slots with the PREVIOUS frame, which is what the
        screen really showed while the window was stalled. The encoder is
        then handed a perfectly regular stream, which it passes through
        frame for frame.
      * **Quality mode, set through SetInputMediaType's third argument.**
        That argument is an attribute store of ICodecAPI values handed to
        the encoder as it is created, which spares reaching the encoder
        through GetServiceForStream afterwards. See REC_VIDEO_QUALITY for
        why a fixed bitrate was the wrong control. `mode` says which one
        the file was actually made with.
    """

    _RCMODE = "1c0608e9-370c-4710-8a58-cb6181c42423"   # AVEncCommonRateControlMode
    _QUALITY = "fcbf57a3-7ea5-4b0c-9644-69b40c39c391"  # AVEncCommonQuality

    _MAJOR = "48eba18e-f8c9-4687-bf11-0a74c9f96a8f"
    _SUBTYPE = "f7e34c9a-42e8-4714-b74b-cb29d72c35e5"
    _BITRATE = "20332624-fb0d-4d9e-bd0d-cbf6786c102e"
    _INTERLACE = "e2724bb8-e676-4806-b4b2-a8d6efb44ccd"
    _SIZE = "1652c33d-d6b2-4012-b834-72030849a37d"
    _RATE = "c459a2e8-3d2c-4e44-b132-fee5156c7bb0"
    _PAR = "c6376a1e-8d0a-4027-be45-6d9a0ad39bb6"
    _STRIDE = "644b4e48-1e02-4516-b0eb-c01ca9d49ac6"
    _VIDEO = "73646976-0000-0010-8000-00aa00389b71"
    _H264 = "34363248-0000-0010-8000-00aa00389b71"
    _RGB32 = "00000016-0000-0010-8000-00aa00389b71"
    MF_VERSION = 0x00020070

    def __init__(self, path: str, w: int, h: int, fps: int,
                 bitrate: int, quality: Optional[int] = None) -> None:
        if w % 16 or h % 16 or w <= 0 or h <= 0:
            raise ValueError(f"frame {w}x{h} is not a multiple of 16")
        self.w, self.h, self.fps = int(w), int(h), int(fps)
        self.frames = 0               # samples handed to the encoder
        self.fills = 0                # ...of which repeats covering a stall
        self.dropped = 0              # frames that landed in a taken slot
        self._wr = None
        self._slot = -1               # the last slot written
        self._prev = (C.c_ubyte * (4 * self.w * self.h))()
        try:
            self._mf = C.WinDLL("mfplat")
            self._rw = C.WinDLL("mfreadwrite")
            ole = C.WinDLL("ole32")
        except (OSError, AttributeError) as exc:
            raise OSError(f"Media Foundation is not available ({exc})")
        # The GUI thread already has COM from Qt; a second call returns
        # S_FALSE or RPC_E_CHANGED_MODE, neither of which matters here.
        ole.CoInitializeEx(None, 0)
        _hr(self._mf.MFStartup(self.MF_VERSION, 0), "MFStartup")
        self._started = True
        self.mode = f"fixed {bitrate / 1e6:.1f} Mb/s"
        try:
            if quality is not None:
                try:
                    self._open(path, bitrate, quality)
                    self.mode = f"quality {quality}"
                except OSError:
                    # Refused: a fresh writer with no encoder parameters, at
                    # the fixed fallback rate. A failed SetInputMediaType
                    # leaves the first writer in a state not worth trusting.
                    _com_release(self._wr)
                    self._wr = None
                    self._open(path, bitrate, None)
            else:
                self._open(path, bitrate, None)
        except Exception:
            self._abandon()
            raise

    def _type(self, subtype: str, bitrate: int = 0):
        t = C.c_void_p()
        _hr(self._mf.MFCreateMediaType(C.byref(t)), "MFCreateMediaType")
        t = t.value
        g = C.POINTER(_GUID)
        set32 = _com(t, 21, g, C.c_uint32)            # IMFAttributes::SetUINT32
        set64 = _com(t, 22, g, C.c_uint64)            # ::SetUINT64
        setg = _com(t, 24, g, g)                      # ::SetGUID
        _hr(setg(t, C.byref(_guid(self._MAJOR)), C.byref(_guid(self._VIDEO))),
            "SetGUID major")
        _hr(setg(t, C.byref(_guid(self._SUBTYPE)), C.byref(_guid(subtype))),
            "SetGUID subtype")
        _hr(set32(t, C.byref(_guid(self._INTERLACE)), 2), "interlace")
        _hr(set64(t, C.byref(_guid(self._SIZE)), (self.w << 32) | self.h),
            "frame size")
        _hr(set64(t, C.byref(_guid(self._RATE)), (self.fps << 32) | 1),
            "frame rate")
        _hr(set64(t, C.byref(_guid(self._PAR)), (1 << 32) | 1), "aspect")
        if bitrate:
            _hr(set32(t, C.byref(_guid(self._BITRATE)), int(bitrate)),
                "bitrate")
        else:
            _hr(set32(t, C.byref(_guid(self._STRIDE)), 4 * self.w), "stride")
        return t

    def _open(self, path: str, bitrate: int, quality: Optional[int]) -> None:
        wr = C.c_void_p()
        _hr(self._rw.MFCreateSinkWriterFromURL(C.c_wchar_p(path), None, None,
                                               C.byref(wr)),
            "MFCreateSinkWriterFromURL")
        self._wr = wr.value
        out = self._type(self._H264, bitrate)
        si = C.c_uint32()
        try:
            _hr(_com(self._wr, 3, C.c_void_p, C.POINTER(C.c_uint32))(
                self._wr, out, C.byref(si)), "AddStream")        # IMFSinkWriter
        finally:
            _com_release(out)
        self._si = si.value
        inp = self._type(self._RGB32)
        enc = None
        try:
            if quality is not None:
                a = C.c_void_p()
                _hr(self._mf.MFCreateAttributes(C.byref(a), 2),
                    "MFCreateAttributes")
                enc = a.value
                set32 = _com(enc, 21, C.POINTER(_GUID), C.c_uint32)
                _hr(set32(enc, C.byref(_guid(self._RCMODE)), 3),  # _Quality
                    "rate control mode")
                _hr(set32(enc, C.byref(_guid(self._QUALITY)), int(quality)),
                    "quality")
            _hr(_com(self._wr, 4, C.c_uint32, C.c_void_p, C.c_void_p)(
                self._wr, self._si, inp, enc), "SetInputMediaType")
        finally:
            _com_release(inp)
            _com_release(enc)
        _hr(_com(self._wr, 5)(self._wr), "BeginWriting")

    def write(self, addr: int, t_s: float) -> bool:
        """The frame at `addr` (w * h * 4 bytes), as the screen at t_s.

        False when it was dropped because its slot was already written; the
        next slot carries the newer screen, so nothing is lost but a frame
        nobody could have seen for more than a tenth of a second.
        """
        slot = int(round(t_s * self.fps))
        if slot <= self._slot:
            self.dropped += 1
            return False
        if self._slot >= 0:
            gap = slot - self._slot - 1
            for j in range(min(gap, int(REC_VIDEO_FILL_MAX_S * self.fps))):
                self._put(C.addressof(self._prev), self._slot + 1 + j)
                self.fills += 1
        self._put(addr, slot)
        C.memmove(self._prev, addr, 4 * self.w * self.h)
        self._slot = slot
        return True

    def _put(self, addr: int, slot: int) -> None:
        cb = 4 * self.w * self.h
        t = int(round(slot * 1e7 / self.fps))         # 100 ns units
        buf = C.c_void_p()
        _hr(self._mf.MFCreateMemoryBuffer(C.c_uint32(cb), C.byref(buf)),
            "MFCreateMemoryBuffer")
        buf = buf.value
        smp = None
        try:
            pd = C.c_void_p()
            _hr(_com(buf, 3, C.POINTER(C.c_void_p), C.c_void_p, C.c_void_p)(
                buf, C.byref(pd), None, None), "Lock")       # IMFMediaBuffer
            C.memmove(pd, addr, cb)
            _com(buf, 4)(buf)                                 # ::Unlock
            _hr(_com(buf, 6, C.c_uint32)(buf, cb), "SetCurrentLength")
            s = C.c_void_p()
            _hr(self._mf.MFCreateSample(C.byref(s)), "MFCreateSample")
            smp = s.value
            _hr(_com(smp, 42, C.c_void_p)(smp, buf), "AddBuffer")  # IMFSample
            _hr(_com(smp, 36, C.c_longlong)(smp, t), "SetSampleTime")
            _hr(_com(smp, 38, C.c_longlong)(smp, int(1e7 / self.fps)),
                "SetSampleDuration")
            _hr(_com(self._wr, 6, C.c_uint32, C.c_void_p)(
                self._wr, self._si, smp), "WriteSample")
            self.frames += 1
        finally:
            _com_release(smp)
            _com_release(buf)

    def close(self) -> None:
        """Finalize. Until this runs the .mp4 has no index and will not play."""
        try:
            if self._wr:
                _hr(_com(self._wr, 11)(self._wr), "Finalize")
        finally:
            self._abandon()

    def _abandon(self) -> None:
        _com_release(self._wr)
        self._wr = None
        if getattr(self, "_started", False):
            self._started = False
            self._mf.MFShutdown()


# Keys that take more than one column of the grid, because their name does
# not survive being shortened.
# Measurement takes the WHOLE row (3 of 3) since 2026-09-17, which is what
# puts it on a row of its own: at span 2 the next key still fitted beside it
# in the third column, so asking for "Measurement alone on a row" is a span
# change rather than an ordering one.
# Preset Keys takes the whole row for the same reason, and it is what puts
# it ABOVE the five slots rather than beside the first of them: asked for
# 2026-09-17 as "a different button just above the 5 Presets".
# Record takes the whole row for two reasons. It is the one key on this
# panel aimed at somebody who has never seen the instrument before -- a
# presale engineer on a customer site -- so it is the key that should be
# found without looking. And three columns is 24 characters of label, which
# is what lets it say "Start Recording" and "End Recording" in full, on one
# line, in Kfir's own words rather than in an abbreviation.
KEY_SPAN = {"Generator": 3, "Measurement": 3, "Preset Keys": 3, "Record": 3,
            "Sequencer": 3}
# Starting label size. It comes DOWN if the machine's font draws the
# label too wide for the key -- see SoftKey.
KEY_PX = 10

# What a key is ENGRAVED with, where that differs from the name of the group
# it opens. The name is the identifier -- _groups, self.keys and the tests
# all use it -- so it is deliberately not touched here; this is the face of
# the key only. A QPushButton honours a newline in its text, which is how a
# two-word key fits 78 px without abbreviating into initials nobody reads.
KEY_LABELS = {
    "Amplitude": "AMPTD",
    # Spelled out in full, on request, and given two grid columns to say
    # it in -- see KEY_SPAN. A double-width key is ordinary on a real front
    # panel, and the alternative was abbreviating the one control whose name
    # should never be guessed at.
    "Generator": "Signal Generator",
    "TX Freq": "TX\nFreq",
    "Amplitude Cal": "Ampl\nCal",
    "SG Check": "SG\nCheck",
    "Preset Keys": "Preset Keys",
    # Re-engraved to REC_LABEL_ON while a recording is running. The key's
    # own lamp says the same thing, and both are deliberate: a lamp is read
    # at a glance by somebody who knows the panel, and the words are read by
    # somebody who does not.
    "Record": "Start Recording",
}
REC_LABEL_OFF = "Start Recording"
REC_LABEL_ON = "End Recording"

# Panel keys whose hover tip is left OFF. Every other panel key says
# "<name> settings. Esc or a click outside closes the panel." -- worth
# having on a key whose name does not say what is behind it, and noise on
# one that does. Preset Keys sits directly above the five keys it fills in
# (5.36), so the panel it opens is the one thing the key could be for, and
# a tip explaining that is an explanation of something already found.
# Asked for 2026-09-17. The controls INSIDE the panel keep their own tips,
# which is where an explanation earns its place -- read when it is wanted.
KEY_NO_TIP = {"Preset Keys"}


class SoftKey(QtWidgets.QPushButton):
    """
    One instrument soft key.

    Checkable, because a key whose panel is open should look pressed -- on a
    real analyzer the lit key is how you know which menu you are in. The
    accent is the same family colour its panel carries, so the key and the
    panel that opens from it are visibly the same thing.
    """

    def __init__(self, label: str, accent: str = A_FILE,
                 span: int = 1) -> None:
        super().__init__(label)
        self.accent = accent
        self.span = max(1, int(span))
        self.setCheckable(True)
        # Fixed both ways, because the point of a grid of hard keys is that
        # they are the same size -- a row of keys stretched to fill a window
        # reads as a toolbar, which is what this stopped being.
        # A spanning key is that many key widths plus the grid spacing it
        # swallows, so it lines up with the column edges either side of it.
        kw = KEY_W * self.span + 4 * (self.span - 1)
        self.setFixedSize(kw, KEY_H)
        self._kw = kw
        self.set_label(label)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setStyleSheet(key_qss(accent))

    def set_label(self, label: str) -> None:
        """
        Engrave the key, fitting the font to what it has to say.

        A fixed key means the LABEL has to fit it, and the font that draws
        it belongs to the machine, not to us: a QPushButton silently ELIDES
        what it cannot fit, so "Amplitude" would ship as "Amplitud...". Two
        labels measured over the line on the first attempt, which is the
        same silent-clipping fault the control column hit at 318 px and
        again at 400 px.

        Checking a width once and hard-coding a size does not close it: the
        offscreen platform the tests run under has NO fonts at all -- 0
        families, falling back to Helvetica metrics -- so a width measured
        there says nothing about the font on Kfir's PC. Measure at runtime
        and come down a size or two if it does not fit.

        A method rather than four lines in __init__ because a preset slot
        key is re-engraved whenever its stored setup is named, stored or
        cleared (see _sync_preset_keys), and a name arriving from the user
        is exactly the label nobody measured in advance.
        """
        self.setText(label)
        f = self.font()
        f.setBold(True)
        for px in (KEY_PX, KEY_PX - 1, KEY_PX - 2):
            # Two steps of headroom is enough for a wide system font;
            # the label budget asserted in the tests is what keeps it
            # from ever needing more.
            f.setPixelSize(px)
            r = QtGui.QFontMetrics(f).boundingRect(
                QtCore.QRect(0, 0, self._kw - 8, KEY_H - 6),
                QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap, label)
            if r.width() <= self._kw - 8 and r.height() <= KEY_H - 6:
                break
        self.setFont(f)


class Popover(QtWidgets.QFrame):
    """
    The panel a soft key opens.

    A child of the main window, NOT a QDialog or a Qt::Popup window. Three
    reasons, all of them things that were tried:

      - a separate top-level window gets its own entry in the taskbar and
        its own shadow, and on Windows it steals activation, so the main
        window's title bar greys out every time you change a setting;
      - Qt::Popup closes itself on the first click INSIDE it that lands on
        certain widgets, which makes a spin box arrow a one-shot;
      - a child widget can be clipped to the window, which is what keeps a
        tall panel from spilling over the desktop.

    It holds one box at a time, and the boxes are built once and re-parented
    in and out. That is what makes a setting survive closing and reopening:
    the widget is the same object, so there is no state to copy anywhere.
    """

    def __init__(self, parent, park=None) -> None:
        super().__init__(parent)
        # Where a box goes when it is swapped out. Handing it back to a real
        # parent matters: an earlier version used setParent(None), which
        # makes the box a TOP-LEVEL WINDOW. It is hidden, so nothing appears,
        # but every switch between two panels left another orphan behind and
        # Qt is entitled to flash one on screen before it is hidden again.
        # Found by a test asserting every box is parked after a close, which
        # is the sort of thing that is invisible until it is asserted.
        self._park = park
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setAutoFillBackground(True)
        self._accent = A_FILE
        self._lay = QtWidgets.QVBoxLayout(self)
        self._lay.setContentsMargins(7, 7, 7, 7)
        self._lay.setSpacing(0)
        self._scroll = QtWidgets.QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._lay.addWidget(self._scroll)
        self._host = QtWidgets.QWidget()
        self._hostlay = QtWidgets.QVBoxLayout(self._host)
        self._hostlay.setContentsMargins(0, 0, 0, 0)
        self._hostlay.setSpacing(6)
        self._scroll.setWidget(self._host)
        self.hide()
        self.content = None

    def _restyle(self, accent: str) -> None:
        self._accent = accent
        self.setStyleSheet(
            "Popover{"
            f"background:{PANEL_BG};border:1px solid {accent};"
            "border-radius:8px;"
            "}")

    def take(self, widgets, accent: str) -> None:
        """Put these boxes in, taking whatever was here back out first."""
        while self._hostlay.count():
            it = self._hostlay.takeAt(0)
            w = it.widget()
            if w is not None:
                # Never deleted: these are the real control boxes and they
                # come back with their state intact, which is the whole
                # reason a setting survives closing a panel.
                if self._park is not None:
                    w.setParent(self._park)
                    self._park.layout().addWidget(w)
                else:
                    w.setParent(None)
        for w in widgets:
            w.setParent(self._host)
            w.show()
            self._hostlay.addWidget(w)
        self._hostlay.addStretch(1)
        self.content = list(widgets)
        self._restyle(accent)

    def wanted(self) -> QtCore.QSize:
        """
        How big this panel would like to be.

        Asks the CONTENT, not the QScrollArea. A scroll area's own sizeHint
        is a small fixed default -- it is designed to be given a size rather
        than to ask for one -- so sizing from it gave every panel the same
        243 px height whether it held one box or four, and the four-box one
        scrolled for no reason. Measured on the host widget instead, plus the
        margins and a scrollbar's width so a panel that does need to scroll
        does not lose a column of text to the bar.
        """
        s = self._host.sizeHint()
        m = self._lay.contentsMargins()
        bar = self.style().pixelMetric(QtWidgets.QStyle.PM_ScrollBarExtent) + 2
        # +4 for the 1 px border this frame draws through its stylesheet. A
        # stylesheet border comes out of the widget's content rect, so
        # without it every panel was sized to exactly its content and then
        # given two pixels less than that -- which showed a scrollbar on
        # twelve panels out of thirteen that had nothing to scroll.
        return QtCore.QSize(s.width() + m.left() + m.right() + bar + 4,
                            s.height() + m.top() + m.bottom() + 4)


class Marker:
    """
    One marker. The ANCHOR is `f`, and that is the whole point.

    It used to be `i`, an index into the display array, with `f` recomputed
    from it every frame. An index is a position on the SCREEN: pan or zoom
    and the same index is a different frequency, so a marker parked on a
    carrier slid off it as soon as the view moved -- which is what Kfir
    reported on 2026-09-01. Anchoring on the frequency instead makes the
    index the derived quantity (see Analyzer._m_index), which is the right
    way round: the frequency is what the user chose and what every readout,
    the delta, the harmonic targets and the CSV are about.

    `f` is NaN when the marker has never been placed. In zero span it holds
    a TIME rather than a frequency -- the same substitution the axis makes --
    and markers are cleared when that mode changes, because an anchor whose
    units have changed underneath it is worse than no anchor.

    `i` and `p` are cached results, refreshed by _draw_markers(). `i` is -1
    when the anchor falls outside the swept range, which is not an error: a
    marker is allowed to sit off screen and come back when the span widens.
    """
    __slots__ = ("i", "f", "p", "on", "delta")

    def __init__(self):
        self.i, self.f, self.p, self.on, self.delta = -1, float("nan"), float("nan"), False, False


class Analyzer(QtWidgets.QMainWindow):

    def __init__(self, src: Source, startup_note: str = "") -> None:
        super().__init__()
        # The revision goes here rather than only in an About box: this is
        # the one piece of text that is on screen in every screenshot he
        # takes, so it is where a build number is actually useful.
        self.setWindowTitle(f"{APP_NAME}   {version_line()}")
        self.setWindowIcon(app_icon())
        self.resize(1500, 920)
        self.src = src
        self.cfg = Config()
        self.last: Optional[Result] = None
        self.tdata: List[Optional[np.ndarray]] = []
        self.markers = [Marker() for _ in range(N_MARKERS)]
        self._peak_i = 0
        self._quiet = False
        # Whether the RFIC BIST loopback is currently switched in. There is
        # no user control for it any more; the SG self-check is the only
        # thing that turns it on, and the level warning follows this.
        #
        # Its LIFETIME is the thing to be careful about, and bug 33 is why:
        # the self-check is the only thing that sets it and _sg_check_undo()
        # is the only thing that clears it, so every exit from the check --
        # including the early return when no sweep arrived -- has to pass
        # through that undo. Nothing else in the GUI can clear it now that
        # the checkbox is gone, so a stranded True would leave every later
        # sweep measuring the transmitter's own baseband instead of the
        # antenna.
        self._lb_on = False
        self._lost_box = None
        # The running recording, or None. Set up before _build(), because
        # _sync_act_keys() reads it to engrave and light the Record key and
        # runs while the keys are being made.
        self.rec = None
        self._rec_warned = False
        self._rec_last = ""
        self._rec_vid = None          # the running Mp4Writer, or None
        self._rec_vid_err = ""        # why there is no video, when there isn't
        self._rec_vtimer = None       # one frame per tick
        self._rec_stimer = None       # the status line, once a second
        # The macro: its step list is built with the panel; the RUN state is
        # None whenever nothing is running (see _macro_run).
        self._mrun = None
        self._mtimer = None
        # The overpower warning: one dialog at a time, re-armed only
        # once the level has fallen clear of the limit -- see
        # _check_rx_hazard().
        self._haz_box = None
        self._haz_armed = True
        self.shot_dir = self._default_shot_dir()
        # Whether the last save's formatted workbook landed. A failed one does
        # NOT fail the save (see _save_trace), so the message has to say which
        # files are actually there.
        self._last_xlsx = False
        self.ref: Optional[np.ndarray] = None      # frozen reference trace
        self.ref_freqs: Optional[np.ndarray] = None
        self.ref_note = ""
        self._diff = False                          # showing live - reference
        self._draw_ms: Optional[float] = None
        self._frozen = False
        self.rx_ch = 0
        # How many times the worker has reported the receive stream
        # stalling this session. Shown in Help > About -- see _on_stalled.
        self._stall_seen = 0
        self._frame_seq = 0
        # The ten channels, held here rather than in the widgets, because the
        # panel shows ONE of them at a time (see _box_chpower). The widgets
        # are a view of the selected entry; _chp_commit() is what takes an
        # edit back into the list, and _chp_load() what puts an entry into
        # the boxes. Channel 1 starts on so that ticking Channel power
        # measures something immediately, exactly as it did when there was
        # only one channel.
        self.chp_cf = [float(self.cfg.center)] * N_CHP
        self.chp_bw = [2e6] * N_CHP
        self.chp_on = [i == 0 for i in range(N_CHP)]
        self._chp_sel = 0
        self._aclr_seeded = False
        # Per channel, and NOT one flag for all ten: seeding is "put this
        # channel where the span is, but only while nobody has set it", so a
        # shared flag would leave channel 2 sitting on channel 1's centre.
        self._chp_seeded = [False] * N_CHP
        self._meas_reset()          # Avg|Hold accumulators for CHP / OBW
        self.sg = SgConfig()
        self._sg_info: dict = {}
        self._sg_warned = False
        self._sg_check_restore = None
        # Which preset slot the instrument was last recalled from, and
        # whether anything has been changed since. Both exist before _build
        # because _push() can fire while the controls are being constructed,
        # and a lamp that reads its own state through getattr() is a guard
        # that reads as safety and is not (bug 31).
        self._slots = [None] * N_PRESETS
        self._slot_cur = None
        self._slot_dirty = True

        self._build()
        self._theme()
        if startup_note:
            self.status.setText(startup_note)
        self._start()
        # After _start(), because _apply_setup() pushes config at the worker
        # and _set_rx() goes through it. Before this the window is on
        # Config()'s defaults, so a missing or switched-off save file needs
        # no fallback -- the defaults are already what is on screen.
        self._session_restore()

    # ---------------------------------------------------------------- build
    def _build(self) -> None:
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        lay = QtWidgets.QVBoxLayout(root)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.plot = SpectrumPlot(axisItems={"bottom": HzAxis(orientation="bottom")})
        self.plot.setMenuEnabled(False)
        self.plot.setLabel("left", "Amplitude", units="dBm")
        self.plot.getAxis("bottom").enableAutoSIPrefix(False)
        self.plot.getAxis("left").enableAutoSIPrefix(False)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        self.plot.getViewBox().setDefaultPadding(0.0)

        self.curves = [self.plot.plot(pen=pg.mkPen(c, width=1.4))
                       for c in TRACE_COLORS]
        for c in self.curves:
            c.setVisible(False)
            # Belt and braces: even if something hands these a very long
            # array, pyqtgraph reduces it to the visible pixels rather than
            # trying to stroke every point.
            c.setDownsampling(auto=True, method="peak")
            c.setClipToView(True)

        # Diamonds, not dots, and asked for in those words on 2026-08-31:
        # "when I set a marker it shows with a tiny red dot, please replace
        # it with a diamond, just like the actual Spectrum Analyzer". Every
        # bench analyzer marks a marker with a diamond or a caret, and the
        # reason is not decoration -- a round dot 9 px across on a 1.4 px
        # trace reads as a blob sitting ON the line, so which sample it
        # actually marks is ambiguous. A diamond has a single point at top
        # and bottom, so its centre is unmistakable at any zoom.
        #
        # A step larger than the old dot as well: pyqtgraph's "d" is
        # inscribed in the size square, so a diamond at 9 px carries about
        # half the ink of a circle at 9 px and reads smaller than what it
        # replaced. MARKER_SIZE is a named constant because the next request
        # about it will be to nudge it.
        self.dots = pg.ScatterPlotItem(size=MARKER_SIZE, symbol="d",
                                       pen=pg.mkPen("#fff", width=1.2),
                                       brush=pg.mkBrush("#ff3b30"))
        self.plot.addItem(self.dots)
        self.mtext = []
        for _ in range(N_MARKERS):
            t = pg.TextItem(anchor=(0.5, 1.3), color="#ffffff")
            t.setVisible(False)
            self.plot.addItem(t)
            self.mtext.append(t)
        self.cursor = pg.TextItem(anchor=(0, 0), color="#9fb3c8")
        self.plot.addItem(self.cursor)

        # Frozen reference, drawn dashed so it never looks like a live trace.
        self.ref_curve = self.plot.plot(
            pen=pg.mkPen(REF_COLOR, width=1.2, style=QtCore.Qt.DashLine))
        self.ref_curve.setVisible(False)
        self.ref_curve.setDownsampling(auto=True, method="peak")
        self.ref_curve.setClipToView(True)
        self.limit_line = pg.InfiniteLine(
            angle=0, movable=False,
            pen=pg.mkPen("#ff453a", width=1.4, style=QtCore.Qt.DashLine))
        self.limit_line.setVisible(False)
        self.plot.addItem(self.limit_line)
        # One shaded band per channel. The SELECTED channel -- the one the
        # Channel CF and Width boxes edit -- is drawn brighter by
        # _chp_mark_sel(), because with ten bands on the graticule nothing
        # else on screen says which one the panel is about.
        self.chp_bands = [
            pg.LinearRegionItem(brush=pg.mkBrush(80, 200, 255, 40),
                                movable=False)
            for _ in range(N_CHP)]
        for _band in self.chp_bands:
            _band.setVisible(False)
            self.plot.addItem(_band)
        # "Channel N" and its reading, written over the middle of each
        # measured band (Kfir, 2026-09-27), in the band's own cyan. Placed in
        # view coordinates every frame, like the Identify label, so it stays
        # near the top of the graticule whatever the ref level and dB/div do.
        self.chp_labels = []
        for _i in range(N_CHP):
            _lab = pg.TextItem("", color=(120, 215, 255), anchor=(0.5, 0.0))
            _f = _lab.textItem.font()
            _f.setBold(True)
            _lab.setFont(_f)
            _lab.setZValue(20)
            _lab.setVisible(False)
            self.plot.addItem(_lab)
            self.chp_labels.append(_lab)
        # ACLR: the carrier's band in green, the offsets in amber, so the
        # measurement reads at a glance and is not mistaken for channel power.
        self.aclr_regions = []
        for _k in range(1 + 2 * ACLR_OFFSETS_MAX):
            _band = pg.LinearRegionItem(
                brush=pg.mkBrush(*((124, 255, 107, 45) if _k == 0
                                   else (255, 179, 64, 40))),
                movable=False)
            _band.setVisible(False)
            self.plot.addItem(_band)
            self.aclr_regions.append(_band)

        # The identified carrier, shaded the same way channel power is, with
        # its verdict written above the shading. Green rather than cyan so
        # the two never get confused: cyan is "what I am integrating over",
        # green is "what I think this is". Green also matches the Identify
        # signal box in the panel.
        self.cls_band = pg.LinearRegionItem(
            brush=pg.mkBrush(124, 255, 107, 34), movable=False)
        self.cls_band.setZValue(-10)          # behind the traces
        self.cls_band.setVisible(False)
        self.plot.addItem(self.cls_band)
        for line in self.cls_band.lines:      # mark the measured edges
            line.setPen(pg.mkPen(A_MEASURE, width=1,
                                 style=QtCore.Qt.DashLine))
        # anchor (0.5, 0): centred on the band, hanging just below the top of
        # the view, so it reads as a label ON the window rather than floating.
        self.cls_text = pg.TextItem(anchor=(0.5, 0), color=A_MEASURE)
        # A step larger than everything else on the plot, and bold. This is
        # a verdict about the carrier you are pointing at, not a scale
        # marking, and at the plain weight it disappeared into the
        # graticule. pyqtgraph gained TextItem.setFont in 0.11; older
        # versions keep the font on the wrapped QGraphicsTextItem, and a
        # label that is merely the ordinary size is not worth raising over.
        _clsf = QtWidgets.QApplication.font()
        _clsf.setPointSizeF(_clsf.pointSizeF() + CLS_LABEL_PT)
        _clsf.setBold(True)
        try:
            self.cls_text.setFont(_clsf)
        except AttributeError:
            try:
                self.cls_text.textItem.setFont(_clsf)
            except Exception:
                pass
        self.cls_text.setVisible(False)
        self.plot.addItem(self.cls_text)

        # RX1+RX2: the second port gets a GRATICULE OF ITS OWN, under the
        # first, and only in Both. Asked for on 2026-09-01 -- "divide the
        # Spectrum screen to two halves: The first for Rx1 and the second
        # for Rx2" -- after the previous arrangement was reported as showing
        # only one port.
        #
        # It was in fact showing both, as one dashed curve on the same
        # graticule, and that is the lesson worth keeping: an overlay of two
        # ports is only readable while they DIFFER. On the simulator RX2 is
        # the same scene 4 dB down with an identical noise floor, so the two
        # lines coincide and read as one trace, and on real hardware two
        # antennas on the same band would do much the same. Two axes cannot
        # hide one port behind the other.
        #
        # Deliberately a plain PlotWidget, not a SpectrumPlot: the wheel and
        # drag gestures that retune the radio belong to ONE plot, and this
        # one follows. Its ranges are set from _axes() alongside the first,
        # rather than by setXLink -- a link would move this view without the
        # sweep knowing, and the picture and the radio would drift apart,
        # which is the whole reason SpectrumPlot exists.
        self.plot2 = pg.PlotWidget(
            axisItems={"bottom": HzAxis(orientation="bottom")})
        self.plot2.setMenuEnabled(False)
        self.plot2.setMouseEnabled(x=False, y=False)
        self.plot2.hideButtons()
        self.plot2.getAxis("bottom").enableAutoSIPrefix(False)
        self.plot2.getViewBox().setDefaultPadding(0.0)
        self.plot2.getAxis("left").setWidth(76)
        self.curve_rx2 = self.plot2.plot(pen=pg.mkPen(RX2_COLOR, width=1.4))
        self.curve_rx2.setDownsampling(auto=True, method="peak")
        self.curve_rx2.setClipToView(True)
        # One tag per graticule, naming its port. Placed in view coordinates
        # and refreshed every frame because the y range moves with the ref
        # level and dB/div. They say only the port now: with a plot each
        # there is nothing to disambiguate about line style, which is what
        # the old "RX1 solid" / "RX2 dashed" wording was for.
        self.rx1_tag = pg.TextItem(
            "RX1", anchor=(0, 0), color="#0b0e11",
            fill=pg.mkBrush("#cfe0ef"), border=pg.mkPen("#cfe0ef"))
        self.rx2_tag = pg.TextItem(
            "RX2", anchor=(0, 0), color="#0b0e11",
            fill=pg.mkBrush(RX2_COLOR), border=pg.mkPen(RX2_COLOR))
        for tag, plt in ((self.rx1_tag, self.plot),
                         (self.rx2_tag, self.plot2)):
            tag.setVisible(False)
            tag.setZValue(50)          # over the traces, never hidden by them
            plt.addItem(tag)
        # Why the lower graticule is empty, when it is. It exists because the
        # pane opens on the port button rather than on the first two-port
        # sweep (see _draw_rx2): an empty plot with no explanation is what
        # Both looked like the first time, and an empty plot that names its
        # own reason is a readout.
        self.rx2_note = pg.TextItem("", anchor=(0.5, 0.5), color="#9fb3c8")
        self.rx2_note.setVisible(False)
        self.rx2_note.setZValue(50)
        self.plot2.addItem(self.rx2_note)

        # Spectrogram: FREQUENCY up the Y axis, TIME along X, newest at the
        # right. That is the orientation Kfir asked for, and it is why this
        # plot no longer shares an axis with the spectrum above it -- the
        # spectrum's X is frequency and this one's X is time, so an X link
        # would tie together two quantities that have nothing to do with each
        # other. Zoom still follows: a pan or a wheel zoom on the spectrum
        # changes the span, which reaches here as the next frame's f0/f1.
        self.wf = pg.PlotWidget(axisItems={"left": HzAxis(orientation="left")})
        self.wf.setMenuEnabled(False)
        self.wf.setMouseEnabled(x=False, y=False)
        self.wf.hideButtons()
        self.wf.setLabel("bottom", "Time  (oldest left, newest right)")
        # No numbers on the time axis on purpose. Rows are display frames,
        # and the frame interval is only nominal -- a slow wide sweep folds
        # several sweeps into one row -- so a seconds scale would be a
        # precise-looking guess. The row count is in the Display box.
        self.wf.getAxis("bottom").setStyle(showValues=False)
        self.wf.getAxis("left").enableAutoSIPrefix(False)
        self.wf.getViewBox().setDefaultPadding(0.0)
        self.img = pg.ImageItem()
        self.img.setLookupTable(build_lut())
        self.wf.addItem(self.img)
        self._wf_buf = None
        self._wf_head = 0
        self._wf_depth = 240
        # Both left axes the same width so the two plots line up down the
        # left edge even though they no longer share a scale. 76 rather than
        # 62 because a frequency label ("2.44000 GHz") is wider than a dBm
        # one; the spectrum gives up 14 px it does not miss.
        self.plot.getAxis("left").setWidth(76)
        self.wf.getAxis("left").setWidth(76)

        # ------------------------------------------------------------------
        # No permanent side panels. Every group of controls lives in a
        # popover behind its own key, and the keys are a grid down the
        # RIGHT-HAND edge under engraved captions, the way the front panel of
        # the bench analyzer Kfir sent a picture of works: the screen is the
        # measurement, and a menu is only on screen while you are using it.
        #
        # They were a row along the bottom first. That was a reasonable
        # reading of a request whose reference image had not arrived with it,
        # and it was wrong -- the keys go on the right, which is where an
        # instrument of this shape puts them and where the picture put them.
        #
        # What that replaced: two fixed columns, 450 px on the right and
        # 400 px on the left, together 850 px of a 1500 px window that were
        # showing controls whether or not anyone was reading them. The right
        # one was about 2800 px of boxes in an 850 px viewport, so most of
        # it was scrolled out of sight anyway -- see the note in section 5.5
        # about a whole instrument going unnoticed 14 boxes down.
        #
        # The BOXES THEMSELVES ARE UNCHANGED. Every _box_*() builder is the
        # one that was there before, with its widgets, tooltips and signal
        # connections; only where they are parented has changed. That is
        # what makes this a UI change and not a rewrite -- and it is why a
        # setting survives closing and reopening a popover: it is the same
        # widget object, so there is nothing to save or restore.
        # ------------------------------------------------------------------
        self._sgbox_head = self._box_sg_head()
        self._sgbox_out = self._box_sg_out()
        self._sgbox_lvl = self._box_sg_lvl()
        self._sgbox_wave = self._box_sg_wave()
        self._sgbox_burst = self._box_sg_burst()
        self._sgbox_test = self._box_sg_test()

        # A thin strip across the top for the two things that are not
        # settings: what the instrument is, and the one key that puts it
        # back. Preset stays at the top because that is where Kfir asked for
        # it and the reason still holds -- it is what you reach for when the
        # state is one you no longer understand, so it must not be behind a
        # menu of its own.
        topbar = QtWidgets.QHBoxLayout()
        topbar.setContentsMargins(0, 0, 0, 2)
        topbar.setSpacing(6)
        self.b_preset_all = QtWidgets.QPushButton("Preset")
        self.b_preset_all.setFixedHeight(26)
        self.b_preset_all.setMinimumWidth(96)
        self.b_preset_all.setToolTip(
            "Puts the whole analyzer back to its default state, the way a\n"
            "bench analyzer's Preset key does: span, RBW, amplitude,\n"
            "traces, markers, the frozen reference, the limit line,\n"
            "measurements, the trigger and the Cal offset.\n\n"
            "The transmitter is keyed off as part of it, and the\n"
            "generator's own settings go back to their defaults too --\n"
            "waveform, level, carrier offset, TX cal offset, burst.\n\n"
            "Your saved setups are left alone. It asks first.")
        # Same look as the keys it sits above -- it is a front-panel key
        # that happens not to open a panel, so it should not read as a
        # different kind of control.
        self.b_preset_all.setStyleSheet(
            key_qss(A_FILE) + "QPushButton{padding:2px 10px;}")
        self.b_preset_all.clicked.connect(lambda: self._preset_reset())
        topbar.addWidget(self.b_preset_all, 0)
        topbar.addStretch(1)
        lay.addLayout(topbar, 0)

        # Center / RBW / Span in a strip UNDER the graticule, which is where
        # the instrument in Kfir's reference photo has them and where he asked
        # for them. They were in the top-right corner, which is a place no
        # bench analyzer puts them.
        #
        # Attached to the spectrum rather than to the window, so it stays with
        # the trace it describes when the splitter is dragged or the
        # spectrogram is switched on. The plot gives up ~20 px for it.
        self.l_tuned = QtWidgets.QLabel("")
        self.l_rbw_rd = QtWidgets.QLabel("")
        self.l_span_rd = QtWidgets.QLabel("")
        rdbar = QtWidgets.QHBoxLayout()
        rdbar.setContentsMargins(78, 1, 8, 1)
        rdbar.setSpacing(12)
        for w, stretch, align in ((self.l_tuned, 1, QtCore.Qt.AlignLeft),
                                  (self.l_rbw_rd, 1, QtCore.Qt.AlignHCenter),
                                  (self.l_span_rd, 1, QtCore.Qt.AlignRight)):
            w.setStyleSheet(
                f"color:{A_TUNE};font-weight:bold;font-size:13px;")
            w.setAlignment(align | QtCore.Qt.AlignVCenter)
            rdbar.addWidget(w, stretch)
        # 78 px of left margin above lines the first readout up with the
        # graticule's left edge, which is where the 76 px Y axis ends.
        spec = QtWidgets.QWidget()
        speclay = QtWidgets.QVBoxLayout(spec)
        speclay.setContentsMargins(0, 0, 0, 0)
        speclay.setSpacing(0)
        speclay.addWidget(self.plot, 1)
        speclay.addLayout(rdbar, 0)

        split.addWidget(spec)
        split.addWidget(self.plot2)
        split.addWidget(self.wf)
        self._split = split
        # Whether the lower graticule is meant to be up. See _show_rx2.
        self._rx2_on = False
        split.setSizes([560, 0, 300])
        # Hidden until the port is Both, for the same reason the spectrogram
        # is hidden until its box is ticked: a pane on screen that nothing is
        # feeding is worse than no pane.
        self.plot2.hide()
        # Hidden to match the unticked box. setChecked(False) on a box that
        # is ALREADY unchecked emits nothing, so _show_wf never runs and the
        # spectrogram would otherwise be on screen while its control says it
        # is off.
        self.wf.hide()
        # The plot and the key panel side by side: keys down the RIGHT, which
        # is where every bench analyzer puts them and where Kfir's reference
        # image puts them. The key panel is added at the end of _build(),
        # once there are keys to put in it.
        mid = QtWidgets.QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(6)
        mid.addWidget(split, 1)
        lay.addLayout(mid, 1)

        # Where a box waits while its popover is closed. Hidden, and a real
        # parent rather than None: a re-parented widget with no parent
        # becomes a top-level window, which Qt will briefly FLASH on screen
        # on Windows before it is hidden again.
        self._park = QtWidgets.QWidget(root)
        self._park.hide()
        park_lay = QtWidgets.QVBoxLayout(self._park)
        park_lay.setContentsMargins(0, 0, 0, 0)

        # One soft key per group. Grouping is by the same families the
        # accents already encoded, so a key opens boxes of one colour and
        # the key is that colour too. Boxes that were separate but always
        # read together share a key -- markers with the counter, the four
        # measurement boxes with each other -- which is what gets this down
        # to a front panel's worth of keys instead of nineteen.
        groups = [
            ("Radio",     A_RADIO,   [self._box_radio(), self._box_health()]),
            # A_TUNE rather than A_RADIO since 2026-09-17: Sweep sits in
            # the FREQUENCY block now, and the accent belongs to the block
            # (5.18, which moved Traces the same way).
            ("Sweep",     A_TUNE,    [self._box_sweep()]),
            ("Freq",      A_TUNE,    [self._box_freq()]),
            # A_VIEW for the same reason: RBW moved into the AMPLITUDE
            # block on the same day.
            ("RBW",        A_VIEW,   [self._box_bw()]),
            ("Trigger",   A_TUNE,    [self._box_trigger()]),
            ("Amplitude", A_VIEW,    [self._box_ampl()]),
            ("Display",   A_VIEW,    [self._box_view()]),
            ("Markers",   A_MEASURE, [self._box_markers(), self._box_counter()]),
            # Traces moved out of the Amplitude block and in here on
            # 2026-08-26, at Kfir's request. It came with A_VIEW and is
            # recoloured to match, because the accent is what makes a block
            # readable as one thing (section 3): a violet key under a green
            # caption reads as a stray, and same-colour keys sitting together
            # is the whole rule. What it does not change is the box -- traces
            # and the reference trace are the same widgets in the same panel.
            ("Traces",    A_MEASURE, [self._box_traces(),
                                      self._box_reference()]),
            # Channel power on a key of its own, at Kfir's request. It was
            # the largest box in the window and the Measure panel was the
            # only one that genuinely had to scroll (5.12); splitting it
            # fixes both at once.
            # Since 2026-09-27 the Measurement key opens a LIST -- Channel
            # Power, Occupied Bandwidth, ACLR -- and each item opens its own
            # settings in the same place. Kfir's layout. The three settings
            # boxes are built inside _box_meas_menu() and registered as
            # panels with no key of their own, anchored to this one.
            ("Measurement", A_MEASURE, [self._box_meas_menu()]),
            # The "Peak Table" box was here until 2026-09-17, listing the
            # n strongest local maxima. Kfir removed it as obsolete, and it
            # is: ten markers with peak search, Next peak and Harmonics of
            # M1 cover what it was for, and the marker table beside them
            # says the same thing about signals somebody chose rather than
            # about whatever happened to be tall.
            # Occupied bandwidth moved to the Measurement list (2026-09-27).
            ("Measure",   A_MEASURE, [self._box_limit(), self._box_classify()]),
            # Single and Restart are ACTION keys: no boxes, no popover.
            # They came out of the Sweep panel onto keys of their own on
            # 2026-08-30, still opening a panel each; Kfir removed the
            # panels on 2026-08-31 -- "I don't need the inner popups, just
            # use them as a regular button from the outside" -- and he is
            # right, because a panel holding one button is a click in front
            # of a click.
            #
            # An empty box list is what marks a key as an action, and
            # build_keys() wires it straight to the callable in _key_acts
            # instead of to _toggle_panel. Such a key is deliberately NOT
            # registered in self._groups, so _open_panel() cannot be talked
            # into anchoring a panel to a key that has nothing to show.
            ("Single",    A_MEASURE, []),
            ("Restart",   A_MEASURE, []),
            # An ACTION key, like Single and Restart: one press starts the
            # recording, the next ends it. A panel in front of it would be
            # the click-in-front-of-a-click 5.24 removed, and it would be
            # worse here than there -- the whole request was that somebody
            # on a customer site can press one thing. Its settings (how
            # often to keep a sweep, where the folder is) live in the
            # Capture panel, which is already the FILE block's answer to
            # "where do saved things go".
            ("Record",    A_FILE,    []),
            ("Capture",   A_FILE,    [self._box_capture()]),
            ("Setups",    A_FILE,    [self._box_presets()]),
            # A panel key, not an action key: the panel IS the macro (its
            # step list), and running it is a button inside. Its running
            # state is on the status bar (l_macro), not the key's lamp --
            # a panel key's lit look already means "panel open".
            ("Sequencer", A_FILE,    [self._box_macro()]),
            # Filling in the five preset slots is its own key, sitting at the
            # head of the PRESETS block rather than as a second box behind
            # Setups. Kfir asked for the move on 2026-09-17 and the reason is
            # discoverability: a panel that manages the keys directly under it
            # is found by looking at them, while one filed behind a key about
            # something else is found by being told where it is. The two are
            # still different things -- Setups is the named-setup list and the
            # save-on-exit tick -- which is why this is a move and not a
            # merge.
            ("Preset Keys", A_FILE,  [self._box_slots()]),
            # Transmit last and red, kept apart from everything that
            # receives -- the split section 5.5 insisted on, which survives
            # the loss of the separate column because the colour does the
            # same job a different side of the window used to.
            #
            # An ACTION key since 2026-09-16: pressing it reveals the
            # generator's own red keys straight away, instead of opening a
            # panel that held one tick-box. See _sg_key().
            ("Generator", A_TX,      []),
        ]
        # The five preset slots, ACTION keys in the FILE accent. One press
        # recalls; there is no panel behind them, which is the whole point
        # -- everything that MANAGES a slot (store, rename, clear, import,
        # export) lives in the Setups panel, and a key that had to open a
        # panel first would be the click-in-front-of-a-click 5.24 removed
        # from Single and Restart.
        #
        # They are appended rather than written out, because five hand-typed
        # near-identical rows is five places for one of them to be wrong.
        groups += [(f"Preset {i + 1}", A_FILE, []) for i in range(N_PRESETS)]

        # The generator's own function keys, on their own row.
        # One key per function, each opening only that function's
        # settings -- not one key opening all of them, which would be
        # the old sidebar in a different container. The row is hidden
        # until the switch above is ticked, so an instrument nobody is
        # transmitting with costs no width, and it is red throughout:
        # that is the transmit/receive split of 5.5, now carried by
        # colour and by a row of its own instead of by a column.
        sg_groups = [
            ("RF OUT",    A_TX, [self._sgbox_head]),
            ("TX Freq",   A_TX, [self._sgbox_out]),
            ("Amplitude Cal", A_TX, [self._sgbox_lvl]),
            ("Waveform",  A_TX, [self._sgbox_wave]),
            ("Burst",     A_TX, [self._sgbox_burst]),
            ("SG Check",  A_TX, [self._sgbox_test]),
        ]
        self._groups = {}
        # Every key's accent, action keys included. _groups cannot serve
        # here: an action key is deliberately absent from it, so a block
        # caption taking its colour from _groups[names[0]] dies with a
        # KeyError the moment an action key is first in its block -- which
        # is what Signal Generator became on 2026-09-16.
        self._key_accent = {}
        self.keys = {}
        # Keys that DO something rather than open something. A bench
        # analyzer has both kinds side by side and they look identical,
        # which is fine -- what matters is that the tooltip says which it
        # is, because one changes the instrument the instant it is pressed
        # and the other only shows you a page.
        self._key_acts = {
            # Plain, one-line tips on request (2026-09-24): the long ones
            # explained things somebody hovering does not need. The detail
            # lives in READ_ME.txt. The "transmits nothing" line on the
            # generator is kept on purpose -- it is the one fact a person
            # must not have to guess before pressing a red key.
            "Single": (self._single_key,
                       "Hold one sweep. Press again to sweep continuously."
                       "  (S)"),
            "Restart": (self._restart_meas,
                        "Clear the traces and start measuring again.\n"
                        "Your settings are kept."),
            "Record": (self._rec_key,
                       "Record the spectrum and save it to a file."),
            "Generator": (self._sg_key,
                          "Show the signal generator keys.\n"
                          "Nothing transmits until you press RF OUT."),
        }
        for _i in range(N_PRESETS):
            # The real tooltip carries the slot's stored name and is written
            # by _sync_preset_keys(); this is what it says before anything
            # has been loaded.
            self._key_acts[f"Preset {_i + 1}"] = (
                (lambda i=_i: self._preset_key(i)), PRESET_KEY_TIP)

        def build_keys(table):
            """
            Make one key per group. Placing them is a separate pass.

            A group with no boxes is an ACTION key -- it fires a callable
            from _key_acts instead of opening a panel, and is left out of
            _groups so nothing can try to anchor a panel to it.
            """
            names = []
            for name, accent, boxes in table:
                key = SoftKey(KEY_LABELS.get(name, name), accent,
                              KEY_SPAN.get(name, 1))
                self._key_accent[name] = accent
                if boxes:
                    for w in boxes:
                        park_lay.addWidget(w)
                    self._groups[name] = (accent, boxes)
                    if name not in KEY_NO_TIP:
                        key.setToolTip(f"{name} settings.  Esc or a click "
                                       f"outside closes the panel.")
                    key.clicked.connect(
                        lambda _c, n=name: self._toggle_panel(n))
                else:
                    fn, tip = self._key_acts[name]
                    key.setToolTip(tip)
                    # Pressing an action key closes whatever panel is open.
                    # Leaving one hanging over the trace while the sweep it
                    # just fired lands is the opposite of what the press was
                    # for -- you press Single in order to READ the trace.
                    key.clicked.connect(
                        lambda _c, f=fn: (self._close_panel(), f()))
                self.keys[name] = key
                names.append(name)
            return names

        build_keys(groups)
        # The Measurement list's three settings panels: in _groups so they
        # open like any panel, with no key of their own -- _panel_key says
        # which key they hang from and light.
        for _sub, _bx in self._meas_subs:
            park_lay.addWidget(_bx)
            self._groups[_sub] = (A_MEASURE, [_bx])
            self._panel_key[_sub] = "Measurement"
        self._sg_keys = build_keys(sg_groups)
        # The slot keys carry their stored setup's NAME, which is only known
        # once the keys exist and the file has been read. _box_slots() above
        # has already loaded it, so this is the engraving pass.
        self._sync_preset_keys()

        # The blocks the keys are grouped into, each under its own engraved
        # caption -- the reference image has Channel / Y Scale / X Scale /
        # Measurement over its columns, and this is the same idea using the
        # accent families the app already had. The caption carries its
        # block's colour, so a block is readable as one thing before any of
        # its labels are.
        blocks = [
            # Caption asked for on 2026-09-06: it read RADIO, and this half
            # of the front panel IS the spectrum analyzer -- which also
            # makes it the pair of SIGNAL GENERATOR at the bottom instead
            # of a word that names neither instrument.
            #
            # Rearranged 2026-09-17 on Kfir's own row-by-row list, after
            # using the panel: Sweep moved up into FREQUENCY, RBW down into
            # AMPLITUDE, Measurement onto a row of its own, and Restart
            # ahead of Single. Two keys therefore changed BLOCKS, and their
            # accents changed with them -- see the groups table, and 5.18
            # for why that is not optional: a block is one colour so it
            # reads as one thing before any of its labels are, and a stray
            # colour under a caption reads as a mistake.
            ("Spectrum analyzer", ["Radio"]),
            ("Frequency",   ["Freq", "Sweep", "Trigger"]),
            ("Amplitude",   ["Amplitude", "RBW", "Display"]),
            # Caption is ANALYSIS rather than MEASUREMENT now that one of
            # the keys under it IS called Measurement -- a key whose face
            # repeats its own group heading reads as a duplicate.
            ("Analysis",    ["Markers", "Measure", "Traces", "Measurement",
                             "Restart", "Single"]),
            # Record heads the block, on a row of its own above the two
            # keys that save one thing at a time -- the same arrangement
            # 5.36 settled on for Preset Keys, and for the same reason: the
            # key somebody is looking for should be the first one under the
            # caption, not the third. Sequencer is nine letters, one over a
            # single key's budget, so it takes the row under Record (see
            # KEY_SPAN); putting it after Capture and Setups instead would
            # leave a hole in the middle of the block rather than at its end.
            ("File",        ["Record", "Sequencer", "Capture", "Setups"]),
            # The preset slots (N_PRESETS), under a caption of their own and next
            # to FILE because that is what they are -- a stored setup you
            # can hand to somebody else. Asked for 2026-09-17, and asked for
            # HERE rather than as a row over the trace: keys belong in the
            # key column with the rest of the front panel.
            ("Presets",     ["Preset Keys"]
                            + [f"Preset {i + 1}" for i in range(N_PRESETS)]),
            # Transmit last and red, kept apart from everything that
            # receives: the split 5.5 insisted on, carried by colour and by
            # its own block rather than by a column of its own.
            ("Signal generator", ["Generator"]),
        ]

        def key_grid(names, parent=None):
            g = QtWidgets.QGridLayout() if parent is None \
                else QtWidgets.QGridLayout(parent)
            g.setContentsMargins(0, 0, 0, 0)
            g.setSpacing(4)
            row = col = 0
            for n in names:
                span = KEY_SPAN.get(n, 1)
                if col + span > KEY_COLS:      # will not fit -- next row
                    row, col = row + 1, 0
                g.addWidget(self.keys[n], row, col, 1, span)
                col += span
                if col >= KEY_COLS:
                    row, col = row + 1, 0
            # Left-align a short last row instead of letting the grid space
            # two keys out across three columns, which reads as a missing key.
            if col:
                g.setColumnStretch(KEY_COLS, 1)
            return g

        self.keypanel = QtWidgets.QWidget()
        kp = QtWidgets.QVBoxLayout(self.keypanel)
        kp.setContentsMargins(4, 0, 0, 0)
        kp.setSpacing(7)
        self._key_blocks = {}
        for caption, names in blocks:
            holder = QtWidgets.QWidget()
            hl = QtWidgets.QVBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(3)
            cap = QtWidgets.QLabel(caption)
            cap.setAlignment(QtCore.Qt.AlignHCenter)
            # Engraved-caption look: small, spaced, upper case, and dimmer
            # than the keys under it. The hierarchy has to run
            # caption -> key -> label, or a bright caption competes with the
            # thing it is naming.
            cap.setText(caption.upper())
            cap.setStyleSheet(
                f"color:{mix_hex(self._key_accent[names[0]], PANEL_BG, 0.30)};"
                "font-size:9px;font-weight:bold;letter-spacing:1px;")
            hl.addWidget(cap)
            hl.addLayout(key_grid(names))
            # Transmit is set apart from everything that receives -- see
            # SG_BLOCK_GAP. Added before the block rather than after the one
            # above it, so it stays attached to the generator if the block
            # order ever changes.
            if caption == "Signal generator":
                kp.addSpacing(SG_BLOCK_GAP)
            kp.addWidget(holder, 0)
            self._key_blocks[caption] = holder

        # The generator's function keys hang under the Signal generator
        # caption, in a widget of their own so the whole block of them comes
        # and goes with the switch while the switch's OWN key stays put --
        # hiding that one too would leave no way to turn the generator on.
        self.sg_keybar = QtWidgets.QWidget()
        key_grid(self._sg_keys, self.sg_keybar)
        self._key_blocks["Signal generator"].layout().addWidget(
            self.sg_keybar)
        self.sg_keybar.hide()

        # A QComboBox sizes itself to its LONGEST item by default, so one
        # descriptive entry ("mini_exp[1] (hardware-independent)") was on its
        # own forcing a panel wider than anything else in it needed.
        for cb in self._park.findChildren(QtWidgets.QComboBox):
            cb.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLength)
            cb.setMinimumContentsLength(8)
            cb.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                             cb.sizePolicy().verticalPolicy())
        # Same story for labels: one long unwrapped sentence sets the width
        # of everything around it. Wrapping is a no-op on anything that
        # already fits, so it goes on the lot.
        for lbl in self._park.findChildren(QtWidgets.QLabel):
            lbl.setWordWrap(True)

        # Keys go in beside the plot, top-aligned, so the blocks read down
        # from the top of the trace like the engraved panel they imitate
        # rather than floating in the middle of the free space.
        mid.addWidget(self.keypanel, 0, QtCore.Qt.AlignTop)

        # The popover itself, and the one filter that closes it on a click
        # anywhere else. Created after the keys so it stacks above them.
        self.popover = Popover(root, self._park)
        self._panel_open = None
        # Watch the plot surfaces for a click, NOT the whole application.
        #
        # installEventFilter on QApplication is the obvious way to catch a
        # click anywhere, and it segfaulted every test process at exit --
        # exit 139 with every check passed, which is a confusing thing to
        # debug. An application-wide filter written in Python outlives the
        # window that owns it: the QApplication is still alive during
        # interpreter teardown and keeps handing events to a Python callable
        # whose module is being dismantled.
        #
        # These are all children of this window, so the registrations die
        # with it and nothing dangles. A QGraphicsView delivers mouse
        # presses to its VIEWPORT rather than to itself, so both go on the
        # list -- filtering only the view catches nothing at all.
        for w in (root, self.plot, self.plot.viewport(),
                  self.wf, self.wf.viewport()):
            w.installEventFilter(self)
        esc = QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Escape), self)
        esc.activated.connect(self._close_panel)

        # Hidden until the "Signal generator" checkbox is ticked. Built
        # regardless so every handler can assume its widgets exist.
        self._sg_toggle_panel(False)
        self._health_refresh()

        sb = self.statusBar()
        self.l_rbw = QtWidgets.QLabel()
        self.l_rate = QtWidgets.QLabel()
        self.l_seg = QtWidgets.QLabel()
        self.l_ovl = QtWidgets.QLabel()
        self.l_ovl.setStyleSheet("color:#ff453a;font-weight:bold;")
        # Separate from l_ovl, which is the ADC clipping. Overload is a
        # measurement problem and clears when you turn the gain down; this
        # one is a hardware problem and does not. Sharing one label would
        # have let a fixable complaint mask an unfixable one.
        self.l_haz = QtWidgets.QLabel()
        self.l_haz.setStyleSheet("color:#ff453a;font-weight:bold;")
        # Separate again, and amber rather than red, because this one is
        # neither a measurement problem nor a hardware problem: it is the
        # app recovering from one. It has to be PERMANENT while it lasts --
        # a transient message fades and leaves a still trace with nothing
        # explaining it, which is how the 2026-09-06 timeout read as a
        # freeze rather than as a fault being worked on.
        self.l_stall = QtWidgets.QLabel()
        self.l_stall.setStyleSheet("color:#ffb340;font-weight:bold;")
        # Amber, and permanent while it lasts, for the same reason as
        # l_stall: it says every level on screen is about 11 dB high, which
        # is not something a message that fades after six seconds may say.
        # The planner keeps the rate above FS_MIN_LINEAR on its own, so the
        # only way to see this is the generator pinning a low rate.
        self.l_fslow = QtWidgets.QLabel()
        self.l_fslow.setStyleSheet("color:#ffb340;font-weight:bold;")
        # Permanent while it applies, not a 6 s transient: a level corrected
        # by a number nobody measured is exactly the thing that has to stay
        # on screen for as long as it is true (bugs 43 and 52).
        self.l_calx = QtWidgets.QLabel()
        self.l_calx.setStyleSheet("color:#ffb340;")
        # Visible from anywhere in the window: if this board is radiating,
        # that should not be something you have to scroll a panel to find.
        self.l_tx = QtWidgets.QLabel("")
        self.l_tx.setStyleSheet("color:#ff453a;font-weight:bold;")
        # Recording, and PERMANENT while it runs, for bug 43's reason: the
        # one thing somebody who walked away needs to know is whether it is
        # still going, and a message that fades cannot tell them.
        #
        # Deliberately NOT red. Red in this window is reserved -- l_tx, the
        # overload and the damage warning are all red, and RF OUT is red
        # because "there is power on that connector" has to be unmistakable
        # across a bench (5.15). A recorder is not an alarm and must not
        # borrow the colour of one. A_MEASURE green says "this is producing
        # numbers", which is exactly what it is doing.
        self.l_rec = QtWidgets.QLabel("")
        self.l_rec.setStyleSheet(f"color:{A_MEASURE};font-weight:bold;")
        # The running macro, permanent while it runs, for the recorder's
        # reason: it is left running by somebody who is not watching.
        self.l_macro = QtWidgets.QLabel("")
        self.l_macro.setStyleSheet(f"color:{A_MEASURE};font-weight:bold;")
        for w in (self.l_rbw, self.l_rate, self.l_seg, self.l_ovl,
                  self.l_haz, self.l_stall, self.l_fslow, self.l_calx,
                  self.l_rec, self.l_macro, self.l_tx):
            sb.addPermanentWidget(w)
        # Two separate slots: the left one is for things the user did (and
        # must stay readable), the right one is the live per-frame state.
        # Writing both to one label meant every message vanished within 40 ms.
        self.l_mode = QtWidgets.QLabel("")
        sb.addPermanentWidget(self.l_mode)
        self.status = QtWidgets.QLabel(self.src.info)
        sb.addWidget(self.status, 1)
        self._msg_timer = QtCore.QTimer(self)
        self._msg_timer.setSingleShot(True)
        self._msg_timer.timeout.connect(lambda: self.status.setText(""))

        m = self.menuBar().addMenu("&File")
        m.addAction("Save trace as CSV...").triggered.connect(self._csv)
        m.addAction("Save picture...").triggered.connect(self._png)
        m.addSeparator()
        m.addAction("Quit").triggered.connect(self.close)
        h = self.menuBar().addMenu("&Help")
        h.addAction("About / version...").triggered.connect(self._about)

        self.plot.scene().sigMouseMoved.connect(self._mouse)
        self.plot.zoom.connect(self._on_zoom)
        self.plot.pan.connect(self._on_pan)
        self.plot.markerAt.connect(self._marker_at_hz)
        for keys, fn in (
                ("F12", self._shot_now),
                ("Space", lambda: self.b_run.setChecked(not self.b_run.isChecked())),
                ("1", lambda: self._set_rx(0)),
                ("2", lambda: self._set_rx(1)),
                ("3", lambda: self._set_rx(RX_BOTH)),
                ("P", self._peak),
                ("N", self._next_peak),
                ("R", self._set_ref),
                ("H", self._harmonics),
                ("T", self._tune_strongest),
                ("S", self._single_key),
                ("C", lambda: self._retune(
                    self.markers[self.cb_m.currentIndex()].f, self.cfg.span)
                    if self.markers[self.cb_m.currentIndex()].on else None),
                ("Left", lambda: self._nudge(-1)),
                ("Right", lambda: self._nudge(+1)),
                ("Up", lambda: self.s_ref.setValue(self.s_ref.value() + 5)),
                ("Down", lambda: self.s_ref.setValue(self.s_ref.value() - 5)),
        ):
            QtWidgets.QShortcut(QtGui.QKeySequence(keys), self, fn)
        self._axes()

    # ------------------------------------------------------------- popovers
    #
    # One panel open at a time, anchored beside its own key. Pressing a
    # different key switches to that panel rather than opening a second one,
    # which is what a front-panel menu does and also the only behaviour that
    # keeps the plot uncovered.

    def _toggle_panel(self, name: str) -> None:
        if self._panel_open == name:
            self._close_panel()
        else:
            self._open_panel(name)

    def _open_panel(self, name: str) -> None:
        # A key that is not on screen cannot be pressed, and its panel must
        # not be openable by any other route either. The generator's function
        # keys come and go with its switch, and opening one while the switch
        # is off would both anchor the panel to a hidden key -- putting it
        # over the top of the trace, since a hidden widget has no useful
        # position -- and un-hide the box it holds, which is how a box that
        # is supposed to be closed ends up on screen.
        if name in getattr(self, "_sg_keys", ()) and self.sg_keybar.isHidden():
            return
        # An ACTION key has no panel at all (Single, Restart -- see
        # build_keys). Refuse rather than KeyError: this method is reachable
        # from a keyboard shortcut and from a test as well as from a key
        # press, and "nothing to open" is a fact about the key, not a
        # programming mistake by the caller. A traceback here would also be
        # a worse way to find out than a key that quietly does nothing,
        # because it would take the whole window down mid-measurement.
        if name not in self._groups:
            return
        accent, boxes = self._groups[name]
        # Only the boxes that are actually meant to be showing. The
        # generator's own boxes are hidden while it is switched off, and a
        # hidden box in a popover would leave a blank panel with a title.
        show = [w for w in boxes if not w.isHidden() or w is boxes[0]]
        self.popover.take(show, accent)
        # A panel with no key of its own (the Measurement list's items)
        # lights the key it hangs from.
        anchor = getattr(self, "_panel_key", {}).get(name, name)
        for n, k in self.keys.items():
            k.setChecked(n == anchor)
        # ...and then put Single's lamp back, because that loop just cleared
        # it. Bug 39 covered _close_panel and stopped one step short: the
        # look is shared, so OPENING a panel hides the fact that the sweep
        # is held just as effectively as closing one did, and the key stays
        # on screen the whole time (the panel opens leftwards, 5.13). A held
        # sweep with an unlit Single is a still trace with nothing on the
        # front panel to explain it. Safe for the opened key too: Single is
        # an ACTION key with no panel of its own, so the two meanings of the
        # lit look can never collide on it.
        self._sync_act_keys()
        self._panel_open = name
        self._place_panel()
        self.popover.show()
        self.popover.raise_()

    def _close_panel(self) -> None:
        if self._panel_open is None:
            return
        # Back to the park, not to no parent: see the note where _park is
        # created. Hiding first stops a visible flicker as it is re-homed.
        self.popover.hide()
        for w in list(self.popover.content or []):
            w.setParent(self._park)
            self._park.layout().addWidget(w)
        self.popover.content = None
        for k in self.keys.values():
            k.setChecked(False)
        self._panel_open = None
        # Single's lamp is a MODE, not a panel -- put it back.
        self._sync_act_keys()

    def _place_panel(self) -> None:
        """
        Put the panel to the LEFT of its key, level with it, inside the
        window.

        Leftwards because the keys are down the right-hand edge, which is
        where the reference image has them: a panel opening the other way
        would fall off the window. Level with the key rather than centred
        on the panel area, so the eye goes from the key it pressed straight
        into the settings that key opened -- that association is the whole
        point of anchoring it rather than docking it.

        A tall panel is clamped to the window and scrolls internally. The
        alternative is a panel taller than the screen, which is what the old
        2800 px column effectively was.
        """
        if self._panel_open is None:
            return
        root = self.centralWidget()
        # Lay the window out NOW if anything is pending. A key that has just
        # been shown -- the generator's, the moment its switch is ticked --
        # still reports the position it had before, and placing a panel
        # against that puts it near the top of the window, over the trace.
        # Qt would fix the geometry on the next event loop pass, which is
        # too late for a panel being placed on this one.
        if root.layout() is not None:
            root.layout().activate()
        key = self.keys[getattr(self, "_panel_key", {}).get(
            self._panel_open, self._panel_open)]
        want = self.popover.wanted()
        # Width is whatever the content needs, bounded only by the window.
        # There is deliberately no tidy maximum: the horizontal scrollbar is
        # off, so a panel narrower than its content does not scroll, it
        # CLIPS, silently. That is the same fault the old right-hand column
        # hit at 318 px and again at 400 px -- twice, by two different
        # routes, both times unnoticed. A capped width here would have been
        # the third: the Measure panel measures 473 px, and a round 470
        # would have shaved 3 px off a checkbox label for the sake of a
        # neater constant. Vertical is different -- that one scrolls.
        w = min(max(want.width(), 300), max(root.width() - 12, 260))
        h = min(max(want.height(), 120), max(root.height() - 12, 160))
        # Left edge of the key column is the right edge of the panel. In a
        # window too narrow to fit the panel beside the keys it slides over
        # them instead of being narrowed, for the clipping reason above: a
        # covered key can be uncovered with Esc, a clipped control cannot be
        # got at at all.
        left = self.keypanel.mapTo(root, QtCore.QPoint(0, 0)).x()
        x = max(6, min(left - w - 6, root.width() - w - 6))
        # Level with the key, then pushed back inside the window if the
        # panel is taller than the room below that key. Anchoring the TOP is
        # what keeps a short panel next to its own key instead of drifting
        # to the middle of the column.
        y = max(6, min(key.mapTo(root, QtCore.QPoint(0, 0)).y(),
                       root.height() - h - 6))
        self.popover.setGeometry(x, y, w, h)

    def eventFilter(self, obj, ev):
        """
        Close the panel on a click anywhere that is not the panel or a key.

        Installed on the plot surfaces and the central widget -- see the
        note where that happens for why not on the application.

        The test is by GLOBAL position against the panel's rectangle rather
        than by walking the parent chain: a combo box's drop-down list is a
        separate top-level window, so it is not a child of the panel, and
        walking parents would close the panel the moment somebody picked an
        item from a list inside it.
        """
        if (self._panel_open is not None
                and ev.type() == QtCore.QEvent.MouseButtonPress):
            gp = ev.globalPos() if hasattr(ev, "globalPos") else None
            if gp is not None:
                inside = self.popover.rect().contains(
                    self.popover.mapFromGlobal(gp))
                on_key = any(k.rect().contains(k.mapFromGlobal(gp))
                             for k in self.keys.values())
                if not inside and not on_key:
                    self._close_panel()
        return super().eventFilter(obj, ev)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._place_panel()

    def _box_radio(self):
        b = Box("Radio", A_RADIO)
        self.l_src = QtWidgets.QLabel()
        self.l_src.setWordWrap(True)
        b.full(self.l_src)

        # Port selector. Both RX channels share the LO on the bladeRF 2.0, so
        # this changes which antenna port is sampled, not the tuning.
        prow = QtWidgets.QHBoxLayout()
        prow.setContentsMargins(0, 0, 0, 0)
        self.b_rx1 = QtWidgets.QPushButton("RX1")
        self.b_rx2 = QtWidgets.QPushButton("RX2")
        self.b_rxboth = QtWidgets.QPushButton("Both")
        for k, btn in ((0, self.b_rx1), (1, self.b_rx2), (RX_BOTH, self.b_rxboth)):
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c, kk=k: self._set_rx(kk))
            prow.addWidget(btn)
        self.b_rx1.setToolTip("Sample the RX1 port. Shortcut: 1")
        self.b_rx2.setToolTip("Sample the RX2 port. Shortcut: 2")
        self.b_rxboth.setToolTip(
            "Receive BOTH ports at once and draw both in the same window.\n"
            "Shortcut: 3\n\n"
            "The two are captured in the same instant (the radio's x2 mode),\n"
            "not swept one after the other, so levels and timing can be\n"
            "compared directly. Both ports share one LO on this board, so\n"
            "they are always on the same span.\n\n"
            "Markers, channel power, OBW and the spectrogram keep reading\n"
            "RX1 -- the RX2 curve is there to compare against.")
        self.b_rx1.setChecked(True)
        pw = QtWidgets.QWidget(); pw.setLayout(prow)
        b.row("Port", pw)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.b_conn = QtWidgets.QPushButton("Reconnect radio")
        self.b_conn.setToolTip("Look for the bladeRF again -- use this after\n"
                               "plugging it in, without restarting the app.")
        self.b_conn.clicked.connect(self._reconnect)
        self.b_sim = QtWidgets.QPushButton("Use simulator")
        self.b_sim.clicked.connect(self._use_sim)
        row.addWidget(self.b_conn)
        row.addWidget(self.b_sim)
        w = QtWidgets.QWidget()
        w.setLayout(row)
        b.full(w)

        self._show_src()
        return b

    def _box_sweep(self):
        """
        Acquisition control, the way a bench analyzer has it.

        Three things that are easy to confuse, so they live together rather
        than scattered down the panel:
          Continuous -- sweep over and over (what an SA does by default)
          Single     -- acquisition stops; each press of Sweep takes exactly
                        one sweep and stops again
          Freeze     -- the DISPLAY holds, but sweeping carries on
                        underneath, so resuming is instant
        Single is the one to use when you want the trace to stop changing
        because you are about to read numbers off it; Freeze is for
        grabbing a screenshot without losing the live rate.
        """
        b = Box("Sweep", A_RADIO)

        # Continuous / Single and the state readout. These went out to a key
        # of their own on 2026-08-30 and came back here on 2026-08-31, when
        # Kfir asked for Single to be a key you press rather than a panel
        # you open: "I don't need the inner popups -- just use them as a
        # regular button from the outside."
        #
        # So the Single KEY is now the one-shot action, and what is left to
        # live in a panel is the MODE -- which is a setting, and is also the
        # only way back to Continuous once Single has been pressed. A key
        # that fires a sweep cannot also be a two-state selector.
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.b_cont = QtWidgets.QPushButton("Continuous")
        self.b_single_mode = QtWidgets.QPushButton("Single")
        for btn, cont in ((self.b_cont, True), (self.b_single_mode, False)):
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c, k=cont: self._set_continuous(k))
            row.addWidget(btn)
        self.b_cont.setChecked(True)
        self.b_cont.setToolTip("Sweep repeatedly -- the normal running state.")
        self.b_single_mode.setToolTip(
            "Stop sweeping and hold what is on screen. Same as pressing\n"
            "the Single key, which also lights up while it is held.\n"
            "Markers, span and everything else stay usable.\n\n"
            "Sweep now takes one more sweep without releasing the hold.\n\n"
            "Different from Freeze below, which holds only the DISPLAY\n"
            "while the radio carries on sweeping underneath.")
        w = QtWidgets.QWidget(); w.setLayout(row)
        b.row("Mode", w)

        # One more sweep WITHOUT releasing the hold. This is what the Single
        # key used to do on a second press, and it had to go somewhere when
        # that key became a toggle on 2026-09-01: accumulating Max Hold or
        # Average one burst at a time is a real use and losing it would have
        # been a worse bug than the one being fixed.
        self.b_sweep_now = QtWidgets.QPushButton("Sweep now")
        self.b_sweep_now.setToolTip(
            "Take one more sweep and hold it again.\n\n"
            "Unlike the Single key, this does not release the hold -- so\n"
            "press it repeatedly to build up a Max Hold or an Average one\n"
            "sweep at a time, which is how you catch a burst.\n\n"
            "In Continuous it switches to Single and takes one sweep.")
        self.b_sweep_now.clicked.connect(self._single_sweep)
        b.row("Once", self.b_sweep_now)

        self.l_sweep = QtWidgets.QLabel("continuous")
        self.l_sweep.setStyleSheet("color:#8fa6bb;")
        b.row("State", self.l_sweep)

        # Sweep time. Auto is the default and is what the sweep naturally
        # takes; manual paces it slower, which is what you want when a signal
        # is drifting or a DUT is stepping through states and the display is
        # racing past it. It cannot make a sweep faster -- nothing can, short
        # of a narrower span or a coarser RBW -- and the measured figure is
        # shown beside it so the two can be compared rather than assumed.
        self.c_swauto = QtWidgets.QCheckBox("Auto sweep time")
        self.c_swauto.setChecked(True)
        self.c_swauto.setToolTip(
            "On: sweep as fast as the span, the RBW and the retunes allow.\n"
            "Off: pace each sweep to the time set below.\n\n"
            "Manual can only slow a sweep down. If the sweep already takes\n"
            "longer than the time asked for, the measured figure says so\n"
            "rather than the setting pretending otherwise.")
        self.c_swauto.toggled.connect(self._sweep_auto)
        b.full(self.c_swauto)

        self.s_swtime = QtWidgets.QDoubleSpinBox()
        self.s_swtime.setRange(1.0, 30000.0)
        self.s_swtime.setDecimals(1)
        self.s_swtime.setValue(50.0)
        self.s_swtime.setSuffix(" ms")
        self.s_swtime.setEnabled(False)
        self.s_swtime.valueChanged.connect(
            lambda v: self._push(sweep_time=v * 1e-3))
        b.row("Sweep time", self.s_swtime)

        self.l_swmeas = QtWidgets.QLabel("--")
        self.l_swmeas.setToolTip(
            "What a sweep is actually taking, measured from the sweep rate.")
        b.row("Measured", self.l_swmeas)

        self.b_run = QtWidgets.QPushButton("Freeze")
        self.b_run.setCheckable(True)
        self.b_run.setToolTip("Hold the DISPLAY so a trace can be read or\n"
                              "screenshotted without it moving. Sweeping\n"
                              "carries on underneath, so resuming is instant.\n"
                              "For a real one-shot, use Single. Shortcut: space")
        self.b_run.toggled.connect(self._set_frozen)
        b.full(self.b_run)

        note = QtWidgets.QLabel(
            "Continuous / Single and the one-shot Sweep button are under "
            "the Single key.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#8fa6bb;")
        b.full(note)
        return b

    def _restart_meas(self) -> None:
        """
        The Restart key. Kept separate from _clear() because _clear is also
        the "Clear traces" button under Display, and that one must not
        un-freeze the display or fire a sweep -- clearing what is drawn and
        restarting the measurement are two different asks that happen to
        share most of their work.
        """
        self._clear()
        # A restart that leaves the display frozen looks like a dead button:
        # everything behind it started again and nothing on screen moved.
        if self._frozen:
            self.b_run.setChecked(False)
        if not self.cfg.continuous:
            # In Single the sweep is stopped, so without this the traces
            # would sit empty until the next press of Sweep.
            self._single_sweep()
        self._message("measurement restarted -- traces, averaging and the "
                      "spectrogram cleared")

    def _set_continuous(self, on: bool) -> None:
        self.b_cont.setChecked(bool(on))
        self.b_single_mode.setChecked(not on)
        # There was a b_sweep.setEnabled(not on) here until 2026-08-31. The
        # one-shot button it referred to is now the Single KEY, which stays
        # enabled in both modes on purpose: pressing it while continuous is
        # a perfectly good way to say "I want one sweep", and _single_sweep
        # switches the mode itself rather than ignoring the press.
        self._push(continuous=bool(on))
        self._sync_act_keys()
        if on:
            self.l_sweep.setText("continuous")
            self._message("sweeping continuously")
        else:
            self.l_sweep.setText("single -- held, Single again for continuous")
            self._message("held on one sweep -- press Single (or S) again to "
                          "sweep continuously")

    def _single_key(self) -> None:
        """
        The Single KEY: hold one sweep, or release the hold.

        Deliberately not the same thing as _single_sweep(), which takes one
        more sweep and is what Restart and the Sweep now button call. The
        key is a two-state control because that is what Kfir expected of it
        (2026-09-01) and because it is the only Single on screen -- the mode
        selector is a panel away, so a key that could enter a state it
        could not leave was a trap rather than a shortcut.
        """
        if self.cfg.continuous:
            self._single_sweep()          # switches the mode itself
        else:
            self._set_continuous(True)

    def _sync_act_keys(self) -> None:
        """
        Re-light action keys that carry a state.

        _close_panel() unchecks EVERY key, because a key's checked look is
        how an open panel is shown. Single now also uses that look to mean
        "the sweep is held", so opening and closing any other panel would
        quietly put its lamp out while the sweep stayed held -- the display
        frozen with nothing on the panel saying why. Called from
        _close_panel, from _open_panel and from _set_continuous -- the
        three things that can move either state. _open_panel was missing
        until 2026-09-06 (bug 48): it checks the key it is opening and
        unchecks every other one, which is the same clearing _close_panel
        does, so the lamp went out for as long as any panel was open.
        """
        keys = getattr(self, "keys", {})
        k = keys.get("Single")
        if k is not None:
            k.setChecked(not self.cfg.continuous)
        # Signal Generator is the second key of this shape, from
        # 2026-09-16: its lamp means "the generator is open", and the
        # clearing loops in _open_panel and _close_panel would put it out
        # at every panel change exactly as they did Single's (bug 48).
        k = keys.get("Generator")
        if k is not None:
            k.setChecked(bool(getattr(self, "_sg_open", False)))
        # And the preset slots, from 2026-09-17: a slot's lamp means "this
        # is what the instrument is set to", which is the same two-meaning
        # look bug 48 is about. Only ONE can be lit, and only while nothing
        # has been changed since the recall -- see _slot_dirty.
        cur = getattr(self, "_slot_cur", None)
        dirty = bool(getattr(self, "_slot_dirty", True))
        for i in range(N_PRESETS):
            k = keys.get(f"Preset {i + 1}")
            if k is not None:
                k.setChecked(cur == i and not dirty)
        # And Record, from 2026-09-20 -- the fourth key of this shape. It
        # carries the state in its FACE as well as its lamp, so the
        # re-engraving belongs here too rather than in _rec_start/_rec_stop:
        # one place that states both, called from the three things that can
        # move either, is what stops the pair drifting apart.
        k = keys.get("Record")
        if k is not None:
            on = getattr(self, "rec", None) is not None
            k.setChecked(on)
            want = REC_LABEL_ON if on else REC_LABEL_OFF
            if k.text() != want:
                k.set_label(want)

    def _single_sweep(self) -> None:
        if getattr(self, "worker", None) is None:
            return
        if self.cfg.continuous:
            # Pressing Sweep while running is a reasonable way to say "I
            # want one sweep" -- switch to Single rather than ignore it.
            self._set_continuous(False)
        self.l_sweep.setText("sweeping ...")
        self.worker.request_single()

    @QtCore.pyqtSlot()
    def _on_sweep_done(self) -> None:
        if not self.cfg.continuous:
            self.l_sweep.setText("single -- held, Single again for continuous")

    def _set_rx(self, index: int) -> None:
        index = int(index) if int(index) in (0, 1, RX_BOTH) else 0
        # The port is part of a stored setup and does not go through
        # _push(), so it has to say so itself.
        if not self._quiet and index != getattr(self, "rx_ch", None):
            self._slot_touched()
        self.rx_ch = index
        self._sync_rx_buttons(index)
        self._restart_reopen_factory()
        self.tdata = []
        self._wf_buf = None
        if getattr(self, "worker", None) is not None:
            # The worker owns the device, so it does the switch.
            self.worker.request_channel(index)
        else:
            self.src.set_channel(index)
            self._show_src()
        self._message("switching to both RX ports ..." if index == RX_BOTH
                      else f"switching to RX{index + 1} ...")

    def _sync_rx_buttons(self, index: int) -> None:
        self.b_rx1.setChecked(index == 0)
        self.b_rx2.setChecked(index == 1)
        self.b_rxboth.setChecked(index == RX_BOTH)
        # The tags and the lower graticule only mean anything in Both.
        both = index == RX_BOTH and not self.cfg.zero_span
        for item in (self.rx1_tag, self.rx2_tag):
            item.setVisible(both)
        # Up on the BUTTON, not on the first two-port sweep -- see _draw_rx2
        # for why that changed. Empty is a legal state here and says so.
        self._show_rx2(both)
        if both:
            self._rx2_note(self.last is None or self.last.power2 is None)

    @QtCore.pyqtSlot(int)
    def _on_port(self, index: int) -> None:
        self.rx_ch = index
        self.tdata = []
        self._wf_buf = None
        self._show_src()
        # RX_BOTH is 2, so "RX{index+1}" announced RX3 -- a port this board
        # does not have. Cosmetic, and it had been there since Both was added.
        self._message("now sampling RX1 and RX2 together"
                      if index == RX_BOTH else f"now sampling RX{index + 1}")

    def _restart_reopen_factory(self) -> None:
        """Auto-reconnect must come back on the port that is selected."""
        if getattr(self, "worker", None) is None:
            return
        if isinstance(self.src, BladeRF):
            ch = self.rx_ch
            self.worker.reopen = lambda: BladeRF(channel=ch)
        else:
            self.worker.reopen = None

    def _message(self, text: str, seconds: float = 6.0) -> None:
        """Show something the user did, and let it linger long enough to read."""
        self.status.setText(text)
        self._msg_timer.start(int(seconds * 1000))

    def _nudge(self, direction: int) -> None:
        """Arrow keys step the centre by a tenth of a span, like a front panel."""
        if self.cfg.zero_span:
            return
        self._retune(self.cfg.center + direction * self.cfg.span / 10.0,
                     self.cfg.span)

    def _set_frozen(self, on: bool) -> None:
        self.b_run.setText("Running - click to freeze" if not on
                           else "FROZEN - click to resume")
        self._frozen = on
        if on:
            self._message("display frozen")
        else:
            self._message("running")

    def _show_src(self) -> None:
        real = isinstance(self.src, BladeRF)
        self.l_src.setText(self.src.info)
        ch = getattr(self.src, "channel", 0)
        self._sync_rx_buttons(ch)
        self.l_src.setStyleSheet("color:#7CFF6B;" if real else "color:#ffd24a;")
        self.b_sim.setEnabled(real)
        self.b_conn.setText("Reconnect radio" if real else "Connect radio")
        self._health_refresh()
        if getattr(self, "l_sg_why", None) is not None:
            self._sg_refresh_support()

    # ---------------------------------------------------------- board health
    def _box_health(self):
        """
        Board health and self-test (section 5.3).

        Every control here is soft-bound: which of these actually work
        depends on the installed driver version, and on Simulated they are
        all fabricated numbers labelled "(simulated)". A control that has
        nothing behind it is disabled with the reason in its tooltip rather
        than left to fail when clicked.
        """
        b = Box("Board Health / Self-Test", A_RADIO)
        self.l_versions = QtWidgets.QLabel("--")
        self.l_versions.setWordWrap(True)
        self.l_versions.setStyleSheet("color:#8fa6bb;")
        b.full(self.l_versions)

        self.bh_refresh = QtWidgets.QPushButton("Read health")
        self.bh_refresh.setToolTip(
            "Library/firmware/FPGA versions, RFIC temperature, power "
            "source, PMIC bus voltage and current, RFIC RSSI. A handful of "
            "one-off reads, not something to poll every frame.")
        self.bh_refresh.clicked.connect(self._health_refresh)
        b.full(self.bh_refresh)

        self.l_temp = QtWidgets.QLabel("--")
        b.row("RFIC temp", self.l_temp)
        self.l_power = QtWidgets.QLabel("--")
        b.row("Power source", self.l_power)
        self.l_pmic = QtWidgets.QLabel("--")
        b.row("Bus V / current", self.l_pmic)
        self.l_rssi = QtWidgets.QLabel("--")
        self.l_rssi.setToolTip("As the RFIC reports it -- not a calibrated "
                               "dBm figure, see the amplitude note.")
        b.row("RFIC RSSI", self.l_rssi)

        return b

    def _health_refresh(self) -> None:
        """
        Kick off a health read on the worker thread and return -- self.src
        is owned by that thread's sweep loop, and an earlier version of
        this called self.src.<method>() straight from the GUI thread. That
        raced the sweep loop's own start()/stop()/read() calls: it is what
        made the counter self-test fail with "stream config" and made the
        loopback toggle hang. _on_health_result() below fills the labels in
        once the reply comes back.
        """
        if not hasattr(self, "l_versions"):
            return    # _box_radio()'s _show_src() can fire before _box_health() exists
        if getattr(self, "worker", None) is None:
            return    # called again once _start() exists, from _start() itself
        self.worker.request_health_read()

    @QtCore.pyqtSlot(dict)
    def _on_health_result(self, data: dict) -> None:
        self.l_versions.setText(
            f"lib {data['lib_version']}  fw {data['fw_version']}  "
            f"fpga {data['fpga_version']}")

        t = data["temp"]
        self.l_temp.setText(f"{t:.1f} °C" if t is not None else "n/a")

        self.l_power.setText(POWER_SOURCE_NAMES.get(data["power_source"], "n/a"))

        bus_v, cur = data["bus_v"], data["current"]
        self.l_pmic.setText(
            f"{bus_v:.2f} V / {cur:.2f} A" if bus_v is not None and cur is not None
            else "n/a")

        rssi = data["rssi"]
        self.l_rssi.setText(f"pre {rssi[0]:.0f}  sym {rssi[1]:.0f}"
                            if rssi is not None else "n/a")



    # ======================================================================
    #  Signal generator panel (Phase 2)
    #
    #  Two separate switches on purpose:
    #    "Signal generator" reveals the controls and costs nothing.
    #    "RF OUT" is what actually puts power on the connector.
    #  Showing someone the panel should never key a transmitter, and the one
    #  that does key it is red and says so.
    # ======================================================================

    def _sg_key(self) -> None:
        """
        The Signal Generator KEY: open the generator, or put it away.

        It was a panel holding one tick-box until 2026-09-16, and Kfir
        removed it -- "the Signal Generator button opens a pop up, that
        needs to be checked to see the SG features. We can skip this
        popup. I want the SG features to open as I press the button
        only." Same call he made for Single and Restart on 2026-08-31
        (5.24), and right for the same reason: a panel holding one
        control is a click in front of a click.

        So this is an ACTION key that carries a state, like Single, and
        the state is the `_sg_open` flag rather than a hidden checkbox.
        A widget nothing can see is a second place for the truth to
        live -- 5.15's third shape -- and the key IS the control now, so
        the flag beside it is the whole of it. What had to move with the
        box is the "No transmitter available" label, which sits in the
        RF OUT box now, next to the button whose disabling it explains.

        Pressing this still transmits NOTHING. RF OUT, behind the first
        red key it reveals, is the one that does: the two-switch split
        this section opens with is exactly as it was.
        """
        self._sg_toggle_panel(not self._sg_open)

    def _box_sg_head(self):
        """Top of the generator's own column: which port, the key, and what
        it is doing right now."""
        b = Box("Signal Generator", A_TX)
        prow = QtWidgets.QHBoxLayout()
        prow.setContentsMargins(0, 0, 0, 0)
        self.b_tx1 = QtWidgets.QPushButton("TX1")
        self.b_tx2 = QtWidgets.QPushButton("TX2")
        self.b_txboth = QtWidgets.QPushButton("Both")
        self.b_tx1.setToolTip("Transmit on the TX1 port only.")
        self.b_tx2.setToolTip("Transmit on the TX2 port only.")
        self.b_txboth.setToolTip(
            "Transmit the same waveform out of BOTH ports at once, using the\n"
            "radio's 2x2 MIMO transmit mode.\n\n"
            "Both ports share one LO and one sample clock, so they are\n"
            "coherent -- same frequency, and a fixed phase relationship\n"
            "rather than a drifting one. That is what makes it useful for\n"
            "feeding two inputs of a DUT, driving two antennas, or checking\n"
            "one path against the other.\n\n"
            "Gain is applied to both ports together.")
        for k, btn in ((0, self.b_tx1), (1, self.b_tx2), (TX_BOTH, self.b_txboth)):
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c, kk=k: self._sg_set_port(kk))
            prow.addWidget(btn)
        self.b_tx1.setChecked(True)
        pw = QtWidgets.QWidget(); pw.setLayout(prow)
        self.w_sg_port = b.row("Port", pw)

        # "No transmitter available: ...". It lived in the Generator
        # panel's own box until 2026-09-16, and that box went with the
        # tick-box it held (see _sg_key). This is where it belonged
        # anyway: beside the button whose disabling it explains.
        self.l_sg_why = QtWidgets.QLabel("")
        self.l_sg_why.setWordWrap(True)
        self.l_sg_why.setStyleSheet("color:#ffd24a;")
        self.l_sg_why.setVisible(False)
        b.full(self.l_sg_why)

        self.b_sg_out = QtWidgets.QPushButton("RF OUT is OFF")
        self.b_sg_out.setCheckable(True)
        self.b_sg_out.setMinimumHeight(26)
        self.b_sg_out.setStyleSheet(key_qss(A_TX))
        self.b_sg_out.setToolTip(
            "Key the transmitter.\n\n"
            "Make sure something is actually connected to the TX port --\n"
            "an antenna, a DUT, or a 50 ohm load. And make sure you are\n"
            "allowed to radiate on the frequency you have set.")
        self.b_sg_out.toggled.connect(self._sg_toggle_out)
        b.full(self.b_sg_out)

        self.l_sg_state = QtWidgets.QLabel("idle")
        self.l_sg_state.setWordWrap(True)
        self.l_sg_state.setStyleSheet("color:#8fa6bb;")
        b.full(self.l_sg_state)
        return b

    def _box_sg_out(self):
        """
        What is being sent: a frequency and an amplitude, and nothing else.

        Kfir asked for exactly that on 2026-08-20, and he is right -- it is
        what a bench signal generator puts on its front panel. The gain, the
        digital backoff, the sample rate and the cal offset are how an
        amplitude gets reached, not what it is, so they moved out of here.
        Sample rate went to the waveform box, which is what it actually
        bounds, and 'Output at' next to the offset that makes it differ. The
        gain, the backoff and the dither control have since gone altogether
        (2026-08-25) -- see _box_sg_lvl, which is what is left of that box.

        The plan line stays, greyed. It is the only thing that says when the
        amplitude asked for cannot be reached -- and a box showing -80 dBm
        while the transmitter delivers -60 would be bug 24 again, in a new
        place.

        The two 'point it at the analyzer' buttons are gone at his request.
        """
        b = Box("SG Output", A_TX)
        self.e_sg_freq = HzEdit(self.sg.freq)
        self.e_sg_freq.setToolTip("Where the TX local oscillator sits. The\n"
                                  "waveform is placed at 'Offset' from here.")
        self.e_sg_freq.changed.connect(lambda v: self._sg_push(freq=v))
        b.row("TX frequency", self.e_sg_freq)

        self.s_sg_level = QtWidgets.QDoubleSpinBox()
        # Top end is what the board can do with no backoff at all; the bottom
        # is where gain has bottomed out and 60 dB of digital backoff has
        # been spent on top of it. Outside that the solver says so rather
        # than silently running out.
        self.s_sg_level.setRange(-140, 20)
        self.s_sg_level.setDecimals(1)
        self.s_sg_level.setSingleStep(1.0)
        self.s_sg_level.setValue(self.sg.level_dbm)
        self.s_sg_level.setSuffix(" dBm")
        self.s_sg_level.setToolTip(
            "The average output power you want, at the port.\n\n"
            + SG_LEVEL_PLAN_NOTE + "\n\n" + SG_LEVEL_NOTE)
        self.s_sg_level.valueChanged.connect(
            lambda v: self._sg_push_level(v))
        b.row("Amplitude", self.s_sg_level)

        # What the solver did, in one line: the gain and backoff it picked,
        # and a warning in amber when the level asked for is off the end of
        # what the transmitter can do. Silence there would mean running at
        # the wrong level and blaming the DUT.
        self.l_sg_plan = QtWidgets.QLabel("")
        self.l_sg_plan.setWordWrap(True)
        self.l_sg_plan.setToolTip(SG_LEVEL_PLAN_NOTE)
        self.l_sg_plan.setStyleSheet("color:#8fa6bb;")
        b.full(self.l_sg_plan)

        return b

    def _box_sg_lvl(self):
        """
        The one calibration number the transmitter has.

        Was "SG amplitude detail" and held six more controls: Set by, TX
        gain, Digital backoff, Dither, Est. output and the estimate note.
        All removed on request, 2026-08-25. What has to be understood before
        putting any of them back:

          * the MODEL is all still here. "Set by" is gone because there is
            only one way to set a level now -- say what you want out --
            which is what a signal generator does; SgConfig.level_direct
            stays True and _sg_replan_level() still solves it into a gain
            and a backoff and pushes both. They are outputs, and an output
            does not need a spin box.
          * DITHER IS NOW PERMANENTLY ON, by request, and the checkbox is
            gone rather than greyed. That is the right default and always
            was -- bug 23 measured the quantisation spurs it removes at
            -62.5 dBc on the board, down to -95.4 with it on -- but "always
            on" now has to be true of the CONFIG, not of a widget nobody can
            see: SgConfig.dither defaults True and every recall forces it,
            so a setup saved before today cannot bring a False back.
          * the achieved level still reaches the user, in the plan line in
            SG output, which is where the amplitude that was asked for is.
            That line was deliberately kept in that box (5.11) for exactly
            this reason.
        """
        b = Box("Amplitude Cal", A_TX)
        self.s_sg_cal = QtWidgets.QDoubleSpinBox()
        self.s_sg_cal.setRange(-80, 80)
        self.s_sg_cal.setSuffix(" dB")
        self.s_sg_cal.setToolTip(
            "Measure the real output once with a power meter, or through\n"
            "a known attenuator into this analyzer, and set this until\n"
            "the estimate agrees. Same idea as the RX cal offset.")
        self.s_sg_cal.valueChanged.connect(self._sg_push_cal)
        b.row("TX cal offset", self.s_sg_cal)

        # Shown only while the RFIC loopback is routing TX back into RX.
        # Without it, changing the gain and seeing absolutely nothing happen
        # looks exactly like a broken control -- which is how it was
        # reported. Measured: through that loopback the level does not move
        # with gain at all, and barely moves with digital backoff until the
        # signal finally drops out of compression.
        self.l_sg_lbwarn = QtWidgets.QLabel(
            "The RFIC loopback is ON, so amplitude changes will NOT show up, "
            "and this cal offset cannot be calibrated through it. The "
            "loopback taps the transmitter digitally, ahead of the gain "
            "stages, and it runs into compression. The amplitude really is "
            "reaching the hardware -- you just cannot see it here. Turn the "
            "loopback off and measure through a cable.")
        self.l_sg_lbwarn.setWordWrap(True)
        self.l_sg_lbwarn.setStyleSheet("color:#ffd24a;")
        self.l_sg_lbwarn.setVisible(False)
        b.full(self.l_sg_lbwarn)

        return b

    def _sg_refresh_loopback_note(self) -> None:
        # Tracks _lb_on, not a checkbox. The user-facing "RFIC BIST loopback"
        # tick is gone, but the loopback itself is not -- the SG self-check
        # still switches it in and out, and fact 7 of section 5.5 (the level
        # does not move with gain through that digital tap) is exactly as
        # true when the self-check turned it on as when a user did. So the
        # warning has to follow the DEVICE state, which is what _lb_on is.
        w = getattr(self, "l_sg_lbwarn", None)
        if w is not None:
            w.setVisible(bool(getattr(self, "_lb_on", False)))

    def _box_sg_wave(self):
        b = Box("SG Waveform", A_TX)
        self.cb_sg_wave = QtWidgets.QComboBox()
        self.cb_sg_wave.addItems(SG_WAVEFORMS)
        self.cb_sg_wave.setToolTip(
            "What to transmit. Between them these cover what the VSG60A\n"
            "offers: carriers, tones and combs, noise, sweeps, pulses,\n"
            "analog modulation, digital modulation, and playback of a\n"
            "recorded file.")
        self.cb_sg_wave.currentTextChanged.connect(self._sg_wave_changed)
        b.row("Waveform", self.cb_sg_wave)

        self.s_sg_fs = QtWidgets.QDoubleSpinBox()
        self.s_sg_fs.setRange(0.52, 61.44)
        self.s_sg_fs.setDecimals(3)
        self.s_sg_fs.setValue(self.sg.fs / 1e6)
        self.s_sg_fs.setSuffix(" MS/s")
        self.s_sg_fs.setToolTip(
            "Baseband sample rate, so the widest the waveform can be.\n"
            "The VSG60A this replaces tops out at 51.2 MS/s; this board\n"
            "reaches 61.44 MS/s.\n\n"
            "Measured on this board: RX and TX CANNOT hold different\n"
            "sample rates -- setting either one drags the other, both\n"
            "ways, because one baseband PLL feeds both directions. So\n"
            "while RF OUT is on, the analyzer follows this number instead\n"
            "of picking its own from the span. Wide spans still work;\n"
            "they simply take more segments to stitch.")
        self.s_sg_fs.valueChanged.connect(lambda v: self._sg_push(fs=v * 1e6))
        b.row("Sample rate", self.s_sg_fs)

        self.e_sg_off = HzEdit(self.sg.offset)
        self.e_sg_off.setToolTip(
            "Where the signal sits inside the baseband, relative to the TX\n"
            "frequency.\n\n"
            "ZERO by default, because zero is the clean setting: the\n"
            "carrier sits on the LO, so LO leakage lands underneath it\n"
            "instead of beside it, there is no quadrature image, and a\n"
            "tone at DC is a constant that quantises exactly.\n\n"
            "Move it off zero and the artifacts separate out and become\n"
            "visible. That is sometimes exactly what you want -- it is how\n"
            "you measure LO leakage and image rejection -- but they are\n"
            "not the DUT. Hover 'Spurs at' for where each one lands.\n\n"
            + SG_SPUR_NOTE)
        self.e_sg_off.changed.connect(self._sg_push_offset)
        b.row("Offset", self.e_sg_off)

        self.l_sg_spurs = QtWidgets.QLabel("none -- offset is 0")
        self.l_sg_spurs.setWordWrap(True)
        self.l_sg_spurs.setStyleSheet("color:#8fa6bb;")
        self.l_sg_spurs.setToolTip(SG_SPUR_NOTE)
        b.row("Spurs at", self.l_sg_spurs)

        self.l_sg_actual = QtWidgets.QLabel("--")
        self.l_sg_actual.setStyleSheet("color:#7CFF6B;")
        self.l_sg_actual.setToolTip(
            "The frequency actually coming out: TX frequency + the offset\n"
            "that could really be synthesised. They differ only when the\n"
            "requested offset cannot be made to fit a whole number of\n"
            "cycles in the loop buffer -- and then this is the true one.")
        b.row("Output at", self.l_sg_actual)

        self.e_sg_spacing = HzEdit(self.sg.tone_spacing)
        self.e_sg_spacing.changed.connect(
            lambda v: self._sg_push(tone_spacing=v))
        b.row("Tone spacing", self.e_sg_spacing)

        self.s_sg_ntones = QtWidgets.QSpinBox()
        self.s_sg_ntones.setRange(1, 512)
        self.s_sg_ntones.setValue(self.sg.n_tones)
        self.s_sg_ntones.setToolTip(
            "Newman phases are used across the comb, so the crest factor\n"
            "stays near 2.5 dB instead of the 10*log10(N) it would be if\n"
            "every tone peaked together.")
        self.s_sg_ntones.valueChanged.connect(lambda v: self._sg_push(n_tones=v))
        b.row("Tones", self.s_sg_ntones)

        self.e_sg_noisebw = HzEdit(self.sg.noise_bw)
        self.e_sg_noisebw.setToolTip("0 = noise across the whole sample rate.")
        self.e_sg_noisebw.changed.connect(lambda v: self._sg_push(noise_bw=v))
        b.row("Noise BW", self.e_sg_noisebw)

        self.e_sg_swspan = HzEdit(self.sg.sweep_span)
        self.e_sg_swspan.changed.connect(lambda v: self._sg_push(sweep_span=v))
        b.row("Sweep span", self.e_sg_swspan)

        self.s_sg_swtime = QtWidgets.QDoubleSpinBox()
        self.s_sg_swtime.setRange(0.01, 1000.0)
        self.s_sg_swtime.setDecimals(3)
        self.s_sg_swtime.setValue(self.sg.sweep_time * 1e3)
        self.s_sg_swtime.setSuffix(" ms")
        self.s_sg_swtime.valueChanged.connect(
            lambda v: self._sg_push(sweep_time=v * 1e-3))
        b.row("Sweep time", self.s_sg_swtime)

        self.s_sg_swsteps = QtWidgets.QSpinBox()
        self.s_sg_swsteps.setRange(2, 4096)
        self.s_sg_swsteps.setValue(self.sg.sweep_steps)
        self.s_sg_swsteps.valueChanged.connect(
            lambda v: self._sg_push(sweep_steps=v))
        b.row("Steps", self.s_sg_swsteps)

        self.s_sg_pper = QtWidgets.QDoubleSpinBox()
        self.s_sg_pper.setRange(0.001, 1000.0)
        self.s_sg_pper.setDecimals(3)
        self.s_sg_pper.setValue(self.sg.pulse_period * 1e3)
        self.s_sg_pper.setSuffix(" ms")
        self.s_sg_pper.setToolTip("Pulse repetition interval.")
        self.s_sg_pper.valueChanged.connect(
            lambda v: self._sg_push(pulse_period=v * 1e-3))
        b.row("PRI", self.s_sg_pper)

        self.s_sg_pwid = QtWidgets.QDoubleSpinBox()
        self.s_sg_pwid.setRange(0.01, 1e6)
        self.s_sg_pwid.setDecimals(2)
        self.s_sg_pwid.setValue(self.sg.pulse_width * 1e6)
        self.s_sg_pwid.setSuffix(" us")
        self.s_sg_pwid.valueChanged.connect(
            lambda v: self._sg_push(pulse_width=v * 1e-6))
        b.row("Pulse width", self.s_sg_pwid)

        self.s_sg_prise = QtWidgets.QDoubleSpinBox()
        self.s_sg_prise.setRange(0.0, 1e5)
        self.s_sg_prise.setDecimals(2)
        self.s_sg_prise.setValue(self.sg.pulse_rise * 1e6)
        self.s_sg_prise.setSuffix(" us")
        self.s_sg_prise.setToolTip(
            "Raised-cosine edges. A hard-keyed pulse splatters across the\n"
            "band; a microsecond of shaping cuts the skirts right down.")
        self.s_sg_prise.valueChanged.connect(
            lambda v: self._sg_push(pulse_rise=v * 1e-6))
        b.row("Edge time", self.s_sg_prise)

        self.s_sg_amdepth = QtWidgets.QDoubleSpinBox()
        self.s_sg_amdepth.setRange(0, 100)
        self.s_sg_amdepth.setValue(self.sg.am_depth)
        self.s_sg_amdepth.setSuffix(" %")
        self.s_sg_amdepth.valueChanged.connect(
            lambda v: self._sg_push(am_depth=v))
        b.row("AM depth", self.s_sg_amdepth)

        self.e_sg_fmdev = HzEdit(self.sg.fm_dev)
        self.e_sg_fmdev.changed.connect(lambda v: self._sg_push(fm_dev=v))
        b.row("FM deviation", self.e_sg_fmdev)

        self.s_sg_pmdev = QtWidgets.QDoubleSpinBox()
        self.s_sg_pmdev.setRange(0.0, 100.0)
        self.s_sg_pmdev.setDecimals(3)
        self.s_sg_pmdev.setValue(self.sg.pm_dev)
        self.s_sg_pmdev.setSuffix(" rad")
        self.s_sg_pmdev.valueChanged.connect(lambda v: self._sg_push(pm_dev=v))
        b.row("PM deviation", self.s_sg_pmdev)

        self.e_sg_modrate = HzEdit(self.sg.mod_rate)
        self.e_sg_modrate.changed.connect(lambda v: self._sg_push(mod_rate=v))
        b.row("Mod rate", self.e_sg_modrate)

        self.e_sg_symrate = HzEdit(self.sg.sym_rate)
        self.e_sg_symrate.setToolTip(
            "Symbol rate. It has to divide the sample rate a whole number\n"
            "of times, so the nearest achievable one is used and shown in\n"
            "the detail line below.")
        self.e_sg_symrate.changed.connect(lambda v: self._sg_push(sym_rate=v))
        b.row("Symbol rate", self.e_sg_symrate)

        self.cb_sg_order = QtWidgets.QComboBox()
        for o in (2, 4, 8, 16, 32, 64, 256, 1024):
            self.cb_sg_order.addItem(str(o), o)
        self.cb_sg_order.setCurrentText("4")
        self.cb_sg_order.currentIndexChanged.connect(
            lambda _i: self._sg_push(mod_order=self.cb_sg_order.currentData()))
        b.row("Order (M)", self.cb_sg_order)

        self.s_sg_alpha = QtWidgets.QDoubleSpinBox()
        self.s_sg_alpha.setRange(0.01, 1.0)
        self.s_sg_alpha.setSingleStep(0.05)
        self.s_sg_alpha.setValue(self.sg.rrc_alpha)
        self.s_sg_alpha.setToolTip("Root raised cosine roll-off.")
        self.s_sg_alpha.valueChanged.connect(
            lambda v: self._sg_push(rrc_alpha=v))
        b.row("RRC alpha", self.s_sg_alpha)

        self.e_sg_fskdev = HzEdit(self.sg.fsk_dev)
        self.e_sg_fskdev.setToolTip(
            "0 = automatic, giving modulation index h = 0.5 -- the same\n"
            "as MSK and Bluetooth GFSK.")
        self.e_sg_fskdev.changed.connect(lambda v: self._sg_push(fsk_dev=v))
        b.row("FSK deviation", self.e_sg_fskdev)

        self.s_sg_bt = QtWidgets.QDoubleSpinBox()
        self.s_sg_bt.setRange(0.05, 2.0)
        self.s_sg_bt.setSingleStep(0.05)
        self.s_sg_bt.setValue(self.sg.gmsk_bt)
        self.s_sg_bt.setToolTip("Gaussian BT. Bluetooth uses 0.5, GSM 0.3.")
        self.s_sg_bt.valueChanged.connect(lambda v: self._sg_push(gmsk_bt=v))
        b.row("GMSK BT", self.s_sg_bt)

        self.cb_sg_prbs = QtWidgets.QComboBox()
        self.cb_sg_prbs.addItems(list(PRBS_TAPS))
        self.cb_sg_prbs.setCurrentText("PRBS9")
        self.cb_sg_prbs.currentTextChanged.connect(
            lambda t: self._sg_push(prbs=t))
        b.row("Data", self.cb_sg_prbs)

        # ---- LTE -------------------------------------------------------
        self.cb_sg_ltebw = QtWidgets.QComboBox()
        self.cb_sg_ltebw.addItems(LTE_BW_ORDER)
        self.cb_sg_ltebw.setCurrentText(self.sg.lte_bw)
        self.cb_sg_ltebw.setToolTip(
            "LTE channel bandwidth. Each one has exactly one sample rate\n"
            "that makes 15 kHz subcarriers come out right, so choosing a\n"
            "bandwidth sets the rate for you:\n\n"
            "  1.4 MHz   6 RB    1.92 MS/s    1.08 MHz occupied\n"
            "  3 MHz    15 RB    3.84 MS/s    2.70 MHz\n"
            "  5 MHz    25 RB    7.68 MS/s    4.50 MHz\n"
            "  10 MHz   50 RB   15.36 MS/s    9.00 MHz\n"
            "  15 MHz   75 RB   23.04 MS/s   13.50 MHz\n"
            "  20 MHz  100 RB   30.72 MS/s   18.00 MHz\n\n"
            "The occupied width is always less than the nominal channel --\n"
            "the rest is guard band, not a fault.")
        self.cb_sg_ltebw.currentTextChanged.connect(self._sg_lte_bw_changed)
        b.row("LTE bandwidth", self.cb_sg_ltebw)

        self.c_sg_ltedata = QtWidgets.QCheckBox("Data ON (loaded cell)")
        self.c_sg_ltedata.setChecked(True)
        self.c_sg_ltedata.setToolTip(
            "ON:  the shared data channel is filled, so the whole allocation\n"
            "     is carrying traffic -- a busy cell.\n\n"
            "OFF: only the synchronisation and reference signals, which is\n"
            "     what an IDLE cell actually transmits. Note it still fills\n"
            "     the channel: reference signals span the full bandwidth\n"
            "     whatever the load, so an idle 20 MHz cell still occupies\n"
            "     18 MHz. It is peakier than a loaded one, not narrower.")
        self.c_sg_ltedata.toggled.connect(lambda v: self._sg_push(lte_data=v))
        b.full(self.c_sg_ltedata)

        self.cb_sg_ltemod = QtWidgets.QComboBox()
        for name, order in LTE_MODS:
            self.cb_sg_ltemod.addItem(name, order)
        self.cb_sg_ltemod.setToolTip(
            "Modulation on the data channel. Higher orders are no wider,\n"
            "but they are less forgiving of distortion -- which is exactly\n"
            "why they are worth having when testing an amplifier.")
        self.cb_sg_ltemod.currentIndexChanged.connect(
            lambda _i: self._sg_push(lte_mod=self.cb_sg_ltemod.currentData()))
        b.row("Data modulation", self.cb_sg_ltemod)

        self.s_sg_lteload = QtWidgets.QDoubleSpinBox()
        self.s_sg_lteload.setRange(0, 100)
        self.s_sg_lteload.setDecimals(0)
        self.s_sg_lteload.setValue(100)
        self.s_sg_lteload.setSuffix(" %")
        self.s_sg_lteload.setToolTip(
            "How many of the resource blocks carry data, allocated from the\n"
            "middle outwards. A lightly loaded cell is the harder test for\n"
            "an amplifier: the same peak power in a narrower slice.\n\n"
            "The occupied bandwidth will NOT shrink with this, because the\n"
            "reference signals still cover the whole channel. That is real\n"
            "LTE behaviour, not a bug here.")
        self.s_sg_lteload.valueChanged.connect(
            lambda v: self._sg_push(lte_rb_load=v))
        b.row("RB loading", self.s_sg_lteload)

        self.s_sg_ltecell = QtWidgets.QSpinBox()
        self.s_sg_ltecell.setRange(0, 503)
        self.s_sg_ltecell.setToolTip(
            "Physical cell identity. Sets the sync sequence root and the\n"
            "reference-signal frequency shift, so two different cell ids\n"
            "give genuinely different signals in the same channel.")
        self.s_sg_ltecell.valueChanged.connect(
            lambda v: self._sg_push(lte_cell_id=v))
        b.row("Cell ID", self.s_sg_ltecell)

        self.s_sg_ltecar = QtWidgets.QSpinBox()
        self.s_sg_ltecar.setRange(1, LTE_MAX_CARRIERS)
        self.s_sg_ltecar.setValue(int(self.sg.lte_carriers))
        self.s_sg_ltecar.setToolTip(
            "How many cells to transmit at once, side by side -- the same\n"
            "idea as the multitone, with a whole LTE downlink in place of\n"
            "each tone.\n\n"
            "They are built as ONE transform at a higher sample rate, not\n"
            "mixed together afterwards, so the 15 kHz grid and the 10 ms\n"
            "frame stay exact however many there are. Each gets its own\n"
            "cell ID, counting up from the one below, so their reference\n"
            "signals do not line up the way three copies of one cell\n"
            "would.\n\n"
            "The rate follows: 3 x 10 MHz needs 30.72 MS/s, 3 x 15 MHz\n"
            "needs 46.08. What will not fit in the radio's 56 MHz channel\n"
            "is refused and said so -- three 20 MHz cells are 58 MHz edge\n"
            "to edge and are not possible on this board.\n\n"
            "Total power is shared between them: three cells each sit\n"
            "4.8 dB below what one would.")
        self.s_sg_ltecar.valueChanged.connect(self._sg_lte_carriers_changed)
        b.row("Carriers", self.s_sg_ltecar)

        self.e_sg_ltespace = HzEdit(self.sg.lte_spacing)
        self.e_sg_ltespace.setToolTip(
            "Centre-to-centre spacing between the cells.\n\n"
            "0 means the channel bandwidth, which puts them adjacent the\n"
            "way a contiguous carrier aggregation deployment does. Widen\n"
            "it to leave a guard band between them, or to spread them\n"
            "across a band. It snaps to 30 kHz so every subcarrier still\n"
            "lands on the 15 kHz grid whether the carrier count is odd or\n"
            "even, and it cannot go narrower than the occupied bandwidth,\n"
            "which would overlap the cells on top of each other.")
        self.e_sg_ltespace.changed.connect(self._sg_lte_spacing_changed)
        b.row("Carrier spacing", self.e_sg_ltespace)

        self.l_sg_ltecar = QtWidgets.QLabel("")
        self.l_sg_ltecar.setWordWrap(True)
        self.l_sg_ltecar.setStyleSheet("color:#8fa6bb;")
        b.full(self.l_sg_ltecar)

        self.c_sg_ltecp = QtWidgets.QCheckBox("Extended cyclic prefix")
        self.c_sg_ltecp.setToolTip(
            "Six symbols per slot with a longer guard instead of seven.\n"
            "Used on large cells where echoes arrive late. Same bandwidth,\n"
            "slightly lower throughput, and a visibly different symbol\n"
            "structure in a zero-span view.")
        self.c_sg_ltecp.toggled.connect(lambda v: self._sg_push(lte_ext_cp=v))
        b.full(self.c_sg_ltecp)

        self.l_sg_ltenote = QtWidgets.QLabel(
            "Structurally correct LTE, not a conformance signal -- hover to "
            "see exactly what is and is not right about it.")
        self.l_sg_ltenote.setWordWrap(True)
        self.l_sg_ltenote.setToolTip(LTE_FIDELITY_NOTE)
        self.l_sg_ltenote.setStyleSheet("color:#ffd24a;")
        b.full(self.l_sg_ltenote)

        arow = QtWidgets.QHBoxLayout()
        arow.setContentsMargins(0, 0, 0, 0)
        self.b_sg_arb = QtWidgets.QPushButton("Choose IQ file...")
        self.b_sg_arb.setToolTip(
            "Play back a bare complex64 'cfile' -- exactly what this app's\n"
            "own 'Record IQ' button writes. Capture something off the air,\n"
            "then transmit it back at a DUT.")
        self.b_sg_arb.clicked.connect(self._sg_choose_arb)
        arow.addWidget(self.b_sg_arb)
        aw = QtWidgets.QWidget(); aw.setLayout(arow)
        self.w_sg_arb = b.full(aw)
        self.l_sg_arb = QtWidgets.QLabel("no file chosen")
        self.l_sg_arb.setWordWrap(True)
        self.l_sg_arb.setStyleSheet("color:#8fa6bb;")
        b.full(self.l_sg_arb)

        self.l_sg_detail = QtWidgets.QLabel("")
        self.l_sg_detail.setWordWrap(True)
        self.l_sg_detail.setStyleSheet("color:#8fa6bb;")
        b.full(self.l_sg_detail)
        return b

    def _box_sg_burst(self):
        b = Box("SG Burst / Trigger", A_TX)
        self.c_sg_burst = QtWidgets.QCheckBox("Burst mode")
        self.c_sg_burst.setToolTip(
            "Key on and off in a repeating pattern. The gate is built into\n"
            "the samples, so both times are exact to the sample -- the\n"
            "sample stream is the clock.\n\n"
            "The transmitter does stay keyed between bursts, so its LO\n"
            "leakage is still there rather than true silence. Genuinely\n"
            "gating the output needs libbladeRF's metadata burst mode,\n"
            "which measured here cannot be configured at all while the\n"
            "receiver is streaming -- and the sweep running alongside the\n"
            "generator is the whole point of this app.")
        self.c_sg_burst.toggled.connect(lambda v: self._sg_push(burst=v))
        b.full(self.c_sg_burst)

        self.s_sg_bon = QtWidgets.QDoubleSpinBox()
        self.s_sg_bon.setRange(0.001, 1000.0)
        self.s_sg_bon.setDecimals(3)
        self.s_sg_bon.setValue(self.sg.burst_on_ms)
        self.s_sg_bon.setSuffix(" ms")
        self.s_sg_bon.valueChanged.connect(lambda v: self._sg_push(burst_on_ms=v))
        b.row("On", self.s_sg_bon)

        self.s_sg_boff = QtWidgets.QDoubleSpinBox()
        self.s_sg_boff.setRange(0.0, 10000.0)
        self.s_sg_boff.setDecimals(3)
        self.s_sg_boff.setValue(self.sg.burst_off_ms)
        self.s_sg_boff.setSuffix(" ms")
        self.s_sg_boff.valueChanged.connect(
            lambda v: self._sg_push(burst_off_ms=v))
        b.row("Off", self.s_sg_boff)

        self.cb_sg_trig = QtWidgets.QComboBox()
        self.cb_sg_trig.addItem("Off", TRIG_ROLE_DISABLED)
        self.cb_sg_trig.addItem("Master (fire it)", TRIG_ROLE_MASTER)
        self.cb_sg_trig.addItem("Slave (waits)", TRIG_ROLE_SLAVE)
        self.cb_sg_trig.setToolTip(
            "Hold the transmitter off until the trigger line fires, so the\n"
            "burst lands at a known moment relative to something the DUT\n"
            "did. Shares the mini_exp[1] line with the RX trigger -- do\n"
            "not set both to master.")
        self.cb_sg_trig.currentIndexChanged.connect(
            lambda _i: self._sg_push(trigger_role=self.cb_sg_trig.currentData()))
        b.row("TX trigger", self.cb_sg_trig)

        self.l_sg_trigwarn = QtWidgets.QLabel(
            "Both the analyzer and the generator are set to master on the "
            "same line -- pick one.")
        self.l_sg_trigwarn.setStyleSheet("color:#ffd24a;")
        self.l_sg_trigwarn.setWordWrap(True)
        self.l_sg_trigwarn.setVisible(False)
        b.full(self.l_sg_trigwarn)
        return b

    def _box_sg_test(self):
        b = Box("SG Checks", A_TX)
        self.c_sg_bias = QtWidgets.QCheckBox("TX bias tee (DC on the port)")
        self.c_sg_bias.setToolTip(
            "DC up the coax, for an external power amplifier such as\n"
            "Nuand's BT-100. Only switch this on if you know what is on\n"
            "the other end of the cable.")
        self.c_sg_bias.toggled.connect(self._sg_toggle_bias)
        b.full(self.c_sg_bias)

        # Kept short on purpose: a QCheckBox cannot word-wrap, so its label
        # sets a hard floor under the whole column's width. The old wording,
        # "Check through the internal loopback", needed 445 px on its own and
        # was quietly forcing a horizontal scrollbar in a 340 px panel.
        self.c_sg_lb = QtWidgets.QCheckBox("Use the internal loopback")
        self.c_sg_lb.setToolTip(
            "Route TX straight back into RX inside the RFIC, so the check\n"
            "below needs no cable and radiates nothing. Confirmed working\n"
            "on this board: the tone came back within 300 Hz of where it\n"
            "was sent, three runs out of three.\n\n"
            "What it proves: waveform synthesis, both sample streams, the\n"
            "USB path, and the whole measurement chain.\n\n"
            "What it does NOT prove: anything RF. It is a digital path --\n"
            "RX is handed the transmitter's baseband, so the tone appears\n"
            "at the same offset whatever the LO is doing. That is why the\n"
            "check narrows the span to a single sweep segment while it\n"
            "runs. For the mixers, the amplifiers and the connectors,\n"
            "untick this and use a cable with an attenuator.")
        self.c_sg_lb.setChecked(True)
        b.full(self.c_sg_lb)

        self.b_sg_check = QtWidgets.QPushButton("TX -> RX check")
        self.b_sg_check.setToolTip(
            "Cable the TX port to the RX port through an attenuator, then\n"
            "press this. It sets up a CW tone, points the analyzer at it,\n"
            "and reports whether the tone came back and how far off it\n"
            "was -- an end-to-end proof that both halves of the board and\n"
            "the whole measurement chain work.\n\n"
            "USE AN ATTENUATOR. Full output straight into RX can damage\n"
            "the receiver.")
        self.b_sg_check.clicked.connect(self._sg_check)
        b.full(self.b_sg_check)
        self.l_sg_check = QtWidgets.QLabel("--")
        self.l_sg_check.setWordWrap(True)
        b.full(self.l_sg_check)
        return b

    # -- signal generator: wiring -----------------------------------------
    def _sg_push(self, **kw) -> None:
        if self._quiet:
            return
        self.sg = replace(self.sg, **kw)
        if getattr(self, "txworker", None) is not None:
            self.txworker.apply(self.sg)
        if "on" in kw or "fs" in kw:
            self._sg_sync_pin()

    # -- output level ------------------------------------------------------
    #
    # Kfir asked for this the right way round: say what you want out of the
    # port, and let the instrument work out how to get there. Gain and
    # digital backoff stay visible, because on this board they are not
    # interchangeable -- see plan_level() -- but in this mode they are
    # readouts of a decision, not the decision itself.

    def _sg_gain_range(self):
        src = getattr(self, "src", None)
        return (float(getattr(src, "tx_gain_min", -23.75)),
                float(getattr(src, "tx_gain_max", 66.0)))

    def _sg_papr(self) -> float:
        """
        Crest factor of the waveform currently selected.

        Measured, once the generator has actually built it; the table of
        defaults until then. The stamped name is what makes that safe: an
        info dict left over from the previous waveform would be out by up to
        10 dB, which is the whole level.
        """
        info = getattr(self, "_sg_info", None) or {}
        if info.get("waveform") == self.sg.waveform:
            return float(info.get("papr_db", 0.0))
        return float(SG_PAPR_NOMINAL.get(self.sg.waveform, 6.0))

    def _sg_level_widgets(self) -> None:
        """
        There is one way to set a level now, so the Amplitude box is always
        live.

        Used to grey the gain/backoff pair against the amplitude, depending
        on "Set by". Those two controls are gone (see _box_sg_lvl) and
        level_direct is always True, so the only thing left to say is that
        Amplitude is enabled. Kept as a named call rather than deleted --
        its callers are all places where "has anything changed about how the
        level is reached?" is the right question to keep asking.
        """
        if getattr(self, "s_sg_level", None) is not None:
            self.s_sg_level.setEnabled(True)

    def _sg_replan_level(self) -> None:
        """
        Solve the requested level into a gain and a backoff, and apply both.

        Re-entrant by construction: it pushes gain and backoff, and a push
        can bring us back here through _on_sg_state when the new waveform
        reports its crest factor. The guard stops that turning into a loop
        while still letting the second, better-informed pass happen.
        """
        if not self.sg.level_direct or getattr(self, "_sg_planning", False):
            return
        if getattr(self, "l_sg_plan", None) is None:
            return                       # called during _build(), too early
        gmin, gmax = self._sg_gain_range()
        papr = self._sg_papr()
        plan = plan_level(self.sg.level_dbm, papr, self.sg.cal_offset,
                          gmin, gmax)
        self._sg_planning = True
        try:
            # Straight to the config. This used to go through the gain and
            # backoff spin boxes and rely on their own handlers to push it;
            # with the boxes gone the push is the only route, and it was
            # always the one that mattered.
            self._sg_papr_used = papr
            self._sg_push(gain=plan["gain"], backoff_db=plan["backoff_db"])
        finally:
            self._sg_planning = False
        txt = (f"gain {plan['gain']:.0f} dB + {plan['backoff_db']:.1f} dB "
               f"digital backoff, PAPR {papr:.1f} dB "
               f"-> {plan['achieved_dbm']:+.1f} dBm")
        if plan["note"]:
            self.l_sg_plan.setText(txt + "\n" + plan["note"])
            self.l_sg_plan.setStyleSheet("color:#ffd24a;")
        else:
            self.l_sg_plan.setText(txt)
            self.l_sg_plan.setStyleSheet("color:#8fa6bb;")

    def _sg_show_level_readback(self) -> None:
        """
        Run the model FORWARDS: what do the gain and backoff in force come
        to at the port? Display only -- nothing is pushed and nothing on the
        radio moves.

        Unreachable while level_direct is True, which it now always is (the
        "Set by" control is gone). Kept because level_from() is the one place
        the level model is written down and this is its only forward caller;
        deleting it would leave the model exercised in one direction only.
        """
        if self.sg.level_direct or getattr(self, "s_sg_level", None) is None:
            return
        _, gmax = self._sg_gain_range()
        lv = level_from(self.sg.gain, self.sg.backoff_db, self._sg_papr(),
                        self.sg.cal_offset, gmax)
        quiet, self._quiet = self._quiet, True
        try:
            self.s_sg_level.setValue(float(np.clip(
                lv, self.s_sg_level.minimum(), self.s_sg_level.maximum())))
        finally:
            self._quiet = quiet
        self.l_sg_plan.setText(
            f"gain {self.sg.gain:.0f} dB + {self.sg.backoff_db:.1f} dB "
            f"digital backoff, PAPR {self._sg_papr():.1f} dB "
            f"-> {lv:+.1f} dBm")
        self.l_sg_plan.setStyleSheet("color:#8fa6bb;")

    def _sg_push_offset(self, v: float) -> None:
        if self._quiet:
            return
        self._sg_push(offset=float(v))
        self.l_sg_spurs.setText(self._sg_spur_text())

    def _sg_push_level(self, v: float) -> None:
        if self._quiet:
            return
        self._sg_push(level_dbm=float(v))
        self._sg_replan_level()

    def _sg_push_cal(self, v: float) -> None:
        if self._quiet:
            return
        self._sg_push(cal_offset=float(v))
        # A cal offset is a statement about the port, so the level asked for
        # has to be re-solved to still mean the same thing there.
        self._sg_replan_level()
        self._sg_show_level_readback()

    def _sg_spur_text(self) -> str:
        """Where the artifacts of a non-zero offset land, in real
        frequencies, so they can be recognised rather than chased."""
        off = float(self.sg.offset)
        if abs(off) < 1.0:
            return "none -- offset is 0"
        f0 = float(self.sg.freq)
        bits = [f"LO leak {fmt_hz_short(f0)}",
                f"image {fmt_hz_short(f0 - off)}"]
        h2 = f0 + 2.0 * off
        if abs(2.0 * off) < float(self.sg.fs) / 2.0:
            bits.append("2nd harmonic of the offset at "
                        + fmt_hz_short(h2)
                        + (" (dithered away)" if self.sg.dither else ""))
        return ", ".join(bits)

    def _sg_sync_pin(self) -> None:
        """
        Tie the analyzer's sample rate to the generator's while it is keyed.

        Measured on the real board: RX and TX cannot hold different sample
        rates -- setting either drags the other, in both directions, because
        they share the baseband PLL. The sweep re-applies its own rate every
        pass, so without this the sweep would quietly drag the transmitter
        off the rate its waveform was built for and every frequency in the
        output would be wrong. Said out loud in the status bar rather than
        just done, because it changes how the analyzer behaves.
        """
        want = float(self.sg.fs) if self.sg.on else 0.0
        # Keying state also decides whether a trigger can be armed at all --
        # see _sync_trigger_enabled(). Done before the early return so
        # unkeying re-enables the trigger even when the rate has not moved.
        self._sync_trigger_enabled()
        if abs(float(self.cfg.fs_pin) - want) < 1e-9:
            return
        self.cfg = replace(self.cfg, fs_pin=want)
        self._fill_rbw()
        self._auto_label()
        self._wf_buf = None
        if getattr(self, "worker", None) is not None:
            self.worker.apply(self.cfg)
        if want:
            self._message(
                f"analyzer sample rate pinned to the generator's "
                f"{want / 1e6:.3f} MS/s -- one baseband PLL feeds both "
                f"directions, so they cannot differ", 9.0)
        else:
            self._message("analyzer sample rate is its own again")

    def _sg_boxes(self):
        return [w for w in (getattr(self, "_sgbox_head", None),
                            getattr(self, "_sgbox_out", None),
                            getattr(self, "_sgbox_lvl", None),
                            getattr(self, "_sgbox_wave", None),
                            getattr(self, "_sgbox_burst", None),
                            getattr(self, "_sgbox_test", None)) if w is not None]

    def _sg_toggle_panel(self, on: bool) -> None:
        """
        Show or hide the generator's own boxes.

        The boxes inside are toggled as well as the column itself. That looks
        redundant, and is not: hiding only the container leaves every child
        reporting itself as visible, so anything that asks a box whether it
        is showing -- the tests do -- would get the wrong answer.

        Hiding also unkeys. Controls you cannot see must not still be
        transmitting.
        """
        on = bool(on)
        # Nothing to do if it is already in that state, and this is not an
        # economy: the "on" branch below OPENS the RF OUT panel, so without
        # this a recalled setup or a session restore carrying sg_open=True
        # would throw that panel over the trace every time it was applied.
        # The tick-box this replaced had the same property for free --
        # setChecked() to the value it already holds emits nothing.
        if getattr(self, "_sg_open", None) is on:
            return
        self._sg_open = on
        for w in self._sg_boxes():
            w.setVisible(on)
        # The generator's controls used to be a column that appeared
        # and disappeared. They are one popover per function now, so
        # what "closed" means is that the function keys go away with
        # the boxes -- and if one of those panels is the one on
        # screen it is closed, or the switch you just unticked would
        # leave a panel of controls it had supposedly closed sitting
        # over the trace.
        if getattr(self, "sg_keybar", None) is not None:
            self.sg_keybar.setVisible(on)
        # The KEY carries this state now, so its lamp has to follow it from
        # every route in: the key itself, a recalled setup, and Preset.
        self._sync_act_keys()
        if (not on and getattr(self, "_panel_open", None)
                in getattr(self, "_sg_keys", ())):
            self._close_panel()
        if not on and self.b_sg_out.isChecked():
            self.b_sg_out.setChecked(False)      # unkeys through _sg_toggle_out
        if on:
            self._sg_refresh_support()
            self._sg_wave_changed(self.cb_sg_wave.currentText())
            # Open the one that keys the transmitter, so the red
            # button is the thing you meet rather than something to
            # go looking for -- 5.5's lesson about a feature added
            # 14 boxes down a column nobody scrolls.
            if getattr(self, "sg_keybar", None) is not None:
                self._open_panel("RF OUT")
        # _build() calls this once to set the initial state, and at that
        # point the status bar does not exist yet -- _message() would fail.
        if getattr(self, "status", None) is not None:
            self._message("signal generator open -- its red keys are under the "
                          "Signal generator caption on the right, and RF OUT "
                          "is the one that actually transmits" if on
                          else "signal generator closed")

    def _sg_refresh_support(self) -> None:
        ok = bool(getattr(self.src, "tx_supported", False))
        why = getattr(self.src, "tx_reason", "") if not ok else ""
        self.l_sg_why.setVisible(not ok)
        self.l_sg_why.setText(f"No transmitter available: {why}" if why else "")
        self.b_sg_out.setEnabled(ok)
        self.b_sg_check.setEnabled(ok)
        w = getattr(self, "s_sg_fs", None)
        if w is not None:
            w.setEnabled(ok)

    def _sg_refresh_ranges(self, src) -> None:
        """Re-range the TX controls for whichever source is live."""
        self._quiet = True
        try:
            self.s_sg_fs.setRange(getattr(src, "tx_fs_min", 0.52e6) / 1e6,
                                  getattr(src, "tx_fs_max", 61.44e6) / 1e6)
            self.s_sg_fs.setValue(float(np.clip(
                self.sg.fs / 1e6, self.s_sg_fs.minimum(), self.s_sg_fs.maximum())))
        finally:
            self._quiet = False
        # The gain spin box's setRange used to clamp cfg.gain to the board as
        # a side effect of being on screen. It is gone, so the clamp has to be
        # explicit -- the same trap the Max IBW removal hit (5.14).
        gmin, gmax = self._sg_gain_range()
        self.sg = replace(self.sg,
                          gain=float(np.clip(self.sg.gain, gmin, gmax)),
                          fs=self.s_sg_fs.value() * 1e6)
        # A new source can have a different gain range, so the amplitude
        # asked for has to be solved against the range it will actually run
        # in rather than against the last one.
        self._sg_replan_level()

    def _sg_vis(self, widgets, on: bool) -> None:
        for w in widgets:
            if w is None:
                continue
            w.setVisible(bool(on))
            lab = getattr(w, "_row_label", None)
            if lab is not None:
                lab.setVisible(bool(on))

    def _sg_wave_changed(self, text: str) -> None:
        """Show only the parameters the selected waveform actually uses."""
        self._sg_push(waveform=text)
        g = self
        self._sg_vis([g.e_sg_off], text != "Arb file")
        self._sg_vis([g.e_sg_spacing], text in ("Two-tone", "Multitone"))
        self._sg_vis([g.s_sg_ntones], text == "Multitone")
        self._sg_vis([g.e_sg_noisebw], text == "AWGN")
        self._sg_vis([g.e_sg_swspan, g.s_sg_swtime], text in SG_SWEEPING)
        self._sg_vis([g.s_sg_swsteps], text == "Stepped sweep")
        self._sg_vis([g.s_sg_pper, g.s_sg_pwid, g.s_sg_prise], text in SG_PULSED)
        self._sg_vis([g.s_sg_amdepth], text == "AM")
        self._sg_vis([g.e_sg_fmdev], text == "FM")
        self._sg_vis([g.s_sg_pmdev], text == "PM")
        self._sg_vis([g.e_sg_modrate], text in ("AM", "FM", "PM"))
        self._sg_vis([g.e_sg_symrate, g.cb_sg_prbs], text in SG_DIGITAL)
        self._sg_vis([g.cb_sg_order], text in ("ASK", "FSK", "PSK", "QAM"))
        self._sg_vis([g.s_sg_alpha], text in ("ASK", "PSK", "QAM"))
        self._sg_vis([g.e_sg_fskdev], text == "FSK")
        self._sg_vis([g.s_sg_bt], text == "GMSK")
        self._sg_vis([g.w_sg_arb, g.l_sg_arb], text == "Arb file")
        lte = text in SG_CELLULAR
        self._sg_vis([g.cb_sg_ltebw, g.c_sg_ltedata, g.cb_sg_ltemod,
                      g.s_sg_lteload, g.s_sg_ltecell, g.c_sg_ltecp,
                      g.s_sg_ltecar, g.e_sg_ltespace, g.l_sg_ltecar,
                      g.l_sg_ltenote], lte)
        # LTE owns the sample rate: only one rate per bandwidth gives 15 kHz
        # subcarriers, so the box follows the bandwidth rather than fighting
        # it. Offset is meaningless too -- the frame is built centred.
        self._sg_vis([g.e_sg_off], text != "Arb file" and not lte)
        self.s_sg_fs.setEnabled(not lte)
        if lte:
            self._sg_lte_apply_rate()
        # Crest factor is a property of the waveform -- 0 dB for CW, 10.6 for
        # LTE -- so the level has to be solved again for the new one or it
        # would be out by the difference between them.
        self.l_sg_spurs.setText(self._sg_spur_text())
        self._sg_level_widgets()
        self._sg_replan_level()
        self._sg_show_level_readback()

    def _sg_lte_bw_changed(self, text: str) -> None:
        self._sg_push(lte_bw=text)
        self._sg_lte_apply_rate()

    def _sg_lte_apply_rate(self) -> None:
        """
        Pin the sample rate to the one the chosen LTE bandwidth needs.

        There is exactly one rate per bandwidth that puts subcarriers 15 kHz
        apart, so this is not a preference. Setting it here rather than
        leaving the two to disagree is what keeps the signal an LTE signal;
        _build_lte() would otherwise have to either lie about it or refuse.
        """
        if self.cb_sg_ltebw.currentText() not in LTE_BW:
            return
        plan = lte_plan(self.sg)
        want = plan["fs"]
        if want <= 0:
            return
        lo, hi = self.s_sg_fs.minimum() * 1e6, self.s_sg_fs.maximum() * 1e6
        if not (lo <= want <= hi):
            self._message(
                f"this radio cannot do the {want/1e6:.2f} MS/s that "
                f"{plan['carriers']} x {self.cb_sg_ltebw.currentText()} "
                f"LTE needs", 8.0)
            return
        if abs(self.s_sg_fs.value() * 1e6 - want) > 1.0:
            self.s_sg_fs.setValue(want / 1e6)      # its signal pushes fs
            self._message(
                f"sample rate set to {want/1e6:.2f} MS/s for "
                f"{plan['carriers']} x {self.cb_sg_ltebw.currentText()} "
                f"LTE", 6.0)
        # With more than one carrier the radio's own 0.8 * fs default would
        # filter the outer ones, so the plan states the bandwidth outright.
        if abs(float(self.sg.bandwidth) - plan["bandwidth"]) > 1.0:
            self._sg_push(bandwidth=plan["bandwidth"])
        self._sg_lte_note(plan)

    def _sg_lte_note(self, plan=None) -> None:
        """Say what the carrier settings actually came to, under them."""
        if getattr(self, "l_sg_ltecar", None) is None:
            return
        plan = plan if plan is not None else lte_plan(self.sg)
        n, asked = plan["carriers"], plan["asked"]
        txt = (f"{n} cell{'s' if n > 1 else ''} at "
               f"{plan['spacing']/1e6:.2f} MHz spacing, "
               f"{plan['span']/1e6:.2f} MHz edge to edge, "
               f"{plan['fs']/1e6:.2f} MS/s")
        if n > 1:
            edge = plan["span"] / 2.0
            txt += (f" -- outermost centres at "
                    f"{plan['offsets'][0]/1e6:+.2f} and "
                    f"{plan['offsets'][-1]/1e6:+.2f} MHz, so the signal "
                    f"runs {-edge/1e6:+.2f} to {edge/1e6:+.2f} MHz about "
                    f"the TX frequency")
        if n < asked:
            txt = (f"{asked} will not fit in this radio's "
                   f"{LTE_MAX_BW/1e6:.0f} MHz channel -- " + txt)
        self.l_sg_ltecar.setText(txt)
        self.l_sg_ltecar.setStyleSheet(
            "color:#ffd24a;" if n < asked else "color:#8fa6bb;")

    def _sg_lte_carriers_changed(self, n: int) -> None:
        self._sg_push(lte_carriers=int(n))
        self._sg_lte_apply_rate()

    def _sg_lte_spacing_changed(self, hz: float) -> None:
        self._sg_push(lte_spacing=float(hz))
        self._sg_lte_apply_rate()

    def _sg_set_port(self, index: int) -> None:
        index = int(index)
        if index not in (0, 1, TX_BOTH):
            index = 0
        self.b_tx1.setChecked(index == 0)
        self.b_tx2.setChecked(index == 1)
        self.b_txboth.setChecked(index == TX_BOTH)
        self._sg_push(channel=index)
        self._message("generator now on BOTH ports, TX1 and TX2 together"
                      if index == TX_BOTH else
                      f"generator now on TX{index + 1}")

    def _sg_toggle_out(self, on: bool) -> None:
        """
        Key or unkey the transmitter.

        The confirmation is shown once per session, not every time, because
        a dialog that appears on every keying stops being read after the
        second one.
        """
        if on and not getattr(self, "_sg_warned", False):
            if not getattr(self.src, "tx_supported", False):
                self.b_sg_out.setChecked(False)
                return
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("About to transmit")
            box.setIcon(QtWidgets.QMessageBox.Warning)
            box.setText(
                "This puts real RF on the TX port.\n\n"
                "Check that the port has an antenna, a DUT or a 50 ohm "
                "load on it -- transmitting into an open connector is "
                "hard on the output stage.\n\n"
                "Check too that you are allowed to radiate on this "
                "frequency at this power. Into a cable and a load, none "
                "of that applies.")
            box.setStandardButtons(QtWidgets.QMessageBox.Ok
                                   | QtWidgets.QMessageBox.Cancel)
            if box.exec_() != QtWidgets.QMessageBox.Ok:
                self.b_sg_out.setChecked(False)
                return
            self._sg_warned = True
        self.b_sg_out.setText("RF OUT is ON" if on else "RF OUT is OFF")
        # Keyed is the one state in this application that has to be
        # unmistakable from across a bench, so it gets a brighter red than
        # the accent and keeps the same key idiom rather than becoming a
        # differently-shaped control while it is on.
        # The button is checkable and checked == keyed, so key_qss's own
        # :checked state IS the transmitting look -- white on a filled
        # accent with a 2px border. All that changes with `on` is which
        # accent it is drawn in: the ordinary transmit orange while it is
        # off, a brighter red while it is live, because "there is RF on that
        # connector" is the one state in this application that has to be
        # unmistakable from across a bench.
        self.b_sg_out.setStyleSheet(key_qss("#ff453a" if on else A_TX))
        # Built later than this panel is, so do not assume it is there.
        if getattr(self, "l_tx", None) is not None:
            self.l_tx.setText("TRANSMITTING" if on else "")
        self._sg_push(on=bool(on))
        if on and getattr(self, "_lb_on", False) \
                and getattr(self, "worker", None) is not None:
            # The loopback is in while the transmitter is not, which is
            # the ordering that latches it to zeros for the life of that
            # RX stream (5.5 fact 3, measured). Re-applying it now bounces
            # the RX stream with TX already running, which is the ordering
            # that works.
            #
            # Reachable only by cycling RF OUT inside the ~2.5 s window of
            # a TX -> RX check, since 5.12 removed the by-hand loopback
            # checkbox and _sg_check_undo() clears the flag on every exit
            # from that check. Kept because that window is real, and
            # because it is the safe ordering either way.
            QtCore.QTimer.singleShot(
                700, lambda: self.worker.request_loopback(LB_RFIC_BIST))
        note = getattr(self, "_rbw_clamp_msg", "")
        self._rbw_clamp_msg = ""
        self._message(("RF OUT on" if on else "RF OUT off")
                      + (f" -- {note}" if note else ""),
                      8.0 if note else 6.0)

    def _sg_choose_arb(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Pick an IQ file to transmit", self.shot_dir,
            "IQ capture (*.cfile *.iq *.bin *.raw);;All files (*)")
        if not path:
            return
        self.l_sg_arb.setText(os.path.basename(path))
        # The sidecar this app writes next to its own captures records the
        # rate they were taken at -- reading it back means a recording
        # replays at the speed it was made, instead of whatever the sample
        # rate box happened to be left on.
        rate = _sg_sidecar_rate(path)
        if rate and abs(rate - self.sg.fs) > 1.0:
            lo, hi = self.s_sg_fs.minimum() * 1e6, self.s_sg_fs.maximum() * 1e6
            if lo <= rate <= hi:
                self.s_sg_fs.setValue(rate / 1e6)
                self._message(f"sample rate set to {rate/1e6:.3f} MS/s from "
                              f"the capture's sidecar file")
        self._sg_push(arb_path=path, waveform="Arb file")
        if self.cb_sg_wave.currentText() != "Arb file":
            self.cb_sg_wave.setCurrentText("Arb file")

    def _sg_toggle_bias(self, on: bool) -> None:
        if self._quiet:
            return
        try:
            self.src.tx_set_bias_tee(bool(on))
            self._message("TX bias tee ON -- DC is on the TX port now" if on
                          else "TX bias tee off")
        except Exception as exc:
            self._message(f"TX bias tee failed: {exc}")
            self.c_sg_bias.blockSignals(True)
            self.c_sg_bias.setChecked(not on)
            self.c_sg_bias.blockSignals(False)

    # -- TX -> RX end-to-end check -----------------------------------------
    def _sg_check(self) -> None:
        """
        Transmit a known tone and see whether the analyzer finds it.

        This is the one test that exercises everything at once: waveform
        synthesis, the TX stream, the RF path through whatever is cabled up,
        the RX stream, the sweep and the peak search. If this passes, the
        bench works.
        """
        if not getattr(self.src, "tx_supported", False):
            self.l_sg_check.setText("no transmitter on this source")
            return
        use_lb = self.c_sg_lb.isChecked()
        self._sg_check_restore = (self.cb_sg_wave.currentText(),
                                  self.cfg.center, self.cfg.span,
                                  self.b_sg_out.isChecked(), use_lb)
        self.b_sg_check.setEnabled(False)
        self.l_sg_check.setStyleSheet("")
        self.l_sg_check.setText(
            "keying the transmitter ..." if use_lb
            else "transmitting a test tone ...")
        self._quiet = True
        self.cb_sg_wave.setCurrentText("CW")
        self._quiet = False
        self._sg_push(waveform="CW")
        self._sg_wave_changed("CW")
        # Through the internal loopback the span MUST stay inside one sweep
        # segment. That path is digital: it hands RX the transmitter's
        # baseband with no RF involved, so the tone turns up at the same
        # offset from whatever the segment centre happens to be. Stitch two
        # segments together and the same tone gets labelled at two different
        # absolute frequencies -- measured on the board as a "peak" 2.5 MHz
        # off, which looks exactly like a real tuning error and is not one.
        span = max(min(self.cfg.span, 20e6), 5e6)
        if use_lb:
            span = min(span, max(self.sg.fs * 0.5, 1e6))
        self._retune(self.sg.freq, span)
        if not self.b_sg_out.isChecked():
            self.b_sg_out.setChecked(True)
        # Transmitter first, loopback second, and only then measure. Measured
        # on real hardware: with the RFIC BIST loopback selected, an RX read
        # taken before TX is streaming latches the loopback to zeros for the
        # life of that RX stream. Keying first and switching the loopback in
        # afterwards (which bounces RX) is what keeps that from happening.
        QtCore.QTimer.singleShot(700, self._sg_check_loopback)

    def _sg_check_loopback(self) -> None:
        old = getattr(self, "_sg_check_restore", None)
        if old is not None and old[4] and getattr(self, "worker", None) is not None:
            # Through the worker, never straight at the device: it owns the
            # handle, and reaching past it is what made the loopback toggle
            # hang the first time round.
            self.l_sg_check.setText("looping TX back into RX inside the RFIC ...")
            self.worker.request_loopback(LB_RFIC_BIST)
            self._lb_on = True
            self._sg_refresh_loopback_note()
        QtCore.QTimer.singleShot(2500, self._sg_check_finish)

    def _sg_check_finish(self) -> None:
        self.b_sg_check.setEnabled(True)
        want = self.sg.freq + float(self._sg_info.get("offset", self.sg.offset))
        r = self.last
        if r is None or not np.isfinite(getattr(r, "peak_hz", float("nan"))):
            self.l_sg_check.setText("no sweep data came back to look at")
            self.l_sg_check.setStyleSheet("color:#ffd24a;")
            self._sg_check_undo()
            return
        err = float(r.peak_hz) - want
        # One display point either side is as close as the sweep can place a
        # peak, so scale the tolerance to the span rather than fixing it.
        tol = max(self.cfg.span / max(self.cfg.points, 1) * 3.0, 2e3)
        if abs(err) <= tol:
            self.l_sg_check.setText(
                f"PASS -- tone found at {fmt_hz(r.peak_hz)}\n"
                f"{err:+,.0f} Hz from where it was sent, "
                f"{r.peak_dbm:.1f} dBm at the receiver")
            self.l_sg_check.setStyleSheet("color:#7CFF6B;font-weight:bold;")
        else:
            self.l_sg_check.setText(
                f"NO -- expected {fmt_hz(want)}, strongest peak was at "
                f"{fmt_hz(r.peak_hz)} ({r.peak_dbm:.1f} dBm).\n"
                f"Check the cable between TX and RX, and that the level is "
                f"above the noise floor.")
            self.l_sg_check.setStyleSheet("color:#ffd24a;")
        self._sg_check_undo()

    def _sg_check_undo(self) -> None:
        """
        Put the radio back the way the check found it.

        Its own method, and called on EVERY exit from _sg_check_finish,
        because it used to sit after an early return: a check that got no
        sweep data left the RFIC loopback switched in and the transmitter
        keyed. That was survivable while a checkbox could undo it by hand.
        Section 5.12 removed the checkbox, so after it there was no way back
        from the GUI at all, and every later sweep would have been measuring
        the transmitter's own baseband rather than the antenna.
        """
        old = getattr(self, "_sg_check_restore", None)
        if old is not None:
            wave, cen, span, was_on, used_lb = old
            if not was_on:
                self.b_sg_out.setChecked(False)
            if used_lb and getattr(self, "worker", None) is not None:
                self.worker.request_loopback(LB_NONE)
                self._lb_on = False
                self._sg_refresh_loopback_note()
            self.cb_sg_wave.setCurrentText(wave)
            self._retune(cen, span)
            self._sg_check_restore = None

    # -- status back from the generator thread -----------------------------
    @QtCore.pyqtSlot(dict)
    def _on_sg_state(self, d: dict) -> None:
        if not d.get("on"):
            self.l_sg_state.setText("idle")
            self.l_sg_state.setStyleSheet("color:#8fa6bb;")
            return
        info = d.get("info") or {}
        self._sg_info = info
        self._sg_refresh_loopback_note()
        # The waveform has now been built, so its crest factor is measured
        # rather than assumed. If the solver worked from the table and the
        # real figure differs, solve again with the real one -- once: the
        # second pass finds them equal and stops. Without this, picking a
        # level for a waveform whose PAPR is not in the table would be out
        # by the difference.
        if (self.sg.level_direct
                and abs(self._sg_papr()
                        - float(getattr(self, "_sg_papr_used", 1e9))) > 0.05):
            self._sg_replan_level()
        out = self.sg.freq + float(info.get("offset", 0.0))
        self.l_sg_actual.setText(fmt_hz(out))
        gap = int(d.get("gap", 0))
        fs_a = float(d.get("fs_actual", 0.0))
        bits = [f"{d.get('port', 'TX1')}", f"{fs_a/1e6:.3f} MS/s"]
        if info:
            bits.append(f"loop {info.get('n', 0):,} smp "
                        f"({info.get('loop_s', 0.0)*1e3:.3f} ms)")
            bits.append(f"PAPR {info.get('papr_db', 0.0):.1f} dB")
        self.l_sg_state.setText("  ".join(bits))
        self.l_sg_state.setStyleSheet(
            "color:#ff453a;font-weight:bold;" if gap else "color:#7CFF6B;")
        notes = list(info.get("notes", []))
        if info.get("clipped"):
            notes.append("clipping: backed off automatically")
        want_fs = float(d.get("fs_wanted", 0.0))
        if want_fs > 0 and fs_a > 0 and abs(fs_a - want_fs) > 1.0:
            # Should not happen while the pin is doing its job, so if it does
            # it means something else moved the rate -- and every frequency
            # in the output is wrong by that ratio until it is sorted out.
            notes.append(
                f"RATE MISMATCH: the waveform was built for "
                f"{want_fs/1e6:.3f} MS/s but the board is running at "
                f"{fs_a/1e6:.3f} MS/s, so everything in it is off by "
                f"{fs_a/want_fs:.4f}x")
        if gap:
            notes.append(f"UNDERRUN: {gap:,} samples were missed -- the PC "
                         f"could not feed the transmitter fast enough. Lower "
                         f"the sample rate.")
        rt = float(d.get("realtime", 1.0))
        sus = float(d.get("sustained", 0.0))
        if rt > 1.02 and sus > 0:
            # The transmitter is being starved, which on screen looks like the
            # signal blinking off and on rather than like an error.
            notes.append(
                f"NOT KEEPING UP: this PC is sustaining about "
                f"{sus / 1e6:.1f} MS/s, but {fs_a / 1e6:.3f} MS/s was asked "
                f"for ({rt:.2f}x too slow). The output has gaps in it -- on "
                f"the analyzer that looks like the signal switching off and "
                f"on. Drop the sample rate to around {sus / 1e6:.0f} MS/s, "
                f"or turn on Oversample, which halves the bytes per sample.")
        if not info.get("exact", True) and self.sg.waveform in ("CW", "Two-tone",
                                                                "Multitone"):
            notes.append("offset snapped to the nearest exactly synthesisable "
                         "value -- 'Output at' is the true frequency")
        self.l_sg_detail.setText("\n".join(f"- {n}" for n in notes))

    # -- generator thread lifecycle ---------------------------------------
    def _sg_start(self) -> None:
        self.txthread = QtCore.QThread(self)
        self.txworker = TxWorker(self.src, self.sg)
        self.txworker.moveToThread(self.txthread)
        self.txthread.started.connect(self.txworker.run)
        self.txworker.state.connect(self._on_sg_state)
        self.txworker.failed.connect(lambda m: self._message(m, 6.0))
        self.txthread.start()

    def _sg_stop(self) -> None:
        if getattr(self, "txworker", None) is not None:
            self.txworker.stop()
        if getattr(self, "txthread", None) is not None:
            self.txthread.quit()
            # Generous: a block already handed to bladerf_sync_tx has to
            # drain before the thread can come back, and at a low sample
            # rate that block is genuinely long.
            self.txthread.wait(5000)

    def _sg_preset_apply(self, d: dict) -> None:
        """
        Put a saved generator state back on the controls.

        RF OUT is deliberately NOT restored. Recalling a setup should never
        start a transmitter on its own -- the operator keys it when they can
        see what it is about to do.
        """
        if not d:
            return
        base = SgConfig()
        vals = {f.name: d.get(f.name, getattr(base, f.name))
                for f in fields(SgConfig)}
        vals["on"] = False
        # Two fields are no longer the user's to choose, so a recall must not
        # let an old setup put either back: dither is permanently on and
        # level_direct is permanently True, both by request (see
        # _box_sg_lvl). A setup saved before 2026-08-25 can carry False for
        # either, and honouring that would leave the instrument in a state
        # with no control able to get out of it -- which is the shape of the
        # trap bug 33 was.
        vals["dither"] = True
        vals["level_direct"] = True
        # Same argument again for the impairments, whose panel went on
        # 2026-08-30. The SgConfig fields are still there and the waveform
        # engine still honours them -- that is what keeps them testable
        # against their closed forms -- but nothing in the window can set
        # one any more, so a setup saved before today carrying an I/Q
        # imbalance would transmit a deliberately broken signal with no
        # control able to explain it or clear it. Bug 33's trap exactly.
        for _f in ("imp_dc_i", "imp_dc_q", "imp_gain_imb", "imp_quad_err",
                   "imp_freq_off", "imp_snr"):
            vals[_f] = 0.0
        vals["imp_invert"] = False
        self._quiet = True
        try:
            self.e_sg_freq.setValue(vals["freq"])
            self.e_sg_off.setValue(vals["offset"])
            self.e_sg_spacing.setValue(vals["tone_spacing"])
            self.e_sg_noisebw.setValue(vals["noise_bw"])
            self.e_sg_swspan.setValue(vals["sweep_span"])
            self.e_sg_fmdev.setValue(vals["fm_dev"])
            self.e_sg_modrate.setValue(vals["mod_rate"])
            self.e_sg_symrate.setValue(vals["sym_rate"])
            self.e_sg_fskdev.setValue(vals["fsk_dev"])
            self.s_sg_fs.setValue(float(np.clip(
                vals["fs"] / 1e6, self.s_sg_fs.minimum(), self.s_sg_fs.maximum())))
            self.s_sg_cal.setValue(vals["cal_offset"])
            self.s_sg_level.setValue(float(np.clip(
                vals["level_dbm"], self.s_sg_level.minimum(),
                self.s_sg_level.maximum())))
            self.s_sg_ntones.setValue(int(vals["n_tones"]))
            self.s_sg_swtime.setValue(vals["sweep_time"] * 1e3)
            self.s_sg_swsteps.setValue(int(vals["sweep_steps"]))
            self.s_sg_pper.setValue(vals["pulse_period"] * 1e3)
            self.s_sg_pwid.setValue(vals["pulse_width"] * 1e6)
            self.s_sg_prise.setValue(vals["pulse_rise"] * 1e6)
            self.s_sg_amdepth.setValue(vals["am_depth"])
            self.s_sg_pmdev.setValue(vals["pm_dev"])
            self.s_sg_alpha.setValue(vals["rrc_alpha"])
            self.s_sg_bt.setValue(vals["gmsk_bt"])
            self.s_sg_bon.setValue(vals["burst_on_ms"])
            self.s_sg_boff.setValue(vals["burst_off_ms"])
            self.c_sg_burst.setChecked(bool(vals["burst"]))
            self.cb_sg_prbs.setCurrentText(str(vals["prbs"]))
            self.cb_sg_ltebw.setCurrentText(str(vals["lte_bw"]))
            self.c_sg_ltedata.setChecked(bool(vals["lte_data"]))
            self.s_sg_ltecar.setValue(int(vals.get("lte_carriers", 1)))
            self.e_sg_ltespace.setValue(float(vals.get("lte_spacing", 0.0)))
            self.s_sg_lteload.setValue(vals["lte_rb_load"])
            self.s_sg_ltecell.setValue(int(vals["lte_cell_id"]))
            self.c_sg_ltecp.setChecked(bool(vals["lte_ext_cp"]))
            j = self.cb_sg_ltemod.findData(int(vals["lte_mod"]))
            if j >= 0:
                self.cb_sg_ltemod.setCurrentIndex(j)
            self.cb_sg_wave.setCurrentText(str(vals["waveform"]))
            i = self.cb_sg_order.findData(int(vals["mod_order"]))
            if i >= 0:
                self.cb_sg_order.setCurrentIndex(i)
            i = self.cb_sg_trig.findData(int(vals["trigger_role"]))
            if i >= 0:
                self.cb_sg_trig.setCurrentIndex(i)
            self.l_sg_arb.setText(os.path.basename(vals["arb_path"])
                                  if vals["arb_path"] else "no file chosen")
            self._sg_set_port(int(vals["channel"]))
        finally:
            self._quiet = False
        self.sg = SgConfig(**vals)
        if getattr(self, "txworker", None) is not None:
            self.txworker.apply(self.sg)
        self._sg_sync_pin()
        self._sg_wave_changed(self.cb_sg_wave.currentText())

    def _box_freq(self):
        b = Box("Frequency", A_TUNE)
        self.e_cen = HzEdit(self.cfg.center)
        self.e_span = HzEdit(self.cfg.span)
        self.e_start = HzEdit(self.cfg.start)
        self.e_stop = HzEdit(self.cfg.stop)
        b.row("Center", self.e_cen)
        b.row("Span", self.e_span)
        b.row("Start", self.e_start)
        b.row("Stop", self.e_stop)
        self.e_cen.changed.connect(self._cen_span)
        self.e_span.changed.connect(self._cen_span)
        self.e_start.changed.connect(self._start_stop)
        self.e_stop.changed.connect(self._start_stop)

        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        for label, s in (("Full", -1), ("1G", 1e9), ("100M", 100e6),
                         ("10M", 10e6), ("1M", 1e6)):
            btn = QtWidgets.QPushButton(label)
            btn.setFixedHeight(22)
            btn.clicked.connect(lambda _, v=s: self._preset(v))
            bar.addWidget(btn)
        h = QtWidgets.QWidget()
        h.setLayout(bar)
        b.full(h)

        self.c_zero = QtWidgets.QCheckBox("Zero span (waveform vs time)")
        self.c_zero.toggled.connect(self._zero)
        b.full(self.c_zero)
        self.s_ztime = QtWidgets.QDoubleSpinBox()
        self.s_ztime.setRange(1, 50000)
        self.s_ztime.setValue(200)
        self.s_ztime.setSuffix(" us")
        self.s_ztime.valueChanged.connect(self._ztime)
        b.row("Time span", self.s_ztime)
        return b

    def _box_trigger(self):
        """
        The sweep trigger: Free Run or Video.

        Video is the trigger a swept analyzer has always had, and it needs no
        wiring: take the sweep, and only put it on screen if its own detected
        level crossed a threshold. Everything downstream -- traces, markers,
        channel power, the limit line -- then sees only sweeps that qualified,
        which is the point. Below the threshold the display HOLDS rather than
        going blank: what you want when you leave it watching for a burst is
        the last thing that happened, not an empty graticule.

        What was here before: Role (master/slave) and Signal, which armed the
        FPGA's own trigger over the mini_exp line for a zero-span capture.
        Both are gone on request. The worker path behind them is untouched
        and stays disabled -- see Config.trigger_role -- so nothing that was
        measured about it has been thrown away, but no control arms it now.
        """
        b = Box("Trigger", A_TUNE)
        self.cb_trig_mode = QtWidgets.QComboBox()
        for label, val in TRIG_MODES:
            self.cb_trig_mode.addItem(label, val)
        self.cb_trig_mode.setToolTip(
            "Free Run -- every sweep goes on screen. The normal state.\n\n"
            "Video -- a sweep is only accepted when its strongest point is\n"
            "at or above the trigger level. Between crossings the display\n"
            "holds and the status line says it is waiting, so an empty\n"
            "screen never gets mistaken for a dead instrument.\n\n"
            "The radio keeps sweeping either way, so the span, the RBW and\n"
            "everything else stay live while it waits.")
        self.cb_trig_mode.currentIndexChanged.connect(self._trig_changed)
        b.row("Source", self.cb_trig_mode)

        self.s_trig_level = QtWidgets.QDoubleSpinBox()
        self.s_trig_level.setRange(-150.0, 40.0)
        self.s_trig_level.setValue(-50.0)
        self.s_trig_level.setSuffix(" dBm")
        self.s_trig_level.setSingleStep(5.0)
        self.s_trig_level.setEnabled(False)
        self.s_trig_level.setToolTip(
            "The level a sweep has to reach to be accepted, measured the\n"
            "same way the trace is -- so it is the number you read off the\n"
            "screen, including the Cal offset.")
        self.s_trig_level.valueChanged.connect(
            lambda v: self._push(trig_level=v))
        b.row("Level", self.s_trig_level)

        self.l_trig = QtWidgets.QLabel("free run")
        self.l_trig.setWordWrap(True)
        b.row("Status", self.l_trig)
        return b

    def _box_ampl(self):
        b = Box("Amplitude", A_VIEW)
        self.s_ref = QtWidgets.QDoubleSpinBox()
        self.s_ref.setRange(-150, 40)
        self.s_ref.setValue(-20)
        self.s_ref.setSuffix(" dBm")
        self.s_ref.setSingleStep(5)
        self.s_ref.valueChanged.connect(lambda _: self._axes())
        b.row("Ref level", self.s_ref)
        self.s_div = QtWidgets.QDoubleSpinBox()
        self.s_div.setRange(1, 20)
        self.s_div.setValue(10)
        self.s_div.setSuffix(" dB")
        self.s_div.valueChanged.connect(lambda _: self._axes())
        b.row("dB / div", self.s_div)
        self.s_gain = QtWidgets.QDoubleSpinBox()
        self.s_gain.setRange(self.src.gain_min, self.src.gain_max)
        self.s_gain.setValue(self.cfg.gain)
        self.s_gain.setSuffix(" dB")
        self.s_gain.valueChanged.connect(lambda v: self._push(gain=v))
        b.row("RF gain", self.s_gain)
        self.s_cal = QtWidgets.QDoubleSpinBox()
        self.s_cal.setRange(-80, 80)
        self.s_cal.setSuffix(" dB")
        # Seeded from the config. It was not until 2026-09-01, which was
        # invisible while the default was 0.0 and a straight lie the moment
        # it was not -- the box read 0 while every level carried 8 dB. The
        # default is 0.0 again (see RX_CAL_DEFAULT_DB) and the seeding
        # stays: a recalled setup brings its own offset here too.
        self.s_cal.setValue(self.cfg.cal_offset)
        self.s_cal.setToolTip(
            f"Added to every level on screen.\n"
            f"Starts at {RX_CAL_DEFAULT_DB:+.0f} dB -- absolute dBm is an\n"
            f"estimate on this board, see READ_ME.\n"
            f"Inject a known level and adjust until the marker agrees.")
        self.s_cal.valueChanged.connect(lambda v: self._push(cal_offset=v))
        b.row("Cal offset", self.s_cal)

        # Two grey notes sat here until 2026-09-29 -- the board table's
        # figure at the centre frequency, and "no mechanical attenuator on
        # this board" -- and Kfir removed both on request. The facts they
        # stated are still true and still in READ_ME; the board table is
        # still applied, and the status bar still says when it is held flat
        # outside the measured band (l_calx), which is the part that is a
        # warning rather than a note.

        btn = QtWidgets.QPushButton("Autoscale")
        btn.clicked.connect(self._autoscale)
        b.full(btn)
        return b

    def _box_bw(self):
        b = Box("RBW", A_TUNE)
        # There was an Oversample checkbox here for one day, 2026-08-31 to
        # 2026-09-01. Kfir removed it: "Remove the Oversample option in the
        # RBW tab." Config.oversample and the whole worker path behind it
        # stay at their defaults, which is exactly what the unticked box
        # produced -- so nothing about a sweep changed. See 5.25 for why the
        # recall path has to FORCE the field rather than leave it, and 5.24
        # for the measurement that made it tempting in the first place.
        self.c_auto = QtWidgets.QCheckBox("Auto RBW (follow span)")
        self.c_auto.setChecked(True)
        self.c_auto.toggled.connect(self._auto_rbw)
        b.full(self.c_auto)
        self.cb_rbw = QtWidgets.QComboBox()
        self._fill_rbw()
        self.cb_rbw.currentIndexChanged.connect(self._pick_rbw)
        b.row("RBW", self.cb_rbw)
        self.cb_rbw.setToolTip(
            "The resolution bandwidth the sweep really measures with. The\n"
            "status bar shows what came out, and the two agree: if a setting\n"
            "cannot be met, the bar says so rather than showing one number\n"
            "here and a different one down there.\n\n"
            "Only values this radio can actually deliver are listed -- the\n"
            "coarsest is set by the shortest usable window.")
        self.cb_win = QtWidgets.QComboBox()
        self.cb_win.addItems(WINDOWS)
        self.cb_win.setToolTip(
            "The RBW filter shape. Measured on this build:\n\n"
            "  shape             scallop   sidelobe   ENBW\n"
            "  Gaussian          -0.67 dB   -96 dB    2.26 bins\n"
            "  Flat Top          -0.01 dB   -95 dB    3.77 bins\n"
            "  Blackman-Harris   -0.83 dB   -92 dB    2.00 bins\n"
            "  Nuttall           -0.85 dB   -98 dB    1.98 bins\n"
            "  Hann              -1.42 dB   -32 dB    1.50 bins\n"
            "  Rectangular       -3.92 dB   -13 dB    1.00 bins\n\n"
            "Gaussian is the default because it is the shape a swept bench\n"
            "analyzer's IF filter has -- ENBW / -3 dB comes out at 1.066,\n"
            "the textbook Gaussian value -- so skirts and shoulders look\n"
            "the way they do on the instrument this replaces.\n\n"
            "Scallop loss is how far a CW tone can read LOW when it falls\n"
            "between bins. For absolute CW amplitude switch to Flat Top:\n"
            "0.01 dB against 0.67. Everything relative is unaffected.\n\n"
            "For the frequency counter use Blackman-Harris or Nuttall.")
        self.cb_win.currentTextChanged.connect(lambda t: self._push(window=t))
        b.row("RBW shape", self.cb_win)
        # Detector and Max IBW were here and are gone, on request. Both
        # remain Config fields at their defaults -- Peak, and 40 MS/s per
        # segment -- so the sweep behaves exactly as it did with the controls
        # sitting untouched, and a saved setup that carries either one still
        # restores it (_apply_setup writes the config, not the widgets).
        return b


    def _box_traces(self):
        b = Box("Traces", A_MEASURE)
        self.cb_tr = []
        self.c_tr_view = []
        for i, mode in enumerate(self.cfg.trace_modes):
            row = QtWidgets.QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            sw = QtWidgets.QLabel("\u25ac")
            sw.setStyleSheet(f"color:{TRACE_COLORS[i]};font-size:15px;")
            sw.setFixedWidth(16)
            cb = QtWidgets.QComboBox()
            cb.addItems(TRACE_MODES)
            cb.setCurrentText(mode)
            cb.currentTextChanged.connect(lambda _t, k=i: self._trace_mode(k))
            # View, in the bench-analyzer sense: freeze this one trace where
            # it is and keep drawing it, while the others carry on sweeping.
            vw = QtWidgets.QCheckBox("View")
            vw.setToolTip(
                "Freeze this trace. It stays on screen exactly as it is and\n"
                "the sweep stops writing to it -- the other traces carry\n"
                "on, so you can hold a known-good spectrum beside the\n"
                "live one.\n\n"
                "Unticking resumes the mode it was in, so a frozen Max\n"
                "Hold carries on holding rather than starting again.\n\n"
                "Markers, the peak table and a CSV export all read the\n"
                "frozen data, because that is what is on screen. Clear\n"
                "traces and a change of span still clear it -- a held\n"
                "trace from a different span would be drawn against the\n"
                "wrong frequencies.")
            vw.toggled.connect(lambda _c, k=i: self._trace_view(k))
            row.addWidget(sw)
            row.addWidget(cb, 1)
            row.addWidget(vw)
            w = QtWidgets.QWidget()
            w.setLayout(row)
            b.full(w)
            self.cb_tr.append(cb)
            self.c_tr_view.append(vw)
        self._trace_view_enable()
        self.s_avg = QtWidgets.QSpinBox()
        self.s_avg.setRange(2, 1000)
        self.s_avg.setValue(10)
        self.s_avg.valueChanged.connect(lambda v: self._push(avg_count=v))
        b.row("Averages", self.s_avg)
        # Back on 2026-09-23, HERE rather than in RBW where 5.14 removed it
        # from: the macro (Kfir's site procedure) captures the same carrier
        # with Positive Peak and with Average, so it has to be a setting a
        # user can choose and a macro step can store. How a point is formed
        # belongs with the traces it forms. Channel power and OBW do not
        # read it -- they integrate power_mean (bug 35).
        self.cb_det = QtWidgets.QComboBox()
        self.cb_det.addItems(DETECTORS)
        self.cb_det.setCurrentText(self.cfg.detector)
        self.cb_det.setToolTip(
            "How each trace point is made from the bins under it.\n"
            "Peak (Positive Peak): the largest -- a narrow carrier is never\n"
            "lost between points. The default.\n"
            "Average: the power (RMS) mean of the bins.\n"
            "Min: the smallest. Sample: the bin in the middle.\n\n"
            "Channel power and OBW are not affected by this: they always\n"
            "integrate the true power.")
        self.cb_det.currentTextChanged.connect(
            lambda t: self._push(detector=t))
        b.row("Detector", self.cb_det)
        btn = QtWidgets.QPushButton("Clear traces")
        btn.clicked.connect(self._clear)
        b.full(btn)
        return b

    def _box_markers(self):
        b = Box("Markers", A_MEASURE)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.cb_m = QtWidgets.QComboBox()
        self.cb_m.addItems([f"M{i+1}" for i in range(N_MARKERS)])
        self.c_mon = QtWidgets.QCheckBox("On")
        self.c_mdelta = QtWidgets.QCheckBox("\u0394 to M1")
        row.addWidget(self.cb_m)
        row.addWidget(self.c_mon)
        row.addWidget(self.c_mdelta)
        w = QtWidgets.QWidget()
        w.setLayout(row)
        b.full(w)
        self.cb_m.currentIndexChanged.connect(self._sync_marker)
        self.cb_m.currentIndexChanged.connect(lambda _: self._draw_markers())
        self.c_mon.toggled.connect(self._marker_on)
        self.c_mdelta.toggled.connect(self._marker_delta)

        # Put a marker on a frequency you TYPE. Asked for on 2026-09-01:
        # "There is no option to set Marker on a manual written frequency,
        # only Peaks, Centers and such."
        #
        # It is the same one line of state that fixed the marker-slides bug
        # -- once the anchor is a frequency rather than a screen position,
        # placing one by hand is just writing that anchor. It could not have
        # been done honestly before: an index-anchored marker put on a typed
        # frequency would have wandered off it at the first zoom.
        #
        # A button as well as the edit box's own signal, because
        # HzEdit.changed does not fire when the text is committed unchanged
        # (see HzEdit._commit) -- so re-pressing after a peak search moved
        # the marker elsewhere would otherwise do nothing.
        mrow = QtWidgets.QHBoxLayout()
        mrow.setContentsMargins(0, 0, 0, 0)
        self.e_mfreq = HzEdit(self.cfg.center)
        self.e_mfreq.setToolTip(
            "Frequency for the selected marker. Type it and press Enter,\n"
            "or use Set.\n\n"
            "Accepts 1.8G, 434M, 100k, 2440e6 and plain hertz.\n\n"
            "The marker stays at that frequency while you pan and zoom --\n"
            "it is anchored to the signal, not to the screen. Set it\n"
            "outside the current span and it waits off screen until the\n"
            "span comes back to it.")
        self.b_mfreq = QtWidgets.QPushButton("Set")
        self.b_mfreq.setFixedHeight(23)
        self.b_mfreq.setMaximumWidth(52)
        self.b_mfreq.setToolTip("Put the selected marker on the frequency"
                                " on the left.")
        self.e_mfreq.changed.connect(lambda _hz: self._marker_to_typed())
        self.b_mfreq.clicked.connect(self._marker_to_typed)
        mrow.addWidget(self.e_mfreq, 1)
        mrow.addWidget(self.b_mfreq, 0)
        wm = QtWidgets.QWidget()
        wm.setLayout(mrow)
        b.row("At", wm)

        # Asked for as "an option to move the marker on the frequency
        # domain, just like zooming in or out with the cursor", 2026-09-01.
        # A tick rather than a bare gesture, because the plot's plain drag
        # already means RETUNE and silently taking that over would break the
        # one gesture people use most. Ctrl inverts whichever mode is set,
        # so the other one is always a keypress away.
        self.c_mmouse = QtWidgets.QCheckBox("Move with mouse")
        self.c_mmouse.setToolTip(
            "Click or drag on the trace to move the selected marker.\n\n"
            "While this is ticked the plain drag moves the marker instead\n"
            "of retuning; hold Ctrl to pan as usual. With it unticked,\n"
            "Ctrl-drag moves the marker. The wheel always zooms.\n\n"
            "The marker lands on a frequency and stays there when you pan\n"
            "or zoom afterwards.")
        self.c_mmouse.toggled.connect(self._marker_mouse)
        b.full(self.c_mmouse)

        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(3)
        # "At center" was asked for on 2026-09-18 -- "add an option to set
        # Marker at CF, just like the SA we've characterized" -- and it is
        # the OPPOSITE of the "To center" button that was already here:
        # this one moves the MARKER onto the centre frequency, that one
        # retunes the RADIO onto the marker. Two controls whose names differ
        # by one word have to be read together rather than hunted for, so
        # they sit side by side and each states its direction in a tooltip.
        # The other four say what they do in their own names and are left
        # without one, for the reason KEY_NO_TIP exists.
        acts = [
            ("Peak search", self._peak, ""),
            ("Next peak", self._next_peak, ""),
            ("At center", self._marker_at_center,
             "Put the selected marker on the centre frequency.\n\n"
             "It is anchored there afterwards and does NOT follow the\n"
             "centre: retune and the marker stays on the signal it was\n"
             "put on, which is what makes it useful for watching one\n"
             "carrier while you look around it.\n\n"
             "The opposite of To center, which moves the radio instead."),
            ("To center", self._to_center,
             "Retune so the centre frequency is the selected marker's.\n\n"
             "This moves the RADIO, not the marker -- the opposite way\n"
             "round from At center."),
            ("All off", self._all_off, ""),
            ("Harmonics of M1", self._harmonics, ""),
            ("To strongest", self._tune_strongest, ""),
        ]
        for i, (label, fn, tip) in enumerate(acts):
            btn = QtWidgets.QPushButton(label)
            btn.setFixedHeight(23)
            if tip:
                btn.setToolTip(tip)
            btn.clicked.connect(fn)
            # An odd count leaves the last button alone on its row. Span it
            # across both columns rather than leaving a half-width stub with
            # a hole beside it.
            wide = (i == len(acts) - 1) and (len(acts) % 2 == 1)
            grid.addWidget(btn, i // 2, i % 2, 1, 2 if wide else 1)
        w2 = QtWidgets.QWidget()
        w2.setLayout(grid)
        b.full(w2)

        self.tbl = QtWidgets.QTableWidget(0, 3)
        self.tbl.setHorizontalHeaderLabels(["#", "Frequency", "Level"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        # About six rows. Ten markers can be on at once since 2026-09-01,
        # and this is a FIXED height on purpose: letting it grow with the
        # rows would push the panel past the "do not cover the trace"
        # ceiling (CLAUDE.md 5.12), so it scrolls instead.
        self.tbl.setFixedHeight(176)
        hh = self.tbl.horizontalHeader()
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
        self.tbl.setColumnWidth(0, 30)
        hh.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        b.full(self.tbl)
        return b

    def _default_shot_dir(self) -> str:
        # Off APP_NAME rather than a literal: the program has been renamed
        # twice now (2026-08-19 and 2026-09-02), and each time a hard-coded
        # copy of the name is one more place to miss. Screenshots already
        # taken stay in the old folder -- they are the user's files and
        # moving them behind their back would be worse than leaving them.
        home = os.path.expanduser("~")
        pics = os.path.join(home, "Pictures")
        base = pics if os.path.isdir(pics) else home
        return os.path.join(base, APP_NAME)

    def _box_reference(self):
        """
        Freeze the current trace, then compare against it later.

        This is the one that matters for firmware work: capture a known-good
        spectrum, change the code, and see the difference directly instead of
        eyeballing two screenshots side by side.
        """
        b = Box("Reference / Compare", A_MEASURE)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        bs = QtWidgets.QPushButton("Set reference")
        bs.setToolTip("Freeze what is on screen now as the reference.")
        bs.clicked.connect(self._set_ref)
        bc = QtWidgets.QPushButton("Clear")
        bc.clicked.connect(self._clear_ref)
        row.addWidget(bs); row.addWidget(bc)
        w = QtWidgets.QWidget(); w.setLayout(row); b.full(w)

        self.c_ref_show = QtWidgets.QCheckBox("Show reference (white)")
        self.c_ref_show.setChecked(True)
        self.c_ref_show.toggled.connect(lambda _: self._refresh_ref_curve())
        b.full(self.c_ref_show)

        self.c_ref_diff = QtWidgets.QCheckBox("Subtract it (show dB change)")
        self.c_ref_diff.setToolTip(
            "Plot live minus reference. The vertical axis switches to dB of\n"
            "change, so 0 means nothing moved.")
        self.c_ref_diff.toggled.connect(self._toggle_diff)
        b.full(self.c_ref_diff)

        self.l_ref = QtWidgets.QLabel("no reference stored")
        self.l_ref.setWordWrap(True)
        self.l_ref.setStyleSheet("color:#8fa6bb;")
        b.full(self.l_ref)
        return b

    def _box_limit(self):
        b = Box("Limit Line", A_MEASURE)
        self.c_limit = QtWidgets.QCheckBox("Enable pass / fail")
        self.c_limit.setToolTip("Flags any point above the limit. Useful as a\n"
                                "quick emissions or spur check after a change.")
        self.c_limit.toggled.connect(self._toggle_limit)
        b.full(self.c_limit)
        self.s_limit = QtWidgets.QDoubleSpinBox()
        self.s_limit.setRange(-160, 40)
        self.s_limit.setValue(-60)
        self.s_limit.setSuffix(" dBm")
        self.s_limit.valueChanged.connect(lambda _: self._refresh_limit())
        b.row("Limit", self.s_limit)
        self.l_verdict = QtWidgets.QLabel("--")
        self.l_verdict.setStyleSheet("font-weight:bold;")
        b.row("Result", self.l_verdict)
        return b

    def _box_meas_menu(self):
        """
        The Measurement key's page: a plain list of the three measurements,
        each opening its own settings in the same place, and under it the
        Avg | Hold all three share.

        Kfir, 2026-09-27: "When pressing the Measurement tab, I want all the
        features (OBW, Channel Power, ACLR) to show as a simple list, and
        after clicking one of them, you can open their windows for
        configurations." The averaging sits here rather than inside one of
        them because it governs all three -- in Channel Power it needed a
        note in OBW pointing back at it, and a control that reaches into a
        panel you cannot see has to admit it. Here it simply does not.
        """
        # Built at the point the channel power box always was, so nothing
        # that runs during construction sees a different order.
        self._meas_subs = [("Channel Power", self._box_chpower()),
                           ("Occupied Bandwidth", self._box_measure()),
                           ("ACLR", self._box_aclr())]
        self._panel_key = {}
        b = Box("Measurement", A_MEASURE)
        self._meas_state = {}
        for name, box in self._meas_subs:
            btn = QtWidgets.QPushButton(name)
            btn.setFixedHeight(26)
            btn.clicked.connect(lambda _c, n=name: self._open_panel(n))
            lab = QtWidgets.QLabel("off")
            lab.setFixedWidth(34)
            lab.setAlignment(QtCore.Qt.AlignCenter)
            row = QtWidgets.QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(btn, 1)
            row.addWidget(lab)
            w = QtWidgets.QWidget(); w.setLayout(row); b.full(w)
            self._meas_state[name] = lab
            # And the way back, at the top of each settings panel.
            back = QtWidgets.QPushButton("< Measurement")
            back.setFixedHeight(22)
            back.clicked.connect(lambda _c: self._open_panel("Measurement"))
            box.f.insertRow(0, back)
        b.full(rule())        # Avg | Hold over successive readings. A single sweep's channel power
        # jumps around by whatever the noise happens to be doing, which makes
        # the number hard to read and hard to compare. Averaging in the LINEAR
        # power domain (never in dB, which biases low) settles it down the way
        # a bench analyzer's Avg|Hold does.
        self.cb_meas_avg = QtWidgets.QComboBox()
        # No "Off". A single sweep's channel power moves by whatever the noise
        # happened to do, so the un-averaged number is not a reading anyone
        # can use -- measured on a noise-dominated carrier, 20 averages cut
        # the standard deviation from 0.090 dB to 0.006 dB while moving the
        # mean by 0.01 dB. Count 1 is still available if a genuinely
        # instantaneous value is ever wanted, and is honest about being one.
        self.cb_meas_avg.addItem("Average", "Average")
        self.cb_meas_avg.addItem("Max hold", "Max hold")
        self.cb_meas_avg.setToolTip("Applies to all three measurements.")

        self.cb_meas_avg.currentIndexChanged.connect(self._meas_mode_changed)
        b.row("Avg | Hold", self.cb_meas_avg)

        self.s_meas_avg = QtWidgets.QSpinBox()
        # From 1, which is the old "Off" -- one reading, no averaging --
        # rather than a separate mode that reads as "measurement disabled".
        self.s_meas_avg.setRange(1, 1000)
        self.s_meas_avg.setValue(20)
        self.s_meas_avg.setToolTip("Readings to average or hold over. 1 = none.")

        self.s_meas_avg.valueChanged.connect(lambda _: self._meas_reset())
        b.row("Count", self.s_meas_avg)

        for c in (self.c_chp, self.c_obw, self.c_aclr):
            c.toggled.connect(lambda _on: self._meas_menu_sync())
        self._meas_menu_sync()
        return b

    def _meas_menu_sync(self) -> None:
        """ON / off beside each item, so the list says what is running."""
        on = {"Channel Power": self.c_chp.isChecked(),
              "Occupied Bandwidth": self.c_obw.isChecked(),
              "ACLR": self.c_aclr.isChecked()}
        for n, lab in self._meas_state.items():
            lab.setText("ON" if on[n] else "off")
            lab.setStyleSheet(f"color:{A_MEASURE};font-weight:bold;"
                              if on[n] else "color:#6f7d8a;")
    def _box_chpower(self):
        """
        Channel power, on a key of its own.

        Asked for on 2026-08-26: "take the CH Power out of Measure and put it
        under a new window called Measurement". It is the biggest measurement
        in the app -- six controls plus the averaging -- and it was sharing a
        box with the occupied bandwidth and a panel with the limit line, the
        peak table and the identifier. That panel was the only one in the
        window that genuinely had to scroll.

        Avg | Hold came WITH it rather than staying behind, because it is
        mostly about settling a power figure. It still governs the OBW
        readout under Measure, which is a control acting across two panels --
        so both ends say so: the tooltip here names OBW, and the OBW box
        carries a line pointing back here. A control that reaches somewhere
        you cannot see has to admit it.
        """
        b = Box("Channel Power", A_MEASURE)
        self.c_chp = QtWidgets.QCheckBox("Channel power")
        self.c_chp.setToolTip(
            "The master switch. Each of the ten channels has its own tick\n"
            "below; every one that is on is measured on every sweep, and\n"
            "they all appear in the table together.")
        self.c_chp.toggled.connect(self._chp_toggled)
        b.full(self.c_chp)
        # Ten channels, one set of editors and a selector (2026-09-14, on
        # request). Thirty boxes would not fit: this panel was already the
        # tallest in the window and had to be split out of Measure for it
        # (5.18). A selector plus a table is also the idiom this window
        # already uses for its ten markers, so there is nothing new to learn.
        self.cb_chsel = QtWidgets.QComboBox()
        for _i in range(N_CHP):
            self.cb_chsel.addItem(f"Channel {_i + 1}", _i)
        self.cb_chsel.setToolTip(
            "Which channel the three boxes below are about. Picking one\n"
            "does not switch it on or off -- the tick under here does that,\n"
            "and every ticked channel is measured whichever one is showing.\n"
            "The selected channel's band is the bright one on the plot.")
        self.cb_chsel.currentIndexChanged.connect(self._chp_sel_changed)
        b.row("Edit", self.cb_chsel)
        self.c_chp_ch = QtWidgets.QCheckBox("Measure this channel")
        self.c_chp_ch.setChecked(True)
        self.c_chp_ch.setToolTip(
            "Measure the channel selected above. Any number of the ten can\n"
            "be on at once -- they are all integrated from the same sweep,\n"
            "so their powers are directly comparable.")
        self.c_chp_ch.toggled.connect(self._chp_ch_toggled)
        b.full(self.c_chp_ch)
        # The channel always has its own centre frequency, typed in here.
        # The "Use this centre, not the span's" tick that used to sit above
        # this went on 2026-08-30, on request: following the span meant
        # retuning the analyzer to measure a carrier off to one side, which
        # moves everything else on screen, and having two ways to say where
        # the channel is meant the shaded band could sit somewhere the box
        # did not name.
        #
        # It is seeded from the sweep centre the first time Channel power is
        # switched on (_chp_seed_cf), so ticking the box still measures what
        # is in front of you without any typing. After that the typed value
        # is kept for good -- it is the whole point of the control.
        self.e_chcf = HzEdit(self.cfg.center)
        self.e_chcf.setToolTip(
            "Where the channel is. The shaded band on the plot always shows\n"
            "exactly what is being integrated, and the readout says so\n"
            "plainly -- PARTIAL, or 'outside span' -- if the band does not\n"
            "fit on screen, rather than quietly integrating the part that\n"
            "does.")
        self.e_chcf.changed.connect(self._chp_cf_typed)
        b.row("Channel CF", self.e_chcf)
        self.b_chcf_here = QtWidgets.QPushButton("CF = marker 1")
        self.b_chcf_here.setToolTip(
            "Drop the channel centre on marker 1 -- peak-search it first and\n"
            "the integration window lands on the carrier without any typing.")
        self.b_chcf_here.clicked.connect(self._chp_cf_from_marker)
        b.full(self.b_chcf_here)
        self.e_chbw = HzEdit(2e6)
        # Changing the integration width redefines THIS channel's
        # measurement, so its accumulated average describes something else
        # now -- and only its own, since the other nine are unchanged.
        self.e_chbw.changed.connect(lambda _: self._chp_bw_typed())
        b.row("Width", self.e_chbw)
        self.l_chp = QtWidgets.QLabel("--")
        self.l_chp.setStyleSheet("color:#4ac6ff;font-weight:bold;")
        self.l_chp.setToolTip(
            "The selected channel's power. Every channel that is on is in\n"
            "the table below, including this one.")
        b.row("Power", self.l_chp)
        # All the channels at once, which is the point of the change: a
        # readout showing one of ten would make ten channels a set of
        # settings rather than a measurement.
        self.tbl_chp = QtWidgets.QTableWidget(0, 4)
        self.tbl_chp.setHorizontalHeaderLabels(
            ["Ch", "Centre", "Width", "Power"])
        self.tbl_chp.verticalHeader().setVisible(False)
        self.tbl_chp.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_chp.setSelectionMode(
            QtWidgets.QAbstractItemView.NoSelection)
        # Fixed height and scrolling past about five rows, for the reason
        # the peak table is fixed (see N_MARKERS): a table that grew with
        # its contents would push this panel past _place_panel's ceiling the
        # moment somebody ticked all ten, and the trace would go under it.
        self.tbl_chp.setFixedHeight(148)
        _hh = self.tbl_chp.horizontalHeader()
        _hh.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        for _c in (1, 2, 3):
            _hh.setSectionResizeMode(_c, QtWidgets.QHeaderView.Stretch)
        self.tbl_chp.setVisible(False)
        b.full(self.tbl_chp)
        return b

    def _box_measure(self):
        """
        What is left under the Measure key: the occupied bandwidth.

        Channel power moved to its own key on 2026-08-26 (see _box_chpower),
        and it took the Avg | Hold controls with it -- which still govern the
        readout below. The note says so, because a setting that lives behind
        a different key is otherwise something you have to go looking for.
        """
        b = Box("Occupied Bandwidth", A_MEASURE)
        self.c_obw = QtWidgets.QCheckBox("Occupied bandwidth (99%)")
        self.c_obw.toggled.connect(lambda _: self._refresh_meas())
        b.full(self.c_obw)
        self.l_obw = QtWidgets.QLabel("--")
        self.l_obw.setStyleSheet("color:#4ac6ff;font-weight:bold;")
        b.row("OBW", self.l_obw)

        return b

    # -- reference -------------------------------------------------------
    def _set_ref(self) -> None:
        d = self._ref_trace()
        if self.last is None or d is None:
            self._message("nothing to store yet")
            return
        self.ref = d.copy()
        self.ref_freqs = self.last.freqs.copy()
        self.ref_note = (f"{datetime.now().strftime('%H:%M:%S')}  "
                         f"{fmt_hz_short(self.cfg.center)} span "
                         f"{fmt_hz_short(self.cfg.span)}  "
                         f"RBW {fmt_hz_short(self.last.rbw)}")
        self.l_ref.setText("reference: " + self.ref_note)
        self._refresh_ref_curve()
        self._message("reference stored")

    def _clear_ref(self) -> None:
        self.ref = self.ref_freqs = None
        self.ref_note = ""
        self.l_ref.setText("no reference stored")
        self.c_ref_diff.setChecked(False)
        self._refresh_ref_curve()

    def _ref_usable(self) -> bool:
        return (self.ref is not None and self.last is not None
                and self.ref.size == self.last.freqs.size)

    def _refresh_ref_curve(self) -> None:
        show = (self.c_ref_show.isChecked() and not self._diff
                and self.ref is not None and self.ref_freqs is not None)
        if show:
            self.ref_curve.setData(self.ref_freqs, self.ref)
        self.ref_curve.setVisible(bool(show))

    def _toggle_diff(self, on: bool) -> None:
        if on and self.ref is None:
            self.c_ref_diff.setChecked(False)
            self._message("set a reference first")
            return
        self._diff = on
        # The vertical axis stops being absolute power in this mode, so say so
        # rather than leaving a dBm label on a difference.
        self.plot.setLabel("left", "Change vs reference" if on else "Amplitude",
                           units="dB" if on else "dBm")
        self._refresh_ref_curve()
        self._axes()

    # -- limit line ------------------------------------------------------
    def _toggle_limit(self, on: bool) -> None:
        self.limit_line.setVisible(on)
        self.l_verdict.setText("--" if not on else "...")
        self._refresh_limit()

    def _refresh_limit(self) -> None:
        self.limit_line.setPos(self.s_limit.value())
        if not self.c_limit.isChecked() or self.last is None:
            return
        d = self._displayed()
        if d is None:
            return
        over = int(np.count_nonzero(d > self.s_limit.value()))
        if over:
            self.l_verdict.setText(f"FAIL  ({over} pts)")
            self.l_verdict.setStyleSheet("color:#ff453a;font-weight:bold;")
        else:
            self.l_verdict.setText("PASS")
            self.l_verdict.setStyleSheet("color:#7CFF6B;font-weight:bold;")

    # -- channel power / OBW --------------------------------------------
    def _chp_commit(self) -> None:
        """Take the three editors back into the selected channel's slot.

        The list is the truth and the boxes are a view of one entry of it,
        so everything that reads the channels commits first. It PULLS from
        the widgets rather than being pushed from their signals on purpose:
        HzEdit does not emit `changed` for a programmatic setValue -- the
        same fact _chp_seed_cf turns on -- so a list fed only by the edit
        signals would silently miss a centre that arrived from the marker
        button, a recalled setup or a test.
        """
        i = self._chp_sel
        self.chp_cf[i] = float(self.e_chcf.value())
        self.chp_bw[i] = float(self.e_chbw.value())
        self.chp_on[i] = bool(self.c_chp_ch.isChecked())

    def _chp_load(self, i: int) -> None:
        """Show channel i in the three editors, quietly -- writing a value
        into a box is not the user setting it, and the handlers behind these
        boxes throw the Avg|Hold accumulators away."""
        # The PREVIOUS value goes back, not False: _apply_setup calls this
        # from inside its own quiet stretch, and clearing the flag there
        # would let the settings that follow push a half-applied config.
        was, self._quiet = self._quiet, True
        try:
            self.e_chcf.setValue(self.chp_cf[i])
            self.e_chbw.setValue(self.chp_bw[i])
            self.c_chp_ch.blockSignals(True)
            self.c_chp_ch.setChecked(bool(self.chp_on[i]))
            self.c_chp_ch.blockSignals(False)
        finally:
            self._quiet = was

    def _chp_mark_sel(self) -> None:
        """Brighten the selected channel's band. Ten identical bands on one
        graticule say nothing about which one the panel edits."""
        for i, band in enumerate(getattr(self, "chp_bands", [])):
            band.setBrush(pg.mkBrush(80, 200, 255,
                                     85 if i == self._chp_sel else 34))
            band.update()

    def _chp_sel_changed(self, _i: int = 0) -> None:
        # Commit the channel being LEFT before loading the new one, or an
        # edit made and not yet read would be overwritten by the load.
        self._chp_commit()
        self._chp_sel = max(0, int(self.cb_chsel.currentIndex()))
        self._chp_load(self._chp_sel)
        self._chp_mark_sel()
        # No _meas_reset here: selecting a channel changes what the panel is
        # SHOWING, not what is being measured, and the other nine channels'
        # averages have to survive a look at one of them.
        self._refresh_meas()

    def _chp_ch_toggled(self, on: bool) -> None:
        if on:
            self._chp_seed_cf()
        self._chp_commit()
        # Only this channel's accumulator is stale; the rest are measuring
        # the same thing they were. _meas_reset drops all of them, which is
        # the wrong trade with ten channels running, so it is one slot.
        self._meas_chp[self._chp_sel] = None
        self._meas_chp_n[self._chp_sel] = 0
        self._refresh_meas()

    def _chp_toggled(self, on: bool) -> None:
        if on:
            self._chp_seed_cf()
        self.tbl_chp.setVisible(on)
        # The panel is sized when it is opened, so a table that appears
        # inside an open one has to ask for the space again.
        self._place_panel()
        self._meas_reset()
        self._refresh_meas()

    def _chp_seed_cf(self) -> None:
        """Put the SELECTED channel's centre on the sweep centre, but only
        while nobody has ever set that channel by hand. Switching Channel
        power on then measures what is in front of you; once a value has
        been typed, or taken from a marker, or recalled from a setup, it is
        the user's and stays."""
        i = self._chp_sel
        if not self._chp_seeded[i]:
            self._quiet = True
            self.e_chcf.setValue(self.cfg.center)
            self._quiet = False
            self.chp_cf[i] = float(self.cfg.center)
            # Seeded once per session per channel, and that is the end of
            # it. Marking it here rather than leaning on the edit signal
            # matters: HzEdit does NOT emit `changed` for a programmatic
            # setValue, so a rule of "re-seed until somebody types" would
            # quietly overwrite a value that had been set from a marker or a
            # saved setup the next time Channel power was switched off and
            # on.
            self._chp_seeded[i] = True

    def _chp_cf_typed(self, _v: float) -> None:
        self._chp_seeded[self._chp_sel] = True
        self._chp_commit()
        self._meas_chp[self._chp_sel] = None
        self._meas_chp_n[self._chp_sel] = 0
        self._refresh_meas()

    def _chp_cf_from_marker(self) -> None:
        m = self.markers[0]
        if not m.on:
            self._message("marker 1 is off -- switch it on and peak-search it")
            return
        self.e_chcf.setValue(float(m.f))
        self._chp_seeded[self._chp_sel] = True
        self._chp_commit()
        self._meas_chp[self._chp_sel] = None
        self._meas_chp_n[self._chp_sel] = 0
        self._refresh_meas()
        self._message(f"channel {self._chp_sel + 1} centre set to "
                      f"{fmt_hz(float(m.f), 4)}")

    def _chp_bw_typed(self) -> None:
        self._chp_commit()
        self._meas_chp[self._chp_sel] = None
        self._meas_chp_n[self._chp_sel] = 0
        self._refresh_meas()

    def _meas_mode_changed(self, _i: int = 0) -> None:
        self._meas_reset()

    def _meas_mode(self) -> str:
        # "Average" rather than "Off" as the fallback: averaging is no longer
        # optional, so code that runs before the box is built (or in a
        # headless test) sees the same behaviour the UI gives.
        cb = getattr(self, "cb_meas_avg", None)
        return cb.currentData() if cb is not None else "Average"

    def _meas_reset(self) -> None:
        """Drop the Avg|Hold accumulators. Called whenever the thing being
        measured changes, so old readings never leak into a new setup."""
        # One accumulator per channel (N_CHP), in LINEAR power
        # (mW-equivalent). A list rather than a scalar since 2026-09-14 --
        # ten channels are folded independently from the same sweep.
        self._meas_chp = [None] * N_CHP
        self._meas_chp_n = [0] * N_CHP
        self._meas_obw = None      # accumulated Hz
        self._meas_obw_n = 0
        self._meas_aclr = {}       # ACLR: band index -> (mW, n)
        self._meas_seq = -1

    def _meas_fold(self, acc, n, value):
        """
        One reading into an Avg|Hold accumulator.

        Average uses the same 1/n-then-exponential convention as the trace
        averaging (Trace.update), so a fresh average converges quickly
        instead of crawling up from zero, then tracks with alpha = 1/count.
        """
        mode = self._meas_mode()
        count = max(int(self.s_meas_avg.value()), 1)
        if acc is None or count <= 1:
            return value, 1
        if mode == "Max hold":
            return max(acc, value), min(n + 1, count)
        n = min(n + 1, count)
        a = 1.0 / n
        return (1.0 - a) * acc + a * value, n

    def _meas_suffix(self, n: int) -> str:
        mode = self._meas_mode()
        count = int(self.s_meas_avg.value())
        if count <= 1:
            return "  (live)"
        tag = "avg" if mode == "Average" else "hold"
        return f"  ({tag} {min(n, count)}/{count})"

    def _refresh_meas(self) -> None:
        # The boxes are a view of ONE channel, so read them back into the
        # list before anything asks what the ten channels are.
        self._chp_commit()
        measuring = ((self.c_chp.isChecked() and any(self.chp_on))
                     or self.c_obw.isChecked() or self._aclr_on())
        if bool(self.cfg.meas_on) != measuring:
            # Tells the sweep whether to build the mean-power trace at all.
            self._push(meas_on=measuring)
        if self.last is None or self.cfg.zero_span or self._diff:
            self._chp_blank()
            self.l_obw.setText("--")
            self._aclr_blank()
            self._meas_reset()
            return

        # Only fold a given sweep in once. _refresh_meas() is also called
        # straight from the checkboxes and the width field, and without this
        # guard those clicks would each count the same sweep again and bias
        # the average towards whatever was on screen when you clicked.
        fresh = getattr(self, "_frame_seq", 0) != getattr(self, "_meas_seq", -1)
        if fresh:
            self._meas_seq = getattr(self, "_frame_seq", 0)

        f = self.last.freqs
        # THE measurement input, and it is not the trace on screen: a
        # detected trace does not carry its bucket's power, so integrating it
        # made the answer a function of the zoom (2.8 dB across the span
        # range, measured). mean_power() is the same axis, one value per
        # point, in linear-mean dBm. None until the first sweep after
        # switching a measurement on.
        pw = self.last.power_mean
        if pw is None or pw.size != f.size:
            pw = None
        if self.c_chp.isChecked() and pw is not None:
            self._chp_channels(f, pw, fresh)
        else:
            self._chp_blank()
        if self.c_obw.isChecked() and pw is not None:
            # Same argument as channel power: a percentile of a detected
            # trace moves with the bucket size, so this reads the mean-power
            # trace too.
            lin = 10.0 ** (pw / 10.0)
            tot = np.sum(lin)
            if tot > 0:
                c = np.cumsum(lin) / tot
                lo_i = int(np.searchsorted(c, 0.005))
                hi_i = int(np.searchsorted(c, 0.995))
                lo_i = min(max(lo_i, 0), f.size - 1)
                hi_i = min(max(hi_i, 0), f.size - 1)
                if hi_i > lo_i:
                    obw = float(f[hi_i] - f[lo_i])
                    if fresh:
                        self._meas_obw, self._meas_obw_n = self._meas_fold(
                            self._meas_obw, self._meas_obw_n, obw)
                    shown = self._meas_obw if self._meas_obw is not None else obw
                    self.l_obw.setText(fmt_hz_short(shown)
                                       + self._meas_suffix(self._meas_obw_n))
                else:
                    self.l_obw.setText("--")
            else:
                self.l_obw.setText("--")
        elif not self.c_obw.isChecked():
            self.l_obw.setText("--")
        if self._aclr_on() and pw is not None:
            self._aclr_refresh(f, pw, fresh)
        else:
            self._aclr_blank()

    def _chp_blank(self) -> None:
        """No channel power on screen: no readout, no table, no bands."""
        self.l_chp.setText("--")
        self.l_chp.setStyleSheet("color:#4ac6ff;font-weight:bold;")
        self.l_chp.setToolTip("")
        self.tbl_chp.setRowCount(0)
        for band in self.chp_bands:
            band.setVisible(False)
        for lab in getattr(self, "chp_labels", []):
            lab.setVisible(False)

    def _chp_channels(self, f, pw, fresh: bool) -> None:
        """
        Every channel that is switched on, measured off ONE sweep.

        Ten channels rather than one since 2026-09-14, and they are folded
        independently: each keeps its own Avg|Hold accumulator, so looking
        at channel 7 does not disturb the average settling on channel 2.
        Sharing the sweep is what makes the ten powers directly comparable
        -- they are all the same instant, the same RBW and the same gain.

        The arithmetic per channel is unchanged from the single-channel
        version and the reasons are still the ones in bug 35: it reads
        power_mean (the per-bucket LINEAR mean the sweep builds for the
        purpose) and never the trace on screen, the two buckets straddling
        the band edges are weighted by the fraction of them inside, and the
        RBW correction is the bin spacing over the RBW.
        """
        rows = []
        for i in range(N_CHP):
            band = self.chp_bands[i]
            lab = self.chp_labels[i]
            if not self.chp_on[i]:
                band.setVisible(False)
                lab.setVisible(False)
                if i == self._chp_sel:
                    self.l_chp.setText("--")
                    self.l_chp.setStyleSheet("color:#4ac6ff;font-weight:bold;")
                    self.l_chp.setToolTip("")
                continue
            cf, bw = self.chp_cf[i], self.chp_bw[i]
            lo, hi = cf - bw / 2, cf + bw / 2
            # Say it rather than quietly integrating whatever part of the
            # band happens to overlap the span -- a partly-off-screen channel
            # reads low, and low by an amount nothing on screen explains.
            # The integration itself is band_power_mw(), shared with ACLR.
            tot, partial = band_power_mw(f, pw, self.last.rbw, lo, hi)
            if tot is not None:
                if fresh:
                    self._meas_chp[i], self._meas_chp_n[i] = self._meas_fold(
                        self._meas_chp[i], self._meas_chp_n[i], tot)
                shown = (self._meas_chp[i] if self._meas_chp[i] is not None
                         else tot)
                txt = (f"{10*np.log10(max(shown,1e-30)):.2f} dBm"
                       + self._meas_suffix(self._meas_chp_n[i])
                       + ("  PARTIAL" if partial else ""))
                tip = ("The channel band runs off the edge of the span, so "
                       "this\nis the power of the part that is on screen -- "
                       "lower\nthan the real channel power. Widen the span "
                       "or move the\nchannel centre." if partial else "")
            else:
                txt, tip, partial = "outside span", "", True
            rows.append((i, cf, bw, txt))
            if i == self._chp_sel:
                self.l_chp.setText(txt)
                self.l_chp.setStyleSheet("color:#ffb340;font-weight:bold;"
                                         if partial else
                                         "color:#4ac6ff;font-weight:bold;")
                self.l_chp.setToolTip(tip)
            band.setRegion([lo, hi])
            band.setVisible(True)
            if tot is None:
                lab.setVisible(False)
            else:
                lab.setText(f"Channel {i + 1}\n"
                            f"{10*np.log10(max(shown, 1e-30)):.2f} dBm / "
                            f"{fmt_hz_short(bw)}")
                lab.setColor("#ffb340" if partial else (120, 215, 255))
                (_x0, _x1), (_y0, _y1) = self.plot.viewRange()
                lab.setPos(float(np.clip(cf, _x0, _x1)),
                           _y1 - (_y1 - _y0) * 0.03)
                lab.setVisible(True)
        self.tbl_chp.setRowCount(len(rows))
        for r, (i, cf, bw, txt) in enumerate(rows):
            cells = [str(i + 1), fmt_hz(float(cf), 4), fmt_hz_short(float(bw)),
                     txt]
            for c, v in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(v)
                if c:
                    it.setTextAlignment(QtCore.Qt.AlignRight
                                        | QtCore.Qt.AlignVCenter)
                # The row the panel's own boxes are editing, picked out the
                # same way its band is, so the table and the editors cannot
                # be read as being about different channels.
                if i == self._chp_sel:
                    ft = it.font()
                    ft.setBold(True)
                    it.setFont(ft)
                self.tbl_chp.setItem(r, c, it)

    # -- ACLR (2026-09-26) ---------------------------------------------------
    #
    # Kfir: "Add ACLR window. It shall have any Spectrum Analyzer option - To
    # automatically set the measure BWs based on regular LTE channels
    # (20,15,10,5,1.4MHz) ... Also the user can set the integ BW manually.
    # And please, do NOT set a lot of explanations when I hover over it."
    # So: no tooltips in this panel at all (a test holds it to that), and the
    # reasoning lives here instead.
    #
    # The carrier and every offset are integrated off ONE sweep with
    # band_power_mw(), the same integrator channel power uses, and each band
    # is folded through the shared Avg|Hold in LINEAR power before the ratio
    # is taken -- averaging dBc directly would bias it, the way dB averaging
    # biases channel power low. Choosing a standard fills Integ BW and
    # Spacing; typing either one switches the standard to Manual, which is
    # what an analyzer does and means the box always says what is measured.

    def _box_aclr(self):
        b = Box("ACLR", A_MEASURE)
        self.c_aclr = QtWidgets.QCheckBox("ACLR")
        self.c_aclr.toggled.connect(self._aclr_toggled)
        b.full(self.c_aclr)
        self.cb_aclr = QtWidgets.QComboBox()
        for lab, _chan, _integ in ACLR_PRESETS:
            self.cb_aclr.addItem(lab)
        self.cb_aclr.addItem(ACLR_MANUAL)
        self.cb_aclr.currentIndexChanged.connect(self._aclr_preset)
        b.row("Standard", self.cb_aclr)
        self.e_aclr_cf = HzEdit(self.cfg.center)
        self.e_aclr_cf.changed.connect(lambda _v: self._aclr_edited())
        b.row("Carrier CF", self.e_aclr_cf)
        self.e_aclr_bw = HzEdit(ACLR_PRESETS[0][2])
        self.e_aclr_bw.changed.connect(lambda _v: self._aclr_to_manual())
        b.row("Integ BW", self.e_aclr_bw)
        self.e_aclr_sp = HzEdit(ACLR_PRESETS[0][1])
        self.e_aclr_sp.changed.connect(lambda _v: self._aclr_to_manual())
        b.row("Spacing", self.e_aclr_sp)
        self.s_aclr_n = QtWidgets.QSpinBox()
        self.s_aclr_n.setRange(1, ACLR_OFFSETS_MAX)
        self.s_aclr_n.setValue(2)
        self.s_aclr_n.valueChanged.connect(lambda _v: self._aclr_edited())
        b.row("Offsets", self.s_aclr_n)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        for label, fn in (("CF = center", self._aclr_cf_center),
                          ("Fit span", self._aclr_fit)):
            btn = QtWidgets.QPushButton(label)
            btn.setFixedHeight(23)
            btn.clicked.connect(fn)
            row.addWidget(btn)
        w = QtWidgets.QWidget(); w.setLayout(row); b.full(w)
        self.l_aclr = QtWidgets.QLabel("--")
        self.l_aclr.setStyleSheet("color:#7CFF6B;font-weight:bold;")
        b.row("Carrier", self.l_aclr)
        self.tbl_aclr = QtWidgets.QTableWidget(0, 4)
        self.tbl_aclr.setHorizontalHeaderLabels(
            ["Offset", "Spacing", "Lower dBc", "Upper dBc"])
        self.tbl_aclr.verticalHeader().setVisible(False)
        self.tbl_aclr.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_aclr.setSelectionMode(
            QtWidgets.QAbstractItemView.NoSelection)
        # Fixed: three offset rows plus the header, so the panel is sized
        # once and never grows past _place_panel's ceiling.
        self.tbl_aclr.setFixedHeight(28 + 26 * ACLR_OFFSETS_MAX)
        _hh = self.tbl_aclr.horizontalHeader()
        for _c in range(4):
            _hh.setSectionResizeMode(_c, QtWidgets.QHeaderView.Stretch)
        b.full(self.tbl_aclr)
        return b

    def _aclr_on(self) -> bool:
        c = getattr(self, "c_aclr", None)
        return bool(c is not None and c.isChecked())

    def _aclr_reset(self) -> None:
        self._meas_aclr = {}

    def _aclr_toggled(self, on: bool) -> None:
        # Seeded from the sweep centre the first time, like a channel's CF,
        # so ticking it measures what is on screen; after that it is kept.
        if on and not self._aclr_seeded:
            self.e_aclr_cf.setValue(self.cfg.center)
            self._aclr_seeded = True
        self._aclr_reset()
        self._refresh_meas()

    def _aclr_preset(self, i: int) -> None:
        if 0 <= i < len(ACLR_PRESETS):
            _lab, chan, integ = ACLR_PRESETS[i]
            # setValue does not emit `changed`, so this cannot bounce the
            # standard straight back to Manual.
            self.e_aclr_bw.setValue(integ)
            self.e_aclr_sp.setValue(chan)
        self._aclr_edited()

    def _aclr_to_manual(self) -> None:
        self.cb_aclr.blockSignals(True)
        self.cb_aclr.setCurrentText(ACLR_MANUAL)
        self.cb_aclr.blockSignals(False)
        self._aclr_edited()

    def _aclr_edited(self) -> None:
        self._aclr_seeded = True
        self._aclr_reset()
        self._refresh_meas()

    def _aclr_cf_center(self) -> None:
        self.e_aclr_cf.setValue(self.cfg.center)
        self._aclr_edited()

    def _aclr_extent(self):
        cf = float(self.e_aclr_cf.value())
        reach = (int(self.s_aclr_n.value()) * float(self.e_aclr_sp.value())
                 + float(self.e_aclr_bw.value()) / 2)
        return cf - reach, cf + reach

    def _aclr_fit(self) -> None:
        """Centre on the carrier with every offset band on screen, and a
        tenth to spare -- the span an analyzer's ACLR mode sets up."""
        lo, hi = self._aclr_extent()
        self._retune((lo + hi) / 2, (hi - lo) * 1.1)
        self._aclr_reset()
        self._message(f"span set to {fmt_hz_short((hi - lo) * 1.1)} for ACLR")

    def _aclr_blank(self) -> None:
        if not hasattr(self, "l_aclr"):
            return
        self.l_aclr.setText("--")
        self.tbl_aclr.setRowCount(0)
        for reg in self.aclr_regions:
            reg.setVisible(False)

    def _aclr_refresh(self, f, pw, fresh: bool) -> None:
        n = int(self.s_aclr_n.value())
        bands = aclr_bands(float(self.e_aclr_cf.value()),
                           float(self.e_aclr_bw.value()),
                           float(self.e_aclr_sp.value()), n)
        got = {}
        for idx, reg in enumerate(self.aclr_regions):
            if idx >= len(bands):
                reg.setVisible(False)
                continue
            k, lo, hi = bands[idx]
            reg.setRegion([lo, hi])
            reg.setVisible(True)
            mw, partial = band_power_mw(f, pw, self.last.rbw, lo, hi)
            if mw is None:
                got[k] = None
                continue
            if fresh:
                acc, cnt = self._meas_aclr.get(k, (None, 0))
                self._meas_aclr[k] = self._meas_fold(acc, cnt, mw)
            acc, cnt = self._meas_aclr.get(k, (mw, 1))
            got[k] = (acc if acc is not None else mw, partial, cnt)
        main = got.get(0)
        if main is None:
            self.l_aclr.setText("outside span")
            self.l_aclr.setStyleSheet("color:#ffb340;font-weight:bold;")
        else:
            self.l_aclr.setText(
                f"{10 * np.log10(max(main[0], 1e-30)):.2f} dBm"
                + self._meas_suffix(main[2])
                + ("  PARTIAL" if main[1] else ""))
            self.l_aclr.setStyleSheet(
                "color:#ffb340;font-weight:bold;" if main[1]
                else "color:#7CFF6B;font-weight:bold;")
        sp = float(self.e_aclr_sp.value())
        self.tbl_aclr.setRowCount(n)
        for r in range(n):
            k = r + 1
            cells = [ACLR_OFFSET_NAMES[r], fmt_hz_short(k * sp)]
            amber = []
            for side in (-k, k):
                v = got.get(side)
                if v is None or main is None:
                    cells.append("--")
                    amber.append(True)
                else:
                    ratio = max(v[0], 1e-30) / max(main[0], 1e-30)
                    cells.append(f"{10 * np.log10(ratio):.2f}")
                    amber.append(bool(v[1] or main[1]))
            for c, txt in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(txt)
                if c:
                    it.setTextAlignment(QtCore.Qt.AlignRight
                                        | QtCore.Qt.AlignVCenter)
                # A band running off the span reads low, so its dBc is drawn
                # amber rather than passed off as a measurement.
                if c >= 2 and amber[c - 2]:
                    it.setForeground(QtGui.QColor("#ffb340"))
                self.tbl_aclr.setItem(r, c, it)

    def _box_counter(self):
        """
        Frequency counter with a ppm readout.

        Measured window behaviour for the sub-bin fit (worst case over tone
        position, 10 kHz RBW): Blackman-Harris and Nuttall 0.3% of a bin,
        Hann 1.6%, Flat Top 13.9%, Rectangular 16.6%. Flat Top is flat on top
        by design -- excellent for amplitude, poor for a parabola -- so the
        panel says so when it matters.
        """
        b = Box("Frequency Counter", A_MEASURE)
        self.c_count = QtWidgets.QCheckBox("Read the carrier precisely")
        self.c_count.toggled.connect(lambda _: self._refresh_counter())
        b.full(self.c_count)
        self.l_count = QtWidgets.QLabel("--")
        self.l_count.setStyleSheet("color:#7CFF6B;font-weight:bold;")
        self.l_count.setWordWrap(True)
        b.row("Measured", self.l_count)

        self.e_nominal = HzEdit(38.4e6)
        self.e_nominal.setToolTip("What it is supposed to be. The error below\n"
                                  "is then in Hz and ppm -- handy for checking\n"
                                  "a reference oscillator or trimming an XO.")
        self.e_nominal.changed.connect(lambda _: self._refresh_counter())
        b.row("Expected", self.e_nominal)
        bn = QtWidgets.QPushButton("Expected = measured")
        bn.setToolTip("Zero the error here, then watch it drift with\n"
                      "temperature or supply.")
        bn.clicked.connect(self._nominal_from_measured)
        b.full(bn)
        self.l_ppm = QtWidgets.QLabel("--")
        self.l_ppm.setStyleSheet("color:#4ac6ff;font-weight:bold;")
        b.row("Error", self.l_ppm)

        self.l_cwin = QtWidgets.QLabel(
            "For the counter, set RBW shape to Blackman-Harris or Nuttall:\n"
            "0.3% of a bin, against 14% for Flat Top.")
        self.l_cwin.setWordWrap(True)
        self.l_cwin.setStyleSheet("color:#ffd24a;")
        self.l_cwin.setVisible(False)
        b.full(self.l_cwin)
        return b

    def _nominal_from_measured(self) -> None:
        if self.last is not None and np.isfinite(self.last.peak_hz):
            self.e_nominal.setValue(float(self.last.peak_hz))
            self._refresh_counter()

    def _refresh_counter(self) -> None:
        on = self.c_count.isChecked()
        self.l_cwin.setVisible(bool(on and self.cfg.window
                                    in ("Flat Top", "Rectangular")
                                    and not self.cfg.zero_span))
        if not on or self.last is None or self.cfg.zero_span \
                or not np.isfinite(self.last.peak_hz):
            self.l_count.setText("--")
            self.l_ppm.setText("--")
            return
        hz = float(self.last.peak_hz)
        self.l_count.setText(f"{hz:,.0f} Hz\n{hz/1e6:.6f} MHz  "
                             f"@ {self.last.peak_dbm:.2f} dBm")
        nom = self.e_nominal.value()
        if nom > 0:
            err = hz - nom
            self.l_ppm.setText(f"{err:+,.0f} Hz   ({err/nom*1e6:+.3f} ppm)")
        else:
            self.l_ppm.setText("--")

    # -- harmonics -------------------------------------------------------
    def _fundamental_hz(self) -> float:
        """
        M1's frequency, refined where possible.

        The marker sits on a *display* point, and at a wide span one point can
        be a megahertz or more. Multiplying that rounding error by the harmonic
        order would push the 4f target several points off the real line, so
        prefer the interpolated peak of the raw spectrum whenever it belongs to
        the same carrier M1 is parked on.
        """
        m1 = self.markers[0]
        hz = float(m1.f)
        if self.last is None:
            return hz
        pk = float(self.last.peak_hz)
        f = self.last.freqs
        if np.isfinite(pk) and f.size > 1:
            step = float(f[-1] - f[0]) / max(f.size - 1, 1)
            if abs(pk - hz) <= 2.0 * step:
                return pk
        return hz

    def _snap_local_max(self, target: float, guard: int = 1) -> int:
        """
        Nearest display point to target, nudged onto the local peak.

        The window is deliberately one point wide: with the fundamental taken
        from the interpolated peak, the only error left is display-point
        rounding. A wider window would let a marker walk off onto a noise spike
        when the harmonic is genuinely absent -- and "absent" is a result worth
        seeing, not one worth hiding.
        """
        f = self.last.freqs
        i = int(np.argmin(np.abs(f - target)))
        d = self._displayed()
        if d is None or d.size != f.size:
            return i
        lo = max(i - guard, 0)
        hi = min(i + guard + 1, d.size)
        return lo + int(np.argmax(d[lo:hi]))

    def _harmonics(self) -> None:
        """
        Put every spare marker on a harmonic of M1: M2 on 2f, M3 on 3f, and
        so on as far as the markers go.

        With four markers that was 2f/3f/4f. With ten it reaches 10f, which
        is the point of asking for ten on a bench looking at harmonic
        suppression. Ones that fall outside the span are switched OFF and
        named in the message rather than being parked at an edge -- an
        absent harmonic reading the floor is an answer, a marker on the last
        display point pretending to be 8f is not.
        """
        m1 = self.markers[0]
        if not m1.on or self.last is None or self.cfg.zero_span:
            self._message("put M1 on a carrier first (Peak search)")
            return
        f = self.last.freqs
        fund = self._fundamental_hz()
        placed, missed = [], []
        for k in range(2, N_MARKERS + 1):
            idx = k - 1
            target = fund * k
            if target < f[0] or target > f[-1]:
                self.markers[idx].on = False
                missed.append(k)
                continue
            self.markers[idx].f = float(f[self._snap_local_max(target)])
            self.markers[idx].on = True
            self.markers[idx].delta = False
            placed.append(k)
        self._sync_marker()
        self._draw_markers()
        # Runs are collapsed: with ten markers a 100 MHz span typically
        # misses 4f through 10f, and spelling all seven out fills the
        # status line with a list nobody reads.
        msg = ("markers on " + _harm_list(placed) if placed
               else "no harmonic falls inside this span")
        if missed:
            msg += f"  (outside span: {_harm_list(missed)})"
        self._message(msg)

    def _box_capture(self):
        b = Box("Capture", A_FILE)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.b_shot = QtWidgets.QPushButton("Screenshot")
        self.b_shot.setToolTip("Save the window exactly as it looks now.\n"
                               "No dialog -- it is written straight to the\n"
                               "capture folder with a timestamped name.\n"
                               "Shortcut: F12")
        self.b_shot.clicked.connect(self._shot_now)
        self.b_both = QtWidgets.QPushButton("Shot + CSV")
        self.b_both.setToolTip("Screenshot plus the trace data behind it,\n"
                               "same timestamp -- a self-contained record.")
        self.b_both.clicked.connect(self._shot_and_csv)
        row.addWidget(self.b_shot)
        row.addWidget(self.b_both)
        w = QtWidgets.QWidget(); w.setLayout(row); b.full(w)

        row2 = QtWidgets.QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        bo = QtWidgets.QPushButton("Open folder")
        bo.clicked.connect(self._open_shot_dir)
        bc = QtWidgets.QPushButton("Change...")
        bc.clicked.connect(self._choose_shot_dir)
        row2.addWidget(bo); row2.addWidget(bc)
        w2 = QtWidgets.QWidget(); w2.setLayout(row2); b.full(w2)

        self.l_shot = QtWidgets.QLabel(self.shot_dir)
        self.l_shot.setWordWrap(True)
        self.l_shot.setStyleSheet("color:#8fa6bb;")
        b.full(self.l_shot)

        b.full(rule())
        row3 = QtWidgets.QHBoxLayout()
        row3.setContentsMargins(0, 0, 0, 0)
        self.sp_iq = QtWidgets.QDoubleSpinBox()
        self.sp_iq.setRange(0.1, 2000.0)
        self.sp_iq.setDecimals(1)
        self.sp_iq.setValue(20.0)
        self.sp_iq.setSuffix(" ms")
        self.sp_iq.setToolTip("How long to record.")
        self.b_iq = QtWidgets.QPushButton("Record IQ")
        self.b_iq.setToolTip(
            "Save the raw samples, not a picture of them.\n"
            "Recorded at the current centre frequency and sample rate\n"
            "as a bare complex64 file -- the format GNU Radio,\n"
            "inspectrum and numpy all open directly. A .txt beside it\n"
            "records the sample rate, centre, gain and port.\n\n"
            "This is the one to reach for when a bug happens once an\n"
            "hour: catch it, then take the file apart offline instead of\n"
            "waiting for it again.")
        self.b_iq.clicked.connect(self._record_iq)
        row3.addWidget(self.sp_iq)
        row3.addWidget(self.b_iq, 1)
        w3 = QtWidgets.QWidget(); w3.setLayout(row3); b.full(w3)
        self.l_iq = QtWidgets.QLabel("")
        self.l_iq.setWordWrap(True)
        self.l_iq.setStyleSheet("color:#8fa6bb;")
        b.full(self.l_iq)

        # The recorder's settings. The recorder itself is a key, not a
        # button in here -- one press is the whole request -- but the two
        # things you might want to change before pressing it belong with
        # the other "where do saved things go" controls rather than on a
        # panel of their own.
        #
        # Deliberately NOT in the saved setup, for the same reason the
        # capture folder is not: a setup is the instrument's state, and how
        # often a file gets a row is not that. It also means there is no
        # field here to go stale in a setup saved before the recorder
        # existed, which is the trap bug 33 and 5.15 keep finding.
        b.full(rule())
        self.cb_rec_int = QtWidgets.QComboBox()
        for label, _secs in REC_INTERVALS:
            self.cb_rec_int.addItem(label)
        self.cb_rec_int.setToolTip(
            "How often a sweep is kept while recording.\n\n"
            "One a second is plenty for a site survey and keeps a\n"
            "15-minute recording to a few MB. 'Every sweep' catches\n"
            "something brief, at perhaps twenty times the size.\n\n"
            "Changing this does not disturb a recording already\n"
            "running -- it applies to the next one.")
        b.row("Record every", self.cb_rec_int)
        br = QtWidgets.QPushButton("Open recordings")
        br.setToolTip("Open the last recording's folder, or the folder\n"
                      "they all go in if none has been made yet.")
        br.clicked.connect(self._open_rec_dir)
        b.full(br)
        self.l_rec_note = QtWidgets.QLabel(
            "Recording is the Start Recording key above. Each one makes a "
            "folder holding a video of this window, a max hold of the whole "
            "session as a spreadsheet, and the sweeps themselves.")
        self.l_rec_note.setWordWrap(True)
        self.l_rec_note.setStyleSheet("color:#8fa6bb;")
        b.full(self.l_rec_note)
        return b

    # -- raw IQ ----------------------------------------------------------
    def _record_iq(self) -> None:
        if getattr(self, "worker", None) is None:
            self._message("no radio running")
            return
        ms = float(self.sp_iq.value())
        path = self._shot_path(".cfile")
        self.b_iq.setEnabled(False)
        self.b_iq.setText("recording...")
        self.l_iq.setText(f"recording {ms:.1f} ms at "
                          f"{self.cfg.center/1e6:.3f} MHz ...")
        self.worker.request_iq(ms, path)

    def _iq_done(self, path: str, detail: str) -> None:
        self.b_iq.setEnabled(True)
        self.b_iq.setText("Record IQ")
        if not path:
            self.l_iq.setText(f"IQ capture failed: {detail}")
            self._message(f"IQ capture failed: {detail}")
            return
        self.l_iq.setText(f"{os.path.basename(path)}\n{detail}")
        self._message(f"saved {os.path.basename(path)}  ({detail})")

    # -- recording the spectrum ------------------------------------------
    #
    # Asked for 2026-09-20 so presale engineers on a customer site can
    # leave the instrument running, then hand somebody a folder. The
    # writing itself is SweepRecorder, which has no Qt in it; what lives
    # here is the key, the lamp, the status line and the files written at
    # the end.

    def _rec_dir(self) -> str:
        return os.path.join(self.shot_dir, REC_DIRNAME)

    def _rec_interval(self) -> float:
        cb = getattr(self, "cb_rec_int", None)
        if cb is None:
            return REC_DEFAULT_INTERVAL
        return float(REC_INTERVALS[max(0, cb.currentIndex())][1])

    def _rec_key(self) -> None:
        """One key, both directions -- see KEY_SPAN for why it says so."""
        if getattr(self, "rec", None) is None:
            self._rec_start()
        else:
            self._rec_stop()

    def _rec_start(self, folder: Optional[str] = None) -> bool:
        """Start recording, into `folder` or a new dated one under the
        recordings folder. True when a recording is now running. The
        sequencer passes its own run folder (2026-11-22), so a run's
        captures and its recording end up side by side."""
        if getattr(self, "rec", None) is not None:
            return False
        if self.last is None:
            # Nothing has swept yet, so there is no axis to record against
            # and no idea whether the radio works. Saying so beats making
            # an empty folder that looks like a recording.
            self._message("nothing to record yet -- wait for a sweep")
            return False
        if folder is None:
            stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
            folder = os.path.join(self._rec_dir(), f"{CSV_STEM} {stamp}")
        try:
            self.rec = SweepRecorder(folder, self._rec_meta(),
                                     self._rec_interval())
        except Exception as exc:
            self.rec = None
            self._message(f"could not start recording: {exc}")
            return False
        self._rec_warned = False
        self._rec_vid_start(folder)
        # The status line runs on a clock of its own. It used to refresh
        # only from _rec_tick, i.e. when a sweep ARRIVED -- so "PAUSED: the
        # sweep is held" and "no sweep has arrived", the two warnings that
        # exist for the moments sweeps stop arriving, could never appear.
        # Bug 43's shape exactly, shipped 2026-09-20 and found 2026-09-23;
        # the test had called _rec_status() by hand, which is precisely the
        # call the application never made.
        if getattr(self, "_rec_stimer", None) is None:
            self._rec_stimer = QtCore.QTimer(self)
            self._rec_stimer.timeout.connect(lambda: self._rec_status())
        self._rec_stimer.start(1000)
        self._sync_act_keys()
        self._rec_status()
        self._message(f"recording to {folder}"
                      + ("" if self._rec_vid is not None
                         else f"  -- NO VIDEO: {self._rec_vid_err}"))
        return True

    # -- the screen video ------------------------------------------------
    #
    # What goes in it is the WINDOW, never the whole desktop: on a customer
    # site a desktop recording would also carry whatever mail or chat is
    # open, and that is not ours to capture. And it is the window as it
    # stands, open panel included. Screenshots hide the panel (5.33) by
    # hiding it for the instant of the grab, but doing that ten times a
    # second would take the keyboard focus out of whatever field somebody
    # is typing into in that panel, every frame -- and a video of the
    # instrument being set up is more use with the panel in it anyway.

    def _rec_vid_start(self, folder: str) -> None:
        self._rec_vid = None
        self._rec_vid_err = ""
        try:
            im = self.grab().toImage()          # physical pixels, any DPI
            cw, ch = im.width(), im.height()
            mw, mh = REC_VIDEO_MAX
            k = min(1.0, mw / max(cw, 1), mh / max(ch, 1))
            cw, ch = max(16, int(cw * k)), max(16, int(ch * k))
            # Rounded UP to a multiple of 16 and padded with black, never
            # rounded down: that would crop up to 15 px off the bottom of the
            # window, which is where the REC status line lives. See
            # Mp4Writer for what happens at any other size.
            vw, vh = (cw + 15) & ~15, (ch + 15) & ~15
            bps = max(REC_VIDEO_MIN_BPS,
                      int(REC_VIDEO_BPP * vw * vh * REC_VIDEO_FPS))
            canvas = QtGui.QImage(vw, vh, QtGui.QImage.Format_RGB32)
            canvas.fill(0)
            self._rec_vid = Mp4Writer(os.path.join(folder, REC_VIDEO_NAME),
                                      vw, vh, REC_VIDEO_FPS, bps,
                                      quality=REC_VIDEO_QUALITY)
            self._rec_canvas = canvas
            self._rec_content = (cw, ch)
            self._rec_vt0 = time.time()
        except Exception as exc:
            self._rec_vid = None
            self._rec_vid_err = str(exc)
            return
        if getattr(self, "_rec_vtimer", None) is None:
            self._rec_vtimer = QtCore.QTimer(self)
            self._rec_vtimer.timeout.connect(self._rec_frame)
        self._rec_vtimer.start(int(round(1000 / REC_VIDEO_FPS)))
        self._rec_frame()

    def _rec_frame(self) -> None:
        """One frame of the window into the video. Timer-driven, not
        sweep-driven, so the video runs in real time even while a full-span
        sweep takes seconds -- or while the sweep is held, which is exactly
        when somebody is pointing at the screen."""
        vid = getattr(self, "_rec_vid", None)
        if vid is None:
            return
        try:
            im = self.grab().toImage()
            cw, ch = self._rec_content
            same = im.width() == cw and im.height() == ch
            if not same:
                # Resized since the start, or bigger than REC_VIDEO_MAX:
                # fit, keep the aspect, black round the rest. fill() must
                # happen BEFORE the painter opens on the same image.
                self._rec_canvas.fill(0)
            p = QtGui.QPainter(self._rec_canvas)
            try:
                if same:
                    p.drawImage(0, 0, im)
                else:
                    s = min(cw / max(im.width(), 1), ch / max(im.height(), 1))
                    p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
                    p.drawImage(QtCore.QRect(0, 0, int(im.width() * s),
                                             int(im.height() * s)), im)
            finally:
                p.end()
            vid.write(int(self._rec_canvas.constBits()),
                      time.time() - self._rec_vt0)
        except Exception as exc:
            # The sweeps go on being recorded; only the video stops, and it
            # says so on the permanent line rather than in a message that
            # fades.
            self._rec_vid_err = f"video stopped ({exc})"
            self._rec_vid_stop()

    def _rec_vid_stop(self) -> bool:
        """Stop and finalize. True when a playable file was left behind."""
        t = getattr(self, "_rec_vtimer", None)
        if t is not None:
            t.stop()
        vid, self._rec_vid = getattr(self, "_rec_vid", None), None
        if vid is None:
            return False
        try:
            vid.close()
            return vid.frames > 0
        except Exception as exc:
            self._rec_vid_err = f"video not finished ({exc})"
            return False

    def _rec_meta(self) -> dict:
        """What the recording says about itself, before any row exists.

        The build id is in here for the same reason it heads the CSV block:
        a file argued about a month later has to name the build that wrote
        it. The rest is the state the first row was taken in -- the axis is
        stored per row, because it is allowed to move.
        """
        return {
            "app": f"{APP_NAME} {version_line()}",
            "source": self.src.info,
            "center_hz": float(self.cfg.center),
            "span_hz": float(self.cfg.span),
            "rbw_hz": float(self.last.rbw) if self.last is not None else 0.0,
            "gain_db": float(self.cfg.gain),
            "cal_offset_db": float(self.cfg.cal_offset),
            "board_table_db": (float(rx_table_db(self.cfg.center))
                               if getattr(self.src, "real_hardware", False)
                               else 0.0),
            "detector": str(self.cfg.detector),
            "window": str(self.cfg.window),
            "rx_port": int(getattr(self, "rx_ch", 0)),
        }

    def _rec_tick(self, r: Result) -> None:
        """Offer one sweep to the recorder. Called for EVERY sweep.

        Deliberately ahead of the freeze check in _on_result: freezing holds
        the DISPLAY, and this is recording the instrument. Somebody who
        freezes the picture to point at something mid-survey must not
        silently lose those minutes of recording.
        """
        rec = getattr(self, "rec", None)
        if rec is None:
            return
        now = time.time()
        if rec.due(now):
            flags = (0x01 if r.fs_low else 0) | (0x02 if r.cal_extrap else 0) \
                | (0x04 if r.overload else 0)
            try:
                rec.add(r.freqs, r.power, r.power_mean, flags, now)
            except Exception as exc:
                # A disk that filled or a folder that went away mid-run.
                # Stop cleanly and keep what is already written rather than
                # raising up through the frame handler, which would take
                # the display down as well as the recording.
                self._message(f"recording stopped: {exc}")
                self._rec_stop(quiet=True)
                return
            if rec.stopped_why and not self._rec_warned:
                self._rec_warned = True
                self._message(f"recording stopped -- {rec.stopped_why}")
                self._rec_stop(quiet=True)
                return
        self._rec_status(now)

    def _rec_status(self, now: Optional[float] = None) -> None:
        """The permanent line: elapsed, sweeps, size -- or why not.

        The "or why not" is the half that matters and is bug 43's lesson:
        a recorder that has stopped receiving sweeps looks exactly like one
        that is working, and the person it was left running for is by
        definition not watching it.
        """
        rec = getattr(self, "rec", None)
        if rec is None:
            self.l_rec.setText("")
            self.l_rec.setToolTip("")
            return
        now = time.time() if now is None else now
        el = int(rec.elapsed(now))
        txt = (f"REC {el // 60:d}:{el % 60:02d}   {rec.rows:,} sweeps   "
               f"{rec.bytes / 1e6:,.1f} MB")
        # Only two reasons are given, and the second one is deliberately
        # about what is OBSERVED rather than about a cause. A held sweep is
        # worth naming because it is one keypress away and the user can undo
        # it; everything else that stops sweeps -- an unplugged board, a
        # stream being restarted, a Video trigger that has not fired -- is
        # reported as the fact that no sweep arrived, which is true in all
        # of them and cannot be wrong about which one it is.
        why = ""
        if not self.cfg.continuous:
            why = "the sweep is held (press Single)"
        elif rec.t_last is not None and (now - rec.t_last) > max(
                REC_GAP_MIN_S, rec.interval * REC_GAP_FACTOR):
            why = "no sweep has arrived"
        if why:
            txt += f"  -- PAUSED: {why}"
        # The video's state is on the same permanent line. A recording that
        # is quietly not making the video somebody expects is the same
        # fault as one that has quietly stopped sweeping.
        vid = getattr(self, "_rec_vid", None)
        verr = getattr(self, "_rec_vid_err", "")
        if vid is not None:
            txt += "   + video"
        elif verr:
            txt += f"   NO VIDEO: {verr}"
        self.l_rec.setText(txt)
        self.l_rec.setStyleSheet(
            "color:#ffb340;font-weight:bold;" if (why or verr)
            else f"color:{A_MEASURE};font-weight:bold;")
        self.l_rec.setToolTip(f"Recording to {rec.folder}")

    def _rec_stop(self, quiet: bool = False) -> None:
        """End the recording: finish the video, write the max hold.

        No spectrogram.png and no recording.txt since 2026-09-23, at Kfir's
        request -- the video shows what the picture did, and everything the
        sidecar said is in spectrum.bin's own header line and in the max
        hold's provenance block. The .bin's layout is documented in
        READ_ME.txt instead, which is where somebody reading one will look.
        """
        rec = getattr(self, "rec", None)
        if rec is None:
            return
        # Video first, so no frame is taken of a window already saying
        # "saved"; and finalizing it is what makes the file playable at all.
        vid_ok = self._rec_vid_stop()
        if getattr(self, "_rec_stimer", None) is not None:
            self._rec_stimer.stop()
        self.rec = None
        rec.close()
        self._sync_act_keys()
        self._rec_status()
        made = ["spectrum.bin"]
        freqs, mx, used = rec.summary()
        if freqs is not None and mx is not None:
            if self._rec_write_table(rec, freqs, mx, used):
                made += ["max hold.csv", "max hold.xlsx"]
        if vid_ok:
            made.append(REC_VIDEO_NAME)
        elif getattr(self, "_rec_vid_err", ""):
            made.append(f"NO VIDEO: {self._rec_vid_err}")
        el = int(rec.elapsed())
        if not quiet:
            self._message(
                f"recording saved -- {rec.rows:,} sweeps over "
                f"{el // 60:d}:{el % 60:02d} in {rec.folder} "
                f"({', '.join(made)})", seconds=12.0)
            # Kfir, 2026-09-23: open the folder on stop. The person on site
            # wants the files next, and a path in a status line that fades
            # in 12 s is not a way to find them. Not when quiet (window
            # closing) -- an Explorer window appearing as the app exits
            # reads as something having gone wrong.
            try:
                reveal_folder(rec.folder)
            except Exception as exc:
                self._message(f"could not open {rec.folder}: {exc}")
        self._rec_last = rec.folder

    def _rec_write_table(self, rec: "SweepRecorder", freqs, mx,
                         used: int) -> bool:
        """The session's max hold, as the csv and the workbook beside it.

        A max hold rather than the last sweep, because the question a
        recording is left running to answer is "what appeared while I was
        not looking", and only the max hold carries that.

        It does NOT go through _csv_table(): that gatherer is about the
        live traces on screen and this is one derived column over a whole
        session. What IS shared is _csv_meta() and write_xlsx(), so the two
        files still agree about provenance and still look the same.
        """
        try:
            head = ["Frequency[Hz]", "Max Hold[dBm]"]
            data = [[f"{freqs[k]:.6f}", f"{mx[k]:.3f}"]
                    for k in range(freqs.size)]
            meta = self._csv_meta() + [
                ("Recording", os.path.basename(rec.folder)),
                ("Sweeps Recorded", f"{rec.rows:d}"),
                ("Sweeps In Max Hold", f"{used:d}"),
                ("Duration[s]", f"{rec.elapsed():.0f}"),
                ("Gaps", f"{rec.gaps:d}")]
            mcol = max(CSV_META_COL, len(head) + 1)
            import csv as _csv
            rows = [list(head)] + [list(r) for r in data]
            while len(rows) < len(meta):
                rows.append([])
            for k, (label, value) in enumerate(meta):
                row = rows[k]
                row.extend([""] * (mcol - len(row)))
                row.extend([label, value])
            with open(os.path.join(rec.folder, "max hold.csv"), "w",
                      newline="") as fh:
                w = _csv.writer(fh)
                for row in rows:
                    w.writerow(row)
            write_xlsx(os.path.join(rec.folder, "max hold.xlsx"),
                       head, data, meta, mcol)
            return True
        except Exception as exc:
            self._message(f"max hold not written: {exc}")
            return False

    def _open_rec_dir(self) -> None:
        d = getattr(self, "_rec_last", "") or self._rec_dir()
        try:
            os.makedirs(d, exist_ok=True)
            reveal_folder(d)
        except Exception as exc:
            self._message(f"could not open {d}: {exc}")

    def _shot_path(self, ext: str) -> str:
        os.makedirs(self.shot_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Two captures inside the same second would otherwise overwrite.
        path = os.path.join(self.shot_dir, f"{APP_NAME} {stamp}{ext}")
        n = 2
        while os.path.exists(path):
            path = os.path.join(self.shot_dir,
                                f"{APP_NAME} {stamp} ({n}){ext}")
            n += 1
        return path

    def _grab(self) -> "QtGui.QPixmap":
        """The window as a picture, WITHOUT the open control panel.

        Reported 2026-09-14 with a screenshot of the fault: the Capture
        panel was sitting over the trace in the saved PNG, because the only
        way to press Screenshot is to have that panel open, so every shot
        taken from the button carried it. F12 and Shot + CSV do the same
        thing from anywhere.

        The panel is a child widget, and `QWidget.grab()` renders the widget
        TREE rather than reading the screen, so it draws whatever children
        are visible at that instant -- which is why the popover has to be
        hidden rather than merely moved or lowered. Hiding renders
        synchronously inside this call, so no event loop round trip is
        needed and nothing flickers on screen.

        It is hidden and put straight back rather than closed, deliberately.
        Closing would re-park every box (see _close_panel) and leave the
        panel shut, so the button just pressed would vanish under the user's
        pointer and a second shot would need the panel re-opened. What is
        wanted is a picture without the panel, not a panel that goes away.

        The shaded channel bands, the markers and the readouts all stay --
        they are measurements, and a screenshot of a measurement should
        carry them.
        """
        pop = getattr(self, "popover", None)
        showing = pop is not None and pop.isVisible()
        if showing:
            pop.hide()
        try:
            return self.grab()
        finally:
            if showing:
                pop.show()
                pop.raise_()

    def _shot_now(self) -> str:
        try:
            path = self._shot_path(".png")
            self._grab().save(path)
            self._message(f"saved {os.path.basename(path)}  ->  {self.shot_dir}")
            return path
        except Exception as exc:
            self._message(f"screenshot failed: {exc}")
            return ""

    def _shot_and_csv(self) -> None:
        png = self._shot_now()
        if not png:
            return
        csv_path = png[:-4] + ".csv"
        if self._save_trace(csv_path):
            both = " + .csv + .xlsx" if self._last_xlsx else " + .csv"
            self._message(f"saved {os.path.basename(png)}{both}  ->  "
                                f"{self.shot_dir}")

    def _open_shot_dir(self) -> None:
        os.makedirs(self.shot_dir, exist_ok=True)
        QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(self.shot_dir))

    def _choose_shot_dir(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Where should captures go?", self.shot_dir)
        if d:
            self.shot_dir = d
            self.l_shot.setText(d)
            self._message(f"captures now go to {d}")

    def _preset_file(self) -> str:
        """
        Where saved setups live.

        The program has been renamed twice -- "Spectrum Analyzer" until
        2026-08-19 (settings under bladeRF-SpectrumAnalyzer), "RF Blade
        Workbench" until 2026-09-02 (under RF-Blade-Workbench) -- and a
        rename must not quietly throw away setups somebody built up. So the
        newest older folder that has anything in it is carried across the
        first time this one is used, NEWEST FIRST: a machine that has both
        must not be handed the older set.

        session.json comes with it, since "Save settings for next launch"
        is settings too and leaving it behind would silently restore an
        older state. Nothing is deleted -- if a migration turns out to have
        gone wrong, the originals are still where they were.
        """
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "bladeRF-Workbench")
        os.makedirs(d, exist_ok=True)
        new = os.path.join(d, "presets.json")
        if not os.path.exists(new):
            for older in ("RF-Blade-Workbench", "bladeRF-SpectrumAnalyzer"):
                src = os.path.join(base, older)
                if not os.path.isfile(os.path.join(src, "presets.json")):
                    continue
                try:
                    import shutil
                    for name in ("presets.json", "session.json"):
                        f = os.path.join(src, name)
                        if os.path.isfile(f):
                            shutil.copy2(f, os.path.join(d, name))
                except Exception:
                    pass
                break
        return new

    def _default_setup(self) -> dict:
        """
        The shipped state, in the shape _apply_setup() consumes.

        Everything that has a Config default is taken FROM Config rather than
        repeated here, so changing a default in one place changes it here too.
        """
        c = Config()
        return {
            "center": c.center, "span": c.span, "rbw": c.rbw,
            "rbw_auto": c.rbw_auto, "window": c.window,
            "detector": c.detector, "points": c.points, "gain": c.gain,
            "cal_offset": c.cal_offset,
            "max_fs": c.max_fs,
            "zero_span": c.zero_span, "zero_time": c.zero_time,
            "trace_modes": list(c.trace_modes),
            "trace_view": list(c.trace_view), "avg_count": c.avg_count,
            "fps": c.fps, "continuous": c.continuous,
            "ref_level": -20.0, "db_div": 10.0, "rx": 0,
            "limit_on": False, "limit": -60.0,
            "chp_on": False, "chp_bw": 2e6,
            "chp_cf": c.center,
            # The ten channels since 2026-09-14. chp_cf / chp_bw above are
            # kept alongside it and describe channel 1, so a setup written
            # today still says something to the single-channel reader this
            # file had until yesterday.
            "chp_list": [{"on": i == 0, "cf": c.center, "bw": 2e6}
                         for i in range(N_CHP)],
            "obw_on": False, "counter_on": False,
            "nominal": 38.4e6,
            "aclr_on": False, "aclr_std": ACLR_PRESETS[0][0],
            "aclr_cf": c.center, "aclr_bw": ACLR_PRESETS[0][2],
            "aclr_spacing": ACLR_PRESETS[0][1], "aclr_offsets": 2,
            "meas_avg": "Average", "meas_avg_n": 20,
            # The generator too, and for the same reason the RX cal offset is
            # here: a Preset that unkeys the transmitter but leaves a TX cal
            # offset, a carrier offset and a level behind is not a reset, and
            # every one of those silently falsifies what comes out next time.
            # _sg_preset_apply() forces on=False, so this cannot key anything.
            # sg_open stays absent, so the column closes with it.
            "sg": asdict(SgConfig()),
        }

    def _preset_reset(self, confirm: bool = True) -> None:
        """
        Preset: the whole instrument back to its default state.

        What a bench analyzer's Preset key does, and for the same reason --
        after an hour of poking at markers, holds, limits and a reference
        you want one action that puts everything back rather than twenty.

        Deliberately included: keying the transmitter OFF. A Preset that
        left a board radiating would be the one dangerous thing it could do.
        Also included: the Cal offset, because it comes from Config and
        because a stale offset silently falsifies every level on screen.
        Excluded: saved setups and the capture folder.

        The dialog lists what goes, so none of it is a surprise. It also
        takes confirm=False, which is how the tests drive it.
        """
        if confirm and QtWidgets.QMessageBox.question(
                self, "Preset",
                "Reset the whole analyzer to its default state?\n\n"
                "Traces, markers, the frozen reference, the limit line, the\n"
                "trigger and the Cal offset are all cleared, and the\n"
                "transmitter is keyed off.\n\n"
                "The generator goes back to its defaults as well: waveform,\n"
                "level, carrier offset, TX cal offset, burst.\n\n"
                "Saved setups are not touched.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        # Transmit first: whatever else goes wrong below, the board should
        # stop radiating.
        if self.b_sg_out.isChecked():
            self.b_sg_out.setChecked(False)
        self._sg_toggle_panel(False)
        self.b_run.setChecked(False)              # un-freeze the display
        self.c_classify.setChecked(False)
        self.c_wf.setChecked(False)      # default is off, and Preset means default
        self.cb_trig_mode.setCurrentIndex(0)
        self._clear_ref()
        self.c_ref_show.setChecked(True)
        self.c_ref_diff.setChecked(False)
        for m in self.markers:
            m.on = m.delta = False
        self._sync_marker()
        self._draw_markers()
        self._apply_setup(self._default_setup())
        self._clear()                             # traces and spectrogram
        self._message("preset -- everything back to defaults")

    def _box_presets(self):
        b = Box("Setups", A_FILE)
        # Save: keep the settings for next time. Distinct from the named
        # setups below, which are a list you pick from -- this one is "come
        # back the way I left it", with nothing to name and nothing to
        # remember to press.
        self.c_autosave = QtWidgets.QCheckBox("Save settings for next launch")
        self.c_autosave.setToolTip(
            "Remember the current settings and restore them automatically\n"
            "the next time the program starts: frequency, span, RBW,\n"
            "amplitude, traces, measurements, the port, and the whole\n"
            "generator setup.\n\n"
            "Untick it to go back to starting on the defaults -- that\n"
            "clears what was stored.\n\n"
            "Live results are not stored: traces, markers, the frozen\n"
            "reference and the measurement averages all start fresh.")
        self.c_autosave.toggled.connect(self._autosave_toggled)
        b.full(self.c_autosave)
        b.full(rule())
        self.cb_preset = QtWidgets.QComboBox()
        self.cb_preset.setEditable(True)
        self.cb_preset.setToolTip("Type a name and press Save to remember the\n"
                                  "whole setup -- span, RBW, traces, limits,\n"
                                  "port, the lot. Handy for tests you repeat.")
        b.row("Name", self.cb_preset)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        for label, fn in (("Save", self._preset_save),
                          ("Recall", self._preset_load),
                          ("Delete", self._preset_delete)):
            btn = QtWidgets.QPushButton(label)
            btn.setFixedHeight(23)
            btn.clicked.connect(fn)
            row.addWidget(btn)
        w = QtWidgets.QWidget(); w.setLayout(row); b.full(w)
        self._presets_reload()
        return b

    def _presets_reload(self) -> None:
        import json
        self._presets = {}
        try:
            with open(self._preset_file(), "r") as fh:
                self._presets = json.load(fh)
        except Exception:
            self._presets = {}
        cur = self.cb_preset.currentText()
        self.cb_preset.blockSignals(True)
        self.cb_preset.clear()
        self.cb_preset.addItems(sorted(self._presets))
        self.cb_preset.setEditText(cur)
        self.cb_preset.blockSignals(False)

    def _gather_setup(self) -> dict:
        # The three editors are a view of one channel; read them back into
        # the list before the list is written out, or the channel on screen
        # is saved as whatever it was before the last edit.
        self._chp_commit()
        """
        The whole instrument state, in the shape _apply_setup() consumes.

        Split out of _preset_save() so the Save checkbox can write the same
        dict to its own file. One gatherer, one applier: a second one written
        by hand would drift, and the failure mode of a drifted gatherer is a
        setting that silently does not come back -- which is bug 26 exactly.
        Transient runtime state is deliberately absent: no traces, no
        markers, no frozen reference, no measurement accumulators, no
        connection state. Those are what the instrument found, not what the
        user chose.
        """
        return {
            "center": self.cfg.center, "span": self.cfg.span,
            "rbw": self.cfg.rbw, "rbw_auto": self.cfg.rbw_auto,
            "window": self.cfg.window, "detector": self.cfg.detector,
            "points": self.cfg.points, "gain": self.cfg.gain,
            "cal_offset": self.cfg.cal_offset,
            "max_fs": self.cfg.max_fs,
            "zero_span": self.cfg.zero_span, "zero_time": self.cfg.zero_time,
            "trace_modes": list(self.cfg.trace_modes),
            "trace_view": [c.isChecked() for c in self.c_tr_view],
            "avg_count": self.cfg.avg_count, "fps": self.cfg.fps,
            "ref_level": self.s_ref.value(), "db_div": self.s_div.value(),
            "rx": self.rx_ch, "limit_on": self.c_limit.isChecked(),
            "limit": self.s_limit.value(),
            "chp_on": self.c_chp.isChecked(),
            # Channel 1 under the old names as well as the new list, so a
            # setup saved today is still readable by anything that only
            # knows about one channel.
            "chp_bw": self.chp_bw[0], "chp_cf": self.chp_cf[0],
            "chp_list": [{"on": bool(self.chp_on[i]),
                          "cf": float(self.chp_cf[i]),
                          "bw": float(self.chp_bw[i])}
                         for i in range(N_CHP)],
            "obw_on": self.c_obw.isChecked(),
            "counter_on": self.c_count.isChecked(),
            "aclr_on": self.c_aclr.isChecked(),
            "aclr_std": self.cb_aclr.currentText(),
            "aclr_cf": float(self.e_aclr_cf.value()),
            "aclr_bw": float(self.e_aclr_bw.value()),
            "aclr_spacing": float(self.e_aclr_sp.value()),
            "aclr_offsets": int(self.s_aclr_n.value()),
            "nominal": self.e_nominal.value(),
            "meas_avg": self._meas_mode(),
            "meas_avg_n": self.s_meas_avg.value(),
            "continuous": self.cfg.continuous,
            "show_wf": self.c_wf.isChecked(),
            "sweep_auto": self.cfg.sweep_auto,
            "sweep_time": self.cfg.sweep_time,
            "trig_mode": self.cfg.trig_mode,
            "trig_level": self.cfg.trig_level,
            "sg": asdict(self.sg),
            "sg_open": bool(self._sg_open),
        }

    def _preset_save(self) -> None:
        import json
        name = self.cb_preset.currentText().strip()
        if not name:
            self._message("give the setup a name first")
            return
        self._presets[name] = self._gather_setup()
        try:
            with open(self._preset_file(), "w") as fh:
                json.dump(self._presets, fh, indent=2)
        except Exception as exc:
            self._message(f"could not save: {exc}")
            return
        self._presets_reload()
        self.cb_preset.setEditText(name)
        self._message(f"saved setup '{name}'")

    # -- Save: carry the settings over to the next launch -------------------
    #
    # Same dict, same applier, a different file. It is NOT a named entry in
    # presets.json: those are the user's own list, shown in the recall combo,
    # and quietly adding a reserved name to it would make the autosave appear
    # there as though somebody had saved it.
    def _session_file(self) -> str:
        return os.path.join(os.path.dirname(self._preset_file()),
                            "session.json")

    def _session_write(self) -> None:
        """Write the current state, or clear the file when Save is off."""
        import json
        if not hasattr(self, "c_autosave"):
            return
        try:
            if self.c_autosave.isChecked():
                with open(self._session_file(), "w") as fh:
                    json.dump({"save": True,
                               "version": APP_VERSION,
                               "setup": self._gather_setup()}, fh, indent=2)
            elif os.path.isfile(self._session_file()):
                # Unticking means "do not come back like this", so the file
                # goes. Leaving a stale one behind would restore settings
                # from whenever it was last ticked, which is worse than
                # either behaviour the checkbox offers.
                os.remove(self._session_file())
        except Exception as exc:
            self._message(f"could not save settings: {exc}")

    def _session_restore(self) -> None:
        """
        Called once at startup, after the whole window exists.

        No saved file, or save turned off, means the defaults stand -- there
        is nothing to do and nothing to say. A file that will not parse is
        reported rather than swallowed, because silently starting on defaults
        after a crash is how someone loses an afternoon's setup twice.
        """
        import json
        path = self._session_file()
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r") as fh:
                d = json.load(fh)
        except Exception as exc:
            self._message(f"saved settings could not be read ({exc}) -- "
                          f"starting on the defaults")
            return
        if not d.get("save") or not isinstance(d.get("setup"), dict):
            return
        self.c_autosave.blockSignals(True)
        self.c_autosave.setChecked(True)
        self.c_autosave.blockSignals(False)
        self._apply_setup(d["setup"])
        self._message("settings restored from the last session")

    def _autosave_toggled(self, on: bool) -> None:
        # Write immediately rather than only on close: a power cut or a
        # crash between ticking the box and quitting would otherwise mean
        # the tick did nothing, and the box would be lying about what it
        # had done.
        self._session_write()
        self._message("settings will be restored on the next launch" if on
                      else "saved settings cleared -- next launch uses the "
                           "defaults")

    def _preset_load(self) -> None:
        name = self.cb_preset.currentText().strip()
        d = self._presets.get(name)
        if not d:
            self._message(f"no setup called '{name}'")
            return
        self._apply_setup(d)
        self._message(f"recalled setup '{name}'")

    def _apply_setup(self, d: dict) -> None:
        """
        Restore a whole instrument state from the dict _preset_save() writes.

        Shared with the Preset button, which hands it Config()'s own
        defaults. That way "back to how it shipped" goes down exactly the
        same path a recalled setup does and cannot quietly drift away from
        it -- which is the failure mode a hand-written reset always ends up
        having.
        """
        self._quiet = True
        self.s_ref.setValue(d.get("ref_level", -20))
        self.s_div.setValue(d.get("db_div", 10))
        self.s_gain.setValue(float(np.clip(d.get("gain", 30),
                                           self.src.gain_min, self.src.gain_max)))
        # A stored cal offset is HONOURED, including a 0.0 from a setup saved
        # before the +8 dB default existed: it is a calibration somebody set
        # deliberately, and a recalled setup has to mean the level it meant
        # when it was saved. Only a MISSING key falls back to the default.
        # What that cannot be allowed to be is silent -- an 8 dB step with no
        # explanation is the shape of half the bugs in this file -- so the
        # recall says so below when the two differ.
        self.s_cal.setValue(float(d.get("cal_offset", Config().cal_offset)))
        self.cb_win.setCurrentText(d.get("window", "Gaussian"))
        self.sp_points.setValue(int(d.get("points", 1400)))
        self.s_avg.setValue(int(d.get("avg_count", 10)))
        if d.get("detector", "Peak") in DETECTORS:
            self.cb_det.setCurrentText(d.get("detector", "Peak"))
        self.s_fps.setValue(int(d.get("fps", 25)))
        for i, m in enumerate(d.get("trace_modes", [])[:len(self.cb_tr)]):
            self.cb_tr[i].setCurrentText(m)
        # Setups saved before 2026-08-30 have five modes and no freeze list;
        # the traces they do not mention keep their defaults, which is Off.
        for i, v in enumerate(d.get("trace_view", [])[:len(self.c_tr_view)]):
            self.c_tr_view[i].setChecked(bool(v))
        self.c_auto.setChecked(bool(d.get("rbw_auto", True)))
        self.s_limit.setValue(d.get("limit", -60))
        # A setup saved before 2026-08-30 can carry chp_cf_on=False, meaning
        # the channel followed the span when it was saved. There is no such
        # mode any more, so the honest translation is the span centre it
        # would have used -- taking the stored chp_cf instead would silently
        # measure somewhere else than the setup meant.
        _cf1 = (d.get("chp_cf", d.get("center", 2.44e9))
                if d.get("chp_cf_on", True) else d.get("center", 2.44e9))
        _bw1 = d.get("chp_bw", 2e6)
        # A setup saved before 2026-09-14 describes ONE channel, and the
        # honest translation is channel 1 carrying it with the other nine
        # off. Inventing nine more channels from one would put nine shaded
        # bands and nine readings on screen that the setup never meant --
        # and there would be no way to tell them from channels somebody had
        # actually set, which is bug 33's trap in a new place.
        _lst = d.get("chp_list")
        if not isinstance(_lst, list) or not _lst:
            _lst = [{"on": True, "cf": _cf1, "bw": _bw1}]
        for i in range(N_CHP):
            e = _lst[i] if i < len(_lst) and isinstance(_lst[i], dict) else {}
            self.chp_cf[i] = float(e.get("cf", _cf1 if i == 0
                                         else d.get("center", 2.44e9)))
            self.chp_bw[i] = float(e.get("bw", 2e6))
            self.chp_on[i] = bool(e.get("on", i == 0))
        # Everything the setup named is the user's, so nothing gets seeded
        # over it later.
        self._chp_seeded = [True] * N_CHP
        self.cb_chsel.blockSignals(True)
        self.cb_chsel.setCurrentIndex(0)
        self.cb_chsel.blockSignals(False)
        self._chp_sel = 0
        self._chp_load(0)
        self._chp_mark_sel()
        self.e_nominal.setValue(d.get("nominal", 38.4e6))
        # ACLR. A setup from before 2026-09-26 has none of these keys and
        # lands on the defaults, off -- nothing it did not ask for.
        self.cb_aclr.blockSignals(True)
        _std = d.get("aclr_std", ACLR_PRESETS[0][0])
        self.cb_aclr.setCurrentText(_std if self.cb_aclr.findText(_std) >= 0
                                    else ACLR_MANUAL)
        self.cb_aclr.blockSignals(False)
        self.e_aclr_cf.setValue(float(d.get("aclr_cf", d.get("center", 2.44e9))))
        self.e_aclr_bw.setValue(float(d.get("aclr_bw", ACLR_PRESETS[0][2])))
        self.e_aclr_sp.setValue(float(d.get("aclr_spacing", ACLR_PRESETS[0][1])))
        self.s_aclr_n.blockSignals(True)
        self.s_aclr_n.setValue(int(np.clip(d.get("aclr_offsets", 2), 1,
                                           ACLR_OFFSETS_MAX)))
        self.s_aclr_n.blockSignals(False)
        self._aclr_seeded = "aclr_cf" in d
        self.s_ztime.setValue(d.get("zero_time", 200e-6) * 1e6)
        self._quiet = False

        self.cfg = replace(self.cfg,
                           rbw=d.get("rbw", 100e3),
                           rbw_auto=bool(d.get("rbw_auto", True)),
                           window=d.get("window", "Gaussian"),
                           detector=d.get("detector", "Peak"),
                           points=int(d.get("points", 1400)),
                           gain=self.s_gain.value(),
                           cal_offset=float(d.get("cal_offset",
                                                  Config().cal_offset)),
                           # A setup saved on 2026-08-31 can carry
                           # oversample=True and max_fs=122.88e6, and since
                           # the checkbox went (5.25) nothing in the window
                           # could clear either -- so the board would sit in
                           # an unverified overclock on 8-bit samples with no
                           # control to undo it. Exactly bug 33's trap, so
                           # both are forced rather than restored. max_fs is
                           # also clamped to the plain ceiling for the same
                           # reason: a rate the hardware cannot reach with
                           # the feature off gets quietly reduced by the
                           # driver, and then every RBW in the sweep is
                           # computed against a rate the board is not
                           # running (bug 27).
                           max_fs=min(d.get("max_fs", 40e6),
                                      Config().max_fs),
                           oversample=False,
                           zero_time=d.get("zero_time", 200e-6),
                           trace_modes=tuple(d.get("trace_modes",
                                                   self.cfg.trace_modes)),
                           trace_view=tuple(
                               c.isChecked() for c in self.c_tr_view),
                           avg_count=int(d.get("avg_count", 10)),
                           fps=float(d.get("fps", 25)))
        if int(d.get("rx", 0)) != self.rx_ch:
            self._set_rx(int(d.get("rx", 0)))
        self.c_limit.setChecked(bool(d.get("limit_on", False)))
        self.c_chp.setChecked(bool(d.get("chp_on", False)))
        self.c_obw.setChecked(bool(d.get("obw_on", False)))
        self.c_aclr.setChecked(bool(d.get("aclr_on", False)))
        self._aclr_reset()
        self.c_count.setChecked(bool(d.get("counter_on", False)))
        self.c_wf.setChecked(bool(d.get("show_wf", False)))
        self.c_swauto.setChecked(bool(d.get("sweep_auto", True)))
        self.s_swtime.setValue(float(d.get("sweep_time", 0.05)) * 1e3)
        i_tm = self.cb_trig_mode.findData(d.get("trig_mode", TRIG_FREE_RUN))
        if i_tm >= 0:
            self.cb_trig_mode.setCurrentIndex(i_tm)
        self.s_trig_level.setValue(float(d.get("trig_level", -50.0)))
        # Setups written before Avg|Hold became mandatory can say "Off";
        # findData returns -1 for it and the index is left alone, which now
        # means Average. That is the intended reading of an old setup.
        idx = self.cb_meas_avg.findData(d.get("meas_avg", "Average"))
        if idx >= 0:
            self.cb_meas_avg.setCurrentIndex(idx)
        self.s_meas_avg.setValue(int(d.get("meas_avg_n", 20)))
        self._set_continuous(bool(d.get("continuous", True)))
        self.c_zero.setChecked(bool(d.get("zero_span", False)))
        # Frequency last, so the retune lands on the restored span.
        self._retune(d.get("center", 2.44e9), d.get("span", 100e6))
        if d.get("sg"):
            self._sg_toggle_panel(bool(d.get("sg_open", False)))
            self._sg_preset_apply(d["sg"])
        # A level step with nothing on screen to explain it is the shape of
        # half the faults in this program's history, and setups exist on
        # both sides of RX_CAL_DEFAULT_DB's own history: one saved on
        # 2026-09-01 carries 8.0, one saved either side of that carries 0.0.
        # The stored value is HONOURED whichever it is -- it is a
        # calibration somebody set on purpose and a recalled setup has to
        # mean the level it meant when it was saved -- so what is needed is
        # not a force but a sentence.
        if abs(self.cfg.cal_offset - RX_CAL_DEFAULT_DB) > 0.01:
            self._message(
                f"cal offset {self.cfg.cal_offset:+.1f} dB came with this "
                f"setup (the default is {RX_CAL_DEFAULT_DB:+.0f} dB)")

    def _preset_delete(self) -> None:
        import json
        name = self.cb_preset.currentText().strip()
        if name not in self._presets:
            return
        del self._presets[name]
        try:
            with open(self._preset_file(), "w") as fh:
                json.dump(self._presets, fh, indent=2)
        except Exception:
            pass
        self._presets_reload()
        self._message(f"deleted setup '{name}'")

    # -- the five preset slot keys ------------------------------------------
    #
    # Asked for on 2026-09-17: five keys on the front panel that hold a whole
    # spectrum configuration each, so switching between two of them is one
    # press, plus import and export so a set can be handed to a colleague.
    #
    # THREE RULES this whole section follows, and each one is a trap this
    # file has already recorded:
    #
    #   - a slot recalls through _apply_setup(), the same applier the named
    #     setups and the Preset button use. One applier means a slot can
    #     never do something to the instrument that a named setup cannot --
    #     including keying the transmitter, which _apply_setup deliberately
    #     does not do (5.8). A second applier written by hand would drift,
    #     which is bug 26.
    #   - a KEY PRESS never raises a dialog. A modal box under the Qt
    #     offscreen platform is an access violation rather than an exception
    #     (5.9 and 5.22), and every GUI walk in tests\ clicks every key in
    #     the window. An empty slot therefore says so in the status bar and
    #     stops. The MANAGEMENT buttons in the panel may ask, and the one
    #     that does takes a confirm= argument so a test can drive it, which
    #     is exactly how the Preset button is written.
    #   - the slots live in slots.json, BESIDE presets.json and never as a
    #     reserved name inside it. presets.json is the user's own named list
    #     and it is shown in a combo; a reserved key in there would appear as
    #     though somebody had saved it (5.12 makes the same argument for
    #     session.json).

    def _slot_file(self) -> str:
        return os.path.join(os.path.dirname(self._preset_file()),
                            "slots.json")

    @staticmethod
    def _slots_parse(d):
        """
        Read a slots document, or say why it will not be read.

        Returns (entries, reason). entries is None when the document is
        refused, and then reason is a sentence to put on the status bar --
        an import that silently does nothing is the worst of the three
        possible outcomes, and "it did not work" is the second worst.

        Always exactly N_PRESETS entries on success: a file written when
        there were four slots, or eight, still says something useful, and
        the alternative is an index error somewhere further along.
        """
        if not isinstance(d, dict):
            return None, "it is not a preset file"
        if d.get("kind") != PRESET_FILE_KIND:
            return None, f"it is not a {APP_NAME} preset file"
        try:
            ver = int(d.get("version", 1))
        except (TypeError, ValueError):
            ver = 1
        if ver > PRESET_FILE_VERSION:
            return None, (f"it was written by a newer version "
                          f"(format {ver}, this one reads "
                          f"{PRESET_FILE_VERSION})")
        raw = d.get("slots")
        if not isinstance(raw, list):
            return None, "it carries no slots"
        out = []
        for e in raw[:N_PRESETS]:
            if isinstance(e, dict) and isinstance(e.get("setup"), dict):
                out.append({"name": str(e.get("name") or ""),
                            "setup": e["setup"]})
            else:
                # Anything else is an empty slot rather than an error. A
                # null IS how an empty slot is written, and one damaged
                # entry should not cost the other four.
                out.append(None)
        out += [None] * (N_PRESETS - len(out))
        return out, ""

    def _slots_doc(self) -> dict:
        """
        What gets written, and what Export writes -- the SAME document.

        So slots.json can be handed to somebody directly and an exported
        file can be dropped into the settings folder by hand. Two formats
        for one thing is two places for one of them to be wrong.
        """
        return {"app": APP_NAME, "kind": PRESET_FILE_KIND,
                "version": PRESET_FILE_VERSION,
                "written": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "slots": self._slots}

    def _slots_reload(self) -> None:
        import json
        self._slots = [None] * N_PRESETS
        try:
            with open(self._slot_file(), "r") as fh:
                d = json.load(fh)
        except Exception:
            # No file is the ordinary case on a new machine, and a file that
            # will not parse leaves five empty keys rather than no window.
            return
        entries, _why = self._slots_parse(d)
        if entries is not None:
            self._slots = entries

    def _slots_write(self) -> bool:
        import json
        try:
            with open(self._slot_file(), "w") as fh:
                json.dump(self._slots_doc(), fh, indent=2)
        except Exception as exc:
            self._message(f"could not write the presets: {exc}")
            return False
        return True

    def _slot_label(self, i: int) -> str:
        """The slot's name for a human, empty slots included."""
        e = self._slots[i]
        if not e:
            return "(empty)"
        return e.get("name") or "stored, unnamed"

    def _slot_face(self, i: int) -> str:
        """
        What is engraved on the key: the slot NUMBER, then its name.

        The number is on its own line and always there, because the number
        is the identity -- it is what the tooltip, the combo and the status
        messages all say, and a bench instrument's save/recall keys are
        numbered for the same reason. That leaves one line of the two-line,
        eight-character budget for the name, so a longer one is cut with a
        trailing dot to say that it was; the full name is in the tooltip and
        in the Setups panel.
        """
        name = self._slot_label(i)
        if len(name) > 8:
            name = name[:7] + "."
        return f"{i + 1}\n{name}"

    def _sync_preset_keys(self) -> None:
        """
        Re-engrave the five keys, and re-fill the panel that manages them.

        Called after anything that can change what a slot holds. It ends by
        calling _sync_act_keys() rather than setting the lamps itself, so
        there is still exactly one place that decides what a checked key
        means (bug 48).
        """
        keys = getattr(self, "keys", {})
        for i in range(N_PRESETS):
            k = keys.get(f"Preset {i + 1}")
            if k is None:
                continue
            k.set_label(self._slot_face(i))
            k.setToolTip(f"Preset {i + 1}: {self._slot_label(i)}\n\n"
                         + PRESET_KEY_TIP)
        cb = getattr(self, "cb_slot", None)
        if cb is not None:
            at = cb.currentIndex()
            at = at if 0 <= at < N_PRESETS else 0
            cb.blockSignals(True)
            cb.clear()
            cb.addItems([f"{i + 1} - {self._slot_label(i)}"
                         for i in range(N_PRESETS)])
            cb.setCurrentIndex(at)
            cb.blockSignals(False)
            self._slot_show(at)
        self._sync_act_keys()

    def _slot_sel(self) -> int:
        """Which slot the Setups panel is pointed at."""
        cb = getattr(self, "cb_slot", None)
        i = cb.currentIndex() if cb is not None else 0
        return i if 0 <= i < N_PRESETS else 0

    def _slot_show(self, i: int) -> None:
        """Put the selected slot's name in the edit box."""
        e = getattr(self, "e_slot_name", None)
        if e is None:
            return
        i = int(i) if 0 <= int(i) < N_PRESETS else 0
        cur = self._slots[i]
        e.setText((cur or {}).get("name", ""))

    def _slot_touched(self) -> None:
        """
        The measurement has moved away from the slot it was recalled from.

        Cheap on purpose: it is called from _push(), which is every control
        edit in the window, and after the first change there is nothing left
        to do until the next recall.
        """
        if getattr(self, "_slot_cur", None) is None or self._slot_dirty:
            return
        self._slot_dirty = True
        self._sync_act_keys()

    def _preset_key(self, i: int) -> None:
        """
        A preset key was pressed.

        An empty slot says so and stops. It deliberately does NOT offer to
        store the current settings, and it deliberately raises no dialog:
        a key press that turns into a question is a key you cannot press to
        find out what it does, and a modal box from a key press is an access
        violation under the platform every GUI test runs on.
        """
        if not self._slots[i]:
            self._message(f"preset {i + 1} is empty -- store the current "
                          f"settings into it under the Setups key")
            return
        self._slot_recall(i)

    def _slot_recall(self, i: int) -> None:
        e = self._slots[i]
        if not e:
            return
        self._apply_setup(e["setup"])
        # AFTER the apply, never before. _apply_setup drives every control in
        # the window, and each of those goes through _push() -> _slot_touched
        # the moment _quiet is lifted, so a flag set first would be cleared
        # again by the very recall that set it.
        self._slot_cur, self._slot_dirty = i, False
        self._sync_act_keys()
        self._message(f"recalled preset {i + 1} -- {self._slot_label(i)}")

    def _slot_store(self) -> None:
        i = self._slot_sel()
        name = self.e_slot_name.text().strip()
        was = self._slots[i]
        if not name:
            # An unnamed slot is still a usable slot -- what it must not be
            # is a key with nothing on its face.
            name = (was or {}).get("name") or f"Preset {i + 1}"
        self._slots[i] = {"name": name, "setup": self._gather_setup()}
        if not self._slots_write():
            self._slots[i] = was            # it did not land; do not pretend
            self._sync_preset_keys()
            return
        self._slot_cur, self._slot_dirty = i, False
        self._sync_preset_keys()
        over = ""
        if was:
            over = f", over '{was.get('name') or 'what was there'}'"
        self._message(f"stored the current settings in preset {i + 1} "
                      f"as '{name}'{over}")

    def _slot_rename(self) -> None:
        i = self._slot_sel()
        e = self._slots[i]
        if not e:
            self._message(f"preset {i + 1} is empty -- press Store first")
            return
        name = self.e_slot_name.text().strip()
        if not name:
            self._message("give the preset a name first")
            return
        old = e.get("name", "")
        e["name"] = name
        if not self._slots_write():
            e["name"] = old
        self._sync_preset_keys()
        self._message(f"preset {i + 1} is now '{name}'")

    def _slot_clear(self, confirm: bool = True) -> None:
        """
        Empty the selected slot.

        confirm=False is how the tests drive it, exactly as the Preset
        button is driven: the question is a modal box, and a modal box under
        the offscreen platform is an access violation rather than an
        exception. The button that calls this is spelled "Clear..." so that
        it reads as one that asks, which is also what keeps the GUI walk off
        it -- that suite skips anything ending in an ellipsis.
        """
        i = self._slot_sel()
        if not self._slots[i]:
            self._message(f"preset {i + 1} is already empty")
            return
        if confirm:
            if QtWidgets.QMessageBox.question(
                    self, "Clear preset",
                    f"Clear preset {i + 1} ({self._slot_label(i)})?\n\n"
                    f"The stored settings are lost. Nothing on the "
                    f"instrument changes.",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
                return
        was, self._slots[i] = self._slots[i], None
        if not self._slots_write():
            self._slots[i] = was
            self._sync_preset_keys()
            return
        if self._slot_cur == i:
            # The instrument is still set the way that slot said; what is
            # gone is the ability to come back to it. Saying the lamp still
            # means something would be a lie about a slot that is now empty.
            self._slot_cur, self._slot_dirty = None, True
        self._sync_preset_keys()
        self._message(f"preset {i + 1} cleared")

    def _slots_export(self) -> None:
        import json
        if not any(self._slots):
            self._message("there is nothing stored to export")
            return
        stamp = datetime.now().strftime("%Y-%m-%d")
        try:
            os.makedirs(self.shot_dir, exist_ok=True)
            here = self.shot_dir
        except OSError:
            here = ""
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export presets",
            os.path.join(here, f"{APP_NAME} presets {stamp}.json"),
            "Preset file (*.json)")
        if not path:
            return
        try:
            with open(path, "w") as fh:
                json.dump(self._slots_doc(), fh, indent=2)
        except Exception as exc:
            self._message(f"could not export: {exc}")
            return
        n = sum(1 for e in self._slots if e)
        self._message(f"exported {n} preset(s) to {os.path.basename(path)}")

    def _slots_import(self) -> None:
        import json
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import presets", "", "Preset file (*.json)")
        if not path:
            return
        try:
            with open(path, "r") as fh:
                d = json.load(fh)
        except Exception as exc:
            self._message(f"could not read that file: {exc}")
            return
        entries, why = self._slots_parse(d)
        if entries is None:
            # Say WHY. A refusal with no reason is indistinguishable from a
            # button that does not work.
            self._message(f"not imported -- {why}")
            return
        self._slots = entries
        # An import replaces all five, so whatever the instrument was
        # recalled from no longer describes what is stored. Nothing on the
        # instrument itself moves: importing is filing, not recalling.
        self._slot_cur, self._slot_dirty = None, True
        self._slots_write()
        self._sync_preset_keys()
        n = sum(1 for e in entries if e)
        self._message(f"imported {n} preset(s) from "
                      f"{os.path.basename(path)} -- nothing on the "
                      f"instrument has changed, press a preset key to use one")

    def _box_slots(self):
        b = Box("Preset Keys", A_FILE)
        # Read before the keys are engraved: build_keys() runs after every
        # box is built, and _sync_preset_keys() right after it.
        self._slots_reload()
        # No grey explanation at either end of this box since 2026-09-17, on
        # request. It sat on the key directly above the slots it fills in, so
        # it was telling somebody who had already found it what they had
        # found. Every control here still carries its own tooltip, which is
        # where an explanation belongs -- read when it is wanted rather than
        # occupying the panel every time it is opened.
        self.cb_slot = QtWidgets.QComboBox()
        self.cb_slot.setToolTip("Which preset key these buttons act "
                                "on.\n\nSelecting a slot changes nothing on "
                                "the instrument -- press the key itself to "
                                "recall it.")
        self.cb_slot.currentIndexChanged.connect(self._slot_show)
        b.row("Slot", self.cb_slot)
        self.e_slot_name = QtWidgets.QLineEdit()
        self.e_slot_name.setMaxLength(40)
        self.e_slot_name.setToolTip(
            "What the slot is called. The first eight characters are "
            "engraved on the key under its number; the whole name is in "
            "the key's tooltip and in the Slot list above.")
        b.row("Name", self.e_slot_name)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        for label, fn, tip in (
                ("Store", self._slot_store,
                 "Put the CURRENT settings into the selected slot, under "
                 "the name above. Overwrites whatever was there, and says "
                 "what it replaced."),
                ("Rename", self._slot_rename,
                 "Change the selected slot's name without touching the "
                 "settings stored in it."),
                # "Clear..." rather than "Clear": the ellipsis is the
                # ordinary signal that a control asks before it acts, and it
                # is also what keeps the GUI walk off a modal dialog.
                ("Clear...", self._slot_clear,
                 "Empty the selected slot. Asks first. Nothing on the "
                 "instrument changes -- only the stored copy goes.")):
            btn = QtWidgets.QPushButton(label)
            btn.setFixedHeight(23)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _c, f=fn: f())
            row.addWidget(btn)
        w = QtWidgets.QWidget(); w.setLayout(row); b.full(w)
        row2 = QtWidgets.QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        for label, fn, tip in (
                ("Export...", self._slots_export,
                 "Write all the slots to one file, to keep or to hand to "
                 "somebody else. It is the same format the program stores "
                 "them in."),
                ("Import...", self._slots_import,
                 "Read all the slots back from such a file. It replaces "
                 "what is stored and says how many it found; the "
                 "instrument itself is not touched until you press a "
                 "preset key.")):
            btn = QtWidgets.QPushButton(label)
            btn.setFixedHeight(23)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _c, f=fn: f())
            row2.addWidget(btn)
        w2 = QtWidgets.QWidget(); w2.setLayout(row2); b.full(w2)
        return b

    # -- the macro ------------------------------------------------------------
    #
    # Kfir, 2026-09-23: "The user on-site wants to prepare a Macro
    # Configuration Order for recording the spectrum ... prepare a screen
    # configuration, then screenshot it, configure again something else, wait
    # x time delay, then screenshot again, and so on."
    #
    # A settings step is a SNAPSHOT of the whole instrument, taken by setting
    # the screen up the ordinary way and pressing Add settings -- so every
    # control the window has can be in a macro with no second list of fields
    # to maintain, and it recalls through _apply_setup(), the one applier
    # (bug 26). Like a preset, it never keys the transmitter.
    #
    # The run is a QTimer on the GUI thread, never a thread of its own: every
    # step is a GUI action (apply, grab, write), and bug 17 is what happens
    # when anything but the worker touches the radio.

    def _macro_file(self) -> str:
        return os.path.join(os.path.dirname(self._preset_file()),
                            "macro.json")

    def _macro_doc(self) -> dict:
        """What Save writes and what macro.json holds -- the same document,
        so a prepared macro can be emailed and opened, or dropped into the
        settings folder by hand."""
        return {"app": APP_NAME, "kind": MACRO_FILE_KIND,
                "version": MACRO_FILE_VERSION,
                "written": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "name": self._macro["name"],
                "passes": int(self._macro["passes"]),
                "steps": self._macro["steps"]}

    def _macro_autosave(self) -> None:
        """Every edit is kept at once, so the macro prepared yesterday is
        still in the panel today even if nobody pressed Save..."""
        try:
            with open(self._macro_file(), "w") as fh:
                json.dump(self._macro_doc(), fh, indent=1)
        except Exception as exc:
            self._message(f"could not keep the sequence: {exc}")

    def _macro_reload(self) -> None:
        self._macro = {"name": MACRO_NAME, "passes": 1, "steps": []}
        try:
            with open(self._macro_file(), "r") as fh:
                m, _why = macro_parse(json.load(fh))
        except Exception:
            return
        if m is not None:
            # "Macro" in a kept file is the OLD default, not a name anybody
            # chose -- left alone it would say "Macro: 5 steps" under a panel
            # called Sequencer. A name somebody gave it stays as it is.
            if m["name"] == "Macro":
                m["name"] = MACRO_NAME
            self._macro = m

    def _macro_sel(self) -> int:
        return self.lst_macro.currentRow()

    def _macro_refresh(self, select: Optional[int] = None) -> None:
        steps = self._macro["steps"]
        keep = self._macro_sel() if select is None else select
        self._mlines = macro_describe(steps)
        self.lst_macro.blockSignals(True)
        self.lst_macro.clear()
        self.lst_macro.addItems(self._mlines)
        self.lst_macro.blockSignals(False)
        if steps:
            self.lst_macro.setCurrentRow(int(np.clip(keep, 0,
                                                     len(steps) - 1)))
        self._macro_mark()
        self.s_mpasses.blockSignals(True)
        self.s_mpasses.setValue(int(self._macro["passes"]))
        self.s_mpasses.blockSignals(False)
        caps = sum(1 for s in steps if s["kind"] == "capture")
        secs = sum(s["value"] for s in steps
                   if s["kind"] == "wait" and s["unit"] == "seconds")
        n = int(self._macro["passes"])
        self.l_minfo.setText(
            f"{self._macro['name']}: {len(steps)} steps, {caps * n} captures"
            + (f", at least {_num(secs * n)} s" if secs else ""))
        running = self._mrun is not None
        for w in self._macro_edit:
            w.setEnabled(not running)
        self.b_mrun.setText("Stop Sequence" if running else "Run Sequence")

    def _macro_mark(self) -> None:
        """An arrow and bold type on the selected step, as well as the fill.

        Kfir, 2026-09-24: with Up / Down it was not clear which row was
        selected. The reason is focus: pressing a button takes it off the
        list, and Qt then draws the selection in its faint INACTIVE colour.
        The stylesheet in _box_macro fixes the colour for both states; the
        arrow is there as well because it does not depend on colour at all.
        """
        sel = self._macro_sel()
        lines = getattr(self, "_mlines", [])
        for i in range(self.lst_macro.count()):
            it = self.lst_macro.item(i)
            if i >= len(lines):
                break
            it.setText((MACRO_ARROW if i == sel else MACRO_NO_ARROW)
                       + lines[i])
            f = it.font()
            f.setBold(i == sel)
            it.setFont(f)

    def _macro_changed(self, select: Optional[int] = None) -> None:
        self._macro_refresh(select)
        self._macro_autosave()

    def _macro_insert(self, step: dict) -> None:
        """After the selected step, so a procedure is built top to bottom
        and a forgotten step can be put where it belongs."""
        i = self._macro_sel()
        at = len(self._macro["steps"]) if i < 0 else i + 1
        self._macro["steps"].insert(at, step)
        self._macro_changed(at)

    def _macro_wait_step(self) -> dict:
        unit = "sweeps" if self.cb_munit.currentIndex() == 1 else "seconds"
        v = self.s_mwait.value()
        return {"kind": "wait", "unit": unit,
                "value": float(max(1, round(v)) if unit == "sweeps" else v)}

    def _macro_capture_step(self) -> dict:
        return {"kind": "capture",
                "name": self.e_mname.text().strip() or MACRO_DEFAULT_NAME,
                "png": self.c_mpng.isChecked(),
                "csv": self.c_mcsv.isChecked()}

    def _macro_add_settings(self) -> None:
        self._macro_insert({"kind": "settings",
                            "setup": json.loads(json.dumps(
                                self._gather_setup()))})
        self._message("the current settings are a sequence step now")

    def _macro_add_wait(self) -> None:
        self._macro_insert(self._macro_wait_step())

    def _macro_add_capture(self) -> None:
        self._macro_insert(self._macro_capture_step())

    def _macro_update(self) -> None:
        """Rewrite the selected step: a settings step from the SCREEN, a wait
        or a capture from the boxes below the list."""
        i = self._macro_sel()
        if i < 0:
            return
        kind = self._macro["steps"][i]["kind"]
        if kind == "settings":
            new = {"kind": "settings", "setup": json.loads(json.dumps(
                self._gather_setup()))}
        elif kind == "wait":
            new = self._macro_wait_step()
        else:
            new = self._macro_capture_step()
        self._macro["steps"][i] = new
        self._macro_changed(i)
        self._message(f"step {i + 1} updated")

    def _macro_move(self, d: int) -> None:
        i = self._macro_sel()
        j = i + d
        st = self._macro["steps"]
        if i < 0 or not 0 <= j < len(st):
            return
        st[i], st[j] = st[j], st[i]
        self._macro_changed(j)

    def _macro_delete(self) -> None:
        i = self._macro_sel()
        if i < 0:
            return
        del self._macro["steps"][i]
        self._macro_changed(i)

    def _macro_picked(self, i: int) -> None:
        """Selecting a step shows its values in the boxes -- nothing on the
        instrument moves until a settings step is opened with Edit (or
        double-clicked)."""
        self._macro_mark()
        if not 0 <= i < len(self._macro["steps"]):
            return
        s = self._macro["steps"][i]
        if s["kind"] == "wait":
            self.cb_munit.setCurrentIndex(1 if s["unit"] == "sweeps" else 0)
            self.s_mwait.setValue(float(s["value"]))
        elif s["kind"] == "capture":
            self.e_mname.setText(s["name"])
            self.c_mpng.setChecked(bool(s["png"]))
            self.c_mcsv.setChecked(bool(s["csv"]))

    def _macro_show(self, i: int) -> None:
        """Double-click a settings step: put it on the screen, so it can be
        looked at, adjusted, and written back with Update."""
        if self._mrun is not None or not 0 <= i < len(self._macro["steps"]):
            return
        s = self._macro["steps"][i]
        if s["kind"] == "settings":
            self._apply_setup(s["setup"])
            self._message(f"step {i + 1}'s settings are on the screen -- "
                          f"change them and press Update to keep the change")

    def _macro_edit_step(self) -> None:
        """The Edit button: open the selected step for changing, whatever
        its kind. A settings step goes onto the screen; a wait or capture is
        already in the boxes from selecting it. Update writes it back."""
        i = self._macro_sel()
        if i < 0:
            self._message("select a step to edit first")
            return
        if self._macro["steps"][i]["kind"] == "settings":
            self._macro_show(i)
        else:
            self._macro_picked(i)
            self._message(f"step {i + 1} is in the Wait / Name boxes -- "
                          f"change it and press Update to keep the change")

    def _macro_write(self, path: str) -> bool:
        try:
            with open(path, "w") as fh:
                json.dump(self._macro_doc(), fh, indent=1)
        except Exception as exc:
            self._message(f"could not save the sequence: {exc}")
            return False
        return True

    def _macro_read(self, path: str) -> bool:
        try:
            with open(path, "r") as fh:
                m, why = macro_parse(json.load(fh))
        except Exception as exc:
            self._message(f"could not read that file: {exc}")
            return False
        if m is None:
            self._message(f"not opened -- {why}")
            return False
        self._macro = m
        self._macro_changed(0)
        self._message(f"opened sequence '{m['name']}', {len(m['steps'])} "
                      f"steps -- nothing has run yet, press Run Sequence")
        return True

    def _macro_save_as(self) -> None:
        try:
            os.makedirs(self.shot_dir, exist_ok=True)
            here = self.shot_dir
        except OSError:
            here = ""
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save sequence",
            os.path.join(here, f"{self._macro['name']}.json"),
            "Sequence file (*.json)")
        if not path:
            return
        # The file's name is the sequence's name from here on: it is what the
        # run folders are called, and what the person opening it will see.
        self._macro["name"] = os.path.splitext(os.path.basename(path))[0]
        if self._macro_write(path):
            self._macro_changed()
            self._message(f"sequence saved as {os.path.basename(path)}")

    def _macro_open(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open sequence", "", "Sequence file (*.json)")
        if path:
            self._macro_read(path)

    def _macro_toggle(self) -> None:
        if self._mrun is None:
            self._macro_run()
        else:
            self._macro_stop("stopped by hand")

    def _macro_run(self) -> None:
        steps = self._macro["steps"]
        if not steps:
            self._message("the sequence has no steps -- set the screen up and "
                          "press Add settings to start one")
            return
        if self._mrun is not None:
            return
        stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
        safe = macro_name(self._macro["name"], {}) or MACRO_NAME
        self._mrun = {"i": 0, "pass": 1, "entered": False, "seen": 0,
                      "fresh": 0, "stale": 0, "caps": 0, "t_end": 0.0,
                      "t_step": time.monotonic(), "t_last": None,
                      "names": set(),
                      "folder": os.path.join(self.shot_dir, MACRO_DIRNAME,
                                             f"{safe} {stamp}"),
                      # "pending" until the recording starts, then "own"
                      # (ours to save at the end), "user" (somebody's own
                      # recording was already running -- theirs, left
                      # alone) or "failed".
                      "rec": "pending"}
        self._mrun["rec_folder"] = os.path.join(self._mrun["folder"],
                                                MACRO_REC_DIRNAME)
        self._mtimer = QtCore.QTimer(self)
        self._mtimer.timeout.connect(self._macro_tick)
        self._mtimer.start(MACRO_TICK_MS)
        self._macro_refresh()
        self._macro_rec_begin()
        self._message(f"sequence '{self._macro['name']}' running and "
                      f"recording -- Stop Sequence ends it at any time")
        self._macro_tick()

    def _macro_rec_begin(self) -> None:
        """Start the run's recording, if it is not running yet. Called at the
        start and on every tick until it has happened: before the first sweep
        there is nothing to record against (_rec_start says why), and the
        first step is very often a settings step that is about to change the
        axis anyway."""
        m = self._mrun
        if m is None or m["rec"] != "pending":
            return
        if getattr(self, "rec", None) is not None:
            # Somebody pressed Record before the run. That recording is
            # theirs: it carries on through the run and past it, and they
            # stop it when they choose. Stopping it here would end something
            # this run did not start.
            m["rec"] = "user"
            return
        if self.last is None:
            return
        m["rec"] = "own" if self._rec_start(m["rec_folder"]) else "failed"

    def _macro_next(self) -> None:
        m = self._mrun
        m["i"] += 1
        m["entered"] = False
        if m["i"] >= len(self._macro["steps"]):
            if m["pass"] >= int(self._macro["passes"]):
                self._macro_finish()
                return
            m["pass"] += 1
            m["i"] = 0

    def _macro_tick(self) -> None:
        m = self._mrun
        if m is None:
            return
        self._macro_rec_begin()
        # Steps that finish at once (settings) chain within one tick; the
        # bound only stops a list of nothing but settings spinning forever.
        for _guard in range(len(self._macro["steps"]) + 1):
            m = self._mrun
            if m is None:
                return
            st = self._macro["steps"][m["i"]]
            now = time.monotonic()
            if not m["entered"]:
                m["entered"], m["seen"], m["t_step"] = True, 0, now
                if st["kind"] == "settings":
                    try:
                        self._apply_setup(st["setup"])
                        # A fresh Max Hold for every settings step: holding
                        # across it would carry the last screen's peaks into
                        # this one's capture.
                        self._restart_meas()
                    except Exception as exc:
                        self._macro_stop(f"step {m['i'] + 1}'s settings "
                                         f"could not be applied: {exc}")
                        return
                    # The sweep in flight was planned on the OLD settings.
                    m["stale"], m["fresh"] = 1, 0
                    self._macro_next()
                    continue
                if st["kind"] == "wait" and st["unit"] == "seconds":
                    m["t_end"] = now + float(st["value"])
            if st["kind"] == "wait":
                done = (m["seen"] >= int(st["value"]) if st["unit"] == "sweeps"
                        else now >= m["t_end"])
                if not done:
                    break
                self._macro_next()
            elif st["kind"] == "capture":
                # Never a picture of the settings BEFORE this one: at least
                # one sweep has to have arrived since the last settings step.
                if m["fresh"] < 1:
                    break
                if not self._macro_capture(st):
                    return
                self._macro_next()
            else:
                self._macro_next()
        self._macro_status()

    def _macro_fields(self) -> dict:
        m, c = self._mrun, self.cfg
        now = datetime.now()
        return {"pass": m["pass"], "n": m["caps"] + 1,
                "center_mhz": _num(c.center / 1e6),
                "span_mhz": _num(c.span / 1e6),
                "rbw_khz": _num((self.last.rbw if self.last is not None
                                 else c.rbw) / 1e3),
                "detector": c.detector,
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H%M%S")}

    def _macro_capture(self, st: dict) -> bool:
        m = self._mrun
        base = macro_name(st["name"], self._macro_fields())
        # Two captures resolving to one name (a template without {pass} run
        # twice) must not overwrite: the second gets _2, and so on.
        name, k = base, 2
        while name.lower() in m["names"]:
            name, k = f"{base}_{k}", k + 1
        m["names"].add(name.lower())
        try:
            os.makedirs(m["folder"], exist_ok=True)
            stem = os.path.join(m["folder"], name)
            if st.get("png", True) and not self._grab().save(stem + ".png"):
                raise OSError("the picture could not be written")
            # csv only, no xlsx beside it (Kfir, 2026-09-24): a macro makes
            # dozens of captures and the workbook doubles the folder for
            # nothing a presale report needs. _save_trace still writes both.
            if st.get("csv", True) and not self._write_csv(stem + ".csv"):
                raise OSError("the trace could not be written")
        except Exception as exc:
            # Stop rather than carry on: a disk that refused one file will
            # refuse the next, and a run that "finished" with holes in it is
            # worse on site than one that says where it stopped.
            self._macro_stop(f"capture '{name}' failed: {exc}")
            return False
        m["caps"] += 1
        return True

    def _macro_status(self) -> None:
        m = self._mrun
        if m is None:
            self.l_macro.setText("")
            self.l_macro.setToolTip("")
            return
        steps = self._macro["steps"]
        st = steps[min(m["i"], len(steps) - 1)]
        now = time.monotonic()
        head = (f"SEQUENCE pass {m['pass']}/{self._macro['passes']}  "
                f"step {m['i'] + 1}/{len(steps)}  ")
        amber = False
        if st["kind"] == "wait" and st["unit"] == "seconds":
            what = f"wait {max(0.0, m['t_end'] - now):.0f} s"
        elif st["kind"] == "wait":
            what = f"wait {m['seen']}/{int(st['value'])} sweeps"
        else:
            what = "capture: waiting for a sweep"
        # Something that waits on sweeps and gets none says so and says why,
        # for the recorder's reason: nobody is watching it.
        waits_on_sweeps = st["kind"] == "capture" or st["unit"] == "sweeps"
        last = m["t_last"] if m["t_last"] is not None else m["t_step"]
        if waits_on_sweeps and now - max(last, m["t_step"]) > 3.0:
            amber = True
            what += (" -- NO SWEEP: the sweep is held (press Single)"
                     if not self.cfg.continuous else " -- NO SWEEP arriving")
        self.l_macro.setText(head + what + f"  {m['caps']} saved")
        self.l_macro.setStyleSheet(
            f"color:{'#ffb340' if amber else A_MEASURE};font-weight:bold;")
        self.l_macro.setToolTip(f"Saving to {m['folder']}")

    def _macro_end(self) -> dict:
        m = self._mrun
        if self._mtimer is not None:
            self._mtimer.stop()
        self._mrun = None
        # Save the run's own recording. Only if it is still the one this run
        # started: pressing Record mid-run ends it early (and saves it), and
        # whatever is recording now is then somebody else's.
        m["rec_saved"] = False
        rec = getattr(self, "rec", None)
        if m["rec"] == "own" and rec is not None \
                and rec.folder == m["rec_folder"]:
            self._rec_stop(quiet=True)
        if m["rec"] == "own" and os.path.isfile(
                os.path.join(m["rec_folder"], "spectrum.bin")):
            m["rec_saved"] = True
        self._macro_status()
        self._macro_refresh()
        return m

    @staticmethod
    def _macro_rec_note(m: dict) -> str:
        if m["rec_saved"]:
            return f", recording in {MACRO_REC_DIRNAME}"
        if m["rec"] == "user":
            return ", your own recording is still running"
        if m["rec"] == "pending":
            return ", NOT recorded (no sweep arrived to record)"
        if m["rec"] == "failed":
            return ", NOT recorded"
        return ""

    def _macro_finish(self) -> None:
        m = self._macro_end()
        self._message(f"sequence finished -- {m['caps']} capture(s)"
                      f"{self._macro_rec_note(m)} -- {m['folder']}",
                      seconds=12.0)
        if m["caps"] or m["rec_saved"]:
            try:
                reveal_folder(m["folder"])
            except Exception as exc:
                self._message(f"could not open {m['folder']}: {exc}")

    def _macro_stop(self, why: str, quiet: bool = False) -> None:
        if self._mrun is None:
            return
        at = f"pass {self._mrun['pass']}, step {self._mrun['i'] + 1}"
        m = self._macro_end()
        if quiet:
            return
        self._message(f"sequence stopped at {at}: {why} -- {m['caps']} "
                      f"capture(s) kept{self._macro_rec_note(m)}",
                      seconds=12.0)
        if m["caps"] or m["rec_saved"]:
            try:
                reveal_folder(m["folder"])
            except Exception:
                pass

    def _box_macro(self):
        b = Box("Sequencer", A_FILE)
        self._macro_reload()
        self.lst_macro = QtWidgets.QListWidget()
        # Wider than the other panels and wrapping, measured on the real
        # display: at the default width a settings line ran behind a
        # sideways scrollbar, and the end of the line is what it CHANGES.
        self.lst_macro.setFixedHeight(190)
        self.lst_macro.setMinimumWidth(460)
        self.lst_macro.setWordWrap(True)
        self.lst_macro.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarAlwaysOff)
        # The selected row is filled in the tune accent with dark text in
        # BOTH focus states. Without the :!active rule Qt draws it in a faint
        # grey the moment Up / Down takes the focus, which is exactly when
        # somebody is looking for it.
        _sel = (f"background:{A_TUNE};color:#101418;"
                f"border-left:5px solid #ffffff;")
        self.lst_macro.setStyleSheet(
            "QListWidget::item{padding:3px 2px;"
            "border-left:5px solid transparent;}"
            f"QListWidget::item:selected{{{_sel}}}"
            f"QListWidget::item:selected:!active{{{_sel}}}")
        self.lst_macro.setToolTip(
            "The sequence's steps, run top to bottom.\n\n"
            "Click a step to select it (it turns yellow, with an arrow).\n"
            "To change a step: select it, press Edit, change it, then press\n"
            "Update. Edit puts a SETTINGS step on the screen; a wait or a\n"
            "capture is shown in the Wait / Name boxes.")
        self.lst_macro.currentRowChanged.connect(self._macro_picked)
        self.lst_macro.itemDoubleClicked.connect(
            lambda it: self._macro_show(self.lst_macro.row(it)))
        b.full(self.lst_macro)
        self._macro_edit = []

        def buttons(specs):
            row = QtWidgets.QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            out = []
            for label, fn, tip in specs:
                btn = QtWidgets.QPushButton(label)
                btn.setFixedHeight(23)
                btn.setToolTip(tip)
                btn.clicked.connect(lambda _c, f=fn: f())
                row.addWidget(btn)
                out.append(btn)
            w = QtWidgets.QWidget(); w.setLayout(row); b.full(w)
            return out

        self._macro_edit += buttons((
            ("Add settings", self._macro_add_settings,
             "Add the CURRENT screen as a step: set the instrument up the\n"
             "ordinary way first (centre, span, RBW, traces, detector,\n"
             "points...), then press this. Every setting is stored; the\n"
             "list shows what changed from the settings step before.\n\n"
             "Running it restarts the traces, so a Max Hold starts fresh."),
            ("Add wait", self._macro_add_wait,
             "Add a wait of the length in the Wait box below -- seconds, or\n"
             "a number of sweeps (use sweeps for 'Average, count 30')."),
            ("Add capture", self._macro_add_capture,
             "Add a capture: PNG and/or CSV, named from the Name\n"
             "box below. It always waits for a sweep made on the current\n"
             "settings first, so it can never save the previous screen.")))

        self.s_mwait = QtWidgets.QDoubleSpinBox()
        self.s_mwait.setRange(0.1, 36000.0)
        self.s_mwait.setDecimals(1)
        self.s_mwait.setValue(10.0)
        self.cb_munit = QtWidgets.QComboBox()
        self.cb_munit.addItems(["seconds", "sweeps"])
        wrow = QtWidgets.QHBoxLayout(); wrow.setContentsMargins(0, 0, 0, 0)
        wrow.addWidget(self.s_mwait, 1); wrow.addWidget(self.cb_munit)
        ww = QtWidgets.QWidget(); ww.setLayout(wrow)
        b.row("Wait", ww)

        self.e_mname = QtWidgets.QLineEdit(MACRO_DEFAULT_NAME)
        self.e_mname.setToolTip(
            "The capture's file name. These are filled in when it runs:\n"
            "  {pass}        which pass of the sequence, 1, 2, ...\n"
            "  {n}           the capture's number in this run\n"
            "  {center_mhz}  e.g. 750      {span_mhz}  e.g. 100\n"
            "  {rbw_khz}     e.g. 100      {detector}  Peak / Average ...\n"
            "  {date}  {time}\n\n"
            "So {pass}_{center_mhz}_MH saves 1_750_MH.png and .csv.\n"
            "A name used twice in one run gets _2, never an overwrite.")
        self.c_mpng = QtWidgets.QCheckBox("PNG")
        self.c_mpng.setChecked(True)
        self.c_mcsv = QtWidgets.QCheckBox("CSV")
        self.c_mcsv.setChecked(True)
        nrow = QtWidgets.QHBoxLayout(); nrow.setContentsMargins(0, 0, 0, 0)
        nrow.addWidget(self.e_mname, 1)
        nrow.addWidget(self.c_mpng); nrow.addWidget(self.c_mcsv)
        nw = QtWidgets.QWidget(); nw.setLayout(nrow)
        b.row("Name", nw)

        self._macro_edit += buttons((
            ("Up", lambda: self._macro_move(-1), "Move the selected step up."),
            ("Down", lambda: self._macro_move(1),
             "Move the selected step down."),
            ("Edit", self._macro_edit_step,
             "Open the selected step for changing. A settings step is put\n"
             "on the screen -- change what you like the ordinary way; a wait\n"
             "or capture is shown in the boxes above. Then press Update."),
            ("Update", self._macro_update,
             "Write the change back into the selected step: a settings step\n"
             "from the screen as it is now, a wait or capture from the boxes\n"
             "above. The other steps are not touched."),
            ("Delete", self._macro_delete, "Remove the selected step.")))

        self.s_mpasses = QtWidgets.QSpinBox()
        self.s_mpasses.setRange(1, MACRO_PASSES_MAX)
        self.s_mpasses.setToolTip(
            "Run the whole list this many times. {pass} in a capture name\n"
            "counts them, so pass 1 and pass 2 never overwrite each other.")

        def _passes(v):
            self._macro["passes"] = int(v)
            self._macro_changed()
        self.s_mpasses.valueChanged.connect(_passes)
        b.row("Passes", self.s_mpasses)
        self._macro_edit += [self.lst_macro, self.s_mwait, self.cb_munit,
                             self.e_mname, self.c_mpng, self.c_mcsv,
                             self.s_mpasses]

        run = buttons((
            ("Run Sequence", self._macro_toggle,
             "Run the steps top to bottom, all passes. Press again to stop.\n"
             "The status bar says where it is. Each run saves into its own\n"
             "folder under the capture folder's Sequences\\, which opens\n"
             "when it finishes. It never switches the transmitter on."),
            ("Save...", self._macro_save_as,
             "Save the sequence to a file -- to keep, or to send to whoever\n"
             "is going on site. The file's name becomes its name."),
            ("Open...", self._macro_open,
             "Open a sequence file (an older macro file opens too). It\n"
             "replaces the steps here; nothing runs until Run Sequence.")))
        self.b_mrun = run[0]
        self._macro_edit += run[1:]
        self.l_minfo = QtWidgets.QLabel("")
        self.l_minfo.setStyleSheet("color:#8a96a3;")
        self.l_minfo.setWordWrap(True)
        b.full(self.l_minfo)
        self._macro_refresh(0)
        return b

    def _box_view(self):
        b = Box("Display", A_VIEW)
        self.c_wf = QtWidgets.QCheckBox("Show spectrogram")
        # OFF at startup, on request. The spectrum is the measurement; the
        # spectrogram is a second view of it that costs a third of the window
        # and 0.97 ms a frame, and someone who wants it can say so. The
        # checkbox is unchanged otherwise -- this is the default only.
        self.c_wf.setChecked(False)
        self.c_wf.toggled.connect(self._show_wf)
        b.full(self.c_wf)
        self.s_fps = QtWidgets.QSpinBox()
        self.s_fps.setRange(1, 60)
        self.s_fps.setValue(25)
        self.s_fps.setSuffix(" fps")
        self.s_fps.setToolTip("Screen refresh only. Every sweep is still\n"
                              "folded into the traces between frames.")
        self.s_fps.valueChanged.connect(lambda v: self._push(fps=v))
        b.row("Refresh", self.s_fps)
        self.s_depth = QtWidgets.QSpinBox()
        self.s_depth.setRange(40, 800)
        self.s_depth.setValue(240)
        self.s_depth.setSuffix(" rows")
        self.s_depth.valueChanged.connect(self._set_depth)
        b.row("Spectrogram", self.s_depth)

        self.sp_points = QtWidgets.QSpinBox()
        self.sp_points.setRange(200, 4000)
        self.sp_points.setSingleStep(100)
        self.sp_points.setValue(1400)
        self.sp_points.setToolTip(
            "Points across the span -- EXACTLY this many, the first on the\n"
            "start frequency and the last on the stop, as on a bench\n"
            "analyzer. 1001 points on a 100 MHz span is one every 100 kHz,\n"
            "and a saved CSV then lines up row for row with one from\n"
            "another analyzer set the same way.\n"
            "Drawing cost is roughly proportional to\n"
            "this, so dropping it is the quickest way to buy frame rate.\n"
            "With the detector on Peak, fewer points does not hide narrow\n"
            "signals -- each point still reports the largest bin under it.")
        self.sp_points.valueChanged.connect(lambda v: self._push(points=v))
        b.row("Trace points", self.sp_points)

        # "Smooth lines" was here and went on 2026-08-30, on request. It
        # switched pyqtgraph's antialiasing on, which was measured at about
        # ten times the drawing time (section 4 bug 7) for a slightly
        # cleaner line, and it was off by default. Nothing replaces it:
        # pg.setConfigOptions(antialias=False) at the top of the file is the
        # state the unticked box left, so the display behaves exactly as it
        # did with the control sitting untouched.

        self.l_perf = QtWidgets.QLabel("")
        self.l_perf.setStyleSheet("color:#8fa6bb;")
        b.full(self.l_perf)
        return b

    def _theme(self) -> None:
        self.setStyleSheet("""
        QMainWindow,QWidget{background:#12161a;color:#d7e2ec;font-size:12px;}
        QGroupBox{border:1px solid #263039;border-radius:6px;margin-top:9px;
          padding-top:6px;font-weight:bold;color:#8fa6bb;}
        QGroupBox::title{subcontrol-origin:margin;left:9px;padding:0 4px;}
        QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QTableWidget{
          background:#1b2129;border:1px solid #2c3742;border-radius:4px;
          padding:3px 5px;color:#eaf2f8;font-weight:normal;}
        QLineEdit:focus,QComboBox:focus,QDoubleSpinBox:focus{border:1px solid #C0521A;}
        /* Ordinary buttons inside the panels, on both instruments. Same
           idiom as the front-panel keys (see key_qss): a faint top-lit
           gradient, a darker bottom edge for depth, and four states that
           each say something different. min-height is what keeps a row of
           buttons the same size whatever text is in them. */
        QPushButton{border:1px solid #34414e;border-bottom-color:#28323c;
          border-radius:4px;padding:4px 8px;min-height:17px;
          color:#dbe6f0;font-weight:normal;
          background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
            stop:0 #273140,stop:1 #1e2630);}
        QPushButton:hover{border-color:#48596b;
          background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
            stop:0 #313d4d,stop:1 #253039);}
        /* Pressed inverts the gradient, so it reads as recessed rather than
           as a different colour. */
        QPushButton:pressed{border-color:#C0521A;
          background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
            stop:0 #7d360f,stop:1 #a8481a);color:#fff;}
        /* Checkable buttons -- RX1/RX2 and TX1/TX2 -- had no checked style
           at all, so the selected port looked exactly like the unselected
           one and there was no way to tell which was in use. The 2px accent
           border is the "this one is live" mark; a colour change alone is
           easy to miss on a dim bench. */
        QPushButton:checked{border:2px solid #58aae8;color:#ffffff;
          font-weight:bold;padding:3px 7px;
          background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
            stop:0 #1f5c8d,stop:1 #163f60);}
        QPushButton:checked:hover{
          background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
            stop:0 #266ea6,stop:1 #1a4a70);}
        /* Flat and desaturated, deliberately with no gradient: "cannot be
           pressed" must not look like "not currently pressed". */
        QPushButton:disabled{background:#1a2027;border:1px solid #232c35;
          color:#5b6b7a;}
        QCheckBox{font-weight:normal;}
        QHeaderView::section{background:#1b2129;color:#8fa6bb;border:0;padding:3px;}
        QStatusBar{color:#8fa6bb;} QScrollArea{border:0;}
        QMenuBar{background:#12161a;} QMenuBar::item:selected{background:#222b34;}
        """)
        # The grid, re-stated rather than set once at build time. _zero()
        # swaps a whole new bottom axis in (HzAxis <-> SecAxis) and a fresh
        # AxisItem carries grid=False, so entering and leaving zero span used
        # to drop the vertical graticule lines for the rest of the session.
        # It went unnoticed while the grid was faint and became obvious the
        # moment it was brightened; found by the new guard in
        # tests\test_gui_controls.py, not by looking. The pen and the grid are
        # restored in the same place on purpose -- they are the same setting
        # split across two calls (see GRID_PEN), and a swap loses both.
        #
        # And it has to be setGrid() on the axes, not showGrid(): showGrid
        # only moves the plot's own grid checkboxes and alpha slider, and
        # those are ALREADY where we want them, so setting them again emits
        # nothing and pyqtgraph's updateGrid() never runs. Calling showGrid a
        # second time therefore looks like the fix and does nothing at all.
        for pl in (self.plot, self.plot2):
            pl.showGrid(x=True, y=True, alpha=GRID_ALPHA)
            for ax in ("left", "bottom"):
                pl.getAxis(ax).setGrid(int(GRID_ALPHA * 255))
        for p in (self.plot, self.plot2, self.wf):
            # Pure black behind the trace, the way a bench analyzer's screen
            # is, so the brighter graticule has something to be bright
            # against.
            p.setBackground("#07090b")
            for ax in ("left", "bottom"):
                # This pen is the grid as well as the frame -- see GRID_PEN.
                p.getAxis(ax).setPen(pg.mkPen(GRID_PEN))
                p.getAxis(ax).setTextPen(pg.mkPen(AXIS_TEXT))

    # ------------------------------------------------------------- worker
    def _start(self) -> None:
        self.thread = QtCore.QThread(self)
        # Only a real radio can be reopened; the simulator never disappears.
        ch = getattr(self.src, "channel", 0)
        reopen = ((lambda: BladeRF(channel=ch))
                  if isinstance(self.src, BladeRF) else None)
        self.worker = Worker(self.src, self.cfg, reopen=reopen)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.ready.connect(self._on_result)
        self.worker.trigWait.connect(self._on_trig_wait)
        self.worker.stalled.connect(self._on_stalled)
        self.worker.failed.connect(lambda m: self._message(m, 4.0))
        self.worker.lost.connect(self._on_lost)
        self.worker.back.connect(self._on_back)
        self.worker.portChanged.connect(self._on_port)
        self.worker.iqDone.connect(self._iq_done)
        self.worker.healthReady.connect(self._on_health_result)
        self.worker.sweepDone.connect(self._on_sweep_done)
        self.thread.start()
        self._health_refresh()   # now that self.worker actually exists
        self._sg_start()
        self._sg_refresh_ranges(self.src)
        self._sg_refresh_support()

    def _stop(self) -> None:
        # Generator first: it holds the same device handle, and stopping the
        # sweep does not stop it.
        self._sg_stop()
        if getattr(self, "worker", None) is not None:
            self.worker.stop()
        # This guard never actually fires, and that is worth knowing rather
        # than tidying away: QObject ALREADY has a .thread attribute (the
        # built-in method returning the object's thread affinity), so
        # getattr(self, "thread", None) is never None even before _start()
        # has assigned a QThread over it. It is safe today only because
        # _start() runs in __init__, so by the time anything can call _stop()
        # the attribute is a real QThread. If a path ever reaches here first,
        # the failure is "'builtin_function_or_method' object has no
        # attribute 'quit'" -- confusing, and not what the guard promises.
        if isinstance(getattr(self, "thread", None), QtCore.QThread):
            self.thread.quit()
            self.thread.wait(3000)

    def _swap(self, new_src: Source) -> None:
        """Hand acquisition over to a different source, live."""
        self._stop()
        if self._lost_box is not None:
            box, self._lost_box = self._lost_box, None
            box.close()
        self.l_ovl.setText("")
        old = self.src
        if old is not new_src:
            try:
                old.close()       # release the USB handle before reopening
            except Exception:
                pass
        self.src = new_src
        # Gain and rate limits differ between the radio and the simulator, so
        # re-range the controls and pull the current gain into the new window.
        self._quiet = True
        self.s_gain.setRange(new_src.gain_min, new_src.gain_max)
        self.s_gain.setValue(float(np.clip(self.cfg.gain,
                                           new_src.gain_min, new_src.gain_max)))
        self._quiet = False
        self.cfg = replace(self.cfg, gain=self.s_gain.value())
        self._clamp_max_fs(new_src)
        self._sg_refresh_ranges(new_src)
        self.tdata = []
        self._wf_buf = None
        self._show_src()
        self._start()

    def _reconnect(self) -> None:
        """
        Look for the radio again, live. Handles the case where the board was
        plugged in after the app started.
        """
        self.b_conn.setEnabled(False)
        self._message("looking for the bladeRF ...")
        QtWidgets.QApplication.processEvents()

        # If a radio is already open we must stop acquisition and release the
        # USB handle before reopening, or the second open fails with "device
        # busy". Once released the old object is dead, so every failure path
        # below has to end somewhere valid rather than reusing it.
        if isinstance(self.src, BladeRF):
            self._stop()
            try:
                self.src.close()
            except Exception:
                pass

        try:
            radio = BladeRF()
        except RadioError as exc:
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("bladeRF not found")
            box.setIcon(QtWidgets.QMessageBox.Warning)
            box.setText("Could not open the bladeRF.\n\n"
                        "Press 'Show Details' for exactly why.")
            box.setDetailedText(str(exc))
            box.exec_()
            self._swap(Simulated())          # never leave a closed handle live
            self.b_conn.setEnabled(True)
            self._message("radio not found - running simulated")
            return

        self._swap(radio)
        self.b_conn.setEnabled(True)
        self._message(f"connected: {radio.info}")

    @QtCore.pyqtSlot(str)
    def _on_lost(self, why: str) -> None:
        """The board vanished mid-sweep."""
        self.l_src.setText("bladeRF DISCONNECTED\nwaiting for it to come back")
        self.l_src.setStyleSheet("color:#ff453a;font-weight:bold;")
        self.b_sim.setEnabled(True)
        self.l_ovl.setText("DISCONNECTED")
        # Unkey: the button must not still read "RF OUT is ON" when there is
        # no longer a radio to transmit with.
        if getattr(self, "b_sg_out", None) is not None and self.b_sg_out.isChecked():
            self.b_sg_out.setChecked(False)
        # Superseded by the disconnect message: a stall notice left up
        # beside it would describe a stream on a handle that is gone.
        self.l_stall.setText("")
        self.l_stall.setToolTip("")
        self._message("bladeRF disconnected - retrying automatically")

        if self._lost_box is None:
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("bladeRF disconnected")
            box.setIcon(QtWidgets.QMessageBox.Warning)
            box.setText("The bladeRF has disconnected.\n\n"
                        "Plug it back in and it will pick up again on its own "
                        "- this message will close itself.\n\n"
                        "The last trace is still on screen, but it is no "
                        "longer live.")
            box.setDetailedText(why)
            box.setStandardButtons(QtWidgets.QMessageBox.Ok)
            # Deliberately NOT modal: a modal dialog would block this thread,
            # so the automatic reconnect could not update the window until the
            # dialog was dismissed by hand.
            box.setWindowModality(QtCore.Qt.NonModal)
            box.finished.connect(self._lost_box_closed)
            self._lost_box = box
            box.show()

    def _lost_box_closed(self, _result: int) -> None:
        # Nothing else belongs here. This used to carry a copy of the tail of
        # __init__, so merely dismissing the "disconnected" popup threw away
        # the frozen reference trace, reset the capture folder, un-froze the
        # display, and set rx_ch back to 0 without actually switching the
        # port -- after which the auto-reconnect would come back on RX1 while
        # the panel still said RX2.
        self._lost_box = None

    @QtCore.pyqtSlot(object)
    def _on_back(self, src: Source) -> None:
        """Reopened by the worker -- adopt it without restarting the thread."""
        self.src = src
        if self._lost_box is not None:
            box, self._lost_box = self._lost_box, None
            box.close()
        self._quiet = True
        self.s_gain.setRange(src.gain_min, src.gain_max)
        self.s_gain.setValue(float(np.clip(self.cfg.gain,
                                           src.gain_min, src.gain_max)))
        self._clamp_max_fs(src)
        self._quiet = False
        self.cfg = replace(self.cfg, gain=self.s_gain.value())
        self.worker.apply(self.cfg)
        self.tdata = []
        self._wf_buf = None
        self.l_ovl.setText("")
        # The generator thread is still holding the handle that just died, so
        # it gets a fresh one -- restarted rather than handed over, because
        # swapping the source under a running thread is the same race the
        # health panel already learned about.
        self._sg_stop()
        self._sg_start()
        self._show_src()
        self._message(f"reconnected: {src.info}")

    def _use_sim(self) -> None:
        self._swap(Simulated())
        self._message("simulated source")

    def _push(self, **kw) -> None:
        if self._quiet:
            return
        self.cfg = replace(self.cfg, **kw)
        # The instrument no longer matches the slot it was recalled from.
        # _quiet is what keeps a RECALL from doing this to itself: every
        # setter _apply_setup() drives runs inside it, and _slot_recall()
        # sets the flag afterwards anyway, so the order cannot go wrong.
        self._slot_touched()
        if any(k in kw for k in ("window", "max_fs")):
            # What can be realised changed, so the menu has to change with it
            # or it would go back to offering RBWs the sweep cannot meet.
            self._fill_rbw()
            self._auto_label()
        if any(k in kw for k in ("span", "center", "rbw", "window", "detector",
                                 "max_fs", "zero_span", "points")):
            self._wf_buf = None
            # The measured quantity itself just changed, so anything already
            # accumulated describes a different measurement.
            self._meas_reset()
        self.worker.apply(self.cfg)

    # ----------------------------------------------------------- controls
    def _cen_span(self, _=None) -> None:
        c, s = self.e_cen.value(), max(self.e_span.value(), 1.0)
        self._quiet = True
        self.e_start.setValue(c - s / 2)
        self.e_stop.setValue(c + s / 2)
        self._quiet = False
        self._push(center=c, span=s)
        self._auto_label()
        self._axes()

    def _start_stop(self, _=None) -> None:
        a, b = self.e_start.value(), self.e_stop.value()
        if b <= a:
            b = a + 1e6
        c, s = (a + b) / 2, b - a
        self._quiet = True
        self.e_cen.setValue(c)
        self.e_span.setValue(s)
        self.e_stop.setValue(b)
        self._quiet = False
        self._push(center=c, span=s)
        self._auto_label()
        self._axes()

    def _preset(self, span: float) -> None:
        if span < 0:
            lo, hi = self.src.freq_min, self.src.freq_max
            c, s = (lo + hi) / 2, hi - lo
        else:
            c, s = self.e_cen.value(), span
        self._quiet = True
        self.e_cen.setValue(c)
        self.e_span.setValue(s)
        self.e_start.setValue(c - s / 2)
        self.e_stop.setValue(c + s / 2)
        self._quiet = False
        self._push(center=c, span=s)
        self._auto_label()
        self._axes()

    def _on_zoom(self, factor: float, at_hz: float) -> None:
        """Wheel: change span, keeping whatever is under the cursor put."""
        if self.cfg.zero_span:
            self.s_ztime.setValue(float(np.clip(
                self.s_ztime.value() * factor, self.s_ztime.minimum(),
                self.s_ztime.maximum())))
            return
        span = float(np.clip(self.cfg.span * factor, 1e3,
                             self.src.freq_max - self.src.freq_min))
        # Keep at_hz at the same fraction across the window so zooming feels
        # anchored rather than jumping back to the middle.
        frac = 0.5
        if self.cfg.span > 0:
            frac = float(np.clip((at_hz - self.cfg.start) / self.cfg.span,
                                 0.0, 1.0))
        centre = at_hz - (frac - 0.5) * span
        self._retune(centre, span)

    def _on_pan(self, dx_hz: float) -> None:
        if self.cfg.zero_span or dx_hz == 0.0:
            return
        self._retune(self.cfg.center + dx_hz, self.cfg.span)

    def _retune(self, centre: float, span: float) -> None:
        lo, hi = self.src.freq_min, self.src.freq_max
        span = float(np.clip(span, 1e3, hi - lo))
        centre = float(np.clip(centre, lo + span / 2, hi - span / 2))
        self._quiet = True
        self.e_cen.setValue(centre)
        self.e_span.setValue(span)
        self.e_start.setValue(centre - span / 2)
        self.e_stop.setValue(centre + span / 2)
        self._quiet = False
        self._push(center=centre, span=span)
        self._auto_label()
        self._axes()

    def _zero(self, on: bool) -> None:
        # Markers are anchored to a position on the X axis, and this control
        # changes what that axis IS -- frequency to time and back. An anchor
        # of 2.44e9 means nothing on a 200 us axis, and 2e-5 means nothing on
        # a frequency one, so carrying them across would leave every marker
        # parked at an edge with a readout that looks like a fault. Clearing
        # is the honest answer: the anchors did not survive, so do not
        # pretend they did.
        if bool(on) != bool(self.cfg.zero_span):
            for m in self.markers:
                m.on, m.i, m.f, m.p = False, -1, float("nan"), float("nan")
            self.c_mon.blockSignals(True)
            self.c_mon.setChecked(False)
            self.c_mon.blockSignals(False)
        self._push(zero_span=on)
        self.plot.getPlotItem().setAxisItems(
            {"bottom": (SecAxis if on else HzAxis)(orientation="bottom")})
        self.plot.getAxis("bottom").enableAutoSIPrefix(False)
        self.plot.setLabel("bottom", "Time" if on else "")
        self._theme()
        for w in (self.c_auto, self.cb_rbw, self.cb_win):
            w.setEnabled(not on)      # RBW is meaningless with the LO parked
        if not on:
            self.cb_rbw.setEnabled(not self.c_auto.isChecked())
        self.wf.setVisible(not on and self.c_wf.isChecked())
        # Zero span parks the LO and plots against TIME. There is one
        # time-domain capture and it comes from one port, so a second
        # FREQUENCY graticule has nothing to show. Coming back OUT of zero
        # span has to put it back if the port is still Both -- _sync_rx_buttons
        # is where that decision lives, so it is made in one place rather
        # than twice.
        self._sync_rx_buttons(self.rx_ch)
        # Triggering only means something with the LO parked -- leaving zero
        # span turns it back off rather than leaving it silently armed in
        # the background (Worker._sweep() disarms either way, this just
        # keeps the UI honest about it).
        self._sync_trigger_enabled()
        self._axes()

    def _sweep_auto(self, on: bool) -> None:
        self.s_swtime.setEnabled(not on)
        self._push(sweep_auto=bool(on))
        self._message("sweep time: auto" if on else
                      f"sweep time: {self.s_swtime.value():.1f} ms")

    def _ztime(self, us: float) -> None:
        self._push(zero_time=us * 1e-6)
        self._axes()

    # Measured on the board, and the reason these two controls lock each
    # other out: metadata mode is a property of the DEVICE, not of one
    # direction. Configuring the RX stream for metadata -- which is what a
    # triggered capture needs -- returns BLADERF_ERR_INVAL (-3) while the
    # transmitter is streaming in a plain format, the mirror image of the
    # already-known failure in the other direction. Rather than let the user
    # arm something that cannot work, the generator wins while it is keyed:
    # it is actively emitting, and quietly dropping a transmission would be
    # the worse surprise.
    _TRIG_SG_REASON = (
        "not while the generator is transmitting.\n\n"
        "A triggered capture needs the radio in metadata mode, and metadata "
        "mode is device-wide -- the radio refuses to enter it in one "
        "direction while the other is streaming normally (measured: error "
        "-3). Unkey RF OUT and the trigger becomes available again.")

    def _trig_locked_by_sg(self) -> bool:
        """
        Kept for the hardware trigger, which is what the metadata clash is
        about. The Video trigger is a decision taken in software about a
        sweep that has already been measured, so it is unaffected by
        anything the transmitter is doing.
        """
        return bool(getattr(self, "sg", None) is not None and self.sg.on)

    def _sync_trigger_enabled(self) -> None:
        """Nothing to keep in step -- see the body."""
        # Nothing to keep in step any more: the Video trigger works in
        # every span and is unaffected by the transmitter (see
        # _trig_locked_by_sg). Kept as a named no-op rather than deleted,
        # because the four callers are all places where "has anything
        # changed that the trigger cares about?" is the right question to
        # keep asking -- and if the hardware trigger ever gets a control
        # again, this is where its constraint goes back.
        return

    def _trig_changed(self, _i: int = 0) -> None:
        if getattr(self, "_quiet_trig", False):
            return
        mode = self.cb_trig_mode.currentData()
        video = mode == TRIG_VIDEO
        self.s_trig_level.setEnabled(video)
        self._push(trig_mode=mode)
        self.l_trig.setText(
            f"video, waiting for {self.s_trig_level.value():.0f} dBm"
            if video else "free run")
        self._message(f"trigger: {self.cb_trig_mode.currentText()}")

    # The decorator's types must MATCH the signal, or the connect in
    # _start() raises TypeError and the window never comes up. This said
    # (bool) until 2026-09-06 -- bug 49 -- and no software suite could see
    # it, because make_test_window stubs _start out (a QThread started under
    # the offscreen platform segfaults on this build). Same shape as bug 18:
    # every test passed and the actual application was broken.
    @QtCore.pyqtSlot(int, str)
    def _on_stalled(self, tries: int, why: str) -> None:
        """
        The worker is recovering the receive stream, or has finished.

        Edge-driven, and the label stays up for the whole of it: the trace
        is not advancing while this is on screen, and the one thing the user
        needs is to know that is being dealt with rather than wonder whether
        the program has died. The reason is kept in the tooltip rather than
        the label so the status bar does not grow a paragraph.
        """
        if tries <= 0:
            self.l_stall.setText("")
            self.l_stall.setToolTip("")
            return
        self.l_stall.setText(f"receive stalled - restarting the stream "
                             f"({tries})")
        self.l_stall.setToolTip(
            f"{why}\n\nThe board is still answering, so this is not a "
            f"disconnect: the receive stream stopped delivering and is "
            f"being restarted. After "
            f"{Worker.STREAM_RETRY_MAX} goes the device is reopened instead.")
        self._stall_seen += 1

    def _on_trig_wait(self, waiting: bool) -> None:
        """
        The worker says the Video trigger is holding, or has let go. Edge
        driven -- one signal per transition, not per rejected sweep.
        """
        if self.cb_trig_mode.currentData() != TRIG_VIDEO:
            return
        lvl = self.s_trig_level.value()
        self.l_trig.setText(f"video, waiting for {lvl:.0f} dBm" if waiting
                            else f"video, triggered at {lvl:.0f} dBm")
        self.l_trig.setStyleSheet("color:#ffb340;" if waiting
                                  else f"color:{A_MEASURE};")

    # What used to be here: arming the hardware trigger dropped the sweep
    # into Single, because a triggered capture is one acquisition and leaving
    # it free-running degraded every capture after the first (measured: 128
    # of 8000 samples, bug 21). That is still true of the hardware trigger,
    # and it is why nothing here does the same thing to the VIDEO trigger --
    # a video trigger has to keep sweeping to notice the crossing at all. It
    # is the display that waits, not the acquisition.
    #
    # _trig_fire() went with the Fire button. Worker.request_fire() is left
    # in place: it is the hardware trigger's, and that path is intact and
    # simply unarmed (see Config.trigger_role).

    def _fill_rbw(self) -> None:
        self.cb_rbw.blockSignals(True)
        self.cb_rbw.clear()
        self.cb_rbw.addItem("Auto")
        # Both arguments matter: the coarsest realisable RBW is
        # fs*ENBW/N_MIN, so it moves when either the rate ceiling or the
        # window shape changes. _push() refills on both for that reason.
        #
        # And the rate that matters is the one in force. While RF OUT is on
        # the generator pins it (5.5 fact 1), and it can be far below max_fs:
        # at a pinned 5 MS/s the coarsest reachable RBW is 179 kHz, so
        # offering 1 MHz was offering an 82% miss. Capping here is better
        # than warning afterwards.
        # And which FLOOR applies depends on whether the rate can move at
        # all. Pinned, the length is the only knob left and the coarse end
        # of the menu cannot be hit exactly -- see N_MIN_EXACT.
        fs_cap = self.cfg.max_fs
        n_min = N_MIN_FFT
        if self.cfg.fs_pin > 0.0:
            fs_cap = min(fs_cap, self.cfg.fs_pin)
            n_min = N_MIN_EXACT
        self._rbws = rbw_choices(fs_cap, self.cfg.window, n_min)
        for v in self._rbws:
            self.cb_rbw.addItem(fmt_hz_short(v))
        self.cb_rbw.blockSignals(False)
        # A pin is temporary; a manual RBW is not. Keying RF OUT on can drop
        # the ceiling far enough to move the setting (5 MS/s tops out at
        # 179 kHz), and unkeying it must put back what was asked for --
        # otherwise one press of a transmit button silently rewrites a
        # measurement setting for the rest of the session. Remembered as the
        # PAIR (wanted, what we clamped it to): if cfg.rbw is no longer the
        # value we forced, the user has since chosen something themselves and
        # restoring would overwrite a deliberate choice, so it stands down.
        top = self._rbws[-1] if self._rbws else 0.0
        wish, forced = getattr(self, "_rbw_wish", (0.0, 0.0))
        if (self._rbws and not self.cfg.rbw_auto
                and self.cfg.rbw > top * 1.001):
            old, new = self.cfg.rbw, top
            self._rbw_wish = (wish or old, new)
            self.cfg = replace(self.cfg, rbw=new)
            if getattr(self, "worker", None) is not None:
                self.worker.apply(self.cfg)
            # Kept as well as shown, because the usual way to get here is
            # keying RF OUT, and _sg_toggle_out() posts "RF OUT on" a moment
            # afterwards -- which would wipe the one line explaining why the
            # RBW just moved. It appends this instead.
            self._rbw_clamp_msg = (
                f"RBW {fmt_hz_short(old)} cannot be reached at this sample "
                f"rate -- now {fmt_hz_short(new)}")
            if getattr(self, "_msg_timer", None) is not None:
                self._message(self._rbw_clamp_msg, 8.0)
        elif wish and self._rbws and wish <= top * 1.001:
            self._rbw_wish = (0.0, 0.0)
            if abs(self.cfg.rbw - forced) < 1e-9 and not self.cfg.rbw_auto:
                self.cfg = replace(self.cfg, rbw=wish)
                if getattr(self, "worker", None) is not None:
                    self.worker.apply(self.cfg)
                if getattr(self, "_msg_timer", None) is not None:
                    self._message(
                        f"RBW back to the {fmt_hz_short(wish)} you asked for "
                        f"-- the sample rate that blocked it is gone", 8.0)

    def _auto_rbw(self, on: bool) -> None:
        self.cb_rbw.setEnabled(not on)
        self._push(rbw_auto=on)
        self._auto_label()

    def _pick_rbw(self, i: int) -> None:
        if i == 0:
            self.c_auto.setChecked(True)
        elif 1 <= i <= len(self._rbws):
            self._push(rbw=self._rbws[i - 1])

    def _auto_label(self) -> None:
        # Show "Auto" rather than a number: with Auto RBW the value follows
        # the span, and the status bar is the authoritative readout of what
        # was actually used.
        self.cb_rbw.blockSignals(True)
        if self.cfg.rbw_auto:
            self.cb_rbw.setCurrentIndex(0)
        elif self._rbws:
            best = min(range(len(self._rbws)),
                       key=lambda k: abs(self._rbws[k] - self.cfg.rbw))
            self.cb_rbw.setCurrentIndex(best + 1)
        self.cb_rbw.blockSignals(False)

    def _trace_mode(self, _i: int) -> None:
        self._push(trace_modes=tuple(cb.currentText() for cb in self.cb_tr))
        self._trace_view_enable()

    def _trace_view(self, _i: int) -> None:
        self._push(trace_view=tuple(c.isChecked() for c in self.c_tr_view))

    def _trace_view_enable(self) -> None:
        """A trace that is Off is not drawn, so there is nothing to freeze.
        Greying it says that, rather than leaving a tick that does nothing."""
        for cb, vw in zip(self.cb_tr, getattr(self, "c_tr_view", [])):
            vw.setEnabled(cb.currentText() != "Off")

    def _clear(self) -> None:
        self.tdata = []
        self.worker.clear_traces()
        self._wf_buf = None
        self._meas_reset()

    def _rx_hazard_dbm(self, r: Result) -> float:
        """
        The level arriving at the RX CONNECTOR, in dBm, or nan.

        This used to subtract cfg.ext_atten, because a displayed level was
        referred to the DUT and the connector sees the pad's OUTPUT. Ext
        atten was removed on 2026-08-31 (see _box_ampl), so the displayed
        level IS the connector level and there is nothing to work back
        through.

        Worth keeping the reasoning: if any "what is in front of the input"
        figure is ever added again, it has to come back OFF here. Getting
        that sign backwards would mean fitting an attenuator -- the one
        action that makes the input safe -- made the warning fire harder.

        cal_offset stays IN, because it is part of estimating the absolute
        level at all rather than something between the signal and the board.
        """
        pk = float(getattr(r, "peak_dbm", float("nan")))
        if not np.isfinite(pk):
            # Zero span and the very first frames have no interpolated peak.
            # The trace is still in dBm, so use it rather than skip the check.
            if r.power is None or not r.power.size:
                return float("nan")
            pk = float(np.max(r.power))
            if not np.isfinite(pk):
                return float("nan")
        return pk

    def _check_rx_hazard(self, r: Result) -> None:
        """
        Warn when the RX port is being fed more than it should be.

        Deliberately only a warning. Nothing here can protect the board --
        there is no attenuator to switch in (see _box_ampl) and dropping the
        RF gain moves a stage that is already downstream of the damage. The
        one useful thing the software can do is say so before the smoke, so
        that is all it does. Pretending otherwise would be worse than
        silence.

        Gated on real hardware: on the simulated scene there is no connector
        and no risk, and a popup there teaches people to dismiss this one.

        Gated on the loopback too, because through BLADERF_LB_RFIC_BIST the
        receiver is handed the transmitter's own baseband (5.5 fact 3) and
        the level on screen has nothing to do with what is on the SMA.
        """
        lvl = self._rx_hazard_dbm(r)
        verdict, self._haz_armed = rx_hazard(
            lvl, getattr(self, "_haz_armed", True),
            live=bool(getattr(self.src, "real_hardware", False)),
            loopback=bool(getattr(self, "_lb_on", False)))
        self.l_haz.setText(f"RX OVERPOWER {lvl:+.1f} dBm" if verdict else "")
        if verdict == "warn" and self._haz_box is None:
            self._haz_show(lvl)

    def _haz_show(self, lvl: float) -> None:
        """
        Raise the dialog. Split out ONLY so a test can replace it: see
        rx_hazard() for why nothing can call this under the test platform.
        Everything that decides anything is above; this just draws.
        """
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("RX overpower - damage hazard")
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setText(
            f"About {lvl:+.1f} dBm is arriving at {self.src.port}, past the "
            f"{RX_DAMAGE_DBM:.0f} dBm this input should see.\n\n"
            "Turn the source down or fit an attenuator. Reducing RF gain "
            "will NOT protect the input -- that control is inside the RFIC, "
            "downstream of the damage.\n\n"
            "This board has no mechanical attenuator, so a pad on the "
            "cable is the only protection there is.")
        box.setDetailedText(
            "How this number is worked out, and how far to trust it:\n\n"
            f"  peak of the sweep      {lvl:+.2f} dBm\n"
            f"  cal offset in use      {self.cfg.cal_offset:+.2f} dB\n"
            f"  board table            "
            f"{float(rx_table_db(self.cfg.center)):+.2f} dB\n"
            f"  RF gain                {self.cfg.gain:.1f} dB\n\n"
            "Absolute dBm on this instrument is an ESTIMATE unless you have "
            "calibrated it against a known source -- it depends on the board "
            "and the cabling. Treat this as an early warning, not as a "
            "measurement: if it fires, the input is somewhere near its "
            "limit even if the exact figure is a decibel or two out.\n\n"
            "It will not appear again until the level has dropped at least "
            f"{RX_DAMAGE_CLEAR_DB:.0f} dB below the limit.")
        box.setStandardButtons(QtWidgets.QMessageBox.Ok)
        # Non-modal, for the same reason the disconnect popup is: a modal
        # dialog blocks this thread, so the display would stop updating at
        # exactly the moment somebody is turning their source down and
        # wanting to watch the level fall.
        box.setWindowModality(QtCore.Qt.NonModal)
        box.finished.connect(self._haz_box_closed)
        self._haz_box = box
        box.show()
        self._message(f"RX overpower: {lvl:+.1f} dBm at {self.src.port}", 10.0)
        return

    def _haz_box_closed(self, _result: int) -> None:
        self._haz_box = None

    def _autoscale(self) -> None:
        if self.last is not None and self.last.power.size:
            pk = float(np.max(self.last.power))
            self.s_ref.setValue(float(np.ceil((pk + 8) / 5) * 5))

    def _clamp_max_fs(self, src) -> None:
        """
        Keep cfg.max_fs inside what this source can actually do.

        Used to be the Max IBW spin box's setRange, which did this as a side
        effect of being on screen. The control is gone (on request) and the
        clamp is not optional: a config asking for a rate above the board's
        ceiling gets silently reduced by the driver, and then every RBW and
        every span in the sweep is computed against a rate the hardware is
        not running -- bug 27's shape.
        """
        lo, hi = float(getattr(src, "fs_min", 0.52e6)), float(
            getattr(src, "fs_max", 61.44e6))
        want = float(np.clip(self.cfg.max_fs, lo, hi))
        if want != self.cfg.max_fs:
            self.cfg = replace(self.cfg, max_fs=want)

    def _show_wf(self, on: bool) -> None:
        self.wf.setVisible(on and not self.cfg.zero_span)

    def _set_depth(self, d: int) -> None:
        self._wf_depth = int(d)
        self._wf_buf = None

    # ------------------------------------------------------------ markers
    def _cur(self) -> Marker:
        return self.markers[self.cb_m.currentIndex()]

    def _index_at(self, x: float) -> int:
        """
        Nearest display point to a position on the X axis, or -1.

        Result.freqs carries whatever the axis currently IS -- hertz in a
        swept view, seconds in zero span -- so one lookup serves both, and
        the marker and the cursor readout cannot disagree about which point
        a position lands on.

        -1 means x is outside the swept range, which callers must treat as
        a legitimate state rather than as a fault: narrow the span past a
        marker and this is what it returns.

        The tolerance is half a display point at each end. Without it a
        marker placed exactly on the last point of a sweep would drop out
        on the next frame, because the stitched frequency axis moves by a
        few hertz between passes.
        """
        if self.last is None or not np.isfinite(x):
            return -1
        f = self.last.freqs
        if f.size == 0:
            return -1
        i = int(np.argmin(np.abs(f - x)))
        if f.size > 1:
            step = abs(float(f[-1]) - float(f[0])) / (f.size - 1)
        else:
            step = 0.0
        if abs(float(f[i]) - x) > max(step, 0.0) * 0.5 + 1e-6:
            # Genuinely off the edge of the sweep, not just off-grid.
            lo = min(float(f[0]), float(f[-1]))
            hi = max(float(f[0]), float(f[-1]))
            if x < lo or x > hi:
                return -1
        return i

    def _m_index(self, m: Marker) -> int:
        """
        Nearest display point to a marker's own frequency, or -1.

        This is the replacement for storing the index: the anchor is the
        frequency and the index is worked out against whatever sweep is
        on screen right now. See _index_at for what -1 means and why it
        is not a fault.
        """
        return self._index_at(m.f)

    def _sync_marker(self) -> None:
        m = self._cur()
        for w, v in ((self.c_mon, m.on), (self.c_mdelta, m.delta)):
            w.blockSignals(True)
            w.setChecked(v)
            w.blockSignals(False)
        self._sync_mfreq()

    def _sync_mfreq(self) -> None:
        """
        Show the selected marker's own frequency in the At box.

        HzEdit.setValue() does not emit changed (see _commit), so this
        cannot loop back into _marker_to_typed -- the same property 5.19
        relied on for the channel centre, and the reason "has the user
        typed here" is not observable. The box is a readout that happens
        to be editable.
        """
        if not hasattr(self, "e_mfreq"):
            return                     # called during _build, before the box
        m = self._cur()
        if np.isfinite(m.f) and not self.cfg.zero_span:
            self.e_mfreq.setValue(float(m.f))

    def _marker_to_typed(self) -> None:
        """Anchor the selected marker on the frequency in the At box."""
        if self.cfg.zero_span:
            self._message("markers are placed by frequency -- leave zero "
                          "span first")
            return
        hz = float(self.e_mfreq.value())
        if not np.isfinite(hz) or hz <= 0.0:
            self._message("enter a frequency, e.g. 2.44G")
            return
        m = self._cur()
        m.f, m.on, m.i = hz, True, -1
        self.c_mon.blockSignals(True)
        self.c_mon.setChecked(True)
        self.c_mon.blockSignals(False)
        self._draw_markers()
        i = self.cb_m.currentIndex() + 1
        if m.i < 0:
            self._message(f"M{i} set to {fmt_hz_short(hz)} -- outside this "
                          "span, so it is waiting off screen")
        else:
            self._message(f"M{i} at {fmt_hz_short(hz)}")

    def _marker_at_hz(self, hz: float) -> None:
        """
        Put the selected marker on a frequency the mouse pointed at.

        Called for every mouse-move of a marker drag, so it does the least it
        can: it does NOT print a status message (a message per pixel would
        wipe anything else out) and it does not touch the anchor when the
        gesture makes no sense. It DOES keep the At box in step, because that
        box is meant to read back where the marker is.
        """
        if self.cfg.zero_span or not np.isfinite(hz):
            return
        m = self._cur()
        m.f, m.on = float(hz), True
        if not self.c_mon.isChecked():
            self.c_mon.blockSignals(True)
            self.c_mon.setChecked(True)
            self.c_mon.blockSignals(False)
        self._sync_mfreq()
        self._draw_markers()

    def _marker_mouse(self, on: bool) -> None:
        """The 'Move with mouse' tick. Ctrl inverts it either way round."""
        self.plot.marker_mouse = bool(on)
        self._message("mouse moves the marker -- Ctrl-drag pans" if on
                      else "mouse pans and zooms -- Ctrl-drag moves the "
                           "marker")

    def _marker_on(self, on: bool) -> None:
        m = self._cur()
        m.on = on
        if on and not np.isfinite(m.f) and self.last is not None:
            # Never placed: put it somewhere useful rather than nowhere.
            self._peak()
        self._draw_markers()

    def _marker_delta(self, on: bool) -> None:
        self._cur().delta = on
        self._draw_markers()

    def _ref_trace(self) -> Optional[np.ndarray]:
        """Markers read the first live trace, else the raw sweep."""
        for i, cb in enumerate(self.cb_tr):
            if cb.currentText() != "Off":
                d = self.tdata[i] if i < len(self.tdata) else None
                if d is not None:
                    return d
        return self.last.power if self.last else None

    def _displayed(self) -> Optional[np.ndarray]:
        """
        The series the user is actually looking at -- after the reference has
        been subtracted, if that mode is on. Markers, the peak table and the
        limit check all read this so they agree with the picture.
        """
        base = self._ref_trace()
        if base is None or self.last is None:
            return None
        if self._diff and self._ref_usable() and base.size == self.ref.size:
            return base - self.ref
        return base

    def _peak(self) -> None:
        d = self._displayed()
        if self.last is None or d is None:
            return
        m = self._cur()
        m.f, m.on = float(self.last.freqs[int(np.argmax(d))]), True
        self._peak_i = 0
        self._sync_mfreq()
        self.c_mon.setChecked(True)
        self._draw_markers()

    def _next_peak(self) -> None:
        d = self._displayed()
        if self.last is None or d is None or d.size < 3:
            return
        inner = np.arange(1, d.size - 1)
        loc = inner[(d[inner] >= d[inner - 1]) & (d[inner] >= d[inner + 1])]
        if loc.size == 0:
            return
        floor = float(np.median(d))
        strong = loc[d[loc] > floor + 6.0]
        if strong.size == 0:
            strong = loc
        order = strong[np.argsort(d[strong])[::-1]][:20]
        self._peak_i = (self._peak_i + 1) % len(order)
        m = self._cur()
        m.f, m.on = float(self.last.freqs[int(order[self._peak_i])]), True
        self._sync_mfreq()
        self.c_mon.setChecked(True)
        self._draw_markers()

    def _marker_at_center(self) -> None:
        """
        Put the selected marker on the centre frequency.

        The other half of the pair "To center" was already: that one moves
        the radio onto the marker, this one moves the marker onto the radio.
        Asked for on 2026-09-18, and it is one press of what typing the
        centre into the At box already did.

        It is a PLACEMENT, not a link: the anchor is a frequency (bug 38),
        so retuning afterwards leaves the marker where it was put rather
        than dragging it along with the centre. That is the whole reason
        the button is worth having -- park a marker on the carrier, then
        look around it without losing the reading.
        """
        if self.cfg.zero_span:
            # Same refusal as a typed frequency: the X axis is TIME here, so
            # a frequency anchor means nothing on it.
            self._message("markers are placed by frequency -- leave zero "
                          "span first")
            return
        hz = float(self.cfg.center)
        m = self._cur()
        m.f, m.on, m.i = hz, True, -1
        self.c_mon.blockSignals(True)
        self.c_mon.setChecked(True)
        self.c_mon.blockSignals(False)
        # The At box is meant to read back where the marker IS, so it moves
        # with it. setValue does not emit changed (HzEdit._commit), so this
        # cannot loop back round through _marker_to_typed.
        self._sync_mfreq()
        self._draw_markers()
        i = self.cb_m.currentIndex() + 1
        self._message(f"M{i} at the centre, {fmt_hz_short(hz)}")

    def _to_center(self) -> None:
        m = self._cur()
        if not m.on or self.cfg.zero_span:
            return
        self._quiet = True
        self.e_cen.setValue(m.f)
        self.e_start.setValue(m.f - self.cfg.span / 2)
        self.e_stop.setValue(m.f + self.cfg.span / 2)
        self._quiet = False
        self._push(center=m.f)
        self._axes()

    def _tune_strongest(self) -> None:
        """Centre on whatever is loudest right now -- quick way to grab a DUT."""
        if self.last is None or self.cfg.zero_span:
            return
        hz = self.last.peak_hz
        if not np.isfinite(hz):
            d = self._displayed()
            if d is None:
                return
            hz = float(self.last.freqs[int(np.argmax(d))])
        hz = float(hz)
        # A wide span cannot be centred near the band edge -- 1.8 GHz around
        # 434 MHz would start below the radio's minimum, and _retune would
        # quietly slide the centre up instead, losing the very peak we came
        # for. Narrow the span just enough to keep the peak in the middle.
        lo, hi = self.src.freq_min, self.src.freq_max
        span = float(self.cfg.span)
        room = 2.0 * min(hz - lo, hi - hz)
        narrowed = room < span - 1.0
        if narrowed:
            span = max(room, 1e3)
        self._retune(hz, span)
        msg = f"centred on {fmt_hz_short(hz)}"
        if narrowed:
            msg += f"  (span narrowed to {fmt_hz_short(span)} -- band edge)"
        self._message(msg)

    def _all_off(self) -> None:
        for m in self.markers:
            m.on, m.i, m.f = False, -1, float("nan")
        self.c_mon.setChecked(False)
        self._draw_markers()

    def _label_anchor(self, x: float, y: float) -> Tuple[float, float]:
        """Pull a marker readout back inside the plot near the edges."""
        (x0, x1), (y0, y1) = self.plot.viewRange()
        fx = (x - x0) / max(x1 - x0, 1e-12)
        fy = (y - y0) / max(y1 - y0, 1e-12)
        ax = 0.0 if fx < 0.16 else (1.0 if fx > 0.84 else 0.5)
        return (ax, -0.35 if fy > 0.90 else 1.3)

    def _draw_markers(self) -> None:
        if self.last is None:
            return
        d = self._displayed()
        xs, ys = [], []
        sel = self.cb_m.currentIndex()
        m1 = self.markers[0]
        z = self.cfg.zero_span
        unit = "dB" if self._diff else "dBm"
        for i, m in enumerate(self.markers):
            t = self.mtext[i]
            # The index is DERIVED from the anchor every frame -- that is
            # what stops a marker sliding when the view moves. It comes back
            # as -1 when the anchor is outside the sweep, which is a normal
            # state (narrow the span past a marker and it waits off screen),
            # so the readout goes blank and the anchor is left alone.
            m.i = self._m_index(m)
            if not m.on or d is None or not (0 <= m.i < d.size):
                m.p = float("nan")
                t.setVisible(False)
                continue
            m.p = float(d[m.i])
            xs.append(m.f)
            ys.append(m.p)
            # Only the selected marker gets a full readout; four full labels
            # pile into an unreadable heap and the table has every value.
            if i == sel:
                if m.delta and i > 0 and m1.on and np.isfinite(m1.p):
                    df, dp = m.f - m1.f, m.p - m1.p
                    lab = (f"\u0394{i+1}  {df*1e6:.3g} us  {dp:+.2f} dB" if z
                           else f"\u0394{i+1}  {fmt_hz_short(df)}  {dp:+.2f} dB")
                else:
                    lab = (f"M{i+1}  {m.f*1e6:.3g} us  {m.p:.2f} {unit}" if z
                           else f"M{i+1}  {fmt_hz_short(m.f)}  {m.p:.2f} {unit}")
            else:
                lab = f"M{i+1}"
            t.setText(lab)
            # Keep the label inside the graticule. A marker sitting on the
            # first or last division used to have its readout half off screen
            # -- exactly where you park it when hunting a band edge.
            t.setAnchor(self._label_anchor(m.f, m.p))
            t.setPos(m.f, m.p)
            t.setVisible(True)
        self.dots.setData(x=xs, y=ys)

        rows = [(i, m) for i, m in enumerate(self.markers) if m.on]
        self.tbl.setRowCount(len(rows))
        for r, (i, m) in enumerate(rows):
            ftxt = ("--" if not np.isfinite(m.f)
                    else (f"{m.f*1e6:.3f} us" if z else fmt_hz(m.f, 4)))
            # A marker whose anchor is outside the sweep says so. Showing
            # the level it happened to read before the span moved would be
            # a stale number presented as a live one.
            ltxt = ("off screen" if (np.isfinite(m.f) and m.i < 0)
                    else ("--" if not np.isfinite(m.p)
                          else f"{m.p:.2f} {unit}"))
            for c, v in enumerate([f"M{i+1}", ftxt, ltxt]):
                it = QtWidgets.QTableWidgetItem(v)
                if c:
                    it.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self.tbl.setItem(r, c, it)

    # ------------------------------------------------------------- render
    def _axes(self) -> None:
        ref, div = self.s_ref.value(), self.s_div.value()
        if self._diff:
            # A difference is centred on zero, not hanging below a reference
            # level, so give it a symmetric window.
            half = N_DIV * div / 2.0
            lo, hi = -half, half
        else:
            lo, hi = ref - N_DIV * div, ref
        self.plot.setYRange(lo, hi, padding=0)
        if self.cfg.zero_span:
            self.plot.setXRange(0, self.cfg.zero_time, padding=0)
            self.cursor.setPos(0, ref)
        else:
            self.plot.setXRange(self.cfg.start, self.cfg.stop, padding=0)
            self.cursor.setPos(self.cfg.start, ref)
        # The RX2 graticule gets the SAME scales, from the same two controls.
        # That is what "the regular configurations apply to both screens"
        # means in practice: there is one ref level, one dB/div and one span
        # in the whole instrument, and both halves are drawn against them --
        # so a level read off the lower plot is directly comparable with one
        # read off the upper, which is the only reason to show them together.
        # Set here rather than by an axis link, so the sweep stays the single
        # source of truth for what the view is showing.
        if not self.cfg.zero_span:
            self.plot2.setYRange(lo, hi, padding=0)
            self.plot2.setXRange(self.cfg.start, self.cfg.stop, padding=0)

    def _push_wf(self, row: np.ndarray, f0: float, f1: float) -> None:
        ref = self.s_ref.value()
        vmin = ref - N_DIV * self.s_div.value()
        n = row.size
        d = self._wf_depth
        if self._wf_buf is None or self._wf_buf.shape != (n, 2 * d):
            # Frequency down the rows, time across the columns -- the shape
            # the image is drawn in, so no transpose is needed per frame.
            #
            # Twice the depth in time, with every sweep written into both
            # halves. The newest d columns are then always one slice, which is
            # what keeps this from concatenating the whole buffer (about
            # 1.3 MB) on every single frame.
            #
            # It is a COLUMN slice now, which is not contiguous, so pyqtgraph
            # copies it while building the QImage. Measured end to end at
            # 1409 bins x 240 rows, push plus a forced render: 0.97 ms per
            # frame against 0.71 ms for the old row-sliced waterfall. A
            # quarter of a millisecond of a 21 ms frame, for the axes being
            # the way round they were asked for. (setImage() defers the
            # conversion to paint time, so timing it without render() shows
            # 0.075 ms and means nothing.)
            self._wf_buf = np.full((n, 2 * d), vmin, dtype=np.float32)
            self._wf_head = 0
        self._wf_buf[:, self._wf_head] = row
        self._wf_buf[:, self._wf_head + d] = row
        self._wf_head = (self._wf_head + 1) % d
        ordered = self._wf_buf[:, self._wf_head:self._wf_head + d]
        # row-major: first index is y (frequency, f0 at the bottom), second is
        # x (time, oldest at the left), so the newest sweep is the right edge.
        self.img.setImage(ordered, levels=(vmin, ref), autoLevels=False)
        self.img.setRect(QtCore.QRectF(0.0, f0, float(self._wf_depth),
                                       max(f1 - f0, 1.0)))
        self.wf.setXRange(0, self._wf_depth, padding=0)
        self.wf.setYRange(f0, f1, padding=0)

    @QtCore.pyqtSlot(object)
    def _on_result(self, r: Result) -> None:
        # Ahead of the freeze check on purpose: Freeze holds the DISPLAY,
        # and the recorder is recording the instrument. Somebody who
        # freezes the picture to point something out mid-survey must not
        # silently lose those minutes.
        self._rec_tick(r)
        if self._mrun is not None:
            # Counted ahead of the freeze too: a macro waiting N sweeps is
            # waiting for the INSTRUMENT. The first result after a settings
            # step may have been swept on the old settings, so it is dropped.
            if self._mrun["stale"] > 0:
                self._mrun["stale"] -= 1
            else:
                self._mrun["seen"] += 1
                self._mrun["fresh"] += 1
                self._mrun["t_last"] = time.monotonic()
        if self._frozen:
            # Keep sweeping in the background so resuming is instant; just do
            # not disturb what is on screen.
            return
        # Identifies this sweep so the Avg|Hold accumulators fold it in
        # exactly once, however many times _refresh_meas() gets called.
        self._frame_seq = getattr(self, "_frame_seq", 0) + 1
        t_draw = time.perf_counter()
        self.last = r
        self.tdata = list(r.traces)
        diff_ok = self._diff and self._ref_usable()
        for i, cb in enumerate(self.cb_tr):
            d = r.traces[i] if i < len(r.traces) else None
            if d is None or cb.currentText() == "Off" or d.size != r.freqs.size:
                self.curves[i].setVisible(False)
                continue
            self.curves[i].setData(r.freqs,
                                   d - self.ref if diff_ok else d)
            self.curves[i].setVisible(True)

        self._draw_rx2(r, diff_ok)

        if (self.wf.isVisible() and not self.cfg.zero_span
                and r.wf_row is not None and r.freqs.size > 1):
            # Always absolute power here -- the spectrogram is a history of
            # what was there, not of how it differs from a reference.
            self._push_wf(r.wf_row, float(r.freqs[0]), float(r.freqs[-1]))

        self._draw_markers()
        self._cursor_text()
        self._refresh_ref_curve()
        self._refresh_limit()
        self._refresh_meas()
        self._refresh_counter()
        self._refresh_classify()
        # r.rbw carries the sample rate in zero span, which is what sets the
        # time resolution and the bandwidth being watched -- worth showing.
        if self.cfg.zero_span:
            self.l_rbw.setText(f"{fmt_hz_short(r.rbw)} sample rate  "
                               f"({1e6 / max(r.rbw, 1):.3g} us/pt)")
            self.l_rbw.setStyleSheet("")
            self.l_rbw.setToolTip("Sample rate, which is what sets the time "
                                  "resolution in zero span.")
        else:
            # Realised, always -- and when it could not be met, the request
            # alongside it and in amber. Showing one of the two on its own is
            # what made this readout look false: the setting said 100 kHz and
            # the bar said 19.6 kHz, and neither admitted the other existed.
            miss = (r.rbw_req > 0.0
                    and abs(r.rbw - r.rbw_req) > 0.01 * r.rbw_req)
            self.l_rbw.setText(
                f"RBW {fmt_hz_short(r.rbw)}"
                + (f"  (set {fmt_hz_short(r.rbw_req)})" if miss else ""))
            self.l_rbw.setStyleSheet("color:#ffb340;font-weight:bold;"
                                     if miss else "")
            self.l_rbw.setToolTip(
                "The requested RBW cannot be delivered at this span and\n"
                "sample rate -- the window length ran into a limit. The\n"
                "figure shown first is what the sweep actually measured\n"
                "with, and it is the one every measurement uses."
                if miss else
                "Resolution bandwidth the sweep actually measured with.")
        seg_txt = f"FFT {r.nfft}   Seg {r.segments}"
        if r.nfft_pad and r.nfft_pad != r.nfft:
            # Window length -> transform length. Padding buys bins, not
            # resolution, and showing both stops "FFT 64" looking broken.
            seg_txt = f"FFT {r.nfft}->{r.nfft_pad}   Seg {r.segments}"
        if self.cfg.quick_tune and r.qt_attempted:
            seg_txt += f"   QT {r.qt_hits}/{r.qt_attempted}"
        self.l_seg.setText(seg_txt)
        self.l_rate.setText(f"{r.rate:.0f} sweep/s")
        if getattr(self, "l_swmeas", None) is not None and r.rate > 0:
            ms = 1000.0 / r.rate
            self.l_swmeas.setText(f"{ms:,.1f} ms" if ms < 10000
                                  else f"{ms / 1000:,.1f} s")
        # Centre and span across the top. With the settings behind soft keys
        # there is no longer a Frequency box permanently on screen showing
        # what is tuned, and "what am I looking at" is the one thing that
        # must not need a key press to answer.
        if getattr(self, "l_tuned", None) is not None:
            zs = bool(self.cfg.zero_span)
            self.l_tuned.setText(f"Center {fmt_hz(self.cfg.center)}")
            # In zero span the LO is parked and RBW means nothing, so the
            # middle readout says what the sweep is actually bounded by --
            # the same substitution the status bar has always made.
            self.l_rbw_rd.setText(
                f"Time span {self.cfg.zero_time * 1e6:.0f} us" if zs
                else f"RBW {fmt_hz_short(r.rbw)}")
            self.l_span_rd.setText("Zero span" if zs
                                   else f"Span {fmt_hz(self.cfg.span)}")
        self.l_ovl.setText("INPUT OVERLOAD" if r.overload else "")
        # Measured 2026-09-16: at or below FS_MIN_LINEAR the AD9361 adds
        # about 11 dB the amplitude model does not know about. Say it rather
        # than quietly showing a wrong level -- and name the cause, since the
        # only route here is the generator owning the sample rate.
        self.l_fslow.setText(
            "LEVELS ~11 dB HIGH: rate pinned below "
            f"{FS_MIN_LINEAR / 1e6:.3f} MS/s" if r.fs_low else "")
        self.l_calx.setText(
            f"outside the calibrated band ({fmt_hz(RX_CAL_LO_HZ)} to "
            f"{fmt_hz(RX_CAL_HI_HZ)}) the board table is held flat"
            if r.cal_extrap else "")
        self._check_rx_hazard(r)
        # Only ever true if the hardware trigger is armed, and no control
        # arms it now (see Config.trigger_role). Kept because the readout is
        # correct and measured; it must not fight the Video trigger's own
        # status, which is why trigger_role is part of the condition.
        if self.cfg.zero_span and self.cfg.trigger_role != TRIG_ROLE_DISABLED \
                and r.timestamp is not None:
            miniexp = ", ".join(n for n, v in
                                (("mini_exp1", r.miniexp1), ("mini_exp2", r.miniexp2)) if v)
            # A short block is normal for the first capture or two after a
            # trigger fires, while the sample stream resyncs -- measured
            # 7748 then 508 of 8000, then full blocks from there. Saying how
            # many samples actually arrived is useful; shouting "OVERRUN" at
            # something self-correcting is not.
            short = (r.n_requested and r.n_actual
                     and r.n_actual < r.n_requested)
            bits = [f"fired -- ts {r.timestamp:,}"]
            if short:
                bits.append(f"short {r.n_actual:,}/{r.n_requested:,} samples "
                            f"({r.n_actual / max(r.rbw, 1) * 1e3:.3f} ms)")
            if miniexp:
                bits.append(f"{miniexp} asserted")
            self.l_trig.setText("  |  ".join(bits))
        mode = ("time domain" if self.cfg.zero_span else
                "real time" if r.segments == 1 else "stepped sweep")
        self.l_mode.setText(f"{self.src.info}   |   {mode}   |   "
                            f"{r.folded} sweep/frame")
        # Rolling average of how long a redraw takes, so if the display is
        # struggling it is obvious which way to turn the knobs.
        ms = (time.perf_counter() - t_draw) * 1e3
        self._draw_ms = ms if self._draw_ms is None else \
            0.85 * self._draw_ms + 0.15 * ms
        self.l_perf.setText(f"draw {self._draw_ms:.1f} ms/frame")

    def _box_classify(self):
        """
        Name the carrier under the cursor: technology, channel width, edges
        and which band it lands in.
        """
        b = Box("Identify Signal", A_MEASURE)
        self.c_classify = QtWidgets.QCheckBox("Identify what is under the cursor")
        self.c_classify.setToolTip(
            "Hover the plot and this names the carrier you are pointing at:\n"
            "its measured start and stop, the standard channel width that\n"
            "best fits, and the band it falls in.\n\n"
            "It is an inference from bandwidth and band plan -- nothing here\n"
            "demodulates anything, so it cannot tell LTE from 5G NR of the\n"
            "same width in the same band, and says so when that happens.")
        self.c_classify.toggled.connect(self._toggle_classify)
        b.full(self.c_classify)

        self.l_cls_tech = QtWidgets.QLabel("--")
        self.l_cls_tech.setStyleSheet(f"color:{A_MEASURE};font-weight:bold;")
        b.row("Best match", self.l_cls_tech)
        self.l_cls_edges = QtWidgets.QLabel("--")
        b.row("Start / stop", self.l_cls_edges)
        self.l_cls_bw = QtWidgets.QLabel("--")
        b.row("Occupied", self.l_cls_bw)
        self.l_cls_band = QtWidgets.QLabel("--")
        b.row("Band", self.l_cls_band)
        # How far the plateau stands above the floor. Weak carriers are found
        # now (from ~3 dB), and a width read off one of those deserves less
        # trust than one read off a carrier 30 dB up -- so it says which.
        self.l_cls_rise = QtWidgets.QLabel("--")
        b.row("Above floor", self.l_cls_rise)

        self.l_cls_note = QtWidgets.QLabel(
            "A guess from width and frequency, not a decode. LTE and NR of "
            "the same width are indistinguishable this way.")
        self.l_cls_note.setWordWrap(True)
        self.l_cls_note.setStyleSheet("color:#ffd24a;")
        self.l_cls_note.setVisible(False)
        b.full(self.l_cls_note)
        return b

    def _toggle_classify(self, on: bool) -> None:
        self.l_cls_note.setVisible(bool(on))
        if not on:
            self._show_classify(None)
            self._message("signal identification off")
        else:
            self._message("hover the plot to identify a carrier")

    def _show_classify(self, info) -> None:
        if not hasattr(self, "l_cls_tech"):
            return
        if info is None:
            for lab in (self.l_cls_tech, self.l_cls_edges,
                        self.l_cls_bw, self.l_cls_band, self.l_cls_rise):
                lab.setText("--")
            self.cls_band.setVisible(False)
            self.cls_text.setVisible(False)
            return

        # Shade the measured extent of the carrier and label it in place.
        self.cls_band.setRegion([info["start"], info["stop"]])
        self.cls_band.setVisible(True)
        head = info["tech"] or "unrecognised width"
        if info["channel"]:
            head += f"  {fmt_hz_short(info['channel'])} channel"
        lines = [head,
                 f"{info['start']/1e6:.3f} - {info['stop']/1e6:.3f} MHz"
                 f"   ({fmt_hz_short(info['occupied'])} occupied)"]
        if info["band"]:
            lines.append(info["band"])
        self.cls_text.setText("\n".join(lines))
        (x0, x1), (y0, y1) = self.plot.viewRange()
        # Centre the label on the band, but swing the anchor at the edges so
        # a wide label is not half off screen -- the same fix the marker
        # readouts needed (see _label_anchor), and for the same reason: a
        # carrier parked near the edge of the span is exactly where you go
        # looking for it.
        mid = float(np.clip(info["centre"], x0, x1))
        frac = (mid - x0) / max(x1 - x0, 1e-12)
        ax = 0.0 if frac < 0.25 else (1.0 if frac > 0.75 else 0.5)
        self.cls_text.setAnchor((ax, 0.0))
        # Hangs below the top of the view rather than off it -- see
        # CLS_LABEL_DROP. Positioned in view coordinates and rewritten every
        # frame, so it follows the ref level and dB/div rather than being
        # left behind by them.
        self.cls_text.setPos(mid, y1 - (y1 - y0) * CLS_LABEL_DROP)
        self.cls_text.setVisible(True)
        self.l_cls_tech.setText(info["tech"] or "unrecognised width")
        self.l_cls_edges.setText(
            f"{info['start']/1e6:.3f} - {info['stop']/1e6:.3f} MHz")
        occ = fmt_hz_short(info["occupied"])
        self.l_cls_bw.setText(
            f"{occ}" + (f"  (in a {fmt_hz_short(info['channel'])} channel)"
                        if info["channel"] else ""))
        self.l_cls_band.setText(
            (info["band"] + (f" -- {info['note']}" if info["note"] else ""))
            if info["band"] else "not in the known band plan")
        rise = info.get("rise_db")
        self.l_cls_rise.setText("--" if rise is None else
                                f"{rise:.1f} dB" + ("  (weak)" if rise < 6.0
                                                     else ""))

    # -- signal under the cursor -----------------------------------------
    def _classify_trace(self) -> Optional[np.ndarray]:
        """
        Which trace to measure a channel's width from: a Max Hold one if
        there is any, otherwise whatever is on screen.

        This matters more than it looks. A real LTE downlink is bursty -- a
        lightly loaded cell only transmits its control region and reference
        signals, so ONE sweep shows a carrier much narrower than its channel
        and the width match wanders with the traffic. Measured on the live
        700 MHz band, single sweeps of the same carrier gave 1.7 MHz, then
        8.9 MHz, then 11.8 MHz. Max Hold fills the channel in over a few
        sweeps and settles it. Trace 2 is Max Hold out of the box, so this
        usually just works without the user knowing why.
        """
        if self.last is None:
            return None
        want = self.last.freqs.size
        for i, cb in enumerate(self.cb_tr):
            if cb.currentText() == "Max Hold" and i < len(self.tdata):
                d = self.tdata[i]
                if d is not None and d.size == want:
                    return d
        return self._displayed()

    def _signal_edges(self, hz: float):
        """
        The edges of whatever carrier sits under hz: (lo, hi, level_dbm,
        rise_db), or None. The work is find_carrier(), which is pure and
        says how it finds weak carriers; this only picks the trace.

        The rule it inherited, and keeps: a modulated block's peak bin is an
        outlier and single bins fade far below it, so a threshold at the
        peak minus 10 dB measured 0.12 MHz for an 18 MHz carrier. The edge is
        half the plateau height in dB, on a smoothed trace, with fades walked
        through.
        """
        if self.last is None or self.cfg.zero_span:
            return None
        d = self._classify_trace()
        f = self.last.freqs
        if d is None or d.size < 16 or d.size != f.size:
            return None
        got = find_carrier(f, d, hz,
                           stitched=int(getattr(self.last, "segments", 1)) > 1)
        if got is None:
            return None
        return got["lo"], got["hi"], got["level"], got["rise"]

    def _classify_at(self, hz: float):
        """(summary, detail_lines) for the signal under hz, or None."""
        edges = self._signal_edges(hz)
        if edges is None:
            return None
        lo, hi, peak, rise = edges
        info = classify_span(lo, hi)
        info["peak_dbm"] = peak
        info["rise_db"] = rise
        return info
    def _cursor_text(self) -> None:
        """
        The corner readout: where the cursor is, and what the TRACE reads
        there.

        It used to print the POINTER's own Y position, formatted as a dBm.
        That looks exactly like a level and is not one -- slide the mouse up
        and down over a perfectly steady carrier and the number walks tens
        of decibels. Reported 2026-09-02 as the power changing drastically
        when zooming, and the zoom is what makes it look causal: a wheel
        zoom is done with the pointer over the plot, so the pointer moves
        while the span changes. Measured on that recording, over five
        seconds: the corner swung -28.89 to -43.22 dBm while M1, on the same
        carrier, held -40.08 the whole way.

        So the level now comes off the DISPLAYED trace at the cursor's own
        position, through the same _index_at the markers use, and is left
        out entirely when there is no trace point there -- a readout that
        cannot show a level is better than one showing a level nothing
        measured.
        """
        x = getattr(self, "_cursor_hz", None)
        if x is None:
            self.cursor.setText("")
            return
        z = self.cfg.zero_span
        head = f"  {x*1e6:.3f} us" if z else f"  {fmt_hz_short(x)}"
        d = self._displayed()
        i = self._index_at(x)
        if d is None or not (0 <= i < d.size):
            self.cursor.setText(head)
            return
        unit = "dB" if self._diff else "dBm"
        self.cursor.setText(f"{head}   {float(d[i]):.2f} {unit}")

    def _mouse(self, pos) -> None:
        vb = self.plot.getPlotItem().vb
        if not self.plot.sceneBoundingRect().contains(pos):
            self._cursor_hz = None
            self.cursor.setText("")
            return
        p = vb.mapSceneToView(pos)
        # Remembered so both the level and the identification keep up with
        # the sweep while the cursor sits still -- see _refresh_classify()
        # and the _cursor_text() call in _on_result. Without it the readout
        # showed whatever was true the last time the mouse happened to move.
        self._cursor_hz = float(p.x())
        self._cursor_text()
        if self.cfg.zero_span:
            self._show_classify(None)
            return
        # The verdict used to be tacked onto the cursor as a second line,
        # but it is better anchored to the signal itself (_show_classify)
        # than trailing the mouse.
        self._show_classify(
            self._classify_at(self._cursor_hz)
            if self.c_classify.isChecked() else None)

    def _draw_rx2(self, r: Result, diff_ok: bool) -> None:
        """
        The lower graticule: the second port, and a tag on each plot.

        Whether the pane is UP is the port button's business (see
        _sync_rx_buttons); all this does is fill it, or say why it is empty.

        It used to be the other way round -- the pane opened on the RESULT,
        when a sweep arrived actually carrying a second port, so that a
        click could never leave an unexplained empty plot on screen for the
        frame or two the worker takes to switch (bug 8). That reasoning was
        right about the gap and wrong about everything else, and Kfir
        reported Both as still not working. If no two-port result ever
        arrives, a result-driven pane never appears AT ALL and nothing says
        why -- and there are three ordinary ways to be in exactly that
        state: the sweep is HELD in Single, the display is FROZEN (both stop
        _on_result outright), or the radio refused the two-port mode. So the
        pane now opens on the button and carries a note until real data
        lands in it, which covers the gap by explaining it instead of by
        hiding.

        Tags are parked at the left edge, just inside the y axis, and are
        opaque so they stay readable over a trace. Positioned in view
        coordinates and refreshed every frame because the y range moves with
        the ref level and dB/div.
        """
        have = (r.power2 is not None and r.power2.size == r.freqs.size
                and not self.cfg.zero_span)
        both = self._rx2_on
        for tag in (self.rx1_tag, self.rx2_tag):
            tag.setVisible(bool(both))
        if not both:
            return
        self._rx2_note(not have)
        if not have:
            self.curve_rx2.setData([], [])
            return
        self.curve_rx2.setData(
            r.freqs, r.power2 - self.ref if diff_ok else r.power2)
        (x0, x1), (y0, y1) = self.plot.viewRange()
        x = x0 + (x1 - x0) * 0.006
        span = y1 - y0
        self.rx1_tag.setPos(x, y1 - span * 0.02)
        (x0, x1), (y0, y1) = self.plot2.viewRange()
        self.rx2_tag.setPos(x0 + (x1 - x0) * 0.006, y1 - (y1 - y0) * 0.02)

    def _rx2_note(self, on: bool) -> None:
        """
        The "nothing in here yet" line on the lower graticule.

        It names the reason when we can tell one: with the port set to Both
        and the source NOT in two-port mode, the radio refused the switch,
        and that is worth saying outright rather than leaving a blank plot
        to be read as a broken feature. Otherwise it is simply waiting --
        which is the normal state while the sweep is held in Single, or for
        the frame or two the worker takes to change port.
        """
        if not on:
            self.rx2_note.setVisible(False)
            return
        # Order matters. A freeze or a hold is CERTAINLY what is stopping
        # the frame, whatever the radio is doing, and it is the half the
        # user can act on -- so it is named first. "Not in two-port mode"
        # is also true for the frame or two the worker takes to apply the
        # switch (bug 8), which is why it is worded as not-yet rather than
        # as a refusal: a real refusal reports itself on the status line.
        if self._frozen:
            why = "waiting -- the display is frozen (Space)"
        elif not self.cfg.continuous:
            why = "waiting -- the sweep is held (Sweep now)"
        elif not bool(getattr(self.src, "dual_rx", False)):
            why = "waiting for the radio to switch to two ports"
        else:
            why = "waiting for a two-port sweep"
        self.rx2_note.setText("RX2 -- " + why)
        (x0, x1), (y0, y1) = self.plot2.viewRange()
        self.rx2_note.setPos(x0 + (x1 - x0) * 0.5, y0 + (y1 - y0) * 0.5)
        self.rx2_note.setVisible(True)

    def _show_rx2(self, on: bool) -> None:
        """
        Show or hide the lower graticule, and share the height out.

        The sizes are re-dealt rather than left to Qt: a splitter pane that
        has just been un-hidden comes back at whatever size it last had,
        which for one that started at zero is zero -- visible, and 0 px
        tall. Dividing what the two spectra have between them is also the
        request itself ("divide the Spectrum screen to two halves"), and the
        spectrogram keeps whatever it already had so switching ports does
        not shuffle a third pane the user has sized by hand.
        """
        on = bool(on)
        # Our own flag, NOT plot2.isVisible(). isVisible() is False for every
        # child of a window that has not been shown yet, so during _build --
        # and in any headless test -- it reports hidden whatever was asked
        # for, and this guard would then swallow the call that puts the pane
        # away again. Found by driving it: switching back to RX1 left the
        # lower graticule armed to appear the moment the window was shown.
        if on == self._rx2_on:
            return
        self._rx2_on = on
        sizes = self._split.sizes()
        spec_h, rx2_h, wf_h = sizes[0], sizes[1], sizes[2]
        self.plot2.setVisible(on)
        total = spec_h + rx2_h
        if on:
            half = max(total // 2, 1)
            self._split.setSizes([total - half, half, wf_h])
        else:
            self._split.setSizes([total, 0, wf_h])

    def _refresh_classify(self) -> None:
        """Re-identify whatever the cursor is resting on, once per frame."""
        if not hasattr(self, "c_classify") or not self.c_classify.isChecked():
            return
        hz = getattr(self, "_cursor_hz", None)
        if hz is None or self.cfg.zero_span:
            return
        self._show_classify(self._classify_at(hz))

    # ------------------------------------------------------------- export
    def _csv_meta(self) -> List[Tuple[str, str]]:
        """What the file has to say about itself, as (label, value) pairs.

        Spelled the way it is said -- "RBW[Hz]", not "rbw_hz" -- because this
        text is read by a person opening a spreadsheet, not parsed by
        anything. Asked for 2026-09-03, along with putting it down a column
        instead of across the first row.

        The build id is first and stays first: a CSV from a month ago cannot
        be argued about unless it names the build that made it, and a reader
        skimming the block should meet that before anything else.

        Nine rows since 2026-09-07, on request: "RBW Requested[Hz]" and
        "Window" are gone. What that costs is worth knowing rather than
        rediscovering -- the requested/realised PAIR is how bugs 24 and 30
        were made honest, so the file no longer records that a clamp
        happened. "RBW[Hz]" is the realised value, which is the one a
        measurement is actually made at, and the status bar still shows the
        clamp while it is on screen.
        """
        return [("App", f"{APP_NAME} {version_line()}"),
                ("Source", self.src.info),
                ("RBW[Hz]", f"{self.last.rbw:.0f}"),
                ("Detector", str(self.cfg.detector)),
                ("RF Gain[dB]", f"{self.cfg.gain:g}"),
                ("Cal Offset[dB]", f"{self.cfg.cal_offset:g}"),
                ("Board Table[dB]",
                 f"{float(rx_table_db(self.cfg.center)):+.2f}"
                 if getattr(self.src, "real_hardware", False) else "0.00"),
                ("Center[Hz]", f"{self.cfg.center:.0f}"),
                ("Span[Hz]", f"{self.cfg.span:.0f}"),
                ("UTC", datetime.utcnow().isoformat(timespec="seconds"))]

    def _csv_table(self):
        """The one table BOTH saved files are made of.

        Returns (head, data, meta, mcol): the title row, the formatted
        numbers under it, the provenance pairs, and the 0-based column the
        provenance block starts in.

        There is one gatherer rather than one per format on purpose. Two
        would drift, and a drifted gatherer means the csv and the workbook
        disagreeing about the same measurement -- which is bug 26's shape
        (`_gather_setup` split out for exactly this reason) applied to a
        file instead of to a setup. It also means the numbers are formatted
        ONCE, so the two files carry identical digits.

        Layout, asked for 2026-09-03: the column titles are on the FIRST row
        where a spreadsheet expects to find them, and the lines of
        provenance that used to occupy that row have moved into a column of
        their own, starting at CSV_META_COL. Across one row they ran off the
        right-hand edge and had to be scrolled to; down a column they are
        beside the data and readable at a glance.

        The metadata column is allowed to move RIGHT and never left. Column E
        is clear in the ordinary case of one live trace, but six traces plus a
        reference reach column H, and a fixed column would then have written
        the provenance over the top of the measurement.
        """
        head = ["Time[s]" if self.cfg.zero_span else "Frequency[Hz]",
                "Power[dBm]"]
        cols = [self.last.power]
        for i, cb in enumerate(self.cb_tr):
            d = self.tdata[i] if i < len(self.tdata) else None
            if (d is not None and cb.currentText() != "Off"
                    and d.size == self.last.freqs.size):
                # A frozen trace is marked, or a saved measurement would not
                # record that the column is not live.
                head.append(f"Trace {i+1} {cb.currentText()}"
                            + (" (View)" if self.c_tr_view[i].isChecked()
                               else "") + "[dBm]")
                cols.append(d)
        if self.ref is not None and self.ref.size == self.last.freqs.size:
            head.append("Reference[dBm]")
            cols.append(self.ref)

        data = [[f"{self.last.freqs[k]:.6f}"]
                + [f"{c[k]:.3f}" for c in cols]
                for k in range(self.last.freqs.size)]
        return head, data, self._csv_meta(), max(CSV_META_COL, len(head) + 1)

    def _write_csv(self, path: str) -> bool:
        """Trace data behind the current display, as plain csv."""
        if self.last is None:
            return False
        import csv as _csv
        try:
            head, data, meta, mcol = self._csv_table()
            rows = [list(head)] + [list(r) for r in data]
            # A sweep shorter than the metadata block is not reachable from
            # the GUI (the display is hundreds of points wide), but a
            # truncated block would lose the build id, which is the one line
            # that has to survive.
            while len(rows) < len(meta):
                rows.append([])
            for k, (label, value) in enumerate(meta):
                row = rows[k]
                row.extend([""] * (mcol - len(row)))
                row.extend([label, value])
            with open(path, "w", newline="") as fh:
                w = _csv.writer(fh)
                for row in rows:
                    w.writerow(row)
            return True
        except Exception as exc:
            self._message(f"CSV failed: {exc}")
            return False

    def _write_xlsx(self, path: str) -> bool:
        """The same table as a real workbook: widths, bold titles, a frame.

        See write_xlsx for why this exists at all -- a csv cannot carry any
        of that, and Excel re-guesses every column width when it opens one.
        """
        if self.last is None:
            return False
        try:
            head, data, meta, mcol = self._csv_table()
            write_xlsx(path, head, data, meta, mcol)
            return True
        except Exception as exc:
            self._message(f"XLSX failed: {exc}")
            return False

    def _save_trace(self, csv_path: str) -> bool:
        """Write the csv AND the formatted workbook beside it.

        Both, on request, 2026-09-07. The csv is the measurement in the form
        anything can read; the xlsx is the same numbers laid out to be
        looked at. A failed workbook does NOT fail the save -- the csv is
        the file that matters and losing it to a formatting fault would be
        the worse trade -- so the caller is told which of the two landed.
        """
        if not self._write_csv(csv_path):
            return False
        self._last_xlsx = self._write_xlsx(
            os.path.splitext(csv_path)[0] + ".xlsx")
        return True

    def _csv_default_path(self) -> str:
        """CSV_STEM + date + a number that has not been used yet."""
        stamp = datetime.now().strftime("%Y-%m-%d")
        try:
            os.makedirs(self.shot_dir, exist_ok=True)
            here = self.shot_dir
        except OSError:
            here = ""
        n = 1
        while True:
            path = os.path.join(here, f"{CSV_STEM} {stamp} {n}.csv")
            if not os.path.exists(path):
                return path
            n += 1

    def _csv(self) -> None:
        if self.last is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save trace", self._csv_default_path(), "CSV (*.csv)")
        if path and self._save_trace(path):
            stem = os.path.basename(path)
            self._message(f"saved {stem}" + (" + .xlsx" if self._last_xlsx
                                             else ""))

    def _png(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save picture", "spectrum.png", "PNG (*.png)")
        if path:
            self._grab().save(path)
            self._message(f"saved {path}")

    def _about(self) -> None:
        """Version, build fingerprint and the versions underneath it."""
        import platform
        try:
            qt = QtCore.QT_VERSION_STR
        except Exception:
            qt = "?"
        try:
            pgv = pg.__version__
        except Exception:
            pgv = "?"
        lines = [
            f"{APP_NAME}",
            f"{version_line()}",
            "",
            f"Author      {APP_AUTHOR}",
            (f"source      {os.path.abspath(__file__)}"
             if not getattr(sys, "frozen", False) else
             f"packaged    {os.path.abspath(sys.executable)}"),
            f"Python      {platform.python_version()} "
            f"({8 * __import__('struct').calcsize('P')}-bit)",
            f"Qt          {qt}      pyqtgraph {pgv}      "
            f"numpy {np.__version__}",
            "",
            f"radio       {self.src.info}",
            # Diagnostic, and it earns its place here for the same reason
            # the build id does: this is the page to screenshot when the
            # program misbehaves, and "sometimes, after a while" is only
            # arguable with a number attached to it.
            f"RX stream   {self._stall_seen} stall(s) reported, "
            f"{getattr(self.worker, 'stream_restarts', lambda: 0)()} restart(s)",
            f"libbladeRF  {self.src.lib_version()}",
            f"firmware    {self.src.fw_version()}      "
            f"FPGA {self.src.fpga_version()}",
        ]
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("About")
        box.setText("\n".join(lines))
        box.setIcon(QtWidgets.QMessageBox.NoIcon)
        box.exec_()

    def closeEvent(self, ev) -> None:
        # A running recording is finished properly first. Its sweeps are
        # already safe -- every row is flushed as it is written -- but the
        # max hold, the picture and the sidecar are made at the END, and
        # they are the three files somebody actually opens. Closing the
        # window mid-recording is an ordinary way to finish a site survey,
        # so it must not be the one way to lose them.
        try:
            self._rec_stop(quiet=True)
        except Exception:
            pass
        try:
            self._macro_stop("the window closed", quiet=True)
        except Exception:
            pass
        # Before the threads go down: _gather_setup() reads widgets, and a
        # half-torn-down window is not something to read state out of.
        try:
            self._session_write()
        except Exception:
            pass
        try:
            self._stop()
        except Exception:
            pass
        try:
            self.src.close()
        except Exception:
            pass
        super().closeEvent(ev)


# ============================================================================
#  the application icon
# ============================================================================
#
# Kfir's artwork, 2026-08-30. It is carried INSIDE this file as base64
# because of the single-file rule: the launcher exe extracts only the .py to
# a temp folder and PyInstaller freezes only the .py, so an icon sitting
# beside the source would be there in this folder and nowhere else -- present
# while developing and missing in every shipped form, which is the worst way
# for an asset to fail.
#
# The window icon is a different thing from the EXE icon, and both are
# needed. Windows takes the taskbar and Explorer icons from the binary's
# resource (build\icon.rc, linked into all three C programs by windres, and
# passed to PyInstaller with --icon); Qt takes the title bar and alt-tab
# icons from this. Set only one and the other place shows a default.
#
# Regenerate both from build\icon.png with:  py -3-64 build\gen_icon.py
# The region between the markers below is written by that script.

# --- icon begin: generated by build\gen_icon.py, do not edit by hand
ICON_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAYAAADDPmHLAABQfElEQVR42u29ebxdVXn//37W2sOZ"
    "7rljbkYShgBhkFmQMYAyFHBCg1YFqzhTW5VWW1sbcepgbbWT32odaqutIE6IAg4QQARkHsIQAiQh"
    "IXNypzPsvddavz/2Pufsc+4NEiT9Dr/svO7r3ntuzjn7rPWsZ/g8n+d5YO+199p77b32Xnuvvdfe"
    "a++199p7/f/rkv9N7ynLly9n5cqV8n/jol2VfV+W+3naH5/zGcu6/rp8+aHuiiuucNmv7v85KVu+"
    "fLlaunSpt2zZMr33zP3GSy9dutQD1P/tGkCWLVumrrzySisiean2ly5dOjsMK8MT9fFyFFkF4AFJ"
    "/tlJAp4HQJwk+NnPrav1W5L7mZ7H8t9br+ft4maTaQ9kj3he5+fsilsfBIjjBPzcq8bJ9NdN4uwm"
    "/dzP6RWowI6MzJrQ2m699NJLN1900UUm93S1bNkyueqqq8z/VQKwbNkynb/ps84///CBUvVsUXpp"
    "bJLDrbFzrLUlYxLiOMFam+o+53AOnLM4QIkCHMZYlIAohYgCEQSX3r5kH8E5EME5h3Ugrcez/2md"
    "Q0RQSiOSvlf6XMFZi7MW62z6N+twzqFEAIe1pvMezmFd+pi0HsJlittln6Ej79ZarLXpQkv6/9tX"
    "dg9KCUqpSaXUBq30A4GvbiqXizdce+21q1ofYvny5XLFFVfY/9MFQJYtW6ayzdfnn//aZX4QvMva"
    "5DTr0OPjY0xMTFCr1YijCOecQ8S1FrW9KOlS0nnIZQso6aJnj7U32LU+ieCQdEMQEJU+JoJI628d"
    "K+tEwDrApq/hOuurJP3dWoOkf2w/UXIb3hHc9O/Sev/WPbUEhM7P0vmYiIiIElGi8DyPsBAS+AGe"
    "p5uFMPh5qVT5X7feetM12SHRgH0x/QR5Me18S0Jf8Ypzzh8cGr4iTpJjt27ZyqbNG2nUG0nr87Yc"
    "QRHJbWq24S7vCbmuw0JbNKClA9oHMxMQUCAq+111hICOgNC1ndk+uUw8XCoQ4lwqEM6mm+ts+1ld"
    "AuHIbT64joR2PkH3sc/uw3WEIxWg9iWI83zPK5fLlIolwjC4uW+w+rFbbrzx5pZ8ZoLwf4YAtFT+"
    "8PBw3ymnnPF55Xlv37hxIxuf3WDiOEaUUlqJpJuRLZjLHZH2pmSLQc/utE9P57adtDY+f7qlvekO"
    "AaUB3d58RHDZ31xOGHDZhgOCBZeAc4izOGvAmez/pMIgzqbr39IYvRvsXPcRbf+efgjJmQKXl/qO"
    "OOKcc9ZaKyJSLpdVtVqlVCp+7l3vvPRP3/3ud8eZNjD/2wWgtfkvOe64gxfNXXiViLzksccfM7Wp"
    "KfF8X+VVpMyku1z3XXSEI7euQufkukx1o2grXVE40emmikaUxokGSb87UYjycKJANFayn11epdts"
    "kw1ik1QIbII4A9bgbAzWIM60hUJyWgHnOjopd7J79FjXAqR+CF3+w0yXs9Zoz5OBgQHVV6ncunDh"
    "/Df88Ic/3NDra/2PC0DrBg44YMkxhx5++E8mJydGVz3+eKK19pRSWOs6CyEzvJVzeVPZY09b6yI9"
    "9h1cW81n30WD8nDKB+WD0jjxQPk4HSDaR5QPygMVYJWPQ3XdlsKAjcHGOBOBScBGYNIvlzQRGyE2"
    "xplUGCAVAnGGtv3KhKK1t5lxy3mAjhn32blus9CzPS7VPnF/teqXy5UnfF/OefDBB5/8bc3BbyMA"
    "CrCHH37skvn7zL9lcmJiZM2apxLt+V7bNuZOtMwgAK7jCXV+dh1/YJrdFslUuko3WXntjUaHOF1A"
    "/AKifJwKMSogkhINChhVAl0CvwhBETwftJcuQRJBsw5xDaIamBpi6xSkSYEmyjZwcR2X1CGpg2mm"
    "gmHjVEvYBMF0+QuCy6l613ZUuo2Dm+7fzCAdLucgW2uTSl+fV+3rWz06us+pt9xy/bO/jRC8UAGQ"
    "5cuXy+c///nq8SecfGeUJAc+8fhjxvN87XpuuPOMnL1vq7wuLywNm7J1sY5OmJbZdkem6sUD7YMK"
    "sk0vIl4Rq8vUpY+aVCEYxK8OMTo6wr7zR9h3fj/z5/QxOlykvy+gEPgEvsI6sMZQrzepTTXZsq3G"
    "+k0TPL1hnDUbtrFh4xbq27ZAbQsh4xSZQpsaNq5hkxqSNBAbpybDxKlZzgsBOS3Y0m7ZqovrOLsz"
    "qv8uf6jzd2Nt0l/t98rl8u1HHnnY0swMvKDoQH4b1X/Sqad/u1goXfT4Y48mSikvVVPT8Uzplflp"
    "trHbm285d4hkm65APJBUzTsVIH4J5ZcwusiUq9LQswgG57Pf/os48chFHHfYHBYt6KdU8akBm2uw"
    "YRw2jzt21ixjdcu8YUXoKVY9mzBQECq+MLuqmdcPo0UoarD1iPXP7uDeh9dz2z2rWfnIapIdGwjs"
    "VkpqEm3q2LiWagfTTLWCS8Da1KEkcyadbQuCzHjCe0PHXg3huqMIZ+OhwWG/WC5+4dGVD30AlmnY"
    "fX9AXujmH3HE0a8bnTvvO089uTqJmpEnmRqf2fudYad7gJH25qcRIjZv30XjxE9Pu1dEhRUiqTBm"
    "BvEG9uOoIw7hrJMP4oRj51EaKrF2HO5a7bj38SmeXD/Blq1TsHMSmk2Io9ROJzCwuEpfuci6e54F"
    "36RnSGnwAyiVKQ5WWDS/j6MOHOSEgwIOmgXJ1CR33fcM1614iLvvWYnd+RQVvZ2AGjaawsV1sKmJ"
    "EFIHEme6/IMefz/TDB1F6VxvCJzXqJKtm3Oe1mZoaMgLQ+/UlStX3vpChEBekMAsWFA4ceH+Dzeb"
    "0b4bN25wnuer9ub3qDLX4+jJLk6+bTl4LlP3onDScupCnFdE+WUiVWXczGLWvkdw/stfwvkvX8Lg"
    "3CqPboerb4+5+8EdjD+9FXZugWgnuBpiJxHbQFySxviSHtQ5h+1Hwbc8ffdTSGDA2uzmFE5CLAVQ"
    "faAHoDiMv2AORx85m9ecUuKYOTCxbSfX3XAf37/+DrateYyi3kpBprDRJC6uIbaJs3H6vlmU0RIC"
    "gZ4IYHp41LWeLsMYsscEwTpryuWy7qtUfv3mN7/xZVdccQW76wvslgAsXbrUW7FiRXLI4Ue8Z3hw"
    "+ItPr3na4NAzOizkIFLp6LW82bft/yc4l4vPW5uvCzhdRAdlEj3ATjuHOfsfydtffwJnnrGE9Yni"
    "O3c6fvbLrSTNceJH1kG0EeV2IGYMZ2o4G6Un0NTT8A2HUmAbljlHLqFY9Hnql/ehigpr4vaSiA5B"
    "lUA8lC6AV8XIADCCPuRgNFVOPHaUN57mc0A15uabHuBrV93C+sfupextJmASE02mTqONwCY4l7RD"
    "xw401bNWeeXvfhMYlroEs2bN0n4YvvqxlQ/+cHe1wG4JgEtBVX30MS+93xh7yObNm632tG5tcj7e"
    "7xWGzoeUnB/YAnTym69T504VEL+MBFW2RcMMLTiK97/9dzjzjAO5c6PwpZ9MserujbD9WTCbqc6J"
    "mVz9NC6ZwCXjYCazhW/Z5DgDb0A8D9e0zD3iYIp9Azy54lZUUWNN0gnndBFEIehUA+kiokuIP0hp"
    "v32Z2gjOzYHKPBYeuZB3vWaQkxZZfvHTX/PPX7+BHWvvZaA4hosnMNEkYpupk+hM5hvYtj/Qzk3Q"
    "jR/04iQdZdABlKy1plKpqHKptOLJJ1edsbsRwe4IgAbMfvsdeOrIrNGbN2/eZKM4UkI+6eK6sPxe"
    "T89lNsx1JWoE66Tt4aeefQkdVpmyFRreYi5506t458Uv446tPv/83QmeunMdTKxFmfWQbMG5BqU5"
    "AVOrngDXBFNLHTIXZ5rHpJ56CzjSGtdImHfc0RSKBZ686VZUOcgEIN0YlJfZ5HyoWQBCwoUHEm2v"
    "I/iIPwujFkCwiDlHLeZDbx7mhDl1vvbvP+Pr//Uj/Gg1ZW+KpDkBpg4tk9AWAJcpyHwk4HocwZye"
    "6IVJnUNp5YaHhkxQKR7+2IMPPrY7QuDthvqXFStWUKiUX2OsodlsWKW06oActgvMaSVG8prA9QoC"
    "QponyxA7FSBeGR32sa0xi0OOOoNPfeTVxEOz+P3/qHP/zU/BjkfQZi022Y5LxnHJOGJr2NooRFvB"
    "1sEm6eY7014HZ+NcgkhBHKPF4UmSxfYuje2zXAA2RQpbIBPipRvoBJqDMLUFi4d4WxFvPTpZzcY7"
    "nuDDDxzMklOX8PGLX8mrfucYPvqJ/+DRu29ioKpJmgriGhaQFpDUgwnQySpMzx84pkHGCBhjjLXO"
    "s5G5AHhs6dKlasWKFfb5nurnda1Zs8YBzJs7/9ONRnNerTblRCnVhW7m0KxulzaP92exvUs9/fTk"
    "e+AVUUEVo/vYafbnXe94C8s/+jq+eXeJj31uDZvuuRtdfwCaj2Pra6DxLBJtg2gnYqagOBe788ns"
    "lDXBNNLvLQfMxh1MX4AoYmDBPMJCka2PP4aEKkX4Mgg4FYKWICVZfB+DaaJKfdjxjel7mBokY6kg"
    "MoEnO9m8ejPf+blDRhby6ctPwboit9+xFj80KJWmn0W6s4+7goG7NntaIOVaKWfnB74KwzDetm3L"
    "t9asWSPPFxPwdsNUuDlz5swSkYMajXqWauvE9W6GsE96Yd2uME86oI4qoMI+puIKft8xfOUzb2Pw"
    "oAN4/ae3s+H2h1CsgmgdNt6Oi7aDmYBkCmcbabgVBoQDCfGqMUS7bCPTzUYERGebmH4UER/XbDA6"
    "d4D+2bN4JK6nJ940c8/zOuEb2WsonWqAeAri8RStUhkgZWpgGiTxBMpbh7j1/Pf/WstNtx3FP/7p"
    "Ml523GL+8MP/gokeoRSCiaay2DfGOdsGyERcz9bJNNXvpoOJEkURghx+7rnnhtddd11zFwHXjHDu"
    "84V96e8fWaS1rsZx4hCRFnEin8DJi6jNBKIrbZuhfA6FQ+NUiA77GGtUWLT4dK76j8t5Sh3AhZet"
    "ZsOdt6DN7djJh7BTq6G+FhoboLkV4jFIJiGeQrkGhVITkuzUtxw/18rkZac6S/IIFqylOlBlaHQo"
    "XXhnO5vf/tl0XsdGGQTcABPjbMvXqEEyBdEYNDdDfR12ag1m4lG0vZ2N9/+YZe+9n7X+0XznO59g"
    "/r4nM96ooIMKToVpYgqFc5IDwXoA455oQGSa1ZAkZS3NHRsbm7M7/p16nuBPqi7CYA4iGJvYdIPt"
    "DGGf63r7/Kbb7OTbTO07FeIFZbZNFjjuxAv4t69+iC/dVuVTn74f2XwjqnYXZvJxqD8NjWdx8Xh6"
    "8uKxdNGTBpgmYpv41ua8/ri92Z2NzzbXtmJxS7lQZLCv2gNFdv6OawE5SSeScBE2qme5gChnBiYh"
    "3pl+RVuhvhYz/jiq+QBq7EY+8xcr+MrNI/zLNz7OMcefzfhkAS/sAwlTLYhq2/k2MJgjmjDtjOUg"
    "ZRGxxjgRAmA02zN50UzAVVdtFoB6barqaY2zXem6TsLO9UC9uUDAteVNZchegA5KbBsvcPYFy/ij"
    "T7yT9/3DTh668W58+wDJ5FPYxiZIxsBMpbZdVMfGt1A1axFCCkU/3SwrWaYu73tI9v8zJzC7L10o"
    "EJaK3a5QSwis5DRBfi0tLs6yhLTMhQHJhEwEXD29T1PD2gYkU/jlMb7/1QZPPXUsf/3Fj/KJywvc"
    "dv136auWMQ2Dkyz6kFQbdZhDbkYQNW8B0gPmXJIksmnTtr50z67iRfQBVgDQrNd1qVTO0L0WL6eD"
    "9rXZMZJPY+aYO9JC2dLN3z7uc/YFr+OyP38n7/74Bp668xY8/xniscehsRGS8fR02WaqfpWXO+XZ"
    "CUhiwr4yB5/0Ep763nfB05ntzodQ2clvMYYyFeUVCqhCgfTgqC5CB2I7ISGuyxdzcaPjXIrNsA2d"
    "ZndMS4ii9MtGkEyRiMKv3sf9P17Heydeyef+5nL+nphbrvsufX0lkmYngeS682MzIjL5MLG13NZa"
    "6vXJ3WJee7D7WHA+XnXPAWd2kjutvJgC5aP9ItvHhZNffgHv/dj7eN/HnmL9vb9A8whJo5na+Ggb"
    "2JZNT5G0dC8z9d6ihRmD72uK1Uped/ZsWkutq5xl1CilKIZlXFDFSqOthsll79IwMpehUQpKfTCe"
    "nXyX+cPOpQLQ1jy2239oFkjGDNor8dQv4fIrzuVzf/URpiYnuOeWH1EpFzBRgnM2/Vx5QK2dEJcZ"
    "MAI6a/ECKEK7xT0300ibDmczZy/nvDjX0QquDXymAqC8kIkaLDn8BC77+Af44CfXsf6un6Ddw5jJ"
    "NRBvATMGSQacmEaWZcsg3ZZtzzlsWitKfQOZt2+7bXkOf+/2nHxcUGKsPslRb3kV/aNzIYp7vOu8"
    "Q2ghSZBCCdlnAcT1zt9bfkIr1LRx5jA2s88whWtuhXgLZmot2t7NU7/8Hh/59DN86G8/xqKDj6VW"
    "B/EKqXlEdcgvbR/Kda1pPiJoaWR5Acnd3S4+6PDoWpvrOqyX3B13eJMtE6ARHdCMYXBkMR/+u+X8"
    "5Re38PSt16Dl0XTzoy2p2o8nszg+O/ltypbJqXFph3hBZYjC0ED2u5cBN7oD+mQZxVZKGfFBPESB"
    "VQEn/84pDI4Og3GIajlkOWZxjlwqyuH7ufAyT0tvaw7TEVYbZ5+lAUkNoq2YqfVoHmDVTVfzd18Z"
    "44/+8TOU+/bBGEmZS6husqvL5QncLvwBlz+le1AA2tz3rhvrZvP2fkEHTUtMHx/+7J/xresVD/7o"
    "u2h5GDO5Lt38eDz1ol0j8+RbBIvshLUXx2a+Xbq5OijiVyrgBWk2seX4tT+idOInUaA8RDSJSQiK"
    "fbhCCat0KhjTTpF0QbFKICxVMic8ZyYkt/EtYW3TzKLMj5mCZByJtmImn8HTD3HP967k+tv7ueyz"
    "y2nWMhpbTgukt626GFWt+gnJh4QCogStvT0nAJpcwUUuEOjk8rsfSJVBiv0rL2Bsh+Ftf3wZK8cO"
    "4Wdf/Tae9xBmam128ieyBaojrRCrJQQtlY/KVK3piL9tYpM6SfuU5uN51x3bZ89LTZfQUCETUYPA"
    "13hB0AkRXY7D1w4DbRtF9LXk3sd0Xhvb/XjbXCU5c1DHJRMQb8dMbUDrB/jRP/8X2/yTeO0fXEZt"
    "e4TygnS1JWcKcrG1SFe2ZQbfbE9pAJ1V5vQyeHNhn8vLQOb8KeUzMR5z3JlnMHr8RXzts1eiuQ9T"
    "Ww/xtnTzbefUt1O4Ldud1zTtaiAFxkD/AKo6iFUtnnimmrvMRM5sIIj2KC9YiPIKqdbMNAkZxbzz"
    "nK7wpZ3aFr/QCWln1Bh0k0QzIXA2jx1M4aId2Kn1KHsnX//L73Poue9m8QknUZ+IUNrv1j5u5jxe"
    "PtQWBM/z/gdMwAypii5AyOUIaqKIDZSqQ1zw/g/zlX+8BbftZlzzWVy0LQVQMhg1T7Sc5sA5AxJ0"
    "1LQILk7Q++2PNzIHG0dQnQeFgSzkUzncIfvKoFyvUOWCT74fXQiJLZgWxJvlKHYNoAo4hXNqBqwt"
    "b5xtjwNp2uTR9PM1O0IQb4dkI/GmG/jPf72H1/zxXxAUBlJGFJkgd1HlellWuUhA/U84gblTn2f9"
    "uvZXLtePQumAqZ0Nzn/PO7j9AZ8Nv7wKrTZio+1IMpkBO1FHVXZBuDan6i16ziji+zmWsabghSgl"
    "YCxq1iykUs0iN+mocSe5E6vQfkhhsA+0T2QhQrB5Z09kFyfO4hVChhfPza197ynNh5G224Ftm7MW"
    "5TwDi5pjKG8Ta1ZcxaNrZ/OKS99BY6yG8vwuVvS0kqauPcnMs7fHncDpuX7X6yBmxRuiPOpTEYuP"
    "OZLhw17NDV/5Fsp/BtPYAckELr/5Nunw5/IFF20ATvBnlRFPMjWfevpaLGBoJhAMVPCKxc6G6xD8"
    "StsBlAwFFM+nUC4RAw0HkQPTNh2d8rKO45iZBhzh0DBzTzoc/BJ4hY6g0Ws68iYg1WCuKzrIRQhm"
    "EtfYjvKe5Pqvf5dFp1zCnAMPJWokiNIdoZwJEPot+d27rwHyCSB6o4GcS5qFX8Y4Tn/HB7j+Bw9j"
    "t94O8Q6IJzpYev7Ek1f53TV2QEbqyG2QE8KwiFYhcWwYnTNEub9Cq25MqqOouQemJdntRVSI5xMX"
    "A5zSGKuISOnhON3tVudXNdtor1jGix2jb/hdKoe9BOIGolQXQWNXEJ6QxyZy5iCp4eIJMDuI19/M"
    "TT97hlPf+QGSZpKFpZILp3uAtq4CWsHT4R4UAJOWUHclKpybgaggiNLUJxssOeVUprzDWHXD1ahg"
    "DBtNpt5+5uhJ+8T3YPE9KBcoBmbNxgv8FFPImJ37veJ0Rg4+hHgqZsnppzB6wAKwDnEKFQpeVYPV"
    "7eoiJC0oaWiNUQojigiwfoiU+zscRslpgZbQ4SAMif0io4sPpjhQyULAzLxYg5RGwAt71PRMhY42"
    "C3GTFDI2NWw0gQq2sfInP8QbPY39Xno8jVqjIwQ9vMBeCEKUoPc0EthGnnqyVQ6XpXlbDN/UgTnq"
    "NZdyyzV3QOPxNM43tdQhSl+tzQ9oaxAVpF8tJk6Wb0c0B77lDVQW7pd6/zoFc6rz5qKq/dSsEPYP"
    "ogoVkBDER3yFlCqp49gqG9MFnIO6KIwu0MQQJxZ/zr54+83DxTb9f5JyAdvPRUHpAAgGcKLxBgeR"
    "YhEI03vRAViDmncoUpmd0cr8DpuoBUa1oxjJgTsGSNIQMRqDifu57YZHOfi8d2DjJHsN1YlS8oCc"
    "dMtWJwhYtqdwgJkoTB3BSJszaBpTNQ444QQmZV+eueWHiF/DxpPtcM/l8vNt0KQXvnWtcqtUQ9hi"
    "iPNUzkEUnOchSqMdRL5CCuX0ObaJ5/kUigGYuF3uLS5BRKg7iBOLFU09SigOFJh96H4QRwgJ+P2p"
    "jc/yDi6qw7wRVHkYZQy64KODChCndYQuSbmkBYtScZb/zqjmmFTztJ3BFtMo7qSvTRNsAxdPIGor"
    "a279IWropcx7yXFEtUZbeFyehOO6EW5nbUcClu0RH0B1cf7yIYnLx+lKY+OIxS9/PQ/+8nFoPJl5"
    "/M1Obj6fecur+zmHddvglsSJpuj5KOe6nDQJNOIFNKxFlCA6U9UqSLVs3OiEU6JwjQRjCnhKUY9q"
    "oEPixFIYKjHvJYelqtRaVHUENTAbjEkdMSfMO+tEwllFGpHDD8to6fYPwOH3VxFfdfL1IikbuLoP"
    "BH25z54LGa3tJI1sE7F1GFvJ4/euYfHZv4uJm2ktZE5b9uYE0qWSdhCwbM84gRZrXZfd6ab8pLYz"
    "bjQZ3f9AZOBInrztBiSIsEk9S5HmYnybc/gyHRYOF5G2Ny45PD79rrww9zef2Ato2BhRXlYMaqE4"
    "AkYojg4xcMRL0vXxfFwjYvDUExk4+lDqkw3wizRNnLaDKVdplkuAh7OCLibospc6hioVoP5jjiGc"
    "NQsSS4zC9OIMOJSyiA5yfkMaeahSGfGDbmc5n3FsM5ea2HgKvAme/tWNVPc5hf45+2CiOC2Dd70e"
    "hXR8AdjDQJBWM8CQHU2Q+k+apF5n3kvPZN2qnbixpxBX62TzejNzec3hBM+37RPfRvREgR+SaC/j"
    "+AbtBbci4IXEzhFbgy4NwaKFkDQpzt+HwWOOanv+xI4FZ53InJOOpj4ZY7PXixAkKEO5mPIVnMIr"
    "BPjFFiSrAR/fD/D9EmBJkLR4hTR5JBmaWNl3P3S52EJmOo6kL6mf4Dqta6aFi9a0IwPlaiSbH2bj"
    "2hrzjn05caOW3ccM9Zb5Q6j1nhCApW0fIHX8ZBoI0YGDHX6xwODBZ/LEXXeAbuJMI0ew7IkYpBPS"
    "iR8y57TT0aVyZ6GUTinbERS9EBE/u/MAvD7EWgSNIAgKUaCrRcBDlIcXloAW6ycAL2gXfSRxgs1C"
    "QKc9tJc5a0nM8EuPYfSUE4FUu6ACgrAEvkfsNJ6EHYvcgpJdk+HDjqA0MicrQdNZiGiZfdRxVOaO"
    "ZlFDj3lrJ1FMGzBySQ3Yyaq772bw0LOzJI/kKqZ7dLNLowK9RzWA0u3ixDYDWCQXYimSZpP+hfuT"
    "yDwmn34Y0VHacKGriYLrTqVmAI0qVhg+bSm6nMbyojyIE0r7H8TQGWeyc2wS54UgGhUoBs5+OYkN"
    "iJII0MRo8BTDw7OBYorZFwKQACcB4KPDAOUJTeewTpFkjqsRIWpvpKYway7hrNHshHtgHTHgVIhz"
    "EBmL1WXw56bhn1dAjxxEE52BTyoHRycMHnUc5YX7pbUA7c+eM3FdIXCCs01E1Rl/8i6kuD998/bD"
    "RM1cmX0eGnYvuMWk2g0FgEKjtUK62+Hk6vyFpFlj9iEnsWX9dph6JiuMzIE8bW5ej53Pmj7Y2Kab"
    "JToNrxJDcf8FjJ57LrZh20X1XnmI+ccempXjK5oOGonB75/FotOOB0IMglEp7RxVgGAeRvskokkc"
    "xCZV5ZGFxFosPhTnAAWssxgv1TZSLOLPPQhnBaWLJIkjMRGUBmHBwbjIoiplKmecTyBhFrdnh8Ir"
    "QnEJiVXZRqlpyalOSJiRUGzmEAq48TVs3bCdoQNPIolqHf8oxw/Il+J57AkNsKKDBKROoOsBpjul"
    "jqIU/QuPZ9PjjwMTGfxpcx+w5TR5ne+tWB9F4AdpMSY6i7/TWDzRIb7nI7qMmnMolEZQoyNEmcev"
    "RSNW0GERRgYAje+VCb0Q/CGkUEIddDBOlXCSlqMZJxhjsZJaGU0Ii48ESX0K46VtJvuPOY59/+i9"
    "RDsbKWTsPDAejgi/4sApdN8sinNGUM6hg3Lmp3iIF6Bmz0l5o7qcapP2Z28lqcJujdD2BwzYcbY+"
    "+Tj9i05Mawa6WuVMP/jensgFLF3acTCkJ/4n18rFJobiwDC2sC871j0GutfpyzlAXhlUMROADPxB"
    "44xD+udDZXYb8y9Uh+nrG2SsaZG+YZZ86oP4g8NI3wDGBRnNXGEcWKWRchV0H1b5GKOQxYfgz92f"
    "gUPmERuLy4AVJ2kSyKEQHSIW9Oy0dUzgFQjDfpACqljEq1axRjBZDaNJFKpYpLTvfqlGKvXRNzyH"
    "KFFQGoG5R+ESh9dXoXrEEWB1ln7WHaFvAUThSPpzV2LJpr6TMoxvWI2uHkBQHsIaMx34d600hOxO"
    "sdcLAIK0zsHdPZi3KEwc0Td3MY0kxOx8BsGmgE+LKNESBJOgqnORoNJJnYoCr8xUPcIb7sObPQsX"
    "J6kv4IV4SqcAYKFMafYIOnAoHWCNwhhLbCDKys2kUAApEscxUwbCuaMUF8yl/2WHgfiYxGJb3UdS"
    "qh/WgrWGxeedghqYTeI8EgW4AHSAUiHOapyJiI3DNWNKowsZPO5lGdBj0X4JMRrxfIrz50LcRJfK"
    "VPc/jDiymDgBKfTgt4JU5uZ4/jlSqU0QMcTbn6TeCCmO7IdN4jZDSGBao43dxYJ3MxfADLg/bWas"
    "SSKq85YwuW0sZfW2nyCdcEqlsbU3UEbCdHGpx8w++xwWvOHN2G3jjB51LCNHHwXNBKqzSFRAPbb4"
    "CEIRlXiooIxVPkkimNjijOBcWmlkEovssy9B/wJCk6DjCL8UUF20APGKOGtJEoe1Nj3RFoxxGPEp"
    "zpqNFKs4glRmSwegggrOaTAKnIcYwSYOUT5hsZpCzyoAr4JnhMLQMCOnnQ4ovEKVvsHZkAjFOfsx"
    "+sbLu+Fm8fGrfZlp9KZR0HAJNDZT2zlBceRgrGn2JKy69yPcs4QQg7W2q97PtaKC7JHyyEFMbN6Y"
    "lmm7TiKnRcoUHYBVVOaP4vf1p/bUWvy5C/HnLcTFCdLXjx6aDU4xeuFrCUfmYRoG5RTO96gnAQRD"
    "OKfaWeTEKGwiOAlI6sIBl5xL9cAFxDXL3JOOoLLfvmn2ldTe2yjFEBIrJInCGLBWpRXKQQFLgJOQ"
    "Oe+8CL86nJ5sNFgPl2R9Cq1CJARXRHQVpUOaTYOEZfyRBemG6hLOr5DEDsIy5YUHd5xg8RG/wMBB"
    "hyJesWMe8jiBs+Ca1Me2Uhw+OGtyIRnbyk1vecoeBYJ0Z/PdtHdGeR5e/wImt28BlSuqbLNxvXaC"
    "pf9lFxDM2gcSi5q1P6IqeKpKM/HwvJCwMABOUz3yGLxyFdtU2CaE80dplOdgZy1GYgE8rPFTVM4q"
    "rFHEVlGYP4wuF3A2oO/IxQRz59GIwSQKkwjWKKzTOCs4K5kZCNI2chSIIyCoUDlwCaIL6XslGrEK"
    "Z1TqpFsf1xT0fkfiD8zGcwFxpFEyQCghOB9jfawt4PBwBPiqCMX5oMtgHCocoHrsy1FBKUs85f0D"
    "1UYKp7ZvRFUWIHnSZ1eTzRQo3l0n0NvdZJDLNzJocyfTpsteUELCYeqTq1LAxTQyB6+QNldwWXGH"
    "OEK/ghYPkYCFl74LSTwk8VFa4+IEp3y8+QeiJcTqPlQiNJoOXyqoiRjte0gkJE1wiUaMwsQKazQu"
    "0eACCn6VuFmnTowxAdZ4YH2ccbhE0loTP0X+rIE40dh6QuGQJZT2WUhgDU0b4qRM0hRMXVCJQpzC"
    "RIJSBZJGgzm/8yrEQm3cEOoCkW0yNRYRHnQy/siBSCy4JNV+zcmIvqPOor7mXpKxNXiD8yjbIG1H"
    "YyYgKHSKYDLMAeUR1XbgFQ9E+6Xu8vscJiAi6N3kA3i7mw7WPcyUNoHWOvxSBaX6iJvNrKVbAfEH"
    "kcYYauhAzI5nCeeMMHjimZStxfeHcFJmYP5ipjZHxA1H4Hk0JmPKc/dlzut/F9dQWOtj8ZHYwyUe"
    "QaJREtCc0rhYcFGEjj2SpsVawbMBTatxTcFRRIvCJBO4yEcBJq6hYlK7nqRRgM1Oc33cMvKy4wnn"
    "zqe+ejWqMoAOZxPXHGI8bOyhovS7BUSF6OJsdJYQFAvKeSRNzYJXXUw8PkFjLEasj4iHJDHDR53C"
    "pu1r6D/saPoWHc7EljHCOQcSh/3EO55BynNxzfFUSzgHXoEkijBSQQdlTFxLPf6uVgyCc/wPcQLz"
    "OSBp9Qey+IUqsSkhTqOCflQ4gugizpWZv/RMvP59CAb2Y3TJ8UyNJ1QPPZ6Fb/kQ8XaFbWioKyTy"
    "0K5EUtMoqVJ0ZXxXxdQsfkOhdT/ReBNly9DUSFMjtoAkCh1rvCREmQI6KeMahtAL8ZICrgk68vFM"
    "gDKA8QldicApSBSB7id0HiYJCEwJGhrrioRSQEuZpK4oEuC5AJeEaFfENTXa9REwgGvESDPERD6+"
    "K1HRIcYUEFfElwK+VHCNBGyZUjhIIEU8r59CaQE0Yuae/nsMHHUOIhWKi45NYW5VRgWDKC+tQ/D9"
    "Mjrow1nXJrHuqkHXHjIBKisJp7vrtQjWGoJCGeeK2MYQmAVpo6bqLILRJXiLTqZ66iAKzba1jlAP"
    "QN8gng6pbXc0YwPiiFRIY8yjXKjijGX7eAlXqyPeEM26h6sb1IRh50SRsFHCNWPwS0RNjTIBSWRo"
    "+iFR4tDKI1aKJIYkGqIQBEzUY5Koj6ItYYwiiqYg9vASD+M5JHGMT8WEUxbMKLVGkcmpCYJKhbGm"
    "kEQehGVIQmwCtfEGKrbYeBZQoux71OpjNGtC4BUxcYXGuEbiGKUGSKKIKDbY2SfQLIwyvsURBFWi"
    "SYurLKG69DL80dk0JxNMrYib2JRyGyfLOBcSFKo0JjZli2/y3cUR2f1soLe76eDp7c1zjDfrs3D+"
    "MOEbziUeX8/g4gGCgbnosodXb9DYZwhjPMoSUCl6TMUxY42YSsHHU0UiU0dCjauPUh4p4aZmsxMP"
    "P2lQqVRRPoyP19CzKpjBCoOjIRhLXWlcAQIHU02DHyoER9ELGHMx2kGf8/ECw9ZaQuhpqn2aLVMW"
    "7fdTNIrxKUO5GDCgHRu3VdFDAd58TewJyZwy/eUC25uGwBoKZY2LIEqgEcVgHJ6BcsmnlghxfRDf"
    "T5HN2lQDzw/wEKKojqcV9UaCf9wC+ssVJnfWmTTQpw06PJyJuEYoDu/Ew6iNO6amtmI3NfDCAbxw"
    "iHU2o8N1DSfIp4cS9mh1cL4baFdS2Bm0X6S5fTPjj18HtknjWXAmJo4TPE9jrMMCgV8ChCSuY5zF"
    "0x5apenV9rwFm1YCORUgOsTHYJzBSJr5C/2Ata0KcZPgDY1gtm8ncRYvS5t6WogSgyiPUAvGGhJr"
    "8L0AjCFWFq80BLWdREmEJxpPe2mCKGP5OOfQXoByhsjEKOURzppDsm0LSdLEmBilfER7iI0xzuAc"
    "aAFj0/YvktU3uqyVjIifjqNxMYlNcE6jtYe4JolRaAFr4xT69Ur4XonA8ykNn5yGoi5JeQrtiuTW"
    "AbS9441eZAHQCrEdNrA4wUnWENklKOfY8MQ6HvjZj0Em0oQGkvXnyc34aRV32CiNElqoosrAkFZl"
    "Lh0yBs6mP7cyks7lSJsaZu0HW57orvPHdcJQG+eSMFlHsNIgBBXYuS7tHJ6vQ1BhRl+z3fWI1sLo"
    "YtjyZPoa0tOKRyRrHpEraMm3+XRxJ+mT9S1EF1O2VOv0tp4jWd8CHQAllpw0iqeyIRbZVBTJV2Lv"
    "eR+g0827vZ/tLh2WpNmE+k6CQg3lJjPUSmNto2vES2tTnI3aJIc2yVEVOggYunuBvWrWHcR0F3yi"
    "8SkTq+09fpHNVQZnAuBadC2DaIW4cazektrZNj1dsgxi1O7n134vG+PLALE/BjruJnS0iKTJRA/Z"
    "I1dr6OJ2j+D28BqJEB13l/Y6i6i0jZxSEShFUZq4JOqiYnVmkbgXlAzyXnBRCPketqm023gKL6uL"
    "t2YSa5uppCaNrDlkJgSiERXgMliz3T5GSDeqTZuW9vNaXD3XqsGnp1giGsYlU2n2Mc+3a2kVm+Qq"
    "fiQbO2FxqoCNpkDXexpLNLr6DHYKMC2uOYmLJ7J7yTWREEGkgTP1bHNt7rRnrWlc1FNIY9OuZWny"
    "urtdrMoGVukCShdwUSOl1vW2oW9XBsqe1QAojVK2HXfSij2zsW8mrqNMgsJgbYTYKBvNErerdaTF"
    "YWv133M9wiWZqm2f8ly4k9jpCYnMGRLTTFu1YbuTLVZl+QeTa8WW2k+tQ0Q8YpdA4nC57sVp94+k"
    "/XqSEyoXT3X6F3Q5YAISp21hRXL1Di0BkJRhlG+e7UBLq9imxYBObbuYjsYUZVFJyq1M5djS25vf"
    "4XbTBXwBGqADOuSnYKTyZ+IplInRaSfrbPpW1t1aNDiVWj6bzQDES50ll2Cz09Ypy7OdopBWvCmS"
    "KySR7tEqGa2anjk8SOa3OANOoVSAJwFRnGBtBS8o4bk0y5nY1A47bAplO9NDZUtDL5VRucXF3U2e"
    "RRA8nIu7Gz1nfoTQCd1ENFp0uh5Gsv4eCpXVIFqXZgLTuYYJWhTKJpikvotScLfnfQAFPYSQTOid"
    "Q5QQxxO4uIGnU0IFLh3+aEibMWipUvXm0R/OxVFk0+QjNJKteB54WpHYZruRcuc05tgyuW6fnVGB"
    "2eKZdKZPuhCSOkcC4lQbNdPKw5mQuLYP+w0czsLZBzJRm2DluipN9RRBuIPYZHCejTNzYrra24JN"
    "T3g2X0i6jqBkPmbSIwAtbz01A57yiRMhsYpysICR8iKSZIKpZBsT8XoMUynBBZWtg8PXRcRGJMlU"
    "mzTS0jwulxzcXUbQCwgDbYcPkIMjRRRxnLZHD/1+6s6hUDhrCdUQ+w+cwcF95zAn3J95IwOUJaBc"
    "aPBEdB/fuOtbbJi4kzBwGEymBk23Z+1S5g05DZAehfTMSMY7dFgk61snVrIuXhpfiiTNfuYWT+Dj"
    "l7ybM044jGYNigVYPzXOx/7pm9y4+hsUCpbI1HGSIMS5pky2PRTSJan6dy7p6fUrnYKQnjZf4tIT"
    "rkUTRQX2HzyRi17yehb6x1KLPTbs2MHW2hY2x6t4YvxG1kzegqWByhL8YVCFuI4xDbTyuxt1tR3y"
    "PewDWHrepGvShcKYJkk0TjEcYMwpHDGD4WL27zuLgCIrd1zD7fEWkjUxoerj4MGX8aYTXslVb/kK"
    "f37tX3Pj2v+iGBpiZ7GYnu7Z3d0+JD94B9odOqTddjW1A2I9POWRJAWWjJzHd5Z/hKefmOT3P30l"
    "G8emqJYHWXbWEq788/fy/i/0ceV9X8EPNxDbhO6kd67llTWdQk/Jz3jJtaVrvX+bva/wVYFGFHLh"
    "Ie/mAye/lzvXPsZ/3vuvPD11L5GtowQGgvmMFA6gz5vN01M3MhGvR4lPMRgmjnZiTBOtArpni7kZ"
    "5tLuCQ1gTS7enD7/wzlDs7aNol/Fuph5leOp+gt4bMc17GyuSUNJSZMiWkqsf+Zeblj7n7zpkHfx"
    "r2+6go/8YIjvPfIlCkFMbOL09JIbuZo5Sm2vu1OMkNUd5EinGQ9BocGFDMih/OcHPsStd63j/V/7"
    "OwwPUyoMomvDrPjqv3PPQ2/nn//0LWz+0zFufPqLBJ5P4pLMl+keGytd3UR7c7MyrdoJNL4KaEQD"
    "vOvED/Hel76Zy6/+K36x/r9QMomvFdbVsBi2NO+BCWEw2JfR0uEEXoVt9ScoBcM06lt6HL9ct9Bp"
    "TWP2kAloD1LuLX6VFIuu1Z5msHI8w8UlWBfxxM4fEZlxPOVlgtMEDA6H1jGh3+Rbj/wdCZa/ed0f"
    "8syX13P3lu+iVQPjTK73XisfOUPvAEc27NHkNkqlZE2tiaIRPvOWy9m8aZLLvvYJ/PBRsJMYmoie"
    "pFLcxtfu/CKLvz2bv33HJZz5sRupuwcQ10i7doptf8Z0cqjpxgx6BSBrgt/63RNNIy7y6iVv5UOn"
    "XcLFX/8Iv950JaXAkJhm1hQqjYp0Bm7tiFZRs1sYKhzAYLg/Rb+fTTvv6+rA7tj1cI49nA10M007"
    "RamAsYlVVPQoQsjm2v1Y10xdGdfE0cQSYV0D6xoktkGU1CgHda585PN8bcV1/OUFH6XgFiF4WeRA"
    "rpmzzXXmcl39g6S3o4gDLYo41izd72x+56XH8kf/8Y9YdS/G7iS2Exg3Rmy20IzGKAVr+avvfo5o"
    "Ct535jtoxn3oVt8g1zr5rfnCZhf90Nw0EqzCw1rNwvJxXHH+B/jT7/wDv950FSU/IjJTJNlaOBfj"
    "XIIlwhGhRDCuwdb6YwTeABV/mPGpp1HKazvIkp9S+gKjgBfWIcS5aZ2CHBYRj6n6RjyVELuJFLfG"
    "4Uids/TGExwGSwIkWJo0zSQFb4LP3/JZjEl4z4nvIUpCNF6nC2lvHVS+qzI267CZtaR0qWus0Iib"
    "xx+84o1856Y7eHD7zwm8Oomdwro61tVxLsa4GsZNMc59/O33vsVbX34W8wuHYEzqiXdT4HNC6FqD"
    "pGeeA4QTtHgkdog/O/9yfrnqLr63+isU/QZNU8O6CEeCa3UPITesOnM6xSkiO46xE9SbW1DiZVGQ"
    "67I+HdOc7On+AC53+LolTotHI95BI9rCYHn/dDBCO16f3kbVpQw9HDGWiBqP8Xc3folLjn0t+xSP"
    "SSndbQSts+HTtEDbLnc0glKK2CheNu9UDpu/mH/92TfQajOxqWNppGXdLsZlJy6xNUKvwbUrr2bD"
    "ph28+YTXkthCqgXyLGiX+kL5OeTS8kXzo2GdpB5/AqfOP49j5x7L537+T2i1hdg2UpVPa7Zgaura"
    "6ySdWknnLAPl/ag3txLFkyjR05tF/zbY3m4JgLEYY2bqEZarDbTsmFjJrMpixHnpKcy/Te/JxeIw"
    "GBtR8Aw3rrmeRzav490nvQ/jqm01PP29cj39spOY94s1GuuqvPW013DLI/fy2MRteCrCuEY7XHRZ"
    "Va5zSQreEFFnFf9x0w+56JRz6Nf746xu1x52d/fI4fFdc+9z0KzTKObxgbPfxw/uu44np+7CUwnW"
    "xTmqPLnh0109VlDiIc5jpLI/Y7UnurVhq/qc7p7Ce9QE6Jzfnx+J22kSYdEqYPP4/QwU51HwBtMi"
    "zRYKNo3KLF3duS0RVjbypVu/wflHnsai0pEYq1F4baRbXKdtTPux1oJl5dgKTWIUi4pHcOL+x/LN"
    "265ByXYscWeaZ06LpBU3FmNjApXw44euQ8ceLz/gTGLro5WfCbHsgoxNVzsbcTpFG63m9IXnsqh/"
    "AV+786sU9BTGxZ2y7pleKct9iKSCF3pDVAojbB1/OB12lRM25+i6p/T1kj3vA0zrEJJrIO2pkB31"
    "J8HVGa4s7mhNl8KbKeybngJrsy8XYVyTyNbQaoJb1/+MNVuf5c1HLcM4PxUOG2NshHER1kbZ89Ln"
    "WhfjrG2/NmIwLuBVR72STdu38Mt1N6CkRmwaGNdMba+NcK7zZbJ7cNJgY3wPP77vFt689EIUoxgb"
    "Y2ySvU9MYiOMbd1H95dxcfb5HIpR3nnyJVz74M94pn4vjjqJmcLYBtY103uxTUz7s8RYZzKwTXAW"
    "hisHYV2NsfoaPAlzoaebZgD2OBTcNftXpKcqNRt2gCY2DTaN38XCwRPZNvUEoYQY18BlvABBpc5M"
    "zldunbBAVYhjzQ/uuJXfP/s1XHXfdYy5BzBM4cRmzlLvQDWh4A3hSYgixKOAqP258Jhz+OlDd1Py"
    "ywTBosz5S7J+RjGBrqBVgUAXMnWrUBJAMsiKhx/hohNfzsuGzuKJqQBUDSPpZyh6gzS9YayLpjWy"
    "SjWQB0mVo+e9ipfMOYi/v+6rzCnOR6kmTTvQNnt5dW5d0oZ0xSm0FMGF7Dt0PFvG7seaBM8vYHtm"
    "RLueHA17FArWKhf2u+m96xxYsXgqYM3OWzn7wNfx6MZhJpo7MDQzAbBtFdce5NgqhHBgxOFh+NHq"
    "b3Npcj5nLTmbrz5wJ4GuYVyWqHE2l07LZuehaJoptCQ0DBw/+xBG+/r57j3fx9pNNKIpEqbaIZTN"
    "YnjtEprJRCoACIKPT8ztz17L6o2v45XHncsnfn47gbeFxNay8u6QZjKeJWw6eYL0X4AWj8hUeeNJ"
    "r+RXz9zHA2O/oBTUieMGxtWze7BdfdDagi0KwceJR9mfy2BpLvc++0VEe6nQuBlKAnY1TmRPEEK6"
    "Z77PnCvwVMi2qdWM159gpLyYrZMPgopTLdCelSfpAvY0O9BSR0tIZHbw3ftu4DXHnsKX7tHEZgxL"
    "nDlrbtrbe6pAPdmRhV3CBcedwYObVvLoxC2EaitRPIWTJJcuTp0/rUKayY52WVaK14ckNuHqu3/B"
    "28+4gE/dIEzabRmWkWYAa8mO3IiYTmGepogjZN/iPhw7d3/e91/Lqdu1RM2UXGJcfYYmj7mSezRa"
    "ClgUB44cwWT0DDtrT+N75VYc1hNq9uz9nuwUaoydwfuf7hW0buaxrddy0MhSfNWPlhAlHko0IhqV"
    "feV/VqJQWcdvT5pcec9/M6c8izMXvBJLiK/ClH8nLc9co2g5iRolPuIKDKp9Wbrv0Vx160+AHSnB"
    "RyR9fdL3QdK2LpKVraWv03FWA6lz7f0/oGyKnLHvUqwr4GX8gfRevZTnn71v+hXg6wBLH2879VKe"
    "3L6WOzb/mEC1Wr6r9N5Fp88VL0sLe+3HtKTVQ4EeYr+h43hi6/Uz9AictuKdM5nsYRyg25mXXH1o"
    "nqZkCXSZp3fcStEXFvQfh7WktrHLd2cGepPDOoNWMc807+fGVb/mbSe/HtwACj8lf3QFPhkk6lIc"
    "InYeLz/w5Tix/OLJn+JLGmI6Wmlkm5ts2kLTXE9YF6NUwob4PlY8cSdvOfHVwCDK5VrJzsCKFhTO"
    "+sz2D+X8w0/m3391NQlbMq1hdjlNoSuOER9nPRYNnICSmDU7foWvi92+T8+kCOfyfIA9XBza0b6S"
    "axrtpoVEIqkzuHLLDzh+wTIUJZSEGTooz+FkmhQXcAlKxvnqbf/O8QcewXGzXkFkBd011KETDCp8"
    "xHloZnPxy97IT1feyg6zCqWSjCdjp6vOLifa5aaf2Cxcm+Cbd3yXlx70Epb0HUfiUgcvRRjzzp8g"
    "aDwV0HQFLn7pWxmvj/GTJ75DoNLP0tv9tNtxk0ybeXhSxJM+jpp7Hqu2/QRjoq5hEeSGTbsZX4v/"
    "oTDQ9W5698Rb5xICr8KDm35ANSyxePAMrFUofoMQZNJsncVTCffvWMEvVz/AZadfgnMj6Iwp20EB"
    "sg7gEpC4gGOGT2XJ3IV8686rUdQxWX8ClxtwkydxSBeEkhMCZ/CV5a5NK3h0w1O87cQ3YdwQviqk"
    "fINc5JL+7CEuYI5/NBefdC7/dst/02R9Ok4O29NLLS88LTFOy92c1SweOoOS7/PQpmvwvGLmsPZI"
    "bL4ou8sR2AMmYEUvHYzpfeuF7o5XLguIYlvntmf+F6cvfCuBjOBJMa2xd6oHDHE9qdV0E5Rs5ws3"
    "/CtnHH48xw2fSWw1nhQ6zR/RKAIUGutGuOzlb+eutQ/w0Ngv8ZTNwj47zUzRBR65nCva2qpUEyU8"
    "wz//4utcdNrZHFg4HuN8lBTTDUenQJfz0ly/7eMdJ/8edVfj6of+m0AlGJu0WT0zHNmsxZyHwseT"
    "EoGaxYkLL+RXz/wbiWm2CSG9SmvXvcP3YI8gYwwmP66F/JyATCSkAw9alxB6fdy/6QdY2cpL57yR"
    "xCi8bAFB9RgPl/MpUtXpq4S7t93Aj+69mU+++oNodwCeKqApoAjQ+HhSxsQDnDhyPq84/Ei+cMO/"
    "oWRHGjXQkyVk+si1rq7nrQqozAwEyvLT1deyasNTXH72uzB2PqEqoygihCiXOm3WFji0cg7vOesC"
    "PvO9f6HOGpy08h2tEso8WC3toVriPDxVwiaa4+ctQ6kxHtx0DYFXzpJm0zN9M9ZmyR5LB69ok26c"
    "nVmapzUryKklEY9rnryCM/Z7LSPBwSjCNCponyLp4f91BMw4g6/G+eSPP8uB+8zj/cddRi0ZpKQH"
    "CWWAUPXjSx+BPYS/uegPuea+W7hz68/xJEm5C+Km3RLPtZQuf74yf0TWs/y7/8BFZ5/EOfMuot7o"
    "o+QNEEiVgu7HkyqBO4p/vuSPufmxu/nhk1cSqhjbmniW1zouz6pKATEtAdoVmFU8glP3fRXXPP6p"
    "XAv66RO5ZFefZc9VBy9ttwkUkV0sqHTbqIzibJ0h8Iqs23k/D+74by467KNgBvBVBUWQlTqpnvGs"
    "5NK8BiWWTdH9vOern+ajr38D7zn6L4iSw7B2FGv2oWDO5Atvupz+fs3HfvDXBGpnR/VneJUSQSuN"
    "RudIWtIGobTo9O/Ky/kILtVCYrht4zV89sqr+fcPfogz5lyCjQ9B7DysWcSAOZtvXPw3jPQX+MA3"
    "P06gtrdBKzeDt99qmSOSmq9AVXG2yrLDPsRD265i7di9BF4pFzlId/+o3ti/1UF8zwFBLQ1g8jU8"
    "uamW0vNYzleRFPAJggrfe+yvueKkszhz4SX8Yt2/EegyzayqCOkMPehuQ2tJbJOC1vxi3VW8/z/n"
    "8g/veg8vP+hofvbg3VTCYS469UQGZzku/Nwfs8Peh5JmmhNoe/46BWBaVV9SyE43mQ8gJLbedS6U"
    "BFnFrSFxEQU9yedu/iuGqn187U9/j++vOIW7HnuEwVKFN//OqTTsDl739x9hp30QJMKSTgHt1O7l"
    "kEtU5vX7BKqEiUOWLriEWVXNF+75S3yvjG1zEulec6FnlLx0KOmypwtDbH4QpGvbMdfFTZNO4Wrb"
    "NAhK0uLFLz/4Xv7ihGt5esdjPD31C3xxxEyk3Xclg0Kd7RExQ2TrFPQ4Vz30Tzz+8ZV86OxLed/r"
    "zsfGTW5+6E6+8qXreKxxHVrVSWyEk9wcHbGcsvA8Dpm1hO31ca5f9T0mozGUpNm1weIIFyx5Azgo"
    "+kV+sfrHrN7+aNqcmdSzTmwDTz/Dn//oL3joyY1cfPbpHHvY6TSn6nz7lz/ki7/4JjvdAyjVIHLN"
    "VPtIj5ssaXeRVsjnSwltqyzoO5lXHvZK/vqOCzON42G7uqrOyMHriWPdCxobs3t1AUojKpmhTCyv"
    "5Xptrms7daFf5OkdD/LtVR/jAyd9kr/4+bNM8nh6yl09+1gqD9FkGsbiSNJsoY65d/t3uOS/f8Ug"
    "84ipMcEOhr0QUeMY18zIpOmCaNHEtsFFh1/KpcddyMrNO/nl079gKppE4RHqIh8/65949SGvoujB"
    "jx+/mase+jpKVBcr2LqIxAqefpofrvwKV678OhXKNGkywToKMgGqSeLSGj/JNs51TRzRpI3sPDwp"
    "EcoAJQ7hD0/6KN9/+gqe2nkfoV/F5JJMu97UGaaKO/Z8eTgtypX0jAnq4QfMBBsYF1MMB7l+9b9z"
    "8OiRfPikT3HFzZdT8ISmE2LXssy2PVFEcuCMIyaxBq08lKxl3K1L0y8iODVIYho5vDzv6/vUojpb"
    "Jgw7azvxVICvCjjgY2d8gVfsfy5TDbjmqev4wI9/l0YyhZJO8iWtfRQsTWJrqIbbaZodjJsEURDi"
    "SGyUmZ0MmZTuoc4iWZYQ8KREQfej44X80emf4LGJ7/Hjx/6N0O/PZRjdb4JfZtQHexQIsjZtFTvN"
    "2Xc9pFiZaaZemv0yLqYQ9PMPt3+EqPAYf3zCX0GyiIIexJNiBhR5XWPeOqTnVBMY1yC2UxhXI3ZT"
    "RHYSm7GN200sch3MWyQNsTqL2UNwig+edAXnHHguNoFbn17BR66/lMTGaQVR6+z2QIeOmMTVic0U"
    "hjqJraX3QtR22qR3flKWItaEeFKmqAcgns8fnnAFqvQUn7vtjwj8aua47mI23HO4+sL0WZd7Nhkk"
    "u2al5Vk7u3AicDhCr8RHf34pw3N2cvlLr8DFiyjpWYTSn8X4Xg5pa3sW7Vx6SiyNM5y9hbXbnD/S"
    "bROtSfMkzghx4njvcX/Omw+/lFrTcse62/nw9ZcyFU2kmiordhXXCx6l752SOVMeYxtroJcq1w3x"
    "agr4UqXkDUG8gD847hPsv8jwkZ9egq/Ddp6iO2Jwu5ft3dNNonRrYETX2IruwYnuN+kjlyJ8olLn"
    "7APXvYlFi2I+8tJP48eHUNAD+FJBS7GN8LX5Al2SZ3NM4zTkdHnvIStba8UnrakstWaDNxz6Lt52"
    "5IcYrydsn9rBp276IyaiMZSkPZDazm1vZNsOtTrv3VXR2wXzSJax9FCE+PRR1ANItC8ffOknOe6w"
    "Cu+7ZhmxaaKU2kWyiOcAr2bY+T2lAZZm3aIL5aJTeYDC/aY0wfSe1i200NoErTVxUuPdP3w98xbt"
    "5BNL/4pidBQFNZtQBtpCIC4FjNL0rfSCz11Qcm8rW5cb+dqIYTCczbJD3p11GBWq/gCn7HNO2vWT"
    "jPvXStC4mdfZ0e2hi3Sr+87GB3gUCaWfkp6FHx3CR0/6DEccFnDxla9isjGGp7yUaraLjX/+xz8N"
    "A/1i0b3oAjA6OuoAZg0N1bTWiBLp7lPodmH3Z2ppmcGaGc/P05rE1HjH9y/CDt/HP77qL9mXVxPa"
    "eZTUMJ70ZSbBTzN++cHQPbTQzli17ARKJ9libTbcyypC8Xho832s3bkacZqLD/tDDho8CutsxkfI"
    "b2r355JpKWDZxeaHeJQoqCFC5tAfv4y/OeczLDhgJ2/+r1cz0diB72mMi3pU/3Shc79hQGhHDoVq"
    "qVTbYybA84qbJSVWqHwGjRmY6t3nXqYLh8tAHhehtCDEvOe7b+Wu5jf5h9/9A06uvJ0gWUJZzyKQ"
    "KpoSmrCjDdp0c+nKqtHTzbRtAowjTsAj5JEtj/IXN72Pbz30ZeLYUfGqvPPIP8WTICu8oGsSl7iW"
    "cHWjedKz8So79ZoQnwplbxae2YdDgzfwLxddwdbCzbzpm8toRBP4WpHYKOe0uhnXcnoBzsxiIkrE"
    "OWcLhf5tAIceeqh70QTgqquucgDDw9V1SqlIKSXTPL8ZRtm69ga4aTkCl3OsrEs7YpUCn49f/yf8"
    "/UMf4pPveC3vOfSjlOOXUZJ9KOohPEptbZDmEFpCoLpmEcg00knWlMLCeH2Mz/7qz9jR2MFd62/n"
    "oY0racaO4+eczvn7X0xkG3gqyHH1JCe/ktG38mng1on3M1tfoaCGKOu5FOOjef2CD/JPb303P3z2"
    "87z/B+9BS4LWdOMVPeP3nHvukHomSrpSGj/wt538qnOeBbjiiivci6kBHMDVV1+93vO8NZ7n4VwL"
    "7JMZcgLdPIHpFLJcMUXm/RpijIupBEW+fc83eOO3X81JpwpfvvCvOdZ/K0FyECU9j4IM41NGE6IJ"
    "OqYhs98tX6Ezh0eyFmqpEzjemGSsuRMyXt+3V36FWrNGMzJcsuTD7Fs5NHMEddukSIbb5yloCg8l"
    "PpoATRGfCqEaoOTNomAOZF/zWv7q7M9w6YWH8/4b3soXbvlbSp4Pktb/5Td/ump3z7vIIztONgh8"
    "PM9f9dYLLhjfHVhgdwRAi0gSBP4dYeinU1emkRGm89TbLczongct+RrDrCGEdTGRa1AJizyy4V5e"
    "+aXXcEv8b/z1+17Ph4/5DPvFF1KwB1LSszNBaJmGQk4YvPZGtYWBdB5AHFusSXuKWeeIibh903X8"
    "7OkfoZym6g3ze4d8LO0NKF7GEdQ57N5L29pQQFNEU8SjQkg/ZW8OZXsQg/FpvOWAP+Yrl32Qsf5b"
    "OffL57Piqeup+AUSGu2qoHwZmIibnkTh+cR8HRe3WAhdGAa/tGkIq190H2Dp0qUCMDg4cm0YFkQp"
    "Jc7ZXRYlueeMT6fPHaQdXiVEtk7oK7Ru8IlrPsElV76Rg07azr9e9n4u2e9y5iYXULaHU9JzKaoh"
    "AgYJpIpHCiSlX377pDpAE1BUioBSymlA0s2QmKtXfZmNE5sRK5yz4NWcv/DtRLaBJsxew0e5IE1j"
    "U8SXcgrjqhHKei6hO4CB+DReNftyvnzxJ3ntq2fzJyt+n/dd9ftMNjdS8j0iV0sLQbNC2U4NIe18"
    "ynOfercLA+BQWqtCsSizZs26FmDZsmXPOxLYnchRALd8+fKBb3zjG6u2btk2XG82kLb8tihhMmPY"
    "0k4juxnqyns4fm0ARVKmTb1psRR47bHn8wen/z6V+hKu/dlKfvL4T3mG+0nUJvrCAluiVRjXSKtz"
    "iLvauhzUfzTzyguZiuus3H4ndTOFL2kj5ym7hYP6j2VOaR+sS9gRbebB7bdleQmXFWr4qUCIz6zw"
    "QCajBsoMMpujOWn+yVx42jEM7DPGv9/9H3z5xm9Qs5vpCzxi28QQdfoeOdtdFfd84vtp69VVqW0r"
    "5ZIMj4w88Sd/ctVh7373ccnuwELyApJHyRFHHvn5jc8++4c7do4lOOflekR0ZgnSM8tOdtXCRGYI"
    "FVthXOrktU7iZBxTUrN43cnncckxb2GOO4I779nG9XfdwcbGWp7mTiK1EavGMTSwtpkxaoTI1jFZ"
    "TUIghZS5rNIxtU2TVgwlpFVDCiGUYpqaEh+tArQU8WwVZYbZh+MY9maz9OBjOePEgzGjz3DlPVfy"
    "jZuuZEtjDSWtQSUkrpmVf9uczScXoewS8d+dPUlGR2d5Q0MjH3744Qc/u3TpUm/FihXJntAALF++"
    "XF1xxRXu8j/7s4VXf/Nbj2zZsiVsNiORrA3Xrr1WeZ4fTLo7fOS0QYt1i/WYSmICBnjFkWfwppMu"
    "5KjBU6lvLPGre9bxq1X3sWrqUbazjgabMEyAV0fEtqHcdMp5QtkbRiPsTDbiU8hSx6m9tw4wBZTr"
    "I2CYIRayf2EJLz3gGE4+al+G941ZNX4/V/36e1x710/ZkWygIKC9lL9gZ6CCd4OkzwPwkR5VIdP6"
    "NNhSscjwyPDW8847b8kXv/jFnbsrQbsNHi5btkxfddVV5sgjj/70xo0bPrpz53hijPFSEXiuRkW7"
    "m6+SrtrBFgtX8PBUiDjNVGxwhBw0awmvOPIUzjnyFRxQOpR4Z5V1z9R5dPV6nnhmLRt2bGKH284U"
    "YzSpY4lJqFOmikeBnTyLTwlNgE+RAhUGGGJedTYHzFvAQYvmsf++A1RmN3gmeoKfrbyJ6+6+iYfW"
    "P0zCOCUlKG1JXBPjOnmJ3hBv17k7Nz2t+hvXzaGUSoaHh71ytfr7qx9//J8z58/wglZ5954jd911"
    "V+Hiiy+599lnNxw0NVVL5y6+sDY1z/ctO2ydLPbWkpJCo9jRBDQlFg4u5PglR3Hi4cdwyLxDmVvY"
    "h9D2E417TI3B1LhhYmeTKLbYOIFE43zwNZQrRar9HpVBKJQhKU2wNdnAoxse4/ZH7+aOR+/jyU1P"
    "0WCMAEfoCVZiEtvMkkO23f9oujcvv8HWy24dXuesGejv1+VK9ba1a58+NdXCzwUpvngC0O4Z+Zpl"
    "y46/9847b926ZauK4kSBE+d2JdXP7d/Kbt1qBsI4lZVW+Wn3MaeJY2hkrVkD+hgpjTB/dDYL585n"
    "wfA8Zg+M0F8aoFQqUtBFVAIREbV6g/H6OFvGt/HM9mdZs/EZ1m3awJbxbdQZB2IChMBLk1hpqXtG"
    "/Wp1+ejy5J+fR++mISduJpi3y1+21tq+SkX6qn3jAwNzjn344btXd1qa7/7RekFXyxScd94rf+/R"
    "Rx/+2saNm5I4jnU7G+xeeL3Jb85rSwfkEemYCJexbFUa/uEEkzhi50jaEYrqjHxHt9lKtDcx/Vln"
    "0JLnCaLSDGarpW0nJW27Y/qexNS0Ck66nWO3Czq3MEP3uTaiaW25XKKvr08qlYELVq165McvRPX/"
    "1gKQYQPezTffnJxz3nkfePThh/9+8+YtNo4TnHNKXog/61x3tNBqHCwzm1CXK83Km4hWsVhK7sx8"
    "h6z4tNOuRtqxeObC5uhf6Ya7rE6x07rW9eDzbuYO6s/Hq8/LiDzvLq2mXC7rUrGEw71l8+bN32xF"
    "Zi90D/VvIwBr1qyxgPfEqlW3nXnWWc/WpiYvaDQaylqbOOdUp6v48/yAuUn0uSLDGdXmNLhZXNcg"
    "BicuI42kHclaHTjSTiCt791fSfa4dRGGuE04sRkBxfUIwvNrzrirnXYd3sLz2HoRMZVy2SuVy7Ww"
    "VPrdDevXf3vp0qXemjVrkt9mD38rAWit+NKlS73rfvzjX5933qturddrpxmTDBtjnLXOtvS0yG+S"
    "dNdFgd+lopJdKzLpHWUpM7B52o5ah1nUQujIbXT3vGM3w2mW51GdIbu8ZycZ6CXPETOlaUITBIGu"
    "9vWpQrH0YBCUXr12zZO/AH7rzX+xBIA1a9bYpUuXetdc84PV//qtb/7nqodWVp0zxzhntbFWnHOJ"
    "c628WrvL8/TsukzPt+9KHETyP0ubmCS9h03o6TbqshKtXP9f6S236eYlTyPF7nI4g9s92zsD3TtL"
    "nVgEGwa+7uvrU6VSqREWi5875uijLv71r+9Y+9vY/BfVB9iVYygivO1t7zzuvvvu+dD27dteU6/X"
    "ivVanWYcY42xmWb4DaSXHrRMnodjnW8b09VJ67mfLj01wsL03ss9XZFz/Q+fJ4b+HK/TekOlRGut"
    "JQxCgsBHe954EBSu7Osr//3KlStX5iOwFzPA3hNBuwKMiOLt733P4kfue+DC7Tu3n9+oNY6MokZ/"
    "FMVYYzJny3bPeJhW7CiQq4Wf6bG8AOxKrbaqjnrTUZ0JnLny9nwbnF1RcNqKzGVzCWZA79rTUbp7"
    "e3dpvEyVeZ6H1hpEtmml7vE87xrf7/v+6tUPr+sZ2eRe7M3aI1cGG0tLVWmtueyyy2Y/+OCDB+/c"
    "ObE4SeJ54+Pj1VqtplwGjivVEW21yzzBb/5E0rVHKmPd9PzeHvKhOq1uezOyPbcg8hz0POkVxBky"
    "vNPlSJw44/v+WOiH68PQX1WpzHn87rtXbO0d2fxinvr/0Wv58uVq6dKl3p4Utv8Hrw5YsYev/+lN"
    "kWXLlqnNmzcLwIoVK/Zu9Uwl0ew5UH3vtffae+299l57r73X3mvvtffae+299l57r73X3mvvtffa"
    "e/3/8fr/AKk4KmN1/CLYAAAAAElFTkSuQmCC"
)
# --- icon end

_APP_ICON = None


def app_icon():
    """The window icon, decoded once and kept. Returns an empty QIcon if the
    blob is missing or will not decode -- an icon is decoration, and nothing
    about a measurement should fail because of it."""
    global _APP_ICON
    if _APP_ICON is None:
        _APP_ICON = QtGui.QIcon()
        try:
            if ICON_PNG_B64:
                pm = QtGui.QPixmap()
                if pm.loadFromData(base64.b64decode(ICON_PNG_B64), "PNG"):
                    _APP_ICON = QtGui.QIcon(pm)
        except Exception:
            pass
    return _APP_ICON


# ============================================================================
#  main
# ============================================================================

# The identity Windows groups taskbar buttons under. It has nothing to do
# with QIcon and everything to do with why the taskbar showed a Python
# snake: the app runs inside pythonw.exe, and the shell takes the taskbar
# icon from the AppUserModelID of the process, which for an unbranded host
# defaults to the host's own. setWindowIcon does not touch it -- that is why
# the title bar was right and the taskbar was wrong, which looks like the
# icon "not applying" and is two different icons with two different sources.
# Reverse-DNS-ish and stable: change it and Windows treats the program as a
# different application, so a pinned shortcut stops matching its window.
APP_USER_MODEL_ID = "Netline.bladeRFWorkbench.Analyzer.1"


def claim_taskbar_identity() -> None:
    """Tell Windows this process is us, not the Python that is hosting it.

    Fails soft on purpose: it is decoration, it is Windows-only, and the
    call does not exist before Windows 7. Nothing about a measurement should
    depend on it.
    """
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            ctypes.c_wchar_p(APP_USER_MODEL_ID))
    except Exception:
        pass


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    force_sim = "--sim" in argv
    lib = None
    if "--lib" in argv:
        try:
            lib = argv[argv.index("--lib") + 1]
        except IndexError:
            pass

    # Before the QApplication, so the first window created already belongs
    # to the right application identity -- a taskbar button that has been
    # grouped once is not regrouped by a later call.
    claim_taskbar_identity()

    app = QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    # Application-wide, so the message boxes below carry it too -- including
    # the "bladeRF not connected" one, which for a first-time user on the
    # simulator is the first window the program ever shows.
    app.setWindowIcon(app_icon())

    note = ""
    src: Source
    if force_sim:
        src = Simulated()
        note = "Simulated source (--sim)."
    else:
        try:
            src = BladeRF(lib_path=lib)
        except RadioError as exc:
            # Do not die on the doorstep: show the reason, then come up on the
            # simulator so the interface is still usable.
            src = Simulated()
            note = ("No radio found - running simulated. Plug the bladeRF in "
                    "and press 'Connect radio' under the Radio key.")
            box = QtWidgets.QMessageBox()
            box.setWindowTitle("bladeRF not connected")
            box.setIcon(QtWidgets.QMessageBox.Information)
            box.setText("Starting on the simulated source instead.\n\n"
                        "If you plug the radio in later, press 'Connect radio' "
                        "in the Radio panel -- no need to restart.\n\n"
                        "Press 'Show Details' for exactly what went wrong.")
            box.setDetailedText(str(exc))
            box.exec_()

    win = Analyzer(src, note)
    win.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
