# -*- coding: utf-8 -*-
"""Combinaciones de teclas: shift+1, ctrl+f, etc.

Lo que importa es el orden: las de delante se pulsan y se mantienen mientras se
pulsa y suelta la ultima, y se sueltan al reves. Si no, el juego no lo lee como
un atajo.
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as G

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


def p_resolver():
    print("--- resolver_combo ---")
    casos = [
        ("1", ["1"]),
        ("shift+1", [G.keyboard.Key.shift, "1"]),
        ("ctrl+f", [G.keyboard.Key.ctrl, "f"]),
        ("ctrl+shift+intro", [G.keyboard.Key.ctrl, G.keyboard.Key.shift,
                              G.keyboard.Key.enter]),
        ("SHIFT+2", [G.keyboard.Key.shift, "2"]),
        ("alt+tab", [G.keyboard.Key.alt, G.keyboard.Key.tab]),
        ("mayus+3", [G.keyboard.Key.shift, "3"]),
    ]
    for texto, esperado in casos:
        try:
            got = G.resolver_combo(texto)
            check(texto, got == esperado, f"{got}")
        except ValueError as exc:
            check(texto, False, str(exc))


def p_resolver_errores():
    print("--- combinaciones que no valen ---")
    for texto, esperado in (("shift+teclamala", "no conozco la tecla"),
                            ("", "no es ninguna tecla"),
                            ("+", "no es ninguna tecla"),
                            ("a+b+c+d+e", "demasiadas teclas")):
        try:
            G.resolver_combo(texto)
            check(repr(texto), False, "no lanzo error")
        except ValueError as exc:
            check(repr(texto), esperado in str(exc), str(exc))


def p_analisis():
    print("--- el guion las acepta ---")
    pasos, errs = G.Script.parse("tecla shift+1\npulsar ctrl+f\nparar", {})
    check("sin errores", not errs, str(errs))
    check("2 teclas + parar", len(pasos) == 3, str(len(pasos)))
    d = G.Script.describe(pasos)
    check("lo explica como combinacion", "shift + 1 a la vez" in d[0], d[0])
    check("y la simple sigue igual",
          "pulsa la tecla" in G.Script.describe(
              G.Script.parse("tecla espacio\nparar", {})[0])[0])
    errs2 = G.Script.parse("tecla shift+teclamala", {})[1]
    check("caza la tecla mala del combo",
          any("no conozco la tecla" in e for e in errs2), str(errs2))


def p_orden_de_pulsacion():
    print("--- el orden: shift abajo, 1 abajo, 1 arriba, shift arriba ---")
    lineas = []
    s = G.Script(lineas.append, {})
    s.sound = False
    s.keyboard = TecladoEspia()
    errs = s.load("tecla shift+1\nparar")
    check("analiza", not errs, str(errs))
    s.start()
    t0 = time.perf_counter()
    while s.running and time.perf_counter() - t0 < 5:
        time.sleep(0.01)
    s.stop()
    s._thread.join(timeout=2.0)
    ops = s.keyboard.acciones
    sh = str(G.keyboard.Key.shift)
    esperado = [("press", sh), ("press", "1"), ("release", "1"),
                ("release", sh)]
    check("la secuencia exacta", ops == esperado, f"{ops}")


def p_orden_tres_teclas():
    print("--- con tres: ctrl, shift, f y al reves al soltar ---")
    lineas = []
    s = G.Script(lineas.append, {})
    s.sound = False
    s.keyboard = TecladoEspia()
    s.load("tecla ctrl+shift+f\nparar")
    s.start()
    t0 = time.perf_counter()
    while s.running and time.perf_counter() - t0 < 5:
        time.sleep(0.01)
    s.stop()
    s._thread.join(timeout=2.0)
    ops = s.keyboard.acciones
    ct, sh = str(G.keyboard.Key.ctrl), str(G.keyboard.Key.shift)
    esperado = [("press", ct), ("press", sh), ("press", "f"),
                ("release", "f"), ("release", sh), ("release", ct)]
    check("se sueltan al reves", ops == esperado, f"{ops}")


def p_simple_sigue_funcionando():
    print("--- una tecla suelta sigue haciendo press y release ---")
    lineas = []
    s = G.Script(lineas.append, {})
    s.sound = False
    s.keyboard = TecladoEspia()
    s.load("tecla espacio\nparar")
    s.start()
    t0 = time.perf_counter()
    while s.running and time.perf_counter() - t0 < 5:
        time.sleep(0.01)
    s.stop()
    s._thread.join(timeout=2.0)
    ops = s.keyboard.acciones
    esperado = [("press", str(G.keyboard.Key.space)),
                ("release", str(G.keyboard.Key.space))]
    check("press y release, nada mas", ops == esperado, f"{ops}")


if __name__ == "__main__":
    for fn in (p_resolver, p_resolver_errores, p_analisis,
               p_orden_de_pulsacion, p_orden_tres_teclas,
               p_simple_sigue_funcionando):
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
