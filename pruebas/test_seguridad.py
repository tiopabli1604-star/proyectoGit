# -*- coding: utf-8 -*-
"""Reafirmar lo mantenido, pausa por ventana activa y guardia.

El bloque de 'reafirmar' cubre el fallo que reporto el usuario: tras saltar el
captcha dejaban de funcionar el clic mantenido y la W.
"""
import os
import sys
import threading
import time
import tkinter as tk

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as G

W, H = 800, 600
MON = {"left": 0, "top": 0, "width": W, "height": H}

R = []


def check(nombre, ok, extra=""):
    R.append(ok)
    extra = str(extra) if extra else ""
    print(f"  {'OK  ' if ok else 'FALLO'} {nombre}{(' — ' + extra) if extra else ''}")


class TecladoEspia:
    def __init__(self):
        self.acciones = []

    def press(self, k):
        self.acciones.append(("press", str(k)))

    def release(self, k):
        self.acciones.append(("release", str(k)))

    def type(self, s):
        self.acciones.append(("type", s))


class RatonEspia:
    def __init__(self):
        self.acciones = []
        self.position = (0, 0)

    def press(self, b):
        self.acciones.append(("press", str(b)))

    def release(self, b):
        self.acciones.append(("release", str(b)))


def vacia():
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = (30, 30, 30)
    cv2.rectangle(img, (200, 150), (600, 450), (200, 200, 200), -1)
    return img


def con_objeto(cx=400, cy=300):
    img = vacia()
    bgr = cv2.cvtColor(np.uint8([[[45, 200, 220]]]), cv2.COLOR_HSV2BGR)[0, 0]
    cv2.rectangle(img, (cx - 13, cy - 13), (cx + 13, cy + 13), bgr.tolist(), -1)
    return img


def instalar(secuencia):
    est = {"i": 0}

    def grab():
        img = secuencia[min(est["i"], len(secuencia) - 1)]
        est["i"] += 1
        return img, MON
    G.Finder.grab_screen = staticmethod(grab)
    return est


OBJETIVOS = {"cristal": {"mode": "unico", "sat_min": 40, "val_min": 90,
                         "min_area": 80, "max_area": 40000, "max_side": 80,
                         "frames": 1, "on_gui": False,
                         "roi_left": 0.0, "roi_right": 1.0,
                         "roi_top": 0.0, "roi_bottom": 1.0}}


def guion(texto, clics=None):
    lineas = []
    s = G.Script(lineas.append, OBJETIVOS)
    s.interval = 0.02
    s.confirmaciones = 1
    s.sound = False
    s.keyboard = TecladoEspia()
    s.mouse = RatonEspia()
    errores = s.load(texto)
    return s, errores, lineas


def correr(s, limite=10.0):
    s.start()
    t0 = time.perf_counter()
    while s.running and time.perf_counter() - t0 < limite:
        time.sleep(0.01)
    colgado = s.running
    s.stop()
    s._thread.join(timeout=2.0)
    return colgado


# --------------------------------------------------- reafirmar
def p_clic_no_se_lleva_el_mantenido():
    """El fallo del usuario: el clic del captcha soltaba el clic mantenido."""
    print("--- un 'clic' no debe dejar suelto el clic mantenido ---")
    instalar([con_objeto()])
    orig = G.click_at
    G.click_at = lambda m, x, y, **kw: m.acciones.append(("click_at", "left"))
    try:
        s, errs, log = guion("mantener_clic\nbuscar cristal\nclic\nparar")
        check("analiza", not errs, str(errs))
        correr(s)
        check("el boton sigue dado por mantenido al final",
              "left" in s._botones or not s.running, str(s._botones))
        ops = [a[0] for a in s.mouse.acciones]
        # la coreografia entera, en orden:
        #   press    = mantener_clic
        #   release  = se aparta para que el clic no lo deje suelto
        #   click_at = el clic del captcha
        #   release  + press = reafirmar, que lo recupera
        #   release  = la limpieza del final del guion
        esperado = ["press", "release", "click_at", "release", "press",
                    "release"]
        check("la secuencia es la correcta, en orden", ops == esperado,
              f"{ops} / esperado {esperado}")
        check("y lo recupera antes de la limpieza final",
              len(ops) >= 2 and ops[-2] == "press", str(ops))
        check("lo dice en el registro",
              any("recupero lo mantenido" in l and "left" in l for l in log),
              str([l for l in log if "recupero" in l]))
    finally:
        G.click_at = orig


