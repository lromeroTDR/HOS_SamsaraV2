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
# Carga de variables de entorno desde un archivo .env para desarrollo local.
load_dotenv()

# Configuración básica del logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- VARIABLES DE ENTORNO Y CONSTANTES ---
# Lectura de URLs y tokens desde el entorno. Es crucial que estas variables estén definidas.
API_TOKEN = os.getenv("API_TOKEN")
API_URL_TRIPS = os.getenv("API_URL_TRIPS")
API_URL_ASSETS = os.getenv("API_URL_ASSETS")
SAMSARA_TAGS_URL = os.getenv("SAMSARA_TAGS_URL")
OUTPUT_CSV_PATH = "data/resultado_etl.csv"  # Ruta de salida para el archivo CSV

# Validación de que las variables de entorno necesarias están presentes.
required_vars = ["API_TOKEN", "API_URL_TRIPS", "API_URL_ASSETS", "SAMSARA_TAGS_URL"]
missing_vars = [var for var in required_vars if not globals()[var]]
if missing_vars:
    error_message = f"Faltan las siguientes variables de entorno: {', '.join(missing_vars)}"
    logging.error(error_message)
    raise EnvironmentError(error_message)

# --- FUNCIONES DE AUTENTICACIÓN Y UTILIDAD DE TIEMPO (Extraídas) ---

def auth_headers() -> Dict[str, str]:
    """Crea el diccionario de cabeceras para la autenticación en la API de Samsara."""
    return {
        "accept": "application/json",
        "authorization": "Bearer " + API_TOKEN
    }

def dt_to_ms(dt: datetime) -> int:
    """Convierte un objeto datetime de Python a milisegundos en UTC."""
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    else:
        dt = dt.astimezone(pytz.utc)
    return int(dt.timestamp() * 1000)

# --- FUNCIONES DE EXTRACCIÓN DE DATOS DE SAMSARA (Extraídas) ---

def transformar_tags(df_tags: pd.DataFrame) -> pd.DataFrame:
    """Filtra y transforma los tags para obtener Proyecto y EC por vehículo."""
    lista_EC: List[str] = [f"EC-{str(i).zfill(2)}" for i in range(1, 11)]
    
    if "parentTag.name" not in df_tags.columns:
        logging.warning("No se encuentra la columna 'parentTag.name' para filtrar por EC.")
        return pd.DataFrame()

    df_filtrado = df_tags[df_tags["parentTag.name"].isin(lista_EC)].copy()
    if df_filtrado.empty:
        logging.warning("No se encontraron tags que coincidan con los equipos colaborativos (EC).")
        return pd.DataFrame()
    
    df_explotado = df_filtrado.explode("vehicles").dropna(subset=['vehicles'])
    if df_explotado.empty:
        logging.warning("Los tags de EC filtrados no tienen vehículos asociados.")
        return pd.DataFrame()

    res = pd.concat([
        df_explotado[["tagName", "parentTag.name"]].reset_index(drop=True),
        pd.json_normalize(df_explotado["vehicles"]).reset_index(drop=True)
    ], axis=1)
    
    res = res.rename(columns={'tagName': 'Proyecto', 'parentTag.name': 'EC'})
    return res

def obtener_datos_proyectos_ec(headers: Dict[str, str], url: str) -> pd.DataFrame:
    """Orquesta la obtención y transformación de tags de Samsara."""
    logging.info("Iniciando la obtención de datos de Proyectos y EC desde Samsara...")
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json().get('data', [])
        if not data:
            logging.warning("La API de Samsara no devolvió tags.")
            return pd.DataFrame()

        df_tags = pd.json_normalize(data, sep='.')
        df_tags = df_tags.rename(columns={'id': 'tagId', 'name': 'tagName', 'parentTagId': 'parentTagId'})
        
        df_proyectos = transformar_tags(df_tags)
        if not df_proyectos.empty:
            return df_proyectos[['Proyecto', 'EC', 'id', 'name']]
        return pd.DataFrame()
    except requests.exceptions.RequestException as e:
        logging.error(f"ERROR al obtener o procesar los tags de Samsara: {e}")
        return pd.DataFrame()

def obtain_assets(session: requests.Session, api_url_assets: str, headers: dict) -> List[Dict]:
    """Obtiene la lista de todos los vehículos (assets) manejando paginación."""
    after = None
    data: List[Dict] = []
    while True:
        params = {"type": "vehicle"}
        if after:
            params["after"] = after
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
        except requests.exceptions.RequestException as e:
            logging.error(f"Error de red al consultar API Assets: {e}")
            break
    return data

