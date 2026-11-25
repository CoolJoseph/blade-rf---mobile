"""
Virtual Qt core: the GUI event loop, signals and the change counters.

WHY THIS EXISTS
    The desktop bladeRF Workbench is one PyQt5 file. On Android there is no
    PyQt5, so instead of rewriting its ~9,000 lines of Analyzer by hand (and
    re-creating every bug the desktop already fixed), the ORIGINAL file runs
    here unchanged on top of a small "virtual Qt": widgets keep their state
    in Python, and a WebView draws that state and sends taps back.

    Rules that keep it faithful to real Qt:
      * one GUI thread runs every slot of a GUI object (the Analyzer), exactly
        like Qt's main thread; worker threads' signals are QUEUED to it.
      * a modal dialog (exec_, QMessageBox.question, QFileDialog) runs a
        nested loop until the web side answers, like QDialog.exec_().
      * slots get as many signal arguments as they accept (PyQt behaviour).
"""
from __future__ import annotations

import heapq
import inspect
import itertools
import queue
import threading
import time
import traceback
import weakref
from collections import deque

# --------------------------------------------------------------------------
#  change counters -- the web side polls and only re-reads what moved
# --------------------------------------------------------------------------


class _State:
    ui_ver = 0        # any widget property changed
    plot_ver = 0      # any plot item changed
    struct_ver = 0    # widget tree membership / visibility changed


STATE = _State()


def bump_ui():
    STATE.ui_ver += 1


def bump_struct():
    STATE.struct_ver += 1
    STATE.ui_ver += 1


def bump_plot():
    STATE.plot_ver += 1


# --------------------------------------------------------------------------
#  the GUI loop
# --------------------------------------------------------------------------


class Loop:
    def __init__(self):
        self.q: "queue.Queue" = queue.Queue()
        self._timers = []
        self._tlock = threading.Lock()
        self._seq = itertools.count()
        self.thread = None
        self.errors = deque(maxlen=200)
        self.quit_flag = False

    def bind_current(self):
        self.thread = threading.current_thread()

    def in_gui(self) -> bool:
        return self.thread is None or threading.current_thread() is self.thread

    def post(self, fn, *args):
        self.q.put((fn, args))

    def call_later(self, ms: float, fn) -> list:
        """Schedule fn on the GUI thread. Returns a handle; handle[0]=False cancels."""
        h = [True]
        with self._tlock:
            heapq.heappush(self._timers, (time.monotonic() + max(0.0, ms) / 1000.0,
                                          next(self._seq), h, fn))
        self.q.put((None, ()))        # wake the loop so it recomputes its wait
        return h

    def _run(self, fn, args):
        try:
            fn(*args)
        except Exception:
            tb = traceback.format_exc()
            self.errors.append(tb)
            print(tb, flush=True)

    def process(self, max_wait: float = 0.05) -> None:
        now = time.monotonic()
        due = []
        with self._tlock:
            while self._timers and self._timers[0][0] <= now:
                due.append(heapq.heappop(self._timers))
            nxt = self._timers[0][0] if self._timers else None
        for _t, _s, h, fn in due:
            if h[0]:
                self._run(fn, ())
        wait = max_wait if nxt is None else max(0.0, min(max_wait, nxt - time.monotonic()))
        try:
            fn, args = self.q.get(timeout=wait)
        except queue.Empty:
            return
        if fn is not None:
            self._run(fn, args)
        # drain whatever else is already queued, bounded so timers stay fair
        for _ in range(200):
            try:
                fn, args = self.q.get_nowait()
            except queue.Empty:
                break
            if fn is not None:
                self._run(fn, args)

    def run_until(self, pred, timeout: float = None) -> None:
        t_end = None if timeout is None else time.monotonic() + timeout
        while not pred() and not self.quit_flag:
            if t_end is not None and time.monotonic() > t_end:
                return
            self.process(0.05)

    def run_forever(self) -> None:
        self.bind_current()
        while not self.quit_flag:
            self.process(0.05)

    def call_sync(self, fn, timeout: float = 5.0):
        """From a non-GUI thread: run fn on the GUI thread and return its result."""
        if self.in_gui():
            return fn()
        box = {}
        ev = threading.Event()

        def job():
            try:
                box["r"] = fn()
            except Exception as exc:     # surfaced to the caller
                box["e"] = exc
                self.errors.append(traceback.format_exc())
            finally:
                ev.set()

        self.post(job)
        if not ev.wait(timeout):
            raise TimeoutError("GUI thread busy")
        if "e" in box:
            raise box["e"]
        return box.get("r")


