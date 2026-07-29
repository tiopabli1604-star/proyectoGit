# -*- coding: utf-8 -*-
"""Motor de guiones: analisis de errores y ejecucion paso a paso.

El ejecutor no clica de verdad: se le cambia click_at por un espia, y
grab_screen por escenas sinteticas que se pueden encadenar para simular que el
objetivo aparece al cabo de unos escaneos.
"""
import json
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as G

W, H = 800, 600
MON = {"left": 0, "top": 0, "width": W, "height": H}

R = []


def check(nombre, ok, extra=""):
    R.append(ok)
    print(f"  {'OK  ' if ok else 'FALLO'} {nombre}{(' — ' + extra) if extra else ''}")


# ------------------------------------------------------------ escenas
def vacia():
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = (30, 30, 30)
    cv2.rectangle(img, (200, 150), (600, 450), (200, 200, 200), -1)
    return img


def con_objeto(cx=400, cy=300):
    img = vacia()
    bgr = cv2.cvtColor(np.uint8([[[45, 200, 220]]]), cv2.COLOR_HSV2BGR)[0, 0]
    cv2.rectangle(img, (cx - 13, cy - 13), (cx + 13, cy + 13),
                  bgr.tolist(), -1)
    return img


def instalar(secuencia):
    """secuencia: lista de imagenes; la ultima se repite para siempre."""
    est = {"i": 0}

    def grab():
        img = secuencia[min(est["i"], len(secuencia) - 1)]
        est["i"] += 1
        return img, MON
    G.Finder.grab_screen = staticmethod(grab)
    return est


OBJETIVOS = {
    "cosa": {"mode": "unico", "sat_min": 40, "val_min": 90, "min_area": 80,
             "max_area": 40000, "max_side": 80, "frames": 1, "on_gui": True,
             "roi_left": 0.2, "roi_right": 0.8, "roi_top": 0.2,
             "roi_bottom": 0.8},
    "otra": {"mode": "unico", "sat_min": 40, "val_min": 90, "min_area": 80,
             "max_area": 40000, "max_side": 80, "frames": 1, "on_gui": True,
             "roi_left": 0.0, "roi_right": 1.0, "roi_top": 0.0,
             "roi_bottom": 1.0},
}


class Espia:
    """Sustituye a click_at y apunta los clics en vez de hacerlos."""

    def __init__(self):
        self.clics = []
        self._orig = G.click_at

    def __enter__(self):
        def falso(mouse, x, y, boton="left", doble=False, restore=True,
                  move_delay=0.20):
            self.clics.append((x, y, boton, doble))
        G.click_at = falso
        # el modulo guarda la referencia en tiempo de llamada, asi que basta
        return self

    def __exit__(self, *a):
        G.click_at = self._orig
        return False


def guion(texto, objetivos=None, interval=0.02, confirmaciones=1):
    lineas = []
    s = G.Script(lineas.append, objetivos if objetivos is not None else OBJETIVOS)
    s.interval = interval
    s.confirmaciones = confirmaciones
    s.sound = False
    errores = s.load(texto)
    return s, errores, lineas


def correr(s, limite=8.0):
    """Corre hasta que acabe solo. Devuelve True si se quedo colgado."""
    s.start()
    t0 = time.perf_counter()
    while s.running and time.perf_counter() - t0 < limite:
        time.sleep(0.01)
    colgado = s.running
    s.stop()
    s._thread.join(timeout=2.0)     # que el hilo acabe antes de mirar los clics
    return colgado


