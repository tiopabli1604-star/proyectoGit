# -*- coding: utf-8 -*-
"""
Golem — automatiza clics y macros en Windows.

Un gólem al que le enseñas una tarea y la repite por ti:

  · Grabador de macros: captura todos los eventos de ratón (movimiento, clic,
    arrastre, rueda) y teclado con sus tiempos exactos, y los reproduce igual.
  · Vigilante de pantalla: encuentra el objetivo y hace clic en su centro
    automáticamente, esté donde esté. Por defecto busca "lo único con color"
    dentro de la zona que marques, que no necesita calibración alguna.

Hotkeys globales:
  F6  = grabar / parar grabación
  F7  = reproducir / parar reproducción
  F2  = marcar la zona de búsqueda: dos esquinas, una pulsación cada una
  F8  = cuentagotas: capturar el color bajo el ratón y calibrarse solo
  F4  = capturar plantilla: recorta la imagen bajo el ratón como referencia
  F9  = activar / desactivar el vigilante
  F12 = PARADA TOTAL de emergencia
"""

import ctypes
import json
import math
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

try:
    import winsound
except ImportError:
    winsound = None

import numpy as np
import cv2
import mss
from pynput import mouse, keyboard
from pynput.mouse import Button, Controller as MouseController
from pynput.keyboard import Key, KeyCode, Controller as KeyboardController

APP_NAME = "Golem"
APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
CONFIG_PATH = os.path.join(APP_DIR, "golem_config.json")
DEBUG_IMG = os.path.join(APP_DIR, "golem_debug.png")
SHOT_PATH = os.path.join(APP_DIR, "golem_clic_%d.png")
TEMPLATE_IMG = os.path.join(APP_DIR, "golem_plantilla.png")
LOG_PATH = os.path.join(APP_DIR, "golem_log.txt")

HOTKEY_RECORD = keyboard.Key.f6
HOTKEY_PLAY = keyboard.Key.f7
HOTKEY_PICK = keyboard.Key.f8
HOTKEY_TEMPLATE = keyboard.Key.f4
HOTKEY_WATCH = keyboard.Key.f9
HOTKEY_ZONE = keyboard.Key.f2
HOTKEY_PANIC = keyboard.Key.f12
HOTKEYS = {HOTKEY_RECORD, HOTKEY_PLAY, HOTKEY_PICK, HOTKEY_TEMPLATE,
           HOTKEY_WATCH, HOTKEY_ZONE, HOTKEY_PANIC}

SCALE = 0.5  # las capturas de color se analizan a media resolución


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


def beep(ok=True):
    if winsound is None:
        return
    try:
        winsound.Beep(880 if ok else 300, 90)
    except Exception:
        pass


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


# ---------------------------------------------------------------- detector

