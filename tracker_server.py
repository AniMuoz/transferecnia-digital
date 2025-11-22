# tracker_server.py
import os
import math
from typing import List, Dict, Any, Optional, Tuple

import requests
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from geopy.distance import geodesic

app = Flask(__name__)
CORS(app)

# ==================== CONFIG ====================

# URL del Web Service JSON de posicionamiento (DyS)
# Ejemplo (ajústalo a lo que te dieron):
#   WS_POS_URL=https://www.dtpmetropolitano.cl/posiciones
WS_POS_URL = os.getenv("WS_POS_URL", "").strip()
WS_POS_USER = os.getenv("WS_POS_USER", "").strip()
WS_POS_PASS = os.getenv("WS_POS_PASS", "").strip()
WS_POS_TOKEN = os.getenv("WS_POS_TOKEN", "").strip()

# Si quieres usar OpenRouteService pon tu API Key aquí (opcional)
ORS_API_KEY = os.getenv("ORS_API_KEY", "").strip()

# ==================== RUTAS (OSRM / ORS) ====================


def _route_generate_osrm(src_lat: float, src_lon: float,
                         dst_lat: float, dst_lon: float) -> List[Tuple[float, float]]:
    """
    Ruta por calles usando OSRM público.
    Devuelve lista de (lat, lon).
    """
    url = (
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{src_lon},{src_lat};{dst_lon},{dst_lat}"
        f"?overview=full&geometries=geojson"
    )
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    coords = r.json()["routes"][0]["geometry"]["coordinates"]  # [lon, lat]
    return [(lat, lon) for lon, lat in coords]


def _route_generate_ors(src_lat: float, src_lon: float,
                        dst_lat: float, dst_lon: float) -> List[Tuple[float, float]]:
    """
    Ruta por calles usando OpenRouteService (si tienes API key).
    """
    url = "https://api.openrouteservice.org/v2/directions/driving-car"
    params = {
        "api_key": ORS_API_KEY,
        "start": f"{src_lon},{src_lat}",
        "end": f"{dst_lon},{dst_lat}",
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    coords = r.json()["features"][0]["geometry"]["coordinates"]  # [lon, lat]
    return [(lat, lon) for lon, lat in coords]


def generate_route(src_lat: float, src_lon: float,
                   dst_lat: float, dst_lon: float) -> List[Tuple[float, float]]:
    """
    Intenta ORS si hay key, si no usa OSRM.
    """
    if ORS_API_KEY:
        try:
            return _route_generate_ors(src_lat, src_lon, dst_lat, dst_lon)
        except Exception:
            pass
    return _route_generate_osrm(src_lat, src_lon, dst_lat, dst_lon)

# ==================== WS JSON POSICIONAMIENTO ====================


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "."))
    except Exception:
        return None


