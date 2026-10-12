"""
vibranceGUI (NVIDIA, 2.3.1.1) - Minimalist Edition
Faithful to original vibranceGUI UI with:
- Full English interface
- Minimalist design matching reference layout
- Windows Vibrance Level (Desktop)
- Per-Game Program Settings with icon tiles
- Fast Auto-detection observer with 0ms Alt-Tab revert
"""

import sys
import os
import json
import time
import threading
import winreg
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import Image, ImageDraw, ImageFont, ImageTk
import psutil

from color_engine import GammaEngine, UnifiedVibranceEngine

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "desktop": {"vibrance": 50},
    "autostart": False,
    "never_change_res": True,
    "games": {
        "valorant-win64-shipping.exe": {
            "name": "VALORANT",
            "vibrance_enabled": True,
            "vibrance": 75,
            "gamma_enabled": False,
            "gamma": 2.20,
            "boost_enabled": False,
            "boost": 0
        },
        "cs2.exe": {
            "name": "cs2",
            "vibrance_enabled": True,
            "vibrance": 80,
            "gamma_enabled": True,
            "gamma": 1.80,
            "boost_enabled": False,
            "boost": 0
        },
        "r5reloaded.exe": {
            "name": "apex",
            "vibrance_enabled": True,
            "vibrance": 75,
            "gamma_enabled": True,
            "gamma": 1.80,
            "boost_enabled": False,
            "boost": 0
        }
    }
}

GAME_COLORS = {
    "cs2": ("#f59e0b", "#1e293b", "CS2"),
    "csgo": ("#f59e0b", "#1e293b", "CSGO"),
    "valorant": ("#e11d48", "#ffffff", "VAL"),
    "apex": ("#dc2626", "#ffffff", "APEX"),
    "r5reloaded": ("#dc2626", "#ffffff", "APEX"),
    "pubg": ("#d97706", "#ffffff", "PUBG"),
    "bf6": ("#0284c7", "#ffffff", "BF6"),
    "battlefield": ("#0284c7", "#ffffff", "BF"),
    "dota2": ("#b91c1c", "#ffffff", "DOTA"),
    "league": ("#0ea5e9", "#ffffff", "LOL"),
    "overwatch": ("#ea580c", "#ffffff", "OW"),
}


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print("Save config failed:", e)


def update_windows_autostart(enable):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        if enable:
            bat_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "run.bat"))
            winreg.SetValueEx(key, "vibranceGUI", 0, winreg.REG_SZ, f'"{bat_path}"')
        else:
            try:
                winreg.DeleteValue(key, "vibranceGUI")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception:
        pass


def generate_game_badge(exe_name, name_label=""):
    key = exe_name.lower().replace(".exe", "")
    for k, (bg_col, fg_col, txt) in GAME_COLORS.items():
        if k in key:
            return _render_badge(txt, bg_col, fg_col)

    # Generic fallback badge
    short_text = (name_label or key)[:4].upper()
    return _render_badge(short_text, "#334155", "#f8fafc")


