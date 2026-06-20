import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
from backend.app.simulation.minimal_runner import _setup_wofost_engine
from datetime import date
try:
    engine = _setup_wofost_engine()
    engine.run(10)
    print("LAI:", engine.get_variable('LAI'))
    # Let's see what is inside engine to overwrite LAI
    # We can use reflection or look at engine structure
    print(engine.__dict__.keys())
except Exception as e:
    print(e)