# ------------------------------------------------------------ analisis
def p_errores():
    print("--- el analisis caza los errores y dice la linea ---")
    casos = [
        ("buscarr cosa", "no conozco la instrucción"),
        ("buscar", "necesita el nombre"),
        ("buscar fantasma", "no hay un objetivo llamado"),
        ("buscar cosa\nclic\nclic doble raro", "no entiendo 'raro'"),
        ("esperar", "necesita los segundos"),
        ("esperar mucho", "no es un número de segundos"),
        ("tecla\n", "necesita una tecla"),
        ("tecla teclaquenoexiste", "no conozco la tecla"),
        ("clic", "sin un 'buscar' antes"),
        ("ir", "necesita un número de paso"),
        ("buscar cosa\nir 99", "no existe"),
        ("repetir 0", "solo va un número de veces"),
        ("repetir dos", "solo va un número de veces"),
        ("repetir 3 4", "solo va un número de veces"),
        ("parar 3", "no lleva nada detrás"),
        ("macro noexiste.macro.json", "no encuentro"),
        ("buscar cosa si_falla repetir", "no sirve sin un límite"),
        ("buscar cosa 5 si_falla ir", "necesita un número de paso"),
        ("buscar cosa 5 si_falla loquesea", "tras 'si_falla'"),
        ("escribir", "sin texto"),
        ("", "está vacío"),
        ("# solo un comentario", "está vacío"),
    ]
    for texto, esperado in casos:
        _, errs, _ = guion(texto)
        ok = any(esperado in e for e in errs)
        check(repr(texto)[:38], ok,
              (errs[0] if errs else "no dio ningun error")[:74])


def p_bucle_sin_freno():
    print("--- un bucle que no espera nada se rechaza ---")
    _, errs, _ = guion("buscar cosa\nclic\nrepetir")
    check("con 'buscar' dentro, se acepta", not errs, str(errs[:1]))
    _, errs, _ = guion("pitar\nrepetir")
    check("sin nada que espere, se rechaza",
          any("sin freno" in e for e in errs), str(errs[:1]))
    _, errs, _ = guion("pitar\nesperar 1\nir 1")
    check("con 'esperar' y salto atras, se acepta", not errs, str(errs[:1]))


def p_describe():
    print("--- el guion se explica en palabras ---")
    pasos, errs = G.Script.parse(
        "buscar cosa 30 si_falla repetir\nclic doble\nesperar 2\nrepetir",
        OBJETIVOS)
    check("sin errores", not errs, str(errs))
    d = G.Script.describe(pasos)
    check("4 pasos numerados", len(d) == 4, " / ".join(d)[:100])
    check("explica el si_falla", "vuelve al paso 1" in d[0], d[0])
    check("explica el doble clic", "doble clic" in d[1], d[1])


def p_comentarios():
    print("--- comentarios y lineas en blanco ---")
    pasos, errs = G.Script.parse(
        "# esto es un comentario\n\nbuscar cosa   # al final tambien\nclic\n",
        OBJETIVOS)
    check("los ignora", not errs and len(pasos) == 2,
          f"{len(pasos)} pasos, {errs}")
    pasos, errs = G.Script.parse("escribir hola # esto NO es comentario",
                                 OBJETIVOS)
    check("pero 'escribir' se lleva el texto literal",
          not errs and pasos[0]["texto"] == "hola # esto NO es comentario",
          pasos[0]["texto"] if pasos else str(errs))


# ------------------------------------------------------------ ejecucion
def p_ejecuta_basico():
    print("--- busca, clica y para ---")
    instalar([con_objeto()])
    s, errs, log = guion("buscar cosa\nclic\nparar")
    check("analiza bien", not errs, str(errs))
    with Espia() as e:
        colgado = correr(s)
    check("no se queda colgado", not colgado)
    check("clico una vez", len(e.clics) == 1, str(e.clics))
    check("y en el sitio del objeto",
          bool(e.clics) and abs(e.clics[0][0] - 400) < 14
          and abs(e.clics[0][1] - 300) < 14, str(e.clics[:1]))


def p_espera_a_que_aparezca():
    print("--- espera a que aparezca, no clica antes de tiempo ---")
    instalar([vacia()] * 5 + [con_objeto()])
    s, errs, log = guion("buscar cosa 5\nclic\nparar")
    with Espia() as e:
        correr(s)
    check("acaba clicando una vez", len(e.clics) == 1, str(e.clics))
    check("el registro dice que lo vio",
          any("visto 'cosa'" in l for l in log), str(log[:2]))


