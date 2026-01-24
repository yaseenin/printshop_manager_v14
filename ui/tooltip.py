# ui/tooltip.py
# Simple Tkinter tooltip (SAFE)

import tkinter as tk


class ToolTip:
    def __init__(self, widget, text_fn):
        self.widget = widget
        self.text_fn = text_fn
        self.tip = None

        widget.bind("<Motion>", self._move)
        widget.bind("<Leave>", self._hide)

    def _move(self, event):
        text = self.text_fn(event)
        if not text:
            self._hide()
            return

        if self.tip is None:
            self.tip = tk.Toplevel(self.widget)
            self.tip.wm_overrideredirect(True)
            label = tk.Label(
                self.tip,
                text=text,
                background="#111827",
                foreground="white",
                relief="solid",
                borderwidth=1,
                font=("Segoe UI", 9)
            )
            label.pack(ipadx=6, ipady=3)

        self.tip.wm_geometry(f"+{event.x_root + 10}+{event.y_root + 10}")

    def _hide(self, _=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None
