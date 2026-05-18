"""
AI email generation service.
Uses NVIDIA's OpenAI-compatible API.

Drafting goal:
- Use manual About/enrichment first when provided.
- Infer only precise, title-relevant pain points.
- Write a tight outbound email around pain points, not generic service dumping.
"""

import json
import logging
import os
import re
from typing import Any, Dict

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
logger = logging.getLogger(__name__)

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.1-70b-instruct")

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=NVIDIA_API_KEY,
)

INITIAL_SYSTEM = """
You are a senior enterprise B2B outreach strategist for Innominds.
Your job is NOT to write a generic sales email. Your job is to identify the prospect's most likely business/technical pain points from their title, company, and enrichment context, then write a short email around only those pain points.

Strict rules:
1. Do NOT return JSON.
2. Return exactly this format:
SUBJECT: <short human subject, max 7 words>

BODY:
Hi <First Name>,

<paragraph 1: one specific observation from manual/LinkedIn context or title/company. No flattery.>

<paragraph 2: 2-3 precise pain points this person likely owns. Connect to Innominds only through those pain points.>

<paragraph 3: soft 15-minute CTA.>

Best,
<sender name>
3. Do not mention every Innominds service. Mention only the service angle that matches the pain points.
4. No buzzwords unless they are grounded in the prospect's role/context.
5. Do not use phrases like "I hope you're doing well", "game-changer", "cutting-edge", "synergy", "transform your business", "revolutionize".
6. Keep body under 140 words.
7. If enrichment is empty, use job title + company to infer conservative role-based pain points. Do not pretend you saw LinkedIn details.
8. Pain points must be valid, practical, and senior-friendly.
"""

FOLLOWUP_SYSTEM = """
You are writing a brief B2B follow-up email.
Return exactly this format:
SUBJECT: Re: <original subject>

BODY:
Hi <First Name>,

<short follow-up around the same pain point, not generic checking-in>

Best,
<sender name>
"""


def _clean_body(body: str, subject: str = "") -> str:
    body = (body or "").strip()
    if body.startswith("{") and "body" in body:
        try:
            data = json.loads(body)
            subject = data.get("subject", subject)
            body = data.get("body", body)
        except Exception:
            pass
    body = re.sub(r"^BODY\s*:\s*", "", body, flags=re.I).strip()
    body = re.sub(r"^Subject\s*:\s*.*?\n+", "", body, flags=re.I | re.S).strip()
    if subject:
        body = body.replace(subject, "").strip()
    body = body.replace("\\n", "\n")
    lines = [line.strip() for line in body.splitlines()]
    cleaned, blank = [], False
    for line in lines:
        if not line:
            if not blank:
                cleaned.append("")
            blank = True
        else:
            cleaned.append(line)
            blank = False
    return "\n".join(cleaned).strip()


def _parse_model_output(text: str) -> Dict[str, str]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Empty model response")
    raw = re.sub(r"^```(?:json|text)?", "", raw, flags=re.I).strip()
    raw = re.sub(r"```$", "", raw).strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
            subject = str(data.get("subject", "Relevant pain points")).strip()
            body = _clean_body(str(data.get("body", "")).strip(), subject)
            return {"subject": subject, "body": body}
        except Exception:
            pass
    subject = "Relevant pain points"
    body = raw
    m = re.search(r"SUBJECT\s*:\s*(.+)", raw, flags=re.I)
    if m:
        subject = m.group(1).strip().strip('"')
    m = re.search(r"BODY\s*:\s*(.*)", raw, flags=re.I | re.S)
    if m:
        body = m.group(1).strip()
    return {"subject": subject, "body": _clean_body(body, subject)}


def _first_name_from_context(prospect_context: str) -> str:
    m = re.search(r"Name:\s*([^\n]+)", prospect_context or "")
    return m.group(1).strip().split()[0] if m else "there"


def _extract_line(prospect_context: str, label: str) -> str:
    m = re.search(rf"^{re.escape(label)}:\s*(.*)$", prospect_context or "", flags=re.I | re.M)
    return m.group(1).strip() if m else ""


def _role_pain_points(title: str, company: str, context: str) -> str:
    t = (title or "").lower()
    ctx = (context or "").lower()
    if any(x in t+ctx for x in ["pharmacovigilance", "safety", "pv"]):
        return "case intake triage, safety data quality, signal workflow visibility, inspection-ready automation"
    if any(x in t+ctx for x in ["data", "analytics", "bi", "insights", "rwe", "real world"]):
        return "trusted data pipelines, dashboard adoption, self-service analytics, lineage, observability, and AI-ready data foundations"
    if any(x in t+ctx for x in ["manufacturing", "quality", "operations", "supply", "plant", "mes", "scada"]):
        return "deviation reduction, batch visibility, predictive quality, MES/SCADA workflow friction, and shopfloor data integration"
    if any(x in t+ctx for x in ["digital", "product", "platform", "engineering", "technology", "cloud"]):
        return "platform reliability, cloud modernization, GenAI delivery, product engineering velocity, and governed automation"
    if any(x in t+ctx for x in ["clinical", "medical", "regulatory", "r&d", "research"]):
        return "document-heavy workflows, evidence synthesis, compliant GenAI assist, and fragmented clinical/scientific data"
    return "manual workflows, fragmented systems, data quality, AI adoption risk, and delivery velocity"


