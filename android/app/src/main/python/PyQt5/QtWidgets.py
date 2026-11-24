"""
Virtual QtWidgets.

Widgets hold their state in Python; the web front end (webui/) renders it
and sends taps back as the same calls a user would make in real Qt:
click(), setValue()+editingFinished, setCurrentIndex(), and so on. Signal
emission follows Qt's rules (only on change, not while blockSignals, the
first item added to an empty combo selects it) because the Analyzer's logic
depends on those rules exactly.
"""
from __future__ import annotations

import itertools
import math
import os
import weakref

from _vqt_core import (DIALOGS, HOST, LOOP, bump_struct, bump_ui,
                       pyqtSignal)
from PyQt5.QtCore import (QMargins, QObject, QPoint, QRect, QSize, Qt)
from PyQt5.QtGui import QFont, QPixmap

_ids = itertools.count(1)
REGISTRY: "weakref.WeakValueDictionary" = weakref.WeakValueDictionary()


class _EnumMeta(type(QObject)):
    """Unknown Capitalised class attributes are enum values (ints)."""
    _gen = itertools.count(1000)

    def __getattr__(cls, name):
        if name[:1].isupper() and not name.startswith("__"):
            v = next(_EnumMeta._gen)
            setattr(cls, name, v)
            return v
        raise AttributeError(name)


class _Style:
    PM_ScrollBarExtent = 9

    def pixelMetric(self, *a):
        return 12

    def standardIcon(self, *a):
        from PyQt5.QtGui import QIcon
        return QIcon()

    def polish(self, *a):
        pass

    def unpolish(self, *a):
        pass


class QStyle(_Style, metaclass=_EnumMeta):
    pass


class QSizePolicy(metaclass=_EnumMeta):
    Fixed = 0
    Minimum = 1
    Maximum = 4
    Preferred = 5
    Expanding = 7
    MinimumExpanding = 3
    Ignored = 13

    def __init__(self, *a):
        self._h = a[0] if a else 5
        self._v = a[1] if len(a) > 1 else 5

    def horizontalPolicy(self):
        return self._h

    def verticalPolicy(self):
        return self._v

    def setHorizontalPolicy(self, p):
        self._h = p

    def setVerticalPolicy(self, p):
        self._v = p

    def setHorizontalStretch(self, *a):
        pass

    def setVerticalStretch(self, *a):
        pass

    def setRetainSizeWhenHidden(self, *a):
        pass


# ============================================================================
#  QWidget
# ============================================================================


class QWidget(QObject, metaclass=_EnumMeta):
    def __init__(self, parent=None, *a, **k):
        QObject.__init__(self)
        self._wid = next(_ids)
        REGISTRY[self._wid] = self
        self._explicit_parent = parent if isinstance(parent, QWidget) else None
        self._in_layout = None          # the layout this widget sits in
        self._layout = None
        self._hidden = False
        self._shown_top = False
        self._enabled = True
        self._tip = ""
        self._style = ""
        self._font = QFont()
        self._title = ""
        self._children = []
        self._min_w = 0
        self._fixed_h = None
        if self._explicit_parent is not None:
            self._explicit_parent._children.append(self)

    # ---- identity / tree ------------------------------------------------
    def parentWidget(self):
        if self._in_layout is not None:
            ow = self._in_layout.parentWidget()
            if ow is not None:
                return ow
        return self._explicit_parent

    def parent(self):
        return self.parentWidget()

    def setParent(self, p, *a):
        old = self._explicit_parent
        if old is not None and self in old._children:
            old._children.remove(self)
        if self._in_layout is not None:
            self._in_layout._remove(self)
        self._explicit_parent = p if isinstance(p, QWidget) else None
        if self._explicit_parent is not None:
            self._explicit_parent._children.append(self)
        bump_struct()

    def window(self):
        w = self
        while w.parentWidget() is not None:
            w = w.parentWidget()
        return w

    def _all_children(self):
        seen = []
        for c in self._children:
            seen.append(c)
        if self._layout is not None:
            for c in self._layout._widgets_recursive():
                if c not in seen:
                    seen.append(c)
        return seen

    def findChildren(self, cls=None, *a):
        out = []
        stack = list(self._all_children())
        visited = set()
        while stack:
            w = stack.pop(0)
            if id(w) in visited:
                continue
            visited.add(id(w))
            if cls is None or isinstance(w, cls):
                out.append(w)
            stack.extend(w._all_children())
        return out

    def findChild(self, cls=None, *a):
        r = self.findChildren(cls)
        return r[0] if r else None

    def children(self):
        return list(self._all_children())

    # ---- layout ----------------------------------------------------------
    def setLayout(self, lay):
        self._layout = lay
        lay._owner = self
        bump_struct()

    def layout(self):
        return self._layout

    # ---- visibility -------------------------------------------------------
    def show(self):
        if self._hidden or self.parentWidget() is None:
            self._hidden = False
            self._shown_top = True
            bump_struct()

    def hide(self):
        if not self._hidden:
            self._hidden = True
            bump_struct()

    def setVisible(self, v):
        if v:
            self.show()
        else:
            self.hide()

    def setHidden(self, h):
        self.setVisible(not h)

    def isHidden(self):
        return self._hidden

    def isVisible(self):
        if self._hidden:
            return False
        p = self.parentWidget()
        if p is None:
            return self._shown_top
        return p.isVisible()

    def isVisibleTo(self, anc):
        w = self
        while w is not None and w is not anc:
            if w._hidden:
                return False
            w = w.parentWidget()
        return True

    def close(self):
        self.hide()
        return True

    def raise_(self):
        pass

    def lower(self):
        pass

    def activateWindow(self):
        pass

    # ---- state -----------------------------------------------------------
    def setEnabled(self, b):
        b = bool(b)
        if b != self._enabled:
            self._enabled = b
            bump_ui()

    def setDisabled(self, b):
        self.setEnabled(not b)

    def isEnabled(self):
        if not self._enabled:
            return False
        p = self.parentWidget()
        return True if p is None else p.isEnabled()

    def setToolTip(self, t):
        self._tip = str(t or "")

    def toolTip(self):
        return self._tip

    def setStatusTip(self, t):
        pass

    def setWhatsThis(self, t):
        pass

    def setStyleSheet(self, s):
        s = str(s or "")
        if s != self._style:
            self._style = s
            bump_ui()

    def styleSheet(self):
        return self._style

    def style(self):
        return _Style()

    def setFont(self, f):
        self._font = f
        bump_ui()

    def font(self):
        return self._font

    def setWindowTitle(self, t):
        self._title = str(t)

    def windowTitle(self):
        return self._title

    # ---- geometry: nothing is laid out here; plausible numbers -------------
    def width(self):
        return 900

    def height(self):
        return 600

    def size(self):
        return QSize(self.width(), self.height())

    def rect(self):
        return QRect(0, 0, self.width(), self.height())

    def geometry(self):
        return self.rect()

    def sizeHint(self):
        return QSize(200, 30)

    def minimumSizeHint(self):
        return QSize(60, 20)

    def mapTo(self, *a):
        return QPoint(0, 0)

    def mapFrom(self, *a):
        return QPoint(0, 0)

    def mapToGlobal(self, p):
        return p

    def mapFromGlobal(self, p):
        return p

    def setMinimumWidth(self, w):
        self._min_w = int(w)

    def setFixedHeight(self, h):
        self._fixed_h = int(h)

    def contentsMargins(self):
        return QMargins()

    def grab(self, *a):
        return QPixmap()

    def sizePolicy(self):
        return QSizePolicy()

    def palette(self):
        from PyQt5.QtGui import QPalette
        return QPalette()

    def fontMetrics(self):
        from PyQt5.QtGui import QFontMetrics
        return QFontMetrics(self._font)

    def hasFocus(self):
        return False

    def underMouse(self):
        return False

    def isWindow(self):
        return self.parentWidget() is None

    def winId(self):
        return 0

    def devicePixelRatioF(self):
        return 1.0

    def closeEvent(self, ev):
        pass

    def resizeEvent(self, ev):
        pass

    def mousePressEvent(self, ev):
        pass

    def mouseMoveEvent(self, ev):
        pass

    def mouseReleaseEvent(self, ev):
        pass

    def wheelEvent(self, ev):
        pass

    def keyPressEvent(self, ev):
        pass

    def showEvent(self, ev):
        pass

    def hideEvent(self, ev):
        pass

    def event(self, ev):
        return False


