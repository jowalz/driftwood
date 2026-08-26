import os
import sys

# agent.py, analysis.py, state.py, actions.py importieren sich gegenseitig
# flach (from analysis import ...), weil das Docker-Image agent/ flach nach
# /app kopiert und dort "python agent.py" laeuft. Fuer den Paket-Aufruf
# (python -m agent.routing_examples) muss dasselbe flache Importieren auch
# ausserhalb des Containers funktionieren -- also den eigenen Ordner selbst
# auf sys.path legen, statt die Import-Konvention in jeder Datei zu aendern.
sys.path.insert(0, os.path.dirname(__file__))
