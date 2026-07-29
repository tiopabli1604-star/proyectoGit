# -*- coding: utf-8 -*-
"""
MacroPro — Grabador/reproductor de macros + Auto-Captcha por color.

Modo 1 (Macro): graba TODOS los eventos de ratón (movimiento, clic, arrastre,
rueda) y teclado con tiempos exactos, y los reproduce de forma idéntica.
Modo 2 (Auto-Captcha): vigila la pantalla y cuando aparece una zona verde
señalizada, hace clic en su centro automáticamente (esté donde esté).

Hotkeys globales:
  F6 = grabar / parar grabación
  F7 = reproducir / parar reproducción
  F9 = activar / desactivar Auto-Captcha
"""

import ctypes
import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# DPI-aware: imprescindible para que las coordenadas de pynput y mss coincidan
# con los píxeles físicos de la pantalla en Windows con escalado.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# Resolución de temporizador de 1 ms para reproducción precisa.
try:
    ctypes.windll.winmm.timeBeginPeriod(1)
except Exception:
    pass

import numpy as np
import cv2
import mss
from pynput import mouse, keyboard
from pynput.mouse import Button, Controller as MouseController
from pynput.keyboard import Key, KeyCode, Controller as KeyboardController

APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
CONFIG_PATH = os.path.join(APP_DIR, "macropro_config.json")

HOTKEY_RECORD = keyboard.Key.f6
HOTKEY_PLAY = keyboard.Key.f7
HOTKEY_CAPTCHA = keyboard.Key.f9
HOTKEYS = {HOTKEY_RECORD, HOTKEY_PLAY, HOTKEY_CAPTCHA}


# ---------------------------------------------------------------- utilidades

def key_to_str(key):
    if isinstance(key, KeyCode):
        if key.char is not None:
            return "c:" + key.char
        return "v:" + str(key.vk)
    return "k:" + key.name


def str_to_key(s):
    kind, _, val = s.partition(":")
    if kind == "c":
        return val
    if kind == "v":
        return KeyCode.from_vk(int(val))
    return getattr(Key, val)


def precise_sleep_until(t_target, t0):
    """Duerme hasta perf_counter() - t0 >= t_target con precisión ~1ms."""
    while True:
        remaining = t_target - (time.perf_counter() - t0)
        if remaining <= 0:
            return
        if remaining > 0.003:
            time.sleep(remaining - 0.002)
        else:
            time.sleep(0)  # spin suave


# ---------------------------------------------------------------- grabador

class Recorder:
    """Graba eventos globales de ratón y teclado con marca de tiempo."""

    MOVE_MIN_INTERVAL = 0.008  # ~125 muestras/s de movimiento: fluido y ligero

    def __init__(self):
        self.events = []
        self.recording = False
        self._t0 = 0.0
        self._last_move_t = 0.0
        self._m_listener = None
        self._k_listener = None
        self.lock = threading.Lock()

    def start(self):
        with self.lock:
            self.events = []
            self._t0 = time.perf_counter()
            self._last_move_t = -1.0
            self.recording = True
        self._m_listener = mouse.Listener(
            on_move=self._on_move, on_click=self._on_click,
            on_scroll=self._on_scroll)
        self._k_listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release)
        self._m_listener.start()
        self._k_listener.start()

    def stop(self):
        self.recording = False
        if self._m_listener:
            self._m_listener.stop()
            self._m_listener = None
        if self._k_listener:
            self._k_listener.stop()
            self._k_listener = None
        return list(self.events)

    def _now(self):
        return time.perf_counter() - self._t0

    def _on_move(self, x, y):
        if not self.recording:
            return
        t = self._now()
        if t - self._last_move_t < self.MOVE_MIN_INTERVAL:
            return
        self._last_move_t = t
        self.events.append({"t": t, "e": "mm", "x": x, "y": y})

    def _on_click(self, x, y, button, pressed):
        if not self.recording:
            return
        self.events.append({"t": self._now(), "e": "mc", "x": x, "y": y,
                            "b": button.name, "d": pressed})

    def _on_scroll(self, x, y, dx, dy):
        if not self.recording:
            return
        self.events.append({"t": self._now(), "e": "ms", "x": x, "y": y,
                            "dx": dx, "dy": dy})

    def _on_press(self, key):
        if not self.recording or key in HOTKEYS:
            return
        self.events.append({"t": self._now(), "e": "kd", "k": key_to_str(key)})

    def _on_release(self, key):
        if not self.recording or key in HOTKEYS:
            return
        self.events.append({"t": self._now(), "e": "ku", "k": key_to_str(key)})


# ---------------------------------------------------------------- reproductor

