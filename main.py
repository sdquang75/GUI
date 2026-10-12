"""
Vibe Color & Gamer Profile Utility (vibranceGUI Architecture)
Per-game profile manager with automated foreground detection:
- Each game has its own independent profile (Vibrance, Gamma, Black Boost).
- Auto-switches profile when game window is active.
- Auto-reverts to Windows Desktop default (50% DVC + clean gamma) on Alt-Tab.
- Config persistent in config.json.
- Allows browsing .exe or picking directly from currently running games!
"""

import sys
import os
import json
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import psutil

from color_engine import GammaEngine, UnifiedVibranceEngine

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "desktop": {
        "vibrance": 50
    },
    "auto_detect": True,
    "games": {
        "cs2.exe": {
            "name": "Counter-Strike 2",
            "vibrance_enabled": True,
            "vibrance": 80,
            "gamma_enabled": True,
            "gamma": 1.80,
            "boost_enabled": False,
            "boost": 0
        },
        "valorant-win64-shipping.exe": {
            "name": "VALORANT",
            "vibrance_enabled": True,
            "vibrance": 75,
            "gamma_enabled": False,
            "gamma": 2.20,
            "boost_enabled": False,
            "boost": 0
        },
        "r5reloaded.exe": {
            "name": "Apex Legends",
            "vibrance_enabled": True,
            "vibrance": 75,
            "gamma_enabled": True,
            "gamma": 1.80,
            "boost_enabled": True,
            "boost": 25
        }
    }
}


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print("Failed to save config:", e)


