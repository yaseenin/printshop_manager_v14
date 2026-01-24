
import tkinter as tk
from tkinter import ttk
import os
class SettingsTab:
    def __init__(self,parent):
        self.frame = ttk.Frame(parent); ttk.Label(self.frame, text='Settings', font=('Arial',14,'bold')).pack(anchor='w', padx=8, pady=6)
        ttk.Label(self.frame, text=f'Database: {os.path.join(os.path.dirname(__file__), "..", "printshop_v14.db")}').pack(anchor='w', padx=8)
