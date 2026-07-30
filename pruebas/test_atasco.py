# -*- coding: utf-8 -*-
"""Detector de atasco: la pantalla deja de cambiar mientras se ordena avanzar.

Las escenas simulan las tres situaciones que importan: andando (la vista cambia
mucho), contra una pared (casi no cambia, pero algo se mueve: particulas, el
cielo) y andando pero mirando algo casi liso, que NO debe contar como atasco
gracias a que el umbral se calcula solo.
"""
import os
import sys
import threading
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as G

W, H = 960, 540
MON = {"left": 0, "top": 0, "width": W, "height": H}

R = []


def check(nombre, ok, extra=""):
    R.append(ok)
    extra = str(extra) if extra else ""
    print(f"  {'OK  ' if ok else 'FALLO'} {nombre}{(' — ' + extra) if extra else ''}")


def paisaje(semilla, ruido=0):
    """Un 'mundo' con bloques; cambiar la semilla equivale a haberse movido."""
    rng = np.random.RandomState(semilla)
    img = np.zeros((H, W, 3), np.uint8)
    img[:H // 3] = (200, 170, 120)
    img[H // 3:] = (60, 110, 70)
    for _ in range(120):
        x, y = rng.randint(0, W - 60), rng.randint(H // 3, H - 60)
        s = rng.randint(30, 60)
        c = tuple(int(v) for v in rng.randint(40, 190, 3))
        cv2.rectangle(img, (x, y), (x + s, y + s), c, -1)
    if ruido:
        r = rng.randint(-ruido, ruido + 1, img.shape).astype(np.int16)
        img = np.clip(img.astype(np.int16) + r, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(img)


def pared(fase):
    """Contra una pared: casi todo igual, con un detalle animado pequeno."""
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = (70, 80, 95)
    for y in range(0, H, 40):
        for x in range(0, W, 40):
            cv2.rectangle(img, (x + 2, y + 2), (x + 36, y + 36),
                          (78, 88, 104), -1)
    # una particula que se mueve, para que no sea una imagen identica
    cv2.circle(img, (120 + (fase * 3) % 20, 400), 3, (200, 200, 200), -1)
    return np.ascontiguousarray(img)


def instalar(secuencia):
    est = {"i": 0}

    def grab():
        img = secuencia[min(est["i"], len(secuencia) - 1)]
        est["i"] += 1
        return img, MON
    G.Finder.grab_screen = staticmethod(grab)
    return est


def correr_detector(secuencia, det=None, pasos=None):
    """Pasa la secuencia por el detector. Devuelve (paso_en_que_salto, datos)."""
    instalar(secuencia)
    det = det or G.StuckDetector()
    datos = []
    salto = None
    for i in range(pasos or len(secuencia)):
        atascado, cambio, umbral = det.paso()
        datos.append((cambio, umbral))
        if atascado and salto is None:
            salto = i
    return salto, datos


def p_andando_no_es_atasco():
    print("--- andando: la vista cambia, no debe dar atasco ---")
    sec = [paisaje(s) for s in range(20)]
    salto, datos = correr_detector(sec)
    cambios = [c for c, _ in datos if c is not None]
    check("nunca dice atascado", salto is None, f"salto en el paso {salto}")
    check("y el cambio medido es alto",
          min(cambios) > 5, f"cambio minimo {min(cambios):.2f}")


def p_pared_es_atasco():
    print("--- contra una pared: debe detectarlo ---")
    # 8 fotogramas andando (para tener referencia) y luego pared
    sec = [paisaje(s) for s in range(8)] + [pared(f) for f in range(12)]
    salto, datos = correr_detector(sec)
    check("lo detecta", salto is not None, str(salto))
    check("y no antes de llegar a la pared", salto is None or salto >= 8,
          f"salto en el paso {salto} (la pared empieza en el 8)")
    if salto is not None:
        c, u = datos[salto]
        check("el cambio esta por debajo del umbral", c < u,
              f"cambio {c:.2f} < umbral {u:.2f}")


def p_atascado_desde_el_principio():
    print("--- si llega ya atascado, el suelo absoluto lo pilla ---")
    sec = [pared(f) for f in range(14)]
    salto, datos = correr_detector(sec)
    check("lo detecta igual", salto is not None, str(salto))
    check("y pronto", salto is not None and salto <= 6,
          f"paso {salto}")


def p_andando_junto_a_una_pared():
    """El caso peligroso: se anda pegado a una pared, asi que la vista cambia
    mucho menos que en campo abierto. No debe contar como atasco."""
    print("--- andando pegado a una pared texturada: no es atasco ---")
    base = np.random.RandomState(5).randint(60, 120, (H, W + 400, 3)).astype(np.uint8)
    base = cv2.GaussianBlur(base, (9, 9), 0)      # textura de piedra
    sec = []
    for s in range(20):                            # la pared desfila despacio
        img = base[:, s * 6:s * 6 + W].copy()
        sec.append(np.ascontiguousarray(img))
    salto, datos = correr_detector(sec)
    cambios = [c for c, _ in datos if c is not None]
    check("no dice atascado", salto is None, f"salto en {salto}")
    check("y el cambio, aunque modesto, pasa del suelo absoluto",
          min(cambios) > G.StuckDetector().umbral_min,
          f"cambio minimo {min(cambios):.2f} vs suelo "
          f"{G.StuckDetector().umbral_min}")


def p_suelo_absoluto():
    """Por debajo del suelo absoluto todo cuenta como atasco, a proposito: una
    pantalla que no cambia NADA no puede ser un personaje andando."""
    print("--- una pantalla congelada del todo siempre es atasco ---")
    fijo = paisaje(1)
    salto, datos = correr_detector([fijo] * 10)
    check("lo detecta", salto is not None, str(salto))
    cambios = [c for c, _ in datos if c is not None]
    check("porque el cambio es cero", max(cambios) == 0.0,
          f"maximo {max(cambios):.3f}")


def p_se_recupera():
    print("--- si se desatasca, deja de avisar ---")
    sec = ([paisaje(s) for s in range(8)] + [pared(f) for f in range(10)]
           + [paisaje(s) for s in range(50, 60)])
    instalar(sec)
    det = G.StuckDetector()
    estados = []
    for _ in range(len(sec)):
        atascado, _c, _u = det.paso()
        estados.append(atascado)
    check("hubo atasco en algun momento", any(estados))
    check("y al final ya no", not estados[-1], str(estados[-6:]))
    check("el contador de quietos vuelve a cero", det.quietos == 0,
          str(det.quietos))


def p_reset():
    print("--- reset limpia el historial ---")
    sec = [paisaje(s) for s in range(6)]
    instalar(sec)
    det = G.StuckDetector()
    for _ in range(5):
        det.paso()
    check("tiene historial", len(det.cambios) > 0, str(len(det.cambios)))
    det.reset()
    check("y tras reset no", not det.cambios and det.quietos == 0
          and det._prev is None)


# ------------------------------------------------------ dentro del guion
class TecladoEspia:
    def __init__(self):
        self.acciones = []

    def press(self, k):
        self.acciones.append(("press", str(k)))

    def release(self, k):
        self.acciones.append(("release", str(k)))

    def type(self, s):
        self.acciones.append(("type", s))


OBJETIVOS = {}


def guion(texto):
    lineas = []
    s = G.Script(lineas.append, OBJETIVOS)
    s.sound = False
    s.keyboard = TecladoEspia()
    s.stuck_intervalo = 0.02
    errores = s.load(texto)
    return s, errores, lineas


def correr(s, limite=15.0):
    s.start()
    t0 = time.perf_counter()
    while s.running and time.perf_counter() - t0 < limite:
        time.sleep(0.01)
    colgado = s.running
    s.stop()
    s._thread.join(timeout=2.0)
    return colgado


def p_analisis():
    print("--- el analisis de si_atascado ---")
    pasos, errs = G.Script.parse("mantener w 10 si_atascado ir 3\n"
                                 "parar\n"
                                 "girar 400 0\n"
                                 "repetir", OBJETIVOS)
    check("acepta la forma buena", not errs, str(errs))
    if pasos:
        d = G.Script.describe(pasos)
        check("y lo explica", "si se queda atascado" in d[0]
              and "salta al paso 3" in d[0], d[0])
    casos = [
        ("mantener w si_atascado parar", "necesita también los segundos"),
        ("mantener w 5 si_atascado", "tras 'si_atascado' pon"),
        ("mantener w 5 si_atascado loquesea", "tras 'si_atascado' pon"),
        ("mantener w 5 si_atascado ir", "necesita un número de paso"),
        ("mantener w 5 si_atascado ir 99", "no existe"),
        ("mantener w 5 raro", "no entiendo 'raro'"),
        ("mantener w raro", "no es un número de segundos"),
    ]
    for texto, esperado in casos:
        errs2 = G.Script.parse(texto, OBJETIVOS)[1]
        check(repr(texto)[:42], any(esperado in e for e in errs2),
              (errs2[0] if errs2 else "no dio error")[:60])


def p_guion_detecta_y_salta():
    print("--- el guion salta a la rutina de desatasco ---")
    # pared desde el principio: se atasca enseguida
    instalar([pared(f) for f in range(200)])
    s, errs, log = guion("mantener w 6 si_atascado ir 3\n"
                         "parar\n"
                         "pitar\n"
                         "parar")
    check("analiza", not errs, str(errs))
    colgado = correr(s, limite=12.0)
    check("no se cuelga", not colgado)
    check("detecta el atasco", any("ATASCADO" in l for l in log),
          str([l for l in log if "ATASC" in l][:1]))
    check("y salta al paso 3, no al 2",
          any("4. parar" in l for l in log) and
          not any("2. parar" in l for l in log), str(log))
    check("la tecla queda soltada", not s._teclas, str(s._teclas))
    ks = s.keyboard.acciones
    check("con su press y su release",
          any(a[0] == "press" for a in ks) and any(a[0] == "release" for a in ks),
          str(ks))


def p_guion_no_salta_si_todo_va_bien():
    print("--- si la vista cambia, agota el tiempo y sigue ---")
    instalar([paisaje(s % 40) for s in range(400)])
    s, errs, log = guion("mantener w 0.5 si_atascado ir 3\n"
                         "pitar\n"
                         "parar\n"
                         "parar")
    check("analiza", not errs, str(errs))
    correr(s, limite=10.0)
    check("no dice atascado", not any("ATASCADO" in l for l in log),
          str([l for l in log if "ATASC" in l][:1]))
    check("pasa por el paso 2, no salta al 3",
          any(l.startswith("2. ") for l in log), str(log))
    check("y acaba en el 3", any(l.startswith("3. parar") for l in log),
          str(log))


def p_se_corta_al_parar():
    print("--- vigilando un atasco, F12 corta y suelta ---")
    instalar([paisaje(s % 40) for s in range(2000)])
    s, errs, log = guion("mantener w 60 si_atascado parar\nparar")
    s.start()
    t0 = time.perf_counter()
    while not any("vigilando" in l for l in log) and time.perf_counter() - t0 < 4:
        time.sleep(0.01)
    check("esta vigilando", any("vigilando" in l for l in log), str(log[:2]))
    check("y la tecla pulsada", "w" in s._teclas, str(s._teclas))
    s.stop()
    s._thread.join(timeout=3.0)
    check("el hilo muere sin esperar los 60 s", not s._thread.is_alive())
    check("y suelta la tecla", not s._teclas, str(s._teclas))


if __name__ == "__main__":
    for fn in (p_andando_no_es_atasco, p_pared_es_atasco,
               p_atascado_desde_el_principio, p_andando_junto_a_una_pared,
               p_suelo_absoluto, p_se_recupera, p_reset, p_analisis,
               p_guion_detecta_y_salta, p_guion_no_salta_si_todo_va_bien,
               p_se_corta_al_parar):
        try:
            fn()
        except Exception:
            import traceback
            traceback.print_exc()
            R.append(False)
        print()
    fallos = R.count(False)
    print(f"{len(R)} comprobaciones, "
          + ("TODO OK" if not fallos else f"{fallos} FALLO(S)"))
    sys.exit(1 if fallos else 0)
