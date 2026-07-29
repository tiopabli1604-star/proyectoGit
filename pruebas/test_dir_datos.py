# -*- coding: utf-8 -*-
"""Donde guarda Golem sus archivos.

Si el exe se abre desde la descarga del navegador, Windows lo ejecuta desde un
'scoped_dir' de %TEMP% que se borra solo, y los ajustes se perderian en cada
arranque sin que se note. Estas pruebas comprueban la deteccion de ese caso.
"""
import os
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import main as G

R = []


def check(nombre, ok, extra=""):
    R.append(ok)
    extra = str(extra) if extra else ""
    print(f"  {'OK  ' if ok else 'FALLO'} {nombre}{(' — ' + extra) if extra else ''}")


def p_detecta_temporal():
    print("--- reconoce las carpetas temporales ---")
    t = tempfile.gettempdir()
    casos = [
        (t, True, "el propio %TEMP%"),
        (os.path.join(t, "scoped_dir5096_39852999"), True,
         "el scoped_dir del navegador"),
        (os.path.join(t, "a", "b", "c"), True, "algo hondo dentro de %TEMP%"),
        (t.upper(), True, "en mayusculas (Windows no distingue)"),
        (RAIZ, False, "la carpeta del proyecto"),
        (os.path.expanduser("~\\Desktop\\Golem"), False, "el Escritorio"),
        ("C:\\Program Files\\Golem", False, "Program Files"),
        (t + "aunque_empiece_igual", False,
         "una carpeta que solo empieza igual"),
    ]
    for ruta, esperado, nombre in casos:
        got = G._es_temporal(ruta)
        check(nombre, got == esperado, f"{ruta} -> {got}")


def p_escritura():
    print("--- comprueba si puede escribir ---")
    check("en una carpeta normal si", G._se_puede_escribir(RAIZ) is True)
    check("en una que no existe, no",
          G._se_puede_escribir(os.path.join(RAIZ, "no", "existe", "nada"))
          is False)
    antes = set(os.listdir(RAIZ))
    G._se_puede_escribir(RAIZ)
    check("y no deja basura detras", set(os.listdir(RAIZ)) == antes,
          str(set(os.listdir(RAIZ)) - antes))


def _lanzar_desde(carpeta):
    """Importa main.py con sys.argv[0] falseado y devuelve (APP_DIR, motivo)."""
    # con marcas, porque el motivo puede ser cadena vacia y un strip() se
    # comeria la linea entera
    codigo = (
        "import os, sys\n"
        f"sys.argv[0] = os.path.join(r'{carpeta}', 'Golem.exe')\n"
        f"sys.path.insert(0, r'{RAIZ}')\n"
        "import main as G\n"
        "print('DIR=' + G.APP_DIR)\n"
        "print('MOTIVO=' + G.DATA_MOTIVO)\n"
    )
    r = subprocess.run([sys.executable, "-c", codigo], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    d = m = None
    for l in (r.stdout or "").splitlines():
        if l.startswith("DIR="):
            d = l[4:]
        elif l.startswith("MOTIVO="):
            m = l[7:]
    if d is None or m is None:
        return None, f"(fallo: {(r.stderr or '')[-200:]})"
    return d, m


def p_elige_bien():
    print("--- elige la carpeta segun de donde se lance ---")
    scoped = os.path.join(tempfile.gettempdir(), "scoped_dir5096_39852999")
    os.makedirs(scoped, exist_ok=True)
    app_dir, motivo = _lanzar_desde(scoped)
    local = os.environ.get("LOCALAPPDATA", "")
    check("desde un scoped_dir NO guarda ahi",
          app_dir is not None and os.path.normcase(app_dir)
          != os.path.normcase(scoped), app_dir)
    check("lo lleva a LOCALAPPDATA\\Golem",
          app_dir is not None and local
          and os.path.normcase(app_dir)
          == os.path.normcase(os.path.join(local, "Golem")), app_dir)
    check("y dice el motivo", "temporal" in (motivo or ""), motivo)
    check("la carpeta existe", app_dir and os.path.isdir(app_dir), app_dir)

    app_dir2, motivo2 = _lanzar_desde(RAIZ)
    check("desde una carpeta normal guarda ahi mismo",
          app_dir2 is not None and os.path.normcase(app_dir2)
          == os.path.normcase(RAIZ), app_dir2)
    check("y sin ningun aviso", motivo2 == "", repr(motivo2))


def p_rutas_coherentes():
    print("--- todas las rutas salen de la carpeta elegida ---")
    for nombre, ruta in (("config", G.CONFIG_PATH), ("registro", G.LOG_PATH),
                         ("plantilla", G.TEMPLATE_IMG),
                         ("depuracion", G.DEBUG_IMG),
                         ("capturas", G.SHOT_PATH)):
        check(nombre, os.path.dirname(ruta) == G.APP_DIR, ruta)


if __name__ == "__main__":
    for fn in (p_detecta_temporal, p_escritura, p_elige_bien,
               p_rutas_coherentes):
        fn()
        print()
    fallos = R.count(False)
    print(f"{len(R)} comprobaciones, "
          + ("TODO OK" if not fallos else f"{fallos} FALLO(S)"))
    sys.exit(1 if fallos else 0)
