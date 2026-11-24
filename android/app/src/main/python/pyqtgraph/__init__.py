"""pyqtgraph stand-in. The mobile app draws in the WebView, so nothing here
is ever drawn -- the desktop module only needs these names to exist while it
defines its (unused) plot widget classes."""
__version__ = "virtual"


def setConfigOption(*a, **k):
    pass


def setConfigOptions(*a, **k):
    pass


class _Any:
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return lambda *a, **k: None


class AxisItem(_Any):
    pass


class PlotWidget(_Any):
    pass


PlotItem = ViewBox = PlotDataItem = TextItem = ScatterPlotItem = _Any
InfiniteLine = LinearRegionItem = ImageItem = GraphicsLayoutWidget = _Any


def mkPen(*a, **k):
    return None


def mkBrush(*a, **k):
    return None
