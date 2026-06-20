import datetime as dt
from pcse.models import Wofost72_WLP_FD
from pcse.base import ParameterProvider
from backend.app.simulation.weather_provider import create_weather_provider
from backend.app.simulation.crop_provider import create_crop_provider
from backend.app.simulation.soil_provider import create_soil_params
from backend.app.simulation.site_provider import create_site_params
from backend.app.simulation.agromanagement import build_agromanagement

wdp = create_weather_provider(52.0, 5.5, 10.0, 2020, 2021, False)
cropd = create_crop_provider("wheat", "Winter_wheat_101")
soildata = create_soil_params()
sitedata = create_site_params()
params = ParameterProvider(cropdata=cropd, soildata=soildata, sitedata=sitedata)
agro = build_agromanagement("wheat", "Winter_wheat_101", dt.date(2020, 10, 15), dt.date(2021, 7, 30), 300)

wofost = Wofost72_WLP_FD(params, wdp, agro)

for i in range(150):
    wofost.run(days=1)
    
print("Before setting:")
print("LAI:", wofost.get_variable("LAI"))
print("TWSO:", wofost.get_variable("TWSO"))

wofost.set_variable("LAI", 2.5)

print("After setting:")
print("LAI:", wofost.get_variable("LAI"))

try:
    wofost.set_variable("TWSO", 500.0)
    print("Set TWSO successfully")
except Exception as e:
    print("Error setting TWSO:", e)

# How about TWLV, TWST, TWRT?
try:
    wofost.set_variable("TWLV", 1000.0)
    print("Set TWLV successfully")
except Exception as e:
    print("Error setting TWLV:", e)