def _noop(*a, **k):
    return None


# Pure presentation calls: accepted and ignored. Listed explicitly (rather
# than a catch-all __getattr__) because the Analyzer uses
# getattr(self, "name", None) to ask whether something EXISTS, and a
# catch-all would answer yes to everything.
for _n in ("setMinimumHeight", "setMaximumWidth", "setMaximumHeight",
           "setFixedWidth", "setFixedSize", "setMinimumSize",
           "setMaximumSize", "resize", "move", "setGeometry",
           "setSizePolicy", "setContentsMargins", "setCursor",
           "unsetCursor", "setFocus", "clearFocus", "setFocusPolicy",
           "setAutoFillBackground", "update", "repaint", "adjustSize",
           "updateGeometry", "setWindowIcon", "setWindowModality",
           "setWindowFlags", "setAttribute", "setPalette",
           "setMouseTracking", "setAcceptDrops", "setUpdatesEnabled",
           "setGraphicsEffect", "setBackgroundRole", "setForegroundRole",
           "setContextMenuPolicy", "setTabOrder", "setLayoutDirection",
           "ensurePolished", "setWindowOpacity", "setInputMethodHints",
           "showNormal", "showMaximized", "showMinimized", "setShortcut",
           "setTextInteractionFlags", "setOpenExternalLinks",
           "setFrameShadow", "setLineWidth", "setMidLineWidth",
           "setAccessibleName", "setAccessibleDescription",
           "setSizeIncrement", "setBaseSize"):
    setattr(QWidget, _n, _noop)


class QFrame(QWidget):
    NoFrame = 0
    Box = 1
    Panel = 2
    StyledPanel = 6
    HLine = 4
    VLine = 5
    Plain = 0x10
    Raised = 0x20
    Sunken = 0x30

    def __init__(self, parent=None, *a, **k):
        super().__init__(parent)
        self._shape = 0

    def setFrameShape(self, s):
        self._shape = s

    def frameShape(self):
        return self._shape

    def setFrameStyle(self, s):
        self._shape = s & 0xF


# ============================================================================
#  Layouts
# ============================================================================


class _Item:
    """A slot in a layout: widget, layout, stretch or spacing."""
    __slots__ = ("kind", "w", "l", "n", "row", "col", "rs", "cs", "label",
                 "stretch")

    def __init__(self, kind, w=None, l=None, n=0):
        self.kind, self.w, self.l, self.n = kind, w, l, n
        self.row = self.col = 0
        self.rs = self.cs = 1
        self.label = None
        self.stretch = 0

    def widget(self):
        return self.w

    def layout(self):
        return self.l

    def spacerItem(self):
        return self if self.kind in ("stretch", "space") else None


class QLayout(QObject, metaclass=_EnumMeta):
    SetDefaultConstraint = 0
    SetFixedSize = 3
    SetMinimumSize = 2

    def __init__(self, parent=None, *a):
        QObject.__init__(self)
        self._items = []
        self._owner = None
        self._parent_layout = None
        if isinstance(parent, QWidget):
            parent.setLayout(self)

    def parentWidget(self):
        lay = self
        while lay is not None:
            if lay._owner is not None:
                return lay._owner
            lay = lay._parent_layout
        return None

    def _adopt(self, w):
        if w._in_layout is not None and w._in_layout is not self:
            w._in_layout._remove(w)
        if w._explicit_parent is not None and w in w._explicit_parent._children:
            w._explicit_parent._children.remove(w)
            w._explicit_parent = None
        w._in_layout = self

    def _remove(self, w):
        self._items = [it for it in self._items if it.w is not w
                       and it.label is not w]
        if w._in_layout is self:
            w._in_layout = None
        bump_struct()

    def _widgets_recursive(self):
        out = []
        for it in self._items:
            if it.label is not None and isinstance(it.label, QWidget):
                out.append(it.label)
            if it.w is not None:
                out.append(it.w)
            if it.l is not None:
                out.extend(it.l._widgets_recursive())
        return out

    def _add_layout_item(self, lay):
        lay._parent_layout = self
        return _Item("layout", l=lay)

    def addWidget(self, w, *a, **k):
        self._adopt(w)
        self._items.append(_Item("widget", w=w))
        bump_struct()

    def removeWidget(self, w):
        self._remove(w)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        if not 0 <= i < len(self._items):
            return None
        it = self._items.pop(i)
        if it.w is not None and it.w._in_layout is self:
            it.w._in_layout = None
        bump_struct()
        return it

    def indexOf(self, w):
        for i, it in enumerate(self._items):
            if it.w is w:
                return i
        return -1

    def setContentsMargins(self, *a):
        pass

    def contentsMargins(self):
        return QMargins()

    def setSpacing(self, *a):
        pass

    def spacing(self):
        return 6

    def setAlignment(self, *a):
        pass

    def activate(self):
        return True

    def setSizeConstraint(self, *a):
        pass

    def update(self):
        pass

    def sizeHint(self):
        return QSize(200, 30)

    def invalidate(self):
        pass


class QBoxLayout(QLayout):
    def __init__(self, parent=None, *a):
        super().__init__(parent)

    def addWidget(self, w, stretch=0, alignment=0):
        self._adopt(w)
        it = _Item("widget", w=w)
        it.stretch = stretch
        self._items.append(it)
        bump_struct()

    def insertWidget(self, idx, w, stretch=0, alignment=0):
        self._adopt(w)
        it = _Item("widget", w=w)
        it.stretch = stretch
        if idx < 0:
            idx = len(self._items)
        self._items.insert(idx, it)
        bump_struct()

    def addLayout(self, lay, stretch=0):
        it = self._add_layout_item(lay)
        it.stretch = stretch
        self._items.append(it)
        bump_struct()

    def insertLayout(self, idx, lay, stretch=0):
        it = self._add_layout_item(lay)
        if idx < 0:
            idx = len(self._items)
        self._items.insert(idx, it)
        bump_struct()

    def addStretch(self, n=0):
        self._items.append(_Item("stretch", n=n or 1))

    def insertStretch(self, idx, n=0):
        self._items.insert(idx if idx >= 0 else len(self._items),
                           _Item("stretch", n=n or 1))

    def addSpacing(self, n):
        self._items.append(_Item("space", n=n))

    def insertSpacing(self, idx, n):
        self._items.insert(idx if idx >= 0 else len(self._items),
                           _Item("space", n=n))

    def addSpacerItem(self, *a):
        self._items.append(_Item("stretch", n=1))

    def setStretch(self, *a):
        pass

    def setStretchFactor(self, *a):
        return True

    def setDirection(self, *a):
        pass


class QHBoxLayout(QBoxLayout):
    _dir = "h"


class QVBoxLayout(QBoxLayout):
    _dir = "v"


class QGridLayout(QLayout):
    _dir = "grid"

    def addWidget(self, w, row=0, col=0, rs=1, cs=1, alignment=0):
        self._adopt(w)
        it = _Item("widget", w=w)
        it.row, it.col, it.rs, it.cs = row, col, max(1, rs), max(1, cs)
        self._items.append(it)
        bump_struct()

    def addLayout(self, lay, row=0, col=0, rs=1, cs=1, alignment=0):
        it = self._add_layout_item(lay)
        it.row, it.col, it.rs, it.cs = row, col, max(1, rs), max(1, cs)
        self._items.append(it)
        bump_struct()

    def setColumnStretch(self, *a):
        pass

    def setRowStretch(self, *a):
        pass

    def setHorizontalSpacing(self, *a):
        pass

    def setVerticalSpacing(self, *a):
        pass

    def setColumnMinimumWidth(self, *a):
        pass

    def setRowMinimumHeight(self, *a):
        pass

    def rowCount(self):
        return 1 + max((it.row + it.rs - 1 for it in self._items), default=-1)

    def columnCount(self):
        return 1 + max((it.col + it.cs - 1 for it in self._items), default=-1)

    def itemAtPosition(self, r, c):
        for it in self._items:
            if it.row == r and it.col == c:
                return it
        return None


