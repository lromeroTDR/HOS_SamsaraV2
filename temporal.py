# temporal.py
# Este script extrae datos de la API de Samsara, los transforma y los guarda en un archivo CSV.

import os
import requests
import pandas as pd
import pytz
from datetime import datetime, timedelta
import logging
from typing import Dict, List, Tuple
from dotenv import load_dotenv

# --- CONFIGURACIÓN INICIAL ---
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- VARIABLES DE ENTORNO Y CONSTANTES ---
API_TOKEN = os.getenv("API_TOKEN")
API_URL_TRIPS = os.getenv("API_URL_TRIPS")
API_URL_ASSETS = os.getenv("API_URL_ASSETS")
SAMSARA_TAGS_URL = os.getenv("SAMSARA_TAGS_URL")
OUTPUT_CSV_PATH = "data/resultado_etl.csv"

# Validación de variables
required_vars = ["API_TOKEN", "API_URL_TRIPS", "API_URL_ASSETS", "SAMSARA_TAGS_URL"]
missing_vars = [var for var in required_vars if not globals()[var]]
if missing_vars:
    error_message = f"Faltan las siguientes variables de entorno: {', '.join(missing_vars)}"
    logging.error(error_message)
    raise EnvironmentError(error_message)

# --- FUNCIONES DE UTILIDAD ---

def auth_headers() -> Dict[str, str]:
    return {
        "accept": "application/json",
        "authorization": "Bearer " + API_TOKEN
    }

def dt_to_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    else:
        dt = dt.astimezone(pytz.utc)
    return int(dt.timestamp() * 1000)

# --- FUNCIONES DE EXTRACCIÓN Y TRANSFORMACIÓN ---

def transformar_tags(df_tags: pd.DataFrame) -> pd.DataFrame:
    lista_EC: List[str] = [f"EC-{str(i).zfill(2)}" for i in range(1, 11)]
    
    if "parentTag.name" not in df_tags.columns:
        return pd.DataFrame()

    df_filtrado = df_tags[df_tags["parentTag.name"].isin(lista_EC)].copy()
    if df_filtrado.empty:
        return pd.DataFrame()
    
    df_explotado = df_filtrado.explode("vehicles").dropna(subset=['vehicles'])
    if df_explotado.empty:
        return pd.DataFrame()

    res = pd.concat([
        df_explotado[["tagName", "parentTag.name"]].reset_index(drop=True),
        pd.json_normalize(df_explotado["vehicles"]).reset_index(drop=True)
    ], axis=1)
    
    res = res.rename(columns={'tagName': 'Proyecto', 'parentTag.name': 'EC'})
    return res

def obtener_datos_proyectos_ec(headers: Dict[str, str], url: str) -> pd.DataFrame:
    logging.info("Iniciando la obtención de datos de Proyectos y EC desde Samsara...")
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json().get('data', [])
        if not data:
            return pd.DataFrame()

        df_tags = pd.json_normalize(data, sep='.')
        df_tags = df_tags.rename(columns={'id': 'tagId', 'name': 'tagName'})
        
        df_proyectos = transformar_tags(df_tags)
        if not df_proyectos.empty:
            return df_proyectos[['Proyecto', 'EC', 'id', 'name']]
        return pd.DataFrame()
    except Exception as e:
        logging.error(f"Error en obtener_datos_proyectos_ec: {e}")
        return pd.DataFrame()

def obtain_assets(session: requests.Session, api_url_assets: str, headers: dict) -> List[Dict]:
    after = None
    data = []
    while True:
        params = {"type": "vehicle"}
        if after: params["after"] = after
        try:
            r = session.get(api_url_assets, headers=headers, params=params, timeout=30)
            r.raise_for_status()
            body = r.json()
            for asset in body.get("data", []):
                if "name" in asset and "id" in asset:
                    data.append({"name": asset["name"], "id": asset["id"]})
            
            pag = body.get("pagination", {})
            if pag.get("hasNextPage") and pag.get("endCursor"):
                after = pag.get("endCursor")
            else:
                break
        except Exception as e:
            logging.error(f"Error en obtain_assets: {e}")
            break
    return data


