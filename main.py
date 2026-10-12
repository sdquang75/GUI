"""
Vibe Color Boost - Pure Software Game Profile Utility
Built with the exact VibranceGUI automation mechanism:
1. When game is in focus -> Apply In-Game Vibrance (e.g. 75%) + optional Gamma (1.8)
2. When game is NOT in focus / Alt-Tab -> Revert to Windows Default (50% Vibrance + clean Gamma)
3. On Reset / Exit -> Always guarantee 100% restore to Windows default (50% DVC + clean Gamma)
"""

import sys
import os
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import psutil

from color_engine import GammaEngine, UnifiedVibranceEngine

PRESETS = {
    "🌟 vibranceGUI (1:1)": {"vib_on": True, "vibrance": 75, "gamma_on": False, "gamma": 2.20, "boost_on": False, "boost": 0},
    "⚡ FPS (Gamma 1.8)": {"vib_on": True, "vibrance": 75, "gamma_on": True, "gamma": 1.80, "boost_on": False, "boost": 0},
    "🔦 Shadow Hunter": {"vib_on": True, "vibrance": 75, "gamma_on": True, "gamma": 1.80, "boost_on": True, "boost": 30},
    "🔄 Reset Default": {"vib_on": True, "vibrance": 50, "gamma_on": False, "gamma": 2.20, "boost_on": False, "boost": 0},
}

DEFAULT_GAME_LIST = "cs2.exe, valorant.exe, valorant-win64-shipping.exe, apex.exe, overwatch.exe, pubg.exe, cod.exe, r5reloaded.exe"

class VibeColorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Vibe Color & Gamma Tuning (vibranceGUI Core)")
        self.root.geometry("640x790")
        self.root.configure(bg="#121214")
        self.root.resizable(False, False)

        # Initialize Engines
        self.gamma_engine = GammaEngine()
        self.nv_engine = UnifiedVibranceEngine()

        # State tracking (VibranceGUI model)
        self.is_in_game = False
        self.manual_override = False
        self.auto_detect_enabled = tk.BooleanVar(value=True)  # Enabled by default like vibranceGUI!
        self.games_list_str = tk.StringVar(value=DEFAULT_GAME_LIST)

        # Feature Toggles
        self.toggle_vibrance = tk.BooleanVar(value=True)
        self.toggle_gamma = tk.BooleanVar(value=False)
        self.toggle_boost = tk.BooleanVar(value=False)

        # Values
        self.var_vibrance = tk.IntVar(value=75)
        self.var_desktop_vib = tk.IntVar(value=50)  # Windows Desktop default
        self.var_gamma = tk.DoubleVar(value=1.80)
        self.var_boost = tk.DoubleVar(value=30.0)

        self._monitoring_active = True

        self._build_ui()
        self._start_detector_thread()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _build_ui(self):
        # Header
        header = tk.Frame(self.root, bg="#18181b", height=70)
        header.pack(fill="x", side="top")

        title_lbl = tk.Label(
            header,
            text="⚡ VIBE COLOR & GAMER PROFILE",
            font=("Segoe UI", 15, "bold"),
            fg="#38bdf8",
            bg="#18181b"
        )
        title_lbl.pack(pady=(12, 2))

        sub_lbl = tk.Label(
            header,
            text="Exact vibranceGUI Mechanism  •  Auto Revert on Alt-Tab  •  Hardware DVC",
            font=("Segoe UI", 9),
            fg="#94a3b8",
            bg="#18181b"
        )
        sub_lbl.pack(pady=(0, 10))

        content = tk.Frame(self.root, bg="#121214", padx=24, pady=12)
        content.pack(fill="both", expand=True)

        # --- Quick Presets ---
        preset_frame = tk.LabelFrame(
            content,
            text="  Quick Presets  ",
            font=("Segoe UI", 10, "bold"),
            fg="#e2e8f0",
            bg="#18181b",
            bd=1,
            relief="groove",
            padx=10,
            pady=8
        )
        preset_frame.pack(fill="x", pady=(0, 10))

        p_btn_row = tk.Frame(preset_frame, bg="#18181b")
        p_btn_row.pack(fill="x")

        for name, values in PRESETS.items():
            btn = tk.Button(
                p_btn_row,
                text=name,
                font=("Segoe UI", 9, "bold"),
                bg="#27272a",
                fg="#f8fafc",
                activebackground="#38bdf8",
                activeforeground="#000",
                bd=0,
                padx=6,
                pady=6,
                cursor="hand2",
                command=lambda v=values: self.apply_preset(v)
            )
            btn.pack(side="left", expand=True, fill="x", padx=3)

        # --- Controls Box ---
        ctrl_frame = tk.LabelFrame(
            content,
            text="  In-Game Settings (Active while playing)  ",
            font=("Segoe UI", 10, "bold"),
            fg="#e2e8f0",
            bg="#18181b",
            bd=1,
            relief="groove",
            padx=16,
            pady=10
        )
        ctrl_frame.pack(fill="x", pady=(0, 10))

        # 1. Digital Vibrance Section
        v_hdr = tk.Frame(ctrl_frame, bg="#18181b")
        v_hdr.pack(fill="x", pady=(2, 2))
        chkVib = tk.Checkbutton(
            v_hdr,
            text="Digital Vibrance (In-game Saturation, revert to 50% on Alt-Tab)",
            variable=self.toggle_vibrance,
            font=("Segoe UI", 9, "bold"),
            fg="#f59e0b",
            bg="#18181b",
            selectcolor="#27272a",
            activebackground="#18181b",
            activeforeground="#f59e0b",
            command=self._on_control_change
        )
        chkVib.pack(side="left")
        self.lbl_vib_val = tk.Label(v_hdr, text=f"{self.var_vibrance.get()}%", font=("Segoe UI", 9, "bold"), fg="#f59e0b", bg="#18181b")
        self.lbl_vib_val.pack(side="right")

        self.slider_vib = tk.Scale(
            ctrl_frame,
            from_=50,
            to=100,
            resolution=1,
            orient="horizontal",
            variable=self.var_vibrance,
            bg="#18181b",
            fg="#cbd5e1",
            highlightthickness=0,
            troughcolor="#27272a",
            activebackground="#f59e0b",
            showvalue=0,
            command=self._on_control_change
        )
        self.slider_vib.pack(fill="x", pady=(0, 8))

        # 2. Pure Gamma Section
        g_hdr = tk.Frame(ctrl_frame, bg="#18181b")
        g_hdr.pack(fill="x", pady=(2, 2))
        chkGamma = tk.Checkbutton(
            g_hdr,
            text="Custom Gamma (Tương đương r_fullscreen_gamma CS2)",
            variable=self.toggle_gamma,
            font=("Segoe UI", 9, "bold"),
            fg="#38bdf8",
            bg="#18181b",
            selectcolor="#27272a",
            activebackground="#18181b",
            activeforeground="#38bdf8",
            command=self._on_control_change
        )
        chkGamma.pack(side="left")
        self.lbl_gamma_val = tk.Label(g_hdr, text=f"{self.var_gamma.get():.2f}", font=("Segoe UI", 9, "bold"), fg="#38bdf8", bg="#18181b")
        self.lbl_gamma_val.pack(side="right")

        self.slider_gamma = tk.Scale(
            ctrl_frame,
            from_=1.40,
            to=2.40,
            resolution=0.05,
            orient="horizontal",
            variable=self.var_gamma,
            bg="#18181b",
            fg="#cbd5e1",
            highlightthickness=0,
            troughcolor="#27272a",
            activebackground="#38bdf8",
            showvalue=0,
            command=self._on_control_change
        )
        self.slider_gamma.pack(fill="x", pady=(0, 8))

        # 3. Black Boost Section
        b_hdr = tk.Frame(ctrl_frame, bg="#18181b")
        b_hdr.pack(fill="x", pady=(2, 2))
        chkBoost = tk.Checkbutton(
            b_hdr,
            text="Black Boost (Nâng sáng góc tối)",
            variable=self.toggle_boost,
            font=("Segoe UI", 8, "bold"),
            fg="#22c55e",
            bg="#18181b",
            selectcolor="#27272a",
            activebackground="#18181b",
            activeforeground="#22c55e",
            command=self._on_control_change
        )
        chkBoost.pack(side="left")
        self.lbl_boost_val = tk.Label(b_hdr, text=f"{int(self.var_boost.get())}%", font=("Segoe UI", 9, "bold"), fg="#22c55e", bg="#18181b")
        self.lbl_boost_val.pack(side="right")

        self.slider_boost = tk.Scale(
            ctrl_frame,
            from_=0,
            to=100,
            resolution=1,
            orient="horizontal",
            variable=self.var_boost,
            bg="#18181b",
            fg="#cbd5e1",
            highlightthickness=0,
            troughcolor="#27272a",
            activebackground="#22c55e",
            showvalue=0,
            command=self._on_control_change
        )
        self.slider_boost.pack(fill="x", pady=(0, 4))

        # --- Visual Curve Graph ---
        graph_box = tk.Frame(content, bg="#18181b", padx=10, pady=4)
        graph_box.pack(fill="x", pady=(0, 10))

        tk.Label(graph_box, text="Live Curve: [Gray = Normal 2.2]  [Cyan = Active Output]", font=("Segoe UI", 8), fg="#94a3b8", bg="#18181b").pack(anchor="w")
        self.canvas_curve = tk.Canvas(graph_box, height=55, bg="#09090b", highlightthickness=1, highlightbackground="#27272a")
        self.canvas_curve.pack(fill="x", pady=(2, 0))

        # --- Automation Box (VibranceGUI style) ---
        auto_frame = tk.LabelFrame(
            content,
            text="  Auto Game Detection (Cơ chế VibranceGUI)  ",
            font=("Segoe UI", 10, "bold"),
            fg="#e2e8f0",
            bg="#18181b",
            bd=1,
            relief="groove",
            padx=14,
            pady=8
        )
        auto_frame.pack(fill="x", pady=(0, 10))

        chk_row = tk.Frame(auto_frame, bg="#18181b")
        chk_row.pack(fill="x", pady=(0, 4))

        chk = tk.Checkbutton(
            chk_row,
            text="Tự động kích hoạt khi vào game & trả về mặc định khi Alt-Tab",
            variable=self.auto_detect_enabled,
            font=("Segoe UI", 9, "bold"),
            fg="#38bdf8",
            bg="#18181b",
            selectcolor="#27272a",
            activebackground="#18181b",
            activeforeground="#38bdf8",
            command=self._on_auto_detect_toggle
        )
        chk.pack(side="left")

        self.status_lbl = tk.Label(
            chk_row,
            text="Status: Desktop (50% Default)",
            font=("Segoe UI", 8, "italic"),
            fg="#94a3b8",
            bg="#18181b"
        )
        self.status_lbl.pack(side="right")

        tk.Label(
            auto_frame,
            text="Danh sách file game (.exe):",
            font=("Segoe UI", 8),
            fg="#94a3b8",
            bg="#18181b"
        ).pack(anchor="w")

        entry_games = tk.Entry(
            auto_frame,
            textvariable=self.games_list_str,
            font=("Segoe UI", 8),
            bg="#27272a",
            fg="#f8fafc",
            insertbackground="#ffffff",
            bd=0,
            relief="flat"
        )
        entry_games.pack(fill="x", pady=(2, 0), ipady=3)

        # --- Main Action Buttons ---
        btn_frame = tk.Frame(content, bg="#121214")
        btn_frame.pack(fill="x")

        self.btn_apply = tk.Button(
            btn_frame,
            text="⚡ TEST/FORCE GAME MODE",
            font=("Segoe UI", 10, "bold"),
            bg="#0284c7",
            fg="#ffffff",
            activebackground="#0369a1",
            activeforeground="#ffffff",
            bd=0,
            pady=8,
            cursor="hand2",
            command=self.force_game_mode
        )
        self.btn_apply.pack(side="left", expand=True, fill="x", padx=(0, 6))

        self.btn_restore = tk.Button(
            btn_frame,
            text="🔄 RESET VỀ MẶC ĐỊNH (50%)",
            font=("Segoe UI", 10, "bold"),
            bg="#334155",
            fg="#ffffff",
            activebackground="#1e293b",
            activeforeground="#ffffff",
            bd=0,
            pady=8,
            cursor="hand2",
            command=self.reset_to_windows_default
        )
        self.btn_restore.pack(side="right", expand=True, fill="x", padx=(6, 0))

        self._update_curve_canvas()

    def _on_control_change(self, _=None):
        self.lbl_gamma_val.config(text=f"{self.var_gamma.get():.2f}")
        self.lbl_boost_val.config(text=f"{int(self.var_boost.get())}%")
        self.lbl_vib_val.config(text=f"{self.var_vibrance.get()}%")
        self._update_curve_canvas()

        # If currently in game or manually forced, apply live!
        if self.is_in_game or self.manual_override:
            self.apply_ingame_settings()

    def _on_auto_detect_toggle(self):
        if not self.auto_detect_enabled.get():
            self.manual_override = False
            self.reset_to_windows_default()

    def _update_curve_canvas(self):
        self.canvas_curve.delete("all")
        w = self.canvas_curve.winfo_width()
        if w < 10:
            w = 560
        h = 55
        padding = 5

        pts_normal = []
        pts_active = []

        use_gamma = self.toggle_gamma.get()
        use_boost = self.toggle_boost.get()
        gamma = self.var_gamma.get() if use_gamma else 2.20
        boost = (self.var_boost.get() / 100.0) if use_boost else 0.0
        base_p = gamma / 2.2

        num_steps = 60
        for step in range(num_steps + 1):
            x = step / float(num_steps)
            cx = padding + x * (w - 2 * padding)

            # Normal 2.2
            y_norm = x
            cy_norm = (h - padding) - y_norm * (h - 2 * padding)
            pts_normal.extend([cx, cy_norm])

            # Active Curve
            lift = (1.0 - x) ** 2 * (x ** 0.6) * 1.5 * boost if (x > 0 and use_boost) else 0.0
            y_act = (x ** base_p) + lift if x > 0 else 0.0
            y_act = max(0.0, min(1.0, y_act))
            cy_act = (h - padding) - y_act * (h - 2 * padding)
            pts_active.extend([cx, cy_act])

        self.canvas_curve.create_line(pts_normal, fill="#475569", width=1, dash=(3, 3))
        self.canvas_curve.create_line(pts_active, fill="#06b6d4", width=2)

    def apply_preset(self, p):
        self.toggle_vibrance.set(p["vib_on"])
        self.var_vibrance.set(p["vibrance"])
        self.toggle_gamma.set(p["gamma_on"])
        self.var_gamma.set(p["gamma"])
        self.toggle_boost.set(p["boost_on"])
        self.var_boost.set(p["boost"])
        self._on_control_change()
        self.force_game_mode()

    def apply_ingame_settings(self):
        """Applies In-Game Vibrance and Gamma."""
        # 1. Vibrance
        if self.toggle_vibrance.get():
            self.nv_engine.set_level(self.var_vibrance.get())
        else:
            self.nv_engine.set_level(50)

        # 2. Gamma & Boost
        use_gamma = self.toggle_gamma.get()
        use_boost = self.toggle_boost.get()
        if use_gamma or use_boost:
            g = self.var_gamma.get() if use_gamma else 2.20
            b = (self.var_boost.get() / 100.0) if use_boost else 0.0
            self.gamma_engine.apply_curve(gamma=g, black_boost=b)
        else:
            self.gamma_engine.restore()

    def reset_to_windows_default(self):
        """Forces 100% restoration to clean Windows defaults (50% DVC + linear gamma)."""
        self.manual_override = False
        self.is_in_game = False
        self.nv_engine.set_level(50)
        self.gamma_engine.restore()
        self.status_lbl.config(text="Status: Desktop (50% Default)", fg="#94a3b8")
        self.btn_apply.config(bg="#0284c7", text="⚡ TEST/FORCE GAME MODE")

    def force_game_mode(self):
        """Manually forces in-game settings for testing."""
        self.manual_override = True
        self.apply_ingame_settings()
        self.status_lbl.config(text="Status: In-Game Active (Manual)", fg="#10b981")
        self.btn_apply.config(bg="#10b981", text="✔ GAME MODE ACTIVE")

    def _start_detector_thread(self):
        t = threading.Thread(target=self._monitor_loop, daemon=True)
        t.start()

    def _monitor_loop(self):
        import ctypes
        user32 = ctypes.windll.user32

        while self._monitoring_active:
            try:
                # If in-game and custom gamma is on, enforce against DirectX wipes
                if (self.is_in_game or self.manual_override) and (self.toggle_gamma.get() or self.toggle_boost.get()):
                    self.gamma_engine.enforce_if_needed()

                if self.auto_detect_enabled.get() and not self.manual_override:
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

                    games = [g.strip().lower() for g in self.games_list_str.get().split(",") if g.strip()]
                    is_match = fg_name in games if fg_name else False

                    if is_match and not self.is_in_game:
                        # Entered game!
                        self.is_in_game = True
                        self.root.after(0, self.apply_ingame_settings)
                        self.root.after(0, lambda n=fg_name: self.status_lbl.config(text=f"Active Game: {n}", fg="#10b981"))
                        self.root.after(0, lambda: self.btn_apply.config(bg="#10b981", text="✔ IN-GAME ACTIVE"))
                    elif not is_match and self.is_in_game:
                        # Left game (Alt-Tab to Desktop/Chrome/etc.) -> REVERT IMMEDIATELY!
                        self.is_in_game = False
                        self.root.after(0, self.reset_to_windows_default)

            except Exception:
                pass

            time.sleep(0.25)

    def on_closing(self):
        self._monitoring_active = False
        self.reset_to_windows_default()
        self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    app = VibeColorApp(root)
    root.mainloop()