class Finder:
    """Localiza el objetivo en pantalla.

    Modo 'unico': lo único que tenga color. No mira el tono, solo si el píxel
    está saturado, así que dentro de un cofre — donde las casillas vacías son
    gris puro — el objeto es lo único que puede salir. No hay nada que
    calibrar, y el brillo morado de un encantamiento deja de estorbar porque
    también es color y se suma a la mancha en vez de romperla.
    Modo 'color': máscara HSV de un tono concreto. Útil cuando dentro de la
    zona hay varias cosas con color y solo interesa una.
    Modo 'plantilla': cv2.matchTemplate contra un recorte capturado por el
    usuario. Mucho más selectivo, pero exige que el objetivo se vea igual
    (mismo tamaño y resolución) — inservible con objetos encantados.
    """

    def __init__(self):
        self.mode = "unico"
        # --- color ---
        self.hue = 48        # verde lima; el cuentagotas (F8) lo afina
        self.hue_tol = 12
        self.sat_min = 40
        self.val_min = 90
        self.min_area = 80      # px² reales
        self.max_area = 40000   # px² reales; descarta paredes/fondos enormes
        self.frames = 3         # fotogramas que se unen en cada escaneo
        self.frame_gap = 0.10   # s entre esos fotogramas
        self.max_side = 80      # lado máx. del objetivo en px reales
        self.on_gui = True      # exigir fondo gris de interfaz alrededor
        # --- plantilla ---
        self.template = None
        self.tpl_size = 40      # lado del recorte que captura F4
        self.tpl_thr = 0.85     # correlación mínima para aceptar
        self.last_score = 0.0
        # --- común ---
        # zona de búsqueda, en fracción de pantalla
        self.roi_top = 0.0
        self.roi_bottom = 1.0
        self.roi_left = 0.0
        self.roi_right = 1.0
        self.rejects = []       # motivos de descarte del último escaneo
        self.load_template()

    # ---- captura de pantalla ----
    @staticmethod
    def grab_screen():
        with mss.mss() as sct:
            mon = sct.monitors[1]
            img = np.asarray(sct.grab(mon))
        return img[:, :, :3], mon  # BGR, monitor

    def _band(self, bgr):
        """Recorta el rectángulo de búsqueda. Devuelve (recorte, x_ini, y_ini)."""
        h, w = bgr.shape[:2]
        y_ini = int(h * max(0.0, min(1.0, self.roi_top)))
        y_fin = int(h * max(0.0, min(1.0, self.roi_bottom)))
        x_ini = int(w * max(0.0, min(1.0, self.roi_left)))
        x_fin = int(w * max(0.0, min(1.0, self.roi_right)))
        if y_fin - y_ini < 10:
            y_ini, y_fin = 0, h
        if x_fin - x_ini < 10:
            x_ini, x_fin = 0, w
        return bgr[y_ini:y_fin, x_ini:x_fin], x_ini, y_ini

    # ---- modo color ----
    def build_mask(self, bgr_small):
        hsv = cv2.cvtColor(bgr_small, cv2.COLOR_BGR2HSV)
        if self.mode == "unico":
            # cualquier píxel con color, sin mirar el tono: el gris de las
            # casillas vacías tiene saturación casi 0 y se queda fuera solo
            mask = cv2.inRange(hsv,
                               np.array([0, self.sat_min, self.val_min]),
                               np.array([179, 255, 255]))
            return cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                                    np.ones((2, 2), np.uint8))
        lo_h = self.hue - self.hue_tol
        hi_h = self.hue + self.hue_tol
        if lo_h < 0 or hi_h > 179:
            # el rango cruza el extremo del círculo de tono: dos tramos
            a = cv2.inRange(hsv,
                            np.array([0, self.sat_min, self.val_min]),
                            np.array([max(0, hi_h % 180), 255, 255]))
            b = cv2.inRange(hsv,
                            np.array([lo_h % 180, self.sat_min, self.val_min]),
                            np.array([179, 255, 255]))
            mask = cv2.bitwise_or(a, b)
        else:
            mask = cv2.inRange(hsv,
                               np.array([lo_h, self.sat_min, self.val_min]),
                               np.array([hi_h, 255, 255]))
        return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))

    def _on_gui_panel(self, hsv, x, y, w, h):
        """¿El objetivo está sobre el gris claro de una interfaz?

        Mira un anillo alrededor del objeto y exige que sea gris (poca
        saturación) y claro (brillo alto), como el panel de un cofre:

          · sobre el césped, las hojas o el agua el fondo es un color vivo,
            así que la saturación lo delata;
          · el texto de colores del HUD (nombre del bioma, marcadores) está
            sobre fondos oscuros y poco saturados, así que ahí lo que lo
            delata es el brillo.
        """
        alto, ancho = hsv.shape[:2]
        m = max(4, int(min(w, h) * 0.9))
        x0, x1 = max(0, x - m), min(ancho, x + w + m)
        y0, y1 = max(0, y - m), min(alto, y + h + m)
        zona = hsv[y0:y1, x0:x1]
        if zona.size == 0:
            return True
        anillo = np.ones(zona.shape[:2], bool)
        anillo[y - y0:y + h - y0, x - x0:x + w - x0] = False  # fuera el objeto
        if not anillo.any():
            return True
        sat = float(np.median(zona[:, :, 1][anillo]))
        val = float(np.median(zona[:, :, 2][anillo]))
        return sat < 60 and val > 110

    def _color_candidates(self, save_debug=False):
        """Máscara de color sobre la unión de varios fotogramas.

        Un objeto encantado lleva un brillo que barre el sprite y tapa una
        parte distinta en cada instante, así que en un solo fotograma el color
        aparece roto en trozos. Uniendo 3 capturas seguidas cada píxel cuenta
        si era del color buscado en *alguna* de ellas, y un cierre morfológico
        vuelve a pegar los trozos en una sola mancha.
        """
        mask = None
        small = mon = x_ini = y_ini = None
        for i in range(max(1, int(self.frames))):
            if i:
                time.sleep(max(0.0, self.frame_gap))
            bgr, mon = self.grab_screen()
            band, x_ini, y_ini = self._band(bgr)
            small = cv2.resize(band, None, fx=SCALE, fy=SCALE,
                               interpolation=cv2.INTER_AREA)
            m = self.build_mask(small)
            mask = m if mask is None else cv2.bitwise_or(mask, m)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                                np.ones((5, 5), np.uint8))
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        n, labels, stats, centroids = cv2.connectedComponentsWithStats(mask)

        px_factor = 1.0 / (SCALE * SCALE)  # área reducida -> área real
        found, rechazos, marcas = [], [], []
        for i in range(1, n):
            x, y, w, hh = (stats[i, cv2.CC_STAT_LEFT],
                           stats[i, cv2.CC_STAT_TOP],
                           stats[i, cv2.CC_STAT_WIDTH],
                           stats[i, cv2.CC_STAT_HEIGHT])
            area_real = int(stats[i, cv2.CC_STAT_AREA] * px_factor)
            lado_real = int(max(w, hh) / SCALE)
            cx, cy = centroids[i]
            px = int(cx / SCALE) + x_ini + mon["left"]
            py = int(cy / SCALE) + y_ini + mon["top"]

            if area_real < self.min_area:
                motivo = f"área {area_real} px² < mínimo {self.min_area}"
            elif area_real > self.max_area:
                motivo = f"área {area_real} px² > máximo {self.max_area}"
            elif lado_real > self.max_side:
                motivo = (f"mide {lado_real} px de lado > máximo "
                          f"{self.max_side}: demasiado grande para una casilla")
            elif self.on_gui and not self._on_gui_panel(hsv, x, y, w, hh):
                motivo = "no está sobre una interfaz gris (parece paisaje)"
            else:
                motivo = None

            if motivo is None:
                found.append((px, py, area_real, 0.0))
            elif area_real >= 20:   # no llenar el registro de motas de color
                rechazos.append(f"({px}, {py}) descartado: {motivo}")
            marcas.append((x, y, w, hh, area_real, motivo is None))

        found.sort(key=lambda c: -c[2])
        self.rejects = rechazos

        if save_debug:
            vis = small.copy()
            vis[mask > 0] = (0, 0, 255)
            for x, y, w, hh, area_real, ok in marcas:
                col = (0, 255, 0) if ok else (0, 255, 255)
                cv2.rectangle(vis, (x - 2, y - 2), (x + w + 2, y + hh + 2),
                              col, 1)
                cv2.putText(vis, str(area_real), (x, max(8, y - 4)),
                            cv2.FONT_HERSHEY_PLAIN, 0.7, col, 1)
            self._save_debug(vis)
        return found

    # ---- modo plantilla ----
    def load_template(self):
        if os.path.exists(TEMPLATE_IMG):
            try:
                img = cv2.imread(TEMPLATE_IMG, cv2.IMREAD_COLOR)
                if img is not None and img.size:
                    self.template = img
            except Exception:
                self.template = None

    def capture_template(self):
        """Recorta un cuadro alrededor del ratón y lo guarda como referencia."""
        x, y = MouseController().position
        half = max(6, int(self.tpl_size) // 2)
        with mss.mss() as sct:
            mon = sct.monitors[1]
            left = max(mon["left"],
                       min(int(x) - half, mon["left"] + mon["width"] - 2 * half))
            top = max(mon["top"],
                      min(int(y) - half, mon["top"] + mon["height"] - 2 * half))
            img = np.asarray(sct.grab({"left": int(left), "top": int(top),
                                       "width": 2 * half, "height": 2 * half}))
        self.template = np.ascontiguousarray(img[:, :, :3])
        cv2.imwrite(TEMPLATE_IMG, self.template)
        return self.template.shape[1], self.template.shape[0]

    def _template_candidates(self, bgr, mon, x_ini, y_ini, save_debug):
        tpl = self.template
        if tpl is None:
            raise RuntimeError("no hay plantilla capturada (usa F4)")
        th, tw = tpl.shape[:2]
        if bgr.shape[0] < th or bgr.shape[1] < tw:
            raise RuntimeError("la plantilla es mayor que la zona de búsqueda")
        res = cv2.matchTemplate(bgr, tpl, cv2.TM_CCOEFF_NORMED)
        _, best, _, loc = cv2.minMaxLoc(res)
        found = []
        if best >= self.tpl_thr:
            found.append((loc[0] + tw // 2 + x_ini + mon["left"],
                          loc[1] + th // 2 + y_ini + mon["top"],
                          tw * th, float(best)))
        if save_debug:
            vis = cv2.resize(bgr, None, fx=SCALE, fy=SCALE,
                             interpolation=cv2.INTER_AREA)
            col = (0, 255, 0) if best >= self.tpl_thr else (0, 255, 255)
            p1 = (int(loc[0] * SCALE), int(loc[1] * SCALE))
            p2 = (int((loc[0] + tw) * SCALE), int((loc[1] + th) * SCALE))
            cv2.rectangle(vis, p1, p2, col, 2)
            cv2.putText(vis, f"{best:.3f}", (p1[0], max(10, p1[1] - 4)),
                        cv2.FONT_HERSHEY_PLAIN, 1.0, col, 1)
            self._save_debug(vis)
        self.last_score = float(best)
        return found

    @staticmethod
    def _save_debug(vis):
        try:
            cv2.imwrite(DEBUG_IMG, vis)
        except Exception:
            pass

    # ---- entrada única ----
    def candidates(self, save_debug=False):
        """[(x, y, area, score), ...] ordenado de mejor a peor."""
        self.rejects = []
        if self.mode == "plantilla":
            bgr, mon = self.grab_screen()
            band, x_ini, y_ini = self._band(bgr)
            return self._template_candidates(band, mon, x_ini, y_ini,
                                             save_debug)
        return self._color_candidates(save_debug)

    def zone_report(self):
        """Qué hay dentro de la zona de búsqueda, en HSV.

        Cuando no detecta nada, esto dice si el problema es la zona (el fondo
        sale coloreado => está sobre el paisaje) o el umbral (el fondo es gris
        y el objetivo está ahí, pero no pasa el filtro).
        """
        bgr, mon = self.grab_screen()
        band, x_ini, y_ini = self._band(bgr)
        hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
        h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        alto, ancho = band.shape[:2]
        out = [f"zona mirada: {ancho}x{alto} px desde "
               f"({x_ini + mon['left']}, {y_ini + mon['top']})",
               f"fondo de la zona: tono {int(np.median(h))}, "
               f"saturación {int(np.median(s))}, brillo {int(np.median(v))}"]
        if int(np.median(s)) > 60:
            out.append("   el fondo tiene bastante color: la zona no parece "
                       "estar sobre el gris de una interfaz")
        umbral = max(40, int(np.percentile(s, 99.5)))
        sel = (s >= umbral) & (v >= 40)
        n = int(sel.sum())
        if n >= 10:
            out.append(f"lo más coloreado que hay dentro ({n} px con "
                       f"saturación >= {umbral}): tono {int(np.median(h[sel]))}, "
                       f"saturación {int(np.median(s[sel]))}, "
                       f"brillo {int(np.median(v[sel]))}")
            out.append(f"   con los ajustes de ahora exiges saturación >= "
                       f"{self.sat_min} y brillo >= {self.val_min}"
                       + (f", y tono {self.hue} ± {self.hue_tol}"
                          if self.mode == "color" else ""))
        else:
            out.append("dentro de la zona no hay practicamente nada con "
                       "color: ¿está bien marcada, y estaba el aviso en "
                       "pantalla al probar?")
        return out

    def pick_color_at_cursor(self, muestras=5, gap=0.06):
        """Lee el color bajo el ratón. Devuelve (h, s, v, b, g, r, inestable).

        Toma varias muestras de 5x5 separadas en el tiempo y se queda con la
        mediana de todas. Con un objeto encantado el brillo morado pasa por
        encima del sprite, así que una sola lectura puede pillar el brillo en
        vez del color de debajo; la mediana temporal lo ignora mientras el
        brillo no tape el punto más de la mitad del tiempo.
        """
        x, y = MouseController().position
        parches = []
        with mss.mss() as sct:
            mon = sct.monitors[1]
            left = max(mon["left"], min(x - 2, mon["left"] + mon["width"] - 5))
            top = max(mon["top"], min(y - 2, mon["top"] + mon["height"] - 5))
            caja = {"left": int(left), "top": int(top), "width": 5, "height": 5}
            for i in range(max(1, muestras)):
                if i:
                    time.sleep(gap)
                parches.append(np.asarray(sct.grab(caja))[:, :, :3])
        pila = np.concatenate([p.reshape(-1, 3) for p in parches])
        bgr = np.median(pila, axis=0).astype(np.uint8)
        hsv = cv2.cvtColor(bgr.reshape(1, 1, 3), cv2.COLOR_BGR2HSV)[0, 0]

        # ¿varía mucho de una muestra a otra? Entonces hay algo animado encima.
        medias = [p.reshape(-1, 3).mean(axis=0) for p in parches]
        inestable = bool(len(medias) > 1 and
                         np.max(np.ptp(np.array(medias), axis=0)) > 25)
        return (int(hsv[0]), int(hsv[1]), int(hsv[2]),
                int(bgr[0]), int(bgr[1]), int(bgr[2]), inestable)


# ---------------------------------------------------------------- vigilante

class Watcher:
    """Vigila la pantalla y clica el objetivo cuando aparece.

    Exige verlo en 2 escaneos seguidos antes de clicar (anti-falsos positivos)
    y aplica un cooldown tras cada clic.
    """

    def __init__(self, finder, log_fn, status_fn=None):
        self.finder = finder
        self.mouse = MouseController()
        self.log = log_fn
        self.status = status_fn
        self.active = False
        self.paused = False           # se pausa durante grabación/reproducción
        self._thread = None
        self.interval = 0.7           # s entre escaneos
        self.cooldown = 10.0          # s tras un clic
        self.double_click = False
        self.restore_mouse = True
        self.sound = True
        self.shots = True             # guardar captura de cada clic
        self.move_delay = 0.20        # s entre mover el ratón y pulsar
        self.clicks_done = 0
        self.last_click_time = None

    def start(self):
        if self.active:
            return
        self.active = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.active = False

    def _run(self):
        pending = None  # detección del escaneo anterior, esperando confirmación
        last_click = 0.0
        errores = 0
        while self.active:
            time.sleep(self.interval)
            if self.paused or not self.active:
                pending = None
                continue
            espera = self.cooldown - (time.perf_counter() - last_click)
            if espera > 0:
                if self.status:
                    self.status(f"VIGILANDO — en pausa {espera:.0f}s tras el "
                                f"último clic  ({self.clicks_done} clics)")
                continue
            try:
                found = self.finder.candidates()
                errores = 0
            except Exception as exc:
                errores += 1
                if errores <= 3:
                    self.log(f"Error al buscar: {exc}")
                continue
            if self.status:
                self.status(f"VIGILANDO — {self.clicks_done} clics"
                            + (f", último a las {self.last_click_time}"
                               if self.last_click_time else ""))
            if not found:
                pending = None
                continue
            x, y, area, score = found[0]
            if pending is None or abs(pending[0] - x) > 40 or abs(pending[1] - y) > 40:
                pending = (x, y)      # 1ª vez: esperar confirmación
                continue
            shot = self._save_shot(x, y, self.clicks_done + 1) if self.shots else None
            self._click(x, y)
            self.clicks_done += 1
            self.last_click_time = time.strftime("%H:%M:%S")
            last_click = time.perf_counter()
            pending = None
            det = (f"parecido {score:.3f}" if self.finder.mode == "plantilla"
                   else f"área {area} px²")
            extra = f" (+{len(found) - 1} candidatos más)" if len(found) > 1 else ""
            self.log(f"Clic en ({x}, {y}) — {det}{extra}  "
                     f"[total: {self.clicks_done}]"
                     + (f"  captura: {shot}" if shot else ""))
            if self.sound:
                beep(True)

    def _click(self, x, y):
        prev = self.mouse.position
        self.mouse.position = (x, y)
        # Un juego a 60 fps tarda un fotograma o dos en enterarse de que el
        # cursor se ha movido. Si pulsamos antes, el clic se procesa con la
        # casilla anterior bajo el ratón y no cuenta.
        time.sleep(self.move_delay)
        self.mouse.press(Button.left)
        time.sleep(0.06)
        self.mouse.release(Button.left)
        if self.double_click:
            time.sleep(0.10)
            self.mouse.press(Button.left)
            time.sleep(0.06)
            self.mouse.release(Button.left)
        if self.restore_mouse:
            time.sleep(0.20)   # deja que el juego procese el clic antes de irse
            self.mouse.position = prev

    def _save_shot(self, x, y, n):
        """Guarda una captura marcando dónde va a clicar, para poder auditarlo."""
        try:
            bgr, mon = self.finder.grab_screen()
            vis = bgr.copy()
            px, py = int(x - mon["left"]), int(y - mon["top"])
            cv2.drawMarker(vis, (px, py), (0, 0, 255), cv2.MARKER_CROSS, 44, 2)
            cv2.circle(vis, (px, py), 28, (0, 0, 255), 2)
            cv2.putText(vis, f"clic #{n} en ({x}, {y})",
                        (max(4, px - 130), max(24, py - 38)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
            path = SHOT_PATH % (n % 3 + 1)
            cv2.imwrite(path, cv2.resize(vis, None, fx=0.5, fy=0.5,
                                         interpolation=cv2.INTER_AREA))
            return os.path.basename(path)
        except Exception:
            return None


# ---------------------------------------------------------------- GUI

class App:
    def __init__(self, root):
        self.root = root
        root.title(f"{APP_NAME} — automatiza clics y macros")
        root.geometry("600x820")

        self.recorder = Recorder()
        self.player = Player(on_finish=self._on_play_finish,
                             on_progress=self._on_play_progress)
        self.finder = Finder()
        self.watcher = Watcher(self.finder, self.log, self.set_status)
        self.events = []
        self.current_file = None
        self._zone_p1 = None
        self._sched_stop = threading.Event()
        self._sched_thread = None

        self._build_ui()
        self._migrado = False
        self._load_config()
        self._start_hotkeys()
        self.log(f"{APP_NAME} listo. F6 grabar | F7 reproducir | "
                 f"F2 marcar zona | F8 cuentagotas | F4 plantilla | "
                 f"F9 vigilar | F12 PARAR")
        if self._migrado:
            self.log("He cambiado tus ajustes al modo nuevo 'lo único con "
                     "color': dentro de la zona que marques clica lo único que "
                     "tenga color, sin calibrar ningún tono. Vuelve a marcar la "
                     "zona con F2 y prueba la detección.")
        if self.finder.template is not None:
            th, tw = self.finder.template.shape[:2]
            self.log(f"Plantilla cargada de disco ({tw}x{th} px).")
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

        row3 = ttk.Frame(fm)
        row3.pack(fill="x", **pad)
        self.var_sched = tk.BooleanVar(value=False)
        ttk.Checkbutton(row3, text="Repetir la macro sola cada",
                        variable=self.var_sched,
                        command=self._toggle_scheduler).pack(side="left")
        self.var_sched_min = tk.StringVar(value="15")
        ttk.Spinbox(row3, textvariable=self.var_sched_min, from_=1, to=1440,
                    width=6).pack(side="left", padx=4)
        ttk.Label(row3, text="minutos").pack(side="left")

        self.lbl_macro = ttk.Label(fm, text="Sin macro cargada")
        self.lbl_macro.pack(anchor="w", **pad)

        # --- sección vigilante ---
        fc = ttk.LabelFrame(self.root, text=" Vigilante (clic automático) ")
        fc.pack(fill="x", **pad)

        rowm = ttk.Frame(fc)
        rowm.pack(fill="x", **pad)
        ttk.Label(rowm, text="Buscar:").pack(side="left")
        self.var_mode = tk.StringVar(value="unico")
        ttk.Radiobutton(rowm, text="Lo único con color", value="unico",
                        variable=self.var_mode,
                        command=self._on_mode_change).pack(side="left", padx=4)
        ttk.Radiobutton(rowm, text="Un color concreto", value="color",
                        variable=self.var_mode,
                        command=self._on_mode_change).pack(side="left", padx=4)
        ttk.Radiobutton(rowm, text="Imagen de referencia", value="plantilla",
                        variable=self.var_mode,
                        command=self._on_mode_change).pack(side="left", padx=4)

        rowc0 = ttk.Frame(fc)
        rowc0.pack(fill="x", **pad)
        ttk.Button(rowc0, text="Cuentagotas de color (F8)",
                   command=self.pick_color).pack(side="left", padx=4)
        ttk.Button(rowc0, text="Capturar imagen (F4)",
                   command=self.capture_template).pack(side="left", padx=4)
        ttk.Button(rowc0, text="Marcar zona (F2)",
                   command=self.mark_zone).pack(side="left", padx=4)
        ttk.Button(rowc0, text="Toda la pantalla",
                   command=self.reset_zone).pack(side="left", padx=4)
        self.lbl_color = ttk.Label(rowc0, text="sin calibrar")
        self.lbl_color.pack(side="left", padx=8)

        rowc1 = ttk.Frame(fc)
        rowc1.pack(fill="x", **pad)
        self.btn_watch = ttk.Button(rowc1, text="Activar vigilancia (F9)",
                                    command=self.toggle_watch)
        self.btn_watch.pack(side="left", padx=4)
        ttk.Button(rowc1, text="Probar detección (3 s)",
                   command=self.test_detection).pack(side="left", padx=4)
        ttk.Button(rowc1, text="Ver imagen de depuración",
                   command=self.open_debug).pack(side="left", padx=4)

        grid = ttk.Frame(fc)
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
        self.var_tpl_size = tk.StringVar(value="40")
        self.var_tpl_thr = tk.StringVar(value="0.85")
        self.var_frames = tk.StringVar(value="3")
        self.var_max_side = tk.StringVar(value="80")
        self.var_interval = tk.StringVar(value="1.2")
        self.var_cooldown = tk.StringVar(value="10")
        self.var_roi_top = tk.StringVar(value="0")
        self.var_roi_bottom = tk.StringVar(value="100")
        self.var_roi_left = tk.StringVar(value="0")
        self.var_roi_right = tk.StringVar(value="100")
        spin(0, 0, "Tono (0-179):", self.var_hue, 0, 179)
        spin(0, 1, "± tolerancia:", self.var_hue_tol, 1, 60)
        spin(1, 0, "Saturación mín.:", self.var_sat, 0, 255)
        spin(1, 1, "Brillo mín.:", self.var_val, 0, 255)
        spin(2, 0, "Área mín. (px²):", self.var_area, 10, 100000, 10)
        spin(2, 1, "Área máx. (px²):", self.var_area_max, 100, 5000000, 1000)
        spin(3, 0, "Lado de la imagen (px):", self.var_tpl_size, 12, 400, 4)
        spin(3, 1, "Parecido mín. (0-1):", self.var_tpl_thr, 0.5, 0.99, 0.01)
        spin(4, 0, "Zona: alto de % a %:", self.var_roi_top, 0, 99)
        spin(4, 1, "", self.var_roi_bottom, 1, 100)
        spin(7, 0, "Zona: ancho de % a %:", self.var_roi_left, 0, 99)
        spin(7, 1, "", self.var_roi_right, 1, 100)
        spin(5, 0, "Escaneo cada (s):", self.var_interval, 0.2, 10, 0.1)
        spin(5, 1, "Cooldown (s):", self.var_cooldown, 1, 3600)
        spin(6, 0, "Lado máx. (px):", self.var_max_side, 10, 2000, 10)
        spin(6, 1, "Fotogramas unidos:", self.var_frames, 1, 6)

        rowc2 = ttk.Frame(fc)
        rowc2.pack(fill="x", **pad)
        self.var_double = tk.BooleanVar(value=False)
        ttk.Checkbutton(rowc2, text="Doble clic",
                        variable=self.var_double).pack(side="left")
        self.var_restore = tk.BooleanVar(value=True)
        ttk.Checkbutton(rowc2, text="Devolver el ratón",
                        variable=self.var_restore).pack(side="left", padx=10)
        self.var_sound = tk.BooleanVar(value=True)
        ttk.Checkbutton(rowc2, text="Pitido al clicar",
                        variable=self.var_sound).pack(side="left", padx=10)

        rowc3 = ttk.Frame(fc)
        rowc3.pack(fill="x", **pad)
        self.var_on_gui = tk.BooleanVar(value=True)
        ttk.Checkbutton(rowc3, text="Solo sobre una interfaz (fondo gris)",
                        variable=self.var_on_gui,
                        command=self._apply_settings).pack(side="left")
        self.var_shots = tk.BooleanVar(value=True)
        ttk.Checkbutton(rowc3, text="Guardar captura de cada clic",
                        variable=self.var_shots,
                        command=self._apply_settings).pack(side="left", padx=10)
        ttk.Button(rowc3, text="Ver último clic",
                   command=self.open_shot).pack(side="left", padx=4)

        # --- opciones generales ---
        fo = ttk.Frame(self.root)
        fo.pack(fill="x", **pad)
        self.var_topmost = tk.BooleanVar(value=False)
        ttk.Checkbutton(fo, text="Ventana siempre visible",
                        variable=self.var_topmost,
                        command=self._apply_topmost).pack(side="left")
        self.var_logfile = tk.BooleanVar(value=True)
        ttk.Checkbutton(fo, text="Guardar registro en archivo",
                        variable=self.var_logfile).pack(side="left", padx=10)
        ttk.Button(fo, text="■ PARADA TOTAL (F12)",
                   command=self.panic).pack(side="right", padx=4)

        # --- registro ---
        fl = ttk.LabelFrame(self.root, text=" Registro ")
        fl.pack(fill="both", expand=True, **pad)
        self.txt_log = tk.Text(fl, height=11, state="disabled",
                               font=("Consolas", 9))
        self.txt_log.pack(fill="both", expand=True, padx=4, pady=4)

        self.status = ttk.Label(self.root, relief="sunken", anchor="w",
                                text="Inactivo")
        self.status.pack(fill="x", side="bottom")

    # ---------- helpers ----------
    def log(self, msg):
        linea = time.strftime("[%H:%M:%S] ") + msg

        def _append():
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end", linea + "\n")
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")
        self.root.after(0, _append)
        if getattr(self, "var_logfile", None) and self.var_logfile.get():
            try:
                with open(LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d ") + linea + "\n")
            except Exception:
                pass

    def set_status(self, text):
        self.root.after(0, lambda: self.status.configure(text=text))

    def _apply_topmost(self):
        self.root.attributes("-topmost", self.var_topmost.get())

    def _apply_settings(self):
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
            f.tpl_size = int(float(self.var_tpl_size.get()))
            f.tpl_thr = float(self.var_tpl_thr.get())
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
        except ValueError:
            self.log("Aviso: algún parámetro no es un número válido; "
                     "se mantienen los anteriores.")

    # ---------- parada de emergencia ----------
    def panic(self):
        paro = []
        if self.player.playing:
            self.player.stop()
            paro.append("reproducción")
        if self.watcher.active:
            self.watcher.stop()
            self.btn_watch.configure(text="Activar vigilancia (F9)")
            paro.append("vigilancia")
        if self.recorder.recording:
            self.events = self.recorder.stop()
            self.btn_rec.configure(text="● Grabar (F6)")
            paro.append("grabación")
        if self.var_sched.get():
            self.var_sched.set(False)
            self._toggle_scheduler()
            paro.append("repetición programada")
        self.set_status("PARADA TOTAL")
        self.log("PARADA TOTAL: " + (", ".join(paro) if paro
                                     else "no había nada activo") + ".")
        beep(False)

    def _on_mode_change(self):
        self._apply_settings()
        if self.finder.mode == "unico":
            self.log("Modo 'lo único con color': no hace falta el cuentagotas. "
                     "Marca la zona con F2 y listo — dentro de ella clicará lo "
                     "único que tenga color, sea del tono que sea.")
        elif self.finder.mode == "color":
            self.log("Modo 'un color concreto': usa el cuentagotas (F8) sobre "
                     "el objetivo para calibrar el tono.")
        else:
            self.log("Modo 'imagen de referencia': captura el recorte con F4. "
                     "No sirve con objetos encantados.")

    # ---------- zona de búsqueda ----------
    def mark_zone(self):
        """Marca el rectángulo de búsqueda con dos pulsaciones de F2.

        Dos esquinas a golpe de tecla en vez de un arrastre: así funciona
        igual de bien sobre un juego a pantalla completa, donde no se puede
        dibujar un recuadro encima.
        """
        x, y = MouseController().position
        with mss.mss() as sct:
            mon = sct.monitors[1]
        if self._zone_p1 is None:
            self._zone_p1 = (x, y)
            self.log(f"Zona: esquina 1 en ({x}, {y}). Lleva el ratón a la "
                     f"esquina opuesta y pulsa F2 otra vez.")
            beep(True)
            return
        x1, y1 = self._zone_p1
        self._zone_p1 = None
        left, right = sorted((x1 - mon["left"], x - mon["left"]))
        top, bottom = sorted((y1 - mon["top"], y - mon["top"]))
        if right - left < 20 or bottom - top < 20:
            self.log("Zona demasiado pequeña; inténtalo otra vez con F2.")
            beep(False)
            return
        # Los ajustes se guardan en % entero, y en 1920 px cada 1% son 19 px.
        # Al redondear hay que crecer siempre hacia fuera: si se redondeara al
        # más cercano, un borde en el 58.4% se guardaría como 58 y recortaría
        # 11 px de lo que el usuario acaba de marcar, con el objetivo dentro.
        self.var_roi_left.set(str(math.floor(left * 100 / mon["width"])))
        self.var_roi_right.set(str(max(1, math.ceil(right * 100 / mon["width"]))))
        self.var_roi_top.set(str(math.floor(top * 100 / mon["height"])))
        self.var_roi_bottom.set(
            str(max(1, math.ceil(bottom * 100 / mon["height"]))))
        self._apply_settings()
        f = self.finder
        real_w = int((f.roi_right - f.roi_left) * mon["width"])
        real_h = int((f.roi_bottom - f.roi_top) * mon["height"])
        self.log(f"Zona de búsqueda fijada: marcaste {right - left}x"
                 f"{bottom - top} px desde ({left}, {top}); se guarda como "
                 f"{real_w}x{real_h} px (el % es entero, así que se redondea "
                 f"hacia fuera). Fuera de ahí no mirará nada.")
        beep(True)

    def reset_zone(self):
        self._zone_p1 = None
        for var, val in ((self.var_roi_left, "0"), (self.var_roi_right, "100"),
                         (self.var_roi_top, "0"), (self.var_roi_bottom, "100")):
            var.set(val)
        self._apply_settings()
        self.log("Zona de búsqueda: toda la pantalla.")

    # ---------- cuentagotas / plantilla ----------
    def pick_color(self):
        try:
            h, s, v, b, g, r, inestable = self.finder.pick_color_at_cursor()
        except Exception as exc:
            self.log(f"Error leyendo el color: {exc}")
            return
        # Rango generoso alrededor de la muestra: los sprites tienen sombras y
        # los cristales translúcidos varían según el fondo.
        self.var_mode.set("color")
        self.var_hue.set(str(h))
        self.var_hue_tol.set("12")
        self.var_sat.set(str(max(25, s - 60)))
        self.var_val.set(str(max(50, v - 60)))
        self._apply_settings()
        self.lbl_color.configure(text=f"RGB({r},{g},{b})  H={h} S={s} V={v}")
        self.log(f"Color capturado: RGB({r},{g},{b}) → tono {h}, "
                 f"saturación {s}, brillo {v}. Rango: tono {h}±12, "
                 f"sat≥{max(25, s - 60)}, brillo≥{max(50, v - 60)}.")
        if inestable:
            self.log("Ojo: el color parpadeaba mientras lo leía. Es el brillo "
                     "de un objeto encantado; he usado el color de debajo. "
                     "Deja 'Fotogramas unidos' en 3 o más y NO uses el modo "
                     "Imagen de referencia con objetos encantados.")
        self.log("He cambiado al modo 'un color concreto'. Si dentro de tu "
                 "zona el objetivo es lo único que tiene color, el modo 'lo "
                 "único con color' acierta más y no necesita esta calibración.")
        self.log("Pulsa 'Probar detección' para comprobarlo sin clicar.")
        beep(True)

    def capture_template(self):
        self._apply_settings()
        try:
            w, h = self.finder.capture_template()
        except Exception as exc:
            self.log(f"Error capturando la imagen: {exc}")
            return
        self.var_mode.set("plantilla")
        self._apply_settings()
        self.lbl_color.configure(text=f"plantilla {w}x{h} px")
        self.log(f"Imagen de referencia capturada ({w}x{h} px) y guardada en "
                 f"{TEMPLATE_IMG}. Modo cambiado a 'Imagen de referencia'.")
        self.log("Este modo es más selectivo que el color, pero exige que el "
                 "objetivo se vea siempre del mismo tamaño.")
        beep(True)

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

    # ---------- repetición programada ----------
    def _toggle_scheduler(self):
        if self.var_sched.get():
            if not self.events:
                self.log("Graba o carga una macro antes de programarla.")
                self.var_sched.set(False)
                return
            try:
                minutos = float(self.var_sched_min.get())
            except ValueError:
                self.log("Los minutos no son un número válido.")
                self.var_sched.set(False)
                return
            self._sched_stop.clear()
            self._sched_thread = threading.Thread(
                target=self._sched_run, args=(minutos,), daemon=True)
            self._sched_thread.start()
            self.log(f"Repetición programada: cada {minutos:g} minutos.")
        else:
            self._sched_stop.set()
            self.log("Repetición programada desactivada.")

    def _sched_run(self, minutos):
        while not self._sched_stop.wait(minutos * 60):
            if self.recorder.recording or self.player.playing:
                self.log("Salto la repetición programada: ya había algo "
                         "en marcha.")
                continue
            self.log("Repetición programada: lanzando la macro.")
            self.root.after(0, self.toggle_play)

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

    # ---------- vigilante ----------
    def toggle_watch(self):
        if not self.watcher.active:
            self._apply_settings()
            if self.finder.mode == "plantilla" and self.finder.template is None:
                self.log("No hay imagen de referencia: captúrala con F4 o "
                         "cambia a otro modo.")
                return
            f = self.finder
            sin_zona = (f.roi_left, f.roi_right, f.roi_top,
                        f.roi_bottom) == (0.0, 1.0, 0.0, 1.0)
            if f.mode == "unico" and sin_zona and not f.on_gui:
                self.log("No activo la vigilancia: en modo 'lo único con "
                         "color', sin zona marcada y sin el filtro de "
                         "interfaz, mira toda la pantalla — y en un juego casi "
                         "todo tiene color, así que clicaría cualquier cosa. "
                         "Marca la zona con F2 (o marca 'Solo sobre una "
                         "interfaz').")
                beep(False)
                return
            self.watcher.start()
            self.btn_watch.configure(text="Desactivar vigilancia (F9)")
            self.log(f"Vigilancia ACTIVADA (modo {self.finder.mode}).")
        else:
            self.watcher.stop()
            self.btn_watch.configure(text="Activar vigilancia (F9)")
            self.set_status("Inactivo")
            self.log("Vigilancia desactivada.")

    def test_detection(self):
        self._apply_settings()
        self.log("Probando en 3 segundos — deja la pantalla como cuando "
                 "sale el aviso…")

        def _do():
            time.sleep(3)
            try:
                found = self.finder.candidates(save_debug=True)
            except Exception as exc:
                self.log(f"Error: {exc}")
                return
            if not found:
                if self.finder.mode == "plantilla":
                    sc = getattr(self.finder, "last_score", None)
                    self.log("NO detectado. Mejor parecido encontrado: "
                             f"{sc:.3f} (umbral {self.finder.tpl_thr:.2f}). "
                             "Baja el 'Parecido mín.' o recaptura la imagen."
                             if sc is not None else "NO detectado.")
                else:
                    self.log("NO detectado. Esto es lo que veía dentro de la "
                             "zona:")
                    try:
                        for l in self.finder.zone_report():
                            self.log("   " + l)
                    except Exception as exc:
                        self.log(f"   (no se pudo inspeccionar la zona: {exc})")
                    self.log("Si el fondo sale gris y el objetivo aparece "
                             "arriba, baja 'Área mín.' o 'Saturación mín.'. "
                             "Si el fondo sale con color, vuelve a marcar la "
                             "zona con F2.")
            else:
                self.log(f"{len(found)} candidato(s). El nº 1 es el que se "
                         f"clicaría:")
                for i, (x, y, a, s) in enumerate(found[:5], 1):
                    det = (f"parecido {s:.3f}"
                           if self.finder.mode == "plantilla"
                           else f"área {a} px²")
                    self.log(f"   {i}. ({x}, {y})  {det}")
                if len(found) > 1:
                    self.log("Hay más de un candidato: si el correcto no es "
                             "el nº 1, aprieta la zona con F2 hasta que solo "
                             "quede uno. Es lo que mejor funciona.")
            for r in self.finder.rejects[:8]:
                self.log("   · " + r)
            self.log(f"Imagen de depuración: {DEBUG_IMG}")
        threading.Thread(target=_do, daemon=True).start()

    def open_debug(self):
        if not os.path.exists(DEBUG_IMG):
            self.log("Aún no hay imagen de depuración: usa 'Probar detección'.")
            return
        try:
            os.startfile(DEBUG_IMG)
        except Exception as exc:
            self.log(f"No se pudo abrir: {exc}")

    def open_shot(self):
        shots = [SHOT_PATH % i for i in (1, 2, 3)]
        shots = [p for p in shots if os.path.exists(p)]
        if not shots:
            self.log("Aún no hay capturas de clics.")
            return
        ultima = max(shots, key=os.path.getmtime)
        try:
            os.startfile(ultima)
        except Exception as exc:
            self.log(f"No se pudo abrir: {exc}")

    # ---------- hotkeys ----------
    def _start_hotkeys(self):
        acciones = {
            HOTKEY_RECORD: self.toggle_record,
            HOTKEY_PLAY: self.toggle_play,
            HOTKEY_PICK: self.pick_color,
            HOTKEY_TEMPLATE: self.capture_template,
            HOTKEY_WATCH: self.toggle_watch,
            HOTKEY_ZONE: self.mark_zone,
            HOTKEY_PANIC: self.panic,
        }

        def on_press(key):
            accion = acciones.get(key)
            if accion:
                self.root.after(0, accion)
        self._hotkey_listener = keyboard.Listener(on_press=on_press)
        self._hotkey_listener.start()

    # ---------- configuración ----------
    def _cfg_map(self):
        return {
            "mode": self.var_mode,
            "hue": self.var_hue, "hue_tol": self.var_hue_tol,
            "sat": self.var_sat, "val": self.var_val,
            "area": self.var_area, "area_max": self.var_area_max,
            "tpl_size": self.var_tpl_size, "tpl_thr": self.var_tpl_thr,
            "roi_top": self.var_roi_top, "roi_bottom": self.var_roi_bottom,
            "roi_left": self.var_roi_left, "roi_right": self.var_roi_right,
            "max_side": self.var_max_side, "frames": self.var_frames,
            "interval": self.var_interval, "cooldown": self.var_cooldown,
            "repeats": self.var_repeats, "speed": self.var_speed,
            "sched_min": self.var_sched_min,
        }

    def _load_config(self):
        if not os.path.exists(CONFIG_PATH):
            self._apply_settings()
            return
        try:
            # utf-8-sig: el Notepad de Windows guarda con BOM, y con "utf-8"
            # pelado json.load reventaría y se perderían todos los ajustes
            # sin decir nada.
            with open(CONFIG_PATH, encoding="utf-8-sig") as f:
                cfg = json.load(f)
        except Exception as exc:
            self.log(f"No pude leer {os.path.basename(CONFIG_PATH)} ({exc}); "
                     "sigo con los ajustes de fábrica.")
            self._apply_settings()
            return
        for k, var in self._cfg_map().items():
            if k in cfg:
                var.set(str(cfg[k]))
        # Los ajustes guardados por una versión anterior traen mode="color",
        # que pisaría el modo nuevo. Se cambia una sola vez y se avisa.
        if int(cfg.get("v", 1)) < 2 and self.var_mode.get() == "color":
            self.var_mode.set("unico")
            self._migrado = True
        self.var_double.set(cfg.get("double", False))
        self.var_restore.set(cfg.get("restore", True))
        self.var_sound.set(cfg.get("sound", True))
        self.var_on_gui.set(cfg.get("on_gui", True))
        self.var_shots.set(cfg.get("shots", True))
        self.var_topmost.set(cfg.get("topmost", False))
        self.var_logfile.set(cfg.get("logfile", True))
        self._apply_topmost()
        self._apply_settings()

    def _save_config(self):
        cfg = {k: var.get() for k, var in self._cfg_map().items()}
        cfg["v"] = 2
        cfg["double"] = self.var_double.get()
        cfg["restore"] = self.var_restore.get()
        cfg["sound"] = self.var_sound.get()
        cfg["on_gui"] = self.var_on_gui.get()
        cfg["shots"] = self.var_shots.get()
        cfg["topmost"] = self.var_topmost.get()
        cfg["logfile"] = self.var_logfile.get()
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    def _on_close(self):
        self._save_config()
        self._sched_stop.set()
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