def request_time(session: requests.Session, api_url: str, asset: Dict, start_ms: int, end_ms: int, headers: dict, mode: str) -> int:
    """Función genérica para calcular tiempo de viaje o de parada."""
    after = None
    total_seconds = 0
    all_trips = []

    params = {"vehicleId": asset["id"], "startMs": start_ms, "endMs": end_ms}

    while True:
        if after:
            params["after"] = after
        try:
            r = session.get(api_url, headers=headers, params=params, timeout=30)
            if r.status_code != 200:
                logging.error(f"Error en API para {asset.get('name')} ({mode}): status={r.status_code} body={r.text}")
                return 0
            
            body = r.json()
            trips = body.get('trips', [])
            
            if mode == 'travel':
                for trip in trips:
                    s, e = trip.get('startMs'), trip.get('endMs', end_ms)
                    e = end_ms if e == 9223372036854775807 else e
                    if isinstance(s, int) and isinstance(e, int) and e >= s:
                        total_seconds += (e - s) // 1000
            elif mode == 'stopped':
                 all_trips.extend(trips)

            pag = body.get("pagination", {})
            if pag.get("hasNextPage") and pag.get("endCursor"):
                after = pag.get("endCursor")
            else:
                break
        except requests.exceptions.RequestException as e:
            logging.error(f"Error de red en API para {asset.get('name')} ({mode}): {e}")
            return 0
    
    if mode == 'stopped':
        if not all_trips:
            return 0
        all_trips.sort(key=lambda x: x.get('startMs', 0))
        stopped_time_ms = 0
        three_hours_in_ms = 3 * 60 * 60 * 1000
        #three_hours_in_ms = 0
        for i in range(len(all_trips) - 1):
            end_current = all_trips[i].get('endMs')
            start_next = all_trips[i+1].get('startMs')
            if isinstance(end_current, int) and isinstance(start_next, int) and start_next > end_current:
                stop_duration = start_next - end_current
                if stop_duration > three_hours_in_ms:
                    stopped_time_ms += stop_duration
        total_seconds = stopped_time_ms // 1000
            
    return total_seconds

# --- FUNCIÓN PRINCIPAL DE ORQUESTACIÓN ---

def run_etl():
    """
    Orquesta el proceso de ETL: Extrae, Transforma y Guarda en CSV.
    """
    logging.info("Iniciando proceso de ETL.")
    
    # --- 1. Definir Rango de Tiempo ---
    # Se procesan los datos del día actual, desde las 00:00 hasta el momento de ejecución.
    mexico_tz = pytz.timezone("America/Mexico_City")
    now_utc = datetime.now(pytz.utc)
    start_of_day_local = datetime.now(mexico_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_day_utc = start_of_day_local.astimezone(pytz.utc)
    
    start_ms = dt_to_ms(start_of_day_utc)
    end_ms = dt_to_ms(now_utc)
    
    logging.info(f"Rango de procesamiento: {start_of_day_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC a {now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC")

    # --- 2. Extracción y Transformación ---
    headers = auth_headers()
    session = requests.Session()
    
    # Obtener datos de proyectos y crear un diccionario para búsqueda rápida.
    df_proyectos = obtener_datos_proyectos_ec(headers, SAMSARA_TAGS_URL)
    proyectos_lookup = {}
    if not df_proyectos.empty:
        proyectos_lookup = df_proyectos.set_index('name')[['Proyecto', 'EC']].to_dict('index')
    
    # Obtener todos los vehículos.
    assets = obtain_assets(session, API_URL_ASSETS, headers)
    logging.info(f"Se obtuvieron {len(assets)} vehículos.")
    
    # Procesar cada vehículo para obtener sus tiempos.
    processed_data = []
    for i, asset in enumerate(assets):
        asset_name = asset['name']
        travel_secs = request_time(session, API_URL_TRIPS, asset, start_ms, end_ms, headers, 'travel')
        stopped_secs = request_time(session, API_URL_TRIPS, asset, start_ms, end_ms, headers, 'stopped')
        
        proyecto_data = proyectos_lookup.get(asset_name, {})
        proyecto = proyecto_data.get('Proyecto')
        ec = proyecto_data.get('EC')

        logging.info(f"Procesando {i+1}/{len(assets)}: {asset_name} - Viaje: {travel_secs}s, Detenido: {stopped_secs}s")
        
        processed_data.append({
            "asset_id": asset['id'],
            "asset_name": asset_name,
            "travel_seconds": travel_secs,
            "travel_time_str": str(timedelta(seconds=travel_secs)),
            "stopped_seconds": stopped_secs,
            "stopped_time_str": str(timedelta(seconds=stopped_secs)),
            "proyecto": proyecto,
            "ec": ec,
            "fecha_procesamiento": start_of_day_utc.strftime('%Y-%m-%d')
        })
        
    # --- 3. Carga ---
    # Convertir la lista de diccionarios a un DataFrame de pandas y guardar en CSV.
    if processed_data:
        df_final = pd.DataFrame(processed_data)
        logging.info(f"Creando archivo CSV en: {OUTPUT_CSV_PATH}")
        # Asegurarse de que el directorio de datos exista
        os.makedirs(os.path.dirname(OUTPUT_CSV_PATH), exist_ok=True)
        df_final.to_csv(OUTPUT_CSV_PATH, index=False, encoding='utf-8')
        logging.info(f"Proceso completado. Se guardaron {len(df_final)} registros en {OUTPUT_CSV_PATH}.")
    else:
        logging.warning("No se procesaron datos, no se generó ningún archivo CSV.")

# --- PUNTO DE ENTRADA DEL SCRIPT ---
if __name__ == "__main__":
    run_etl()
