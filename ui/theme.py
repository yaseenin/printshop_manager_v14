# ui/theme.py
# Centralized theme definitions and apply function.

CURRENT_THEME = "dark"

THEMES = {
    "dark": {
        "BG_MAIN": "#0f172a",
        "BG_FRAME": "#111827",
        "BG_CARD": "#1e2933",
        "BORDER_CARD": "#334155",
        "FG_TEXT": "#e5e7eb",
        "ACCENT_BLUE": "#38bdf8",
        "ONLINE_GREEN": "#22c55e",
        "OFFLINE_RED": "#ef4444",
    },
    "light": {
        "BG_MAIN": "#f3f4f6",
        "BG_FRAME": "#e5e7eb",
        "BG_CARD": "#ffffff",
        "BORDER_CARD": "#d1d5db",
        "FG_TEXT": "#1e2933",
        "ACCENT_BLUE": "#2563eb",
        "ONLINE_GREEN": "#16a34a",
        "OFFLINE_RED": "#dc2626",
    }
}


def apply_theme(root, style, theme_name: str):
    """
    Apply theme to the application.
    - root: tk.Tk instance
    - style: ttk.Style instance
    - theme_name: "dark" or "light"
    Returns the color dict used by UI modules.
    """
    global CURRENT_THEME
    if theme_name not in THEMES:
        theme_name = "dark"
    CURRENT_THEME = theme_name
    colors = THEMES[theme_name]

    # Root background
    try:
        root.configure(bg=colors["BG_MAIN"])
    except Exception:
        pass

    # TTK style updates (broad)
    try:
        style.configure("TFrame", background=colors["BG_MAIN"])
        style.configure("TLabel", background=colors["BG_MAIN"], foreground=colors["FG_TEXT"])
        style.configure("TButton", background=colors["BG_CARD"], foreground=colors["FG_TEXT"])
        style.configure("TNotebook", background=colors["BG_FRAME"], borderwidth=0)
        style.configure("TNotebook.Tab", background=colors["BG_CARD"], foreground=colors["FG_TEXT"])
        style.map("TNotebook.Tab",
                  background=[("selected", colors["BG_FRAME"])],
                  foreground=[("selected", colors["FG_TEXT"])])
        style.configure("Treeview", background=colors["BG_CARD"], foreground=colors["FG_TEXT"],
                        fieldbackground=colors["BG_CARD"])
        style.configure("Treeview.Heading", background=colors["BG_FRAME"], foreground=colors["FG_TEXT"])
    except Exception:
        pass

    return colors
