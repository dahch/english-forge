"""Proxy del catálogo de voces de TTS.

La UI no habla con personal-api directamente (no lo conoce ni tiene su URL), así
que este router reenvía la consulta manteniendo el mismo contrato que
`GET /v1/voices` de personal-api.

Se mantiene separado de `/api/settings` porque es una lectura contra un servicio
externo que puede tardar o fallar de formas que no deben romper la página de
ajustes: si falla, el selector de voz muestra un error y el resto sigue
funcionando.
"""

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voices", tags=["voices"])

_VOICES_TIMEOUT_SECONDS = 20


@router.get("")
async def list_voices(
    provider: str = Query("pockettts"),
):
    """Devuelve `{provider, voices, has_more}` o un error explicable.

    No requiere autenticación de usuario para el motor local (son voces
    públicas del servidor), pero se deja sin `get_current_user` a propósito:
    el catálogo no es dato de usuario y así la pantalla de ajustes puede pintarlo
    aunque la sesión esté a medio renovar.
    """
    settings = get_settings()
    base_url = settings.PERSONAL_API_URL.rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=_VOICES_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{base_url}/v1/voices", params={"provider": provider})
    except httpx.RequestError as e:
        logger.warning(f"Voice catalog request failed: {e}")
        raise HTTPException(
            status_code=503, detail="No se pudo contactar con personal-api para listar voces."
        )

    if resp.status_code >= 400:
        # Se propaga el motivo real (p. ej. falta CARTESIA_API_KEY) en vez de un
        # 500 genérico: el usuario necesita saber qué configurar.
        detail = resp.text
        try:
            payload = resp.json()
            detail = payload.get("detail") or detail
        except Exception:
            pass
        raise HTTPException(status_code=resp.status_code, detail=detail)

    return resp.json()