class QFormLayout(QLayout):
    _dir = "form"

    def addRow(self, a, b=None):
        if b is None:
            if isinstance(a, QLayout):
                it = self._add_layout_item(a)
            else:
                self._adopt(a)
                it = _Item("widget", w=a)
            it.cs = 2
            self._items.append(it)
        else:
            if isinstance(a, str):
                a = QLabel(a)
            self._adopt(a)
            if isinstance(b, QLayout):
                it = self._add_layout_item(b)
            else:
                self._adopt(b)
                it = _Item("widget", w=b)
            it.label = a
            self._items.append(it)
        bump_struct()

    def insertRow(self, idx, a, b=None):
        n = len(self._items)
        self.addRow(a, b)
        it = self._items.pop()
        self._items.insert(idx if 0 <= idx <= n else n, it)

    def rowCount(self):
        return len(self._items)

    def labelForField(self, w):
        for it in self._items:
            if it.w is w or it.l is w:
                return it.label
        return None

    def setRowVisible(self, row, vis):
        it = self._items[row] if isinstance(row, int) else None
        if it is None:
            for x in self._items:
                if x.w is row or x.l is row:
                    it = x
        if it is not None:
            for w in (it.label, it.w):
                if isinstance(w, QWidget):
                    w.setVisible(vis)

    def removeRow(self, row):
        it = self._items.pop(row)
        for w in (it.label, it.w):
            if isinstance(w, QWidget):
                w._in_layout = None
        bump_struct()

    def setLabelAlignment(self, *a):
        pass

    def setFormAlignment(self, *a):
        pass

    def setFieldGrowthPolicy(self, *a):
        pass

    def setRowWrapPolicy(self, *a):
        pass

    def setHorizontalSpacing(self, *a):
        pass

    def setVerticalSpacing(self, *a):
        pass


class QStackedLayout(QBoxLayout):
    _dir = "v"


# ============================================================================
#  Simple widgets
# ============================================================================


class QLabel(QFrame):
    def __init__(self, text="", parent=None, *a, **k):
        if isinstance(text, QWidget):
            text, parent = "", text
        super().__init__(parent)
        self._text = str(text)
        self._wrap = False
        self._align = 0

    def setText(self, t):
        t = str(t)
        if t != self._text:
            self._text = t
            bump_ui()

    def text(self):
        return self._text

    def clear(self):
        self.setText("")

    def setNum(self, n):
        self.setText(str(n))

    def setWordWrap(self, b):
        self._wrap = bool(b)

    def wordWrap(self):
        return self._wrap

    def setAlignment(self, a):
        self._align = int(a)

    def alignment(self):
        return self._align

    def setTextFormat(self, f):
        pass

    def setPixmap(self, p):
        pass

    def setBuddy(self, w):
        pass

    def setIndent(self, i):
        pass

    def setMargin(self, m):
        pass

    def setScaledContents(self, b):
        pass


class QAbstractButton(QWidget):
    clicked = pyqtSignal(bool)
    toggled = pyqtSignal(bool)
    pressed = pyqtSignal()
    released = pyqtSignal()

    def __init__(self, text="", parent=None, *a, **k):
        if isinstance(text, QWidget):
            text, parent = "", text
        super().__init__(parent)
        self._text = str(text)
        self._checkable = False
        self._checked = False

    def setText(self, t):
        t = str(t)
        if t != self._text:
            self._text = t
            bump_ui()

    def text(self):
        return self._text

    def setCheckable(self, b):
        self._checkable = bool(b)
        bump_ui()

    def isCheckable(self):
        return self._checkable

    def setChecked(self, b):
        b = bool(b)
        if not self._checkable or b == self._checked:
            return
        self._checked = b
        bump_ui()
        self.toggled.emit(b)

    def isChecked(self):
        return self._checked

    def toggle(self):
        self.setChecked(not self._checked)

    def click(self):
        """What a user's tap does -- the web side calls exactly this."""
        if not self.isEnabled():
            return
        self.pressed.emit()
        self.released.emit()
        if self._checkable:
            self.setChecked(not self._checked)
        self.clicked.emit(self._checked)

    animateClick = click

    def setIcon(self, *a):
        pass

    def setIconSize(self, *a):
        pass

    def setAutoRepeat(self, *a):
        pass

    def setAutoExclusive(self, *a):
        pass


class QPushButton(QAbstractButton):
    def setDefault(self, *a):
        pass

    def setAutoDefault(self, *a):
        pass

    def setFlat(self, *a):
        pass

    def setMenu(self, m):
        self._menu = m


class QToolButton(QPushButton):
    pass


class QCheckBox(QAbstractButton):
    stateChanged = pyqtSignal(int)

    def __init__(self, text="", parent=None, *a, **k):
        super().__init__(text, parent)
        self._checkable = True

    def setChecked(self, b):
        b = bool(b)
        if b == self._checked:
            return
        self._checked = b
        bump_ui()
        self.toggled.emit(b)
        self.stateChanged.emit(2 if b else 0)

    def checkState(self):
        return Qt.Checked if self._checked else Qt.Unchecked

    def setCheckState(self, s):
        self.setChecked(s == Qt.Checked)

    def setTristate(self, *a):
        pass


class QRadioButton(QCheckBox):
    pass


class QButtonGroup(QObject):
    buttonClicked = pyqtSignal(object)
    idClicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._b = []

    def addButton(self, b, i=-1):
        self._b.append(b)

    def buttons(self):
        return list(self._b)

    def setExclusive(self, *a):
        pass


# ---- spin boxes -------------------------------------------------------------


class QAbstractSpinBox(QWidget):
    editingFinished = pyqtSignal()
    UpDownArrows = 0
    NoButtons = 2
    CorrectToNearestValue = 1
    AdaptiveDecimalStepType = 1
    DefaultStepType = 0

    def setKeyboardTracking(self, *a):
        pass

    def setButtonSymbols(self, *a):
        pass

    def setAccelerated(self, *a):
        pass

    def setStepType(self, *a):
        pass

    def setWrapping(self, *a):
        pass

    def setAlignment(self, *a):
        pass

    def setCorrectionMode(self, *a):
        pass

    def setReadOnly(self, b):
        self._ro = bool(b)

    def selectAll(self):
        pass

    def setGroupSeparatorShown(self, *a):
        pass

    def lineEdit(self):
        return QLineEdit()


