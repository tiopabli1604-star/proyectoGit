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
  F9  = grabar / parar grabación
  F10 = reproducir / parar reproducción
  F2  = marcar la zona de búsqueda: dos esquinas, una pulsación cada una
  F8  = cuentagotas: capturar el color bajo el ratón y calibrarse solo
  F4  = capturar plantilla: recorta la imagen bajo el ratón como referencia
  F6  = activar / desactivar el vigilante
  F7  = ejecutar / parar el guion de varios pasos
  F12 = PARADA TOTAL de emergencia
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

APP_NAME = "Golem"
EXE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))


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
    destino = os.path.join(base, APP_NAME)
    try:
        os.makedirs(destino, exist_ok=True)
    except Exception:
        return EXE_DIR, ""
    return destino, motivo


APP_DIR, DATA_MOTIVO = _elegir_dir_datos()
CONFIG_PATH = os.path.join(APP_DIR, "golem_config.json")
DEBUG_IMG = os.path.join(APP_DIR, "golem_debug.png")
SHOT_PATH = os.path.join(APP_DIR, "golem_clic_%d.png")
WATCH_SHOT = os.path.join(APP_DIR, "golem_vigilancia_%02d.png")
TEMPLATE_IMG = os.path.join(APP_DIR, "golem_plantilla.png")
LOG_PATH = os.path.join(APP_DIR, "golem_log.txt")

HOTKEY_RECORD = keyboard.Key.f9
HOTKEY_PLAY = keyboard.Key.f10
HOTKEY_PICK = keyboard.Key.f8
HOTKEY_TEMPLATE = keyboard.Key.f4
HOTKEY_WATCH = keyboard.Key.f6
HOTKEY_ZONE = keyboard.Key.f2
HOTKEY_SCRIPT = keyboard.Key.f7
HOTKEY_PANIC = keyboard.Key.f12
HOTKEYS = {HOTKEY_RECORD, HOTKEY_PLAY, HOTKEY_PICK, HOTKEY_TEMPLATE,
           HOTKEY_WATCH, HOTKEY_ZONE, HOTKEY_SCRIPT, HOTKEY_PANIC}

# Los nombres para los botones y los mensajes salen de las constantes de
# arriba: así, si se cambia una tecla, no queda ninguna etiqueta mintiendo.
T_REC = HOTKEY_RECORD.name.upper()
T_PLAY = HOTKEY_PLAY.name.upper()
T_PICK = HOTKEY_PICK.name.upper()
T_TPL = HOTKEY_TEMPLATE.name.upper()
T_WATCH = HOTKEY_WATCH.name.upper()
T_ZONE = HOTKEY_ZONE.name.upper()
T_SCRIPT = HOTKEY_SCRIPT.name.upper()
T_PANIC = HOTKEY_PANIC.name.upper()

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


# ------------------------------------- movimiento relativo (juegos en 1ª persona)
#
# En un juego que captura el ratón (Minecraft en primera persona) el cursor del
# sistema no se mueve: el juego pide "raw input" y lee los desplazamientos que
# manda el ratón, no dónde está el puntero. Por eso mover el cursor con
# SetCursorPos —lo que hace pynput— no gira la cámara. Hay que hacer las dos
# mitades con la API de Windows:
#
#   · grabar: registrarse como receptor de raw input y quedarse con los deltas
#     (lLastX / lLastY) de cada informe del ratón;
#   · reproducir: SendInput con MOUSEEVENTF_MOVE *sin* MOUSEEVENTF_ABSOLUTE, que
#     inyecta un desplazamiento relativo y sí llega al juego.

_user32 = ctypes.windll.user32

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_MOVE_NOCOALESCE = 0x2000
RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
MOUSE_MOVE_ABSOLUTE = 0x01
WM_INPUT = 0x00FF
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
HWND_MESSAGE = -3


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT_U(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_U)]


def move_relative(dx, dy):
    """Inyecta un desplazamiento relativo del ratón. Devuelve True si entró.

    Es lo único que entiende un juego en primera persona con el ratón preso.
    NOCOALESCE evita que Windows junte varios movimientos seguidos en uno, que
    es justo lo que arruinaría la precisión al reproducir rápido.
    """
    dx, dy = int(dx), int(dy)
    if dx == 0 and dy == 0:
        return True
    inp = _INPUT(type=INPUT_MOUSE)
    inp.mi = _MOUSEINPUT(dx=dx, dy=dy, mouseData=0,
                         dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_MOVE_NOCOALESCE,
                         time=0, dwExtraInfo=None)
    return _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)) == 1


class _RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


class _RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
                ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM)]


class _RAWMOUSE_BTN(ctypes.Structure):
    _fields_ = [("usButtonFlags", wintypes.USHORT),
                ("usButtonData", wintypes.USHORT)]


class _RAWMOUSE_U(ctypes.Union):
    _fields_ = [("ulButtons", wintypes.ULONG), ("btn", _RAWMOUSE_BTN)]


class _RAWMOUSE(ctypes.Structure):
    _fields_ = [("usFlags", wintypes.USHORT), ("u", _RAWMOUSE_U),
                ("ulRawButtons", wintypes.ULONG),
                ("lLastX", wintypes.LONG), ("lLastY", wintypes.LONG),
                ("ulExtraInformation", wintypes.ULONG)]


class _RAWINPUT(ctypes.Structure):
    _fields_ = [("header", _RAWINPUTHEADER), ("mouse", _RAWMOUSE)]


# LRESULT es LONG_PTR: del tamaño de un puntero, no siempre 32 bits
_LRESULT = ctypes.c_ssize_t
_WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT,
                              wintypes.WPARAM, wintypes.LPARAM)
_user32.DefWindowProcW.restype = _LRESULT
_user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                   wintypes.WPARAM, wintypes.LPARAM]
_user32.SendInput.restype = wintypes.UINT
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
_user32.CreateWindowExW.restype = wintypes.HWND
_user32.GetRawInputData.restype = wintypes.UINT


class _WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", _WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


_RAW_CLASE = "GolemRawMouse"
_raw_oyentes = {}            # hwnd -> RawMouseListener
_raw_clase_lista = False
_raw_lock = threading.Lock()


def _raw_wndproc(hwnd, msg, wparam, lparam):
    """Único WNDPROC del proceso; reparte por hwnd.

    La clase de ventana se registra una sola vez y se queda con el puntero al
    WNDPROC para siempre. Si ese puntero fuera un método de una instancia, en
    cuanto la instancia se recogiera Windows saltaría a memoria liberada
    (access violation al despachar WM_INPUT). Por eso el WNDPROC vive en el
    módulo y la instancia se busca en un diccionario.
    """
    if msg == WM_INPUT:
        oyente = _raw_oyentes.get(int(hwnd) if hwnd else 0)
        if oyente is not None:
            oyente._procesar(lparam)
    elif msg == WM_CLOSE:
        _user32.DestroyWindow(hwnd)
        return 0
    elif msg == WM_DESTROY:
        # saca del bucle GetMessage al hilo que está despachando, que es el del
        # propio oyente. Sin esto habría que confiar en que GetMessage devuelva
        # -1 al quedarse la ventana inválida, que es mucho más frágil.
        _user32.PostQuitMessage(0)
        return 0
    return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)


_RAW_WNDPROC = _WNDPROC(_raw_wndproc)   # referencia viva mientras corra el proceso


class RawMouseListener:
    """Escucha los desplazamientos crudos del ratón, los mire quien los mire.

    Crea una ventana sin interfaz (HWND_MESSAGE) y se registra con
    RIDEV_INPUTSINK, que es lo que permite seguir recibiendo los informes del
    ratón aunque la ventana activa sea el juego. Cada informe trae el delta que
    ha mandado el ratón, que es exactamente lo que hay que guardar.
    """

    def __init__(self, on_move):
        self.on_move = on_move
        self._hwnd = None
        self._thread = None
        self._listo = threading.Event()
        self.error = None

    def _procesar(self, lparam):
        tam = wintypes.UINT(ctypes.sizeof(_RAWINPUT))
        datos = _RAWINPUT()
        leidos = _user32.GetRawInputData(
            wintypes.HANDLE(lparam), RID_INPUT, ctypes.byref(datos),
            ctypes.byref(tam), ctypes.sizeof(_RAWINPUTHEADER))
        if leidos <= 0 or datos.header.dwType != RIM_TYPEMOUSE:
            return
        m = datos.mouse
        # los ratones normales informan en relativo; un digitalizador o un
        # escritorio remoto puede informar en absoluto, y entonces lLastX y
        # lLastY no son deltas y no sirven
        if (m.usFlags & MOUSE_MOVE_ABSOLUTE) or not (m.lLastX or m.lLastY):
            return
        try:
            self.on_move(m.lLastX, m.lLastY)
        except Exception:
            pass

    def _run(self):
        global _raw_clase_lista
        try:
            hinst = ctypes.windll.kernel32.GetModuleHandleW(None)
            with _raw_lock:
                if not _raw_clase_lista:
                    wc = _WNDCLASS()
                    wc.lpfnWndProc = _RAW_WNDPROC
                    wc.hInstance = hinst
                    wc.lpszClassName = _RAW_CLASE
                    if not _user32.RegisterClassW(ctypes.byref(wc)):
                        raise OSError("no pude registrar la clase de ventana")
                    _raw_clase_lista = True
            hwnd = _user32.CreateWindowExW(
                0, _RAW_CLASE, _RAW_CLASE, 0, 0, 0, 0, 0,
                wintypes.HWND(HWND_MESSAGE), None, hinst, None)
            if not hwnd:
                raise OSError("no pude crear la ventana de mensajes")
            # apuntarse ANTES de registrar el raw input, para no perder el
            # primer informe que llegue
            _raw_oyentes[int(hwnd)] = self
            self._hwnd = hwnd
            rid = _RAWINPUTDEVICE(usUsagePage=0x01, usUsage=0x02,
                                  dwFlags=RIDEV_INPUTSINK,
                                  hwndTarget=hwnd)
            if not _user32.RegisterRawInputDevices(
                    ctypes.byref(rid), 1, ctypes.sizeof(_RAWINPUTDEVICE)):
                raise OSError("no pude registrarme para recibir raw input")
        except Exception as exc:
            self.error = exc
            if self._hwnd:
                _raw_oyentes.pop(int(self._hwnd), None)
                _user32.DestroyWindow(wintypes.HWND(self._hwnd))
                self._hwnd = None
            self._listo.set()
            return
        self._listo.set()
        msg = wintypes.MSG()
        # hwnd nulo: todos los mensajes de este hilo, y así WM_QUIT (que no va
        # dirigido a una ventana) también corta el bucle
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
        _raw_oyentes.pop(int(hwnd), None)

    def start(self, timeout=2.0):
        """Arranca y espera a saber si se pudo registrar. True si va."""
        self._listo.clear()
        self.error = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._listo.wait(timeout)
        return self.error is None and self._hwnd is not None

    def stop(self):
        hwnd = self._hwnd
        if not hwnd:
            return
        self._hwnd = None
        _raw_oyentes.pop(int(hwnd), None)
        # el bucle de mensajes vive en el otro hilo; se le saca con un WM_CLOSE
        _user32.PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0)
        if self._thread:
            self._thread.join(timeout=1.5)


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


# ---------------------------------------------------------------- grabador

class Recorder:
    """Graba eventos globales de ratón y teclado con marca de tiempo.

    Dos formas de guardar el movimiento del ratón:

      · **absoluta** ('mm', x/y): dónde estaba el puntero. Es lo que vale para
        menús, escritorio y ventanas normales.
      · **relativa** ('mr', dx/dy): cuánto se ha desplazado el ratón, leído por
        raw input. Es la única que sirve en un juego en primera persona con el
        ratón preso, donde el cursor no se mueve y el juego solo mira los
        desplazamientos. Y se graba tal cual llega del ratón, sin pasar por la
        aceleración del puntero de Windows, así que al reproducirlo el juego
        recibe exactamente los mismos números.
    """

    MOVE_MIN_INTERVAL = 0.008  # ~125 muestras/s de movimiento: fluido y ligero

    def __init__(self):
        self.events = []
        self.recording = False
        self.relative = False       # modo para juegos en primera persona
        self.raw_error = None
        self.ancla = None           # trozo de la vista al empezar a grabar
        self.ancla_pos = None
        self.ancla_sirve = True     # ¿es reconocible esa vista?
        self.ancla_aviso = ""
        self._t0 = 0.0
        self._last_move_t = 0.0
        self._m_listener = None
        self._k_listener = None
        self._raw = None
        self.lock = threading.Lock()

    def start(self):
        """Prepara todo ANTES de arrancar el reloj.

        La foto de la vista y el registro del raw input tardan sus decenas de
        milisegundos. Si el reloj arrancara antes, ese rato contaría como tiempo
        grabado sin que se estuviera escuchando nada, y se perderían los primeros
        movimientos. Así que primero se monta todo y lo último es abrir la puerta.
        """
        self.raw_error = None
        self._raw = None
        self.ancla = None
        self.ancla_pos = None
        self.ancla_aviso = ""
        self.recording = False
        if self.relative:
            # una foto de hacia dónde se está mirando, para poder recolocar la
            # cámara ahí antes de reproducir
            try:
                self.ancla, self.ancla_pos = capturar_ancla()
                sirve, motivo = evaluar_ancla(self.ancla, self.ancla_pos)
                self.ancla_aviso = motivo
                if not sirve:
                    self.ancla_sirve = False
                else:
                    self.ancla_sirve = True
            except Exception:
                self.ancla, self.ancla_pos = None, None
                self.ancla_sirve = False
            self._raw = RawMouseListener(self._on_raw_move)
            if not self._raw.start():
                self.raw_error = self._raw.error or "motivo desconocido"
                self._raw = None
        # en modo relativo se sigue escuchando a pynput para clics, rueda y
        # teclado; solo el movimiento viene por raw input
        self._m_listener = mouse.Listener(
            on_move=self._on_move, on_click=self._on_click,
            on_scroll=self._on_scroll)
        self._k_listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release)
        self._m_listener.start()
        self._k_listener.start()
        with self.lock:
            self.events = []
            self._t0 = time.perf_counter()
            self._last_move_t = -1.0
            self.recording = True      # ya se está escuchando: ahora sí

    def stop(self):
        self.recording = False
        if self._raw:
            self._raw.stop()
            self._raw = None
        if self._m_listener:
            self._m_listener.stop()
            self._m_listener = None
        if self._k_listener:
            self._k_listener.stop()
            self._k_listener = None
        return list(self.events)

    def _now(self):
        return time.perf_counter() - self._t0

    def _on_raw_move(self, dx, dy):
        """Un informe del ratón: se guarda el delta sin tocar ni agrupar."""
        if not self.recording:
            return
        self.events.append({"t": self._now(), "e": "mr", "dx": int(dx),
                            "dy": int(dy)})

    def _on_move(self, x, y):
        if not self.recording or self.relative:
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
        self.relative = False
        self._thread = None
        self.on_finish = on_finish
        self.on_progress = on_progress

    @staticmethod
    def es_relativa(events):
        """¿Esta macro guarda el movimiento en relativo?

        Se deduce de los propios eventos en vez de fiarse de una bandera del
        archivo, para que una macro vieja siga funcionando igual.
        """
        return any(ev.get("e") == "mr" for ev in events)

    def play(self, events, speed=1.0, repeats=1):
        if self.playing or not events:
            return
        self.relative = self.es_relativa(events)
        self.playing = True
        self._thread = threading.Thread(
            target=self._run, args=(events, speed, repeats), daemon=True)
        self._thread.start()

    def stop(self):
        self.playing = False

    def play_sync(self, events, speed=1.0, repeats=1):
        """Reproduce en el hilo actual y no vuelve hasta acabar.

        La usa el guion, que necesita que un paso 'macro' termine antes de
        pasar al siguiente.
        """
        if not events:
            return
        self.relative = self.es_relativa(events)
        self.playing = True
        self._run(events, speed, repeats)

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
        if e == "mr":
            # desplazamiento relativo: lo único que entiende un juego en
            # primera persona con el ratón preso
            move_relative(ev["dx"], ev["dy"])
        elif e == "mm":
            self.mouse.position = (ev["x"], ev["y"])
        elif e == "mc":
            # en una macro relativa el cursor del sistema no pinta nada: el
            # clic va donde apunte la mira, y recolocarlo rompería la cámara
            if not self.relative:
                self.mouse.position = (ev["x"], ev["y"])
            btn = getattr(Button, ev["b"])
            if ev["d"]:
                self.mouse.press(btn)
            else:
                self.mouse.release(btn)
        elif e == "ms":
            if not self.relative:
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


