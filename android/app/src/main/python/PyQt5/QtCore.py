"""Virtual QtCore -- see _vqt_core for why."""
from __future__ import annotations

import itertools
import threading
import time

from _vqt_core import (LOOP, BoundSignal, bump_ui, pyqtSignal,  # noqa: F401
                       pyqtSlot)

QT_VERSION_STR = "5.15.virtual"
PYQT_VERSION_STR = "5.15.virtual"

_flag_gen = itertools.count(40)


class _QtMeta(type):
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        v = 1 << next(_flag_gen)
        setattr(cls, name, v)
        return v


class Qt(metaclass=_QtMeta):
    AlignLeft = 0x1
    AlignRight = 0x2
    AlignHCenter = 0x4
    AlignJustify = 0x8
    AlignTop = 0x20
    AlignBottom = 0x40
    AlignVCenter = 0x80
    AlignCenter = AlignHCenter | AlignVCenter
    TextWordWrap = 0x1000
    Horizontal = 1
    Vertical = 2
    LeftButton = 1
    RightButton = 2
    NoModifier = 0
    ShiftModifier = 0x02000000
    ControlModifier = 0x04000000
    AltModifier = 0x08000000
    SolidLine = 1
    DashLine = 2
    DotLine = 3
    NonModal = 0
    WindowModal = 1
    ApplicationModal = 2
    Checked = 2
    Unchecked = 0
    ItemIsEnabled = 32
    ItemIsSelectable = 1
    ItemIsEditable = 2
    UserRole = 0x100
    DisplayRole = 0
    ToolTipRole = 3
    Key_Escape = 0x01000000
    ScrollBarAlwaysOff = 1
    ScrollBarAsNeeded = 0
    PointingHandCursor = 13
    SizeHorCursor = 6
    ClosedHandCursor = 18
    ArrowCursor = 0
    RichText = 1
    PlainText = 0
    AutoText = 2
    KeepAspectRatio = 1
    SmoothTransformation = 1
    WA_DeleteOnClose = 55


class QObject:
    def __init__(self, parent=None, *a, **k):
        self._vqt_parent = parent
        self._vqt_blocked = False

    def blockSignals(self, b: bool) -> bool:
        old = getattr(self, "_vqt_blocked", False)
        self._vqt_blocked = bool(b)
        return old

    def signalsBlocked(self) -> bool:
        return getattr(self, "_vqt_blocked", False)

    def moveToThread(self, t):
        pass

    def deleteLater(self):
        pass

    def setObjectName(self, n):
        self._vqt_name = n

    def objectName(self):
        return getattr(self, "_vqt_name", "")

    def installEventFilter(self, obj):
        pass

    def removeEventFilter(self, obj):
        pass

    def parent(self):
        return getattr(self, "_vqt_parent", None)

    def setProperty(self, k, v):
        self.__dict__.setdefault("_vqt_props", {})[k] = v
        return True

    def property(self, k):
        return self.__dict__.get("_vqt_props", {}).get(k)

    def eventFilter(self, obj, ev):
        return False


class QMutex:
    def __init__(self, *a):
        self._l = threading.RLock()

    def lock(self):
        self._l.acquire()

    def unlock(self):
        self._l.release()

    def tryLock(self, timeout=0):
        if timeout and timeout > 0:
            return self._l.acquire(timeout=timeout / 1000.0)
        return self._l.acquire(False)


class QMutexLocker:
    def __init__(self, m):
        self._m = m
        m.lock()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self._m.unlock()

    def unlock(self):
        self._m.unlock()


class QElapsedTimer:
    def __init__(self):
        self._t = None

    def start(self):
        self._t = time.perf_counter_ns()

    def restart(self):
        now = time.perf_counter_ns()
        old = self._t if self._t is not None else now
        self._t = now
        return (now - old) // 1_000_000

    def isValid(self):
        return self._t is not None

    def invalidate(self):
        self._t = None

    def nsecsElapsed(self):
        return 0 if self._t is None else time.perf_counter_ns() - self._t

    def elapsed(self):
        return self.nsecsElapsed() // 1_000_000

    def hasExpired(self, ms):
        return self.elapsed() > ms