def p_reafirmar_suelta_y_pulsa():
    print("--- 'reafirmar' suelta y vuelve a pulsar (la W tras el cofre) ---")
    instalar([vacia()])
    s, errs, log = guion("mantener w\nreafirmar\nparar")
    check("analiza", not errs, str(errs))
    correr(s)
    ks = [a[0] for a in s.keyboard.acciones]
    check("press, release, press", ks[:3] == ["press", "release", "press"],
          str(s.keyboard.acciones))
    check("lo dice en el registro",
          any("reafirmado: w" in l for l in log),
          str([l for l in log if "reafirm" in l]))


def p_reafirmar_sin_nada():
    print("--- 'reafirmar' sin nada mantenido no revienta ---")
    instalar([vacia()])
    s, errs, log = guion("reafirmar\nparar")
    check("analiza", not errs, str(errs))
    colgado = correr(s)
    check("no se cuelga", not colgado)
    check("y lo dice", any("no había nada mantenido" in l for l in log),
          str(log))


def p_reafirmar_no_lleva_argumentos():
    print("--- 'reafirmar' no lleva nada detras ---")
    errs = G.Script.parse("reafirmar w", OBJETIVOS)[1]
    check("lo avisa", any("no lleva nada detrás" in e for e in errs), str(errs))