def p_timeout_parar():
    print("--- si no aparece y la politica es parar ---")
    instalar([vacia()])
    s, errs, log = guion("buscar cosa 0.3\nclic\nparar")
    with Espia() as e:
        colgado = correr(s)
    check("no cuelga", not colgado)
    check("no clica nada", not e.clics, str(e.clics))
    check("lo dice en el registro",
          any("no apareció" in l for l in log), str(log[:2]))


def p_timeout_si_falla_ir():
    print("--- si no aparece, salta a otro paso ---")
    instalar([vacia()])
    s, errs, log = guion("buscar cosa 0.3 si_falla ir 3\n"
                         "clic\n"
                         "pitar\n"
                         "parar")
    check("analiza bien", not errs, str(errs))
    with Espia() as e:
        correr(s)
    check("no clica", not e.clics, str(e.clics))
    check("llego al paso 3 y paro", any("4. parar" in l for l in log),
          str(log))


def p_bucle_de_vigilancia():
    print("--- bucle: cada vez que aparece, clica (el caso del captcha) ---")
    # aparece, desaparece, vuelve a aparecer
    sec = [con_objeto(300, 250)] * 2 + [vacia()] * 2 + [con_objeto(500, 350)] * 8
    instalar(sec)
    s, errs, log = guion("buscar cosa\nclic\ndesaparecer cosa 2\nrepetir")
    check("analiza bien", not errs, str(errs))
    with Espia() as e:
        s.start()
        t0 = time.perf_counter()
        while len(e.clics) < 2 and time.perf_counter() - t0 < 8:
            time.sleep(0.01)
        s.stop()
        time.sleep(0.05)
    check("clico dos veces, en los dos sitios", len(e.clics) >= 2,
          str(e.clics[:3]))
    if len(e.clics) >= 2:
        check("primero en el de arriba", abs(e.clics[0][0] - 300) < 14,
              str(e.clics[0]))
        check("y luego en el de abajo", abs(e.clics[1][0] - 500) < 14,
              str(e.clics[1]))


def p_confirmaciones():
    print("--- exige verlo 2 escaneos seguidos (anti falso positivo) ---")
    # aparece un solo fotograma y desaparece: no debe clicar
    instalar([vacia(), con_objeto(), vacia(), vacia(), vacia(), vacia()])
    s, errs, log = guion("buscar cosa 0.4\nclic\nparar", confirmaciones=2)
    with Espia() as e:
        correr(s)
    check("un destello suelto no se clica", not e.clics, str(e.clics))

    instalar([vacia(), con_objeto(), con_objeto(), con_objeto()])
    s, errs, log = guion("buscar cosa 2\nclic\nparar", confirmaciones=2)
    with Espia() as e:
        correr(s)
    check("dos seguidos si", len(e.clics) == 1, str(e.clics))


def p_parar_a_mitad():
    print("--- se puede parar a mitad de una busqueda larga ---")
    instalar([vacia()])
    s, errs, log = guion("buscar cosa 30\nclic\nparar", interval=0.05)
    s.start()
    time.sleep(0.2)
    check("el hilo esta vivo antes de parar", s._thread.is_alive())
    t0 = time.perf_counter()
    s.stop()
    # stop() pone running=False al instante, asi que hay que esperar al HILO:
    # si se comprobara la bandera, la prueba pasaria sin demostrar nada.
    s._thread.join(timeout=3.0)
    tardanza = time.perf_counter() - t0
    check("el hilo muere en menos de 1 s",
          not s._thread.is_alive() and tardanza < 1.0, f"{tardanza:.2f} s")


