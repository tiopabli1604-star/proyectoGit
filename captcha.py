# -*- coding: utf-8 -*-
"""
Golem Captcha — vigila la pantalla y clica el aviso cuando aparece.

Es la mitad de Golem que resuelve el captcha, sola: mira una zona de la pantalla
cada pocos segundos y, cuando aparece ahí el objetivo, lo clica. Nada más.

No graba macros, no reproduce movimiento, no toca el teclado y no mueve la cámara.
Solo lee la pantalla y da un clic. Eso lo hace más simple de usar y también menos
alarmante: no hay nada que inyecte pulsaciones ni que se enganche al teclado.

El motor de detección es exactamente el mismo que el de Golem (se importa de
main.py), así que cualquier arreglo ahí vale para los dos.

Hotkeys globales (las mismas que en Golem y con el mismo significado):
  F2  = marcar la zona de búsqueda: dos esquinas, una pulsación cada una
  F8  = cuentagotas: capturar el color bajo el ratón
  F6  = activar / desactivar la vigilancia
  F12 = PARADA TOTAL
"""

import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

import cv2
import mss

import main as G

APP_NAME = "Golem Captcha"

# Archivos propios, para no pisarse con los de Golem. Se reaprovecha la misma
# lógica de dónde guardarlos (junto al ejecutable, o en LOCALAPPDATA si se está
# ejecutando desde una carpeta temporal que Windows va a borrar).
APP_DIR = G.APP_DIR
CONFIG_PATH = os.path.join(APP_DIR, "captcha_config.json")
LOG_PATH = os.path.join(APP_DIR, "captcha_log.txt")

# El Finder y el Watcher escriben sus imágenes en rutas del módulo de Golem, así
# que se redirigen a las del captcha.
G.DEBUG_IMG = os.path.join(APP_DIR, "captcha_debug.png")
G.SHOT_PATH = os.path.join(APP_DIR, "captcha_clic_%d.png")
G.TEMPLATE_IMG = os.path.join(APP_DIR, "captcha_plantilla.png")

# Las mismas teclas que en Golem y con el mismo significado, para que no haya
# sorpresas si alguna vez tienes los dos abiertos: pulsar F9 aquí no puede
# ponerte a grabar una macro allí.
HOTKEY_ZONE = G.HOTKEY_ZONE          # F2
HOTKEY_PICK = G.HOTKEY_PICK          # F8
HOTKEY_WATCH = G.HOTKEY_WATCH        # F6
HOTKEY_PANIC = G.HOTKEY_PANIC        # F12
HOTKEYS = {HOTKEY_ZONE, HOTKEY_PICK, HOTKEY_WATCH, HOTKEY_PANIC}
T_ZONE, T_PICK = HOTKEY_ZONE.name.upper(), HOTKEY_PICK.name.upper()
T_WATCH, T_PANIC = HOTKEY_WATCH.name.upper(), HOTKEY_PANIC.name.upper()


