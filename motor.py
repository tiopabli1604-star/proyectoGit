# -*- coding: utf-8 -*-
"""
Motor comun: mirar la pantalla y clicar.

Aqui vive lo que comparten Golem y AutoCaptcha: el buscador (Finder), que
localiza el objetivo por color o por imagen, y el vigilante (Watcher), que lo
clica cuando aparece. Nada de esto sabe de macros, de teclado ni de camaras.

Se mantiene en un solo sitio a proposito: un arreglo en la deteccion vale para
los dos programas y no pueden divergir.
"""


import base64
import ctypes
import json
import math
import os
import queue
import sys
import tempfile
from ctypes import wintypes
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

NOMBRE_APP = "AutoCaptcha"    # nombre de la carpeta en %LOCALAPPDATA%
EXE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))


# --------------------------------------------- donde guardar los archivos
def _es_temporal(d):
    """¿Está esa carpeta dentro del %TEMP% de Windows?"""
    try:
        t = os.path.normcase(os.path.abspath(tempfile.gettempdir()))
        d = os.path.normcase(os.path.abspath(d))
        return d == t or d.startswith(t + os.sep)
    except Exception:
        return False


def _se_puede_escribir(d):
    try:
        p = os.path.join(d, ".golem_prueba_escritura")
        with open(p, "w") as f:
            f.write("x")
        os.remove(p)
        return True
    except Exception:
        return False


def _elegir_dir_datos():
    """Dónde guardar ajustes, registro, plantilla y capturas.

    Junto al ejecutable mientras se pueda, que es lo cómodo y lo portable. Pero
    si el exe se abre directamente desde la descarga del navegador, Windows lo
    ejecuta desde un 'scoped_dir' de %TEMP% que se borra solo: los ajustes, la
    zona marcada y la plantilla se perderían en cada arranque sin que se note.
    En ese caso se usa %LOCALAPPDATA%\\Golem, que no se va.
    """
    if _es_temporal(EXE_DIR):
        motivo = ("el ejecutable se está ejecutando desde una carpeta temporal "
                  "de Windows, que se borra sola")
    elif not _se_puede_escribir(EXE_DIR):
        motivo = "no puedo escribir en la carpeta del ejecutable"
    else:
        return EXE_DIR, ""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    destino = os.path.join(base, NOMBRE_APP)
    try:
        os.makedirs(destino, exist_ok=True)
    except Exception:
        return EXE_DIR, ""
    return destino, motivo


APP_DIR, DATA_MOTIVO = _elegir_dir_datos()
# Nombres por defecto; cada programa los cambia por los suyos al importar.
DEBUG_IMG = os.path.join(APP_DIR, "debug.png")
SHOT_PATH = os.path.join(APP_DIR, "clic_%d.png")
TEMPLATE_IMG = os.path.join(APP_DIR, "plantilla.png")

# Las mismas teclas y con el mismo significado que en Golem, para que no haya
# sorpresas si algún día se tienen los dos programas abiertos.
HOTKEY_ZONE = keyboard.Key.f2       # marcar la zona
HOTKEY_PICK = keyboard.Key.f8       # cuentagotas
HOTKEY_WATCH = keyboard.Key.f6      # vigilar
HOTKEY_PANIC = keyboard.Key.f12     # parada total


SCALE = 0.5  # las capturas de color se analizan a media resolución

# ------------------------------------------------ ventana activa y clic
_user32 = ctypes.windll.user32
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetForegroundWindow.restype = wintypes.HWND


def ventana_activa():
    """Título de la ventana que tiene el foco. Cadena vacía si no se sabe."""
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return ""
        n = int(_user32.GetWindowTextLengthW(hwnd))
        buf = ctypes.create_unicode_buffer(n + 2)
        _user32.GetWindowTextW(hwnd, buf, n + 2)
        return buf.value or ""
    except Exception:
        return ""


def click_at(mouse, x, y, boton="left", doble=False, restore=True,
             move_delay=0.20):
    """Mueve, clica y (si se pide) devuelve el ratón donde estaba.

    Lo usan el vigilante y el guion, para que los dos tengan el mismo retardo
    entre mover y pulsar: un juego a 60 fps tarda un fotograma o dos en
    enterarse de que el cursor se ha movido, y si se pulsa antes el clic se
    procesa con la casilla anterior bajo el ratón y no cuenta.
    """
    btn = getattr(Button, boton)
    prev = mouse.position
    mouse.position = (x, y)
    time.sleep(move_delay)
    mouse.press(btn)
    time.sleep(0.06)
    mouse.release(btn)
    if doble:
        time.sleep(0.10)
        mouse.press(btn)
        time.sleep(0.06)
        mouse.release(btn)
    if restore:
        time.sleep(0.20)   # deja que el juego procese el clic antes de irse
        mouse.position = prev


def beep(ok=True):
    if winsound is None:
        return
    try:
        winsound.Beep(880 if ok else 300, 90)
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

    # claves que definen "qué buscar y dónde": un objetivo con nombre
    PERFIL = ("mode", "hue", "hue_tol", "sat_min", "val_min", "min_area",
              "max_area", "max_side", "frames", "on_gui", "tpl_thr",
              "roi_left", "roi_right", "roi_top", "roi_bottom")

    def snapshot(self):
        """Los ajustes de búsqueda de ahora, para guardarlos como objetivo."""
        return {k: getattr(self, k) for k in self.PERFIL}

    def apply(self, perfil):
        """Carga un objetivo guardado. Ignora claves que ya no existan."""
        for k in self.PERFIL:
            if k in perfil:
                setattr(self, k, perfil[k])

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
        self.ventana_req = ""         # solo clicar si el título la contiene
        self.verificar = True         # comprobar que el objetivo se fue

    def ventana_ok(self):
        if not self.ventana_req:
            return True
        return self.ventana_req.lower() in ventana_activa().lower()

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
            if not self.ventana_ok():
                pending = None
                if self.status:
                    self.status(f"VIGILANDO EN PAUSA — delante está "
                                f"«{ventana_activa()}»")
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
            if len(found) > 1:
                # saber quién competía es lo que dice si la zona está holgada
                otros = ", ".join(f"({c[0]}, {c[1]}) {c[2]} px²"
                                  for c in found[1:4])
                self.log(f"   competían: {otros}. Si alguno de esos es el "
                         f"bueno, aprieta la zona con F2.")
            if self.verificar:
                # ¿ha servido de algo? Si el objetivo sigue ahí, el clic no
                # contó, y eso conviene saberlo antes de 15 minutos
                time.sleep(0.6)
                try:
                    sigue = self.finder.candidates()
                except Exception:
                    sigue = None
                if sigue and abs(sigue[0][0] - x) < 40 \
                        and abs(sigue[0][1] - y) < 40:
                    self.log("   ojo: el objetivo sigue ahí después del clic. "
                             "O no ha contado, o tarda en desaparecer.")
            if self.sound:
                beep(True)

    def _click(self, x, y):
        click_at(self.mouse, x, y, doble=self.double_click,
                 restore=self.restore_mouse, move_delay=self.move_delay)

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