class QDoubleSpinBox(QAbstractSpinBox):
    valueChanged = pyqtSignal(float)
    textChanged = pyqtSignal(str)
    _int = False

    def __init__(self, parent=None, *a, **k):
        super().__init__(parent)
        self._min, self._max = 0.0, 99.99
        self._step = 1.0
        self._dec = 2
        self._val = 0.0
        self._suffix = ""
        self._prefix = ""
        self._special = ""

    def _round(self, v):
        if self._int:
            return int(round(v))
        return round(float(v), self._dec)

    def _set(self, v, emit=True):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return
        v = self._round(min(max(v, self._min), self._max))
        if v != self._val:
            self._val = v
            bump_ui()
            if emit:
                self.valueChanged.emit(v)
                self.textChanged.emit(self.text())

    def setValue(self, v):
        self._set(v)

    def value(self):
        return self._val

    def setRange(self, lo, hi):
        self._min = self._round(lo)
        self._max = max(self._min, self._round(hi))
        bump_ui()
        self._set(self._val)

    def setMinimum(self, lo):
        self.setRange(lo, max(lo, self._max))

    def setMaximum(self, hi):
        self.setRange(min(self._min, hi), hi)

    def minimum(self):
        return self._min

    def maximum(self):
        return self._max

    def setSingleStep(self, s):
        self._step = s
        bump_ui()

    def singleStep(self):
        return self._step

    def setDecimals(self, d):
        self._dec = int(d)
        self._min = round(self._min, self._dec)
        self._max = round(self._max, self._dec)
        self._val = round(self._val, self._dec)
        bump_ui()

    def decimals(self):
        return 0 if self._int else self._dec

    def setSuffix(self, s):
        self._suffix = str(s)
        bump_ui()

    def suffix(self):
        return self._suffix

    def setPrefix(self, s):
        self._prefix = str(s)
        bump_ui()

    def prefix(self):
        return self._prefix

    def setSpecialValueText(self, s):
        self._special = str(s)

    def specialValueText(self):
        return self._special

    def cleanText(self):
        return (str(self._val) if self._int
                else f"{self._val:.{self._dec}f}")

    def text(self):
        return f"{self._prefix}{self.cleanText()}{self._suffix}"

    def stepBy(self, n):
        self.setValue(self._val + n * self._step)

    def stepUp(self):
        self.stepBy(1)

    def stepDown(self):
        self.stepBy(-1)

    def textFromValue(self, v):
        return str(v)

    # web side: the user typed a value and left the field
    def _user_commit(self, v):
        if not self.isEnabled():
            return
        self._set(float(v))
        self.editingFinished.emit()


class QSpinBox(QDoubleSpinBox):
    valueChanged = pyqtSignal(int)
    _int = True

    def __init__(self, parent=None, *a, **k):
        super().__init__(parent)
        self._min, self._max, self._val, self._step = 0, 99, 0, 1
        self._dec = 0

    def setDisplayIntegerBase(self, *a):
        pass


# ---- line edit ----------------------------------------------------------------


class QLineEdit(QWidget):
    editingFinished = pyqtSignal()
    textChanged = pyqtSignal(str)
    textEdited = pyqtSignal(str)
    returnPressed = pyqtSignal()
    Normal = 0
    Password = 2

    def __init__(self, text="", parent=None, *a, **k):
        if isinstance(text, QWidget):
            text, parent = "", text
        super().__init__(parent)
        self._text = str(text)
        self._ph = ""
        self._ro = False
        self._maxlen = 32767

    def setText(self, t):
        t = str(t)
        if t != self._text:
            self._text = t
            bump_ui()
            self.textChanged.emit(t)

    def text(self):
        return self._text

    def clear(self):
        self.setText("")

    def setPlaceholderText(self, t):
        self._ph = str(t)

    def placeholderText(self):
        return self._ph

    def setReadOnly(self, b):
        self._ro = bool(b)
        bump_ui()

    def isReadOnly(self):
        return self._ro

    def setMaxLength(self, n):
        self._maxlen = int(n)

    def setValidator(self, *a):
        pass

    def setAlignment(self, *a):
        pass

    def setClearButtonEnabled(self, *a):
        pass

    def setEchoMode(self, *a):
        pass

    def setFrame(self, *a):
        pass

    def selectAll(self):
        pass

    def setCursorPosition(self, *a):
        pass

    def setCompleter(self, *a):
        pass

    def _user_commit(self, t):
        if not self.isEnabled() or self._ro:
            return
        t = str(t)[: self._maxlen]
        if t != self._text:
            self._text = t
            bump_ui()
            self.textEdited.emit(t)
            self.textChanged.emit(t)
        self.returnPressed.emit()
        self.editingFinished.emit()


# ---- combo box -------------------------------------------------------------------


class _ComboItem:
    __slots__ = ("text", "data", "tip", "enabled")

    def __init__(self, text, data=None):
        self.text, self.data, self.tip, self.enabled = str(text), data, "", True


class _ModelItem:
    def __init__(self, combo, i):
        self._c, self._i = combo, i

    def setEnabled(self, b):
        self._c._items[self._i].enabled = bool(b)
        bump_ui()

    def isEnabled(self):
        return self._c._items[self._i].enabled

    def setFlags(self, f):
        self.setEnabled(bool(f & Qt.ItemIsEnabled))

    def flags(self):
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable if self.isEnabled() else 0

    def setToolTip(self, t):
        self._c._items[self._i].tip = str(t)


class _ComboModel:
    def __init__(self, combo):
        self._c = combo

    def item(self, i, *a):
        return _ModelItem(self._c, i)

    def rowCount(self, *a):
        return len(self._c._items)


class QComboBox(QWidget):
    currentIndexChanged = pyqtSignal(int)
    currentTextChanged = pyqtSignal(str)
    activated = pyqtSignal(int)
    textActivated = pyqtSignal(str)
    editTextChanged = pyqtSignal(str)
    highlighted = pyqtSignal(int)
    AdjustToContents = 0
    AdjustToMinimumContentsLength = 2
    AdjustToContentsOnFirstShow = 1
    NoInsert = 0

    def __init__(self, parent=None, *a, **k):
        super().__init__(parent)
        self._items = []
        self._idx = -1
        self._editable = False
        self._edit_text = ""

    def _text_at(self, i):
        return self._items[i].text if 0 <= i < len(self._items) else ""

    def _set_index(self, i):
        old_text = self.currentText()
        if i == self._idx:
            return
        self._idx = i
        if self._editable:
            self._edit_text = self._text_at(i)
        bump_ui()
        self.currentIndexChanged.emit(i)
        if self.currentText() != old_text:
            self.currentTextChanged.emit(self.currentText())

    def addItem(self, *a):
        if a and not isinstance(a[0], str) and len(a) >= 2:
            a = a[1:]                         # (icon, text, data)
        text = a[0] if a else ""
        data = a[1] if len(a) > 1 else None
        self._items.append(_ComboItem(text, data))
        bump_ui()
        if self._idx < 0:
            self._set_index(0)

    def addItems(self, texts):
        for t in texts:
            self.addItem(str(t))

    def insertItem(self, i, *a):
        if a and not isinstance(a[0], str) and len(a) >= 2:
            a = a[1:]
        i = max(0, min(i, len(self._items)))
        self._items.insert(i, _ComboItem(a[0] if a else "", a[1] if len(a) > 1 else None))
        if self._idx < 0:
            self._set_index(0)
        elif i <= self._idx:
            self._idx += 1
        bump_ui()

    def insertItems(self, i, texts):
        for k, t in enumerate(texts):
            self.insertItem(i + k, t)

    def insertSeparator(self, i):
        pass

    def removeItem(self, i):
        if not 0 <= i < len(self._items):
            return
        self._items.pop(i)
        if not self._items:
            self._set_index(-1)
        elif i < self._idx:
            self._idx -= 1
        elif i == self._idx:
            self._idx = -2                 # force emit
            self._set_index(min(i, len(self._items) - 1))
        bump_ui()

    def clear(self):
        self._items = []
        self._set_index(-1)
        bump_ui()

    def count(self):
        return len(self._items)

    def currentIndex(self):
        return self._idx

    def setCurrentIndex(self, i):
        if i < -1 or i >= len(self._items):
            return
        self._set_index(i)

    def currentText(self):
        if self._editable:
            return self._edit_text if self._idx < 0 or self._edit_text else self._text_at(self._idx)
        return self._text_at(self._idx)

    def setCurrentText(self, t):
        t = str(t)
        i = self.findText(t)
        if i >= 0:
            self._set_index(i)
        elif self._editable:
            self.setEditText(t)

    def currentData(self, role=None):
        if role == Qt.ToolTipRole:
            return self._items[self._idx].tip if 0 <= self._idx < len(self._items) else None
        return self._items[self._idx].data if 0 <= self._idx < len(self._items) else None

    def itemText(self, i):
        return self._text_at(i)

    def setItemText(self, i, t):
        if 0 <= i < len(self._items):
            old = self.currentText()
            self._items[i].text = str(t)
            bump_ui()
            if i == self._idx and old != self.currentText():
                self.currentTextChanged.emit(self.currentText())

    def itemData(self, i, role=None):
        if not 0 <= i < len(self._items):
            return None
        if role == Qt.ToolTipRole:
            return self._items[i].tip
        return self._items[i].data

    def setItemData(self, i, v, role=None):
        if not 0 <= i < len(self._items):
            return
        if role == Qt.ToolTipRole:
            self._items[i].tip = str(v)
        else:
            self._items[i].data = v

    def findText(self, t, *a):
        t = str(t)
        for i, it in enumerate(self._items):
            if it.text == t:
                return i
        return -1

    def findData(self, d, *a):
        for i, it in enumerate(self._items):
            if it.data == d:
                return i
        return -1

    def setEditable(self, b):
        self._editable = bool(b)
        if self._editable:
            self._edit_text = self._text_at(self._idx)
        bump_ui()

    def isEditable(self):
        return self._editable

    def setEditText(self, t):
        t = str(t)
        if t != self._edit_text:
            self._edit_text = t
            bump_ui()
            self.editTextChanged.emit(t)
            self.currentTextChanged.emit(t)

    def lineEdit(self):
        return QLineEdit(self._edit_text)

    def model(self):
        return _ComboModel(self)

    def view(self):
        return QWidget()

    def setModel(self, *a):
        pass

    def setMaxVisibleItems(self, *a):
        pass

    def setSizeAdjustPolicy(self, *a):
        pass

    def setMinimumContentsLength(self, *a):
        pass

    def setInsertPolicy(self, *a):
        pass

    def setDuplicatesEnabled(self, *a):
        pass

    def showPopup(self):
        pass

    def setValidator(self, *a):
        pass

    # web side
    def _user_pick(self, i):
        if not self.isEnabled() or not 0 <= i < len(self._items):
            return
        if not self._items[i].enabled:
            return
        self._set_index(i)
        self.activated.emit(i)
        self.textActivated.emit(self._text_at(i))

    def _user_edit(self, t):
        if not self.isEnabled() or not self._editable:
            return
        i = self.findText(t)
        if i >= 0:
            self._user_pick(i)
        else:
            self.setEditText(t)