def p_secuencia_completa_del_captcha():
    """El guion que de verdad va a usar: andar, atender el captcha, seguir."""
    print("--- la secuencia real: mantener, captcha, reafirmar, seguir ---")
    # el objeto aparece, y despues desaparece
    instalar([con_objeto()] * 3 + [vacia()] * 20)
    orig = G.click_at
    G.click_at = lambda m, x, y, **kw: m.acciones.append(("click_at", "left"))
    try:
        s, errs, log = guion("mantener w\n"
                             "mantener_clic\n"
                             "buscar cristal 3\n"
                             "clic\n"
                             "desaparecer cristal 3\n"
                             "reafirmar\n"
                             "parar")
        check("analiza", not errs, str(errs))
        colgado = correr(s)
        check("no se cuelga", not colgado)
        check("llega al final", any("7. parar" in l for l in log), str(log))
        check("reafirma al cerrarse el cofre",
              any("reafirmado" in l for l in log),
              str([l for l in log if "reafirm" in l]))
        # Se reafirma en varios sitios (el clic, el desaparecer y el reafirmar
        # explicito), asi que hay varios pares. Lo que importa es el invariante:
        # cada suelta va seguida de una pulsacion, salvo la ultima, que es la
        # limpieza del final. Dos sueltas seguidas significarian que se quedo
        # suelta sin recuperar.
        ks = [a[0] for a in s.keyboard.acciones]
        check("alterna pulsar y soltar, sin quedarse suelta",
              ks == ["press", "release"] * (len(ks) // 2) and len(ks) % 2 == 0,
              str(ks))
        check("la ultima suelta es la limpieza, y antes estaba pulsada",
              len(ks) >= 2 and ks[-1] == "release" and ks[-2] == "press",
              str(ks[-4:]))
        check("y se reafirmo mas de una vez", ks.count("press") >= 3,
              f"{ks.count('press')} pulsaciones")
    finally:
        G.click_at = orig


# --------------------------------------------------- ventana activa
def p_ventana_ok():
    print("--- comparacion del titulo de la ventana ---")
    s, _e, _l = guion("parar")
    s.ventana_req = ""
    check("sin exigencia, siempre vale", s.ventana_ok() is True)
    s.ventana_req = "estonoexisteseguro_zzz"
    check("con una que no esta, no vale", s.ventana_ok() is False)
    actual = G.ventana_activa()
    check("hay un titulo de ventana activa", isinstance(actual, str),
          repr(actual[:40]))
    if actual:
        s.ventana_req = actual[:6]
        check("y con un trozo del titulo real, vale", s.ventana_ok() is True,
              repr(actual[:6]))


def p_pausa_y_suelta():
    print("--- si la ventana no esta delante: pausa y SUELTA las teclas ---")
    instalar([vacia()])
    s, errs, log = guion("mantener w\nesperar 5\nparar")
    s.ventana_req = "estonoexisteseguro_zzz"
    s.start()
    t0 = time.perf_counter()
    while not any("En pausa" in l for l in log) and time.perf_counter() - t0 < 4:
        time.sleep(0.01)
    check("se pausa", any("En pausa" in l for l in log), str(log[:2]))
    check("no ha llegado a mantener la W", not s._teclas, str(s._teclas))
    check("y sigue vivo, esperando", s.running)
    s.stop()
    s._thread.join(timeout=3.0)
    check("para bien", not s._thread.is_alive())


def p_vuelve_y_reafirma():
    print("--- al volver la ventana, recupera lo mantenido ---")
    instalar([vacia()])
    s, errs, log = guion("mantener w\nesperar 0.2\nparar")
    correr(s)                      # sin exigencia: mantiene la w
    check("primero mantiene la w",
          any(a[0] == "press" for a in s.keyboard.acciones),
          str(s.keyboard.acciones[:2]))

    # ahora con exigencia imposible desde el principio
    s2, _e, log2 = guion("mantener w\nesperar 5\nparar")
    s2.ventana_req = "estonoexisteseguro_zzz"
    s2.start()
    t0 = time.perf_counter()
    while not any("En pausa" in l for l in log2) and time.perf_counter() - t0 < 4:
        time.sleep(0.01)
    s2.ventana_req = ""           # como si volviera la ventana del juego
    t0 = time.perf_counter()
    while not any("Vuelvo" in l for l in log2) and time.perf_counter() - t0 < 4:
        time.sleep(0.01)
    check("dice que vuelve", any("Vuelvo" in l for l in log2),
          str([l for l in log2 if "Vuelvo" in l]))
    s2.stop()
    s2._thread.join(timeout=3.0)


def p_vigilante_respeta_la_ventana():
    print("--- el vigilante tampoco clica si la ventana no esta delante ---")
    w = G.Watcher(G.Finder(), lambda m: None)
    w.ventana_req = ""
    check("sin exigencia, vale", w.ventana_ok() is True)
    w.ventana_req = "estonoexisteseguro_zzz"
    check("con una que no esta, no", w.ventana_ok() is False)


# --------------------------------------------------- guardia
def abrir():
    if os.path.exists(G.CONFIG_PATH):
        os.remove(G.CONFIG_PATH)
    root = tk.Tk()
    root.withdraw()
    app = G.App(root)
    return app, root


def cerrar(app, root):
    try:
        app._guard_stop.set()
        app._txt_stop.set()
        app._sched_stop.set()
        app.script.stop()
        if app._hotkey_listener:
            app._hotkey_listener.stop()
    except Exception:
        pass
    root.destroy()


def registro(app, root):
    try:
        app._drain_log(reprogramar=False)
    except Exception:
        pass
    return app.txt_log.get("1.0", "end")


def p_guardia_valida():
    print("--- la guardia valida lo que le pides ---")
    app, root = abrir()
    try:
        app.var_guard_on.set(True)
        app._toggle_guard()
        check("sin nada que hacer, no arranca",
              app.var_guard_on.get() is False)
        check("y lo explica", "no tiene nada que hacer" in registro(app, root))

        app.var_guard_target.set("fantasma")
        app.var_guard_on.set(True)
        app._toggle_guard()
        check("con un objetivo que no existe, no arranca",
              app.var_guard_on.get() is False)
        check("y lo dice", "No hay un objetivo llamado" in registro(app, root))

        app.var_guard_target.set("")
        app.var_guard_horas.set("dos")
        app.var_guard_on.set(True)
        app._toggle_guard()
        check("con horas no numericas, no arranca",
              app.var_guard_on.get() is False)
    finally:
        cerrar(app, root)


def p_guardia_aborta():
    print("--- la guardia aborta si aparece el objetivo ---")
    app, root = abrir()
    try:
        app.targets["peligro"] = dict(OBJETIVOS["cristal"])
        app._refresh_targets()
        instalar([con_objeto()])
        app._guard_payload = ("peligro", 0.0, 0.0)
        app._guard_stop.clear()
        hilo = threading.Thread(target=app._guard_run, daemon=True)
        hilo.start()
        t0 = time.perf_counter()
        while "ABORTO" not in registro(app, root) and \
                time.perf_counter() - t0 < 8:
            time.sleep(0.05)
        check("lo detecta y aborta", "ABORTO" in registro(app, root),
              [l for l in registro(app, root).splitlines() if "ABORT" in l][:1])
    finally:
        app._guard_stop.set()
        cerrar(app, root)


def p_guardia_no_aborta_sin_motivo():
    print("--- y no aborta si el objetivo no esta ---")
    app, root = abrir()
    try:
        app.targets["peligro"] = dict(OBJETIVOS["cristal"])
        app._refresh_targets()
        instalar([vacia()])
        app._guard_payload = ("peligro", 0.0, 0.0)
        app._guard_stop.clear()
        hilo = threading.Thread(target=app._guard_run, daemon=True)
        hilo.start()
        time.sleep(3.0)
        check("no aborta", "ABORTO" not in registro(app, root))
        check("y sigue vigilando", hilo.is_alive())
    finally:
        app._guard_stop.set()
        cerrar(app, root)


def p_guardia_captura():
    print("--- la guardia guarda capturas periodicas ---")
    app, root = abrir()
    try:
        instalar([con_objeto()])
        rutas = [G.WATCH_SHOT % (n % 12 + 1) for n in range(1, 3)]
        for r in rutas:
            if os.path.exists(r):
                os.remove(r)
        app._guardar_vigilancia(1)
        app._guardar_vigilancia(2)
        check("crea los archivos", all(os.path.exists(r) for r in rutas),
              str([os.path.basename(r) for r in rutas]))
        check("y lo dice en el registro",
              "captura" in registro(app, root))
    finally:
        for r in rutas:
            if os.path.exists(r):
                os.remove(r)
        cerrar(app, root)


def p_panic_para_la_guardia():
    print("--- F12 para la guardia ---")
    app, root = abrir()
    try:
        app.targets["peligro"] = dict(OBJETIVOS["cristal"])
        app._refresh_targets()
        app.var_guard_target.set("peligro")
        app.var_guard_on.set(True)
        app._toggle_guard()
        check("arranco", app.var_guard_on.get() is True,
              registro(app, root)[-120:])
        app.panic()
        check("F12 la desmarca", app.var_guard_on.get() is False)
        check("y la nombra", "guardia" in registro(app, root))
    finally:
        cerrar(app, root)


if __name__ == "__main__":
    respaldo = None
    if os.path.exists(G.CONFIG_PATH):
        with open(G.CONFIG_PATH, encoding="utf-8-sig") as f:
            respaldo = f.read()
    try:
        for fn in (p_clic_no_se_lleva_el_mantenido, p_reafirmar_suelta_y_pulsa,
                   p_reafirmar_sin_nada, p_reafirmar_no_lleva_argumentos,
                   p_secuencia_completa_del_captcha, p_ventana_ok,
                   p_pausa_y_suelta, p_vuelve_y_reafirma,
                   p_vigilante_respeta_la_ventana, p_guardia_valida,
                   p_guardia_aborta, p_guardia_no_aborta_sin_motivo,
                   p_guardia_captura, p_panic_para_la_guardia):
            try:
                fn()
            except Exception:
                import traceback
                traceback.print_exc()
                R.append(False)
            print()
    finally:
        if respaldo is not None:
            with open(G.CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write(respaldo)
        elif os.path.exists(G.CONFIG_PATH):
            os.remove(G.CONFIG_PATH)
    fallos = R.count(False)
    print(f"{len(R)} comprobaciones, "
          + ("TODO OK" if not fallos else f"{fallos} FALLO(S)"))
    sys.exit(1 if fallos else 0)
