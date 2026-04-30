import webview

import settings

MIN_WIDTH = 280
MIN_HEIGHT = 200
COLLAPSED_HEIGHT = 104
window = None


class Api:
    def resize_window(self, width: int, height: int):
        if window is None:
            return False
        target_width = max(MIN_WIDTH, int(width))
        target_height = max(MIN_HEIGHT, int(height))
        window.resize(target_width, target_height)
        return True

    def move_window(self, x: int, y: int):
        if window is None:
            return False
        window.move(int(x), int(y))
        return True

    def request_shutdown(self):
        """
        Called by the overlay Quit button (x).
        Posts to the Flask shutdown endpoint, then destroys the window to
        release webview.start() on the main thread.
        """
        try:
            import urllib.request
            urllib.request.urlopen(
                "http://127.0.0.1:5555/api/control/shutdown",
                data=b"",
                timeout=2,
            )
        except Exception as e:
            print(f"[Overlay] Shutdown request failed: {e}")
        finally:
            if window is not None:
                window.destroy()
        return True

    def save_geometry(self, x, y, width, height):
        """Persist window geometry so the next launch restores position/size."""
        settings.set("overlay.geometry", {
            "x": int(x),
            "y": int(y),
            "width": int(width),
            "height": int(height),
        })
        return True

    def save_collapsed(self, collapsed):
        """Persist collapsed state so the next launch restores it."""
        settings.set("overlay.collapsed", bool(collapsed))
        return True


def launch_overlay(port: int = 5555):
    global window

    geom = settings.get("overlay.geometry", {})
    x      = int(geom.get("x",      30))
    y      = int(geom.get("y",      30))
    width  = int(geom.get("width",  380))
    height = int(geom.get("height", 760))

    # Clamp off-screen / corrupt values
    if x < -50 or x > 8000 or y < 0 or y > 4000:
        x, y = 30, 30
    if width < MIN_WIDTH:
        width = 380
    if height < MIN_HEIGHT:
        height = 760

    # Apply collapsed state to initial height
    collapsed = settings.get("overlay.collapsed", False)
    if collapsed:
        height = COLLAPSED_HEIGHT

    url = f"http://127.0.0.1:{port}/overlay"
    window = webview.create_window(
        "Bazaar Tracker",
        url,
        width=width,
        height=height,
        x=x,
        y=y,
        frameless=True,
        easy_drag=False,
        on_top=True,
        background_color="#07090f",
        resizable=True,
        min_size=(MIN_WIDTH, MIN_HEIGHT),
        js_api=Api(),
    )
    webview.start(debug=False)


if __name__ == "__main__":
    launch_overlay()