def fetch_official_positions(service: Optional[str] = None,
                             direction: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Llama al WS JSON de DyS y devuelve una lista de buses normalizada:

    [{
      "bus_id": "BJFC-73",
      "lat": -33.4,
      "lon": -70.6,
      "speed_kmh": 25.0,
      "service": "T201",
      "direction": "I",
      "raw": {...registro completo...}
    }, ...]
    """
    if not WS_POS_URL:
        raise RuntimeError(
            "Falta configurar WS_POS_URL (URL del web service de posicionamiento)."
        )

    # Ajusta estos nombres de parámetros según el PDF del WS:
    # por ejemplo, si el WS usa ?linea=T201, cambia "servicio" por "linea".
    params: Dict[str, Any] = {}
    if service:
        params["servicio"] = service
    if direction:
        params["sentido"] = direction

    headers: Dict[str, str] = {}
    if WS_POS_TOKEN:
        headers["Authorization"] = f"Bearer {WS_POS_TOKEN}"

    auth = (WS_POS_USER, WS_POS_PASS) if (WS_POS_USER and WS_POS_PASS) else None

    resp = requests.get(
        WS_POS_URL,
        params=params,
        headers=headers or None,
        auth=auth,
        timeout=15,
    )
    resp.raise_for_status()

    try:
        data = resp.json()
    except ValueError as e:
        raise RuntimeError("La respuesta del WS JSON no se pudo parsear como JSON.") from e

    posiciones = data.get("posiciones") or data.get("Posiciones") or []
    flat: List[Dict[str, Any]] = []

    # Cada item en "posiciones" es una cadena con entre 1 y 4 registros separados por ';'
    # Cada registro tiene 12 campos en el orden del documento que te dieron.
    for item in posiciones:
        if isinstance(item, str):
            tokens = [t.strip() for t in item.split(";") if t.strip() != ""]
            for i in range(0, len(tokens), 12):
                chunk = tokens[i:i + 12]
                if len(chunk) < 12:
                    break
                (
                    fecha_gps,
                    patente,
                    lat_str,
                    lon_str,
                    vel_str,
                    dir_geo,
                    num_operador,
                    nom_servicio,
                    sentido,
                    ruta_consola,
                    ruta_sinoptico,
                    fecha_insert,
                ) = chunk
                flat.append(
                    {
                        "fecha_gps": fecha_gps,
                        "patente": patente,
                        "lat": lat_str,
                        "lon": lon_str,
                        "vel": vel_str,
                        "dir_geo": dir_geo,
                        "num_operador": num_operador,
                        "servicio": nom_servicio,
                        "sentido": sentido,
                        "ruta_consola": ruta_consola,
                        "ruta_sinoptico": ruta_sinoptico,
                        "fecha_insert": fecha_insert,
                    }
                )
        elif isinstance(item, dict):
            flat.append(item)

    # Nos quedamos con el último registro por patente
    by_patente: Dict[str, Dict[str, Any]] = {}
    for rec in flat:
        patente = str(
            rec.get("patente")
            or rec.get("Patente")
            or rec.get("PATENTE")
            or ""
        )
        if not patente:
            continue
        by_patente[patente] = rec

    buses: List[Dict[str, Any]] = []
    for patente, rec in by_patente.items():
        lat = _to_float(rec.get("lat") or rec.get("Latitud"))
        lon = _to_float(rec.get("lon") or rec.get("Longitud"))
        if lat is None or lon is None:
            continue

        speed = _to_float(rec.get("vel") or rec.get("Velocidad Instantánea")) or 20.0
        service_code = rec.get("servicio") or rec.get("Nombre Comercial del Servicio")
        sentido = rec.get("sentido") or rec.get("Sentido")

        buses.append(
            {
                "bus_id": patente,
                "lat": lat,
                "lon": lon,
                "speed_kmh": float(speed),
                "service": service_code,
                "direction": sentido,
                "raw": rec,
            }
        )

    # Filtrado opcional por servicio/sentido
    if service:
        u = service.upper()
        buses = [b for b in buses if str(b.get("service") or "").upper() == u]
    if direction:
        u = direction.upper()
        buses = [b for b in buses if str(b.get("direction") or "").upper() == u]

    return buses

# ==================== HTML / UI ====================

INDEX_HTML = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>Monitor / Recomendador de buses DTP</title>
<meta name="viewport" content="width=device-width,initial-scale=1" />
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  body{font-family:Arial, sans-serif; padding:12px; max-width:1200px; margin:auto; background:#fafafa;}
  h1{font-size:1.4rem;margin:6px 0 2px 0}
  .sub{font-size:0.85rem;color:#555;margin-bottom:10px}
  .card{border:1px solid #ddd;border-radius:8px;padding:10px;margin:8px 0;background:#fff}
  .row{display:flex;gap:8px;flex-wrap:wrap}.row>*{flex:1 1 0}
  button,input{padding:6px 8px;font-size:0.9rem}
  button{cursor:pointer;border:1px solid #ccc;border-radius:4px;background:#f3f3f3}
  button:hover{background:#e6e6e6}
  button.btn-primary{background:#007bff;color:#fff;border-color:#007bff}
  button.btn-primary:hover{background:#0069d9}
  button.btn-toggle.active{background:#007bff;color:#fff}
  #map{height:480px;border-radius:8px;border:1px solid #ddd}
  table{width:100%;border-collapse:collapse;font-size:0.9rem}
  th,td{padding:6px 4px;border-bottom:1px solid #eee;text-align:left}
  th{background:#f5f5f5}
  .badge{display:inline-block;padding:2px 6px;border-radius:999px;font-size:0.8rem;color:#fff}
  .pill-grey{display:inline-block;padding:2px 6px;border-radius:999px;background:#eee;font-size:0.8rem;color:#333}
  #statusText{font-size:0.85rem;color:#444;margin-bottom:4px}
</style>
</head>
<body>
<h1>Monitor / Recomendador de buses DTP</h1>
<div class="sub">Selecciona un paradero y un destino en el mapa, y el sistema recomendará las micros que llegan pronto a tu paradero.</div>
<div id="statusText">Esperando selección de paradero y destino…</div>

<div class="card">
  <h3>1. Selección de paradero y destino</h3>
  <div class="row">
    <div>
      <button id="btnPickStop" class="btn-toggle btn-primary">Click en mapa = Paradero</button>
      <div style="margin-top:4px;font-size:0.85rem">
        Paradero: <span id="stopCoords" class="pill-grey">(no definido)</span>
      </div>
    </div>
    <div>
      <button id="btnPickDest" class="btn-toggle">Click en mapa = Destino</button>
      <div style="margin-top:4px;font-size:0.85rem">
        Destino: <span id="destCoords" class="pill-grey">(no definido)</span>
      </div>
    </div>
  </div>
</div>

<div class="card">
  <h3>2. Parámetros</h3>
  <div class="row">
    <div>
      <label style="font-size:0.85rem">Servicio (opcional)</label><br>
      <input id="serviceInput" placeholder="T201 / 422 / etc" style="width:100%" />
    </div>
    <div>
      <label style="font-size:0.85rem">Intervalo auto-actualización (seg)</label><br>
      <input id="refreshSec" value="5" style="width:70px" />
      <button id="btnToggleAuto">Auto: encendido</button>
    </div>
  </div>
  <div style="margin-top:8px">
    <button id="btnBuscar" class="btn-primary" style="width:100%">Buscar ahora</button>
  </div>
</div>

<div class="card">
  <h3>Mapa</h3>
  <div id="map"></div>
</div>

<div class="card">
  <h3>Micros recomendadas</h3>
  <table>
    <thead>
      <tr>
        <th>Servicio</th>
        <th>Sentido</th>
        <th>Distancia al paradero</th>
        <th>ETA al paradero</th>
        <th>Velocidad</th>
        <th>Recomendado</th>
      </tr>
    </thead>
    <tbody id="tbodyRecs">
      <tr><td colspan="6"><i>Sin datos todavía.</i></td></tr>
    </tbody>
  </table>
</div>

<script>
(function(){
  const statusEl = document.getElementById('statusText');
  const btnPickStop = document.getElementById('btnPickStop');
  const btnPickDest = document.getElementById('btnPickDest');
  const stopCoordsEl = document.getElementById('stopCoords');
  const destCoordsEl = document.getElementById('destCoords');
  const serviceInput = document.getElementById('serviceInput');
  const refreshSecInput = document.getElementById('refreshSec');
  const btnBuscar = document.getElementById('btnBuscar');
  const btnToggleAuto = document.getElementById('btnToggleAuto');
  const tbodyRecs = document.getElementById('tbodyRecs');

  let pickMode = 'stop';
  let stopLatLng = null;
  let destLatLng = null;

  let map = L.map('map');
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19
  }).addTo(map);
  map.setView([-33.45, -70.65], 13);

  let stopMarker = null;
  let destMarker = null;
  let routeLine = null;
  let busMarkers = [];
  let autoOn = true;
  let timer = null;

  const COLOR_PALETTE = [
    '#1f77b4','#ff7f0e','#2ca02c','#d62728','#9467bd',
    '#8c564b','#e377c2','#7f7f7f','#bcbd22','#17becf'
  ];
  const svcColor = {};
  function getColorForService(svc){
    if(!svc) return '#0074D9';
    if(!svcColor[svc]){
      const idx = Object.keys(svcColor).length % COLOR_PALETTE.length;
      svcColor[svc] = COLOR_PALETTE[idx];
    }
    return svcColor[svc];
  }

  function setPickMode(mode){
    pickMode = mode;
    btnPickStop.classList.toggle('btn-primary', mode==='stop');
    btnPickStop.classList.toggle('active', mode==='stop');
    btnPickDest.classList.toggle('btn-primary', mode==='dest');
    btnPickDest.classList.toggle('active', mode==='dest');
  }
  setPickMode('stop');

  function updateCoordsLabels(){
    if(stopLatLng){
      stopCoordsEl.textContent = stopLatLng.lat.toFixed(5)+', '+stopLatLng.lng.toFixed(5);
    } else {
      stopCoordsEl.textContent = '(no definido)';
    }
    if(destLatLng){
      destCoordsEl.textContent = destLatLng.lat.toFixed(5)+', '+destLatLng.lng.toFixed(5);
    } else {
      destCoordsEl.textContent = '(no definido)';
    }
  }

  map.on('click', function(e){
    if(pickMode === 'stop'){
      stopLatLng = e.latlng;
      if(!stopMarker){
        stopMarker = L.marker(stopLatLng).addTo(map);
      }else{
        stopMarker.setLatLng(stopLatLng);
      }
    }else if(pickMode === 'dest'){
      destLatLng = e.latlng;
      if(!destMarker){
        destMarker = L.marker(destLatLng).addTo(map);
      }else{
        destMarker.setLatLng(destLatLng);
      }
    }
    updateCoordsLabels();
  });

  btnPickStop.onclick = () => setPickMode('stop');
  btnPickDest.onclick = () => setPickMode('dest');

  btnToggleAuto.onclick = () => {
    autoOn = !autoOn;
    btnToggleAuto.textContent = autoOn ? 'Auto: encendido' : 'Auto: apagado';
    setupAuto();
  };

  btnBuscar.onclick = () => runSearch(true);

  function clearDynamicLayers(){
    if(routeLine){
      map.removeLayer(routeLine);
      routeLine = null;
    }
    busMarkers.forEach(m => map.removeLayer(m));
    busMarkers = [];
  }

  async function runSearch(centerMap){
    if(!stopLatLng || !destLatLng){
      alert('Primero selecciona un paradero y un destino haciendo click en el mapa.');
      return;
    }
    const body = {
      stop_lat: stopLatLng.lat,
      stop_lon: stopLatLng.lng,
      dest_lat: destLatLng.lat,
      dest_lon: destLatLng.lng,
      service: (serviceInput.value || '').trim()
    };
    try{
      const resp = await fetch('/api/search', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify(body)
      });
      const data = await resp.json();
      if(!data.ok){
        statusEl.textContent = 'Error: ' + (data.error || 'desconocido');
        return;
      }

      const t = new Date().toLocaleTimeString();
      statusEl.textContent =
        `(${t}) Recomendaciones: ${data.recommended_count} buses recomendados de ${data.candidate_count} candidatos.`;

      clearDynamicLayers();

      // Ruta paradero → destino (naranja, discontinua)
      if(Array.isArray(data.stop_to_dest) && data.stop_to_dest.length >= 2){
        const latlngs = data.stop_to_dest.map(p => [p[0], p[1]]);
        routeLine = L.polyline(latlngs, {
          color:'#ff8800',
          weight:7,
          opacity:0.9,
          dashArray:'6,6'
        }).addTo(map);
      }

      // Tabla
      tbodyRecs.innerHTML = '';
      const recs = data.recommended || [];
      if(recs.length === 0){
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 6;
        td.innerHTML = '<i>Sin buses recomendados para este momento.</i>';
        tr.appendChild(td);
        tbodyRecs.appendChild(tr);
        return;
      }

      // Solo micros recomendadas: marcadores + filas tabla
      recs.forEach(rec => {
        const color = getColorForService(rec.service || '');
        const m = L.circleMarker([rec.lat, rec.lon], {
          radius:6,
          color:color,
          fillColor:color,
          fillOpacity:0.9
        }).addTo(map);
        m.bindTooltip(
          `${rec.service || '—'} · sentido ${rec.direction || ''} · ETA ${rec.eta_to_stop_min.toFixed(1)} min`
        );
        busMarkers.push(m);

        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td><span class="badge" style="background:${color}">${rec.service || '—'}</span></td>
          <td>${rec.direction || '—'}</td>
          <td>${rec.distance_to_stop_km.toFixed(2)} km</td>
          <td>${rec.eta_to_stop_min.toFixed(1)} min</td>
          <td>${rec.speed_kmh.toFixed(1)} km/h</td>
          <td>Sí</td>
        `;
        tbodyRecs.appendChild(tr);
      });

      if(centerMap){
        const pts = [];
        if(stopLatLng) pts.push([stopLatLng.lat, stopLatLng.lng]);
        if(destLatLng) pts.push([destLatLng.lat, destLatLng.lng]);
        recs.forEach(rec => pts.push([rec.lat, rec.lon]));
        if(pts.length > 0){
          const bounds = L.latLngBounds(pts);
          map.fitBounds(bounds.pad(0.25));
        }
      }

    }catch(err){
      console.error(err);
      statusEl.textContent = 'Error llamando al backend (revisa consola).';
    }
  }

  function setupAuto(){
    if(timer) clearInterval(timer);
    const sec = parseInt(refreshSecInput.value || '5', 10);
    if(!autoOn || isNaN(sec) || sec <= 0) return;
    timer = setInterval(() => runSearch(false), sec*1000);
  }

  setupAuto(); // arranca auto-actualización con el valor inicial
})();
</script>
</body>
</html>
"""

# ==================== ENDPOINTS ====================


@app.route("/")
def index():
    return render_template_string(INDEX_HTML)


@app.route("/api/search", methods=["POST"])
def api_search():
    """
    Recibe:
      {
        "stop_lat": -33.45,
        "stop_lon": -70.65,
        "dest_lat": -33.46,
        "dest_lon": -70.64,
        "service": "T201"   (opcional)
      }

    Devuelve rutas + buses recomendados.
    """
    try:
        data = request.get_json(force=True)
        stop_lat = float(data["stop_lat"])
        stop_lon = float(data["stop_lon"])
        dest_lat = float(data["dest_lat"])
        dest_lon = float(data["dest_lon"])
        service = (data.get("service") or "").strip() or None
    except Exception:
        return jsonify({"ok": False, "error": "Payload inválido"}), 400

    try:
        buses = fetch_official_positions(service=service, direction=None)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    candidates_out: List[Dict[str, Any]] = []
    recommended_out: List[Dict[str, Any]] = []

    stop_pos = (stop_lat, stop_lon)

    for b in buses:
        lat = b["lat"]
        lon = b["lon"]
        pos_bus = (lat, lon)
        dist_to_stop_km = geodesic(pos_bus, stop_pos).km
        speed = max(float(b.get("speed_kmh", 20.0)), 5.0)
        eta_to_stop_min = (dist_to_stop_km / speed) * 60.0

        rec_obj = {
            "bus_id": b["bus_id"],
            "service": b.get("service"),
            "direction": b.get("direction"),
            "lat": lat,
            "lon": lon,
            "speed_kmh": speed,
            "distance_to_stop_km": dist_to_stop_km,
            "eta_to_stop_min": eta_to_stop_min,
        }
        candidates_out.append(rec_obj)

        # Heurística de recomendación (puedes ajustar a tu gusto)
        # - Máx 0.7 km hasta el paradero
        # - Máx 12 minutos de ETA al paradero
        if dist_to_stop_km <= 0.7 and eta_to_stop_min <= 12.0:
            recommended_out.append(rec_obj)

    # Ordenamos recomendados por ETA
    recommended_out.sort(key=lambda x: x["eta_to_stop_min"])
    candidate_count = len(candidates_out)
    recommended_count = len(recommended_out)

    # Ruta paradero → destino
    try:
        stop_to_dest = generate_route(stop_lat, stop_lon, dest_lat, dest_lon)
    except Exception:
        stop_to_dest = []

    return jsonify(
        {
            "ok": True,
            "stop": [stop_lat, stop_lon],
            "dest": [dest_lat, dest_lon],
            "stop_to_dest": stop_to_dest,
            "candidate_count": candidate_count,
            "recommended_count": recommended_count,
            # Solo devolvemos recomendados; si quieres candidatos, añade otra clave.
            "recommended": recommended_out,
        }
    )

# ==================== MAIN ====================

if __name__ == "__main__":
  print("Servidor iniciado. Abre http://127.0.0.1:5000")
  app.run(host="0.0.0.0", port=5000, debug=True)