class QThread(QObject):
    started = pyqtSignal()
    finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._t = None

    def start(self, *a):
        if self._t is not None and self._t.is_alive():
            return
        # `started` slots run IN the new thread (Qt: DirectConnection from
        # the thread itself), which is what makes worker.run() a loop on
        # its own thread rather than on the GUI thread.
        slots = list(self.__dict__.get("_sig_started", BoundSignal(None))._slots)

        def body():
            from _vqt_core import _call_slot
            for s in slots:
                _call_slot(s, ())
            self.finished.emit()

        self._t = threading.Thread(target=body, daemon=True,
                                   name=f"QThread-{id(self):x}")
        self._t.start()

    def quit(self):
        pass

    def exit(self, code=0):
        pass

    def wait(self, ms=None):
        if self._t is None:
            return True
        self._t.join(None if ms is None else ms / 1000.0)
        return not self._t.is_alive()

    def isRunning(self):
        return self._t is not None and self._t.is_alive()

    def isFinished(self):
        return self._t is not None and not self._t.is_alive()

    @staticmethod
    def msleep(ms):
        time.sleep(ms / 1000.0)

    @staticmethod
    def usleep(us):
        time.sleep(us / 1e6)

    @staticmethod
    def sleep(s):
        time.sleep(s)

    @staticmethod
    def currentThread():
        return threading.current_thread()


class QTimer(QObject):
    timeout = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._interval = 0
        self._single = False
        self._h = None

    def setInterval(self, ms):
        self._interval = int(ms)

    def interval(self):
        return self._interval

    def setSingleShot(self, b):
        self._single = bool(b)

    def isSingleShot(self):
        return self._single

    def isActive(self):
        return self._h is not None and self._h[0]

    def start(self, ms=None):
        if ms is not None:
            self._interval = int(ms)
        self.stop()
        self._arm()

    def _arm(self):
        h = None

        def fire():
            if self._h is not h:
                return
            if self._single:
                self._h = None
            else:
                self._arm()
            self.timeout.emit()

        h = LOOP.call_later(max(self._interval, 1 if not self._single else 0), fire)
        self._h = h

    def stop(self):
        if self._h is not None:
            self._h[0] = False
        self._h = None

    def remainingTime(self):
        return self._interval if self.isActive() else -1

    @staticmethod
    def singleShot(ms, *args):
        fn = args[-1]
        LOOP.call_later(ms, fn)


class QPoint:
    def __init__(self, x=0, y=0):
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y

    def __add__(self, o):
        return type(self)(self._x + o.x(), self._y + o.y())

    def __sub__(self, o):
        return type(self)(self._x - o.x(), self._y - o.y())


QPointF = QPoint


class QSize:
    def __init__(self, w=0, h=0):
        self._w, self._h = w, h

    def width(self):
        return self._w

    def height(self):
        return self._h

    def isValid(self):
        return self._w >= 0 and self._h >= 0


QSizeF = QSize


class QRect:
    def __init__(self, *a):
        if len(a) == 4:
            self._x, self._y, self._w, self._h = a
        elif len(a) == 2:
            self._x, self._y = a[0].x(), a[0].y()
            self._w, self._h = a[1].width(), a[1].height()
        else:
            self._x = self._y = self._w = self._h = 0

    def x(self):
        return self._x

    def y(self):
        return self._y

    def width(self):
        return self._w

    def height(self):
        return self._h

    def left(self):
        return self._x

    def top(self):
        return self._y

    def right(self):
        return self._x + self._w

    def bottom(self):
        return self._y + self._h

    def center(self):
        return QPoint(self._x + self._w / 2, self._y + self._h / 2)

    def size(self):
        return QSize(self._w, self._h)

    def contains(self, *a):
        return True

    def adjusted(self, *a):
        return self


QRectF = QRect


class QMargins:
    def __init__(self, l=0, t=0, r=0, b=0):
        self._m = (l, t, r, b)

    def left(self):
        return self._m[0]

    def top(self):
        return self._m[1]

    def right(self):
        return self._m[2]

    def bottom(self):
        return self._m[3]


class QUrl:
    def __init__(self, s=""):
        self._s = s

    @staticmethod
    def fromLocalFile(p):
        u = QUrl(p)
        u._local = p
        return u

    def toLocalFile(self):
        return getattr(self, "_local", self._s)

    def toString(self):
        return self._s


class QEvent:
    MouseButtonPress = 2
    MouseButtonRelease = 3
    MouseMove = 5
    KeyPress = 6
    Resize = 14
    Show = 17
    Hide = 18

    def __init__(self, t=0):
        self._t = t

    def type(self):
        return self._t


class QSettings:
    def __init__(self, *a, **k):
        self._d = {}

    def value(self, k, default=None, type=None):
        return self._d.get(k, default)

    def setValue(self, k, v):
        self._d[k] = v


class QCoreApplication:
    @staticmethod
    def processEvents(*a):
        pass

    @staticmethod
    def instance():
        return None


def qVersion():
    return QT_VERSION_STR


def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    from _vqt_core import LOOP as _L  # noqa
    raise AttributeError(f"virtual QtCore has no {name}")
