"""Virtual QtGui: fonts and metrics are estimates (nothing is laid out in
Python -- the WebView lays out), pictures are delegated to the web side."""
from __future__ import annotations

from _vqt_core import HOST
from PyQt5.QtCore import QRect, QSize


class QFont:
    Bold = 75
    Normal = 50

    def __init__(self, *a):
        self._bold = False
        self._pt = 9.0
        self._px = 12
        self._family = a[0] if a and isinstance(a[0], str) else "sans"

    def setBold(self, b):
        self._bold = bool(b)

    def bold(self):
        return self._bold

    def setPointSizeF(self, p):
        self._pt = float(p)

    def setPointSize(self, p):
        self._pt = float(p)

    def pointSizeF(self):
        return self._pt

    def pointSize(self):
        return int(self._pt)

    def setPixelSize(self, p):
        self._px = int(p)

    def pixelSize(self):
        return self._px

    def setFamily(self, f):
        self._family = f

    def family(self):
        return self._family

    def setWeight(self, w):
        self._bold = w >= 63

    def setItalic(self, b):
        pass

    def setStyleHint(self, *a):
        pass

    def setLetterSpacing(self, *a):
        pass


class QFontMetrics:
    def __init__(self, f=None):
        self._px = getattr(f, "_px", 12)

    def _w(self, text):
        lines = str(text).split("\n")
        return int(max(len(s) for s in lines) * self._px * 0.55), len(lines)

    def boundingRect(self, *a):
        text = a[-1] if a else ""
        w, n = self._w(text)
        return QRect(0, 0, w, n * self.height())

    def horizontalAdvance(self, text, *a):
        return self._w(text)[0]

    width = horizontalAdvance

    def height(self):
        return int(self._px * 1.3)

    def lineSpacing(self):
        return self.height()

    def averageCharWidth(self):
        return int(self._px * 0.55)

    def elidedText(self, text, *a):
        return text


QFontMetricsF = QFontMetrics


class QColor:
    def __init__(self, *a):
        self._a = a

    def name(self):
        if self._a and isinstance(self._a[0], str):
            return self._a[0]
        if len(self._a) >= 3:
            return "#%02x%02x%02x" % tuple(int(x) for x in self._a[:3])
        return "#000000"


class QBrush:
    def __init__(self, *a):
        self._a = a


class QPen:
    def __init__(self, *a):
        self._a = a


class QIcon:
    def __init__(self, *a):
        pass

    def isNull(self):
        return True

    def addPixmap(self, *a):
        pass


class QImage:
    Format_RGB32 = 4
    Format_RGB888 = 13
    Format_ARGB32 = 5
    Format_RGB = 4

    def __init__(self, *a):
        self._w = a[0] if len(a) >= 2 and isinstance(a[0], int) else 1280
        self._h = a[1] if len(a) >= 2 and isinstance(a[1], int) else 720

    def width(self):
        return self._w

    def height(self):
        return self._h

    def fill(self, *a):
        pass

    def constBits(self):
        return 0

    def bits(self):
        return 0

    def isNull(self):
        return False

    def save(self, path, *a):
        return QPixmap().save(path)

    def size(self):
        return QSize(self._w, self._h)

    def scaled(self, *a, **k):
        return self


class QPixmap:
    """What widget.grab() returns. save() asks the web side for the picture."""

    def __init__(self, *a):
        self._w, self._h = 1280, 720

    def toImage(self):
        return QImage(self._w, self._h)

    def save(self, path, *a, **k):
        if HOST.screenshot is None:
            raise RuntimeError("no screen to capture")
        if not HOST.screenshot(str(path)):
            raise RuntimeError("screen capture failed")
        return True

    def width(self):
        return self._w

    def height(self):
        return self._h

    def isNull(self):
        return False

    def loadFromData(self, *a, **k):
        return True

    def scaled(self, *a, **k):
        return self

    def size(self):
        return QSize(self._w, self._h)

    def fill(self, *a):
        pass

    def devicePixelRatio(self):
        return 1.0


class QPainter:
    SmoothPixmapTransform = 4
    Antialiasing = 1

    def __init__(self, *a):
        pass

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: None


class QDesktopServices:
    @staticmethod
    def openUrl(url):
        p = url.toLocalFile() if hasattr(url, "toLocalFile") else str(url)
        if HOST.open_url is not None:
            HOST.open_url(p)
        return True


class QKeySequence:
    def __init__(self, *a):
        pass


class QPalette:
    Window = 10
    WindowText = 0
    Base = 9
    Text = 6
    Button = 1
    ButtonText = 8
    Highlight = 12
    HighlightedText = 13

    def __init__(self, *a):
        pass

    def setColor(self, *a):
        pass

    def color(self, *a):
        return QColor("#000000")


class QGuiApplication:
    @staticmethod
    def primaryScreen():
        return None


class QValidator:
    def __init__(self, *a):
        pass


QDoubleValidator = QIntValidator = QRegExpValidator = QValidator


class QCursor:
    def __init__(self, *a):
        pass

    @staticmethod
    def pos():
        from PyQt5.QtCore import QPoint
        return QPoint(0, 0)
