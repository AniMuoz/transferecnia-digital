# main.py
import requests
import time

TRACKER_URL = "http://127.0.0.1:5000"  # o la IP donde corre tracker_server

def get_buses():
    """Obtener estado de todos los buses desde tracker_server"""
    try:
        r = requests.get(f"{TRACKER_URL}/sim/buses")
        r.raise_for_status()
        data = r.json()
        if data.get("ok"):
            return data["buses"], data["destino"]
    except Exception as e:
        print("Error al obtener buses:", e)
    return [], None

def get_occupancy():
    """Obtener ocupación de buses desde tracker_server"""
    try:
        r = requests.get(f"{TRACKER_URL}/occupancy/list")
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("Error al obtener ocupación:", e)
        return {}

if __name__ == "__main__":
    while True:
        buses, destino = get_buses()
        ocup = get_occupancy()
        print("Destino:", destino)
        print("Buses:", buses)
        print("Ocupación:", ocup)
        time.sleep(5)