class GameProfileDialog(tk.Toplevel):
    """Dialog to create or edit a game profile."""
    def __init__(self, parent, exe_name="", profile_data=None):
        super().__init__(parent)
        self.title("Game Profile Settings" if exe_name else "Add New Game")
        self.geometry("500x560")
        self.configure(bg="#18181b")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.result = None
        self.exe_name = exe_name
        data = profile_data or {
            "name": os.path.splitext(exe_name)[0] if exe_name else "New Game",
            "vibrance_enabled": True,
            "vibrance": 75,
            "gamma_enabled": False,
            "gamma": 1.80,
            "boost_enabled": False,
            "boost": 0
        }

        self.var_name = tk.StringVar(value=data.get("name", ""))
        self.var_exe = tk.StringVar(value=exe_name)
        self.var_vib_on = tk.BooleanVar(value=data.get("vibrance_enabled", True))
        self.var_vib = tk.IntVar(value=data.get("vibrance", 75))
        self.var_gamma_on = tk.BooleanVar(value=data.get("gamma_enabled", False))
        self.var_gamma = tk.DoubleVar(value=data.get("gamma", 1.80))
        self.var_boost_on = tk.BooleanVar(value=data.get("boost_enabled", False))
        self.var_boost = tk.IntVar(value=data.get("boost", 0))

        self._build_ui()

    def _build_ui(self):
        pad_frame = tk.Frame(self, bg="#18181b", padx=20, pady=16)
        pad_frame.pack(fill="both", expand=True)

        # Header info
        tk.Label(pad_frame, text="Configure In-Game Profile", font=("Segoe UI", 12, "bold"), fg="#38bdf8", bg="#18181b").pack(anchor="w", pady=(0, 12))

        # Game Name
        tk.Label(pad_frame, text="Game Title:", font=("Segoe UI", 9, "bold"), fg="#e2e8f0", bg="#18181b").pack(anchor="w")
        ent_name = tk.Entry(pad_frame, textvariable=self.var_name, font=("Segoe UI", 9), bg="#27272a", fg="#f8fafc", bd=0)
        ent_name.pack(fill="x", pady=(2, 8), ipady=3)

        # Executable
        tk.Label(pad_frame, text="Process (.exe):", font=("Segoe UI", 9, "bold"), fg="#e2e8f0", bg="#18181b").pack(anchor="w")
        row_exe = tk.Frame(pad_frame, bg="#18181b")
        row_exe.pack(fill="x", pady=(2, 14))

        ent_exe = tk.Entry(row_exe, textvariable=self.var_exe, font=("Segoe UI", 9), bg="#27272a", fg="#f8fafc", bd=0)
        ent_exe.pack(side="left", fill="x", expand=True, ipady=3)

        btn_browse = tk.Button(row_exe, text="Browse...", font=("Segoe UI", 8, "bold"), bg="#3f3f46", fg="#fff", bd=0, padx=8, command=self._browse_exe)
        btn_browse.pack(side="right", padx=(6, 0))

        # --- Digital Vibrance ---
        box_vib = tk.LabelFrame(pad_frame, text="  Digital Vibrance (Hardware DVC)  ", font=("Segoe UI", 9, "bold"), fg="#f59e0b", bg="#18181b", bd=1, padx=12, pady=6)
        box_vib.pack(fill="x", pady=(0, 10))

        v_row = tk.Frame(box_vib, bg="#18181b")
        v_row.pack(fill="x")
        tk.Checkbutton(v_row, text="Enable Vibrance for this game", variable=self.var_vib_on, font=("Segoe UI", 8), fg="#cbd5e1", bg="#18181b", selectcolor="#27272a", activebackground="#18181b").pack(side="left")
        self.lbl_v_val = tk.Label(v_row, text=f"{self.var_vib.get()}%", font=("Segoe UI", 8, "bold"), fg="#f59e0b", bg="#18181b")
        self.lbl_v_val.pack(side="right")

        scale_vib = tk.Scale(box_vib, from_=50, to=100, orient="horizontal", variable=self.var_vib, bg="#18181b", fg="#cbd5e1", highlightthickness=0, troughcolor="#27272a", activebackground="#f59e0b", showvalue=0, command=lambda v: self.lbl_v_val.config(text=f"{v}%"))
        scale_vib.pack(fill="x", pady=(2, 4))

        # --- Gamma ---
        box_gam = tk.LabelFrame(pad_frame, text="  Custom Gamma (r_fullscreen_gamma)  ", font=("Segoe UI", 9, "bold"), fg="#38bdf8", bg="#18181b", bd=1, padx=12, pady=6)
        box_gam.pack(fill="x", pady=(0, 10))

        g_row = tk.Frame(box_gam, bg="#18181b")
        g_row.pack(fill="x")
        tk.Checkbutton(g_row, text="Enable Gamma (e.g. 1.80 for CS2)", variable=self.var_gamma_on, font=("Segoe UI", 8), fg="#cbd5e1", bg="#18181b", selectcolor="#27272a", activebackground="#18181b").pack(side="left")
        self.lbl_g_val = tk.Label(g_row, text=f"{self.var_gamma.get():.2f}", font=("Segoe UI", 8, "bold"), fg="#38bdf8", bg="#18181b")
        self.lbl_g_val.pack(side="right")

        scale_gam = tk.Scale(box_gam, from_=1.40, to=2.40, resolution=0.05, orient="horizontal", variable=self.var_gamma, bg="#18181b", fg="#cbd5e1", highlightthickness=0, troughcolor="#27272a", activebackground="#38bdf8", showvalue=0, command=lambda v: self.lbl_g_val.config(text=f"{float(v):.2f}"))
        scale_gam.pack(fill="x", pady=(2, 4))

        # --- Black Boost ---
        box_bst = tk.LabelFrame(pad_frame, text="  Black Boost (Shadow Equalizer)  ", font=("Segoe UI", 9, "bold"), fg="#22c55e", bg="#18181b", bd=1, padx=12, pady=6)
        box_bst.pack(fill="x", pady=(0, 14))

        b_row = tk.Frame(box_bst, bg="#18181b")
        b_row.pack(fill="x")
        tk.Checkbutton(b_row, text="Enable Black Boost (Nâng sáng góc tối)", variable=self.var_boost_on, font=("Segoe UI", 8), fg="#cbd5e1", bg="#18181b", selectcolor="#27272a", activebackground="#18181b").pack(side="left")
        self.lbl_b_val = tk.Label(b_row, text=f"{self.var_boost.get()}%", font=("Segoe UI", 8, "bold"), fg="#22c55e", bg="#18181b")
        self.lbl_b_val.pack(side="right")

        scale_bst = tk.Scale(box_bst, from_=0, to=100, orient="horizontal", variable=self.var_boost, bg="#18181b", fg="#cbd5e1", highlightthickness=0, troughcolor="#27272a", activebackground="#22c55e", showvalue=0, command=lambda v: self.lbl_b_val.config(text=f"{v}%"))
        scale_bst.pack(fill="x", pady=(2, 4))

        # Buttons
        btn_bar = tk.Frame(pad_frame, bg="#18181b")
        pad_frame.pack_propagate(True)
        btn_bar.pack(fill="x", side="bottom")

        tk.Button(btn_bar, text="Save Profile", font=("Segoe UI", 9, "bold"), bg="#0284c7", fg="#fff", bd=0, padx=16, pady=6, cursor="hand2", command=self._on_save).pack(side="left")
        tk.Button(btn_bar, text="Cancel", font=("Segoe UI", 9), bg="#3f3f46", fg="#fff", bd=0, padx=14, pady=6, cursor="hand2", command=self.destroy).pack(side="right")

    def _browse_exe(self):
        f = filedialog.askopenfilename(
            parent=self,
            title="Select Game Executable",
            filetypes=[("Executable Files", "*.exe"), ("All Files", "*.*")]
        )
        if f:
            exe = os.path.basename(f).lower()
            self.var_exe.set(exe)
            if not self.var_name.get() or self.var_name.get() == "New Game":
                self.var_name.set(os.path.splitext(exe)[0].upper())

    def _on_save(self):
        exe = self.var_exe.get().strip().lower()
        if not exe:
            messagebox.showwarning("Validation Error", "Please specify an executable (.exe) name!", parent=self)
            return
        if not exe.endswith(".exe"):
            exe += ".exe"

        name = self.var_name.get().strip() or os.path.splitext(exe)[0]

        self.result = (exe, {
            "name": name,
            "vibrance_enabled": self.var_vib_on.get(),
            "vibrance": int(self.var_vib.get()),
            "gamma_enabled": self.var_gamma_on.get(),
            "gamma": round(float(self.var_gamma.get()), 2),
            "boost_enabled": self.var_boost_on.get(),
            "boost": int(self.var_boost.get())
        })
        self.destroy()


