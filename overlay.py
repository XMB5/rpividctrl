import cairo
import pkgutil
import importlib
import overlays
import inspect


class Overlay:
    CTX_WIDTH = 640
    CTX_HEIGHT = 480

    def draw(self, ctx: cairo.Context):
        """
        Called many times per second to draw the overlay

        The top left corner of the video stream is (0, 0)
        and the bottom right corner is (Overlay.CTX_WIDTH, Overlay.CTX_HEIGHT)

        cairo.Context docs: https://pycairo.readthedocs.io/en/latest/reference/context.html"""
        raise NotImplementedError

    @staticmethod
    def get_display_name() -> str:
        raise NotImplementedError

    @staticmethod
    def list_plugins():
        """Lists all overlays found in the overlays directory"""
        # https://packaging.python.org/guides/creating-and-discovering-plugins/

        plugins = set()
        for finder, name, ispkg in pkgutil.iter_modules(overlays.__path__, overlays.__name__ + '.'):
            module = importlib.import_module(name)
            for cls_name, cls in module.__dict__.items():
                if inspect.isclass(cls) and issubclass(cls, Overlay) and not cls == Overlay:
                    plugins.add(cls)

        return plugins