# ============================================================================
#  Item views
# ============================================================================


class QAbstractItemView(QWidget):
    NoEditTriggers = 0
    NoSelection = 0
    SingleSelection = 1
    ExtendedSelection = 3
    SelectRows = 1
    SelectItems = 0
    ScrollPerPixel = 1
    EnsureVisible = 0
    PositionAtCenter = 3

    def setEditTriggers(self, *a):
        pass

    def setSelectionMode(self, m):
        self._selmode = m

    def setSelectionBehavior(self, *a):
        pass

    def setAlternatingRowColors(self, *a):
        pass

    def setVerticalScrollMode(self, *a):
        pass

    def setHorizontalScrollMode(self, *a):
        pass

    def setHorizontalScrollBarPolicy(self, *a):
        pass

    def setVerticalScrollBarPolicy(self, *a):
        pass

    def setUniformItemSizes(self, *a):
        pass

    def setWordWrap(self, *a):
        pass

    def setTextElideMode(self, *a):
        pass

    def setIconSize(self, *a):
        pass

    def setDragDropMode(self, *a):
        pass

    def setFrameShape(self, *a):
        pass

    def viewport(self):
        return self

    def setSizeAdjustPolicy(self, *a):
        pass

    def scrollToBottom(self):
        pass

    def scrollToTop(self):
        pass

    def clearSelection(self):
        pass


class QAbstractScrollArea(QFrame):
    AdjustToContents = 2

    def setHorizontalScrollBarPolicy(self, *a):
        pass

    def setVerticalScrollBarPolicy(self, *a):
        pass

    def setSizeAdjustPolicy(self, *a):
        pass

    def viewport(self):
        return self


class _Cell:
    """QTableWidgetItem / QListWidgetItem."""

    def __init__(self, text="", *a):
        self._text = str(text)
        self._tip = ""
        self._fg = None
        self._bg = None
        self._bold = False
        self._data = {}
        self._align = 0
        self._flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        self._hidden = False
        self._owner = None

    def setText(self, t):
        self._text = str(t)
        bump_ui()

    def text(self):
        return self._text

    def setToolTip(self, t):
        self._tip = str(t)

    def toolTip(self):
        return self._tip

    def setForeground(self, c):
        self._fg = _color_of(c)
        bump_ui()

    def setBackground(self, c):
        self._bg = _color_of(c)
        bump_ui()

    def setTextAlignment(self, a):
        self._align = int(a)

    def setFlags(self, f):
        self._flags = f

    def flags(self):
        return self._flags

    def setData(self, role, v):
        self._data[role] = v
        if role == Qt.DisplayRole:
            self._text = str(v)
        bump_ui()

    def data(self, role):
        if role == Qt.DisplayRole:
            return self._text
        return self._data.get(role)

    def setFont(self, f):
        self._bold = bool(getattr(f, "_bold", False))
        bump_ui()

    def font(self):
        f = QFont()
        f.setBold(self._bold)
        return f

    def setHidden(self, h):
        self._hidden = bool(h)
        bump_ui()

    def isHidden(self):
        return self._hidden

    def setSelected(self, *a):
        pass

    def setSizeHint(self, *a):
        pass

    def setCheckState(self, *a):
        pass

    def listWidget(self):
        return self._owner


def _color_of(c):
    """QColor / QBrush / '#hex' / Qt.GlobalColor -> css string or None."""
    from PyQt5.QtGui import QBrush, QColor
    if isinstance(c, str):
        return c
    if isinstance(c, QBrush):
        c = c._a[0] if c._a else None
        return _color_of(c) if c is not None else None
    if isinstance(c, QColor):
        return c.name()
    return None


QTableWidgetItem = _Cell
QListWidgetItem = _Cell


class QHeaderView(QWidget):
    Stretch = 1
    ResizeToContents = 3
    Fixed = 2
    Interactive = 0

    def setSectionResizeMode(self, *a):
        pass

    def setStretchLastSection(self, *a):
        pass

    def setDefaultSectionSize(self, *a):
        pass

    def setMinimumSectionSize(self, *a):
        pass

    def resizeSection(self, *a):
        pass

    def setHighlightSections(self, *a):
        pass

    def setDefaultAlignment(self, *a):
        pass

    def setSectionsClickable(self, *a):
        pass

    def setVisible(self, v):
        self._hidden = not v
        bump_struct()


