from overlay import Overlay
import cairo
import math


class TestLinesOverlay(Overlay):
    def draw(self, ctx: cairo.Context):

        ctx.set_source_rgb(0, 0, 1)
        ctx.arc(Overlay.CTX_WIDTH / 2, Overlay.CTX_HEIGHT / 2, 20, 0, 2 * math.pi)
        ctx.stroke()

        ctx.set_source_rgb(1, 0, 0)
        ctx.save()
        ctx.set_line_width(10)
        ctx.move_to(Overlay.CTX_WIDTH * 1 / 3, Overlay.CTX_HEIGHT)
        ctx.line_to(Overlay.CTX_WIDTH * 4 / 9, Overlay.CTX_HEIGHT / 3)
        ctx.stroke()
        ctx.restore()  # line width will be back to previous

        ctx.set_source_rgba(1, 0, 1, 0.7)
        ctx.move_to(100, 200)
        ctx.line_to(200, 200)
        ctx.line_to(300, 100)
        ctx.line_to(100, 200)
        ctx.stroke()

        ctx.set_source_rgb(0, 1, 0)
        ctx.move_to(400, 400)
        ctx.curve_to(300, 300, 200, 100, 50, 200)
        ctx.stroke()

        pass

    @staticmethod
    def get_display_name() -> str:
        return 'test lines'