def _render_badge(text, bg_color, fg_color):
    img = Image.new("RGBA", (52, 52), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([1, 1, 51, 51], radius=8, fill=bg_color)

    try:
        font = ImageFont.truetype("arialbd.ttf", 14 if len(text) <= 3 else 11)
    except Exception:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (52 - tw) // 2
    y = (52 - th) // 2 - 1
    draw.text((x, y), text, font=font, fill=fg_color)
    return ImageTk.PhotoImage(img)


class GameConfigModal(tk.Toplevel):
    """Minimalist dialog to configure a single game profile."""
    def __init__(self, parent, exe_name, data):
        super().__init__(parent)
        self.title(f"{exe_name}")
        self.geometry("360x390")
        self.configure(bg="#f0f0f0")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.result = None
        self.exe_name = exe_name
        self.data = data or {}

        self.var_name = tk.StringVar(value=self.data.get("name", os.path.splitext(exe_name)[0]))
        self.var_vib = tk.IntVar(value=self.data.get("vibrance", 75))
        self.var_gamma_on = tk.BooleanVar(value=self.data.get("gamma_enabled", False))
        self.var_gamma = tk.DoubleVar(value=self.data.get("gamma", 1.80))
        self.var_boost_on = tk.BooleanVar(value=self.data.get("boost_enabled", False))
        self.var_boost = tk.IntVar(value=self.data.get("boost", 0))

        self._build_ui()

    def _build_ui(self):
        pad = tk.Frame(self, bg="#f0f0f0", padx=16, pady=12)
        pad.pack(fill="both", expand=True)

        # 1. In-game Vibrance
        box_v = tk.LabelFrame(pad, text="In-game Vibrance Level", font=("Segoe UI", 9), bg="#f0f0f0", padx=10, pady=6)
        box_v.pack(fill="x", pady=(0, 8))

        row_v = tk.Frame(box_v, bg="#f0f0f0")
        row_v.pack(fill="x")
        self.lbl_v = tk.Label(row_v, text=f"{self.var_vib.get()}%", font=("Segoe UI", 9, "bold"), bg="#f0f0f0")
        self.lbl_v.pack(side="right")

        scale_v = ttk.Scale(box_v, from_=50, to=100, variable=self.var_vib, command=lambda v: self.lbl_v.config(text=f"{int(float(v))}%"))
        scale_v.pack(fill="x", pady=2)

        # 2. Gamma
        box_g = tk.LabelFrame(pad, text="Gamma Setting", font=("Segoe UI", 9), bg="#f0f0f0", padx=10, pady=6)
        box_g.pack(fill="x", pady=(0, 8))

        row_g = tk.Frame(box_g, bg="#f0f0f0")
        row_g.pack(fill="x")
        chk_g = tk.Checkbutton(row_g, text="Enable Gamma 1.8", variable=self.var_gamma_on, font=("Segoe UI", 9), bg="#f0f0f0", activebackground="#f0f0f0")
        chk_g.pack(side="left")
        self.lbl_g = tk.Label(row_g, text=f"{self.var_gamma.get():.2f}", font=("Segoe UI", 9), bg="#f0f0f0")
        self.lbl_g.pack(side="right")

        scale_g = ttk.Scale(box_g, from_=1.40, to=2.40, variable=self.var_gamma, command=lambda v: self.lbl_g.config(text=f"{float(v):.2f}"))
        scale_g.pack(fill="x", pady=2)

        # 3. Black Boost
        box_b = tk.LabelFrame(pad, text="Black Boost", font=("Segoe UI", 9), bg="#f0f0f0", padx=10, pady=6)
        box_b.pack(fill="x", pady=(0, 14))

        row_b = tk.Frame(box_b, bg="#f0f0f0")
        row_b.pack(fill="x")
        chk_b = tk.Checkbutton(row_b, text="Enable Black Boost", variable=self.var_boost_on, font=("Segoe UI", 9), bg="#f0f0f0", activebackground="#f0f0f0")
        chk_b.pack(side="left")
        self.lbl_b = tk.Label(row_b, text=f"{self.var_boost.get()}%", font=("Segoe UI", 9), bg="#f0f0f0")
        self.lbl_b.pack(side="right")

        scale_b = ttk.Scale(box_b, from_=0, to=100, variable=self.var_boost, command=lambda v: self.lbl_b.config(text=f"{int(float(v))}%"))
        scale_b.pack(fill="x", pady=2)

        # Bottom buttons
        btn_bar = tk.Frame(pad, bg="#f0f0f0")
        btn_bar.pack(fill="x", side="bottom")

        tk.Button(btn_bar, text="Save", font=("Segoe UI", 9), width=10, bg="#ffffff", bd=1, relief="solid", command=self._save).pack(side="left")
        tk.Button(btn_bar, text="Cancel", font=("Segoe UI", 9), width=10, bg="#ffffff", bd=1, relief="solid", command=self.destroy).pack(side="right")

    def _save(self):
        self.result = {
            "name": self.var_name.get(),
            "vibrance_enabled": True,
            "vibrance": int(self.var_vib.get()),
            "gamma_enabled": self.var_gamma_on.get(),
            "gamma": round(float(self.var_gamma.get()), 2),
            "boost_enabled": self.var_boost_on.get(),
            "boost": int(self.var_boost.get())
        }
        self.destroy()


class RunningProcessesModal(tk.Toplevel):
    """Process selection dialog for [Add]."""
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Select Running Game")
        self.geometry("360x360")
        self.configure(bg="#f0f0f0")
        self.transient(parent)
        self.grab_set()

        self.selected_exe = None

        tk.Label(self, text="Select an active process:", font=("Segoe UI", 9), bg="#f0f0f0", padx=12, pady=8).pack(anchor="w")

        f_list = tk.Frame(self, bg="#f0f0f0", padx=12)
        f_list.pack(fill="both", expand=True)

        self.lb = tk.Listbox(f_list, font=("Segoe UI", 9), bd=1, relief="solid", selectmode="single")
        self.lb.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(f_list, orient="vertical", command=self.lb.yview)
        sb.pack(side="right", fill="y")
        self.lb.configure(yscrollcommand=sb.set)

        self._populate()

        b_bar = tk.Frame(self, bg="#f0f0f0", padx=12, pady=10)
        b_bar.pack(fill="x")
        tk.Button(b_bar, text="Add", font=("Segoe UI", 9), width=10, bg="#ffffff", bd=1, relief="solid", command=self._choose).pack(side="left")
        tk.Button(b_bar, text="Cancel", font=("Segoe UI", 9), width=10, bg="#ffffff", bd=1, relief="solid", command=self.destroy).pack(side="right")

    def _populate(self):
        seen = set()
        ignore = ("explorer.exe", "svchost.exe", "python.exe", "pythonw.exe", "taskhostw.exe", "cmd.exe", "conhost.exe")
        for p in psutil.process_iter(['name']):
            try:
                n = p.info['name']
                if n and n.lower() not in seen and n.lower() not in ignore:
                    seen.add(n.lower())
                    self.lb.insert("end", n.lower())
            except Exception:
                pass

    def _choose(self):
        sel = self.lb.curselection()
        if sel:
            self.selected_exe = self.lb.get(sel[0])
            self.destroy()


class VibranceGUIApp:
    def __init__(self, root):
        self.root = root
        self.root.title("vibranceGUI (NVIDIA, 2.3.1.1)")
        self.root.geometry("390x530")
        self.root.configure(bg="#f0f0f0")
        self.root.resizable(False, False)

        self.cfg = load_config()

        # Hardware engines
        self.gamma_engine = GammaEngine()
        self.nv_engine = UnifiedVibranceEngine()

        # State
        self.current_game_exe = None
        self.selected_card_exe = None
        self.badge_images = {}  # Keep references for Tkinter

        self.var_autostart = tk.BooleanVar(value=self.cfg.get("autostart", False))
        self.var_never_res = tk.BooleanVar(value=self.cfg.get("never_change_res", True))
        self.var_desk_vib = tk.IntVar(value=self.cfg.get("desktop", {}).get("vibrance", 50))

        self._monitoring = True

        self._setup_style()
        self._build_ui()
        self._refresh_game_tiles()
        self._start_observer()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _setup_style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TLabel", background="#f0f0f0", font=("Segoe UI", 9))
        style.configure("TLabelframe", background="#f0f0f0")
        style.configure("TLabelframe.Label", background="#f0f0f0", font=("Segoe UI", 9))
        style.configure("TCheckbutton", background="#f0f0f0", font=("Segoe UI", 9))

    def _build_ui(self):
        main_pad = tk.Frame(self.root, bg="#f0f0f0", padx=10, pady=8)
        main_pad.pack(fill="both", expand=True)

        # ---------------- 1. Settings Box ----------------
        box_settings = tk.LabelFrame(main_pad, text="Settings", font=("Segoe UI", 9), bg="#f0f0f0", padx=10, pady=6)
        box_settings.pack(fill="x", pady=(0, 8))

        row_chk1 = tk.Frame(box_settings, bg="#f0f0f0")
        row_chk1.pack(fill="x", pady=1)
        chk_auto = tk.Checkbutton(row_chk1, text="Autostart vibranceGUI", variable=self.var_autostart, font=("Segoe UI", 9), bg="#f0f0f0", activebackground="#f0f0f0", command=self._on_autostart_toggle)
        chk_auto.pack(side="left")

        row_chk2 = tk.Frame(box_settings, bg="#f0f0f0")
        row_chk2.pack(fill="x", pady=1)
        chk_res = tk.Checkbutton(row_chk2, text="Never change resolutions", variable=self.var_never_res, font=("Segoe UI", 9), bg="#f0f0f0", activebackground="#f0f0f0")
        chk_res.pack(side="left")

        # Windows Vibrance Level Sub-box
        box_win = tk.LabelFrame(box_settings, text="Windows Vibrance Level", font=("Segoe UI", 9), bg="#f0f0f0", padx=10, pady=4)
        box_win.pack(fill="x", pady=(4, 2))

        row_w = tk.Frame(box_win, bg="#f0f0f0")
        row_w.pack(fill="x")

        self.lbl_desk_num = tk.Label(row_w, text=f"{self.var_desk_vib.get()}%", font=("Segoe UI", 9), bg="#f0f0f0", width=5, anchor="e")
        self.lbl_desk_num.pack(side="right")

        self.scale_desk = ttk.Scale(row_w, from_=50, to=100, variable=self.var_desk_vib, command=self._on_desktop_slider)
        self.scale_desk.pack(side="left", fill="x", expand=True)

        # ---------------- 2. Program Settings Box ----------------
        box_prog = tk.LabelFrame(main_pad, text="Program Settings", font=("Segoe UI", 9), bg="#f0f0f0", padx=8, pady=6)
        box_prog.pack(fill="both", expand=True, pady=(0, 6))

        # Buttons Row: [ Add ] [ Add manually ] [ Remove ]
        row_btns = tk.Frame(box_prog, bg="#f0f0f0")
        row_btns.pack(fill="x", pady=(0, 6))

        tk.Button(row_btns, text="Add", font=("Segoe UI", 9), width=8, bg="#ffffff", bd=1, relief="solid", cursor="hand2", command=self._add_from_running).pack(side="left", padx=(0, 4))
        tk.Button(row_btns, text="Add manually", font=("Segoe UI", 9), width=12, bg="#ffffff", bd=1, relief="solid", cursor="hand2", command=self._add_manually).pack(side="left", padx=(0, 4))
        tk.Button(row_btns, text="Remove", font=("Segoe UI", 9), width=9, bg="#ffffff", bd=1, relief="solid", cursor="hand2", command=self._remove_selected).pack(side="left")

        # Container for game cards (White box with border)
        self.canvas_games = tk.Canvas(box_prog, bg="#ffffff", highlightthickness=1, highlightbackground="#b0b0b0")
        self.canvas_games.pack(fill="both", expand=True)
        self.canvas_games.bind("<Button-1>", self._on_canvas_click)
        self.canvas_games.bind("<Double-Button-1>", self._on_canvas_dblclick)

        # ---------------- 3. Bottom Status ----------------
        row_stat = tk.Frame(main_pad, bg="#f0f0f0")
        main_pad.pack_propagate(True)
        row_stat.pack(fill="x", side="bottom")

        tk.Label(row_stat, text="Observer status:", font=("Segoe UI", 9), bg="#f0f0f0").pack(side="left")
        self.lbl_observer = tk.Label(row_stat, text="Running!", font=("Segoe UI", 9, "bold"), fg="#16a34a", bg="#f0f0f0", padx=6)
        self.lbl_observer.pack(side="left")

    def _on_desktop_slider(self, val):
        v = int(float(val))
        self.lbl_desk_num.config(text=f"{v}%")
        self.cfg["desktop"]["vibrance"] = v
        save_config(self.cfg)
        if self.current_game_exe is None:
            self.nv_engine.set_level(v)

    def _on_autostart_toggle(self):
        val = self.var_autostart.get()
        self.cfg["autostart"] = val
        save_config(self.cfg)
        update_windows_autostart(val)

    def _refresh_game_tiles(self):
        self.canvas_games.delete("all")
        self.badge_images.clear()
        self.tile_rects = []

        games = self.cfg.get("games", {})
        tile_w = 68
        tile_h = 76
        start_x = 10
        start_y = 10
        cols = 4

        idx = 0
        for exe, prof in games.items():
            col = idx % cols
            row = idx // cols
            x = start_x + col * (tile_w + 12)
            y = start_y + row * (tile_h + 8)

            badge = generate_game_badge(exe, prof.get("name", ""))
            self.badge_images[exe] = badge

            # Selection highlight box
            is_sel = (exe == self.selected_card_exe)
            if is_sel:
                rect_id = self.canvas_games.create_rectangle(x - 4, y - 4, x + tile_w - 4, y + tile_h - 2, fill="#e0f2fe", outline="#0284c7", width=1)
            else:
                rect_id = self.canvas_games.create_rectangle(x - 4, y - 4, x + tile_w - 4, y + tile_h - 2, fill="#ffffff", outline="#ffffff", width=1)

            # Icon Image
            img_id = self.canvas_games.create_image(x + 4, y, image=badge, anchor="nw")

            # Label text
            display_name = prof.get("name", os.path.splitext(exe)[0])
            if len(display_name) > 8:
                display_name = display_name[:7] + "..."
            txt_id = self.canvas_games.create_text(x + 30, y + 56, text=display_name, font=("Segoe UI", 8), fill="#0f172a", anchor="n")

            self.tile_rects.append((x - 4, y - 4, x + tile_w - 4, y + tile_h - 2, exe))
            idx += 1

    def _on_canvas_click(self, event):
        for (x1, y1, x2, y2, exe) in self.tile_rects:
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                self.selected_card_exe = exe
                self._refresh_game_tiles()
                return
        self.selected_card_exe = None
        self._refresh_game_tiles()

    def _on_canvas_dblclick(self, event):
        for (x1, y1, x2, y2, exe) in self.tile_rects:
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                self.selected_card_exe = exe
                self._edit_game(exe)
                return

    def _edit_game(self, exe):
        prof = self.cfg.get("games", {}).get(exe, {})
        modal = GameConfigModal(self.root, exe, prof)
        self.root.wait_window(modal)
        if modal.result:
            self.cfg["games"][exe] = modal.result
            save_config(self.cfg)
            self._refresh_game_tiles()

    def _add_from_running(self):
        modal = RunningProcessesModal(self.root)
        self.root.wait_window(modal)
        if modal.selected_exe:
            exe = modal.selected_exe
            default_data = {
                "name": os.path.splitext(exe)[0],
                "vibrance_enabled": True,
                "vibrance": 75,
                "gamma_enabled": False,
                "gamma": 1.80,
                "boost_enabled": False,
                "boost": 0
            }
            c_modal = GameConfigModal(self.root, exe, default_data)
            self.root.wait_window(c_modal)
            if c_modal.result:
                self.cfg["games"][exe] = c_modal.result
                save_config(self.cfg)
                self.selected_card_exe = exe
                self._refresh_game_tiles()

    def _add_manually(self):
        f = filedialog.askopenfilename(
            parent=self.root,
            title="Select Game",
            filetypes=[("Executable Files", "*.exe")]
        )
        if f:
            exe = os.path.basename(f).lower()
            default_data = {
                "name": os.path.splitext(exe)[0],
                "vibrance_enabled": True,
                "vibrance": 75,
                "gamma_enabled": False,
                "gamma": 1.80,
                "boost_enabled": False,
                "boost": 0
            }
            c_modal = GameConfigModal(self.root, exe, default_data)
            self.root.wait_window(c_modal)
            if c_modal.result:
                self.cfg["games"][exe] = c_modal.result
                save_config(self.cfg)
                self.selected_card_exe = exe
                self._refresh_game_tiles()

    def _remove_selected(self):
        if not self.selected_card_exe:
            return
        exe = self.selected_card_exe
        if exe in self.cfg.get("games", {}):
            del self.cfg["games"][exe]
            save_config(self.cfg)
            self.selected_card_exe = None
            self._refresh_game_tiles()

    def apply_game_profile(self, exe, prof):
        # Vibrance
        if prof.get("vibrance_enabled", True):
            self.nv_engine.set_level(prof.get("vibrance", 75))
        else:
            self.nv_engine.set_level(self.var_desk_vib.get())

        # Gamma / Boost
        if prof.get("gamma_enabled", False) or prof.get("boost_enabled", False):
            g = prof.get("gamma", 1.80) if prof.get("gamma_enabled") else 2.20
            b = (prof.get("boost", 0) / 100.0) if prof.get("boost_enabled") else 0.0
            self.gamma_engine.apply_curve(gamma=g, black_boost=b)
        else:
            self.gamma_engine.restore()

        self.current_game_exe = exe
        self.lbl_observer.config(text=f"{prof.get('name', exe)} Active!", fg="#0284c7")

    def reset_to_desktop(self):
        self.current_game_exe = None
        self.nv_engine.set_level(self.var_desk_vib.get())
        self.gamma_engine.restore()
        self.lbl_observer.config(text="Running!", fg="#16a34a")

    def _start_observer(self):
        t = threading.Thread(target=self._observer_loop, daemon=True)
        t.start()

    def _observer_loop(self):
        import ctypes
        user32 = ctypes.windll.user32

        while self._monitoring:
            try:
                # Keep gamma locked in fullscreen if active profile requires it
                if self.current_game_exe:
                    prof = self.cfg.get("games", {}).get(self.current_game_exe)
                    if prof and (prof.get("gamma_enabled") or prof.get("boost_enabled")):
                        self.gamma_engine.enforce_if_needed()

                hwnd = user32.GetForegroundWindow()
                fg_name = None
                if hwnd:
                    pid = ctypes.c_ulong()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if pid.value > 0:
                        try:
                            proc = psutil.Process(pid.value)
                            fg_name = proc.name().lower()
                        except Exception:
                            pass

                games = self.cfg.get("games", {})
                matched_exe = fg_name if (fg_name and fg_name in games) else None

                if matched_exe and matched_exe != self.current_game_exe:
                    prof = games[matched_exe]
                    self.root.after(0, lambda e=matched_exe, p=prof: self.apply_game_profile(e, p))
                elif not matched_exe and self.current_game_exe is not None:
                    # Tabbed out to Desktop / Other app -> Revert to 50%!
                    self.root.after(0, self.reset_to_desktop)

            except Exception:
                pass

            time.sleep(0.20)

    def on_closing(self):
        self._monitoring = False
        self.reset_to_desktop()
        self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    app = VibranceGUIApp(root)
    root.mainloop()
