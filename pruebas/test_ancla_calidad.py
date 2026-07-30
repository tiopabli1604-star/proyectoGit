# -*- coding: utf-8 -*-
"""Que la vista sirva como referencia, y que no se pierda movimiento al arrancar."""
import os
import sys
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


def instalar(img):
    def grab():
        return np.ascontiguousarray(img), MON
    G.Finder.grab_screen = staticmethod(grab)


def p_vista_lisa():
    print("--- una vista lisa se rechaza ---")
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = (140, 110, 90)                 # cielo de un color
    instalar(img)
    ancla, pos = G.capturar_ancla()
    sirve, motivo = G.evaluar_ancla(ancla, pos)
    check("no sirve", not sirve, motivo)
    check("y dice que es lisa", "lisa" in motivo, motivo)


def p_vista_repetitiva():
    print("--- una pared de ladrillos iguales se rechaza ---")
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = (90, 90, 110)
    for y in range(0, H, 30):               # patron perfectamente repetido
        for x in range(0, W, 60):
            off = 30 if (y // 30) % 2 else 0
            cv2.rectangle(img, (x + off + 2, y + 2),
                          (x + off + 56, y + 26), (120, 120, 150), -1)
    instalar(img)
    ancla, pos = G.capturar_ancla()
    sirve, motivo = G.evaluar_ancla(ancla, pos)
    check("no sirve", not sirve, motivo)
    check("y dice que se repite", "se repite" in motivo, motivo)


def p_vista_buena():
    print("--- una vista con detalle variado sirve ---")
    rng = np.random.RandomState(7)
    img = rng.randint(40, 220, (H, W, 3)).astype(np.uint8)
    img = cv2.GaussianBlur(img, (7, 7), 0)
    for _ in range(80):
        x, y = rng.randint(0, W - 80), rng.randint(0, H - 80)
        c = tuple(int(v) for v in rng.randint(0, 255, 3))
        cv2.circle(img, (x, y), rng.randint(10, 40), c, -1)
    instalar(img)
    ancla, pos = G.capturar_ancla()
    sirve, motivo = G.evaluar_ancla(ancla, pos)
    check("sirve", sirve, motivo)
    check("y da el contraste y el parecido con otros sitios",
          "contraste" in motivo and "parecido" in motivo, motivo)


def p_no_se_pierde_movimiento():
    print("--- al arrancar no se pierden los primeros movimientos ---")
    rng = np.random.RandomState(3)
    img = rng.randint(0, 255, (H, W, 3)).astype(np.uint8)
    instalar(img)
    rec = G.Recorder()
    rec.relative = True
    rec.start()
    check("el raw input arranco", rec.raw_error is None, str(rec.raw_error))
    # inyectar inmediatamente, sin dar tregua
    for _ in range(6):
        G.move_relative(10, 0)
        time.sleep(0.015)
    time.sleep(0.35)
    ev = rec.stop()
    mr = [e for e in ev if e["e"] == "mr"]
    check("se han grabado los 6 movimientos", len(mr) == 6,
          f"{len(mr)} de 6")
    check("y el primero cae casi en el instante 0",
          bool(mr) and mr[0]["t"] < 0.10, f"t={mr[0]['t']:.3f}" if mr else "-")
    check("con el ancla ya tomada", rec.ancla is not None)


def p_ancla_es_de_antes_de_moverse():
    print("--- el ancla es la vista de justo al pulsar, no la de despues ---")
    rng = np.random.RandomState(11)
    mundo = rng.randint(0, 255, (H * 2, W, 3)).astype(np.uint8)
    est = {"y": 0}

    def grab():
        return np.ascontiguousarray(mundo[est["y"]:est["y"] + H, :]), MON
    G.Finder.grab_screen = staticmethod(grab)

    rec = G.Recorder()
    rec.relative = True
    rec.start()
    ancla = rec.ancla.copy()
    est["y"] = 400                      # como si se hubiera movido la camara
    time.sleep(0.1)
    rec.stop()
    # el ancla debe coincidir con la vista de ANTES, no con la de ahora
    est["y"] = 0
    loc, s_antes = G.localizar_ancla(ancla)
    est["y"] = 400
    loc2, s_ahora = G.localizar_ancla(ancla)
    check("coincide con la vista inicial", s_antes > 0.95, f"{s_antes:.3f}")
    check("y no con la de despues", s_ahora < 0.9, f"{s_ahora:.3f}")


if __name__ == "__main__":
    for fn in (p_vista_lisa, p_vista_repetitiva, p_vista_buena,
               p_no_se_pierde_movimiento, p_ancla_es_de_antes_de_moverse):
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
