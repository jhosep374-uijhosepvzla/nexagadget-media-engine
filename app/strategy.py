from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from groq import Groq
import os
import json

router = APIRouter(
    prefix="/strategy",
    tags=["Strategy"]
)

# Cliente Groq
client = Groq(
    api_key=os.getenv("GROQ_API_KEY")
)


class StrategyRequest(BaseModel):
    industry: str
    business_type: str
    objective: str
    avatar: str
    pain: str
    offer: str
    cta: str


@router.post("/generate")
async def generate_strategy(data: StrategyRequest):
    try:
        prompt = f"""
Eres un estratega senior de marketing digital y crecimiento empresarial.

Analiza la siguiente información:

Industria: {data.industry}
Tipo de negocio: {data.business_type}
Objetivo: {data.objective}
Avatar: {data.avatar}
Dolor principal: {data.pain}
Oferta: {data.offer}
CTA: {data.cta}

Devuelve únicamente un JSON válido con la siguiente estructura:
{{
    "hook": "",
    "reel_script": "",
    "instagram_caption": "",
    "facebook_caption": "",
    "tiktok_caption": "",
    "hashtags": ["", "", ""],
    "lead_magnet": "",
    "next_action": ""
}}
No agregues texto fuera del JSON.
"""
        # Llamada a Groq movida a un threadpool: es una llamada bloqueante
        # y esta función es async, así que sin esto se congela el único
        # worker de la instancia Free mientras espera la respuesta.
        completion = await run_in_threadpool(
            client.chat.completions.create,
            model="llama-3.3-70b-versatile",
            temperature=0.8,
            messages=[{"role": "user", "content": prompt}],
        )

        content = completion.choices[0].message.content
        try:
            result = json.loads(content)
        except Exception:
            result = {"raw_response": content}

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
