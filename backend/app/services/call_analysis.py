from __future__ import annotations

import json
import threading
from typing import Any
from uuid import UUID

import httpx
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.call import Call


class CallAnalysis(BaseModel):
    customer_intent: str = "Unknown"
    key_points: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    follow_up_required: bool = False
    follow_up_notes: str = ""
    outcome: str = ""


class CallAnalysisService:
    @staticmethod
    def schedule(call_id: UUID) -> None:
        threading.Thread(target=CallAnalysisService._run_background, args=(call_id,), daemon=True).start()

    @staticmethod
    def _run_background(call_id: UUID) -> None:
        with SessionLocal() as db:
            try:
                CallAnalysisService.analyze(db, call_id)
            except Exception:
                call = db.get(Call, call_id)
                if call:
                    call.analysis_status = "failed"
                    db.commit()

    @staticmethod
    def analyze(db: Session, call_id: UUID) -> Call | None:
        call = db.get(Call, call_id)
        if call is None or not call.transcript:
            return call
        if call.analysis_status == "completed":
            return call
        call.analysis_status = "running"
        db.commit()
        settings = get_settings()
        if not settings.effective_llm_api_key or not settings.effective_llm_model:
            call.analysis_status = "failed"
            db.commit()
            return call
        prompt = (
            "Return JSON only with keys customer_intent, key_points, action_items, "
            "follow_up_required, follow_up_notes, outcome. Analyze this call transcript.\n\n"
            + call.transcript[:30000]
        )
        base = (settings.effective_llm_base_url or "https://api.openai.com/v1").rstrip("/")
        response = httpx.post(f"{base}/chat/completions", headers={
            "Authorization": f"Bearer {settings.effective_llm_api_key}",
            "Content-Type": "application/json",
        }, json={"model": settings.effective_llm_model, "temperature": 0.1,
                 "messages": [{"role": "system", "content": "You analyze business call outcomes safely and concisely."},
                              {"role": "user", "content": prompt}],
                 "response_format": {"type": "json_object"}}, timeout=settings.llm_timeout_seconds)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        result = CallAnalysis.model_validate(json.loads(content))
        call.analysis_json = result.model_dump()
        call.customer_intent = result.customer_intent
        call.key_points = result.key_points
        call.action_items = result.action_items
        call.follow_up_required = result.follow_up_required
        call.follow_up_notes = result.follow_up_notes
        call.outcome = result.outcome or call.outcome
        call.analysis_status = "completed"
        db.commit()
        db.refresh(call)
        return call
