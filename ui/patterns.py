# ui/patterns.py
# PATTERNS — FINAL & WORKING

import tkinter as tk
from tkinter import ttk
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from core.analytics_patterns import get_heatmap_matrix


class PatternsAnalyticsTab:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)

        header = ttk.Frame(self.frame)
        header.pack(fill="x", padx=12, pady=6)

        ttk.Label(
            header,
            text="PATTERNS — When Demand Happens",
            font=("Segoe UI", 14, "bold")
        ).pack(side="left")

        self.range_var = tk.StringVar(value="30")
        for d in ("7", "30", "90"):
            ttk.Radiobutton(
                header,
                text=f"Last {d} days",
                variable=self.range_var,
                value=d,
                command=self.refresh
            ).pack(side="right", padx=6)

        fig = Figure(figsize=(11, 5), dpi=100)
        self.ax = fig.add_subplot(111)

        self.canvas = FigureCanvasTkAgg(fig, master=self.frame)
        self.canvas.get_tk_widget().pack(
            fill="both", expand=True, padx=12, pady=8
        )

        self.refresh()

    def refresh(self):
        days = int(self.range_var.get())
        matrix = np.array(get_heatmap_matrix(days))

        self.ax.clear()
        self.ax.imshow(matrix, aspect="auto", cmap="YlGnBu")

        self.ax.set_yticks(range(7))
        self.ax.set_yticklabels(
            ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        )

        self.ax.set_xticks(range(0, 24, 2))
        self.ax.set_xticklabels(range(0, 24, 2))

        self.ax.set_title("Hourly Click Demand Heatmap")
        self.ax.set_xlabel("Hour of Day")
        self.ax.set_ylabel("Day of Week")

        self.canvas.draw()


__all__ = ["PatternsAnalyticsTab"]