class App:
    def __init__(self, root):
        self.root = root
        root.title(f"{APP_NAME} — clica el aviso cuando aparece")
        root.geometry("620x620")

        self.finder = G.Finder()
        self.watcher = G.Watcher(self.finder, self.log, self.set_status)
        self._zone_p1 = None
        self._hotkey_listener = None
        self._log_queue = queue.Queue()
        self._status_pend = None
        self._cerrando = False
        self._drain_id = None
        self._logfile_on = True

        self._build_ui()
        self._load_config()
        self._start_hotkeys()
        self._drain_log()
        self.log(f"{APP_NAME} listo. {T_ZONE} marcar zona | {T_PICK} "
                 f"cuentagotas | {T_WATCH} vigilar | {T_PANIC} PARAR")
        if G.DATA_MOTIVO:
            self.log(f"AVISO: {G.DATA_MOTIVO}, así que guardo los ajustes en "
                     f"{APP_DIR}. Copia el programa a una carpeta de verdad "
                     f"para tenerlo todo junto.")
            G.beep(False)
        self.log(f"Para empezar: deja el aviso en pantalla, marca la zona con "
                 f"{T_ZONE} en dos esquinas, y pulsa 'Probar detección'.")
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------------- interfaz
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        f1 = ttk.LabelFrame(self.root, text=" Qué buscar ")
        f1.pack(fill="x", **pad)

        r0 = ttk.Frame(f1)
        r0.pack(fill="x", **pad)
        ttk.Label(r0, text="Buscar:").pack(side="left")
        self.var_mode = tk.StringVar(value="unico")
        ttk.Radiobutton(r0, text="Lo único con color", value="unico",
                        variable=self.var_mode,
                        command=self._on_mode).pack(side="left", padx=4)
        ttk.Radiobutton(r0, text="Un color concreto", value="color",
                        variable=self.var_mode,
                        command=self._on_mode).pack(side="left", padx=4)

        r1 = ttk.Frame(f1)
        r1.pack(fill="x", **pad)
        ttk.Button(r1, text=f"Marcar zona ({T_ZONE})",
                   command=self.mark_zone).pack(side="left", padx=2)
        ttk.Button(r1, text="Toda la pantalla",
                   command=self.reset_zone).pack(side="left", padx=2)
        ttk.Button(r1, text=f"Cuentagotas ({T_PICK})",
                   command=self.pick_color).pack(side="left", padx=2)
        self.lbl_color = ttk.Label(r1, text="sin calibrar")
        self.lbl_color.pack(side="left", padx=8)

        r2 = ttk.Frame(f1)
        r2.pack(fill="x", **pad)
        ttk.Button(r2, text="Probar detección (3 s)",
                   command=self.test_detection).pack(side="left", padx=2)
        ttk.Button(r2, text="Ver imagen de depuración",
                   command=self.open_debug).pack(side="left", padx=2)
        ttk.Button(r2, text="Ver último clic",
                   command=self.open_shot).pack(side="left", padx=2)

        # --- ajustes ---
        f2 = ttk.LabelFrame(self.root, text=" Ajustes ")
        f2.pack(fill="x", **pad)
        grid = ttk.Frame(f2)
        grid.pack(fill="x", **pad)

        def spin(row, col, label, var, lo, hi, inc=1):
            ttk.Label(grid, text=label).grid(row=row, column=col * 2,
                                             sticky="e", padx=4, pady=2)
            ttk.Spinbox(grid, textvariable=var, from_=lo, to=hi,
                        increment=inc, width=8).grid(
                row=row, column=col * 2 + 1, sticky="w", padx=4, pady=2)

        self.var_hue = tk.StringVar(value="48")
        self.var_hue_tol = tk.StringVar(value="12")
        self.var_sat = tk.StringVar(value="40")
        self.var_val = tk.StringVar(value="90")
        self.var_area = tk.StringVar(value="80")
        self.var_area_max = tk.StringVar(value="40000")
        self.var_max_side = tk.StringVar(value="80")
        self.var_frames = tk.StringVar(value="3")
        self.var_interval = tk.StringVar(value="1.2")
        self.var_cooldown = tk.StringVar(value="10")
        self.var_roi_top = tk.StringVar(value="0")
        self.var_roi_bottom = tk.StringVar(value="100")
        self.var_roi_left = tk.StringVar(value="0")
        self.var_roi_right = tk.StringVar(value="100")

        spin(0, 0, "Saturación mín.:", self.var_sat, 0, 255)
        spin(0, 1, "Brillo mín.:", self.var_val, 0, 255)
        spin(1, 0, "Área mín. (px²):", self.var_area, 10, 100000, 10)
        spin(1, 1, "Área máx. (px²):", self.var_area_max, 100, 5000000, 1000)
        spin(2, 0, "Lado máx. (px):", self.var_max_side, 10, 2000, 10)
        spin(2, 1, "Fotogramas unidos:", self.var_frames, 1, 6)
        spin(3, 0, "Escaneo cada (s):", self.var_interval, 0.2, 60, 0.1)
        spin(3, 1, "Espera tras clicar (s):", self.var_cooldown, 1, 3600)
        spin(4, 0, "Zona: alto de % a %:", self.var_roi_top, 0, 99)
        spin(4, 1, "", self.var_roi_bottom, 1, 100)
        spin(5, 0, "Zona: ancho de % a %:", self.var_roi_left, 0, 99)
        spin(5, 1, "", self.var_roi_right, 1, 100)
        self.fila_tono = 6
        spin(6, 0, "Tono (0-179):", self.var_hue, 0, 179)
        spin(6, 1, "± tolerancia:", self.var_hue_tol, 1, 60)

        r3 = ttk.Frame(f2)
        r3.pack(fill="x", **pad)
        self.var_double = tk.BooleanVar(value=False)
        ttk.Checkbutton(r3, text="Doble clic",
                        variable=self.var_double).pack(side="left")
        self.var_restore = tk.BooleanVar(value=True)
        ttk.Checkbutton(r3, text="Devolver el ratón",
                        variable=self.var_restore).pack(side="left", padx=8)
        self.var_sound = tk.BooleanVar(value=True)
        ttk.Checkbutton(r3, text="Pitido al clicar",
                        variable=self.var_sound).pack(side="left", padx=8)

        r4 = ttk.Frame(f2)
        r4.pack(fill="x", **pad)
        self.var_on_gui = tk.BooleanVar(value=True)
        ttk.Checkbutton(r4, text="Solo sobre una interfaz (fondo gris)",
                        variable=self.var_on_gui,
                        command=self._apply).pack(side="left")
        self.var_shots = tk.BooleanVar(value=True)
        ttk.Checkbutton(r4, text="Guardar captura de cada clic",
                        variable=self.var_shots,
                        command=self._apply).pack(side="left", padx=8)

        r5 = ttk.Frame(f2)
        r5.pack(fill="x", **pad)
        ttk.Label(r5, text="Actuar solo si la ventana de delante contiene:").pack(
            side="left")
        self.var_ventana = tk.StringVar(value="")
        ttk.Entry(r5, textvariable=self.var_ventana, width=14).pack(side="left",
                                                                   padx=4)
        ttk.Button(r5, text="Usar la de ahora",
                   command=self.usar_ventana_actual).pack(side="left", padx=2)

        # --- marcha ---
        f3 = ttk.Frame(self.root)
        f3.pack(fill="x", **pad)
        self.btn_watch = ttk.Button(f3, text=f"Activar vigilancia ({T_WATCH})",
                                    command=self.toggle_watch)
        self.btn_watch.pack(side="left", padx=4)
        ttk.Button(f3, text="Comprobar todo",
                   command=self.diagnostico).pack(side="left", padx=4)
        self.var_logfile = tk.BooleanVar(value=True)
        ttk.Checkbutton(f3, text="Guardar registro",
                        variable=self.var_logfile,
                        command=self._apply_logfile).pack(side="left", padx=8)
        ttk.Button(f3, text=f"■ PARADA TOTAL ({T_PANIC})",
                   command=self.panic).pack(side="right", padx=4)

        fl = ttk.LabelFrame(self.root, text=" Registro ")
        fl.pack(fill="both", expand=True, **pad)
        self.txt_log = tk.Text(fl, height=12, state="disabled",
                               font=("Consolas", 9))
        self.txt_log.pack(fill="both", expand=True, padx=4, pady=4)

        self.status = ttk.Label(self.root, relief="sunken", anchor="w",
                                text="Inactivo")
        self.status.pack(fill="x", side="bottom")
        self._on_mode(callado=True)

    # ---------------------------------------------------------------- registro
    def log(self, msg):
        linea = time.strftime("[%H:%M:%S] ") + msg
        self._log_queue.put(linea)
        if self._logfile_on:
            try:
                with open(LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d ") + linea + "\n")
            except Exception:
                pass

    def _drain_log(self, reprogramar=True):
        lineas = []
        try:
            while True:
                lineas.append(self._log_queue.get_nowait())
        except queue.Empty:
            pass
        if lineas:
            try:
                self.txt_log.configure(state="normal")
                self.txt_log.insert("end", "\n".join(lineas) + "\n")
                self.txt_log.see("end")
                self.txt_log.configure(state="disabled")
            except Exception:
                pass
        if self._status_pend is not None:
            try:
                self.status.configure(text=self._status_pend)
            except Exception:
                pass
            self._status_pend = None
        if reprogramar and not self._cerrando:
            try:
                self._drain_id = self.root.after(120, self._drain_log)
            except Exception:
                pass

    def set_status(self, text):
        self._status_pend = text

    def _apply_logfile(self):
        self._logfile_on = bool(self.var_logfile.get())

    # ---------------------------------------------------------------- ajustes
    def _apply(self):
        f, w = self.finder, self.watcher
        try:
            f.mode = self.var_mode.get()
            f.hue = int(float(self.var_hue.get()))
            f.hue_tol = int(float(self.var_hue_tol.get()))
            f.sat_min = int(float(self.var_sat.get()))
            f.val_min = int(float(self.var_val.get()))
            f.min_area = int(float(self.var_area.get()))
            f.max_area = int(float(self.var_area_max.get()))
            f.max_side = int(float(self.var_max_side.get()))
            f.frames = max(1, int(float(self.var_frames.get())))
            f.on_gui = self.var_on_gui.get()
            f.roi_top = float(self.var_roi_top.get()) / 100.0
            f.roi_bottom = float(self.var_roi_bottom.get()) / 100.0
            f.roi_left = float(self.var_roi_left.get()) / 100.0
            f.roi_right = float(self.var_roi_right.get()) / 100.0
            w.interval = max(0.2, float(self.var_interval.get()))
            w.cooldown = float(self.var_cooldown.get())
            w.double_click = self.var_double.get()
            w.restore_mouse = self.var_restore.get()
            w.sound = self.var_sound.get()
            w.shots = self.var_shots.get()
            w.ventana_req = self.var_ventana.get().strip()
        except ValueError:
            self.log("Aviso: algún ajuste no es un número válido; se mantienen "
                     "los anteriores.")

    def _on_mode(self, callado=False):
        self._apply()
        # el tono solo pinta algo en el modo de un color concreto
        estado = "normal" if self.finder.mode == "color" else "disabled"
        for hijo in self.root.winfo_children():
            pass
        if not callado:
            if self.finder.mode == "unico":
                self.log(f"Modo 'lo único con color': no hace falta calibrar "
                         f"nada. Marca la zona con {T_ZONE} y dentro clicará lo "
                         f"único que tenga color.")
            else:
                self.log(f"Modo 'un color concreto': usa el cuentagotas "
                         f"({T_PICK}) sobre el objetivo.")

    # ---------------------------------------------------------------- zona
    def mark_zone(self):
        x, y = G.MouseController().position
        with mss.mss() as sct:
            mon = sct.monitors[1]
        if self._zone_p1 is None:
            self._zone_p1 = (x, y)
            self.log(f"Zona: esquina 1 en ({x}, {y}). Lleva el ratón a la "
                     f"esquina opuesta y pulsa {T_ZONE} otra vez.")
            G.beep(True)
            return
        x1, y1 = self._zone_p1
        self._zone_p1 = None
        left, right = sorted((x1 - mon["left"], x - mon["left"]))
        top, bottom = sorted((y1 - mon["top"], y - mon["top"]))
        if right - left < 20 or bottom - top < 20:
            self.log(f"Zona demasiado pequeña; inténtalo otra vez con {T_ZONE}.")
            G.beep(False)
            return
        self.var_roi_left.set(str(math.floor(left * 100 / mon["width"])))
        self.var_roi_right.set(str(max(1, math.ceil(right * 100 / mon["width"]))))
        self.var_roi_top.set(str(math.floor(top * 100 / mon["height"])))
        self.var_roi_bottom.set(
            str(max(1, math.ceil(bottom * 100 / mon["height"]))))
        self._apply()
        f = self.finder
        self.log(f"Zona fijada: marcaste {right - left}x{bottom - top} px desde "
                 f"({left}, {top}); se guarda como "
                 f"{int((f.roi_right - f.roi_left) * mon['width'])}x"
                 f"{int((f.roi_bottom - f.roi_top) * mon['height'])} px. "
                 f"Fuera de ahí no mirará nada.")
        G.beep(True)

    def reset_zone(self):
        self._zone_p1 = None
        for var, val in ((self.var_roi_left, "0"), (self.var_roi_right, "100"),
                         (self.var_roi_top, "0"), (self.var_roi_bottom, "100")):
            var.set(val)
        self._apply()
        self.log("Zona: toda la pantalla.")

    def pick_color(self):
        try:
            h, s, v, b, g, r, inestable = self.finder.pick_color_at_cursor()
        except Exception as exc:
            self.log(f"Error leyendo el color: {exc}")
            return
        self.var_mode.set("color")
        self.var_hue.set(str(h))
        self.var_hue_tol.set("12")
        self.var_sat.set(str(max(25, s - 60)))
        self.var_val.set(str(max(50, v - 60)))
        self._apply()
        self.lbl_color.configure(text=f"RGB({r},{g},{b})  H={h} S={s} V={v}")
        self.log(f"Color capturado: RGB({r},{g},{b}) → tono {h}, saturación {s}, "
                 f"brillo {v}.")
        if inestable:
            self.log("Ojo: el color parpadeaba. Es el brillo de un objeto "
                     "encantado; deja 'Fotogramas unidos' en 3 o más.")
        G.beep(True)

    def usar_ventana_actual(self):
        t = G.ventana_activa()
        if not t or "Captcha" in t or "Golem" in t:
            self.log("Pon delante la ventana del juego y vuelve a pulsarlo.")
            return
        self.var_ventana.set(t.split()[0][:24])
        self._apply()
        self.log(f"Solo actuaré cuando delante haya una ventana cuyo título "
                 f"contenga «{self.var_ventana.get()}» (ahora es «{t}»).")

    # ---------------------------------------------------------------- probar
    def test_detection(self):
        self._apply()
        nombres = {"unico": "lo único con color", "color": "un color concreto"}
        self.log(f"Probando en 3 segundos (modo: "
                 f"{nombres.get(self.finder.mode, self.finder.mode)}) — deja la "
                 f"pantalla como cuando sale el aviso…")

        def _do():
            time.sleep(3)
            try:
                found = self.finder.candidates(save_debug=True)
            except Exception as exc:
                self.log(f"Error: {exc}")
                return
            if not found:
                self.log("NO detectado. Esto es lo que veía dentro de la zona:")
                try:
                    for l in self.finder.zone_report():
                        self.log("   " + l)
                except Exception as exc:
                    self.log(f"   (no pude inspeccionar la zona: {exc})")
            else:
                self.log(f"{len(found)} candidato(s). El nº 1 es el que se "
                         f"clicaría:")
                for i, (x, y, a, s) in enumerate(found[:5], 1):
                    self.log(f"   {i}. ({x}, {y})  área {a} px²")
                if len(found) > 1:
                    self.log(f"Hay más de uno: aprieta la zona con {T_ZONE} "
                             f"hasta que solo quede el bueno.")
            for r in self.finder.rejects[:8]:
                self.log("   · " + r)
            self.log(f"Imagen de depuración: {G.DEBUG_IMG}")
        threading.Thread(target=_do, daemon=True).start()

    def diagnostico(self):
        self._apply()
        req = self.var_ventana.get().strip()

        def _do():
            L = self.log
            L("=" * 46)
            L("COMPROBACIÓN")
            L(f"· Archivos en: {APP_DIR}")
            if G.DATA_MOTIVO:
                L(f"    OJO: {G.DATA_MOTIVO}.")
            try:
                with mss.mss() as sct:
                    mon = sct.monitors[1]
                L(f"· Pantalla: {mon['width']}x{mon['height']} px")
            except Exception as exc:
                L(f"· Pantalla: no pude consultarla ({exc})")
            t = G.ventana_activa()
            L(f"· Ventana de delante: «{t}»")
            if not req:
                L("    sin exigencia: actuará esté quien esté delante.")
            elif req.lower() in t.lower():
                L(f"    bien: contiene «{req}».")
            else:
                L(f"    ahora NO actuaría: se exige «{req}».")
            f = self.finder
            sin_zona = (f.roi_left, f.roi_right, f.roi_top,
                        f.roi_bottom) == (0.0, 1.0, 0.0, 1.0)
            L("· Zona: " + (f"TODA la pantalla — márcala con {T_ZONE}" if sin_zona
                            else "marcada"))
            try:
                t0 = time.perf_counter()
                n = len(f.candidates())
                ms = (time.perf_counter() - t0) * 1000
                L(f"· Un escaneo tarda {ms:.0f} ms y ahora ve {n} candidato(s)")
            except Exception as exc:
                L(f"· Escaneo: falló ({exc})")
            L("=" * 46)
            G.beep(True)
        threading.Thread(target=_do, daemon=True).start()

    def open_debug(self):
        self._abrir(G.DEBUG_IMG, "Aún no hay imagen: usa 'Probar detección'.")

    def open_shot(self):
        shots = [G.SHOT_PATH % i for i in (1, 2, 3)]
        shots = [p for p in shots if os.path.exists(p)]
        if not shots:
            self.log("Todavía no ha clicado nada.")
            return
        self._abrir(max(shots, key=os.path.getmtime), "")

    def _abrir(self, ruta, si_no_hay):
        if not os.path.exists(ruta):
            self.log(si_no_hay)
            return
        try:
            os.startfile(ruta)
        except Exception as exc:
            self.log(f"No se pudo abrir: {exc}")

    # ---------------------------------------------------------------- marcha
    def toggle_watch(self):
        if not self.watcher.active:
            self._apply()
            f = self.finder
            sin_zona = (f.roi_left, f.roi_right, f.roi_top,
                        f.roi_bottom) == (0.0, 1.0, 0.0, 1.0)
            if f.mode == "unico" and sin_zona and not f.on_gui:
                self.log("No activo la vigilancia: en modo 'lo único con color' "
                         "sin zona marcada mira toda la pantalla y clicaría "
                         f"cualquier cosa. Marca la zona con {T_ZONE}.")
                G.beep(False)
                return
            self.watcher.start()
            self.btn_watch.configure(text=f"Desactivar vigilancia ({T_WATCH})")
            self.log("Vigilancia ACTIVADA. Puedes minimizar esta ventana.")
        else:
            self.watcher.stop()
            self.btn_watch.configure(text=f"Activar vigilancia ({T_WATCH})")
            self.set_status("Inactivo")
            self.log("Vigilancia desactivada.")

    def panic(self):
        if self.watcher.active:
            self.watcher.stop()
            self.btn_watch.configure(text=f"Activar vigilancia ({T_WATCH})")
            self.log("PARADA TOTAL: vigilancia parada.")
        else:
            self.log("PARADA TOTAL: no había nada activo.")
        self.set_status("PARADA TOTAL")
        G.beep(False)

    # ---------------------------------------------------------------- hotkeys
    def _start_hotkeys(self):
        self._acciones = {
            HOTKEY_ZONE: self.mark_zone,
            HOTKEY_PICK: self.pick_color,
            HOTKEY_WATCH: self.toggle_watch,
            HOTKEY_PANIC: self.panic,
        }

        def on_press(key):
            accion = self._acciones.get(key)
            if accion:
                self.root.after(0, accion)
        self._hotkey_listener = G.keyboard.Listener(on_press=on_press)
        self._hotkey_listener.start()

    # ---------------------------------------------------------------- config
    def _cfg_map(self):
        return {
            "mode": self.var_mode, "hue": self.var_hue,
            "hue_tol": self.var_hue_tol, "sat": self.var_sat,
            "val": self.var_val, "area": self.var_area,
            "area_max": self.var_area_max, "max_side": self.var_max_side,
            "frames": self.var_frames, "interval": self.var_interval,
            "cooldown": self.var_cooldown, "roi_top": self.var_roi_top,
            "roi_bottom": self.var_roi_bottom, "roi_left": self.var_roi_left,
            "roi_right": self.var_roi_right, "ventana": self.var_ventana,
        }

    def _load_config(self):
        if not os.path.exists(CONFIG_PATH):
            self._apply()
            return
        try:
            with open(CONFIG_PATH, encoding="utf-8-sig") as f:
                cfg = G.json.load(f)
        except Exception as exc:
            self.log(f"No pude leer {os.path.basename(CONFIG_PATH)} ({exc}); "
                     f"sigo con los ajustes de fábrica.")
            self._apply()
            return
        for k, var in self._cfg_map().items():
            if k in cfg:
                var.set(str(cfg[k]))
        self.var_double.set(cfg.get("double", False))
        self.var_restore.set(cfg.get("restore", True))
        self.var_sound.set(cfg.get("sound", True))
        self.var_on_gui.set(cfg.get("on_gui", True))
        self.var_shots.set(cfg.get("shots", True))
        self.var_logfile.set(cfg.get("logfile", True))
        self._apply_logfile()
        self._apply()

    def _save_config(self):
        cfg = {k: var.get() for k, var in self._cfg_map().items()}
        cfg["double"] = self.var_double.get()
        cfg["restore"] = self.var_restore.get()
        cfg["sound"] = self.var_sound.get()
        cfg["on_gui"] = self.var_on_gui.get()
        cfg["shots"] = self.var_shots.get()
        cfg["logfile"] = self.var_logfile.get()
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                G.json.dump(cfg, f, indent=2)
        except Exception:
            pass

    def _on_close(self):
        self._cerrando = True
        if self._drain_id is not None:
            try:
                self.root.after_cancel(self._drain_id)
            except Exception:
                pass
            self._drain_id = None
        self._save_config()
        self.watcher.stop()
        try:
            if self._hotkey_listener:
                self._hotkey_listener.stop()
        except Exception:
            pass
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