class QTableWidget(QAbstractItemView):
    cellClicked = pyqtSignal(int, int)
    cellDoubleClicked = pyqtSignal(int, int)
    itemSelectionChanged = pyqtSignal()
    currentCellChanged = pyqtSignal(int, int, int, int)
    cellChanged = pyqtSignal(int, int)
    itemChanged = pyqtSignal(object)

    def __init__(self, rows=0, cols=0, parent=None):
        if isinstance(rows, QWidget):
            rows, parent = 0, rows
        super().__init__(parent)
        self._rows, self._cols = int(rows), int(cols)
        self._cells = {}
        self._hh = []
        self._vh = []
        self._hhead = QHeaderView()
        self._vhead = QHeaderView()
        self._cur = -1

    def setRowCount(self, n):
        n = int(n)
        if n != self._rows:
            self._rows = n
            self._cells = {k: v for k, v in self._cells.items() if k[0] < n}
            bump_ui()

    def rowCount(self):
        return self._rows

    def setColumnCount(self, n):
        self._cols = int(n)
        bump_ui()

    def columnCount(self):
        return self._cols

    def insertRow(self, r):
        new = {}
        for (rr, cc), v in self._cells.items():
            new[(rr + 1 if rr >= r else rr, cc)] = v
        self._cells = new
        self._rows += 1
        bump_ui()

    def removeRow(self, r):
        new = {}
        for (rr, cc), v in self._cells.items():
            if rr == r:
                continue
            new[(rr - 1 if rr > r else rr, cc)] = v
        self._cells = new
        self._rows = max(0, self._rows - 1)
        bump_ui()

    def setHorizontalHeaderLabels(self, labels):
        self._hh = [str(x) for x in labels]
        bump_ui()

    def setVerticalHeaderLabels(self, labels):
        self._vh = [str(x) for x in labels]

    def horizontalHeaderItem(self, c):
        return _Cell(self._hh[c]) if c < len(self._hh) else None

    def setItem(self, r, c, it):
        if not isinstance(it, _Cell):
            it = _Cell(str(it))
        it._owner = self
        self._cells[(r, c)] = it
        bump_ui()

    def item(self, r, c):
        return self._cells.get((r, c))

    def takeItem(self, r, c):
        return self._cells.pop((r, c), None)

    def setCellWidget(self, r, c, w):
        it = _Cell("")
        it._widget = w
        self._cells[(r, c)] = it

    def cellWidget(self, r, c):
        it = self._cells.get((r, c))
        return getattr(it, "_widget", None)

    def clearContents(self):
        self._cells = {}
        bump_ui()

    def clear(self):
        self._cells = {}
        self._hh = []
        bump_ui()

    def horizontalHeader(self):
        return self._hhead

    def verticalHeader(self):
        return self._vhead

    def setColumnWidth(self, *a):
        pass

    def setRowHeight(self, *a):
        pass

    def resizeColumnsToContents(self):
        pass

    def resizeRowsToContents(self):
        pass

    def setShowGrid(self, *a):
        pass

    def setSortingEnabled(self, *a):
        pass

    def setCornerButtonEnabled(self, *a):
        pass

    def currentRow(self):
        return self._cur

    def setCurrentCell(self, r, c, *a):
        old = self._cur
        self._cur = r
        if old != r:
            self.currentCellChanged.emit(r, c, old, 0)
            self.itemSelectionChanged.emit()
        bump_ui()

    def selectRow(self, r):
        self.setCurrentCell(r, 0)

    def setRowHidden(self, r, h):
        pass

    def _user_click(self, r, c):
        self.setCurrentCell(r, c)
        self.cellClicked.emit(r, c)

    def _user_dbl(self, r, c):
        self.cellDoubleClicked.emit(r, c)


class QListWidget(QAbstractItemView):
    currentRowChanged = pyqtSignal(int)
    itemClicked = pyqtSignal(object)
    itemDoubleClicked = pyqtSignal(object)
    itemSelectionChanged = pyqtSignal()
    currentItemChanged = pyqtSignal(object, object)
    itemActivated = pyqtSignal(object)
    currentTextChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._list = []
        self._cur = -1

    def _wrap(self, it):
        if not isinstance(it, _Cell):
            it = _Cell(str(it))
        it._owner = self
        return it

    def addItem(self, it):
        self._list.append(self._wrap(it))
        bump_ui()

    def addItems(self, items):
        for t in items:
            self.addItem(t)

    def insertItem(self, i, it):
        self._list.insert(max(0, min(i, len(self._list))), self._wrap(it))
        if 0 <= i <= self._cur:
            self._cur += 1
        bump_ui()

    def takeItem(self, i):
        if not 0 <= i < len(self._list):
            return None
        it = self._list.pop(i)
        if self._cur >= len(self._list):
            self.setCurrentRow(len(self._list) - 1)
        bump_ui()
        return it

    def item(self, i):
        return self._list[i] if 0 <= i < len(self._list) else None

    def row(self, it):
        try:
            return self._list.index(it)
        except ValueError:
            return -1

    def count(self):
        return len(self._list)

    def clear(self):
        self._list = []
        old = self._cur
        self._cur = -1
        bump_ui()
        if old != -1:
            self.currentRowChanged.emit(-1)

    def currentRow(self):
        return self._cur

    def currentItem(self):
        return self.item(self._cur)

    def setCurrentRow(self, i, *a):
        if i < -1 or i >= len(self._list):
            return
        if i != self._cur:
            prev = self.item(self._cur)
            self._cur = i
            bump_ui()
            self.currentRowChanged.emit(i)
            self.currentItemChanged.emit(self.item(i), prev)
            self.itemSelectionChanged.emit()
            it = self.item(i)
            self.currentTextChanged.emit(it.text() if it else "")

    def setCurrentItem(self, it):
        self.setCurrentRow(self.row(it))

    def selectedItems(self):
        it = self.item(self._cur)
        return [it] if it is not None else []

    def scrollToItem(self, *a):
        pass

    def setSpacing(self, *a):
        pass

    def _user_click(self, i):
        self.setCurrentRow(i)
        it = self.item(i)
        if it is not None:
            self.itemClicked.emit(it)

    def _user_dbl(self, i):
        self._user_click(i)
        it = self.item(i)
        if it is not None:
            self.itemDoubleClicked.emit(it)
            self.itemActivated.emit(it)


class QPlainTextEdit(QWidget):
    textChanged = pyqtSignal()

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._text = str(text)

    def setPlainText(self, t):
        self._text = str(t)
        bump_ui()

    def toPlainText(self):
        return self._text

    def appendPlainText(self, t):
        self._text += ("\n" if self._text else "") + str(t)
        bump_ui()

    def setReadOnly(self, *a):
        pass

    def clear(self):
        self.setPlainText("")


QTextEdit = QPlainTextEdit
QTextBrowser = QPlainTextEdit


# ============================================================================
#  Containers
# ============================================================================


class QGroupBox(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, title="", parent=None):
        if isinstance(title, QWidget):
            title, parent = "", title
        super().__init__(parent)
        self._title = str(title)

    def setTitle(self, t):
        self._title = str(t)
        bump_ui()

    def title(self):
        return self._title

    def setCheckable(self, *a):
        pass

    def setFlat(self, *a):
        pass