class Player:
    """Reproduce una lista de eventos con la misma cadencia temporal."""

    def __init__(self, on_finish=None, on_progress=None):
        self.mouse = MouseController()
        self.keyboard = KeyboardController()
        self.playing = False
        self._thread = None
        self.on_finish = on_finish
        self.on_progress = on_progress

    def play(self, events, speed=1.0, repeats=1):
        if self.playing or not events:
            return
        self.playing = True
        self._thread = threading.Thread(
            target=self._run, args=(events, speed, repeats), daemon=True)
        self._thread.start()

    def stop(self):
        self.playing = False

    def _run(self, events, speed, repeats):
        loop = 0
        try:
            while self.playing and (repeats == 0 or loop < repeats):
                loop += 1
                if self.on_progress:
                    self.on_progress(loop, repeats)
                t0 = time.perf_counter()
                for ev in events:
                    if not self.playing:
                        break
                    precise_sleep_until(ev["t"] / speed, t0)
                    if not self.playing:
                        break
                    self._exec(ev)
                self._release_all(events)
        finally:
            self.playing = False
            if self.on_finish:
                self.on_finish()

    def _exec(self, ev):
        e = ev["e"]
        if e == "mm":
            self.mouse.position = (ev["x"], ev["y"])
        elif e == "mc":
            self.mouse.position = (ev["x"], ev["y"])
            btn = getattr(Button, ev["b"])
            if ev["d"]:
                self.mouse.press(btn)
            else:
                self.mouse.release(btn)
        elif e == "ms":
            self.mouse.position = (ev["x"], ev["y"])
            self.mouse.scroll(ev["dx"], ev["dy"])
        elif e == "kd":
            self.keyboard.press(str_to_key(ev["k"]))
        elif e == "ku":
            self.keyboard.release(str_to_key(ev["k"]))

    def _release_all(self, events):
        """Suelta cualquier tecla/botón que quedara pulsado al cortar."""
        pressed_keys, pressed_btns = set(), set()
        for ev in events:
            if ev["e"] == "kd":
                pressed_keys.add(ev["k"])
            elif ev["e"] == "ku":
                pressed_keys.discard(ev["k"])
            elif ev["e"] == "mc":
                if ev["d"]:
                    pressed_btns.add(ev["b"])
                else:
                    pressed_btns.discard(ev["b"])
        for k in pressed_keys:
            try:
                self.keyboard.release(str_to_key(k))
            except Exception:
                pass
        for b in pressed_btns:
            try:
                self.mouse.release(getattr(Button, b))
            except Exception:
                pass


# ---------------------------------------------------------------- auto-captcha

class CaptchaWatcher:
    """Vigila la pantalla y clica la zona verde cuando aparece.

    Detección por color en HSV sobre captura de pantalla (mss + OpenCV).
    Exige ver el blob en 2 frames seguidos antes de clicar (anti-falsos
    positivos) y aplica un cooldown tras cada clic.
    """

    def __init__(self, log_fn):
        self.mouse = MouseController()
        self.log = log_fn
        self.active = False
        self.paused = False           # se pausa durante grabación/reproducción
        self._thread = None
        # Parámetros configurables desde la GUI:
        self.hue = 60                 # verde puro en OpenCV (rango 0-179: 60)
        self.hue_tol = 15
        self.sat_min = 120
        self.val_min = 120
        self.min_area = 400           # px² a resolución reducida (x0.5 -> x4 real)
        self.interval = 0.7           # s entre escaneos
        self.cooldown = 10.0          # s tras un clic
        self.double_click = False
        self.restore_mouse = True
        self.clicks_done = 0

    def start(self):
        if self.active:
            return
        self.active = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.active = False

    def detect_once(self):
        """Un escaneo puntual. Devuelve (x, y, area) o None."""
        with mss.mss() as sct:
            mon = sct.monitors[1]
            img = np.asarray(sct.grab(mon))          # BGRA
        small = cv2.resize(img[:, :, :3], None, fx=0.5, fy=0.5,
                           interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        lo = np.array([max(0, self.hue - self.hue_tol), self.sat_min, self.val_min])
        hi = np.array([min(179, self.hue + self.hue_tol), 255, 255])
        mask = cv2.inRange(hsv, lo, hi)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                                np.ones((3, 3), np.uint8))
        n, labels, stats, centroids = cv2.connectedComponentsWithStats(mask)
        best = None
        for i in range(1, n):
            area = stats[i, cv2.CC_STAT_AREA]
            if area >= self.min_area and (best is None or area > best[2]):
                cx, cy = centroids[i]
                best = (int(cx * 2) + mon["left"],
                        int(cy * 2) + mon["top"], int(area))
        return best

    def _run(self):
        pending = None  # detección del frame anterior, esperando confirmación
        last_click = 0.0
        while self.active:
            time.sleep(self.interval)
            if self.paused or not self.active:
                pending = None
                continue
            if time.perf_counter() - last_click < self.cooldown:
                continue
            try:
                found = self.detect_once()
            except Exception as exc:
                self.log(f"Error de captura: {exc}")
                continue
            if found is None:
                pending = None
                continue
            x, y, area = found
            if pending is None or abs(pending[0] - x) > 40 or abs(pending[1] - y) > 40:
                pending = (x, y)      # 1ª vez: esperar confirmación
                continue
            # Confirmado en 2 frames: clic
            prev = self.mouse.position
            self.mouse.position = (x, y)
            time.sleep(0.05)
            self.mouse.press(Button.left)
            time.sleep(0.03)
            self.mouse.release(Button.left)
            if self.double_click:
                time.sleep(0.08)
                self.mouse.press(Button.left)
                time.sleep(0.03)
                self.mouse.release(Button.left)
            if self.restore_mouse:
                time.sleep(0.05)
                self.mouse.position = prev
            self.clicks_done += 1
            last_click = time.perf_counter()
            pending = None
            self.log(f"Captcha clicado en ({x}, {y}) — área {area * 4} px²  "
                     f"[total: {self.clicks_done}]")