class ProcessPickerModal(tk.Toplevel):
    """Allows user to pick a running game process instead of digging through folders."""
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Select Running Game Process")
        self.geometry("450x400")
        self.configure(bg="#18181b")
        self.transient(parent)
        self.grab_set()

        self.selected_exe = None

        tk.Label(self, text="Select an active game process:", font=("Segoe UI", 10, "bold"), fg="#38bdf8", bg="#18181b", padx=14, pady=10).pack(anchor="w")

        # Treeview
        frame_list = tk.Frame(self, bg="#18181b", padx=14)
        frame_list.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(frame_list, columns=("exe", "name"), show="headings", height=10)
        self.tree.heading("exe", text="Process (.exe)")
        self.tree.heading("name", text="Window / Description")
        self.tree.column("exe", width=160)
        self.tree.column("name", width=240)
        self.tree.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(frame_list, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)

        self._populate_processes()

        btn_bar = tk.Frame(self, bg="#18181b", padx=14, pady=10)
        btn_bar.pack(fill="x")

        tk.Button(btn_bar, text="Choose Selected", font=("Segoe UI", 9, "bold"), bg="#0284c7", fg="#fff", bd=0, padx=12, pady=6, cursor="hand2", command=self._on_choose).pack(side="left")
        tk.Button(btn_bar, text="Cancel", font=("Segoe UI", 9), bg="#3f3f46", fg="#fff", bd=0, padx=12, pady=6, cursor="hand2", command=self.destroy).pack(side="right")

    def _populate_processes(self):
        seen = set()
        for p in psutil.process_iter(['name', 'exe']):
            try:
                name = p.info['name']
                if name and name.lower() not in seen:
                    exe_low = name.lower()
                    if exe_low not in ("explorer.exe", "svchost.exe", "python.exe", "pythonw.exe", "taskhostw.exe", "cmd.exe", "conhost.exe"):
                        seen.add(exe_low)
                        self.tree.insert("", "end", values=(exe_low, p.info.get('name', '')))
            except Exception:
                pass

    def _on_choose(self):
        sel = self.tree.selection()
        if not sel:
            return
        vals = self.tree.item(sel[0], "values")
        if vals:
            self.selected_exe = vals[0]
            self.destroy()


class VibeColorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Vibe Color & Gamer Profiles (vibranceGUI Style)")
        self.root.geometry("680x760")
        self.root.configure(bg="#121214")
        self.root.resizable(False, False)

        self.cfg = load_config()

        # Hardware engines
        self.gamma_engine = GammaEngine()
        self.nv_engine = UnifiedVibranceEngine()

        # State tracking
        self.current_active_game = None
        self.auto_detect_enabled = tk.BooleanVar(value=self.cfg.get("auto_detect", True))
        self.desktop_vib_var = tk.IntVar(value=self.cfg.get("desktop", {}).get("vibrance", 50))

        self._monitoring_active = True

        self._setup_styles()
        self._build_ui()
        self._refresh_games_tree()
        self._start_detector_thread()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview",
                        background="#18181b",
                        foreground="#f8fafc",
                        fieldbackground="#18181b",
                        rowheight=28,
                        font=("Segoe UI", 9))
        style.configure("Treeview.Heading",
                        background="#27272a",
                        foreground="#38bdf8",
                        font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#0284c7")])

    def _build_ui(self):
        # 1. Header
        header = tk.Frame(self.root, bg="#18181b", height=70)
        header.pack(fill="x", side="top")

        tk.Label(header, text="⚡ VIBE COLOR & GAMER PROFILES", font=("Segoe UI", 14, "bold"), fg="#38bdf8", bg="#18181b").pack(pady=(10, 2))
        tk.Label(header, text="Per-Game Profiles  •  Auto Revert on Alt-Tab  •  Hardware DVC 1:1 vibranceGUI", font=("Segoe UI", 8), fg="#94a3b8", bg="#18181b").pack(pady=(0, 8))

        content = tk.Frame(self.root, bg="#121214", padx=20, pady=12)
        content.pack(fill="both", expand=True)

        # 2. Windows Desktop Default Section (vibranceGUI model)
        box_desk = tk.LabelFrame(content, text="  Windows / Desktop Default  ", font=("Segoe UI", 9, "bold"), fg="#e2e8f0", bg="#18181b", bd=1, padx=14, pady=8)
        box_desk.pack(fill="x", pady=(0, 10))

        d_row = tk.Frame(box_desk, bg="#18181b")
        d_row.pack(fill="x")
        tk.Label(d_row, text="Desktop Vibrance Level (Applied outside of games):", font=("Segoe UI", 8), fg="#cbd5e1", bg="#18181b").pack(side="left")
        self.lbl_desk_val = tk.Label(d_row, text=f"{self.desktop_vib_var.get()}% (Default)", font=("Segoe UI", 8, "bold"), fg="#38bdf8", bg="#18181b")
        self.lbl_desk_val.pack(side="right")

        self.scale_desk = tk.Scale(box_desk, from_=50, to=100, orient="horizontal", variable=self.desktop_vib_var, bg="#18181b", fg="#cbd5e1", highlightthickness=0, troughcolor="#27272a", activebackground="#38bdf8", showvalue=0, command=self._on_desktop_slider)
        self.scale_desk.pack(fill="x", pady=(2, 0))

        # 3. Game Profiles Section (Table / List)
        box_games = tk.LabelFrame(content, text="  Configured Game Profiles  ", font=("Segoe UI", 9, "bold"), fg="#e2e8f0", bg="#18181b", bd=1, padx=12, pady=10)
        box_games.pack(fill="both", expand=True, pady=(0, 10))

        tree_frame = tk.Frame(box_games, bg="#18181b")
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(tree_frame, columns=("name", "exe", "vib", "gamma", "boost"), show="headings", height=8)
        self.tree.heading("name", text="Game Title")
        self.tree.heading("exe", text="Process (.exe)")
        self.tree.heading("vib", text="Vibrance")
        self.tree.heading("gamma", text="Gamma")
        self.tree.heading("boost", text="Black Boost")

        self.tree.column("name", width=160)
        self.tree.column("exe", width=170)
        self.tree.column("vib", width=90, anchor="center")
        self.tree.column("gamma", width=90, anchor="center")
        self.tree.column("boost", width=90, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)

        tree_sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        tree_sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=tree_sb.set)
        self.tree.bind("<Double-1>", lambda e: self.edit_selected_game())

        # Buttons under Table
        btn_row = tk.Frame(box_games, bg="#18181b")
        btn_row.pack(fill="x", pady=(8, 0))

        tk.Button(btn_row, text="➕ Add Game (.exe)", font=("Segoe UI", 8, "bold"), bg="#0284c7", fg="#fff", bd=0, padx=10, pady=5, cursor="hand2", command=self.add_game_manual).pack(side="left", padx=2)
        tk.Button(btn_row, text="🎮 Add from Running", font=("Segoe UI", 8, "bold"), bg="#0369a1", fg="#fff", bd=0, padx=10, pady=5, cursor="hand2", command=self.add_game_from_process).pack(side="left", padx=2)
        tk.Button(btn_row, text="✏️ Edit Profile", font=("Segoe UI", 8, "bold"), bg="#3f3f46", fg="#fff", bd=0, padx=10, pady=5, cursor="hand2", command=self.edit_selected_game).pack(side="left", padx=2)
        tk.Button(btn_row, text="🗑️ Remove", font=("Segoe UI", 8, "bold"), bg="#dc2626", fg="#fff", bd=0, padx=10, pady=5, cursor="hand2", command=self.remove_selected_game).pack(side="right", padx=2)

        # 4. Status & Automation Controls
        bot_box = tk.Frame(content, bg="#121214")
        bot_box.pack(fill="x")

        status_box = tk.Frame(bot_box, bg="#18181b", bd=1, relief="groove", padx=12, pady=8)
        status_box.pack(fill="x", pady=(0, 8))

        s_row = tk.Frame(status_box, bg="#18181b")
        s_row.pack(fill="x")

        tk.Checkbutton(s_row, text="Tự động áp dụng khi vào game & trả về mặc định khi Alt-Tab", variable=self.auto_detect_enabled, font=("Segoe UI", 9, "bold"), fg="#38bdf8", bg="#18181b", selectcolor="#27272a", activebackground="#18181b", command=self._on_auto_detect_change).pack(side="left")

        self.lbl_status = tk.Label(status_box, text="Current Display State: Desktop (50% Default)", font=("Segoe UI", 8, "italic"), fg="#94a3b8", bg="#18181b")
        self.lbl_status.pack(anchor="w", pady=(4, 0))

        # Bottom manual triggers
        act_row = tk.Frame(bot_box, bg="#121214")
        act_row.pack(fill="x")

        tk.Button(act_row, text="⚡ TEST SELECTED GAME PROFILE", font=("Segoe UI", 9, "bold"), bg="#0284c7", fg="#fff", bd=0, pady=7, cursor="hand2", command=self.test_selected_profile).pack(side="left", expand=True, fill="x", padx=(0, 4))
        tk.Button(act_row, text="🔄 RESET VỀ DESKTOP DEFAULT (50%)", font=("Segoe UI", 9, "bold"), bg="#334155", fg="#fff", bd=0, pady=7, cursor="hand2", command=self.reset_to_desktop).pack(side="right", expand=True, fill="x", padx=(4, 0))

    def _on_desktop_slider(self, val):
        self.lbl_desk_val.config(text=f"{val}%")
        self.cfg["desktop"]["vibrance"] = int(val)
        save_config(self.cfg)
        if self.current_active_game is None:
            self.nv_engine.set_level(int(val))

    def _on_auto_detect_change(self):
        self.cfg["auto_detect"] = self.auto_detect_enabled.get()
        save_config(self.cfg)
        if not self.auto_detect_enabled.get():
            self.reset_to_desktop()

    def _refresh_games_tree(self):
        self.tree.delete(*self.tree.get_children())
        games = self.cfg.get("games", {})
        for exe, prof in games.items():
            vib_str = f"{prof.get('vibrance', 75)}%" if prof.get("vibrance_enabled", True) else "Off"
            gam_str = f"{prof.get('gamma', 1.80):.2f}" if prof.get("gamma_enabled", False) else "Off (2.2)"
            bst_str = f"{prof.get('boost', 0)}%" if prof.get("boost_enabled", False) else "Off"
            self.tree.insert("", "end", iid=exe, values=(prof.get("name", exe), exe, vib_str, gam_str, bst_str))

    def add_game_manual(self):
        dlg = GameProfileDialog(self.root)
        self.root.wait_window(dlg)
        if dlg.result:
            exe, data = dlg.result
            if "games" not in self.cfg:
                self.cfg["games"] = {}
            self.cfg["games"][exe] = data
            save_config(self.cfg)
            self._refresh_games_tree()

    def add_game_from_process(self):
        picker = ProcessPickerModal(self.root)
        self.root.wait_window(picker)
        if picker.selected_exe:
            dlg = GameProfileDialog(self.root, exe_name=picker.selected_exe)
            self.root.wait_window(dlg)
            if dlg.result:
                exe, data = dlg.result
                if "games" not in self.cfg:
                    self.cfg["games"] = {}
                self.cfg["games"][exe] = data
                save_config(self.cfg)
                self._refresh_games_tree()

    def edit_selected_game(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select Game", "Please select a game from the list to edit.", parent=self.root)
            return
        exe = sel[0]
        data = self.cfg.get("games", {}).get(exe)
        dlg = GameProfileDialog(self.root, exe_name=exe, profile_data=data)
        self.root.wait_window(dlg)
        if dlg.result:
            new_exe, new_data = dlg.result
            if new_exe != exe and exe in self.cfg.get("games", {}):
                del self.cfg["games"][exe]
            self.cfg["games"][new_exe] = new_data
            save_config(self.cfg)
            self._refresh_games_tree()

    def remove_selected_game(self):
        sel = self.tree.selection()
        if not sel:
            return
        exe = sel[0]
        if messagebox.askyesno("Confirm Delete", f"Remove profile for '{exe}'?", parent=self.root):
            if exe in self.cfg.get("games", {}):
                del self.cfg["games"][exe]
                save_config(self.cfg)
                self._refresh_games_tree()

    def apply_game_profile(self, exe, prof):
        # 1. Vibrance
        if prof.get("vibrance_enabled", True):
            self.nv_engine.set_level(prof.get("vibrance", 75))
        else:
            self.nv_engine.set_level(self.desktop_vib_var.get())

        # 2. Gamma & Boost
        use_gamma = prof.get("gamma_enabled", False)
        use_boost = prof.get("boost_enabled", False)
        if use_gamma or use_boost:
            g = prof.get("gamma", 1.80) if use_gamma else 2.20
            b = (prof.get("boost", 0) / 100.0) if use_boost else 0.0
            self.gamma_engine.apply_curve(gamma=g, black_boost=b)
        else:
            self.gamma_engine.restore()

        self.current_active_game = exe
        self.lbl_status.config(
            text=f"Active Game: {prof.get('name', exe)} ({exe}) [Vibrance: {prof.get('vibrance', 75)}%]",
            fg="#10b981"
        )

    def reset_to_desktop(self):
        self.current_active_game = None
        self.nv_engine.set_level(self.desktop_vib_var.get())
        self.gamma_engine.restore()
        self.lbl_status.config(
            text=f"Current Display State: Desktop ({self.desktop_vib_var.get()}% Default)",
            fg="#94a3b8"
        )

    def test_selected_profile(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select Game", "Please select a game to test its profile.", parent=self.root)
            return
        exe = sel[0]
        prof = self.cfg.get("games", {}).get(exe)
        if prof:
            self.apply_game_profile(exe, prof)

    def _start_detector_thread(self):
        t = threading.Thread(target=self._monitor_loop, daemon=True)
        t.start()

    def _monitor_loop(self):
        import ctypes
        user32 = ctypes.windll.user32

        while self._monitoring_active:
            try:
                # If active game profile has custom gamma, enforce in case DirectX wiped it
                if self.current_active_game:
                    prof = self.cfg.get("games", {}).get(self.current_active_game)
                    if prof and (prof.get("gamma_enabled") or prof.get("boost_enabled")):
                        self.gamma_engine.enforce_if_needed()

                if self.auto_detect_enabled.get():
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

                    if matched_exe and matched_exe != self.current_active_game:
                        # Entered a configured game!
                        prof = games[matched_exe]
                        self.root.after(0, lambda e=matched_exe, p=prof: self.apply_game_profile(e, p))
                    elif not matched_exe and self.current_active_game is not None:
                        # Tabbed out to Desktop / Browser / Discord -> Revert to desktop default!
                        self.root.after(0, self.reset_to_desktop)

            except Exception:
                pass

            time.sleep(0.25)

    def on_closing(self):
        self._monitoring_active = False
        self.reset_to_desktop()
        self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    app = VibeColorApp(root)
    root.mainloop()