def _fallback_initial(sender_context: Dict[str, Any], prospect_context: str) -> Dict[str, str]:
    sender_name = sender_context.get("your_name") or os.getenv("EMAIL_FROM_NAME", "Venkat")
    sender_company = sender_context.get("your_company") or "Innominds"
    first = _first_name_from_context(prospect_context)
    title = _extract_line(prospect_context, "Title")
    company = _extract_line(prospect_context, "Company")
    pains = _role_pain_points(title, company, prospect_context)
    role_line = f"your role as {title}" if title else "your current charter"
    body = (
        f"Hi {first},\n\n"
        f"Noticed {role_line}{' at ' + company if company else ''}, and thought the pain points around {pains} may be relevant.\n\n"
        f"At {sender_company}, we typically help teams remove execution friction in exactly those areas — with focused AI, data, cloud, automation, and engineering work rather than broad consulting noise.\n\n"
        "Would a short 15-minute exchange make sense to compare notes?\n\n"
        f"Best,\n{sender_name}"
    )
    return {"subject": "Relevant pain points", "body": body}


async def generate_initial_email(prospect_context: str, sender_context: Dict[str, Any], original_subject: str | None = None) -> Dict[str, str]:
    if not NVIDIA_API_KEY:
        logger.warning("NVIDIA_API_KEY is missing. Returning fallback email.")
        return _fallback_initial(sender_context, prospect_context)
    title = _extract_line(prospect_context, "Title")
    company = _extract_line(prospect_context, "Company")
    inferred_pains = _role_pain_points(title, company, prospect_context)
    user_prompt = f"""
Prospect context. Manual About/enrichment may have been entered by the user and should be treated as the strongest signal:
{prospect_context}

Conservative role-based pain point hints:
{inferred_pains}

Sender context:
Name: {sender_context.get('your_name', '')}
Company: {sender_context.get('your_company', '')}
Role: {sender_context.get('your_role', '')}
Value proposition: {sender_context.get('value_proposition', '')}

Write a precise email that talks about ONLY the most likely pain points. Do not list services. Do not fabricate achievements. If manual/enrichment context exists, ground paragraph 1 in it. If not, clearly base the note on the title/company only.
""".strip()
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": INITIAL_SYSTEM}, {"role": "user", "content": user_prompt}],
            temperature=0.35,
            max_tokens=600,
        )
        result = _parse_model_output(response.choices[0].message.content or "")
        if not result.get("body"):
            raise ValueError("Model returned empty body")
        return result
    except Exception as e:
        logger.exception("NVIDIA email generation failed: %s", e)
        return _fallback_initial(sender_context, prospect_context)


async def generate_followup_email(prospect_context: str, sender_context: Dict[str, Any], original_subject: str, followup_number: int) -> Dict[str, str]:
    if not NVIDIA_API_KEY:
        return {"subject": f"Re: {original_subject or 'Relevant pain points'}", "body": f"Hi,\n\nWanted to resurface the pain-point note below in case this is relevant to your current priorities.\n\nBest,\n{sender_context.get('your_name', '')}"}
    user_prompt = f"""
Prospect context:
{prospect_context}

Sender: {sender_context.get('your_name', '')} at {sender_context.get('your_company', '')}
Original subject: {original_subject}
Follow-up number: {followup_number}

Write a short follow-up around the same specific pain point. Use SUBJECT/BODY format only.
""".strip()
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": FOLLOWUP_SYSTEM}, {"role": "user", "content": user_prompt}],
            temperature=0.3,
            max_tokens=380,
        )
        result = _parse_model_output(response.choices[0].message.content or "")
        if not result.get("subject", "").lower().startswith("re:"):
            result["subject"] = f"Re: {original_subject or result.get('subject','Relevant pain points')}"
        return result
    except Exception as e:
        logger.exception("NVIDIA follow-up generation failed: %s", e)
        return {"subject": f"Re: {original_subject or 'Relevant pain points'}", "body": f"Hi,\n\nWanted to resurface this in case the pain points are relevant.\n\nBest,\n{sender_context.get('your_name', '')}"}