# ---------------------------------------------------------------- GUI

class App:
    def __init__(self, root):
        self.root = root
        root.title("MacroPro — Grabador de macros + Auto-Captcha")
        root.geometry("560x640")
        root.attributes("-topmost", True)

        self.recorder = Recorder()
        self.player = Player(on_finish=self._on_play_finish,
                             on_progress=self._on_play_progress)
        self.watcher = CaptchaWatcher(self.log)
        self.events = []
        self.current_file = None

        self._build_ui()
        self._load_config()
        self._start_hotkeys()
        self.log("Listo. F6 = grabar | F7 = reproducir | F9 = auto-captcha")
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- construcción de la interfaz ----------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # --- sección macro ---
        fm = ttk.LabelFrame(self.root, text=" Macro (grabar y reproducir) ")
        fm.pack(fill="x", **pad)

        row1 = ttk.Frame(fm)
        row1.pack(fill="x", **pad)
        self.btn_rec = ttk.Button(row1, text="● Grabar (F6)",
                                  command=self.toggle_record)
        self.btn_rec.pack(side="left", padx=4)
        self.btn_play = ttk.Button(row1, text="▶ Reproducir (F7)",
                                   command=self.toggle_play)
        self.btn_play.pack(side="left", padx=4)
        ttk.Button(row1, text="Abrir…", command=self.load_macro).pack(
            side="left", padx=4)
        ttk.Button(row1, text="Guardar…", command=self.save_macro).pack(
            side="left", padx=4)

        row2 = ttk.Frame(fm)
        row2.pack(fill="x", **pad)
        ttk.Label(row2, text="Repeticiones (0 = infinito):").pack(side="left")
        self.var_repeats = tk.StringVar(value="1")
        ttk.Entry(row2, textvariable=self.var_repeats, width=6).pack(
            side="left", padx=4)
        ttk.Label(row2, text="Velocidad:").pack(side="left", padx=(12, 0))
        self.var_speed = tk.StringVar(value="1.0")
        ttk.Combobox(row2, textvariable=self.var_speed, width=5,
                     values=["0.5", "1.0", "1.5", "2.0", "4.0"]).pack(
            side="left", padx=4)

        self.lbl_macro = ttk.Label(fm, text="Sin macro cargada")
        self.lbl_macro.pack(anchor="w", **pad)

        # --- sección auto-captcha ---
        fc = ttk.LabelFrame(
            self.root, text=" Auto-Captcha (clic automático en zona verde) ")
        fc.pack(fill="x", **pad)

        rowc1 = ttk.Frame(fc)
        rowc1.pack(fill="x", **pad)
        self.btn_captcha = ttk.Button(rowc1, text="Activar vigilancia (F9)",
                                      command=self.toggle_captcha)
        self.btn_captcha.pack(side="left", padx=4)
        ttk.Button(rowc1, text="Probar detección ahora",
                   command=self.test_detection).pack(side="left", padx=4)

        grid = ttk.Frame(fc)
        grid.pack(fill="x", **pad)

        def spin(row, col, label, var, lo, hi, inc=1):
            ttk.Label(grid, text=label).grid(row=row, column=col * 2,
                                             sticky="e", padx=4, pady=2)
            sb = ttk.Spinbox(grid, textvariable=var, from_=lo, to=hi,
                             increment=inc, width=7)
            sb.grid(row=row, column=col * 2 + 1, sticky="w", padx=4, pady=2)

        self.var_hue = tk.StringVar(value="60")
        self.var_hue_tol = tk.StringVar(value="15")
        self.var_sat = tk.StringVar(value="120")
        self.var_val = tk.StringVar(value="120")
        self.var_area = tk.StringVar(value="400")
        self.var_interval = tk.StringVar(value="0.7")
        self.var_cooldown = tk.StringVar(value="10")
        spin(0, 0, "Tono verde (0-179):", self.var_hue, 0, 179)
        spin(0, 1, "± tolerancia:", self.var_hue_tol, 1, 60)
        spin(1, 0, "Saturación mín.:", self.var_sat, 0, 255)
        spin(1, 1, "Brillo mín.:", self.var_val, 0, 255)
        spin(2, 0, "Área mínima (px²):", self.var_area, 50, 100000, 50)
        spin(2, 1, "Escaneo cada (s):", self.var_interval, 0.2, 10, 0.1)
        spin(3, 0, "Cooldown tras clic (s):", self.var_cooldown, 1, 3600)

        rowc2 = ttk.Frame(fc)
        rowc2.pack(fill="x", **pad)
        self.var_double = tk.BooleanVar(value=False)
        ttk.Checkbutton(rowc2, text="Doble clic",
                        variable=self.var_double).pack(side="left")
        self.var_restore = tk.BooleanVar(value=True)
        ttk.Checkbutton(rowc2, text="Devolver el ratón a su sitio",
                        variable=self.var_restore).pack(side="left", padx=12)

        # --- registro ---
        fl = ttk.LabelFrame(self.root, text=" Registro ")
        fl.pack(fill="both", expand=True, **pad)
        self.txt_log = tk.Text(fl, height=10, state="disabled",
                               font=("Consolas", 9))
        self.txt_log.pack(fill="both", expand=True, padx=4, pady=4)

        self.status = ttk.Label(self.root, relief="sunken", anchor="w",
                                text="Inactivo")
        self.status.pack(fill="x", side="bottom")

    # ---------- helpers ----------
    def log(self, msg):
        def _append():
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end",
                                time.strftime("[%H:%M:%S] ") + msg + "\n")
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")
        self.root.after(0, _append)

    def set_status(self, text):
        self.root.after(0, lambda: self.status.configure(text=text))

    def _apply_watcher_settings(self):
        w = self.watcher
        try:
            w.hue = int(float(self.var_hue.get()))
            w.hue_tol = int(float(self.var_hue_tol.get()))
            w.sat_min = int(float(self.var_sat.get()))
            w.val_min = int(float(self.var_val.get()))
            w.min_area = int(float(self.var_area.get())) // 4  # escala x0.5
            w.interval = max(0.2, float(self.var_interval.get()))
            w.cooldown = float(self.var_cooldown.get())
            w.double_click = self.var_double.get()
            w.restore_mouse = self.var_restore.get()
        except ValueError:
            self.log("Aviso: algún parámetro no es un número válido; "
                     "se mantienen los anteriores.")

    # ---------- macro ----------
    def toggle_record(self):
        if self.player.playing:
            self.log("No se puede grabar mientras se reproduce.")
            return
        if not self.recorder.recording:
            self.watcher.paused = True
            self.recorder.start()
            self.btn_rec.configure(text="■ Parar grabación (F6)")
            self.set_status("GRABANDO…  (F6 para parar)")
            self.log("Grabación iniciada.")
        else:
            self.events = self.recorder.stop()
            self.watcher.paused = False
            self.btn_rec.configure(text="● Grabar (F6)")
            dur = self.events[-1]["t"] if self.events else 0
            self.lbl_macro.configure(
                text=f"Macro grabada: {len(self.events)} eventos, "
                     f"{dur:.1f} s (sin guardar)")
            self.set_status("Inactivo")
            self.log(f"Grabación parada: {len(self.events)} eventos, "
                     f"{dur:.1f} s.")

    def toggle_play(self):
        if self.recorder.recording:
            self.log("Para la grabación antes de reproducir.")
            return
        if self.player.playing:
            self.player.stop()
            return
        if not self.events:
            self.log("No hay macro grabada ni cargada.")
            return
        try:
            repeats = int(self.var_repeats.get())
            speed = float(self.var_speed.get())
        except ValueError:
            self.log("Repeticiones/velocidad no válidas.")
            return
        self.watcher.paused = True
        self.btn_play.configure(text="■ Parar (F7)")
        self.player.play(self.events, speed=speed, repeats=repeats)
        self.log(f"Reproduciendo (x{speed}, "
                 f"{'∞' if repeats == 0 else repeats} veces)…")

    def _on_play_progress(self, loop, total):
        self.set_status(
            f"REPRODUCIENDO  vuelta {loop}/{'∞' if total == 0 else total}  "
            f"(F7 para parar)")

    def _on_play_finish(self):
        self.watcher.paused = False
        self.root.after(0, lambda: self.btn_play.configure(
            text="▶ Reproducir (F7)"))
        self.set_status("Inactivo")
        self.log("Reproducción terminada.")

    def save_macro(self):
        if not self.events:
            self.log("Nada que guardar.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".macro.json", initialdir=APP_DIR,
            filetypes=[("Macro", "*.macro.json"), ("JSON", "*.json")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "events": self.events}, f)
        self.current_file = path
        self.lbl_macro.configure(
            text=f"Macro: {os.path.basename(path)} ({len(self.events)} eventos)")
        self.log(f"Guardada en {path}")

    def load_macro(self):
        path = filedialog.askopenfilename(
            initialdir=APP_DIR,
            filetypes=[("Macro", "*.macro.json"), ("JSON", "*.json")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.events = data["events"]
        except Exception as exc:
            messagebox.showerror("Error", f"No se pudo cargar: {exc}")
            return
        self.current_file = path
        dur = self.events[-1]["t"] if self.events else 0
        self.lbl_macro.configure(
            text=f"Macro: {os.path.basename(path)} "
                 f"({len(self.events)} eventos, {dur:.1f} s)")
        self.log(f"Cargada {os.path.basename(path)}")

    # ---------- captcha ----------
    def toggle_captcha(self):
        if not self.watcher.active:
            self._apply_watcher_settings()
            self.watcher.start()
            self.btn_captcha.configure(text="Desactivar vigilancia (F9)")
            self.log("Auto-Captcha ACTIVADO: vigilando la pantalla…")
        else:
            self.watcher.stop()
            self.btn_captcha.configure(text="Activar vigilancia (F9)")
            self.log("Auto-Captcha desactivado.")

    def test_detection(self):
        self._apply_watcher_settings()
        self.log("Probando detección en 3 segundos — pon la pantalla "
                 "como cuando sale el captcha…")

        def _do():
            time.sleep(3)
            try:
                found = self.watcher.detect_once()
            except Exception as exc:
                self.log(f"Error: {exc}")
                return
            if found:
                x, y, area = found
                self.log(f"DETECTADO: zona verde en ({x}, {y}), "
                         f"área ≈ {area * 4} px². (No se ha clicado.)")
            else:
                self.log("No se detectó ninguna zona verde con los "
                         "parámetros actuales. Ajusta tono/tolerancia/área.")
        threading.Thread(target=_do, daemon=True).start()

    # ---------- hotkeys ----------
    def _start_hotkeys(self):
        def on_press(key):
            if key == HOTKEY_RECORD:
                self.root.after(0, self.toggle_record)
            elif key == HOTKEY_PLAY:
                self.root.after(0, self.toggle_play)
            elif key == HOTKEY_CAPTCHA:
                self.root.after(0, self.toggle_captcha)
        self._hotkey_listener = keyboard.Listener(on_press=on_press)
        self._hotkey_listener.start()

    # ---------- configuración ----------
    def _load_config(self):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            return
        mapping = {
            "hue": self.var_hue, "hue_tol": self.var_hue_tol,
            "sat": self.var_sat, "val": self.var_val,
            "area": self.var_area, "interval": self.var_interval,
            "cooldown": self.var_cooldown, "repeats": self.var_repeats,
            "speed": self.var_speed,
        }
        for k, var in mapping.items():
            if k in cfg:
                var.set(str(cfg[k]))
        self.var_double.set(cfg.get("double", False))
        self.var_restore.set(cfg.get("restore", True))

    def _save_config(self):
        cfg = {
            "hue": self.var_hue.get(), "hue_tol": self.var_hue_tol.get(),
            "sat": self.var_sat.get(), "val": self.var_val.get(),
            "area": self.var_area.get(), "interval": self.var_interval.get(),
            "cooldown": self.var_cooldown.get(),
            "repeats": self.var_repeats.get(), "speed": self.var_speed.get(),
            "double": self.var_double.get(), "restore": self.var_restore.get(),
        }
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    def _on_close(self):
        self._save_config()
        self.player.stop()
        self.watcher.stop()
        if self.recorder.recording:
            self.recorder.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