def p_esperar_largo_se_corta():
    print("--- 'esperar 60' tambien se corta al parar ---")
    instalar([con_objeto()])
    s, errs, log = guion("buscar cosa\nesperar 60\nclic\nparar")
    with Espia() as e:
        s.start()
        t0 = time.perf_counter()
        while (not any("esperando" in l for l in log)
               and time.perf_counter() - t0 < 3):
            time.sleep(0.01)
        check("ha llegado al 'esperar 60'",
              any("esperando" in l for l in log), str(log))
        t1 = time.perf_counter()
        s.stop()
        s._thread.join(timeout=3.0)
        tardanza = time.perf_counter() - t1
        check("el hilo muere sin esperar los 60 s",
              not s._thread.is_alive() and tardanza < 1.0,
              f"{tardanza:.2f} s")
        check("y no llego a clicar", not e.clics, str(e.clics))


def p_dos_objetivos():
    print("--- dos objetivos distintos con zonas distintas ---")
    # objeto fuera de la zona de 'cosa' (esquina), dentro de la de 'otra'
    img = vacia()
    cv2.rectangle(img, (20, 20), (180, 130), (200, 200, 200), -1)
    bgr = cv2.cvtColor(np.uint8([[[45, 200, 220]]]), cv2.COLOR_HSV2BGR)[0, 0]
    cv2.rectangle(img, (87, 62), (113, 88), bgr.tolist(), -1)
    instalar([img])
    s, errs, log = guion("buscar cosa 0.3 si_falla ir 3\nparar\n"
                         "buscar otra 2\nclic\nparar")
    check("analiza bien", not errs, str(errs))
    with Espia() as e:
        correr(s)
    check("'cosa' no lo ve (esta fuera de su zona) y 'otra' si",
          len(e.clics) == 1 and abs(e.clics[0][0] - 100) < 14, str(e.clics))


def p_macro():
    print("--- paso 'macro' reproduce un archivo ---")
    ruta = os.path.join(G.APP_DIR, "_prueba_guion.macro.json")
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "events": [
            {"e": "mm", "t": 0.0, "x": 10, "y": 10},
            {"e": "mm", "t": 0.02, "x": 20, "y": 20},
        ]}, f)
    try:
        instalar([con_objeto()])
        s, errs, log = guion(f"buscar cosa\nmacro _prueba_guion.macro.json\n"
                             f"parar")
        check("analiza bien", not errs, str(errs))
        with Espia() as e:
            colgado = correr(s)
        check("no cuelga", not colgado)
        check("lo dice en el registro",
              any("reproduciendo" in l for l in log), str(log))
    finally:
        os.remove(ruta)


def p_clic_sin_posicion_en_ejecucion():
    print("--- 'clic' tras un si_falla que se salto el buscar ---")
    instalar([vacia()])
    # el analisis lo acepta (hay un buscar antes), pero en ejecucion la
    # posicion nunca se llego a fijar: debe parar, no reventar
    s, errs, log = guion("buscar cosa 0.3 si_falla ir 2\nclic\nparar")
    check("el analisis lo acepta", not errs, str(errs))
    with Espia() as e:
        colgado = correr(s)
    check("no clica y para sin reventar",
          not colgado and not e.clics
          and any("no hay ninguna posición" in l for l in log), str(log))


if __name__ == "__main__":
    for fn in (p_errores, p_bucle_sin_freno, p_describe, p_comentarios,
               p_ejecuta_basico, p_espera_a_que_aparezca, p_timeout_parar,
               p_timeout_si_falla_ir, p_bucle_de_vigilancia, p_confirmaciones,
               p_parar_a_mitad, p_esperar_largo_se_corta, p_dos_objetivos,
               p_macro, p_clic_sin_posicion_en_ejecucion):
        fn()
        print()
    fallos = R.count(False)
    print(f"{len(R)} comprobaciones, "
          + ("TODO OK" if not fallos else f"{fallos} FALLO(S)"))
    sys.exit(1 if fallos else 0)
