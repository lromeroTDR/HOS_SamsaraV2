import pandas as pd
import requests
from typing import Dict, List

def transformar_tags(df_tags: pd.DataFrame) -> pd.DataFrame:
    """
    Filtra por equipos colaborativos, explota la columna de vehículos 
    y normaliza la información en un DataFrame limpio.
    """
    # 1. Definimos la lista de equipos colaborativos
    lista_EC: List[str] = [f"EC-{str(i).zfill(2)}" for i in range(1, 11)]
    
    # 2. Validamos que la columna exista antes de filtrar
    if "parentTag.name" not in df_tags.columns:
        print("Advertencia: No se encuentra la columna 'parentTag.name' para filtrar por EC. Se devolverá un DataFrame vacío.")
        return pd.DataFrame()

    # 3. Filtrado
    df_filtrado = df_tags[df_tags["parentTag.name"].isin(lista_EC)].copy()
    if df_filtrado.empty:
        print("Advertencia: No se encontraron tags que coincidan con los equipos colaborativos (EC).")
        return pd.DataFrame()
    
    # 4. Explotamos la lista de vehículos
    df_explotado = df_filtrado.explode("vehicles").dropna(subset=['vehicles'])
    
    if df_explotado.empty:
        print("Advertencia: Los tags de EC filtrados no tienen vehículos asociados.")
        return pd.DataFrame()

    # 5. Concatenación y Normalización
    res = pd.concat([
        df_explotado[["tagName", "parentTag.name"]].reset_index(drop=True),
        pd.json_normalize(df_explotado["vehicles"]).reset_index(drop=True)
    ], axis=1)
    
    # 6. Renombrado final
    res = res.rename(columns={
        'tagName': 'Proyecto', 
        'parentTag.name': 'EC',
        'name': 'name' 
    })
    
    print(f"Éxito: Se transformaron {len(res)} registros de vehículos con Proyecto y EC.")
    return res

def obtener_datos_proyectos_ec(headers: Dict[str, str], url: str) -> pd.DataFrame:
    """
    Orquesta la extracción y transformación de tags para obtener un DataFrame 
    con columnas ['Proyecto', 'EC', 'id', 'name'].
    """
    print("Iniciando la obtención de datos de Proyectos y EC desde Samsara...")
    try:
        # Petición a la API para obtener tags
        response: requests.Response = requests.get(url, headers=headers)
        response.raise_for_status()

        data = response.json().get('data', [])

        if not data:
            print("Advertencia: La API de Samsara no devolvió tags.")
            return pd.DataFrame()

        df_tags: pd.DataFrame = pd.json_normalize(data, sep='.')

        # Renombrar columnas para que coincidan con lo esperado por transformar_tags
        columnas_interes: Dict[str, str] = {
            'id': 'tagId',
            'name': 'tagName',
            'parentTagId': 'parentTagId'
        }
        df_tags = df_tags.rename(columns=columnas_interes)
        
        # Asegurar que los IDs sean tratados como strings para evitar problemas de formato
        for col in ['tagId', 'parentTagId']:
            if col in df_tags.columns:
                df_tags[col] = df_tags[col].astype(str).replace('nan', None)

        # Transformar los tags para obtener la información de proyectos
        df_proyectos = transformar_tags(df_tags)

        # Seleccionar y asegurar las columnas finales
        if not df_proyectos.empty and all(col in df_proyectos.columns for col in ['Proyecto', 'EC', 'id', 'name']):
            return df_proyectos[['Proyecto', 'EC', 'id', 'name']]
        else:
            print("Advertencia: El DataFrame procesado no contiene las columnas esperadas ('Proyecto', 'EC', 'id', 'name').")
            return pd.DataFrame()

    except requests.exceptions.RequestException as e:
        print(f"ERROR al obtener o procesar los tags de Samsara: {e}")
        return pd.DataFrame()
    except Exception as e:
        print(f"ERROR inesperado durante la obtención de datos de proyectos: {e}")
        return pd.DataFrame()

