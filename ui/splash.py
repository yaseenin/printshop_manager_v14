
import tkinter as tk
from PIL import Image, ImageTk
import os
def show_splash(root):
    top = tk.Toplevel(root); top.overrideredirect(True); top.configure(bg='#f3f4f6')
    imgpath = os.path.join(os.path.dirname(__file__), '..', 'assets', 'icons', 'splash.png')
    if os.path.exists(imgpath):
        try:
            img = Image.open(imgpath)
            tkimg = ImageTk.PhotoImage(img)
            lbl = tk.Label(top, image=tkimg, bg='#f3f4f6')
            lbl.image = tkimg
            lbl.pack()
        except Exception:
            tk.Label(top, text='Print Shop Manager v14', fg='#111827', bg='#f3f4f6', font=('Arial',18)).pack(expand=True, pady=20)
    else:
        tk.Label(top, text='Print Shop Manager v14', fg='#111827', bg='#f3f4f6', font=('Arial',18)).pack(expand=True, pady=20)
    top.update_idletasks()
    sw = top.winfo_screenwidth(); sh = top.winfo_screenheight(); w = top.winfo_width(); h = top.winfo_height()
    top.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//3}")
    return top
