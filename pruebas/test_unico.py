# -*- coding: utf-8 -*-
"""Modo 'lo unico con color': sin calibrar el tono.

Dentro de un cofre las casillas vacias son gris puro, asi que basta con exigir
saturacion. Lo que se comprueba aqui es que eso funciona con cualquier tono, con
el brillo del encantamiento encima y sin tocar ni un ajuste.
"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as G

W, H = 1920, 1080
MON = {"left": 0, "top": 0, "width": W, "height": H}

GUI = (787, 310, 1133, 748)
COFRE = (800, 340, 1125, 560)
CRISTAL = (1032, 539)
LADO = 22

R = []


def check(nombre, ok, extra=""):
    R.append(ok)
    print(f"  {'OK  ' if ok else 'FALLO'} {nombre}{(' — ' + extra) if extra else ''}")


def col(hsv):
    return cv2.cvtColor(np.uint8([[hsv]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()


def escena(fase=0, con_captcha=True, hsv_obj=(45, 105, 228), encantado=True):
    img = np.zeros((H, W, 3), np.uint8)
    img[:H // 3, :] = (235, 190, 120)
    img[H // 3:, :] = col([42, 130, 200])
    rng = np.random.RandomState(7)
    for _ in range(400):
        x = rng.randint(0, W - 40)
        y = rng.randint(H // 3, H - 30)
        s = rng.randint(14, 34)
        img[y:y + s, x:x + s] = col([42 + rng.randint(-4, 5),
                                     130 + rng.randint(-30, 30),
                                     200 + rng.randint(-40, 30)])
    cv2.rectangle(img, (28, 140), (215, 172), (35, 35, 35), -1)
    cv2.putText(img, "Plains", (118, 166), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                col([45, 200, 210]), 2)

    if con_captcha:
        x0, y0, x1, y1 = GUI
        cv2.rectangle(img, (x0, y0), (x1, y1), (198, 198, 198), -1)
        cv2.rectangle(img, (x0, y0), (x1, y1), (85, 85, 85), 2)
        for sy in range(340, 560, 36):
            for sx in range(800, 1120, 36):
                cv2.rectangle(img, (sx, sy), (sx + 32, sy + 32),
                              (139, 139, 139), -1)
        for i, hsv in enumerate(([120, 200, 200], [10, 220, 210],
                                 [90, 180, 190], [45, 190, 215])):
            cv2.rectangle(img, (810 + i * 40, 600), (836 + i * 40, 626),
                          col(hsv), -1)
        cx, cy = CRISTAL
        cv2.rectangle(img, (cx - LADO // 2, cy - LADO // 2),
                      (cx + LADO // 2, cy + LADO // 2), col(list(hsv_obj)), -1)
        if encantado:
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


def recien_abierto():
    """Finder tal cual sale de fabrica, sin cuentagotas ni nada."""
    f = G.Finder()
    f.frame_gap = 0.0
    return f


def zona_cofre(f):
    x0, y0, x1, y1 = COFRE
    f.roi_left, f.roi_right = x0 / W, x1 / W
    f.roi_top, f.roi_bottom = y0 / H, y1 / H


def acierta(c):
    return (bool(c) and abs(c[0][0] - CRISTAL[0]) < 14
            and abs(c[0][1] - CRISTAL[1]) < 14)


def p_por_defecto():
    print("--- el modo de fabrica es 'unico' ---")
    check("Finder arranca en modo unico", G.Finder().mode == "unico",
          G.Finder().mode)


def p_sin_calibrar():
    print("--- sin tocar nada: zona + modo unico ---")
    instalar([escena(k) for k in range(3)])
    f = recien_abierto()
    zona_cofre(f)
    c = f.candidates()
    check("1 candidato y es el objetivo", acierta(c) and len(c) == 1,
          f"{len(c)} cand., {c[0][:3] if c else 'nada'}")


def p_cualquier_tono():
    print("--- da igual el color del objeto: no se calibra nada ---")
    for hsv, nombre in (((45, 105, 228), "lima palido"),
                        ((0, 230, 220), "rojo"),
                        ((120, 220, 230), "azul"),
                        ((15, 240, 200), "naranja"),
                        ((160, 200, 220), "rosa"),
                        ((90, 160, 210), "cian")):
        instalar([escena(k, hsv_obj=hsv) for k in range(3)])
        f = recien_abierto()
        zona_cofre(f)
        c = f.candidates()
        check(nombre, acierta(c) and len(c) == 1,
              f"{len(c)} cand., area {c[0][2] if c else 0}")


def p_fases_brillo():
    print("--- las 4 fases del brillo del encantamiento ---")
    areas = []
    for inicio in range(4):
        instalar([escena((inicio + k) % 4) for k in range(3)])
        f = recien_abierto()
        zona_cofre(f)
        c = f.candidates()
        areas.append(c[0][2] if c else 0)
        check(f"fase {inicio}", acierta(c) and len(c) == 1,
              f"area {c[0][2] if c else 0}")
    # el brillo del encantamiento tambien esta saturado, asi que en este modo
    # se suma a la mancha en vez de romperla: la variacion debe ser residual.
    # Para comparar: en modo de color puro bailaba 160 px^2 sobre 544.
    baile = max(areas) - min(areas)
    check("el area apenas baila entre fases", baile <= 0.05 * max(areas),
          f"{baile} px^2 de variacion sobre {max(areas)} "
          f"({100.0 * baile / max(areas):.1f}%)")


def p_cofre_vacio():
    print("--- cofre abierto pero sin el objeto: no debe clicar nada ---")
    img = escena(0)
    x0, y0, x1, y1 = COFRE
    cx, cy = CRISTAL
    cv2.rectangle(img, (cx - LADO, cy - LADO), (cx + LADO, cy + LADO),
                  (139, 139, 139), -1)
    instalar([img])
    f = recien_abierto()
    zona_cofre(f)
    c = f.candidates()
    check("0 candidatos", len(c) == 0, f"{len(c)}: {c[:2]}")


def p_sin_captcha():
    print("--- captcha cerrado, solo paisaje detras de la zona ---")
    instalar([escena(k, con_captcha=False) for k in range(3)])
    f = recien_abierto()
    zona_cofre(f)
    c = f.candidates()
    check("0 candidatos (la hierba no cuela)", len(c) == 0,
          f"{len(c)}: {[x[:3] for x in c[:2]]}")


def p_sin_zona_es_un_desastre():
    print("--- sin zona, el modo unico ve el mundo entero (de ahi el aviso) ---")
    instalar([escena(0)])
    f = recien_abierto()
    f.on_gui = False
    c = f.candidates()
    check("sin zona no acierta, y por eso se bloquea en F9", not acierta(c),
          f"{len(c)} cand., nº1 {c[0][:3] if c else 'nada'}")


def p_zona_toda_la_ventana():
    print("--- zona floja: toda la ventana, con el inventario dentro ---")
    instalar([escena(k) for k in range(3)])
    f = recien_abierto()
    x0, y0, x1, y1 = GUI
    f.roi_left, f.roi_right = x0 / W, x1 / W
    f.roi_top, f.roi_bottom = y0 / H, y1 / H
    c = f.candidates()
    check("aparece mas de un candidato, como debe avisar el registro",
          len(c) > 1, f"{len(c)} cand., nº1 {c[0][:2] if c else 'nada'}")


def p_informe_zona():
    print("--- el informe de la zona dice lo que hay dentro ---")
    instalar([escena(0)] * 2)
    f = recien_abierto()
    zona_cofre(f)
    txt = " | ".join(f.zone_report())
    check("dice el tamano de la zona", "325x220" in txt or "px desde" in txt,
          txt[:70])
    check("ve el fondo gris (saturacion baja)",
          "saturación 0" in txt or "saturación 1" in txt
          or "saturación 2" in txt, [l for l in f.zone_report()
                                     if "fondo" in l][0])
    check("encuentra algo con color dentro",
          "lo más coloreado" in txt,
          [l for l in f.zone_report() if "coloreado" in l or "nada con" in l][0])

    instalar([escena(0, con_captcha=False)] * 2)
    f = recien_abierto()
    zona_cofre(f)
    inf = f.zone_report()
    check("y avisa cuando la zona cae sobre el paisaje",
          any("no parece" in l for l in inf),
          [l for l in inf if "fondo" in l][0])


if __name__ == "__main__":
    for fn in (p_por_defecto, p_sin_calibrar, p_cualquier_tono, p_fases_brillo,
               p_cofre_vacio, p_sin_captcha, p_sin_zona_es_un_desastre,
               p_zona_toda_la_ventana, p_informe_zona):
        fn()
        print()
    fallos = R.count(False)
    print(f"{len(R)} comprobaciones, "
          + ("TODO OK" if not fallos else f"{fallos} FALLO(S)"))
    sys.exit(1 if fallos else 0)
