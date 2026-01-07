# --- MÓDULO DE PETICIONES A LA API DE SAMSARA ---
# Este archivo encapsula toda la lógica para comunicarse con la API de Samsara.
# Contiene funciones para obtener los vehículos (assets) y para calcular
# el tiempo de viaje de cada uno, manejando la paginación de la API.

import logging
import requests
from datetime import datetime, timezone
from .db import save_to_file

# --- FUNCIONES DE UTILIDAD PARA MANEJO DE TIEMPO ---

def dt_to_ms(dt: datetime) -> int:
    """
    Convierte un objeto `datetime` de Python a milisegundos desde la Época (Unix time).

    La API de Samsara requiere que los rangos de tiempo se especifiquen en milisegundos.
    Esta función se asegura de que la fecha esté en UTC antes de la conversión.

    Args:
        dt (datetime): El objeto de fecha a convertir.

    Returns:
        int: El timestamp correspondiente en milisegundos.
    """
    try:
        # Si el datetime es "naive" (no tiene zona horaria), se asume que es UTC.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # Si es "aware" (tiene zona horaria), se convierte a UTC.
        else:
            dt = dt.astimezone(timezone.utc)
        # `timestamp()` da el tiempo en segundos, se multiplica por 1000 para obtener milisegundos.
        return int(dt.timestamp() * 1000)
    except Exception as e:
        logging.error("Error convirtiendo fecha a milisegundos: %s", e)
        return 0
    
def ms_to_dt(ms: int) -> datetime:
    """
    Convierte milisegundos desde la Época (Unix time) a un objeto `datetime` de Python en UTC.
    Útil para depuración, para convertir los timestamps de la API a un formato legible.

    Args:
        ms (int): El timestamp en milisegundos.

    Returns:
        datetime: El objeto de fecha correspondiente, con zona horaria UTC.
    """
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)

# --- FUNCIONES DE LLAMADA A LA API ---

def obtain_assets(session: requests.Session, api_url_assets: str, headers: dict) -> list[dict]:
    """
    Obtiene una lista de todos los vehículos (assets) de la API de Samsara.

    Maneja la paginación de la API, que es un mecanismo por el cual la API entrega
    los resultados en "páginas" en lugar de todos a la vez. Esta función pide
    páginas una por una hasta que no quedan más.

    Args:
        session (requests.Session): La sesión de requests para reutilizar la conexión.
        api_url_assets (str): La URL del endpoint de la API para los assets.
        headers (dict): Las cabeceras de autenticación.

    Returns:
        list[dict]: Una lista de diccionarios, donde cada uno representa un vehículo
                    con su 'id' y 'name'.
    """
    after = None  # 'after' es el cursor que le dice a la API desde dónde empezar la siguiente página.
    data: list[dict] = []
    
    while True:  # Bucle infinito que se romperá cuando no haya más páginas.
        params = {"type": "vehicle"}  # Filtra para obtener solo assets de tipo "vehículo".
        if after:
            params["after"] = after  # En peticiones subsiguientes, se añade el cursor.
            
        try:
            # Se hace la petición GET a la API.
            r = session.get(api_url_assets, headers=headers, params=params, timeout=30)
            if r.status_code != 200:
                # Si la respuesta no es exitosa (código 200), se registra el error y se detiene.
                logging.error("Error en API Assets: status=%s body=%s", r.status_code, r.text)
                break
            
            body = r.json()  # Se convierte la respuesta JSON a un diccionario de Python.
            for asset in body.get("data", []):
                # Se extrae solo la información necesaria (id y nombre) de cada asset.
                if "name" in asset and "id" in asset:
                    data.append({"name": asset["name"], "id": asset["id"]})
            
            # Se revisa la sección 'pagination' de la respuesta de la API.
            pag = body.get("pagination") or {}
            if pag.get("hasNextPage"):
                # Si hay una página siguiente, se guarda el 'endCursor' para la próxima iteración.
                after = pag.get("endCursor")
                if not after:
                    # Si la API dice que hay más páginas pero no da un cursor, es un error.
                    logging.error("Paginación inconsistente: hasNextPage es true pero no hay endCursor.")
                    break
            else:
                # Si no hay más páginas, se rompe el bucle.
                break
        except requests.exceptions.RequestException as e:
            # Captura errores de red (ej. no hay conexión, el DNS falla).
            logging.error("Error de red al consultar API Assets: %s", e)
            break
    return data    

