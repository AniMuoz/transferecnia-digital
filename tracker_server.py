import os
import math
from typing import Dict, Any, List, Tuple, Optional

import requests
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from geopy.distance import geodesic

app = Flask(__name__)
CORS(app)

# ==================== CONFIG ====================

# Web Service JSON de posicionamiento (DyS)
WS_POS_URL   = os.getenv("WS_POS_URL", "").strip()
WS_POS_USER  = os.getenv("WS_POS_USER", "").strip()
WS_POS_PASS  = os.getenv("WS_POS_PASS", "").strip()
WS_POS_TOKEN = os.getenv("WS_POS_TOKEN", "").strip()

# Centro del mapa (Viña/Santiago, ajusta si quieres)
DEFAULT_CENTER = (-33.4624, -70.6550)


# ==================== HTML (UI) ====================

INDEX_HTML = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>Monitor buses DTP</title>
<meta name="viewport" content="width=device-width,initial-scale=1" />
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  body{font-family:Arial, sans-serif; padding:12px; max-width:1200px; margin:auto}
  h1{font-size:1.4rem;margin:6px 0}
  .card{border:1px solid #ddd;border-radius:8px;padding:10px;margin:8px 0}
  .row{display:flex;gap:8px;flex-wrap:wrap}.row>*{flex:1 1 0}
  input,button{padding:8px}
  button{cursor:pointer}
  #map{height:480px;border-radius:8px}
  table{width:100%;border-collapse:collapse}th,td{padding:6px;border-bottom:1px solid #eee;font-size:0.9rem}
  .pill{display:inline-block;padding:2px 6px;border-radius:999px;background:#eee;font-size:11px;margin-left:4px}
  small{font-size:0.75rem;color:#666}
  .btn-active{background:#007bff;color:white}
</style>
</head>
<body>
<h1>Monitor / Recomendador de buses DTP</h1>
<div id="status">Estado: listo</div>

<div class="card">
  <h3>1. Selección de paradero y destino</h3>
  <p>
    Usa los botones para elegir qué marcar en el mapa:
    <ul>
      <li><b>Paradero</b>: punto donde estás esperando el bus.</li>
      <li><b>Destino</b>: hacia dónde quieres ir (aproximado).</li>
    </ul>
    Luego haz click en el mapa para fijar cada punto.
  </p>
  <div class="row">
    <button id="modeStopBtn" class="btn-active">Modo: marcar PARADERO</button>
    <button id="modeDestBtn">Modo: marcar DESTINO</button>
  </div>
  <br>
  <div class="row">
    <div>
      <label>Paradero (lat, lon)</label>
      <input id="stopLat" placeholder="lat" />
      <input id="stopLon" placeholder="lon" />
    </div>
    <div>
      <label>Destino (lat, lon)</label>
      <input id="destLat" placeholder="lat" />
      <input id="destLon" placeholder="lon" />
    </div>
  </div>
  <small>Puedes editar las coordenadas a mano si quieres algo muy exacto.</small>
</div>

<div class="card">
  <h3>2. Filtros de servicio</h3>
  <div class="row">
    <div>
      <label>Servicio (ej: T201, opcional)</label>
      <input id="serviceInput" placeholder="T201">
    </div>
    <div>
      <label>Sentido (I/R, opcional)</label>
      <input id="directionInput" placeholder="I">
    </div>
  </div>
  <small>Si dejas los filtros vacíos, se usan todos los buses disponibles en el WS.</small>
</div>

<div class="card">
  <h3>3. Operaciones</h3>
  <div class="row">
    <button id="btnBusesStop">Ver buses que llegan a este paradero</button>
    <button id="btnRecommendations">Recomendar buses para ir al destino</button>
    <button id="btnStopAuto">Detener actualización automática</button>
  </div>
  <small>
    El sistema usa la posición, velocidad y dirección aproximada del bus para estimar
    distancia y ETA. Es una estimación simple, solo para demo/prototipo.
  </small>
</div>

<div class="card">
  <h3>Mapa</h3>
  <div id="map"></div>
</div>

<div class="card">
  <h3>Resultados</h3>
  <div id="results"></div>
</div>

<script>
(function(){
  const statusEl = document.getElementById('status');
  const modeStopBtn = document.getElementById('modeStopBtn');
  const modeDestBtn = document.getElementById('modeDestBtn');

  const stopLatEl = document.getElementById('stopLat');
  const stopLonEl = document.getElementById('stopLon');
  const destLatEl = document.getElementById('destLat');
  const destLonEl = document.getElementById('destLon');

  const serviceInput   = document.getElementById('serviceInput');
  const directionInput = document.getElementById('directionInput');

  const btnBusesStop       = document.getElementById('btnBusesStop');
  const btnRecommendations = document.getElementById('btnRecommendations');
  const btnStopAuto        = document.getElementById('btnStopAuto');

  const resultsEl = document.getElementById('results');

  let mode = "stop"; // "stop" o "dest"
  let currentMode = null; // "near" | "reco" | null (para auto-refresh)
  let refreshTimer = null;

  let stopLat = null, stopLon = null;
  let destLat = null, destLon = null;

  // Leaflet
  let map = L.map('map');
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19}).addTo(map);
  map.setView([{{ center_lat }}, {{ center_lon }}], 13);

  let stopMarker = null;
  let destMarker = null;
  let busMarkers = {};
  let routeLayers = [];

  function clearRoutes(){
    for (const l of routeLayers){
      map.removeLayer(l);
    }
    routeLayers = [];
  }

  function setModeClick(newMode){
    mode = newMode;
    if (mode === "stop") {
      modeStopBtn.classList.add("btn-active");
      modeDestBtn.classList.remove("btn-active");
      statusEl.textContent = "Modo selección: haz click en el mapa para marcar PARADERO.";
    } else {
      modeDestBtn.classList.add("btn-active");
      modeStopBtn.classList.remove("btn-active");
      statusEl.textContent = "Modo selección: haz click en el mapa para marcar DESTINO.";
    }
  }

  modeStopBtn.onclick = () => setModeClick("stop");
  modeDestBtn.onclick = () => setModeClick("dest");

  function updateStop(lat, lon){
    stopLat = lat; stopLon = lon;
    stopLatEl.value = lat.toFixed(6);
    stopLonEl.value = lon.toFixed(6);
    if (!stopMarker){
      stopMarker = L.marker([lat, lon], {title:"Paradero"}).addTo(map);
    } else {
      stopMarker.setLatLng([lat, lon]);
    }
  }

  function updateDest(lat, lon){
    destLat = lat; destLon = lon;
    destLatEl.value = lat.toFixed(6);
    destLonEl.value = lon.toFixed(6);
    if (!destMarker){
      destMarker = L.marker([lat, lon], {title:"Destino"}).addTo(map);
    } else {
      destMarker.setLatLng([lat, lon]);
    }
  }

  map.on('click', function(e){
    const lat = e.latlng.lat;
    const lon = e.latlng.lng;
    if (mode === "stop"){
      updateStop(lat, lon);
    } else {
      updateDest(lat, lon);
    }
  });

  function readCoordsFromInputs(){
    if (stopLatEl.value && stopLonEl.value){
      const la = parseFloat(stopLatEl.value);
      const lo = parseFloat(stopLonEl.value);
      if (!isNaN(la) && !isNaN(lo)) updateStop(la, lo);
    }
    if (destLatEl.value && destLonEl.value){
      const la = parseFloat(destLatEl.value);
      const lo = parseFloat(destLonEl.value);
      if (!isNaN(la) && !isNaN(lo)) updateDest(la, lo);
    }
  }

  function renderBuses(buses, options){
    options = options || {};
    const showRecommended = !!options.showRecommended;
    const stop = options.stop || null;
    const dest = options.dest || null;

    // limpiar markers y rutas anteriores
    for (const id in busMarkers){
      map.removeLayer(busMarkers[id]);
    }
    busMarkers = {};
    clearRoutes();

    let rows = [];
    for (const b of buses){
      const id = b.bus_id;
      const la = b.lat;
      const lo = b.lon;
      const svc = b.service || "—";
      const dir = b.direction || "—";
      const dist = (b.distance_to_stop_km != null ? b.distance_to_stop_km.toFixed(2)+" km" : "—");
      const eta  = (b.eta_to_stop_min != null ? b.eta_to_stop_min.toFixed(1)+" min" : "—");
      const speed = (b.speed_kmh != null ? b.speed_kmh.toFixed(1)+" km/h" : "—");
      const rec  = (b.recommended ? "Sí" : "No");

      const isRec = !!b.recommended;
      const color = isRec ? "green" : (b.approaching_stop === false ? "gray" : "blue");

      busMarkers[id] = L.circleMarker([la, lo], {
        radius: 6,
        opacity: 0.9,
        color: color,
        fillOpacity: 0.8
      }).addTo(map);

      let tooltip = `Bus: ${id}<br>Servicio: ${svc} (${dir})<br>Dist paradero: ${dist}<br>ETA paradero: ${eta}`;
      if (showRecommended) tooltip += `<br>Recomendado: ${isRec ? "Sí ✅" : "No"}`;
      busMarkers[id].bindTooltip(tooltip);

      // Rutas visuales
      if (stop){
        const poly1 = L.polyline([[la, lo], [stop[0], stop[1]]], {
          color: color,
          weight: isRec ? 4 : 2,
          opacity: 0.7
        }).addTo(map);
        routeLayers.push(poly1);
      }
      if (showRecommended && isRec && stop && dest){
        const poly2 = L.polyline([[stop[0], stop[1]], [dest[0], dest[1]]], {
          color: "orange",
          weight: 3,
          opacity: 0.6,
          dashArray: "6,6"
        }).addTo(map);
        routeLayers.push(poly2);
      }

      rows.push(`
        <tr>
          <td><b>${id}</b></td>
          <td>${svc}</td>
          <td>${dir}</td>
          <td>${dist}</td>
          <td>${eta}</td>
          <td>${speed}</td>
          ${showRecommended ? `<td>${rec}</td>` : ""}
        </tr>
      `);
    }

    let html = `<table>
      <tr>
        <th>Bus</th><th>Servicio</th><th>Sentido</th>
        <th>Distancia al paradero</th><th>ETA al paradero</th><th>Velocidad</th>
        ${showRecommended ? "<th>Recomendado</th>" : ""}
      </tr>`;
    if (rows.length === 0){
      html += `<tr><td colspan="${showRecommended ? 7 : 6}"><i>Sin buses para los filtros y ubicación seleccionados</i></td></tr>`;
    } else {
      html += rows.join("");
    }
    html += "</table>";
    resultsEl.innerHTML = html;
  }

  // ======== auto-refresh ========
  function setAutoRefresh(newMode){
    currentMode = newMode;
    if (refreshTimer) {
      clearInterval(refreshTimer);
      refreshTimer = null;
    }
    if (!newMode) {
      statusEl.textContent = "Actualización automática detenida.";
      return;
    }
    refreshTimer = setInterval(() => {
      if (currentMode === "near") {
        loadBusesNearStop(false);
      } else if (currentMode === "reco") {
        loadRecommendations(false);
      }
    }, 15000); // cada 15 segundos
  }

  btnStopAuto.onclick = () => setAutoRefresh(null);

  // ======== llamadas al backend ========

  async function loadBusesNearStop(showAlerts=true){
    readCoordsFromInputs();
    if (stopLat == null || stopLon == null){
      if (showAlerts) alert("Primero marca un PARADERO en el mapa.");
      return;
    }

    let url = `/api/buses_near_stop?stop_lat=${encodeURIComponent(stopLat)}&stop_lon=${encodeURIComponent(stopLon)}`;
    const svc = (serviceInput.value || "").trim();
    const dir = (directionInput.value || "").trim();
    if (svc) url += `&service=${encodeURIComponent(svc)}`;
    if (dir) url += `&direction=${encodeURIComponent(dir)}`;

    try {
      const res = await fetch(url);
      const j = await res.json();
      if (!j.ok){
        if (showAlerts) alert("Error WS: " + (j.error || "desconocido"));
        return;
      }
      const count = j.buses.length;
      const now = new Date().toLocaleTimeString();
      statusEl.textContent = `(${now}) Buses que se aproximan al paradero: ${count}`;
      renderBuses(j.buses, {showRecommended:false, stop:j.stop, dest:null});
    } catch (e) {
      console.error(e);
      if (showAlerts) alert("Error consultando backend (revisa consola).");
    }
  }

  async function loadRecommendations(showAlerts=true){
    readCoordsFromInputs();
    if (stopLat == null || stopLon == null){
      if (showAlerts) alert("Primero marca un PARADERO en el mapa.");
      return;
    }
    if (destLat == null || destLon == null){
      if (showAlerts) alert("Ahora marca un DESTINO en el mapa.");
      return;
    }

    let url = `/api/recommendations?stop_lat=${encodeURIComponent(stopLat)}&stop_lon=${encodeURIComponent(stopLon)}&dest_lat=${encodeURIComponent(destLat)}&dest_lon=${encodeURIComponent(destLon)}`;
    const svc = (serviceInput.value || "").trim();
    const dir = (directionInput.value || "").trim();
    if (svc) url += `&service=${encodeURIComponent(svc)}`;
    if (dir) url += `&direction=${encodeURIComponent(dir)}`;

    try {
      const res = await fetch(url);
      const j = await res.json();
      if (!j.ok){
        if (showAlerts) alert("Error WS: " + (j.error || "desconocido"));
        return;
      }
      const recs = j.buses.filter(b => b.recommended);
      const total = j.buses.length;
      const now = new Date().toLocaleTimeString();
      statusEl.textContent = `(${now}) Recomendaciones: ${recs.length} buses recomendados de ${total} candidatos.`;
      renderBuses(j.buses, {showRecommended:true, stop:j.stop, dest:j.dest || null});
    } catch (e) {
      console.error(e);
      if (showAlerts) alert("Error consultando backend (revisa consola).");
    }
  }

  // Botones principales
  btnBusesStop.onclick = async () => {
    await loadBusesNearStop(true);
    setAutoRefresh("near");
  };

  btnRecommendations.onclick = async () => {
    await loadRecommendations(true);
    setAutoRefresh("reco");
  };

  // Mensaje inicial
  setModeClick("stop");
})();
</script>
</body>
</html>
"""


# ==================== LÓGICA WS OFICIAL ====================

def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Ángulo (0–360) desde (lat1,lon1) hacia (lat2,lon2), 0 = Norte."""
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlon_rad = math.radians(lon2 - lon1)
    x = math.sin(dlon_rad) * math.cos(lat2_rad)
    y = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(dlon_rad)
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360.0) % 360.0


def _fetch_official_positions(
    service: Optional[str] = None,
    direction: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Llama al Web Service JSON de posicionamiento (DyS) y devuelve
    una lista normalizada de buses.
    """

    if not WS_POS_URL:
        raise RuntimeError("Configura WS_POS_URL en las variables de entorno")

    # Parámetros de entrada al WS (ajusta los nombres si tu doc usa otros)
    params: Dict[str, Any] = {}
    if service:
        params["servicio"] = service      # CAMBIA "servicio" si la API usa otro nombre
    if direction:
        params["sentido"] = direction     # CAMBIA "sentido" si la API usa otro nombre

    headers: Dict[str, str] = {}
    if WS_POS_TOKEN:
        headers["Authorization"] = f"Bearer {WS_POS_TOKEN}"

    auth = (WS_POS_USER, WS_POS_PASS) if (WS_POS_USER and WS_POS_PASS) else None

    resp = requests.get(
        WS_POS_URL,
        params=params,
        headers=headers or None,
        auth=auth,
        timeout=20,
    )
    resp.raise_for_status()

    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError("La respuesta del WS no es JSON (revisa URL / método).")

    posiciones = data.get("posiciones") or data.get("Posiciones") or []
    flat_records: List[Dict[str, Any]] = []

    # Parsear cada item de 'posiciones' (cadenas con ';')
    for item in posiciones:
        if isinstance(item, dict):
            flat_records.append(item)
            continue

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

                rec = {
                    "Fecha Hora Gps UTC": fecha_gps,
                    "Patente": patente,
                    "Latitud": lat_str,
                    "Longitud": lon_str,
                    "Velocidad Instantánea": vel_str,
                    "Direccion Geografica": dir_geo,
                    "Numero Operador": num_operador,
                    "Nombre Comercial del Servicio": nom_servicio,
                    "Sentido": sentido,
                    "Ruta Consola": ruta_consola,
                    "Ruta Sinoptico": ruta_sinoptico,
                    "Fecha Hora Insercion UTC": fecha_insert,
                }
                flat_records.append(rec)

        if isinstance(item, list):
            for sub in item:
                if isinstance(sub, dict):
                    flat_records.append(sub)
                elif isinstance(sub, str):
                    tokens = [t.strip() for t in sub.split(";") if t.strip() != ""]
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
                        rec = {
                            "Fecha Hora Gps UTC": fecha_gps,
                            "Patente": patente,
                            "Latitud": lat_str,
                            "Longitud": lon_str,
                            "Velocidad Instantánea": vel_str,
                            "Direccion Geografica": dir_geo,
                            "Numero Operador": num_operador,
                            "Nombre Comercial del Servicio": nom_servicio,
                            "Sentido": sentido,
                            "Ruta Consola": ruta_consola,
                            "Ruta Sinoptico": ruta_sinoptico,
                            "Fecha Hora Insercion UTC": fecha_insert,
                        }
                        flat_records.append(rec)

    # Último registro por patente
    by_patente: Dict[str, Dict[str, Any]] = {}
    for rec in flat_records:
        patente = rec.get("Patente") or rec.get("patente") or rec.get("PATENTE")
        if not patente:
            continue
        by_patente[str(patente)] = rec

    def _to_float(value) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(str(value).replace(",", "."))
        except Exception:
            return None

    result: List[Dict[str, Any]] = []

    headings_map = {0: 0.0, 1: 45.0, 2: 90.0, 3: 135.0, 4: 180.0, 5: 225.0, 6: 270.0, 7: 315.0}

    for patente, rec in by_patente.items():
        lat = _to_float(rec.get("Latitud"))
        lon = _to_float(rec.get("Longitud"))
        if lat is None or lon is None:
            continue

        speed = _to_float(rec.get("Velocidad Instantánea")) or 20.0
        service_code = rec.get("Nombre Comercial del Servicio")
        sentido = rec.get("Sentido")

        dir_raw = rec.get("Direccion Geografica")
        heading_idx: Optional[int] = None
        heading_deg: Optional[float] = None
        if dir_raw is not None and str(dir_raw).strip() != "":
            try:
                heading_idx = int(str(dir_raw).strip())
                heading_deg = headings_map.get(heading_idx % 8)
            except ValueError:
                pass

        result.append({
            "bus_id": patente,
            "lat": lat,
            "lon": lon,
            "speed_kmh": speed,
            "service": service_code,
            "direction": sentido,
            "heading_idx": heading_idx,
            "heading_deg": heading_deg,
            "raw": rec,
        })

    # Filtro opcional por servicio/sentido
    if service:
        result = [
            b for b in result
            if str(b.get("service") or "").upper() == str(service).upper()
        ]
    if direction:
        result = [
            b for b in result
            if str(b.get("direction") or "").upper() == str(direction).upper()
        ]

    return result


# ==================== ENDPOINTS ====================

@app.route("/")
def index():
  return render_template_string(
      INDEX_HTML,
      center_lat=DEFAULT_CENTER[0],
      center_lon=DEFAULT_CENTER[1],
  )


@app.route("/api/buses_near_stop")
def api_buses_near_stop():
    try:
        stop_lat = float(request.args["stop_lat"])
        stop_lon = float(request.args["stop_lon"])
    except (KeyError, ValueError):
        return jsonify({"ok": False, "error": "stop_lat y stop_lon son obligatorios"}), 400

    service = request.args.get("service")
    direction = request.args.get("direction")

    try:
        buses_raw = _fetch_official_positions(service, direction)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    MAX_DIST_KM = 5.0
    out: List[Dict[str, Any]] = []

    for b in buses_raw:
        lat = b["lat"]
        lon = b["lon"]
        dist_km = geodesic((lat, lon), (stop_lat, stop_lon)).km
        if dist_km > MAX_DIST_KM:
            continue

        speed = max(float(b.get("speed_kmh", 20.0)), 1e-3)
        eta_min = (dist_km / speed) * 60.0

        heading_deg = b.get("heading_deg")
        angle_to_stop = None
        approaching_stop = None
        if heading_deg is not None:
            bearing_to_stop = _bearing_deg(lat, lon, stop_lat, stop_lon)
            diff = abs(heading_deg - bearing_to_stop)
            if diff > 180.0:
                diff = 360.0 - diff
            angle_to_stop = diff
            approaching_stop = diff <= 90.0

        if heading_deg is not None and approaching_stop is False:
            continue

        out.append({
            "bus_id": b["bus_id"],
            "service": b.get("service"),
            "direction": b.get("direction"),
            "lat": lat,
            "lon": lon,
            "speed_kmh": speed,
            "distance_to_stop_km": dist_km,
            "eta_to_stop_min": eta_min,
            "heading_idx": b.get("heading_idx"),
            "heading_deg": heading_deg,
            "angle_to_stop_deg": angle_to_stop,
            "approaching_stop": approaching_stop,
            "recommended": False,  # aquí no recomendamos, solo mostramos
        })

    out.sort(key=lambda x: x["eta_to_stop_min"])
    return jsonify({"ok": True, "stop": [stop_lat, stop_lon], "buses": out})


@app.route("/api/recommendations")
def api_recommendations():
    try:
        stop_lat = float(request.args["stop_lat"])
        stop_lon = float(request.args["stop_lon"])
        dest_lat = float(request.args["dest_lat"])
        dest_lon = float(request.args["dest_lon"])
    except (KeyError, ValueError):
        return jsonify({"ok": False, "error": "stop_lat, stop_lon, dest_lat, dest_lon son obligatorios"}), 400

    service = request.args.get("service")
    direction = request.args.get("direction")

    try:
        buses_raw = _fetch_official_positions(service, direction)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    MAX_DIST_KM = 5.0
    bearing_to_dest = _bearing_deg(stop_lat, stop_lon, dest_lat, dest_lon)

    out: List[Dict[str, Any]] = []

    for b in buses_raw:
        lat = b["lat"]
        lon = b["lon"]
        dist_to_stop = geodesic((lat, lon), (stop_lat, stop_lon)).km
        if dist_to_stop > MAX_DIST_KM:
            continue

        speed = max(float(b.get("speed_kmh", 20.0)), 1e-3)
        eta_stop = (dist_to_stop / speed) * 60.0

        heading_deg = b.get("heading_deg")
        heading_idx = b.get("heading_idx")

        angle_to_stop = None
        approaching_stop = None
        angle_to_dest = None
        recommended = False

        if heading_deg is not None:
            bearing_to_stop = _bearing_deg(lat, lon, stop_lat, stop_lon)
            diff_stop = abs(heading_deg - bearing_to_stop)
            if diff_stop > 180.0:
                diff_stop = 360.0 - diff_stop
            angle_to_stop = diff_stop
            approaching_stop = diff_stop <= 90.0

            diff_dest = abs(heading_deg - bearing_to_dest)
            if diff_dest > 180.0:
                diff_dest = 360.0 - diff_dest
            angle_to_dest = diff_dest

            if approaching_stop and diff_dest <= 90.0 and eta_stop <= 30.0:
                recommended = True

        if heading_deg is not None and approaching_stop is False:
            continue

        out.append({
            "bus_id": b["bus_id"],
            "service": b.get("service"),
            "direction": b.get("direction"),
            "lat": lat,
            "lon": lon,
            "speed_kmh": speed,
            "distance_to_stop_km": dist_to_stop,
            "eta_to_stop_min": eta_stop,
            "heading_idx": heading_idx,
            "heading_deg": heading_deg,
            "angle_to_stop_deg": angle_to_stop,
            "angle_to_dest_deg": angle_to_dest,
            "approaching_stop": approaching_stop,
            "recommended": recommended,
        })

    out.sort(key=lambda b: (not b["recommended"], b["eta_to_stop_min"]))

    return jsonify({
        "ok": True,
        "stop": [stop_lat, stop_lon],
        "dest": [dest_lat, dest_lon],
        "bearing_stop_to_dest_deg": bearing_to_dest,
        "buses": out,
    })


# ==================== MAIN ====================

if __name__ == "__main__":
    print("Servidor iniciado. Abre http://127.0.0.1:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
