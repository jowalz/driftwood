import os
import sys

# agent.py, analysis.py, state.py, and actions.py import each other flatly
# (from analysis import ...) because the Docker image copies agent/ flat
# into /app, where "python agent.py" runs. For the package-style invocation
# (python -m agent.routing_examples) the same flat imports need to work
# outside the container too -- so put this directory itself on sys.path
# instead of changing the import convention in every file.
sys.path.insert(0, os.path.dirname(__file__))