LOOP = Loop()

# --------------------------------------------------------------------------
#  signals
# --------------------------------------------------------------------------

_nargs_cache: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_nargs_plain: dict = {}


def _accepts(fn) -> int:
    """How many positional args a slot takes; -1 for *args/unknown."""
    target = getattr(fn, "__func__", fn)
    key = target
    try:
        return _nargs_cache[key]
    except (KeyError, TypeError):
        pass
    try:
        sig = inspect.signature(fn)
        n = 0
        for p in sig.parameters.values():
            if p.kind == p.VAR_POSITIONAL:
                n = -1
                break
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
                n += 1
    except (TypeError, ValueError):
        n = -1
    try:
        _nargs_cache[key] = n
    except TypeError:
        pass
    return n


def _call_slot(fn, args):
    n = _accepts(fn)
    fn(*(args if n < 0 else args[:n]))


class BoundSignal:
    __slots__ = ("_slots", "_owner", "__weakref__")

    def __init__(self, owner):
        self._slots = []
        self._owner = weakref.ref(owner) if owner is not None else None

    def connect(self, fn, *a, **k):
        if isinstance(fn, BoundSignal):           # signal-to-signal
            other = fn
            fn = (lambda *args: other.emit(*args))
        self._slots.append(fn)

    def disconnect(self, fn=None):
        if fn is None:
            self._slots.clear()
        else:
            try:
                self._slots.remove(fn)
            except ValueError:
                raise TypeError("not connected")

    def emit(self, *args):
        owner = self._owner() if self._owner else None
        if owner is not None and getattr(owner, "_vqt_blocked", False):
            return
        slots = list(self._slots)
        if not slots:
            return
        if LOOP.in_gui():
            for s in slots:
                _call_slot(s, args)
        else:
            # Cross-thread: queued, exactly like Qt's AutoConnection.
            for s in slots:
                LOOP.post(_call_slot, s, args)

    # PyQt lets you index a signal by overload: sig[int].connect(...)
    def __getitem__(self, _types):
        return self


class pyqtSignal:
    def __init__(self, *types, **k):
        self._attr = None

    def __set_name__(self, owner, name):
        self._attr = "_sig_" + name

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        d = obj.__dict__
        b = d.get(self._attr)
        if b is None:
            b = d[self._attr] = BoundSignal(obj)
        return b


def pyqtSlot(*a, **k):
    def deco(fn):
        return fn
    return deco


# --------------------------------------------------------------------------
#  dialogs: the GUI thread asks, the web side answers
# --------------------------------------------------------------------------


class Dialogs:
    def __init__(self):
        self._ids = itertools.count(1)
        self.open = {}        # id -> dict (what the web side shows)
        self.answers = {}     # id -> answer
        self.objs = {}        # id -> python object (non-modal boxes)

    def ask(self, spec: dict, obj=None):
        """Register a dialog. Returns its id."""
        i = next(self._ids)
        spec = dict(spec)
        spec["id"] = i
        self.open[i] = spec
        if obj is not None:
            self.objs[i] = obj
        bump_struct()
        return i

    def wait(self, i: int):
        LOOP.run_until(lambda: i in self.answers or i not in self.open)
        self.open.pop(i, None)
        self.objs.pop(i, None)
        bump_struct()
        return self.answers.pop(i, None)

    def answer(self, i: int, value):
        """Called on the GUI thread."""
        if i not in self.open:
            return
        obj = self.objs.get(i)
        if obj is not None and not getattr(obj, "_modal_wait", False):
            # non-modal box: close it now and tell it
            self.open.pop(i, None)
            self.objs.pop(i, None)
            bump_struct()
            obj._vqt_answered(value)
            return
        self.answers[i] = value

    def close(self, i: int):
        if i in self.open:
            self.open.pop(i, None)
            self.objs.pop(i, None)
            bump_struct()


DIALOGS = Dialogs()


# Host hooks (set by the Android/desktop bridge). Each is optional.
class Host:
    files_root = "."          # where file dialogs browse
    open_url = None           # fn(path) -> None   (share / open a file or folder)
    screenshot = None         # fn(path) -> bool   (ask the web side for a PNG)


HOST = Host()
