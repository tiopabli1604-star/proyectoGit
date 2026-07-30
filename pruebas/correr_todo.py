# -*- coding: utf-8 -*-
"""Lanza las tres suites y resume. Uso: python pruebas/correr_todo.py"""
import os
import subprocess
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
SUITES = ("test_base.py", "test_zona.py", "test_escena_real.py",
          "test_unico.py", "test_migracion.py", "test_guion.py",
          "test_guion_gui.py", "test_dir_datos.py", "test_relativo.py",
          "test_texto.py", "test_primera_persona.py", "test_atasco.py",
          "test_seguridad.py", "test_sensores.py", "test_combos.py",
          "test_ancla.py", "test_ancla_calidad.py", "test_mantenido.py",
          "test_reproduccion_mantenido.py", "test_agrupado.py",
          "test_integracion.py")

if __name__ == "__main__":
    fallos = []
    for s in SUITES:
        print(f"\n{'=' * 62}\n== {s}\n{'=' * 62}")
        r = subprocess.run([sys.executable, os.path.join(AQUI, s)])
        if r.returncode:
            fallos.append(s)
    print(f"\n{'=' * 62}")
    if fallos:
        print("FALLAN: " + ", ".join(fallos))
    else:
        print(f"Las {len(SUITES)} suites en verde.")
    sys.exit(1 if fallos else 0)