class QScrollArea(QAbstractScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._inner = None

    def setWidget(self, w):
        self._inner = w
        if w is not None:
            w._explicit_parent = self
            self._children.append(w)
        bump_struct()

    def widget(self):
        return self._inner

    def setWidgetResizable(self, *a):
        pass

    def verticalScrollBar(self):
        return _ScrollBar()

    def horizontalScrollBar(self):
        return _ScrollBar()

    def ensureWidgetVisible(self, *a):
        pass


class _ScrollBar:
    def setValue(self, *a):
        pass

    def value(self):
        return 0

    def maximum(self):
        return 0


class QSplitter(QFrame):
    splitterMoved = pyqtSignal(int, int)

    def __init__(self, *a):
        parent = a[-1] if a and isinstance(a[-1], QWidget) else None
        super().__init__(parent)
        self._ws = []
        self._sizes = []

    def addWidget(self, w):
        self._ws.append(w)
        w._explicit_parent = self
        self._children.append(w)
        bump_struct()

    def insertWidget(self, i, w):
        self._ws.insert(i, w)
        w._explicit_parent = self
        self._children.append(w)
        bump_struct()

    def widget(self, i):
        return self._ws[i] if 0 <= i < len(self._ws) else None

    def count(self):
        return len(self._ws)

    def indexOf(self, w):
        return self._ws.index(w) if w in self._ws else -1

    def setSizes(self, s):
        self._sizes = list(s)
        bump_ui()

    def sizes(self):
        if len(self._sizes) == len(self._ws):
            return [0 if w._hidden else s for w, s in zip(self._ws, self._sizes)]
        return [0 if w._hidden else 100 for w in self._ws]

    def setStretchFactor(self, *a):
        pass

    def setCollapsible(self, *a):
        pass

    def setChildrenCollapsible(self, *a):
        pass

    def setHandleWidth(self, *a):
        pass

    def setOrientation(self, *a):
        pass

    def handle(self, i):
        return QWidget()


class QStackedWidget(QFrame):
    currentChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ws = []
        self._cur = -1

    def addWidget(self, w):
        self._ws.append(w)
        w._explicit_parent = self
        self._children.append(w)
        if self._cur < 0:
            self._cur = 0
        return len(self._ws) - 1

    def setCurrentIndex(self, i):
        self._cur = i
        bump_struct()
        self.currentChanged.emit(i)

    def currentIndex(self):
        return self._cur

    def setCurrentWidget(self, w):
        self.setCurrentIndex(self._ws.index(w))

    def widget(self, i):
        return self._ws[i]

    def count(self):
        return len(self._ws)


class QTabWidget(QStackedWidget):
    def addTab(self, w, title):
        w._tab_title = title
        return self.addWidget(w)


# ============================================================================
#  Main window, menus, status bar
# ============================================================================


class QAction(QObject):
    triggered = pyqtSignal(bool)
    toggled = pyqtSignal(bool)

    def __init__(self, text="", parent=None, *a):
        super().__init__(parent)
        self._text = str(text)
        self._enabled = True
        self._checkable = False
        self._checked = False
        self._sep = False

    def text(self):
        return self._text

    def setText(self, t):
        self._text = str(t)

    def setEnabled(self, b):
        self._enabled = bool(b)

    def isEnabled(self):
        return self._enabled

    def setCheckable(self, b):
        self._checkable = bool(b)

    def setChecked(self, b):
        if self._checkable and bool(b) != self._checked:
            self._checked = bool(b)
            self.toggled.emit(self._checked)

    def isChecked(self):
        return self._checked

    def setShortcut(self, *a):
        pass

    def setToolTip(self, *a):
        pass

    def setStatusTip(self, *a):
        pass

    def setVisible(self, *a):
        pass

    def trigger(self):
        if not self._enabled:
            return
        if self._checkable:
            self.setChecked(not self._checked)
        self.triggered.emit(self._checked)


class QMenu(QWidget):
    def __init__(self, title="", parent=None):
        if isinstance(title, QWidget):
            title, parent = "", title
        super().__init__(parent)
        self._title = str(title)
        self._actions = []

    def addAction(self, a, *rest):
        if isinstance(a, str):
            act = QAction(a, self)
            if rest and callable(rest[-1]):
                act.triggered.connect(rest[-1])
        else:
            act = a
        self._actions.append(act)
        bump_struct()
        return act

    def addSeparator(self):
        act = QAction("", self)
        act._sep = True
        self._actions.append(act)
        return act

    def addMenu(self, m):
        if isinstance(m, str):
            m = QMenu(m, self)
        act = QAction(m._title, self)
        act._submenu = m
        self._actions.append(act)
        return m

    def actions(self):
        return list(self._actions)

    def clear(self):
        self._actions = []

    def title(self):
        return self._title

    def exec_(self, *a):
        return None

    popup = exec_


class QMenuBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._menus = []

    def addMenu(self, m):
        if isinstance(m, str):
            m = QMenu(m.replace("&", ""), self)
        self._menus.append(m)
        bump_struct()
        return m


class QStatusBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._normal = []
        self._perm = []
        self._msg = ""

    def addWidget(self, w, stretch=0):
        self._normal.append(w)
        w._explicit_parent = self
        self._children.append(w)

    def addPermanentWidget(self, w, stretch=0):
        self._perm.append(w)
        w._explicit_parent = self
        self._children.append(w)

    def showMessage(self, m, timeout=0):
        self._msg = str(m)
        bump_ui()
        if timeout:
            LOOP.call_later(timeout, self.clearMessage)

    def clearMessage(self):
        self._msg = ""
        bump_ui()

    def currentMessage(self):
        return self._msg

    def setSizeGripEnabled(self, *a):
        pass


class QMainWindow(QWidget):
    def __init__(self, parent=None, *a, **k):
        super().__init__(parent)
        self._central = None
        self._sb = None
        self._mb = None

    def setCentralWidget(self, w):
        self._central = w
        w._explicit_parent = self
        self._children.append(w)
        bump_struct()

    def centralWidget(self):
        return self._central

    def statusBar(self):
        if self._sb is None:
            self._sb = QStatusBar(self)
        return self._sb

    def menuBar(self):
        if self._mb is None:
            self._mb = QMenuBar(self)
        return self._mb

    def addToolBar(self, *a):
        return QWidget()

    def addDockWidget(self, *a):
        pass

    def setUnifiedTitleAndToolBarOnMac(self, *a):
        pass


class QShortcut(QObject):
    activated = pyqtSignal()

    def __init__(self, *a, **k):
        super().__init__()
        self._fn = a[2] if len(a) > 2 and callable(a[2]) else None

    def setEnabled(self, *a):
        pass


class QDialog(QWidget):
    Accepted = 1
    Rejected = 0
    finished = pyqtSignal(int)
    accepted = pyqtSignal()
    rejected = pyqtSignal()

    def exec_(self):
        return self.Rejected

    exec = exec_

    def accept(self):
        self.hide()

    def reject(self):
        self.hide()


# ============================================================================
#  Dialogs -- answered by the web side
# ============================================================================


class QMessageBox(QDialog):
    NoButton = 0x0
    Ok = 0x400
    Save = 0x800
    SaveAll = 0x1000
    Open = 0x2000
    Yes = 0x4000
    YesToAll = 0x8000
    No = 0x10000
    NoToAll = 0x20000
    Abort = 0x40000
    Retry = 0x80000
    Ignore = 0x100000
    Close = 0x200000
    Cancel = 0x400000
    Discard = 0x800000
    Help = 0x1000000
    Apply = 0x2000000
    Reset = 0x4000000
    RestoreDefaults = 0x8000000
    NoIcon = 0
    Information = 1
    Warning = 2
    Critical = 3
    Question = 4
    AcceptRole = 0
    RejectRole = 1
    DestructiveRole = 2
    ActionRole = 3
    YesRole = 5
    NoRole = 6
    _NAMES = [(Ok, "OK"), (Save, "Save"), (Open, "Open"), (Yes, "Yes"),
              (YesToAll, "Yes to All"), (No, "No"), (NoToAll, "No to All"),
              (Abort, "Abort"), (Retry, "Retry"), (Ignore, "Ignore"),
              (Close, "Close"), (Cancel, "Cancel"), (Discard, "Discard"),
              (Apply, "Apply"), (Reset, "Reset"),
              (RestoreDefaults, "Restore Defaults")]

    buttonClicked = pyqtSignal(object)

    def __init__(self, *a):
        parent = None
        if a and (a[0] is None or isinstance(a[0], QWidget)):
            parent = a[0]
            a = a[1:]
        elif a and isinstance(a[0], int) and len(a) >= 3:
            # QMessageBox(icon, title, text, buttons, parent)
            pass
        super().__init__(parent)
        self._icon = 0
        self._text = ""
        self._info = ""
        self._detail = ""
        self._buttons = QMessageBox.Ok
        self._default = 0
        self._custom = []          # (label, role, button object)
        self._dlg_id = None
        self._clicked = None
        self._modal_wait = False
        if len(a) >= 3 and isinstance(a[0], int):
            self._icon, self._title, self._text = a[0], str(a[1]), str(a[2])
            if len(a) >= 4 and isinstance(a[3], int):
                self._buttons = a[3]

    def setIcon(self, i):
        self._icon = i

    def setText(self, t):
        self._text = str(t)
        if self._dlg_id is not None and self._dlg_id in DIALOGS.open:
            DIALOGS.open[self._dlg_id]["text"] = self._text
            bump_struct()

    def text(self):
        return self._text

    def setInformativeText(self, t):
        self._info = str(t)

    def setDetailedText(self, t):
        self._detail = str(t)

    def setStandardButtons(self, b):
        self._buttons = int(b)

    def setDefaultButton(self, b):
        self._default = b if isinstance(b, int) else 0

    def setEscapeButton(self, *a):
        pass

    def setTextFormat(self, *a):
        pass

    def setTextInteractionFlags(self, *a):
        pass

    def addButton(self, label, role=None):
        if isinstance(label, int):
            self._buttons |= label
            return label
        btn = QPushButton(str(label))
        self._custom.append((str(label), role, btn))
        return btn

    def button(self, which):
        return QPushButton()

    def clickedButton(self):
        return self._clicked

    def _spec(self):
        btns = [(v, n) for v, n in self._NAMES if self._buttons & v]
        if not btns and not self._custom:
            btns = [(QMessageBox.Ok, "OK")]
        return {"kind": "message", "title": self._title, "icon": self._icon,
                "text": self._text, "info": self._info,
                "detail": self._detail,
                "buttons": [[v, n] for v, n in btns]
                + [[-(i + 1), lab] for i, (lab, _r, _b) in enumerate(self._custom)],
                "default": self._default}

    def _result_of(self, ans):
        if ans is None:
            for v in (QMessageBox.Cancel, QMessageBox.No, QMessageBox.Close):
                if self._buttons & v:
                    return v
            return QMessageBox.Ok if not self._custom else -1
        ans = int(ans)
        if ans < 0:
            idx = -ans - 1
            if 0 <= idx < len(self._custom):
                self._clicked = self._custom[idx][2]
                return idx
        return ans

    def exec_(self):
        self._modal_wait = True
        self._dlg_id = DIALOGS.ask(self._spec(), self)
        ans = DIALOGS.wait(self._dlg_id)
        self._dlg_id = None
        self._modal_wait = False
        r = self._result_of(ans)
        self.finished.emit(r)
        return r

    exec = exec_

    def show(self):
        if self._dlg_id is None:
            self._dlg_id = DIALOGS.ask(self._spec(), self)

    open = show

    def _vqt_answered(self, ans):
        self._dlg_id = None
        r = self._result_of(ans)
        self.finished.emit(r)

    def close(self):
        if self._dlg_id is not None:
            DIALOGS.close(self._dlg_id)
            self._dlg_id = None
        return True

    done = lambda self, r=0: self.close()   # noqa: E731
    hide = close

    @staticmethod
    def _static(icon, parent, title, text, buttons, default):
        b = QMessageBox(parent)
        b.setIcon(icon)
        b.setWindowTitle(title)
        b.setText(text)
        b.setStandardButtons(buttons)
        b.setDefaultButton(default)
        return b.exec_()

    @staticmethod
    def question(parent, title, text, buttons=None, default=0):
        if buttons is None:
            buttons = QMessageBox.Yes | QMessageBox.No
        return QMessageBox._static(QMessageBox.Question, parent, title, text,
                                   buttons, default)

    @staticmethod
    def warning(parent, title, text, buttons=None, default=0):
        return QMessageBox._static(QMessageBox.Warning, parent, title, text,
                                   buttons or QMessageBox.Ok, default)

    @staticmethod
    def information(parent, title, text, buttons=None, default=0):
        return QMessageBox._static(QMessageBox.Information, parent, title,
                                   text, buttons or QMessageBox.Ok, default)

    @staticmethod
    def critical(parent, title, text, buttons=None, default=0):
        return QMessageBox._static(QMessageBox.Critical, parent, title, text,
                                   buttons or QMessageBox.Ok, default)

    @staticmethod
    def about(parent, title, text):
        QMessageBox._static(QMessageBox.NoIcon, parent, title, text,
                            QMessageBox.Ok, 0)


def _exts(filt: str):
    import re
    out = []
    for pat in re.findall(r"\*\.([A-Za-z0-9_]+)", filt or ""):
        out.append("." + pat.lower())
    return out


def _start_dir(path: str):
    root = os.path.abspath(HOST.files_root)
    if path:
        p = os.path.abspath(os.path.expanduser(path))
        d = p if os.path.isdir(p) else os.path.dirname(p)
        if d.startswith(root) and os.path.isdir(d):
            return d, (os.path.basename(p) if not os.path.isdir(p) else "")
        return root, os.path.basename(p) if not os.path.isdir(p) else ""
    return root, ""


class QFileDialog(QDialog):
    DontConfirmOverwrite = 4
    ShowDirsOnly = 1

    @staticmethod
    def getSaveFileName(parent=None, caption="", directory="", filter="",
                        *a, **k):
        d, name = _start_dir(directory)
        i = DIALOGS.ask({"kind": "save", "title": caption, "dir": d,
                         "name": name, "exts": _exts(filter),
                         "root": os.path.abspath(HOST.files_root)})
        ans = DIALOGS.wait(i)
        if not ans:
            return "", ""
        p = os.path.abspath(str(ans))
        ex = _exts(filter)
        if ex and not os.path.splitext(p)[1]:
            p += ex[0]
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p, filter

    @staticmethod
    def getOpenFileName(parent=None, caption="", directory="", filter="",
                        *a, **k):
        d, _name = _start_dir(directory)
        i = DIALOGS.ask({"kind": "open", "title": caption, "dir": d,
                         "exts": _exts(filter),
                         "root": os.path.abspath(HOST.files_root)})
        ans = DIALOGS.wait(i)
        if not ans:
            return "", ""
        return os.path.abspath(str(ans)), filter

    @staticmethod
    def getExistingDirectory(parent=None, caption="", directory="", *a, **k):
        d, _n = _start_dir(directory)
        i = DIALOGS.ask({"kind": "dir", "title": caption, "dir": d,
                         "root": os.path.abspath(HOST.files_root)})
        ans = DIALOGS.wait(i)
        return os.path.abspath(str(ans)) if ans else ""


# ============================================================================
#  QApplication
# ============================================================================


class _Clipboard:
    def setText(self, t, *a):
        _Clipboard.text_value = str(t)

    def text(self, *a):
        return getattr(_Clipboard, "text_value", "")


class QApplication(QObject):
    _inst = None

    def __init__(self, argv=None, *a):
        super().__init__()
        QApplication._inst = self
        LOOP.bind_current()

    @staticmethod
    def instance():
        return QApplication._inst

    @staticmethod
    def processEvents(*a):
        # Deliberately nothing: the only caller paints a "looking for the
        # radio" message before a blocking open. The web side polls on its
        # own thread, so the message is already visible.
        pass

    @staticmethod
    def font(*a):
        return QFont()

    @staticmethod
    def clipboard():
        return _Clipboard()

    @staticmethod
    def style():
        return _Style()

    @staticmethod
    def desktop():
        return QWidget()

    @staticmethod
    def primaryScreen():
        return None

    @staticmethod
    def beep():
        pass

    def exec_(self):
        LOOP.run_forever()
        return 0

    exec = exec_

    def quit(self):
        LOOP.quit_flag = True

    exit = quit

    def setApplicationName(self, *a):
        pass

    def setOrganizationName(self, *a):
        pass

    def setWindowIcon(self, *a):
        pass

    def setStyleSheet(self, *a):
        pass

    def setStyle(self, *a):
        pass

    def setPalette(self, *a):
        pass

    def setFont(self, *a):
        pass

    def setAttribute(self, *a):
        pass

    def setQuitOnLastWindowClosed(self, *a):
        pass

    def setApplicationVersion(self, *a):
        pass

    def setDesktopFileName(self, *a):
        pass

    def palette(self):
        from PyQt5.QtGui import QPalette
        return QPalette()


QGuiApplication = QApplication


class QStyleFactory:
    @staticmethod
    def create(*a):
        return _Style()


class QGraphicsDropShadowEffect(QObject):
    def __init__(self, *a):
        super().__init__()

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return _noop


class QProgressBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._v, self._min, self._max = 0, 0, 100

    def setRange(self, a, b):
        self._min, self._max = a, b

    def setValue(self, v):
        self._v = v
        bump_ui()

    def value(self):
        return self._v

    def setTextVisible(self, *a):
        pass

    def setFormat(self, *a):
        pass


class QSlider(QWidget):
    valueChanged = pyqtSignal(int)

    def __init__(self, *a):
        super().__init__()
        self._v, self._min, self._max = 0, 0, 99

    def setRange(self, a, b):
        self._min, self._max = a, b

    def setValue(self, v):
        v = int(min(max(v, self._min), self._max))
        if v != self._v:
            self._v = v
            bump_ui()
            self.valueChanged.emit(v)

    def value(self):
        return self._v

    def setOrientation(self, *a):
        pass

    def setTickPosition(self, *a):
        pass

    def setTickInterval(self, *a):
        pass

    def setPageStep(self, *a):
        pass

    def setSingleStep(self, *a):
        pass


def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    raise AttributeError(f"virtual QtWidgets has no {name}")
