# Agendar citas integrando a Claude
# ------------------------------------
# Para agregar el MCP Server a Claude Desktop
# desde git bash o terminal ejecutamos este comando:
# uv run mcp install agendar.py y reiniciar Claude App

import os
import json, os, tempfile
from mcp.server.fastmcp import FastMCP
from typing import Optional, Dict, Any, List
from datetime import datetime

CONSULTORIOS_FILE = os.path.join(os.path.dirname(__file__), "data\consultorios.json")
ESPECIALIDADES_FILE = os.path.join(os.path.dirname(__file__), "data\especialidades.json")

# ------------------- metodos de ayuda
def _time_ok(hora: str) -> bool:
    """Valida formato HH:MM (24h)."""
    try:
        datetime.strptime(hora, "%H:%M")
        return True
    except Exception:
        return False

def _in_range(hora: str, inicio: str, fin: str) -> bool:
    """Retorna True si hora ∈ [inicio, fin]. Todos en formato HH:MM."""
    t  = datetime.strptime(hora, "%H:%M").time()
    ti = datetime.strptime(inicio, "%H:%M").time()
    tf = datetime.strptime(fin, "%H:%M").time()
    return (t >= ti) and (t <= tf)

def _atomic_write(path: str, data: Dict[str, Any]) -> None:
    """Escritura atómica segura de JSON."""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix="tmp_", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise

# ------------------- fin metodos de ayuda


mcp = FastMCP("MCP Agendar Citas")


# Instrucciones son mejor interpretadas en ingles especialmente para modelos de Anthropic

