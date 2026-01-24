# ui/Insights.py
# INSIGHTS — FINAL & NEVER EMPTY

from tkinter import ttk
from core.analytics_insights import get_insights


class InsightsAnalyticsTab:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)

        ttk.Label(
            self.frame,
            text="INSIGHTS — Business Summary",
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w", padx=12, pady=10)

        self.label = ttk.Label(
            self.frame,
            text="Loading insights…",
            font=("Segoe UI", 11),
            justify="left"
        )
        self.label.pack(anchor="w", padx=24)

        self.refresh()

    def refresh(self):
        data = get_insights()

        text = (
            f"Busiest Day: {data['busiest_day']}\n"
            f"Busiest Time: {data['busiest_time']}\n"
            f"Quietest Period: {data['quietest_period']}\n"
            f"Trend: {data['trend']}"
        )

        self.label.config(text=text)
        self.frame.after(60_000, self.refresh)


__all__ = ["InsightsAnalyticsTab"]