# ------------------------------------------------------------------- guion

ALIAS_TECLA = {
    "esc": "esc", "escape": "esc", "intro": "enter", "enter": "enter",
    "espacio": "space", "space": "space", "tab": "tab", "supr": "delete",
    "borrar": "backspace", "mayus": "shift", "shift": "shift",
    "ctrl": "ctrl", "control": "ctrl", "alt": "alt",
    "arriba": "up", "abajo": "down", "izquierda": "left", "derecha": "right",
    "inicio": "home", "fin": "end",
}


def resolver_tecla(nombre):
    """Nombre escrito en el guion -> tecla de pynput. Lanza ValueError."""
    n = nombre.strip().lower()
    if len(n) == 1:
        return n
    n = ALIAS_TECLA.get(n, n)
    try:
        return getattr(keyboard.Key, n)
    except AttributeError:
        raise ValueError(f"no conozco la tecla '{nombre}'")


def resolver_combo(texto):
    """'shift+1' -> [Key.shift, '1']. Una sola tecla también vale.

    Las de delante se quedan pulsadas mientras se pulsa y suelta la última, y
    se sueltan al revés de como se pulsaron, que es como funciona un atajo de
    teclado de verdad.
    """
    partes = [p for p in texto.replace("++", "+plus").split("+") if p != ""]
    if not partes:
        raise ValueError(f"'{texto}' no es ninguna tecla")
    if len(partes) > 4:
        raise ValueError(f"'{texto}' junta demasiadas teclas")
    return [resolver_tecla("+" if p == "plus" else p) for p in partes]


def turn_camera(dx, dy, paso_max=15, gap=0.008, espera=None):
    """Gira la cámara repartiendo el desplazamiento en varios envíos.

    Un solo salto grande no vale: muchos juegos limitan cuánto puede girar la
    vista en un fotograma, y el resto se pierde. Se reparte como lo haría un
    ratón de verdad (unos 125 envíos por segundo).

    El total sumado es **exactamente** el pedido: cada envío se calcula contra
    el objetivo acumulado, así que los restos del redondeo se arrastran en vez
    de perderse. Devuelve lo realmente enviado.

    'espera' es la función de espera cortable del guion; si devuelve False se
    interrumpe el giro a medias.
    """
    dx, dy = int(dx), int(dy)
    if dx == 0 and dy == 0:
        return 0, 0
    n = max(1, math.ceil(max(abs(dx), abs(dy)) / max(1, paso_max)))
    hecho_x = hecho_y = 0
    for i in range(1, n + 1):
        obj_x = round(dx * i / n)
        obj_y = round(dy * i / n)
        move_relative(obj_x - hecho_x, obj_y - hecho_y)
        hecho_x, hecho_y = obj_x, obj_y
        if i < n:
            if espera is not None:
                if not espera(gap):
                    break
            else:
                time.sleep(gap)
    return hecho_x, hecho_y


# ------------------------------------------- ancla de la cámara (1ª persona)
#
# El movimiento relativo no sabe hacia dónde apunta la cámara: solo cuánto se
# mueve. Si al reproducir la vista no arranca donde arrancó al grabar, TODA la
# grabación va desviada desde el primer segundo, y en 20 minutos eso acaba en
# cualquier parte. Como el juego no dice hacia dónde mira, se guarda un trozo de
# la propia vista al empezar a grabar: eso sí se puede reconocer después, y con
# ello se recoloca la cámara antes de reproducir.

ANCLA_FRAC = 0.30      # lado del recorte, en fracción de pantalla


def capturar_ancla():
    """Recorta el centro de la vista. Devuelve (imagen, (x, y) donde estaba)."""
    bgr, _mon = Finder.grab_screen()
    h, w = bgr.shape[:2]
    lw, lh = max(32, int(w * ANCLA_FRAC)), max(32, int(h * ANCLA_FRAC))
    x0, y0 = (w - lw) // 2, (h - lh) // 2
    return np.ascontiguousarray(bgr[y0:y0 + lh, x0:x0 + lw]), (x0, y0)


def ancla_a_texto(img):
    ok, buf = cv2.imencode(".png", img)
    return base64.b64encode(buf.tobytes()).decode("ascii") if ok else None


def texto_a_ancla(txt):
    datos = np.frombuffer(base64.b64decode(txt), np.uint8)
    img = cv2.imdecode(datos, cv2.IMREAD_COLOR)
    if img is None or img.size == 0:
        raise ValueError("el ancla guardada no se puede leer")
    return img