def request_travel_time(session: requests.Session, api_url_trips: str, asset: dict, start_ms: int, end_ms: int, headers: dict) -> int:
    """
    Calcula el tiempo total de viaje (en segundos) para un único vehículo en un rango de tiempo.

    Suma la duración de todos los "viajes" que la API reporta para ese vehículo.
    También maneja la paginación, ya que un vehículo puede tener muchos viajes.

    Args:
        session (requests.Session): La sesión de requests.
        api_url_trips (str): La URL del endpoint de la API para los viajes.
        asset (dict): El diccionario del vehículo (con 'id' y 'name').
        start_ms (int): El timestamp de inicio del rango en milisegundos.
        end_ms (int): El timestamp de fin del rango en milisegundos.
        headers (dict): Las cabeceras de autenticación.

    Returns:
        int: El total de segundos de viaje acumulados.
    """
    after = None
    travel_time = 0  # Acumulador para el tiempo de viaje en segundos.
    
    params = {
        "vehicleId": asset["id"],
        "startMs": start_ms,
        "endMs": end_ms
    }
    
    while True:  # Bucle de paginación.
        if after:
            params["after"] = after
        try:
            r = session.get(api_url_trips, headers=headers, params=params, timeout=30)
            if r.status_code != 200:
                logging.error("Error en API Travel Time para %s: status=%s body=%s", asset.get('name'), r.status_code, r.text)
                return 0
            
            body = r.json()
            
            # Se itera sobre cada 'viaje' en la respuesta.
            for trip in body.get('trips', []):
                s = trip.get('startMs')  # Inicio del viaje.
                e = trip.get('endMs')    # Fin del viaje.
                
                # A veces la API devuelve un valor máximo de entero si el viaje está en curso.
                # En ese caso, se usa el `end_ms` de nuestra petición como fin del viaje.
                e = end_ms if e == 9223372036854775807 else e
                
                # Se valida que los tiempos sean números y coherentes.
                if isinstance(s, int) and isinstance(e, int) and e >= s:
                    # Se calcula la duración del viaje en segundos y se suma al total.
                    travel_time += (e - s) // 1000
            
            # Lógica de paginación, idéntica a `obtain_assets`.
            pag = body.get("pagination") or {}
            if pag.get("hasNextPage"):
                after = pag.get("endCursor")
                if not after:
                    logging.error("Paginación inconsistente en API Travel Time para %s.", asset.get('name'))
                    break
            else:
                break
        except requests.exceptions.RequestException as e:
            logging.error("Error de red al consultar API Travel Time para %s: %s", asset.get('name'), e)
            return 0
            
    return travel_time


def request_stopped_time(session: requests.Session, api_url_trips: str, asset: dict, start_ms: int, end_ms: int, headers: dict) -> int:
    """
    Calcula el tiempo total de detención (en segundos) para un único vehículo,
    acumulando solo las paradas de más de 3 horas.

    Args:
        session (requests.Session): La sesión de requests.
        api_url_trips (str): La URL del endpoint de la API para los viajes.
        asset (dict): El diccionario del vehículo (con 'id' y 'name').
        start_ms (int): El timestamp de inicio del rango en milisegundos.
        end_ms (int): El timestamp de fin del rango en milisegundos.
        headers (dict): Las cabeceras de autenticación.

    Returns:
        int: El total de segundos de detención acumulados de paradas de más de 3 horas.
    """
    after = None
    all_trips = []
    
    params = {
        "vehicleId": asset["id"],
        "startMs": start_ms,
        "endMs": end_ms
    }

    # 1. Recopilar todos los viajes del vehículo en el rango de tiempo.
    while True:
        if after:
            params["after"] = after
        try:
            r = session.get(api_url_trips, headers=headers, params=params, timeout=30)
            if r.status_code != 200:
                logging.error("Error en API Trips (para detenciones) para %s: status=%s body=%s", asset.get('name'), r.status_code, r.text)
                return 0
            
            body = r.json()
            all_trips.extend(body.get('trips', []))
            
            pag = body.get("pagination") or {}
            if pag.get("hasNextPage"):
                after = pag.get("endCursor")
                if not after:
                    logging.error("Paginación inconsistente en API Trips (para detenciones) para %s.", asset.get('name'))
                    break
            else:
                break
        except requests.exceptions.RequestException as e:
            logging.error("Error de red al consultar API Trips (para detenciones) para %s: %s", asset.get('name'), e)
            return 0

    if not all_trips:
        return 0

    # 2. Ordenar los viajes por su hora de inicio.
    all_trips.sort(key=lambda x: x.get('startMs', 0))

    # 3. Calcular el tiempo detenido entre viajes.
    stopped_time_ms = 0
    three_hours_in_ms = 3 * 60 * 60 * 1000

    for i in range(len(all_trips) - 1):
        trip_current = all_trips[i]
        trip_next = all_trips[i+1]

        end_current_trip = trip_current.get('endMs')
        start_next_trip = trip_next.get('startMs')

        if isinstance(end_current_trip, int) and isinstance(start_next_trip, int) and start_next_trip > end_current_trip:
            stop_duration = start_next_trip - end_current_trip
            if stop_duration > three_hours_in_ms:
                stopped_time_ms += stop_duration
    
    return stopped_time_ms // 1000