def request_time(session: requests.Session, api_url: str, asset: Dict, start_ms: int, end_ms: int, headers: dict, mode: str) -> int:
    after = None
    total_seconds = 0
    all_trips = []
    params = {"vehicleId": asset["id"], "startMs": start_ms, "endMs": end_ms}

    while True:
        if after: params["after"] = after
        try:
            r = session.get(api_url, headers=headers, params=params, timeout=30)
            if r.status_code != 200: return 0
            body = r.json()
            trips = body.get('trips', [])

            if mode == 'travel':
                for trip in trips:
                    s = trip.get('startMs')
                    e = trip.get('endMs')
                    
                    # CORRECCIÓN LÓGICA:
                    # Si e es None o el valor máximo, el viaje sigue activo -> usamos end_ms (el ahora)
                    # Pero si e tiene un valor normal, usamos ESE valor, NO el final del reporte.
                    if e is None or e == 9223372036854775807:
                        e_final = end_ms 
                    else:
                        e_final = e
                    
                    if isinstance(s, int) and isinstance(e_final, int) and e_final >= s:
                        total_seconds += (e_final - s) // 1000
            
            elif mode == 'stopped':
                all_trips.extend(trips)

            pag = body.get("pagination", {})
            if pag.get("hasNextPage") and pag.get("endCursor"):
                after = pag.get("endCursor")
            else: break
        except Exception: return 0
    
    if mode == 'stopped':
        three_hours_in_ms = 3 * 60 * 60 * 1000
        stopped_time_ms = 0
        
        if not all_trips:
            # Si no hay viajes, todo el rango es tiempo detenido
            duration = end_ms - start_ms
            if duration > three_hours_in_ms: stopped_time_ms = duration
        else:
            all_trips.sort(key=lambda x: x.get('startMs', 0))
            
            # 1. Gap antes del primer viaje
            if all_trips[0]['startMs'] - start_ms > three_hours_in_ms:
                stopped_time_ms += (all_trips[0]['startMs'] - start_ms)

            # 2. Gaps entre viajes
            for i in range(len(all_trips) - 1):
                e_curr = all_trips[i].get('endMs')
                s_next = all_trips[i+1].get('startMs')
                if e_curr and e_curr != 9223372036854775807:
                    gap = s_next - e_curr
                    if gap > three_hours_in_ms: stopped_time_ms += gap

            # 3. Gap DESPUÉS del último viaje (Lo que te faltaba)
            last_end = all_trips[-1].get('endMs')
            if last_end and last_end != 9223372036854775807:
                gap_final = end_ms - last_end
                if gap_final > three_hours_in_ms:
                    stopped_time_ms += gap_final

        total_seconds = stopped_time_ms // 1000
            
    return total_seconds


# --- ORQUESTACIÓN ---

def run_etl():
    logging.info("Iniciando proceso de ETL.")
    
    #mexico_tz = pytz.timezone("America/Mexico_City")
    #now_utc = datetime.now(pytz.utc)
    #start_of_day_local = datetime.now(mexico_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    #start_of_day_utc = start_of_day_local.astimezone(pytz.utc)
    #start_ms = dt_to_ms(start_of_day_utc)
    #end_ms = dt_to_ms(now_utc)

    # 1. Configuración de tiempo
    mexico_tz = pytz.timezone("America/Mexico_City")
    now_utc = datetime.now(pytz.utc)
    # 2. MODIFICACIÓN: Calculamos la medianoche de AYER pero mantenemos el nombre de la variable
    # Restamos 1 día a la fecha actual de México y reseteamos a las 00:00:00
    start_of_day_local = (datetime.now(mexico_tz) - timedelta(days=1)).replace(
    hour=0, minute=0, second=0, microsecond=0
    )
    # 3. Convertir a UTC (esta es la variable que tu ETL busca en la línea 231)
    start_of_day_utc = start_of_day_local.astimezone(pytz.utc)
    # 4. Convertir a milisegundos para los parámetros de la API de Samsara
    start_ms = dt_to_ms(start_of_day_utc)
    end_ms = dt_to_ms(now_utc)


    headers = auth_headers()
    session = requests.Session()
    
    # 1. Obtener y limpiar proyectos (ELIMINA DUPLICADOS AQUÍ)
    df_proyectos = obtener_datos_proyectos_ec(headers, SAMSARA_TAGS_URL)
    proyectos_lookup = {}
    
    if not df_proyectos.empty:
        # Limpieza crucial para evitar ValueError: index must be unique
        df_proyectos['name'] = df_proyectos['name'].astype(str).str.strip()
        df_proyectos = df_proyectos.drop_duplicates(subset=['name'], keep='first')
        proyectos_lookup = df_proyectos.set_index('name')[['Proyecto', 'EC']].to_dict('index')
    
    # 2. Obtener y limpiar vehículos (assets)
    assets_raw = obtain_assets(session, API_URL_ASSETS, headers)
    if assets_raw:
        df_assets = pd.DataFrame(assets_raw)
        df_assets['name'] = df_assets['name'].astype(str).str.strip()
        df_assets = df_assets.drop_duplicates(subset=['name'], keep='first')
        assets = df_assets.to_dict('records')
    else:
        assets = []

    logging.info(f"Procesando {len(assets)} vehículos únicos.")
    
    processed_data = []
    for i, asset in enumerate(assets):
        asset_name = asset['name']
        travel_secs = request_time(session, API_URL_TRIPS, asset, start_ms, end_ms, headers, 'travel')
        stopped_secs = request_time(session, API_URL_TRIPS, asset, start_ms, end_ms, headers, 'stopped')
        
        # Búsqueda en el lookup de proyectos
        p_info = proyectos_lookup.get(asset_name, {})
        
        processed_data.append({
            "asset_id": asset['id'],
            "asset_name": asset_name,
            "travel_seconds": travel_secs,
            "travel_time_str": str(timedelta(seconds=travel_secs)),
            "stopped_seconds": stopped_secs,
            "stopped_time_str": str(timedelta(seconds=stopped_secs)),
            "proyecto": p_info.get('Proyecto'),
            "ec": p_info.get('EC'),
            "fecha_procesamiento": start_of_day_utc.strftime('%Y-%m-%d')
        })
        logging.info(f"Progreso: {i+1}/{len(assets)} - {asset_name}")
        
    # 3. Guardar resultados
    if processed_data:
        df_final = pd.DataFrame(processed_data)
        os.makedirs(os.path.dirname(OUTPUT_CSV_PATH), exist_ok=True)
        df_final.to_csv(OUTPUT_CSV_PATH, index=False, encoding='utf-8')
        logging.info(f"Éxito: {len(df_final)} registros guardados.")

if __name__ == "__main__":
    run_etl()