def evaluar_ancla(ancla, pos):
    """¿Sirve esta vista como referencia? -> (sirve, explicación).

    Dos formas de que no sirva, y las dos se pueden ver en el momento, que es
    cuando aún se arregla mirando a otro lado:

      · **lisa**: el cielo, una pared de un color. Sin detalle no hay nada que
        reconocer.
      · **repetitiva**: una pared de ladrillos iguales, un suelo de bloques. Se
        parece a sí misma en muchos sitios, así que reconocerla no dice dónde
        está. Se detecta buscándola en la propia pantalla e ignorando el punto
        donde de verdad está: si aun así aparece otro sitio casi igual de
        parecido, es ambigua.
    """
    gris = cv2.cvtColor(ancla, cv2.COLOR_BGR2GRAY)
    contraste = float(np.std(gris))
    if contraste < 12:
        return False, (f"la vista es demasiado lisa (contraste {contraste:.1f}); "
                       f"no habrá nada que reconocer")
    bgr, _mon = Finder.grab_screen()
    if bgr.shape[0] < ancla.shape[0] or bgr.shape[1] < ancla.shape[1]:
        return True, f"contraste {contraste:.1f}"
    res = cv2.matchTemplate(bgr, ancla, cv2.TM_CCOEFF_NORMED)
    ah, aw = ancla.shape[:2]
    # tapar el sitio donde de verdad está, para ver con qué más se confunde
    x0 = max(0, pos[0] - aw // 3)
    y0 = max(0, pos[1] - ah // 3)
    res[y0:pos[1] + ah // 3, x0:pos[0] + aw // 3] = -1.0
    segundo = float(res.max()) if res.size else -1.0
    if segundo > 0.80:
        return False, (f"la vista se repite: hay otro sitio en la propia "
                       f"pantalla que se le parece un {segundo:.2f}. Mira hacia "
                       f"algo más distintivo")
    return True, (f"contraste {contraste:.1f}, y lo más parecido en otro sitio "
                  f"de la pantalla es {max(0.0, segundo):.2f}")


def localizar_ancla(ancla):
    """¿Dónde está ahora ese trozo de vista? -> ((x, y), parecido 0-1)."""
    bgr, _mon = Finder.grab_screen()
    if bgr.shape[0] < ancla.shape[0] or bgr.shape[1] < ancla.shape[1]:
        return None, 0.0
    res = cv2.matchTemplate(bgr, ancla, cv2.TM_CCOEFF_NORMED)
    _mn, mx, _ml, loc = cv2.minMaxLoc(res)
    return (int(loc[0]), int(loc[1])), float(mx)


def _calibrar_con_la_vista(mover, espera, calib=60):
    """Mide unidades de ratón por píxel usando la vista de ahora, sin ancla.

    Hace falta para poder barrer: sin saber cuánto gira la cámara por unidad de
    ratón no se puede dar un paso de "media pantalla", y sin eso el barrido es a
    ciegas. Se usa un trozo de la vista actual como referencia temporal, que
    para esto vale igual que el ancla.
    """
    def parche_actual():
        bgr, _mon = Finder.grab_screen()
        h, w = bgr.shape[:2]
        lw, lh = max(48, int(w * 0.25)), max(48, int(h * 0.25))
        x0, y0 = (w - lw) // 2, (h - lh) // 2
        return np.ascontiguousarray(bgr[y0:y0 + lh, x0:x0 + lw]), (x0, y0), (w, h)

    parche, pos, (w, h) = parche_actual()
    factores = {}
    for eje in ("x", "y"):
        logrado = False
        ultimo = "no lo he podido medir"
        # Se prueban los dos sentidos: si el personaje está mirando al suelo del
        # todo, el juego no le deja bajar más y girar hacia abajo mide 0. Eso no
        # es que el ratón no esté capturado, es que se ha topado con el límite.
        for signo in (1, -1):
            d = calib * signo
            mover(d if eje == "x" else 0, 0 if eje == "x" else d)
            time.sleep(espera)
            loc, score = localizar_ancla(parche)
            if loc is not None and score >= 0.5:
                desp = (loc[0] - pos[0]) if eje == "x" else (loc[1] - pos[1])
                if abs(desp) >= 3:
                    factores[eje] = d / float(desp)
                    logrado = True
                    parche, pos, _ = parche_actual()
                    break
                ultimo = (f"he girado {abs(d)} y la vista no se ha movido "
                          f"({desp} px en {eje})")
            else:
                ultimo = (f"al girar en {eje} he perdido la referencia "
                          f"(parecido {score:.2f})")
            parche, pos, _ = parche_actual()   # referencia nueva para reintentar
        if not logrado:
            return None, None, (f"no consigo medir cuánto gira la cámara: "
                                f"{ultimo}. ¿Está el juego delante y con el "
                                f"ratón capturado?")
    return factores, (w, h), ""


def buscar_ancla_girando(ancla, parecido_min, mover, espera, log,
                         pasos=10, paso_frac=0.55,
                         niveles=(-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5),
                         calib=60):
    """Gira la cámara buscando la vista de la grabación. -> (loc, score, error).

    Barre en horizontal a varias alturas, porque si el personaje está mirando al
    suelo o al cielo por mucho que gire no va a ver nunca la referencia.

    Las dos cosas que hacen que no se cuele el ancla por un hueco:

      · en horizontal cada paso avanza algo más de media pantalla, y con 10
        pasos se da algo más de una vuelta completa (con un campo de visión
        normal, una vuelta son unas cinco pantallas);
      · en vertical las alturas van de media en media pantalla, porque el ancla
        solo mide un 30% de alto: si fueran más separadas podría quedarse justo
        entre dos.

    Y las alturas se recorren de abajo arriba, sin ir y venir: los saltos son
    más pequeños y no se lía con el tope de mirar al cielo o al suelo.
    """
    factores, medidas, err = _calibrar_con_la_vista(mover, espera, calib)
    if factores is None:
        return None, 0.0, err
    w, h = medidas
    paso_x = int(round(w * paso_frac * factores["x"]))
    total = pasos * len(niveles)
    log(f"   barriendo: {pasos} pasos de ~{paso_frac:.0%} de pantalla en "
        f"{len(niveles)} alturas ({total} comprobaciones, unos "
        f"{total * (espera + 0.08):.0f} s). Girando…")

    # Si el ancla asoma medio fuera de la pantalla, matchTemplate la "encuentra"
    # pegada al borde y devuelve una posición falsa. Se exige que quepa entera
    # con margen: el siguiente paso del barrido la traerá hacia dentro.
    margen = 8
    ah, aw = ancla.shape[:2]

    def entera(loc):
        return (loc is not None and margen <= loc[0] <= w - aw - margen
                and margen <= loc[1] <= h - ah - margen)

    mejor = (None, 0.0)
    altura_actual = 0.0
    for nivel in niveles:
        # colocarse en esa altura (relativa a la de partida)
        delta = nivel - altura_actual
        if delta:
            mover(0, int(round(h * delta * factores["y"])))
            altura_actual = nivel
            time.sleep(espera)
        for i in range(pasos):
            loc, score = localizar_ancla(ancla)
            if score > mejor[1]:
                mejor = (loc, score)
            if score >= parecido_min and entera(loc):
                log(f"   encontrada girando: parecido {score:.2f} "
                    f"(altura {nivel:+.2f}, paso {i})")
                return loc, score, factores
            mover(paso_x, 0)
            time.sleep(espera)
    return None, mejor[1], (f"he dado la vuelta mirando en {len(niveles)} "
                            f"alturas y no he "
                            f"reconocido la vista en ningún sitio (lo más "
                            f"parecido fue {mejor[1]:.2f}, hace falta "
                            f"{parecido_min:.2f}). ¿Es el mismo mundo y el mismo "
                            f"sitio donde grabaste?")


def alinear_camara(ancla, destino, parecido_min=0.55, tolerancia=4,
                   intentos=8, calib=60, espera=0.18, mover=None, log=None,
                   buscar=True, espera_barrido=0.12):
    """Gira la cámara hasta que la vista vuelva a estar como al grabar.

    Bucle cerrado: se busca el trozo de referencia, se mira cuántos píxeles está
    desviado y se corrige. La relación entre píxeles y unidades de ratón se mide
    sola, inyectando un giro conocido y viendo cuánto se desplaza la vista:
    depende de la sensibilidad que tenga puesta el juego y del campo de visión,
    así que no se puede suponer. También se aprende el signo de esa forma, en vez
    de dar por hecho hacia dónde gira la cámara al mover el ratón.

    Devuelve (alineada, explicación).
    """
    mover = mover or (lambda dx, dy: turn_camera(dx, dy))
    apunta = log or (lambda _m: None)

    def mirar():
        time.sleep(espera)       # que el juego pinte el fotograma nuevo
        return localizar_ancla(ancla)

    ya_calibrado = None
    loc, score = mirar()
    if loc is None or score < parecido_min:
        if not buscar:
            return False, (f"no reconozco la vista (parecido {score:.2f}, hace "
                           f"falta {parecido_min:.2f}). Ponte más o menos donde "
                           f"estabas al grabar y vuelve a intentarlo.")
        apunta(f"   no reconozco la vista de entrada (parecido {score:.2f}); "
               f"la busco girando.")
        # durante el barrido basta con una espera más corta: solo hace falta
        # reconocerla a grandes rasgos, y el ajuste fino ya espera lo suyo
        loc, score, extra = buscar_ancla_girando(
            ancla, parecido_min, mover, min(espera, espera_barrido), apunta,
            calib=calib)
        if loc is None:
            return False, extra
        # el barrido ya midió la relación píxeles/ratón: se reutiliza en vez de
        # volver a girar para calibrar, que es cuando se puede perder el ancla
        ya_calibrado = extra if isinstance(extra, dict) else None

    # --- medir cuántas unidades de ratón hace falta por píxel, y en qué signo
    if ya_calibrado:
        factores = ya_calibrado
        apunta(f"   reutilizo la medida del barrido: {factores['x']:+.2f} y "
               f"{factores['y']:+.2f} unidades por px")
    else:
        # una sola implementación de la calibración, la misma que usa el barrido
        factores, _medidas, err = _calibrar_con_la_vista(mover, espera, calib)
        if factores is None:
            return False, err
        apunta(f"   calibrado: {factores['x']:+.2f} y {factores['y']:+.2f} "
               f"unidades por px")
        loc, score = mirar()
        if loc is None or score < parecido_min:
            return False, (f"al calibrar he perdido de vista la referencia "
                           f"(parecido {score:.2f}).")

    # --- corregir hasta que quede dentro de la tolerancia
    for intento in range(1, intentos + 1):
        err_x = destino[0] - loc[0]
        err_y = destino[1] - loc[1]
        if abs(err_x) <= tolerancia and abs(err_y) <= tolerancia:
            return True, (f"cámara alineada: sobran {err_x:+d}, {err_y:+d} px "
                          f"(tolerancia {tolerancia}) en {intento - 1} "
                          f"corrección(es)")
        mover(int(round(err_x * factores["x"])),
              int(round(err_y * factores["y"])))
        loc, score = mirar()
        if loc is None or score < parecido_min:
            return False, (f"he perdido la referencia al corregir "
                           f"(parecido {score:.2f})")
        apunta(f"   corrección {intento}: quedan "
               f"{destino[0] - loc[0]:+d}, {destino[1] - loc[1]:+d} px")
    return False, (f"no consigo alinearla: tras {intentos} intentos quedan "
                   f"{destino[0] - loc[0]:+d}, {destino[1] - loc[1]:+d} px de "
                   f"desvío. Colócate a mano más cerca y repite.")


class ZoneChange:
    """Mide cuánto cambia la zona de un objetivo entre fotogramas.

    Es el sensor para lo que aparece y desaparece en un instante, donde buscar
    un color no llega: no hace falta saber de qué color es ni qué forma tiene,
    solo que ahí ha pasado algo. Trabaja en gris y a 96x54, así que un escaneo
    cuesta muy poco y se puede repetir muchas veces por segundo.
    """

    def __init__(self, finder, umbral_min=1.0, factor=3.0, memoria=20,
                 minimo_muestras=4):
        self.finder = finder
        # Antes de tener fondo medido no se puede juzgar: en una zona con algo
        # animado, el primer dato compararía contra el suelo y dispararía al
        # instante. Con 4 muestras a 20 por segundo son 0.2 s de calentamiento.
        self.minimo_muestras = minimo_muestras
        # Un umbral fijo no sirve: el cambio medio se diluye con el tamaño de la
        # zona (un objeto pequeño que aparece en una zona grande mueve la media
        # muy poco) y sube si la zona tiene algo animado de fondo. Así que se
        # compara con lo que venía habiendo, más un suelo absoluto.
        self.umbral_min = umbral_min
        self.factor = factor
        self.memoria = memoria
        self.reset()

    def reset(self):
        self.prev = None
        self.fondo = []

    def umbral(self):
        if not self.fondo:
            return self.umbral_min
        return max(self.umbral_min, float(np.median(self.fondo)) * self.factor)

    def paso(self):
        """Devuelve (disparo, cambio, umbral). El 1er fotograma no dispara."""
        bgr, _mon = self.finder.grab_screen()
        band, _x, _y = self.finder._band(bgr)
        if band.size == 0:
            band = bgr
        peq = cv2.resize(band, (96, 54), interpolation=cv2.INTER_AREA)
        g = cv2.cvtColor(peq, cv2.COLOR_BGR2GRAY).astype(np.int16)
        if self.prev is None:
            self.prev = g
            return False, None, self.umbral()
        cambio = float(np.mean(np.abs(g - self.prev)))
        self.prev = g
        umbral = self.umbral()          # con el fondo de antes de este dato
        listo = len(self.fondo) >= self.minimo_muestras
        self.fondo.append(cambio)
        if len(self.fondo) > self.memoria:
            self.fondo.pop(0)
        return (listo and cambio >= umbral), cambio, umbral


# El audio es opcional: si falla la librería, todo lo demás sigue funcionando y
# solo se pierden las instrucciones de sonido.
try:
    import soundcard as _sc
except Exception:
    _sc = None


class SoundWatch:
    """Escucha lo que sale por los altavoces y mide el nivel.

    Para un aviso que suena siempre igual —el picado de la caña, una campana—
    el oído es más fiable que la vista: no depende de dónde mires ni de que se
    vea bien. Se captura por 'loopback' del altavoz, así que oye el juego sin
    micrófono y sin tocar nada del sistema.

    Guarda el nivel (RMS) de cada bloque en una ventana corta, y da por 'golpe'
    cuando el nivel de ahora se sale claramente de lo que venía habiendo: así el
    umbral se ajusta al volumen que tengas puesto.
    """

    def __init__(self, factor=3.0, minimo=0.01, memoria=40):
        self.factor = factor        # cuántas veces por encima del fondo
        self.minimo = minimo        # nivel mínimo para no disparar con ruido
        self.memoria = memoria      # bloques de fondo que se recuerdan
        self.nivel = 0.0
        self.fondo = 0.0
        self.error = None
        self.activo = False
        self._hist = []
        self._thread = None
        self._stop = threading.Event()
        self._golpe = threading.Event()

    @staticmethod
    def disponible():
        return _sc is not None

    def start(self, timeout=3.0):
        if _sc is None:
            self.error = ("no está la librería de audio (soundcard); "
                          "reinstala el programa")
            return False
        self._stop.clear()
        self._golpe.clear()
        self.error = None
        self._hist = []
        listo = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(listo,),
                                        daemon=True)
        self._thread.start()
        listo.wait(timeout)
        return self.activo

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self.activo = False

    def _run(self, listo):
        try:
            alt = _sc.default_speaker()
            mic = _sc.get_microphone(alt.name, include_loopback=True)
            with mic.recorder(samplerate=44100, channels=2,
                              blocksize=1024) as rec:
                self.activo = True
                listo.set()
                while not self._stop.is_set():
                    datos = np.asarray(rec.record(numframes=2048))
                    if datos.size == 0:
                        continue
                    mono = datos.mean(axis=1)
                    self.nivel = float(np.sqrt(np.mean(mono ** 2)))
                    if self._hist:
                        self.fondo = float(np.median(self._hist))
                        if (self.nivel > self.minimo
                                and self.nivel > self.fondo * self.factor):
                            self._golpe.set()
                    self._hist.append(self.nivel)
                    if len(self._hist) > self.memoria:
                        self._hist.pop(0)
        except Exception as exc:
            self.error = exc
            listo.set()
        finally:
            self.activo = False

    def limpiar(self):
        self._golpe.clear()

    def hubo_golpe(self):
        return self._golpe.is_set()


class StuckDetector:
    """Detecta que la vista ha dejado de cambiar mientras se ordena avanzar.

    No sabe nada del juego: solo compara fotogramas seguidos. Si se está
    mandando andar y la imagen apenas cambia, o hay una pared delante o el
    personaje se ha quedado colgado en algo.

    El umbral no puede ser fijo, porque cuánto cambia la pantalla al andar
    depende del juego, del campo de visión y de la resolución. Así que se mide
    solo: se toma como referencia el percentil 75 de los cambios vistos —el
    nivel típico de "moviéndose", que aguanta aunque una parte del rato esté
    atascado— y se considera atasco cuando el cambio baja de una fracción de
    esa referencia. Hay además un suelo absoluto, para el caso de llegar ya
    atascado y no tener nunca una referencia alta.

    Se mira solo la parte central de la pantalla: fuera quedan la barra de
    objetos, el chat y el objeto de la mano, que se mueven por su cuenta y
    ensuciarían la medida.
    """

    REGION = (0.20, 0.15, 0.80, 0.70)   # izq, arriba, der, abajo (fracciones)

    def __init__(self, umbral_min=1.5, fraccion=0.25, seguidos=3,
                 minimo_muestras=4):
        self.umbral_min = umbral_min
        self.fraccion = fraccion
        self.seguidos = seguidos
        self.minimo_muestras = minimo_muestras
        self.reset()

    def reset(self):
        self._prev = None
        self.cambios = []
        self.quietos = 0

    @staticmethod
    def _muestra():
        bgr, _mon = Finder.grab_screen()
        h, w = bgr.shape[:2]
        l, t, r, b = StuckDetector.REGION
        corte = bgr[int(h * t):int(h * b), int(w * l):int(w * r)]
        if corte.size == 0:
            corte = bgr
        peq = cv2.resize(corte, (160, 90), interpolation=cv2.INTER_AREA)
        return cv2.cvtColor(peq, cv2.COLOR_BGR2GRAY).astype(np.int16)

    def umbral(self):
        """El nivel por debajo del cual se considera que no pasa nada."""
        if len(self.cambios) < self.minimo_muestras:
            return self.umbral_min
        base = float(np.percentile(self.cambios, 75))
        return max(self.umbral_min, base * self.fraccion)

    def paso(self):
        """Toma un fotograma. Devuelve (atascado, cambio, umbral)."""
        g = self._muestra()
        if self._prev is None:
            self._prev = g
            return False, None, self.umbral()
        cambio = float(np.mean(np.abs(g - self._prev)))
        self._prev = g
        umbral = self.umbral()          # con el historial de antes de este dato
        self.cambios.append(cambio)
        if cambio < umbral:
            self.quietos += 1
        else:
            self.quietos = 0
        return self.quietos >= self.seguidos, cambio, umbral


def type_text(kb, texto, tecla_antes=None, intro=True, delay_char=0.02,
              delay_ui=0.30):
    """Teclea un texto como lo haría una persona, no de un volcado.

    Dos pausas que hacen falta en un juego:

      · tras abrir el chat con una tecla (la T en Minecraft) hay que darle un
        momento a que aparezca; si se empieza a teclear en el mismo fotograma,
        las primeras letras se pierden;
      · carácter a carácter, porque volcando la cadena de golpe un juego a 60
        fps se salta letras.
    """
    if tecla_antes:
        k = resolver_tecla(tecla_antes)
        kb.press(k)
        time.sleep(0.05)
        kb.release(k)
        time.sleep(delay_ui)
    for ch in texto:
        kb.type(ch)
        time.sleep(delay_char)
    if intro:
        time.sleep(0.15)
        kb.press(keyboard.Key.enter)
        time.sleep(0.05)
        kb.release(keyboard.Key.enter)


class Script:
    """Guion de varios pasos: busca, clica, espera, salta, repite.

    El vigilante solo sabe "veo esto -> clico esto". Esto encadena pasos y
    permite volver atrás, que es lo que hace falta para una tarea con estados
    (esperar algo, actuar, comprobar el resultado, volver a empezar).

    Cada paso que busca algo se refiere a un *objetivo* con nombre, que es una
    copia guardada de los ajustes de búsqueda (zona incluida). Así un mismo
    guion puede mirar sitios distintos con criterios distintos.
    """

    POLITICAS = ("parar", "seguir", "repetir", "ir")

    def __init__(self, log_fn, targets, status_fn=None, on_finish=None):
        self.log = log_fn
        self.targets = targets          # nombre -> perfil del Finder
        self.status = status_fn
        self.on_finish = on_finish
        self.finder = Finder()
        self.mouse = MouseController()
        self.keyboard = KeyboardController()
        self.steps = []
        self.interval = 0.5             # s entre comprobaciones de 'buscar'
        self.confirmaciones = 2         # escaneos seguidos antes de dar por visto
        self.move_delay = 0.20
        self.restore_mouse = True
        self.sound = True
        self.running = False
        self._stop = threading.Event()
        self._thread = None
        self.last_pos = None
        self.pasos_hechos = 0
        # lo que se deja pulsado hay que soltarlo al acabar, pase lo que pase:
        # si no, un F12 durante un 'mantener w 30' deja la W enganchada y el
        # personaje sigue andando solo
        self._teclas = set()
        self._botones = set()
        self.stuck = StuckDetector()
        self.stuck_intervalo = 0.35     # s entre fotogramas al vigilar
        self.ventana_req = ""           # solo actuar si el título la contiene
        self.cambio_intervalo = 0.05    # s entre fotogramas: ~20 por segundo
        self.sonido = None              # SoundWatch, solo si se usa

    # ---------- análisis ----------
    @classmethod
    def _politica(cls, tokens, nlin, palabra):
        """Analiza '<palabra> parar|seguir|repetir|ir <nº>'.

        Devuelve (politica, error). La usan 'si_falla' de buscar y
        'si_atascado' de mantener, para que se escriban igual las dos.
        """
        if tokens[0].lower() != palabra:
            return None, f"línea {nlin}: no entiendo '{' '.join(tokens)}'"
        if len(tokens) < 2 or tokens[1].lower() not in cls.POLITICAS:
            return None, (f"línea {nlin}: tras '{palabra}' pon parar, seguir, "
                          f"repetir o 'ir <nº>'")
        if tokens[1].lower() == "ir":
            if len(tokens) < 3 or not tokens[2].isdigit():
                return None, (f"línea {nlin}: '{palabra} ir' necesita un "
                              f"número de paso")
            if len(tokens) > 3:
                return None, (f"línea {nlin}: no entiendo "
                              f"'{' '.join(tokens[3:])}'")
            return ("ir", int(tokens[2])), None
        if len(tokens) > 2:
            return None, f"línea {nlin}: no entiendo '{' '.join(tokens[2:])}'"
        return (tokens[1].lower(), None), None

    @classmethod
    def parse(cls, texto, targets):
        """Devuelve (pasos, errores). Cada error es un texto ya legible."""
        pasos, errores = [], []
        for nlin, cruda in enumerate(texto.splitlines(), 1):
            linea = cruda.strip()
            if not linea or linea.startswith("#"):
                continue
            op = linea.split()[0].lower()

            if op == "escribir":
                # el texto va literal, sin tocar: puede llevar # y espacios
                resto = linea[len("escribir"):].strip()
                if not resto:
                    errores.append(f"línea {nlin}: 'escribir' sin texto")
                    continue
                pasos.append({"op": "escribir", "texto": resto, "lin": nlin,
                              "raw": linea})
                continue

            partes = linea.split("#")[0].split()
            if not partes:
                continue
            op = partes[0].lower()
            args = partes[1:]
            p = {"op": op, "lin": nlin, "raw": " ".join(partes)}

            if op in ("buscar", "desaparecer"):
                if not args:
                    errores.append(f"línea {nlin}: '{op}' necesita el nombre "
                                   f"de un objetivo")
                    continue
                p["objetivo"] = args[0]
                if args[0] not in targets:
                    disp = ", ".join(sorted(targets)) or "ninguno todavía"
                    errores.append(f"línea {nlin}: no hay un objetivo llamado "
                                   f"'{args[0]}' (guardados: {disp})")
                p["timeout"] = None
                p["politica"] = ("parar", None)
                rest = args[1:]
                if rest and rest[0].lower() != "si_falla":
                    try:
                        p["timeout"] = float(rest[0].replace(",", "."))
                        if p["timeout"] <= 0:
                            raise ValueError
                    except ValueError:
                        errores.append(f"línea {nlin}: '{rest[0]}' no es un "
                                       f"número de segundos")
                    rest = rest[1:]
                if rest:
                    pol, err = cls._politica(rest, nlin, "si_falla")
                    if err:
                        errores.append(err)
                    else:
                        p["politica"] = pol
                    if p["timeout"] is None and p["politica"][0] != "parar":
                        errores.append(f"línea {nlin}: 'si_falla' no sirve sin "
                                       f"un límite de segundos, porque sin él "
                                       f"espera para siempre")

            elif op in ("esperar_cambio", "esperar_sonido"):
                p["objetivo"] = None
                idx = 0
                if op == "esperar_cambio":
                    if not args:
                        errores.append(f"línea {nlin}: 'esperar_cambio' "
                                       f"necesita el objetivo cuya zona hay "
                                       f"que mirar")
                        continue
                    p["objetivo"] = args[0]
                    if args[0] not in targets:
                        disp = ", ".join(sorted(targets)) or "ninguno todavía"
                        errores.append(f"línea {nlin}: no hay un objetivo "
                                       f"llamado '{args[0]}' "
                                       f"(guardados: {disp})")
                    idx = 1
                resto = list(args[idx:])
                p["timeout"] = None
                p["politica"] = ("parar", None)
                if resto and resto[0].lower() != "si_falla":
                    try:
                        p["timeout"] = float(resto.pop(0).replace(",", "."))
                        if p["timeout"] <= 0:
                            raise ValueError
                    except ValueError:
                        errores.append(f"línea {nlin}: tras '{op}' van los "
                                       f"segundos que como mucho va a esperar")
                if p["timeout"] is None:
                    errores.append(f"línea {nlin}: '{op}' necesita un límite de "
                                   f"segundos, que si no se queda esperando "
                                   f"para siempre")
                if resto:
                    pol, err = cls._politica(resto, nlin, "si_falla")
                    if err:
                        errores.append(err)
                    else:
                        p["politica"] = pol

            elif op == "clic":
                p["boton"] = "left"
                p["doble"] = False
                for a in args:
                    al = a.lower()
                    if al == "doble":
                        p["doble"] = True
                    elif al == "derecho":
                        p["boton"] = "right"
                    elif al == "medio":
                        p["boton"] = "middle"
                    else:
                        errores.append(f"línea {nlin}: no entiendo '{a}' en "
                                       f"'clic' (usa doble, derecho o medio)")

            elif op == "esperar":
                if len(args) != 1:
                    errores.append(f"línea {nlin}: 'esperar' necesita los "
                                   f"segundos")
                else:
                    try:
                        p["segundos"] = float(args[0].replace(",", "."))
                        if p["segundos"] < 0:
                            raise ValueError
                    except ValueError:
                        errores.append(f"línea {nlin}: '{args[0]}' no es un "
                                       f"número de segundos")

            elif op in ("tecla", "pulsar"):
                p["op"] = "tecla"
                if len(args) != 1:
                    errores.append(f"línea {nlin}: '{op}' necesita una tecla "
                                   f"(o una combinación, como shift+1)")
                else:
                    try:
                        resolver_combo(args[0])
                        p["tecla"] = args[0]
                    except ValueError as exc:
                        errores.append(f"línea {nlin}: {exc}")

            elif op == "girar":
                if len(args) != 2:
                    errores.append(f"línea {nlin}: 'girar' necesita dos "
                                   f"números: cuánto a los lados y cuánto "
                                   f"arriba o abajo (por ejemplo: girar 200 0)")
                else:
                    try:
                        p["dx"] = int(float(args[0].replace(",", ".")))
                        p["dy"] = int(float(args[1].replace(",", ".")))
                    except ValueError:
                        errores.append(f"línea {nlin}: '{args[0]} {args[1]}' no "
                                       f"son dos números")

            elif op in ("mantener", "soltar"):
                if not args:
                    errores.append(f"línea {nlin}: '{op}' necesita una tecla")
                else:
                    try:
                        resolver_tecla(args[0])
                        p["tecla"] = args[0]
                    except ValueError as exc:
                        errores.append(f"línea {nlin}: {exc}")
                    p["segundos"] = None
                    p["atasco"] = None
                    resto = list(args[1:])
                    if op == "soltar" and resto:
                        errores.append(f"línea {nlin}: 'soltar' solo lleva la "
                                       f"tecla")
                        resto = []
                    if resto and resto[0].lower() != "si_atascado":
                        try:
                            p["segundos"] = float(resto.pop(0).replace(",", "."))
                            if p["segundos"] < 0:
                                raise ValueError
                        except ValueError:
                            errores.append(f"línea {nlin}: '{args[1]}' no es un "
                                           f"número de segundos")
                    if resto:
                        pol, err = cls._politica(resto, nlin, "si_atascado")
                        if err:
                            errores.append(err)
                        else:
                            p["atasco"] = pol
                        if p["segundos"] is None:
                            errores.append(f"línea {nlin}: 'si_atascado' "
                                           f"necesita también los segundos que "
                                           f"como mucho va a estar avanzando")

            elif op in ("mantener_clic", "soltar_clic"):
                p["boton"] = "left"
                p["segundos"] = None
                resto = list(args)
                if resto and resto[0].lower() in ("izquierdo", "derecho",
                                                  "medio"):
                    p["boton"] = {"izquierdo": "left", "derecho": "right",
                                  "medio": "middle"}[resto.pop(0).lower()]
                if resto and op == "mantener_clic":
                    try:
                        p["segundos"] = float(resto.pop(0).replace(",", "."))
                        if p["segundos"] < 0:
                            raise ValueError
                    except ValueError:
                        errores.append(f"línea {nlin}: en '{op}', tras el botón "
                                       f"solo van los segundos")
                if resto:
                    errores.append(f"línea {nlin}: no entiendo "
                                   f"'{' '.join(resto)}' en '{op}'")

            elif op == "macro":
                if len(args) != 1:
                    errores.append(f"línea {nlin}: 'macro' necesita el nombre "
                                   f"del archivo .macro.json")
                else:
                    p["archivo"] = args[0]
                    ruta = (args[0] if os.path.isabs(args[0])
                            else os.path.join(APP_DIR, args[0]))
                    if not os.path.exists(ruta):
                        errores.append(f"línea {nlin}: no encuentro "
                                       f"'{ruta}'")
                    p["ruta"] = ruta

            elif op == "ir":
                if len(args) != 1 or not args[0].isdigit():
                    errores.append(f"línea {nlin}: 'ir' necesita un número de "
                                   f"paso")
                else:
                    p["destino"] = int(args[0])

            elif op == "repetir":
                p["veces"] = None       # sin número: para siempre
                if args:
                    if len(args) > 1 or not args[0].isdigit() or args[0] == "0":
                        errores.append(f"línea {nlin}: tras 'repetir' solo va "
                                       f"un número de veces (o nada, para "
                                       f"repetir sin parar)")
                    else:
                        p["veces"] = int(args[0])

            elif op in ("parar", "pitar", "reafirmar"):
                if args:
                    errores.append(f"línea {nlin}: '{op}' no lleva nada detrás")

            else:
                errores.append(f"línea {nlin}: no conozco la instrucción "
                               f"'{op}'")
                continue

            # ¿este paso hace perder tiempo? Es lo que frena un bucle
            p["bloquea"] = (op in ("buscar", "desaparecer", "esperar", "macro",
                                   "esperar_cambio", "esperar_sonido")
                            or (op in ("mantener", "mantener_clic")
                                and p.get("segundos")))
            pasos.append(p)

        if not pasos and not errores:
            errores.append("el guion está vacío")

        # los saltos se comprueban al final, cuando ya sabemos cuántos pasos hay
        for i, p in enumerate(pasos, 1):
            destinos = []
            if p["op"] == "ir":
                destinos.append(p.get("destino"))
            if p["op"] in ("buscar", "desaparecer", "esperar_cambio",
                           "esperar_sonido") and \
                    p.get("politica", ("parar",))[0] == "ir":
                destinos.append(p["politica"][1])
            if p.get("atasco") and p["atasco"][0] == "ir":
                destinos.append(p["atasco"][1])
            for d in destinos:
                if d is not None and not (1 <= d <= len(pasos)):
                    errores.append(f"línea {p['lin']}: el paso {d} no existe "
                                   f"(el guion tiene {len(pasos)})")

        # un 'clic' sin un 'buscar' antes no sabe dónde clicar
        visto_buscar = False
        for p in pasos:
            if p["op"] == "buscar":
                visto_buscar = True
            elif p["op"] == "clic" and not visto_buscar:
                errores.append(f"línea {p['lin']}: 'clic' sin un 'buscar' "
                               f"antes: no sabría dónde clicar")
                break

        # un bucle sin nada que espere se comería la CPU y clicaría sin parar
        hay_bucle = any((p["op"] == "repetir" and p.get("veces") is None) or
                        (p["op"] == "ir" and p.get("destino", 99) <= i)
                        for i, p in enumerate(pasos, 1))
        if hay_bucle and not any(p.get("bloquea") for p in pasos):
            errores.append("el guion se repite pero no espera nada: añade un "
                           "'buscar' o un 'esperar' o se disparará sin freno")
        return pasos, errores

    @staticmethod
    def describe(pasos):
        """Los pasos en palabras, numerados como los ve el motor."""
        out = []
        for i, p in enumerate(pasos, 1):
            op = p["op"]
            if op == "buscar":
                t = (f"hasta {p['timeout']:g} s" if p["timeout"]
                     else "esperando lo que haga falta")
                pol = p["politica"]
                fin = {"parar": "para el guion", "seguir": "sigue igual",
                       "repetir": "vuelve al paso 1",
                       "ir": f"salta al paso {pol[1]}"}[pol[0]]
                txt = (f"espera a ver '{p['objetivo']}' ({t}); "
                       f"si no aparece, {fin}")
            elif op == "desaparecer":
                t = (f"hasta {p['timeout']:g} s" if p["timeout"] else "sin límite")
                txt = f"espera a que '{p['objetivo']}' desaparezca ({t})"
            elif op in ("esperar_cambio", "esperar_sonido"):
                pol = p["politica"]
                fin = {"parar": "para el guion", "seguir": "sigue igual",
                       "repetir": "vuelve al paso 1",
                       "ir": f"salta al paso {pol[1]}"}[pol[0]]
                qué = (f"algo cambie en la zona de '{p['objetivo']}'"
                       if op == "esperar_cambio" else "suene algo")
                txt = (f"espera a que {qué} (hasta {p['timeout']:g} s); "
                       f"si no pasa, {fin}")
            elif op == "clic":
                q = "doble clic" if p["doble"] else "clic"
                b = {"left": "", "right": " derecho", "middle": " central"}[p["boton"]]
                txt = f"{q}{b} donde se vio el último objetivo"
            elif op == "esperar":
                txt = f"espera {p['segundos']:g} s"
            elif op == "tecla":
                txt = (f"pulsa {p['tecla'].replace('+', ' + ')} a la vez"
                       if "+" in p["tecla"].strip("+")
                       else f"pulsa la tecla {p['tecla']}")
            elif op == "girar":
                lados = ("nada" if not p["dx"] else
                         f"{abs(p['dx'])} a la {'derecha' if p['dx'] > 0 else 'izquierda'}")
                vert = ("nada" if not p["dy"] else
                        f"{abs(p['dy'])} {'abajo' if p['dy'] > 0 else 'arriba'}")
                txt = f"gira la cámara: {lados}, {vert}"
            elif op == "mantener":
                if p.get("atasco"):
                    pol, dest = p["atasco"]
                    fin = {"parar": "para el guion", "seguir": "sigue igual",
                           "repetir": "vuelve al paso 1",
                           "ir": f"salta al paso {dest}"}[pol]
                    txt = (f"mantiene {p['tecla']} pulsada hasta "
                           f"{p['segundos']:g} s vigilando la pantalla; si se "
                           f"queda atascado, {fin}")
                else:
                    txt = (f"mantiene {p['tecla']} pulsada {p['segundos']:g} s"
                           if p["segundos"] else
                           f"deja {p['tecla']} pulsada (hasta un 'soltar')")
            elif op == "soltar":
                txt = f"suelta {p['tecla']}"
            elif op == "mantener_clic":
                b = {"left": "izquierdo", "right": "derecho",
                     "middle": "central"}[p["boton"]]
                txt = (f"mantiene el clic {b} {p['segundos']:g} s"
                       if p["segundos"] else
                       f"deja el clic {b} pulsado (hasta un 'soltar_clic')")
            elif op == "soltar_clic":
                b = {"left": "izquierdo", "right": "derecho",
                     "middle": "central"}[p["boton"]]
                txt = f"suelta el clic {b}"
            elif op == "escribir":
                txt = f"escribe «{p['texto']}»"
            elif op == "macro":
                txt = f"reproduce la macro {os.path.basename(p['ruta'])}"
            elif op == "pitar":
                txt = "pita"
            elif op == "reafirmar":
                txt = ("vuelve a pulsar lo que estuviera mantenido (tras "
                       "abrirse y cerrarse una interfaz del juego)")
            elif op == "ir":
                txt = f"salta al paso {p['destino']}"
            elif op == "repetir":
                txt = ("vuelve al paso 1" if p.get("veces") is None
                       else f"vuelve al paso 1, hasta {p['veces']} veces")
            else:
                txt = "para"
            out.append(f"{i}. {txt}")
        return out

    # ---------- ejecución ----------
    def load(self, texto):
        pasos, errores = self.parse(texto, self.targets)
        if errores:
            return errores
        self.steps = pasos
        return []

    def start(self):
        if self.running or not self.steps:
            return False
        self._stop.clear()
        self.running = True
        self.pasos_hechos = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        self.running = False

    def _esperar(self, seg):
        """Como sleep, pero se corta al parar."""
        return not self._stop.wait(max(0.0, seg))

    def _esperar_cambio(self, paso, n):
        """Espera a que la zona del objetivo cambie. -> (ok, detalle)."""
        self.finder.apply(self.targets[paso["objetivo"]])
        det = ZoneChange(self.finder)
        t0 = time.perf_counter()
        mayor = 0.0
        ultimo_umbral = det.umbral()
        while time.perf_counter() - t0 < paso["timeout"]:
            if not self._esperar(self.cambio_intervalo):
                return False, "espera de cambio cortada"
            try:
                disparo, c, umbral = det.paso()
            except Exception as exc:
                return False, f"no pude mirar la zona: {exc}"
            ultimo_umbral = umbral
            if c is None:
                continue
            mayor = max(mayor, c)
            if disparo:
                return True, (f"algo ha cambiado en la zona de "
                              f"'{paso['objetivo']}' (cambio {c:.2f}, hacía "
                              f"falta {umbral:.2f}) tras "
                              f"{time.perf_counter() - t0:.2f} s")
        return False, (f"nada cambió en la zona de '{paso['objetivo']}' en "
                       f"{paso['timeout']:g} s (lo más que cambió fue "
                       f"{mayor:.2f}, hacía falta {ultimo_umbral:.2f})")

    def _esperar_sonido(self, paso, n):
        """Espera un golpe de sonido por los altavoces. -> (ok, detalle)."""
        if self.sonido is None:
            self.sonido = SoundWatch()
        if not self.sonido.activo:
            if not self.sonido.start():
                return False, (f"no pude escuchar el audio "
                               f"({self.sonido.error})")
        self.sonido.limpiar()
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < paso["timeout"]:
            if not self._esperar(0.05):
                return False, "espera de sonido cortada"
            if self.sonido.hubo_golpe():
                return True, (f"he oído algo (nivel {self.sonido.nivel:.4f} "
                              f"sobre un fondo de {self.sonido.fondo:.4f}) tras "
                              f"{time.perf_counter() - t0:.2f} s")
        return False, (f"no oí nada en {paso['timeout']:g} s "
                       f"(nivel {self.sonido.nivel:.4f}, fondo "
                       f"{self.sonido.fondo:.4f})")

    def _avanzar_vigilando(self, paso, n):
        """Mantiene la tecla mirando si la pantalla deja de cambiar.

        Devuelve (atascado, cortado).
        """
        self.stuck.reset()
        self.log(f"{n}. {paso['tecla']} pulsada hasta {paso['segundos']:g} s, "
                 f"vigilando si me quedo atascado")
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < paso["segundos"]:
            if not self._esperar(self.stuck_intervalo):
                return False, True
            try:
                atascado, cambio, umbral = self.stuck.paso()
            except Exception as exc:
                self.log(f"   no pude mirar la pantalla: {exc}")
                return False, False
            if atascado:
                pol = paso["atasco"][0]
                self.log(f"   ATASCADO: la pantalla apenas cambia "
                         f"(cambio {cambio:.2f}, hace falta {umbral:.2f}) "
                         f"tras {time.perf_counter() - t0:.1f} s → {pol}")
                return True, False
        return False, False

    def ventana_ok(self):
        if not self.ventana_req:
            return True
        return self.ventana_req.lower() in ventana_activa().lower()

    def _esperar_ventana(self):
        """Si el juego no está delante, suelta todo y espera a que vuelva.

        Es la diferencia entre pausarse y ponerse a teclear comandos en el
        navegador. Al volver se reafirma lo que estuviera mantenido, porque
        durante la pausa se ha soltado de verdad.
        """
        if self.ventana_ok():
            return True
        teclas, botones = set(self._teclas), set(self._botones)
        sueltos = self._soltar_todo()
        self.log(f"En pausa: delante está «{ventana_activa()}» y espero "
                 f"«{self.ventana_req}»."
                 + (f" Suelto {', '.join(sueltos)}." if sueltos else ""))
        if self.status:
            self.status(f"GUION EN PAUSA — esperando «{self.ventana_req}»")
        while not self._stop.is_set():
            if not self._esperar(0.5):
                return False
            if self.ventana_ok():
                if teclas or botones:
                    self._reafirmar(teclas, botones)
                self.log("Vuelvo: la ventana ya está delante."
                         + (" Recupero lo que estaba mantenido."
                            if teclas or botones else ""))
                return True
        return False

    def _reafirmar(self, teclas, botones):
        """Suelta y vuelve a pulsar lo que debería seguir mantenido.

        Hacen falta las dos cosas, y por dos motivos distintos:

          · un 'clic' del propio guion hace press+release del botón, y si ese
            botón era el que estábamos manteniendo, lo deja suelto;
          · al abrirse una interfaz del juego (el cofre del captcha) el juego se
            olvida de las teclas que tenía pulsadas, pero Windows sigue
            creyéndolas pulsadas, así que al cerrarse no le llega ninguna
            pulsación nueva y el personaje se queda quieto.

        Soltar antes de volver a pulsar es imprescindible: si solo se pulsara,
        para Windows ya estaba pulsada y no habría pulsación nueva que mandar.
        """
        for nombre in teclas:
            try:
                k = resolver_tecla(nombre)
                self.keyboard.release(k)
                time.sleep(0.02)
                self.keyboard.press(k)
                self._teclas.add(nombre)
            except Exception:
                pass
        for b in botones:
            try:
                btn = getattr(Button, b)
                self.mouse.release(btn)
                time.sleep(0.02)
                self.mouse.press(btn)
                self._botones.add(b)
            except Exception:
                pass
        return list(teclas) + [f"clic {b}" for b in botones]

    def _soltar_todo(self):
        """Suelta cualquier tecla o botón que quedara pulsado."""
        sueltos = []
        for nombre in list(self._teclas):
            try:
                self.keyboard.release(resolver_tecla(nombre))
                sueltos.append(nombre)
            except Exception:
                pass
        self._teclas.clear()
        for b in list(self._botones):
            try:
                self.mouse.release(getattr(Button, b))
                sueltos.append(f"clic {b}")
            except Exception:
                pass
        self._botones.clear()
        return sueltos

    def _buscar(self, paso, quiero_verlo):
        """Espera a que el objetivo aparezca (o desaparezca). Devuelve
        (resuelto, candidato)."""
        self.finder.apply(self.targets[paso["objetivo"]])
        t0 = time.perf_counter()
        prev = None
        seguidos = 0
        while not self._stop.is_set():
            try:
                c = self.finder.candidates()
            except Exception as exc:
                self.log(f"   error al buscar '{paso['objetivo']}': {exc}")
                return False, None
            if quiero_verlo:
                if c:
                    x, y = c[0][0], c[0][1]
                    if prev and abs(prev[0] - x) <= 40 and abs(prev[1] - y) <= 40:
                        seguidos += 1
                    else:
                        seguidos = 1
                    prev = (x, y)
                    if seguidos >= max(1, self.confirmaciones):
                        return True, c[0]
                else:
                    prev, seguidos = None, 0
            else:
                if not c:
                    return True, None
            if paso["timeout"] and time.perf_counter() - t0 >= paso["timeout"]:
                return False, None
            if not self._esperar(self.interval):
                return False, None
        return False, None

    def _run(self):
        i = 0
        vueltas = {}          # paso de 'repetir' -> vueltas dadas
        try:
            while not self._stop.is_set() and 0 <= i < len(self.steps):
                if not self._esperar_ventana():
                    break
                paso = self.steps[i]
                op = paso["op"]
                n = i + 1
                if self.status:
                    self.status(f"GUION — paso {n}/{len(self.steps)}: "
                                f"{paso['raw']}")

                if op in ("buscar", "desaparecer"):
                    ok, cand = self._buscar(paso, op == "buscar")
                    if self._stop.is_set():
                        break
                    if ok:
                        if cand:
                            self.last_pos = (cand[0], cand[1])
                            self.log(f"{n}. visto '{paso['objetivo']}' en "
                                     f"({cand[0]}, {cand[1]})")
                        else:
                            self.log(f"{n}. '{paso['objetivo']}' ha "
                                     f"desaparecido")
                        i += 1
                    else:
                        pol, dest = paso["politica"]
                        self.log(f"{n}. no apareció '{paso['objetivo']}' en "
                                 f"{paso['timeout']:g} s → {pol}"
                                 if paso["timeout"] else
                                 f"{n}. búsqueda cortada")
                        if pol == "parar":
                            break
                        i = (0 if pol == "repetir"
                             else dest - 1 if pol == "ir" else i + 1)

                elif op in ("esperar_cambio", "esperar_sonido"):
                    ok, detalle = (self._esperar_cambio(paso, n)
                                   if op == "esperar_cambio"
                                   else self._esperar_sonido(paso, n))
                    if self._stop.is_set():
                        break
                    if ok:
                        self.log(f"{n}. {detalle}")
                        i += 1
                    else:
                        pol, dest = paso["politica"]
                        self.log(f"{n}. {detalle} → {pol}")
                        if pol == "parar":
                            break
                        i = (0 if pol == "repetir"
                             else dest - 1 if pol == "ir" else i + 1)

                elif op == "clic":
                    if self.last_pos is None:
                        self.log(f"{n}. no hay ninguna posición donde clicar; "
                                 f"paro.")
                        break
                    x, y = self.last_pos
                    # el clic haría release del botón, y si es el que estamos
                    # manteniendo lo dejaría suelto: se aparta y se restituye
                    botones = set(self._botones)
                    for b in botones:
                        try:
                            self.mouse.release(getattr(Button, b))
                        except Exception:
                            pass
                    self._botones.clear()
                    click_at(self.mouse, x, y, boton=paso["boton"],
                             doble=paso["doble"], restore=self.restore_mouse,
                             move_delay=self.move_delay)
                    self.log(f"{n}. clic en ({x}, {y})")
                    if botones:
                        self._reafirmar((), botones)
                        self.log(f"   y recupero el clic mantenido "
                                 f"({', '.join(sorted(botones))})")
                    if self.sound:
                        beep(True)
                    i += 1

                elif op == "reafirmar":
                    vueltos = self._reafirmar(set(self._teclas),
                                              set(self._botones))
                    self.log(f"{n}. reafirmado: "
                             + (", ".join(vueltos) if vueltos
                                else "no había nada mantenido"))
                    i += 1

                elif op == "esperar":
                    self.log(f"{n}. esperando {paso['segundos']:g} s")
                    if not self._esperar(paso["segundos"]):
                        break
                    i += 1

                elif op == "tecla":
                    combo = resolver_combo(paso["tecla"])
                    # las de delante se mantienen mientras se pulsa la última,
                    # y se sueltan en orden inverso: un atajo de verdad
                    for k in combo[:-1]:
                        self.keyboard.press(k)
                        time.sleep(0.03)
                    self.keyboard.press(combo[-1])
                    time.sleep(0.05)
                    self.keyboard.release(combo[-1])
                    for k in reversed(combo[:-1]):
                        time.sleep(0.03)
                        self.keyboard.release(k)
                    self.log(f"{n}. tecla {paso['tecla']}")
                    i += 1

                elif op == "girar":
                    hx, hy = turn_camera(paso["dx"], paso["dy"],
                                         espera=self._esperar)
                    self.log(f"{n}. girada la cámara ({hx:+d}, {hy:+d})")
                    if self._stop.is_set():
                        break
                    i += 1

                elif op == "mantener":
                    k = resolver_tecla(paso["tecla"])
                    self.keyboard.press(k)
                    self._teclas.add(paso["tecla"])
                    if paso.get("atasco"):
                        atascado, cortado = self._avanzar_vigilando(paso, n)
                        self.keyboard.release(k)
                        self._teclas.discard(paso["tecla"])
                        if cortado:
                            break
                        if atascado:
                            pol, dest = paso["atasco"]
                            if pol == "parar":
                                break
                            i = (0 if pol == "repetir"
                                 else dest - 1 if pol == "ir" else i + 1)
                            continue
                        i += 1
                    elif paso["segundos"]:
                        self.log(f"{n}. {paso['tecla']} pulsada "
                                 f"{paso['segundos']:g} s")
                        cortado = not self._esperar(paso["segundos"])
                        self.keyboard.release(k)
                        self._teclas.discard(paso["tecla"])
                        if cortado:
                            break
                        i += 1
                    else:
                        self.log(f"{n}. {paso['tecla']} pulsada, la dejo así")
                        i += 1

                elif op == "soltar":
                    k = resolver_tecla(paso["tecla"])
                    self.keyboard.release(k)
                    self._teclas.discard(paso["tecla"])
                    self.log(f"{n}. soltada {paso['tecla']}")
                    i += 1

                elif op == "mantener_clic":
                    btn = getattr(Button, paso["boton"])
                    self.mouse.press(btn)
                    self._botones.add(paso["boton"])
                    if paso["segundos"]:
                        self.log(f"{n}. clic {paso['boton']} mantenido "
                                 f"{paso['segundos']:g} s")
                        cortado = not self._esperar(paso["segundos"])
                        self.mouse.release(btn)
                        self._botones.discard(paso["boton"])
                        if cortado:
                            break
                    else:
                        self.log(f"{n}. clic {paso['boton']} pulsado, lo dejo "
                                 f"así")
                    i += 1

                elif op == "soltar_clic":
                    self.mouse.release(getattr(Button, paso["boton"]))
                    self._botones.discard(paso["boton"])
                    self.log(f"{n}. soltado el clic {paso['boton']}")
                    i += 1

                elif op == "escribir":
                    # con la misma cadencia que el texto programado: de golpe,
                    # un juego se salta letras
                    type_text(self.keyboard, paso["texto"], intro=False)
                    self.log(f"{n}. escrito «{paso['texto']}»")
                    i += 1

                elif op == "macro":
                    self.log(f"{n}. reproduciendo "
                             f"{os.path.basename(paso['ruta'])}")
                    try:
                        with open(paso["ruta"], encoding="utf-8-sig") as f:
                            ev = json.load(f).get("events", [])
                        Player().play_sync(ev)
                    except Exception as exc:
                        self.log(f"   no pude reproducirla: {exc}; paro.")
                        break
                    i += 1

                elif op == "pitar":
                    beep(True)
                    self.log(f"{n}. pitido")
                    i += 1

                elif op == "ir":
                    i = paso["destino"] - 1

                elif op == "repetir":
                    if paso.get("veces") is None:
                        i = 0
                    else:
                        hechas = vueltas.get(i, 0) + 1
                        vueltas[i] = hechas
                        if hechas <= paso["veces"]:
                            self.log(f"{n}. vuelta {hechas} de "
                                     f"{paso['veces']}")
                            i = 0
                        else:
                            self.log(f"{n}. hechas las {paso['veces']} vueltas.")
                            break

                else:                      # parar
                    self.log(f"{n}. parar")
                    break
                self.pasos_hechos += 1
        except Exception as exc:
            self.log(f"El guion se ha cortado por un error: {exc}")
        finally:
            if self.sonido is not None:
                self.sonido.stop()
            sueltos = self._soltar_todo()
            if sueltos:
                self.log("Suelto lo que quedaba pulsado: "
                         + ", ".join(sueltos) + ".")
            self.running = False
            self._stop.set()
            if self.on_finish:
                self.on_finish()


# ---------------------------------------------------------------- GUI

class App:
    def __init__(self, root):
        self.root = root
        root.title(f"{APP_NAME} — automatiza clics y macros")
        root.geometry("640x840")

        self.recorder = Recorder()
        self.player = Player(on_finish=self._on_play_finish,
                             on_progress=self._on_play_progress)
        self.finder = Finder()
        self.watcher = Watcher(self.finder, self.log, self.set_status)
        self.targets = {}          # nombre -> perfil del Finder
        self.script = Script(self.log, self.targets, self.set_status,
                             on_finish=self._on_script_finish)
        self.events = []
        self.ancla_img = None       # foto de la vista al grabar (1ª persona)
        self.ancla_pos = None
        self.current_file = None
        self._zone_p1 = None
        self._sched_stop = threading.Event()
        self._sched_thread = None
        self._kb = KeyboardController()
        self._txt_stop = threading.Event()
        self._txt_thread = None
        self._txt_count = 0
        self._guard_stop = threading.Event()
        self._guard_thread = None
        self._guard_payload = ("", 0.0, 0.0)
        # copias normales de ajustes que leen otros hilos, porque las variables
        # de Tkinter solo se pueden tocar desde el hilo de la interfaz
        self._logfile_on = True
        self._txt_payload = ("", None, True)
        self._log_queue = queue.Queue()
        self._status_pend = None
        self._cerrando = False

        self._build_ui()
        self._migrado = False
        self._load_config()
        self._start_hotkeys()
        self._drain_log()          # arranca el vaciado periódico de la cola
        self.log(f"{APP_NAME} listo. {T_REC} grabar | {T_PLAY} reproducir | "
                 f"{T_ZONE} marcar zona | {T_PICK} cuentagotas | "
                 f"{T_TPL} plantilla | {T_WATCH} vigilar | "
                 f"{T_SCRIPT} guion | {T_PANIC} PARAR")
        if DATA_MOTIVO:
            self.log(f"AVISO: {DATA_MOTIVO}, así que guardo tus ajustes en "
                     f"{APP_DIR}. Si quieres tenerlo todo junto al programa, "
                     f"copia Golem.exe a una carpeta de verdad (por ejemplo en "
                     f"el Escritorio) y ábrelo desde ahí.")
            beep(False)
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
        self.btn_rec = ttk.Button(row1, text=f"● Grabar ({T_REC})",
                                  command=self.toggle_record)
        self.btn_rec.pack(side="left", padx=4)
        self.btn_play = ttk.Button(row1, text=f"▶ Reproducir ({T_PLAY})",
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

        row4 = ttk.Frame(fm)
        row4.pack(fill="x", **pad)
        self.var_relative = tk.BooleanVar(value=False)
        ttk.Checkbutton(row4,
                        text="Movimiento relativo (juegos en 1ª persona: "
                             "Minecraft, FPS…)",
                        variable=self.var_relative,
                        command=self._on_relative_change).pack(side="left")

        row4b = ttk.Frame(fm)
        row4b.pack(fill="x", **pad)
        self.var_alinear = tk.BooleanVar(value=True)
        ttk.Checkbutton(row4b,
                        text="Alinear la cámara antes de reproducir",
                        variable=self.var_alinear).pack(side="left")
        ttk.Button(row4b, text="Comprobar alineación",
                   command=self.comprobar_alineacion).pack(side="left", padx=6)

        row5 = ttk.Frame(fm)
        row5.pack(fill="x", **pad)
        self.var_txt_on = tk.BooleanVar(value=False)
        ttk.Checkbutton(row5, text="Escribir un texto solo cada",
                        variable=self.var_txt_on,
                        command=self._toggle_text_scheduler).pack(side="left")
        self.var_txt_min = tk.StringVar(value="31")
        ttk.Spinbox(row5, textvariable=self.var_txt_min, from_=1, to=1440,
                    width=6).pack(side="left", padx=4)
        ttk.Label(row5, text="minutos").pack(side="left")
        ttk.Button(row5, text="Probarlo ahora",
                   command=self.send_text_now).pack(side="left", padx=8)

        row6 = ttk.Frame(fm)
        row6.pack(fill="x", **pad)
        ttk.Label(row6, text="Texto:").pack(side="left")
        self.var_txt_text = tk.StringVar(value="")
        ttk.Entry(row6, textvariable=self.var_txt_text, width=26).pack(
            side="left", padx=4)
        ttk.Label(row6, text="Abrir el chat con:").pack(side="left", padx=(8, 0))
        self.var_txt_key = tk.StringVar(value="t")
        ttk.Entry(row6, textvariable=self.var_txt_key, width=5).pack(
            side="left", padx=4)
        self.var_txt_enter = tk.BooleanVar(value=True)
        ttk.Checkbutton(row6, text="Intro al final",
                        variable=self.var_txt_enter,
                        command=self._apply_text_payload).pack(side="left",
                                                               padx=6)
        for v in (self.var_txt_text, self.var_txt_key):
            v.trace_add("write", self._apply_text_payload)
        self._apply_text_payload()

        self.lbl_macro = ttk.Label(fm, text="Sin macro cargada")
        self.lbl_macro.pack(anchor="w", **pad)

        # --- pestañas: vigilante y guion ---
        nb = ttk.Notebook(self.root)
        nb.pack(fill="x", **pad)
        fc = ttk.Frame(nb)
        nb.add(fc, text="  Vigilante (un solo clic)  ")
        fg = ttk.Frame(nb)
        nb.add(fg, text="  Guion (varios pasos)  ")
        self._build_script_tab(fg, pad)

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
        ttk.Button(rowc0, text=f"Cuentagotas de color ({T_PICK})",
                   command=self.pick_color).pack(side="left", padx=4)
        ttk.Button(rowc0, text=f"Capturar imagen ({T_TPL})",
                   command=self.capture_template).pack(side="left", padx=4)
        ttk.Button(rowc0, text=f"Marcar zona ({T_ZONE})",
                   command=self.mark_zone).pack(side="left", padx=4)
        ttk.Button(rowc0, text="Toda la pantalla",
                   command=self.reset_zone).pack(side="left", padx=4)
        self.lbl_color = ttk.Label(rowc0, text="sin calibrar")
        self.lbl_color.pack(side="left", padx=8)

        rowc1 = ttk.Frame(fc)
        rowc1.pack(fill="x", **pad)
        self.btn_watch = ttk.Button(rowc1, text=f"Activar vigilancia ({T_WATCH})",
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

        # --- seguridad y guardia ---
        fs = ttk.LabelFrame(self.root, text=" Seguridad ")
        fs.pack(fill="x", **pad)

        rs1 = ttk.Frame(fs)
        rs1.pack(fill="x", **pad)
        ttk.Label(rs1, text="Actuar solo si la ventana de delante contiene:").pack(
            side="left")
        self.var_ventana = tk.StringVar(value="")
        ttk.Entry(rs1, textvariable=self.var_ventana, width=16).pack(
            side="left", padx=4)
        ttk.Button(rs1, text="Usar la de ahora",
                   command=self.usar_ventana_actual).pack(side="left", padx=4)
        ttk.Button(rs1, text="Comprobar todo",
                   command=self.diagnostico).pack(side="left", padx=4)

        rs2 = ttk.Frame(fs)
        rs2.pack(fill="x", **pad)
        self.var_guard_on = tk.BooleanVar(value=False)
        ttk.Checkbutton(rs2, text="Guardia:", variable=self.var_guard_on,
                        command=self._toggle_guard).pack(side="left")
        ttk.Label(rs2, text="abortar si aparece").pack(side="left", padx=(4, 0))
        self.var_guard_target = tk.StringVar(value="")
        self.cmb_guard = ttk.Combobox(rs2, textvariable=self.var_guard_target,
                                      width=12, state="readonly")
        self.cmb_guard.pack(side="left", padx=4)
        ttk.Label(rs2, text="parar a las").pack(side="left", padx=(6, 0))
        self.var_guard_horas = tk.StringVar(value="0")
        ttk.Spinbox(rs2, textvariable=self.var_guard_horas, from_=0, to=48,
                    width=4).pack(side="left", padx=2)
        ttk.Label(rs2, text="h · captura cada").pack(side="left")
        self.var_guard_shot = tk.StringVar(value="0")
        ttk.Spinbox(rs2, textvariable=self.var_guard_shot, from_=0, to=600,
                    width=4).pack(side="left", padx=2)
        ttk.Label(rs2, text="min").pack(side="left")

        # --- opciones generales ---
        fo = ttk.Frame(self.root)
        fo.pack(fill="x", **pad)
        self.var_topmost = tk.BooleanVar(value=False)
        ttk.Checkbutton(fo, text="Ventana siempre visible",
                        variable=self.var_topmost,
                        command=self._apply_topmost).pack(side="left")
        self.var_logfile = tk.BooleanVar(value=True)
        ttk.Checkbutton(fo, text="Guardar registro en archivo",
                        variable=self.var_logfile,
                        command=self._apply_logfile).pack(side="left", padx=10)
        ttk.Button(fo, text=f"■ PARADA TOTAL ({T_PANIC})",
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

    def _build_script_tab(self, fg, pad):
        r0 = ttk.Frame(fg)
        r0.pack(fill="x", **pad)
        ttk.Label(r0, text="Objetivos guardados:").pack(side="left")
        self.var_target = tk.StringVar()
        self.cmb_targets = ttk.Combobox(r0, textvariable=self.var_target,
                                        width=16, state="readonly")
        self.cmb_targets.pack(side="left", padx=4)
        ttk.Button(r0, text="Borrar", width=7,
                   command=self.delete_target).pack(side="left", padx=2)

        r1 = ttk.Frame(fg)
        r1.pack(fill="x", **pad)
        ttk.Label(r1, text="Guardar lo de la otra pestaña como:").pack(
            side="left")
        self.var_new_target = tk.StringVar()
        ttk.Entry(r1, textvariable=self.var_new_target, width=14).pack(
            side="left", padx=4)
        ttk.Button(r1, text="Guardar objetivo",
                   command=self.save_target).pack(side="left", padx=2)

        self.txt_script = tk.Text(fg, height=9, font=("Consolas", 9),
                                  undo=True)
        self.txt_script.pack(fill="both", expand=True, padx=8, pady=4)

        r2 = ttk.Frame(fg)
        r2.pack(fill="x", **pad)
        ttk.Button(r2, text="Comprobar",
                   command=self.check_script).pack(side="left", padx=4)
        self.btn_script = ttk.Button(r2, text=f"▶ Ejecutar guion ({T_SCRIPT})",
                                     command=self.toggle_script)
        self.btn_script.pack(side="left", padx=4)
        ttk.Button(r2, text="Instrucciones",
                   command=self.script_help).pack(side="left", padx=4)
        ttk.Button(r2, text="Ejemplo",
                   command=self.script_example).pack(side="left", padx=4)

    # ---------- helpers ----------
    def log(self, msg):
        """Apunta una línea. Se puede llamar desde cualquier hilo.

        Tkinter no es seguro entre hilos, y aquí escriben el vigilante, el
        guion y el temporizador del texto. Así que ningún hilo toca la
        interfaz: dejan la línea en una cola y el hilo de la ventana la vacía.
        Antes se llamaba a root.after() desde el hilo, que a veces funciona y a
        veces suelta un 'invalid command name' o revienta al cerrar.
        """
        linea = time.strftime("[%H:%M:%S] ") + msg
        self._log_queue.put(linea)
        # el archivo es Python normal, se puede escribir desde cualquier hilo;
        # y _logfile_on es una copia de la casilla, no la variable de Tkinter
        if self._logfile_on:
            try:
                with open(LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d ") + linea + "\n")
            except Exception:
                pass

    def _drain_log(self, reprogramar=True):
        """Vuelca la cola en la ventana. Solo lo llama el hilo de la interfaz."""
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
                self.root.after(120, self._drain_log)
            except Exception:
                pass

    def _apply_logfile(self):
        self._logfile_on = bool(self.var_logfile.get())

    def set_status(self, text):
        # igual que log(): se deja escrito y lo aplica el hilo de la ventana
        self._status_pend = text

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
            req = self.var_ventana.get().strip()
            w.ventana_req = req
            self.script.ventana_req = req
        except ValueError:
            self.log("Aviso: algún parámetro no es un número válido; "
                     "se mantienen los anteriores.")

    # ---------- parada de emergencia ----------
    def panic(self):
        paro = []
        if self.script.running:
            self.script.stop()
            self.btn_script.configure(text=f"▶ Ejecutar guion ({T_SCRIPT})")
            paro.append("guion")
        if self.player.playing:
            self.player.stop()
            paro.append("reproducción")
        if self.watcher.active:
            self.watcher.stop()
            self.btn_watch.configure(text=f"Activar vigilancia ({T_WATCH})")
            paro.append("vigilancia")
        if self.recorder.recording:
            self.events = self.recorder.stop()
            self.btn_rec.configure(text=f"● Grabar ({T_REC})")
            paro.append("grabación")
        if self.var_sched.get():
            self.var_sched.set(False)
            self._toggle_scheduler()
            paro.append("repetición programada")
        if self.var_txt_on.get():
            self.var_txt_on.set(False)
            self._toggle_text_scheduler()
            paro.append("texto programado")
        if self.var_guard_on.get():
            self.var_guard_on.set(False)
            self._guard_stop.set()
            paro.append("guardia")
        self.set_status("PARADA TOTAL")
        self.log("PARADA TOTAL: " + (", ".join(paro) if paro
                                     else "no había nada activo") + ".")
        beep(False)

    def comprobar_alineacion(self):
        """Dice cuánto está desviada la cámara, sin tocarla ni reproducir."""
        if self.ancla_img is None:
            self.log("Esta macro no trae foto de la vista. Se guarda al empezar "
                     f"a grabar con 'Movimiento relativo' marcado ({T_REC}).")
            return
        destino = self.ancla_pos

        def _do():
            loc, score = localizar_ancla(self.ancla_img)
            if loc is None:
                self.log("No pude comparar la vista.")
                return
            self.log(f"Parecido con la vista de la grabación: {score:.2f} "
                     f"(hace falta 0.55 para poder alinear).")
            if score < 0.55:
                self.log("   Es poco: estás mirando a otro sitio, o el juego no "
                         "está delante. Colócate más o menos donde grabaste.")
                beep(False)
                return
            self.log(f"   Desvío: {destino[0] - loc[0]:+d} px en horizontal, "
                     f"{destino[1] - loc[1]:+d} px en vertical. Al reproducir "
                     f"se corrige solo.")
            beep(True)
        threading.Thread(target=_do, daemon=True).start()

    # ---------- movimiento relativo ----------
    def _on_relative_change(self):
        self.recorder.relative = self.var_relative.get()
        if self.recorder.relative:
            self.log("Movimiento relativo ACTIVADO para grabar. Guardaré cuánto "
                     "se desplaza el ratón en vez de dónde está el puntero, "
                     "leyéndolo del propio ratón por raw input. Es lo único que "
                     "gira la cámara en un juego que captura el ratón.")
            self.log("   Al reproducir se detecta solo por el contenido de la "
                     "macro, así que no tienes que volver a marcar nada.")
        else:
            self.log("Movimiento relativo desactivado: vuelvo a grabar "
                     "posiciones absolutas, que es lo que vale para menús y "
                     "ventanas normales.")

    # ---------- guion ----------
    def _refresh_targets(self):
        nombres = sorted(self.targets)
        self.cmb_targets.configure(values=nombres)
        if self.var_target.get() not in nombres:
            self.var_target.set(nombres[0] if nombres else "")
        # la guardia elige entre los mismos objetivos, más "ninguno"
        self.cmb_guard.configure(values=[""] + nombres)
        if self.var_guard_target.get() not in [""] + nombres:
            self.var_guard_target.set("")

    def save_target(self):
        nombre = self.var_new_target.get().strip()
        if not nombre:
            self.log("Ponle un nombre al objetivo antes de guardarlo.")
            return
        if " " in nombre:
            self.log("El nombre no puede llevar espacios (en el guion se "
                     "escribe suelto): usa por ejemplo 'cristal_cofre'.")
            return
        self._apply_settings()
        nuevo = nombre not in self.targets
        self.targets[nombre] = self.finder.snapshot()
        self._refresh_targets()
        self.var_target.set(nombre)
        self.var_new_target.set("")
        f = self.finder
        with mss.mss() as sct:
            mon = sct.monitors[1]
        zona = ("toda la pantalla"
                if (f.roi_left, f.roi_right, f.roi_top, f.roi_bottom)
                == (0.0, 1.0, 0.0, 1.0)
                else f"{int((f.roi_right - f.roi_left) * mon['width'])}x"
                     f"{int((f.roi_bottom - f.roi_top) * mon['height'])} px")
        self.log(f"Objetivo '{nombre}' {'guardado' if nuevo else 'actualizado'}: "
                 f"modo {f.mode}, zona {zona}. Ya puedes usarlo en el guion, "
                 f"por ejemplo: buscar {nombre}")
        beep(True)

    def delete_target(self):
        nombre = self.var_target.get()
        if nombre in self.targets:
            del self.targets[nombre]
            self._refresh_targets()
            self.log(f"Objetivo '{nombre}' borrado.")
        else:
            self.log("No hay ningún objetivo seleccionado.")

    def script_help(self):
        for l in ("Instrucciones del guion (una por línea, # para comentarios):",
                  "   buscar <objetivo> [segundos] [si_falla parar|seguir|"
                  "repetir|ir <nº>]",
                  "   desaparecer <objetivo> [segundos]",
                  "   esperar_cambio <objetivo> <segundos> [si_falla …]  ← algo "
                  "se mueve en esa zona",
                  "   esperar_sonido <segundos> [si_falla …]  ← suena algo por "
                  "los altavoces",
                  "   clic [doble|derecho|medio]   ← donde se vio el último "
                  "objetivo",
                  "   esperar <segundos>",
                  "   tecla <nombre>               ← esc, intro, espacio, f, 1…",
                  "        también combinaciones: shift+1, ctrl+f, "
                  "ctrl+shift+intro",
                  "   escribir <texto>",
                  "   macro <archivo.macro.json>",
                  "   pitar",
                  "   ir <nº> / repetir [veces] / parar",
                  "Para juegos en 1ª persona:",
                  "   girar <lados> <arriba/abajo>  ← girar 200 0 mira a la "
                  "derecha; negativo, al revés",
                  "   mantener <tecla> [segundos] [si_atascado parar|seguir|"
                  "repetir|ir <nº>]",
                  "        ← sin segundos, hasta un 'soltar'. Con si_atascado "
                  "vigila que la pantalla",
                  "          siga cambiando: si te quedas contra una pared, "
                  "hace lo que le digas.",
                  "   soltar <tecla>",
                  "   mantener_clic [derecho] [segundos]  ← para picar, minar…",
                  "   soltar_clic [derecho]",
                  "Sin segundos, 'buscar' espera indefinidamente. Los números "
                  "de paso son los que muestra 'Comprobar'. Al parar el guion se "
                  "suelta solo todo lo que hubiera quedado pulsado."):
            self.log(l)

    def script_example(self):
        nombre = self.var_target.get() or "cristal"
        ejemplo = (f"# espera el aviso, lo clica y vuelve a esperar\n"
                   f"buscar {nombre}\n"
                   f"clic\n"
                   f"esperar 2\n"
                   f"desaparecer {nombre} 30\n"
                   f"repetir\n")
        self.txt_script.delete("1.0", "end")
        self.txt_script.insert("1.0", ejemplo)
        self.log("Ejemplo puesto en el guion. Pulsa 'Comprobar' para ver qué "
                 "haría, paso por paso, sin ejecutarlo.")

    def check_script(self):
        texto = self.txt_script.get("1.0", "end")
        pasos, errores = Script.parse(texto, self.targets)
        if errores:
            self.log(f"El guion tiene {len(errores)} problema(s):")
            for e in errores:
                self.log("   · " + e)
            beep(False)
            return False
        self.log(f"El guion está bien. {len(pasos)} paso(s):")
        for l in Script.describe(pasos):
            self.log("   " + l)
        return True

    def toggle_script(self):
        if self.script.running:
            self.script.stop()
            self.log("Guion parado.")
            self.btn_script.configure(text=f"▶ Ejecutar guion ({T_SCRIPT})")
            self.set_status("Inactivo")
            return
        if self.recorder.recording or self.player.playing:
            self.log("No lanzo el guion mientras se graba o se reproduce una "
                     "macro.")
            return
        if self.watcher.active:
            self.log(f"Desactiva la vigilancia ({T_WATCH}) antes de lanzar el guion: "
                     "los dos quieren mover el ratón.")
            return
        texto = self.txt_script.get("1.0", "end")
        errores = self.script.load(texto)
        if errores:
            self.log(f"No lanzo el guion, tiene {len(errores)} problema(s):")
            for e in errores:
                self.log("   · " + e)
            beep(False)
            return
        self.script.restore_mouse = self.var_restore.get()
        self.script.sound = self.var_sound.get()
        self.script.interval = max(0.05, float(self.var_interval.get() or 1.0))
        if self.script.start():
            self.btn_script.configure(text=f"■ Parar guion ({T_SCRIPT})")
            self.log(f"Guion en marcha ({len(self.script.steps)} pasos). "
                     f"{T_SCRIPT} o {T_PANIC} para pararlo.")

    def _on_script_finish(self):
        def _fin():
            self.btn_script.configure(text=f"▶ Ejecutar guion ({T_SCRIPT})")
            self.set_status("Inactivo")
            self.log("Guion terminado.")
        self.root.after(0, _fin)

    def _on_mode_change(self):
        self._apply_settings()
        if self.finder.mode == "unico":
            self.log(f"Modo 'lo único con color': no hace falta el cuentagotas. "
                     f"Marca la zona con {T_ZONE} y listo — dentro de ella "
                     f"clicará lo único que tenga color, sea del tono que sea.")
        elif self.finder.mode == "color":
            self.log(f"Modo 'un color concreto': usa el cuentagotas ({T_PICK}) "
                     f"sobre el objetivo para calibrar el tono.")
        else:
            self.log(f"Modo 'imagen de referencia': captura el recorte con "
                     f"{T_TPL}. No sirve con objetos encantados.")

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
            self.recorder.relative = self.var_relative.get()
            self.recorder.start()
            self.btn_rec.configure(text=f"■ Parar grabación ({T_REC})")
            self.set_status(f"GRABANDO…  ({T_REC} para parar)")
            modo = ("movimiento relativo, para juegos en 1ª persona"
                    if self.recorder.relative else "posiciones absolutas")
            self.log(f"Grabación iniciada ({modo}).")
            if self.recorder.relative:
                if self.recorder.ancla is not None:
                    a = self.recorder.ancla
                    self.log(f"   Foto de la vista guardada "
                             f"({a.shape[1]}x{a.shape[0]} px): con ella "
                             f"recolocaré la cámara aquí antes de reproducir. "
                             f"Es una sola captura, ya está hecha — puedes "
                             f"moverte cuando quieras.")
                    if self.recorder.ancla_sirve:
                        self.log(f"   La vista es reconocible "
                                 f"({self.recorder.ancla_aviso}).")
                    else:
                        self.log(f"   OJO: {self.recorder.ancla_aviso}. "
                                 f"Al reproducir puede que no sepa dónde "
                                 f"estás. Si puedes, para la grabación, mira "
                                 f"hacia algo con más detalle y vuelve a "
                                 f"empezar.")
                        beep(False)
                else:
                    self.log("   No pude guardar la foto de la vista, así que "
                             "no habrá alineación automática.")
            if self.recorder.raw_error:
                self.log(f"   Pero no pude leer el ratón por raw input "
                         f"({self.recorder.raw_error}), así que no se grabará "
                         f"el movimiento. Los clics y el teclado sí.")
                beep(False)
        else:
            self.events = self.recorder.stop()
            self.ancla_img = self.recorder.ancla
            self.ancla_pos = self.recorder.ancla_pos
            self.watcher.paused = False
            self.btn_rec.configure(text=f"● Grabar ({T_REC})")
            dur = self.events[-1]["t"] if self.events else 0
            rel = Player.es_relativa(self.events)
            movs = sum(1 for e in self.events if e["e"] in ("mm", "mr"))
            self.lbl_macro.configure(
                text=f"Macro grabada: {len(self.events)} eventos, "
                     f"{dur:.1f} s{' , relativa' if rel else ''} (sin guardar)")
            self.set_status("Inactivo")
            self.log(f"Grabación parada: {len(self.events)} eventos "
                     f"({movs} de movimiento), {dur:.1f} s"
                     + (", en relativo." if rel else "."))
            if rel:
                total_x = sum(e["dx"] for e in self.events if e["e"] == "mr")
                total_y = sum(e["dy"] for e in self.events if e["e"] == "mr")
                self.log(f"   Desplazamiento total del ratón: {total_x:+d} en "
                         f"horizontal, {total_y:+d} en vertical. Al reproducir "
                         f"se inyectan esos mismos números, uno por uno.")
            elif self.recorder.relative and not movs:
                self.log("   No se ha grabado ningún movimiento: si estabas en "
                         "un juego con el ratón preso, comprueba que el aviso "
                         "de raw input no salió al empezar.")

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
        # Alinear la cámara antes de empezar: con movimiento relativo, arrancar
        # mirando a otro sitio desvía toda la grabación desde el primer segundo.
        if (Player.es_relativa(self.events) and self.ancla_img is not None
                and self.var_alinear.get()):
            self.btn_play.configure(text=f"■ Parar ({T_PLAY})")
            self.set_status("ALINEANDO LA CÁMARA…")

            def _alinear_y_reproducir():
                self.log("Alineando la cámara con la vista de la grabación…")
                try:
                    ok, detalle = alinear_camara(self.ancla_img, self.ancla_pos,
                                                 log=self.log)
                except Exception as exc:
                    ok, detalle = False, f"error al alinear: {exc}"
                self.log(("   " if ok else "   NO alineada: ") + detalle)
                if not ok:
                    self.log("No reproduzco: con la cámara desviada la "
                             "grabación entera saldría torcida. Colócate como "
                             "al grabar, o desmarca 'Alinear la cámara'.")
                    beep(False)
                    self.root.after(0, lambda: (
                        self.btn_play.configure(text=f"▶ Reproducir ({T_PLAY})"),
                        self.set_status("Inactivo")))
                    return
                beep(True)
                self.root.after(0, lambda: self._lanzar_macro(speed, repeats))
            threading.Thread(target=_alinear_y_reproducir, daemon=True).start()
            return
        self.btn_play.configure(text=f"■ Parar ({T_PLAY})")
        self._lanzar_macro(speed, repeats)

    def _lanzar_macro(self, speed, repeats):
        self.watcher.paused = True
        self.btn_play.configure(text=f"■ Parar ({T_PLAY})")
        self.player.play(self.events, speed=speed, repeats=repeats)
        self.log(f"Reproduciendo (x{speed}, "
                 f"{'∞' if repeats == 0 else repeats} veces)…")

    def _on_play_progress(self, loop, total):
        self.set_status(
            f"REPRODUCIENDO  vuelta {loop}/{'∞' if total == 0 else total}  "
            f"({T_PLAY} para parar)")

    def _on_play_finish(self):
        self.watcher.paused = False
        self.root.after(0, lambda: self.btn_play.configure(
            text=f"▶ Reproducir ({T_PLAY})"))
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

    def diagnostico(self):
        """Comprueba de una vez todo lo que puede fallar.

        Existe para que 'no funciona' se convierta en algo concreto sin gastar
        una tarde de idas y venidas: en un solo botón queda por escrito qué ve,
        qué oye, dónde guarda y cuánto tarda.
        """
        self._apply_settings()
        # Todo lo que venga de la interfaz se lee AQUÍ, en el hilo de la
        # ventana: dentro del hilo, un .get() de una variable de Tkinter suelta
        # "main thread is not in main loop".
        req = self.var_ventana.get().strip()
        texto_guion = self.txt_script.get("1.0", "end")

        def _do():
            L = self.log
            L("=" * 52)
            L("COMPROBACIÓN GENERAL")
            L(f"· Archivos en: {APP_DIR}")
            if DATA_MOTIVO:
                L(f"    OJO: {DATA_MOTIVO}. Copia el exe a una carpeta de "
                  f"verdad.")
            else:
                L("    bien: es una carpeta permanente.")
            try:
                with mss.mss() as sct:
                    mon = sct.monitors[1]
                L(f"· Pantalla: {mon['width']}x{mon['height']} px desde "
                  f"({mon['left']}, {mon['top']})")
            except Exception as exc:
                L(f"· Pantalla: no pude consultarla ({exc})")

            t = ventana_activa()
            L(f"· Ventana de delante: «{t}»")
            if not req:
                L("    sin exigencia de ventana: actuará esté quien esté "
                  "delante. Pon el juego y pulsa 'Usar la de ahora'.")
            elif req.lower() in t.lower():
                L(f"    bien: contiene «{req}», así que actuaría.")
            else:
                L(f"    ahora NO actuaría: se exige «{req}». Es lo normal si "
                  f"tienes delante esta ventana y no el juego.")

            f = self.finder
            nombres = {"unico": "lo único con color",
                       "color": "un color concreto",
                       "plantilla": "imagen de referencia"}
            L(f"· Modo de búsqueda: {nombres.get(f.mode, f.mode)}")
            sin_zona = (f.roi_left, f.roi_right, f.roi_top,
                        f.roi_bottom) == (0.0, 1.0, 0.0, 1.0)
            if sin_zona:
                L("    zona: TODA la pantalla. En modo 'lo único con color' "
                  "eso no vale para un juego: marca la zona con F2.")
            else:
                try:
                    with mss.mss() as sct:
                        m = sct.monitors[1]
                    L(f"    zona: "
                      f"{int((f.roi_right - f.roi_left) * m['width'])}x"
                      f"{int((f.roi_bottom - f.roi_top) * m['height'])} px "
                      f"desde ({int(f.roi_left * m['width'])}, "
                      f"{int(f.roi_top * m['height'])})")
                except Exception:
                    pass
            L(f"· Objetivos guardados: "
              + (", ".join(sorted(self.targets)) if self.targets
                 else "ninguno todavía (guárdalos en la pestaña Guion)"))

            try:
                t0 = time.perf_counter()
                n = len(f.candidates())
                ms = (time.perf_counter() - t0) * 1000
                espera = max(0, int(f.frames) - 1) * f.frame_gap * 1000
                L(f"· Un escaneo tarda {ms:.0f} ms y ahora mismo encuentra "
                  f"{n} candidato(s)")
                if espera > 0:
                    L(f"    de esos, {espera:.0f} ms son la espera a propósito "
                      f"entre los {int(f.frames)} fotogramas que une; "
                      f"el análisis en sí cuesta {ms - espera:.0f} ms")
            except Exception as exc:
                L(f"· Escaneo: falló ({exc})")

            L("· Raw input del ratón (para el movimiento relativo):")
            lis = RawMouseListener(lambda dx, dy: None)
            if lis.start():
                lis.stop()
                L("    bien: se puede grabar el movimiento relativo.")
            else:
                L(f"    NO disponible ({lis.error}). El movimiento relativo no "
                  f"se podrá grabar.")

            L("· Audio (para 'esperar_sonido'):")
            if not SoundWatch.disponible():
                L("    no está la librería de audio.")
            else:
                sw = SoundWatch()
                if sw.start():
                    time.sleep(1.0)
                    L(f"    bien: nivel ahora {sw.nivel:.4f} (con el juego "
                      f"sonando debería subir claramente).")
                    sw.stop()
                else:
                    L(f"    NO disponible ({sw.error}).")

            if texto_guion.strip():
                pasos, errs = Script.parse(texto_guion, self.targets)
                if errs:
                    L(f"· Guion: {len(errs)} problema(s). El primero: {errs[0]}")
                else:
                    L(f"· Guion: bien, {len(pasos)} paso(s).")
            else:
                L("· Guion: vacío.")
            L("=" * 52)
            beep(True)
        threading.Thread(target=_do, daemon=True).start()

    def usar_ventana_actual(self):
        t = ventana_activa()
        if not t or APP_NAME in t:
            self.log("Pon delante la ventana del juego y vuelve a pulsarlo "
                     "(ahora la de delante es la de Golem).")
            return
        # una palabra suele bastar y aguanta que el título cambie
        self.var_ventana.set(t.split()[0][:24])
        self._apply_settings()
        self.log(f"Solo actuaré cuando delante haya una ventana cuyo título "
                 f"contenga «{self.var_ventana.get()}» (la de ahora es "
                 f"«{t}»). Si te vas a otra ventana, todo se pausa y suelta "
                 f"las teclas.")

    # ---------- guardia: abortar, límite de tiempo y capturas ----------
    def _toggle_guard(self):
        if not self.var_guard_on.get():
            self._guard_stop.set()
            self.log("Guardia desactivada.")
            return
        try:
            horas = float(self.var_guard_horas.get().replace(",", ".") or 0)
            cada = float(self.var_guard_shot.get().replace(",", ".") or 0)
        except ValueError:
            self.log("El límite de horas o los minutos de captura no son "
                     "números válidos.")
            self.var_guard_on.set(False)
            return
        objetivo = self.var_guard_target.get().strip()
        if objetivo and objetivo not in self.targets:
            self.log(f"No hay un objetivo llamado '{objetivo}'. Guárdalo "
                     f"primero en la pestaña Guion.")
            self.var_guard_on.set(False)
            return
        if not objetivo and horas <= 0 and cada <= 0:
            self.log("La guardia no tiene nada que hacer: elige un objetivo "
                     "para abortar, un límite de horas o un intervalo de "
                     "capturas.")
            self.var_guard_on.set(False)
            return
        self._guard_payload = (objetivo, horas, cada)
        self._guard_stop.clear()
        self._guard_thread = threading.Thread(target=self._guard_run,
                                              daemon=True)
        self._guard_thread.start()
        partes = []
        if objetivo:
            partes.append(f"aborta todo si aparece '{objetivo}'")
        if horas > 0:
            partes.append(f"para todo a las {horas:g} h")
        if cada > 0:
            partes.append(f"guarda una captura cada {cada:g} min")
        self.log("Guardia activada: " + "; ".join(partes) + ".")

    def _guard_run(self):
        objetivo, horas, cada = self._guard_payload
        t0 = time.perf_counter()
        proxima_foto = t0 + cada * 60 if cada > 0 else None
        buscador = Finder() if objetivo else None
        n_foto = 0
        while not self._guard_stop.wait(2.0):
            ahora = time.perf_counter()
            if horas > 0 and ahora - t0 >= horas * 3600:
                self.log(f"Guardia: se han cumplido las {horas:g} horas. "
                         f"Paro todo.")
                self.root.after(0, self.panic)
                return
            if proxima_foto and ahora >= proxima_foto:
                proxima_foto = ahora + cada * 60
                n_foto += 1
                self._guardar_vigilancia(n_foto)
            if buscador is not None:
                try:
                    buscador.apply(self.targets[objetivo])
                    if buscador.candidates():
                        self.log(f"Guardia: ha aparecido '{objetivo}'. "
                                 f"ABORTO todo.")
                        beep(False)
                        self.root.after(0, self.panic)
                        return
                except Exception as exc:
                    self.log(f"Guardia: no pude mirar '{objetivo}' ({exc}).")
                    return

    def _guardar_vigilancia(self, n):
        """Una captura de cómo va la cosa, para verlo al volver."""
        try:
            bgr, _mon = self.finder.grab_screen()
            peq = cv2.resize(bgr, None, fx=0.5, fy=0.5,
                             interpolation=cv2.INTER_AREA)
            cv2.putText(peq, time.strftime("%Y-%m-%d %H:%M:%S"), (10, 24),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
            ruta = WATCH_SHOT % (n % 12 + 1)     # 12 en rotación, no llena nada
            cv2.imwrite(ruta, peq)
            self.log(f"Guardia: captura {os.path.basename(ruta)}")
        except Exception as exc:
            self.log(f"Guardia: no pude guardar la captura ({exc}).")

    # ---------- texto programado ----------
    def _ocupado(self):
        """¿Hay algo moviendo el ratón o el teclado ahora mismo?"""
        return (self.recorder.recording or self.player.playing
                or self.script.running)

    def _toggle_text_scheduler(self):
        if not self.var_txt_on.get():
            self._txt_stop.set()
            self.log("Texto programado desactivado.")
            return
        texto = self.var_txt_text.get()
        if not texto.strip():
            self.log("Escribe primero el texto que quieres que ponga.")
            self.var_txt_on.set(False)
            return
        try:
            minutos = float(self.var_txt_min.get().replace(",", "."))
            if minutos <= 0:
                raise ValueError
        except ValueError:
            self.log("Los minutos del texto no son un número válido.")
            self.var_txt_on.set(False)
            return
        tecla = self.var_txt_key.get().strip()
        if tecla:
            try:
                resolver_tecla(tecla)
            except ValueError as exc:
                self.log(f"La tecla para abrir el chat no vale: {exc}.")
                self.var_txt_on.set(False)
                return
        self._txt_stop.clear()
        self._txt_thread = threading.Thread(
            target=self._txt_run, args=(minutos,), daemon=True)
        self._txt_thread.start()
        self.log(f"Texto programado: «{texto}» cada {minutos:g} minutos"
                 + (f", abriendo el chat con '{tecla}'" if tecla else "")
                 + (" y pulsando Intro." if self.var_txt_enter.get() else "."))
        self.log("   Si al tocarle el turno hay una macro o un guion en marcha, "
                 "espera a que acabe antes de escribir, para no pisar el "
                 "movimiento. No hace falta que las cuentas cuadren al minuto.")

    def _txt_run(self, minutos):
        periodo = minutos * 60
        while not self._txt_stop.wait(periodo):
            # Esperar un hueco en vez de saltárselo: si la macro dura casi todo
            # el periodo, saltárselo significaría no escribir nunca. Se espera
            # como mucho un periodo entero, para no acumular turnos.
            t0 = time.perf_counter()
            aviso = False
            while self._ocupado():
                if not aviso:
                    self.log("Texto programado: hay algo en marcha, espero un "
                             "hueco para no pisar el movimiento.")
                    aviso = True
                if time.perf_counter() - t0 > periodo:
                    self.log("Texto programado: he esperado un periodo entero "
                             "y sigue ocupado; me salto este turno.")
                    break
                if self._txt_stop.wait(1.0):
                    return
            else:
                self._enviar_texto(programado=True)

    def _apply_text_payload(self, *_):
        """Copia lo que hay que escribir a atributos normales.

        El hilo del temporizador no puede leer variables de Tkinter (solo se
        pueden tocar desde el hilo de la interfaz), así que trabaja con esta
        copia. El trace la actualiza en cuanto cambias la casilla, de modo que
        editar el texto con el temporizador en marcha surte efecto igual.
        """
        self._txt_payload = (self.var_txt_text.get(),
                             self.var_txt_key.get().strip() or None,
                             bool(self.var_txt_enter.get()))

    def _enviar_texto(self, programado=False):
        texto, tecla, intro = self._txt_payload
        if not texto:
            self.log("No hay texto que escribir.")
            return False
        try:
            type_text(self._kb, texto, tecla_antes=tecla, intro=intro)
        except Exception as exc:
            self.log(f"No pude escribir el texto: {exc}")
            beep(False)
            return False
        self._txt_count += 1
        self.log(("Texto programado escrito" if programado else "Texto escrito")
                 + f": «{texto}»  [total: {self._txt_count}]")
        return True

    def send_text_now(self):
        if self._ocupado():
            self.log("Ahora mismo hay una macro o un guion en marcha; para eso "
                     "primero y vuelve a probar.")
            return
        threading.Thread(target=self._enviar_texto, daemon=True).start()

    def save_macro(self):
        if not self.events:
            self.log("Nada que guardar.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".macro.json", initialdir=APP_DIR,
            filetypes=[("Macro", "*.macro.json"), ("JSON", "*.json")])
        if not path:
            return
        rel = Player.es_relativa(self.events)
        datos = {"version": 3, "relative": rel, "events": self.events}
        # el ancla va dentro del propio archivo, para que la macro siga siendo
        # una sola cosa que se puede copiar de un sitio a otro
        if rel and self.ancla_img is not None:
            txt = ancla_a_texto(self.ancla_img)
            if txt:
                datos["ancla"] = txt
                datos["ancla_pos"] = list(self.ancla_pos)
        with open(path, "w", encoding="utf-8") as f:
            # 'relative' es informativo: al cargar se deduce de los eventos, así
            # que una macro de la versión 1 sigue reproduciéndose igual
            json.dump(datos, f)
        self.current_file = path
        self.lbl_macro.configure(
            text=f"Macro: {os.path.basename(path)} ({len(self.events)} eventos"
                 f"{', relativa' if rel else ''})")
        self.log(f"Guardada en {path}" + (" (movimiento relativo)" if rel else ""))

    def load_macro(self):
        path = filedialog.askopenfilename(
            initialdir=APP_DIR,
            filetypes=[("Macro", "*.macro.json"), ("JSON", "*.json")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
            self.events = data["events"]
        except Exception as exc:
            messagebox.showerror("Error", f"No se pudo cargar: {exc}")
            return
        self.ancla_img, self.ancla_pos = None, None
        if data.get("ancla"):
            try:
                self.ancla_img = texto_a_ancla(data["ancla"])
                self.ancla_pos = tuple(data.get("ancla_pos", (0, 0)))
            except Exception as exc:
                self.log(f"La macro traía una foto de la vista pero no se "
                         f"puede leer ({exc}); no habrá alineación.")
        self.current_file = path
        dur = self.events[-1]["t"] if self.events else 0
        rel = Player.es_relativa(self.events)
        self.lbl_macro.configure(
            text=f"Macro: {os.path.basename(path)} "
                 f"({len(self.events)} eventos, {dur:.1f} s"
                 f"{', relativa' if rel else ''})")
        self.log(f"Cargada {os.path.basename(path)}"
                 + (" — lleva movimiento relativo, así que se reproducirá para "
                    "un juego en 1ª persona." if rel else ""))
        if rel:
            self.log("   " + ("Trae foto de la vista: alinearé la cámara antes "
                              "de reproducir." if self.ancla_img is not None
                              else "Sin foto de la vista: tendrás que colocar "
                                   "la cámara tú antes de reproducir."))
        # la casilla sigue al contenido, para que no engañe
        self.var_relative.set(rel)
        self.recorder.relative = rel

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
            self.btn_watch.configure(text=f"Desactivar vigilancia ({T_WATCH})")
            self.log(f"Vigilancia ACTIVADA (modo {self.finder.mode}).")
        else:
            self.watcher.stop()
            self.btn_watch.configure(text=f"Activar vigilancia ({T_WATCH})")
            self.set_status("Inactivo")
            self.log("Vigilancia desactivada.")

    def test_detection(self):
        self._apply_settings()
        nombres = {"unico": "lo único con color", "color": "un color concreto",
                   "plantilla": "imagen de referencia"}
        self.log(f"Probando en 3 segundos (modo: "
                 f"{nombres.get(self.finder.mode, self.finder.mode)}) — deja la "
                 f"pantalla como cuando sale el aviso…")
        if self.finder.mode == "plantilla":
            self.log("   Ojo: con un objeto encantado este modo no es fiable, "
                     "porque el brillo cambia el sprite en cada instante. Si el "
                     "parecido te baila entre pruebas, es eso: usa 'lo único "
                     "con color'.")

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
        self._acciones = acciones = {
            HOTKEY_RECORD: self.toggle_record,
            HOTKEY_PLAY: self.toggle_play,
            HOTKEY_PICK: self.pick_color,
            HOTKEY_TEMPLATE: self.capture_template,
            HOTKEY_WATCH: self.toggle_watch,
            HOTKEY_ZONE: self.mark_zone,
            HOTKEY_SCRIPT: self.toggle_script,
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
            "txt_min": self.var_txt_min, "txt_text": self.var_txt_text,
            "txt_key": self.var_txt_key, "ventana": self.var_ventana,
            "guard_target": self.var_guard_target,
            "guard_horas": self.var_guard_horas,
            "guard_shot": self.var_guard_shot,
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
        self._apply_logfile()
        self.var_relative.set(cfg.get("relative", False))
        self.recorder.relative = self.var_relative.get()
        self.var_txt_enter.set(cfg.get("txt_enter", True))
        self._apply_text_payload()
        guardados = cfg.get("targets")
        if isinstance(guardados, dict):
            self.targets.clear()          # el Script comparte este mismo dict
            self.targets.update(guardados)
        self._refresh_targets()
        if isinstance(cfg.get("script"), str) and cfg["script"].strip():
            self.txt_script.delete("1.0", "end")
            self.txt_script.insert("1.0", cfg["script"])
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
        cfg["targets"] = self.targets
        cfg["script"] = self.txt_script.get("1.0", "end").rstrip()
        cfg["relative"] = self.var_relative.get()
        cfg["txt_enter"] = self.var_txt_enter.get()
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    def _on_close(self):
        self._cerrando = True
        self._save_config()
        self._sched_stop.set()
        self._txt_stop.set()
        self._guard_stop.set()
        self.script.stop()
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
