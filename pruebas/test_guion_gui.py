# -*- coding: utf-8 -*-
"""Cableado de la pestana del guion: objetivos, persistencia y guardas.

Levanta la App con Tk de verdad (sin mostrarla) porque casi todo lo que se
rompe aqui son referencias entre widgets y el dict de objetivos compartido con
el Script.
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
    extra = str(extra) if extra else ""
    print(f"  {'OK  ' if ok else 'FALLO'} {nombre}{(' — ' + extra) if extra else ''}")


def abrir(cfg=None):
    if cfg is None:
        if os.path.exists(G.CONFIG_PATH):
            os.remove(G.CONFIG_PATH)
    else:
        with open(G.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    root = tk.Tk()
    root.withdraw()
    app = G.App(root)
    return app, root


def cerrar(app, root):
    try:
        app.script.stop()
        if app._hotkey_listener:
            app._hotkey_listener.stop()
    except Exception:
        pass
    root.destroy()


def registro(app, root):
    root.update()
    return app.txt_log.get("1.0", "end")


def p_guardar_objetivo():
    print("--- guardar el objetivo actual con nombre ---")
    app, root = abrir()
    try:
        app.var_roi_left.set("41")
        app.var_roi_right.set("59")
        app.var_roi_top.set("31")
        app.var_roi_bottom.set("52")
        app.var_new_target.set("cristal")
        app.save_target()
        check("queda en el diccionario", "cristal" in app.targets,
              str(list(app.targets)))
        check("con la zona que habia puesta",
              round(app.targets["cristal"]["roi_left"], 2) == 0.41,
              str(app.targets["cristal"].get("roi_left")))
        check("y con el modo", app.targets["cristal"]["mode"] == "unico",
              str(app.targets["cristal"].get("mode")))
        check("aparece en el desplegable",
              "cristal" in app.cmb_targets.cget("values"),
              str(app.cmb_targets.cget("values")))
        check("el Script ve el mismo diccionario",
              "cristal" in app.script.targets)
        check("y limpia la casilla del nombre", app.var_new_target.get() == "")
    finally:
        cerrar(app, root)


def p_nombre_invalido():
    print("--- nombres que no valen ---")
    app, root = abrir()
    try:
        app.var_new_target.set("")
        app.save_target()
        check("sin nombre no guarda nada", not app.targets, str(app.targets))
        check("y lo dice", "Ponle un nombre" in registro(app, root))
        app.var_new_target.set("con espacio")
        app.save_target()
        check("con espacios tampoco", not app.targets, str(app.targets))
        check("y explica por que",
              "no puede llevar espacios" in registro(app, root))
    finally:
        cerrar(app, root)


def p_borrar_objetivo():
    print("--- borrar un objetivo ---")
    app, root = abrir()
    try:
        app.var_new_target.set("uno")
        app.save_target()
        app.var_new_target.set("dos")
        app.save_target()
        app.var_target.set("uno")
        app.delete_target()
        check("se va el borrado y queda el otro",
              list(app.targets) == ["dos"], str(list(app.targets)))
        check("el desplegable se actualiza",
              app.cmb_targets.cget("values") == ("dos",),
              str(app.cmb_targets.cget("values")))
        app.var_target.set("")
        app.delete_target()
        check("borrar sin seleccion no revienta",
              "ningún objetivo seleccionado" in registro(app, root))
    finally:
        cerrar(app, root)


def p_persistencia():
    print("--- objetivos y guion sobreviven al cierre ---")
    app, root = abrir()
    try:
        app.var_roi_left.set("10")
        app.var_new_target.set("cosa")
        app.save_target()
        app.txt_script.delete("1.0", "end")
        app.txt_script.insert("1.0", "buscar cosa\nclic\nrepetir\n")
        app._save_config()
    finally:
        cerrar(app, root)

    app, root = abrir(cfg=None if not os.path.exists(G.CONFIG_PATH) else
                      json.load(open(G.CONFIG_PATH, encoding="utf-8-sig")))
    try:
        check("el objetivo vuelve", "cosa" in app.targets, str(list(app.targets)))
        check("con su zona",
              round(app.targets["cosa"]["roi_left"], 2) == 0.10,
              str(app.targets["cosa"].get("roi_left")))
        texto = app.txt_script.get("1.0", "end")
        check("el guion vuelve", "buscar cosa" in texto, repr(texto[:40]))
        check("y se puede analizar sin errores",
              not G.Script.parse(texto, app.targets)[1],
              str(G.Script.parse(texto, app.targets)[1]))
    finally:
        cerrar(app, root)


def p_comprobar():
    print("--- el boton Comprobar ---")
    app, root = abrir()
    try:
        app.var_new_target.set("cosa")
        app.save_target()
        app.txt_script.delete("1.0", "end")
        app.txt_script.insert("1.0", "buscar cosa 30 si_falla repetir\nclic\nrepetir")
        ok = app.check_script()
        txt = registro(app, root)
        check("dice que esta bien", ok is True)
        check("y lo explica paso a paso", "vuelve al paso 1" in txt,
              [l for l in txt.splitlines() if "1." in l][:1])

        app.txt_script.delete("1.0", "end")
        app.txt_script.insert("1.0", "buscar fantasma\nclic")
        ok = app.check_script()
        check("caza el objetivo inexistente", ok is False)
        check("y lo dice en el registro",
              "no hay un objetivo llamado" in registro(app, root))
    finally:
        cerrar(app, root)


def p_guardas():
    print("--- no lanza el guion si hay algo mas usando el raton ---")
    app, root = abrir()
    try:
        app.var_new_target.set("cosa")
        app.save_target()
        app.txt_script.delete("1.0", "end")
        app.txt_script.insert("1.0", "buscar cosa\nclic\nrepetir")

        app.watcher.active = True
        app.toggle_script()
        check("con la vigilancia activa no arranca", not app.script.running)
        check("y explica por que", "Desactiva la vigilancia" in registro(app, root))
        app.watcher.active = False

        app.recorder.recording = True
        app.toggle_script()
        check("grabando tampoco", not app.script.running)
        check("y lo dice", "mientras se graba" in registro(app, root))
        app.recorder.recording = False
    finally:
        cerrar(app, root)


def p_guion_roto_no_arranca():
    print("--- un guion con errores no se ejecuta ---")
    app, root = abrir()
    try:
        app.txt_script.delete("1.0", "end")
        app.txt_script.insert("1.0", "buscar fantasma\nclic")
        app.toggle_script()
        check("no arranca", not app.script.running)
        check("y enumera los problemas",
              "No lanzo el guion" in registro(app, root))
    finally:
        cerrar(app, root)


def p_panic_para_el_guion():
    print("--- F12 para el guion ---")
    app, root = abrir()
    try:
        app.script.running = True          # como si estuviera en marcha
        app.panic()
        check("lo para", not app.script.running)
        txt = registro(app, root)
        check("y lo nombra en la parada total", "guion" in txt,
              [l for l in txt.splitlines() if "PARADA" in l][:1])
        check("el boton vuelve a 'Ejecutar'",
              "Ejecutar" in app.btn_script.cget("text"),
              app.btn_script.cget("text"))
    finally:
        cerrar(app, root)


def p_f10_registrado():
    print("--- F10 esta en las hotkeys ---")
    check("F10 en el conjunto", G.HOTKEY_SCRIPT in G.HOTKEYS)
    app, root = abrir()
    try:
        check("F10 llama a toggle_script",
              app._acciones.get(G.HOTKEY_SCRIPT) == app.toggle_script,
              str(app._acciones.get(G.HOTKEY_SCRIPT)))
        check("y F12 sigue siendo la parada total",
              app._acciones.get(G.HOTKEY_PANIC) == app.panic)
        check("todas las hotkeys tienen accion",
              set(app._acciones) == G.HOTKEYS,
              str(G.HOTKEYS - set(app._acciones)))
    finally:
        cerrar(app, root)


def p_ejemplo():
    print("--- el boton Ejemplo deja algo que analiza bien ---")
    app, root = abrir()
    try:
        app.var_new_target.set("cristal")
        app.save_target()
        app.var_target.set("cristal")
        app.script_example()
        texto = app.txt_script.get("1.0", "end")
        pasos, errs = G.Script.parse(texto, app.targets)
        check("el ejemplo es valido", not errs, str(errs))
        check("y usa el objetivo seleccionado", "buscar cristal" in texto,
              repr(texto[:60]))
    finally:
        cerrar(app, root)


if __name__ == "__main__":
    respaldo = None
    if os.path.exists(G.CONFIG_PATH):
        with open(G.CONFIG_PATH, encoding="utf-8-sig") as f:
            respaldo = f.read()
    try:
        for fn in (p_guardar_objetivo, p_nombre_invalido, p_borrar_objetivo,
                   p_persistencia, p_comprobar, p_guardas,
                   p_guion_roto_no_arranca, p_panic_para_el_guion,
                   p_f10_registrado, p_ejemplo):
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
