# -*- coding: utf-8 -*-
"""Un golem_config.json de la version anterior no debe dejarte en el modo viejo.

Levanta la App de verdad con Tk, con un config falso al lado, y comprueba que
migra una sola vez y que no pisa una eleccion posterior del usuario.
"""
import json
import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as G

R = []


def check(nombre, ok, extra=""):
    R.append(ok)
    print(f"  {'OK  ' if ok else 'FALLO'} {nombre}{(' — ' + extra) if extra else ''}")


def con_config(cfg):
    """Escribe un config, abre la App sin mostrarla y devuelve (app, root)."""
    with open(G.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    root = tk.Tk()
    root.withdraw()
    app = G.App(root)
    return app, root


def cerrar(app, root):
    try:
        if app._hotkey_listener:
            app._hotkey_listener.stop()
    except Exception:
        pass
    root.destroy()


VIEJO = {"mode": "color", "hue": "48", "hue_tol": "12", "sat": "40",
         "val": "90", "area": "80", "roi_left": "41", "roi_right": "59",
         "roi_top": "31", "roi_bottom": "52"}


def p_migra():
    print("--- config viejo (mode=color, sin 'v') ---")
    app, root = con_config(VIEJO)
    try:
        check("pasa a modo unico", app.finder.mode == "unico",
              app.finder.mode)
        check("avisa de la migracion", app._migrado is True)
        check("conserva la zona que tenia marcada",
              (round(app.finder.roi_left, 2), round(app.finder.roi_right, 2))
              == (0.41, 0.59),
              f"{app.finder.roi_left}..{app.finder.roi_right}")
        app._save_config()
    finally:
        cerrar(app, root)

    with open(G.CONFIG_PATH, encoding="utf-8") as f:
        guardado = json.load(f)
    check("al guardar deja v=2", guardado.get("v") == 2, str(guardado.get("v")))
    check("y deja el modo unico", guardado.get("mode") == "unico",
          str(guardado.get("mode")))


def p_no_repite():
    print("--- config ya migrado: si el usuario elige color, se respeta ---")
    cfg = dict(VIEJO)
    cfg["v"] = 2
    app, root = con_config(cfg)
    try:
        check("respeta mode=color con v=2", app.finder.mode == "color",
              app.finder.mode)
        check("y no dice que haya migrado", app._migrado is False)
    finally:
        cerrar(app, root)


def p_plantilla_se_respeta():
    print("--- un config en modo plantilla no se toca ---")
    cfg = dict(VIEJO)
    cfg["mode"] = "plantilla"
    app, root = con_config(cfg)
    try:
        check("sigue en plantilla", app.finder.mode == "plantilla",
              app.finder.mode)
        check("no migra", app._migrado is False)
    finally:
        cerrar(app, root)


def p_con_bom():
    """El Notepad de Windows guarda con BOM; no se pueden perder los ajustes."""
    print("--- config guardado con BOM (Notepad) ---")
    cfg = dict(VIEJO)
    cfg["v"] = 2
    cfg["area"] = "123"
    with open(G.CONFIG_PATH, "w", encoding="utf-8-sig") as f:
        json.dump(cfg, f)
    root = tk.Tk()
    root.withdraw()
    app = G.App(root)
    try:
        check("lee el config pese al BOM", app.finder.min_area == 123,
              f"area={app.finder.min_area} (esperado 123)")
        check("y conserva la zona",
              round(app.finder.roi_left, 2) == 0.41, str(app.finder.roi_left))
    finally:
        cerrar(app, root)


def p_config_roto():
    print("--- config corrupto: avisa y sigue con los de fabrica ---")
    with open(G.CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write("{esto no es json")
    root = tk.Tk()
    root.withdraw()
    app = G.App(root)
    try:
        check("arranca en unico", app.finder.mode == "unico", app.finder.mode)
        app._drain_log(reprogramar=False)   # log() encola; hay que volcar
        texto = app.txt_log.get("1.0", "end")
        check("lo dice en el registro", "No pude leer" in texto,
              str([l for l in texto.splitlines() if "No pude" in l][:1]))
    finally:
        cerrar(app, root)


def p_sin_config():
    print("--- primera vez, sin config ---")
    if os.path.exists(G.CONFIG_PATH):
        os.remove(G.CONFIG_PATH)
    root = tk.Tk()
    root.withdraw()
    app = G.App(root)
    try:
        check("arranca en unico", app.finder.mode == "unico", app.finder.mode)
        check("sin aviso de migracion", app._migrado is False)
        check("y sin zona marcada",
              (app.finder.roi_left, app.finder.roi_right,
               app.finder.roi_top, app.finder.roi_bottom) == (0.0, 1.0, 0.0, 1.0))
    finally:
        cerrar(app, root)


if __name__ == "__main__":
    respaldo = None
    if os.path.exists(G.CONFIG_PATH):
        with open(G.CONFIG_PATH, encoding="utf-8") as f:
            respaldo = f.read()
    try:
        for fn in (p_migra, p_no_repite, p_plantilla_se_respeta, p_con_bom,
                   p_config_roto, p_sin_config):
            fn()
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