@mcp.tool()
def get_especialidades() -> Dict[str, Any]:
    """
    Lee un archivo JSON con la lista de especialidades médicas y las devuelve.
    Formato esperado:
    {
      "especialidades": [
        {"id": "ESP-001", "nombre": "Odontología General", "duracion_min": 30},
        ...
      ]
    }

    Return:
        str: all the names of specialties as a single string separated by line breaks.
        if no specialties exists, a default message is returned.
    """
    json_path = ESPECIALIDADES_FILE  # Ruta de especialidades
    if not os.path.exists(json_path):
        return {"ok": False, "error": f"Archivo no encontrado: {json_path}"}

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        especialidades = data.get("especialidades", [])
        return {
            "ok": True,
            "count": len(especialidades),
            "especialidades": especialidades
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def get_consultorios_disponibles(
    especialidad_nombre: Optional[str] = None
) -> Dict[str, Any]:
    """
    Devuelve una lista legible de consultorios disponibles (sin paciente asignado).
    Si se indica una especialidad, filtra solo los que correspondan.
    Ideal para usar en chat (salida en texto plano).
    """
    json_path = CONSULTORIOS_FILE
    if not os.path.exists(json_path):
        return {"ok": False, "error": f"❌ No se encontró el archivo: {json_path}"}

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        consultorios: List[Dict[str, Any]] = data.get("consultorios", [])
    except Exception as e:
        return {"ok": False, "error": f"⚠️ No se pudo leer el archivo: {str(e)}"}

    disponibles = []
    for c in consultorios:
        # Solo los consultorios sin paciente asignado
        if c.get("paciente"):
            continue

        # Filtrado por especialidad (si aplica)
        esp = c.get("especialidad", {})
        match = True
        if especialidad_nombre:
            match = str(esp.get("nombre", "")).strip().lower() == especialidad_nombre.strip().lower()

        if match:
            disponibles.append(c)

    if not disponibles:
        filtro = especialidad_nombre or "todas las especialidades"
        return {
            "ok": True,
            "message": f"🔎 No hay consultorios disponibles para {filtro} en este momento."
        }
    

    # Crear salida legible con saltos de línea
    lines = ["🦷 Consultorios disponibles:\n"]
    for c in disponibles:
        esp = c.get("especialidad", {})
        lines.append(
            f"• {c.get('nombre')}\n"
            f"  Especialidad: {esp.get('nombre')}\n"
            f"  Ubicación: {c.get('ubicacion')}\n"
            f"  Horario: {c['horario']['inicio']} - {c['horario']['fin']}\n"
        )

    text_output = "\n".join(lines)

    return {
        "ok": True,
        "message": text_output.strip()
    }


# Tool: reservar_consultorio
# Valida:
# Que el consultorio exista (por id o nombre)
# Que el horario elegido (HH:MM) esté dentro del rango horario.inicio–horario.fin
# Que el consultorio esté libre (paciente == "")
# Actualiza el JSON atómicamente (para evitar corrupciones).
# Devuelve texto plano listo para chat.
@mcp.tool()
def reservar_consultorio(
    paciente_nombre: str,
    hora: str,
    consultorio_nombre: Optional[str] = None
) -> Dict[str, Any]:
    """
    Separa una cita en el consultorio y horario elegido por el usuario.
    - Marca el campo 'paciente' con el nombre del paciente si el consultorio está libre.
    - Verifica que 'hora' esté dentro del rango de atención del consultorio.
    - Si está ocupado o fuera de rango, devuelve mensaje informativo.

    Parámetros:
      - json_path: ruta al JSON de consultorios (del ejemplo anterior).
      - paciente_nombre: nombre del paciente a registrar.
      - hora: 'HH:MM' (24h).
      - consultorio_id: opcional, ej. 'CONS-002'.
      - consultorio_nombre: opcional, ej. 'Consultorio Ortodoncia'.

    Retorna:
      {
        "ok": true/false,
        "message": "<texto para chat>"
      }
    """
    # Validaciones básicas
    json_path = CONSULTORIOS_FILE
    if not os.path.exists(json_path):
        return {"ok": False, "message": f"❌ No se encontró el archivo: {json_path}"}
    if not paciente_nombre or not paciente_nombre.strip():
        return {"ok": False, "message": "❌ Debes indicar el nombre del paciente."}
    if not hora or not _time_ok(hora):
        return {"ok": False, "message": "❌ Hora inválida. Usa el formato HH:MM (24h)."}

    # Cargar JSON
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        consultorios: List[Dict[str, Any]] = data.get("consultorios", [])
    except Exception as e:
        return {"ok": False, "message": f"⚠️ No se pudo leer el archivo: {str(e)}"}

    # Buscar consultorio
    sel = None
    for c in consultorios:
        if consultorio_nombre and str(c.get("nombre", "")).strip().lower() == consultorio_nombre.strip().lower():
            sel = c; break

    if not sel:
        crit = consultorio_nombre or "(sin criterio)"
        return {"ok": False, "message": f"🔎 No encontré un consultorio que coincida con {crit}."}

    nombre = sel.get("nombre", "Consultorio")
    horario = sel.get("horario", {})
    h_ini = horario.get("inicio")
    h_fin = horario.get("fin")

    if not (h_ini and h_fin and _time_ok(h_ini) and _time_ok(h_fin)):
        return {"ok": False, "message": f"⚠️ El consultorio «{nombre}» no tiene horario válido configurado."}

    # Validar que la hora solicitada esté dentro del horario del consultorio
    if not _in_range(hora, h_ini, h_fin):
        return {
            "ok": False,
            "message": (
                f"⏰ «{nombre}» atiende de {h_ini} a {h_fin}. "
                f"La hora solicitada ({hora}) está fuera de rango. "
                f"Por favor, elige una hora dentro del horario de atención."
            )
        }

    # Verificar disponibilidad (modelo simple: ocupado si 'paciente' tiene valor)
    if str(sel.get("paciente", "")).strip():
        actual = sel.get("paciente")
        return {
            "ok": False,
            "message": (
                f"🚫 El consultorio «{nombre}» ya está ocupado por: {actual}.\n"
                f"Elige otro consultorio u otro horario."
            )
        }

    # Marcar reserva
    sel["paciente"] = paciente_nombre.strip()

    # Guardar JSON atómicamente
    try:
        data["consultorios"] = consultorios
        _atomic_write(json_path, data)
    except Exception as e:
        return {"ok": False, "message": f"⚠️ No se pudo actualizar el archivo: {str(e)}"}

    # Mensaje de confirmación para chat
    ubic = sel.get("ubicacion", "—")
    esp  = sel.get("especialidad", {})
    esp_nom = esp.get("nombre", "—")

    return {
        "ok": True,
        "message": (
            "✅ *Cita reservada exitosamente*\n"
            f"• Paciente: {paciente_nombre}\n"
            f"• Consultorio: {nombre}\n"
            f"• Especialidad: {esp_nom}\n"
            f"• Ubicación: {ubic}\n"
            f"• Hora: {hora}\n"
            f"• Horario de atención del consultorio: {h_ini}–{h_fin}"
        )
    }