# -*- coding: utf-8 -*-
"""Reconstruccion de la captura real: Plains + cofre vacio + cristal encantado.

La hierba de Plains es de un verde-amarillento casi del mismo tono que el
cristal, asi que el color por si solo no puede separarlos. Estas pruebas
comprueban que lo hacen los filtros de contexto y de zona.
"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as G

W, H = 1920, 1080
MON = {"left": 0, "top": 0, "width": W, "height": H}

# medidas tomadas de la captura del usuario (reescaladas a 1920x1080)
GUI = (787, 310, 1133, 748)        # ventana del captcha
COFRE = (800, 340, 1125, 560)      # solo la rejilla del cofre, sin Inventory
CRISTAL = (1032, 539)              # casilla donde salio el cristal
LADO = 22

HSV_CRISTAL = [45, 105, 228]       # lima palido translucido
HSV_HIERBA = [42, 130, 200]        # hierba seca de Plains: casi el mismo tono


def col(hsv):
    return cv2.cvtColor(np.uint8([[hsv]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()


def escena(fase=0, con_captcha=True):
    img = np.zeros((H, W, 3), np.uint8)
    img[:H // 3, :] = (235, 190, 120)                 # cielo
    img[H // 3:, :] = col(HSV_HIERBA)                 # campo de hierba
    rng = np.random.RandomState(7)                    # manchas de hierba
    for _ in range(400):
        x = rng.randint(0, W - 40)
        y = rng.randint(H // 3, H - 30)
        s = rng.randint(14, 34)
        tono = int(HSV_HIERBA[0] + rng.randint(-4, 5))
        img[y:y + s, x:x + s] = col([tono, HSV_HIERBA[1] + rng.randint(-30, 30),
                                     HSV_HIERBA[2] + rng.randint(-40, 30)])
    # HUD: texto verde sobre fondo oscuro semitransparente (Biome: Plains)
    cv2.rectangle(img, (28, 140), (215, 172), (35, 35, 35), -1)
    cv2.putText(img, "Plains", (118, 166), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                col([45, 200, 210]), 2)
    # marcador lateral con texto verde, tambien sobre oscuro
    cv2.rectangle(img, (1680, 385, 235, 60), (40, 40, 40), -1)
    cv2.putText(img, "GENS", (1700, 425), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                col([45, 210, 200]), 2)

    if con_captcha:
        x0, y0, x1, y1 = GUI
        cv2.rectangle(img, (x0, y0), (x1, y1), (198, 198, 198), -1)
        cv2.rectangle(img, (x0, y0), (x1, y1), (85, 85, 85), 2)
        for sy in range(340, 560, 36):                # casillas vacias
            for sx in range(800, 1120, 36):
                cv2.rectangle(img, (sx, sy), (sx + 32, sy + 32),
                              (139, 139, 139), -1)
        # fila del inventario del jugador, con objetos de colores
        for i, hsv in enumerate(([120, 200, 200], [10, 220, 210],
                                 [90, 180, 190], [45, 190, 215])):
            cv2.rectangle(img, (810 + i * 40, 600), (836 + i * 40, 626),
                          col(hsv), -1)
        # el cristal
        cx, cy = CRISTAL
        cv2.rectangle(img, (cx - LADO // 2, cy - LADO // 2),
                      (cx + LADO // 2, cy + LADO // 2), col(HSV_CRISTAL), -1)
        # brillo del encantamiento barriendo el sprite
        capa = img.copy()
        d = int((fase % 4) * LADO / 4) - LADO // 2
        pts = np.array([[cx - LADO // 2 + d, cy - LADO // 2],
                        [cx - LADO // 2 + d + 8, cy - LADO // 2],
                        [cx - LADO // 2 + d + 16, cy + LADO // 2],
                        [cx - LADO // 2 + d + 8, cy + LADO // 2]], np.int32)
        cv2.fillPoly(capa, [pts], col([140, 190, 250]))
        m = np.zeros((H, W), np.uint8)
        cv2.rectangle(m, (cx - LADO // 2, cy - LADO // 2),
                      (cx + LADO // 2, cy + LADO // 2), 255, -1)
        img = np.where(m[:, :, None] > 0,
                       cv2.addWeighted(img, 0.3, capa, 0.7, 0), img)
    return np.ascontiguousarray(img)


def instalar(escenas):
    est = {"i": 0}

    def grab():
        img = escenas[est["i"] % len(escenas)]
        est["i"] += 1
        return img, MON
    G.Finder.grab_screen = staticmethod(grab)


def calibrado():
    """Como queda tras pulsar F8 sobre el cristal (modo de tono concreto)."""
    f = G.Finder()
    f.mode = "color"
    h, s, v = HSV_CRISTAL
    f.hue, f.hue_tol = h, 12
    f.sat_min, f.val_min = max(25, s - 60), max(50, v - 60)
    f.frames, f.frame_gap = 3, 0.0
    return f


def zona_cofre(f):
    x0, y0, x1, y1 = COFRE
    f.roi_left, f.roi_right = x0 / W, x1 / W
    f.roi_top, f.roi_bottom = y0 / H, y1 / H


def acierta(c):
    return (bool(c) and abs(c[0][0] - CRISTAL[0]) < 14
            and abs(c[0][1] - CRISTAL[1]) < 14)


def p_sin_nada():
    """Informativo: sin ningun filtro, para ver que clicaria la version que
    fallaba en el servidor real."""
    print("--- toda la pantalla, sin ningun filtro (lo que fallaba) ---")
    instalar([escena(k) for k in range(3)])
    f = calibrado()
    f.on_gui = False
    f.min_area, f.max_area, f.max_side = 1, 10_000_000, 10_000
    c = f.candidates()
    print(f"  {len(c)} candidato(s); el nº1 en {c[0][:3] if c else 'nada'}")
    print(f"  el cristal esta en {CRISTAL} -> "
          f"{'lo clicaria' if acierta(c) else 'CLICARIA OTRA COSA'}")
    return 0


def en_gui(c):
    x0, y0, x1, y1 = GUI
    return x0 <= c[0] <= x1 and y0 <= c[1] <= y1


def p_filtro_contexto():
    """El filtro de contexto debe borrar el paisaje, pero NO puede separar el
    cristal de otro objeto del mismo tono en el inventario del jugador: los dos
    estan sobre gris. Por eso hace falta la zona."""
    print("--- toda la pantalla, con filtro de interfaz ---")
    instalar([escena(k) for k in range(3)])
    f = calibrado()
    f.on_gui = True
    # sin filtros de tamaño, para que el contexto sea la unica puerta
    f.min_area, f.max_area, f.max_side = 1, 10_000_000, 10_000
    c = f.candidates()
    fuera = [x for x in c if not en_gui(x)]
    esta = any(abs(x[0] - CRISTAL[0]) < 14 and abs(x[1] - CRISTAL[1]) < 14
               for x in c)
    ok = not fuera and esta
    print(f"  {'OK  ' if ok else 'FALLO'} {len(c)} candidato(s), "
          f"{len(fuera)} fuera de la interfaz (debe ser 0); "
          f"el cristal {'esta entre ellos' if esta else 'SE HA PERDIDO'}")
    for r in f.rejects:
        print(f"       {r}")
    if not acierta(c):
        print("       nota: el nº1 no es el cristal, es el objeto del mismo")
        print("       tono en el inventario -> el color no basta, hace falta F2")
    return 0 if ok else 1


def p_hud_oscuro():
    """Texto verde del HUD sobre fondo oscuro: lo tiene que matar el contexto,
    no el filtro de area, asi que aqui desactivo el de area."""
    print("--- texto verde del HUD sobre fondo oscuro ---")
    instalar([escena(k, con_captcha=False) for k in range(3)])
    f = calibrado()
    f.on_gui = True
    f.min_area, f.max_area, f.max_side = 1, 10_000_000, 10_000
    c = f.candidates()
    hud = [r for r in f.rejects if "interfaz" in r]
    ok = len(c) == 0 and len(hud) >= 2      # el campo de hierba y el texto
    print(f"  {'OK  ' if ok else 'FALLO'} sin captcha en pantalla: "
          f"{len(c)} candidato(s) (debe ser 0), "
          f"{len(hud)} descartado(s) por contexto (debe ser >=2)")
    for r in f.rejects:
        print(f"       {r}")
    if c:
        print(f"       se cuela: {c[:3]}")
    return 0 if ok else 1


def p_zona():
    print("--- zona marcada con F2 solo en la rejilla del cofre ---")
    fallos = 0
    instalar([escena(k) for k in range(3)])
    f = calibrado()
    zona_cofre(f)
    f.on_gui = False          # a propósito: la zona sola debe bastar
    c = f.candidates()
    ok = acierta(c) and len(c) == 1
    fallos += 0 if ok else 1
    print(f"  {'OK  ' if ok else 'FALLO'} con captcha: {len(c)} candidato(s), "
          f"nº1 en {c[0][:2] if c else 'nada'} (sin filtro de interfaz)")

    instalar([escena(k, con_captcha=False) for k in range(3)])
    f = calibrado()
    zona_cofre(f)
    f.on_gui = False
    c = f.candidates()
    ok2 = len(c) == 0
    fallos += 0 if ok2 else 1
    print(f"  {'OK  ' if ok2 else 'FALLO'} sin captcha, mirando solo esa zona "
          f"(hay hierba detras): {len(c)} candidato(s), debe ser 0")
    if c:
        print(f"       se cuela: {c[:3]}")

    instalar([escena(k) for k in range(3)])
    f = calibrado()
    zona_cofre(f)
    f.on_gui = True
    c = f.candidates()
    ok3 = acierta(c) and len(c) == 1
    fallos += 0 if ok3 else 1
    print(f"  {'OK  ' if ok3 else 'FALLO'} zona + filtro de interfaz juntos: "
          f"{len(c)} candidato(s) en {c[0][:2] if c else 'nada'}")
    return fallos


def p_todas_las_fases():
    print("--- las 4 fases del brillo, zona + contexto ---")
    fallos = 0
    for inicio in range(4):
        instalar([escena((inicio + k) % 4) for k in range(3)])
        f = calibrado()
        zona_cofre(f)
        c = f.candidates()
        ok = acierta(c)
        fallos += 0 if ok else 1
        print(f"  fase {inicio}: {'OK  ' if ok else 'FALLO'} "
              f"{c[0][:3] if c else 'nada'}")
    return fallos


if __name__ == "__main__":
    total = 0
    for fn in (p_sin_nada, p_filtro_contexto, p_hud_oscuro, p_zona,
               p_todas_las_fases):
        total += fn()
        print()
    print("TODO OK" if total == 0 else f"{total} FALLO(S)")
    sys.exit(1 if total else 0)
