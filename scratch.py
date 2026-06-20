import datetime as dt
from backend.app.simulation.engine import run_simulation

sim = run_simulation(step_by_step=True)
for i in range(150):
    sim.run(days=1)
    
print("Before setting:")
print("LAI:", sim.get_variable("LAI"))
print("TWSO:", sim.get_variable("TWSO"))
print("TAGP:", sim.get_variable("TAGP"))

sim.set_variable("LAI", 2.5)
sim.set_variable("TWSO", 500.0)

print("After setting:")
print("LAI:", sim.get_variable("LAI"))
print("TWSO:", sim.get_variable("TWSO"))

try:
    sim.set_variable("TAGP", 1000.0)
    print("Set TAGP successfully")
except Exception as e:
    print("Error setting TAGP:", e)

