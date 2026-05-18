"""Contacts API routes."""

import csv
import io
from datetime import datetime
from typing import List, Optional

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.db import Activity, Contact, ContactStatus
from backend.services.pipeline import (
    get_progress,
    run_email_draft_for_contact,
    run_full_pipeline_for_contact,
    run_send_email_for_contact,
    run_enrichment_for_contact,
)

router = APIRouter(prefix="/contacts", tags=["contacts"])


class ContactOut(BaseModel):
    id: int
    name: str
    email: str
    linkedin_url: Optional[str]
    company: Optional[str]
    job_title: Optional[str]
    status: str
    email_subject: Optional[str]
    email_body: Optional[str]
    followup_count: int
    next_followup_at: Optional[datetime]
    email_sent_at: Optional[datetime]
    error_message: Optional[str]
    created_at: datetime
    campaign_id: Optional[int]
    linkedin_headline: Optional[str] = None
    linkedin_summary: Optional[str] = None
    linkedin_experience: Optional[str] = None
    linkedin_skills: Optional[str] = None

    class Config:
        from_attributes = True


class ContactUpdate(BaseModel):
    email_subject: Optional[str] = None
    email_body: Optional[str] = None
    status: Optional[str] = None
    name: Optional[str] = None
    company: Optional[str] = None
    job_title: Optional[str] = None
    linkedin_url: Optional[str] = None
    campaign_id: Optional[int] = None
    # Manual/automatic enrichment fields. These are editable in the UI and fed to AI drafting.
    linkedin_headline: Optional[str] = None
    linkedin_summary: Optional[str] = None
    linkedin_experience: Optional[str] = None
    linkedin_skills: Optional[str] = None


class ContactCreate(BaseModel):
    name: str
    email: str
    linkedin_url: Optional[str] = None
    company: Optional[str] = None
    job_title: Optional[str] = None
    campaign_id: Optional[int] = None
    # Optional manual About/context at creation time
    linkedin_summary: Optional[str] = None
    linkedin_headline: Optional[str] = None
    linkedin_experience: Optional[str] = None
    linkedin_skills: Optional[str] = None


class BulkContactAction(BaseModel):
    ids: List[int]
    campaign_id: Optional[int] = None


class ActivityOut(BaseModel):
    id: int
    activity_type: str
    detail: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True




@router.post("/", response_model=ContactOut)
def create_contact(payload: ContactCreate, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Valid email is required")
    existing = db.query(Contact).filter(Contact.email == email).first()
    if existing:
        raise HTTPException(400, "A contact with this email already exists")
    contact = Contact(
        name=payload.name.strip() or email.split("@")[0],
        email=email,
        linkedin_url=payload.linkedin_url,
        company=payload.company,
        job_title=payload.job_title,
        campaign_id=payload.campaign_id,
        linkedin_summary=payload.linkedin_summary,
        linkedin_headline=payload.linkedin_headline,
        linkedin_experience=payload.linkedin_experience,
        linkedin_skills=payload.linkedin_skills,
        enriched_at=datetime.utcnow() if (payload.linkedin_summary or payload.linkedin_headline or payload.linkedin_experience or payload.linkedin_skills) else None,
        status=ContactStatus.enriched if (payload.linkedin_summary or payload.linkedin_headline or payload.linkedin_experience or payload.linkedin_skills) else ContactStatus.pending,
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


@router.post("/upload")
async def upload_contacts(
    file: UploadFile = File(...),
    campaign_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    """Upload a CSV or Excel file of contacts."""
    content = await file.read()

    try:
        if file.filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content))
        elif file.filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(content))
        else:
            raise HTTPException(400, "Only CSV or Excel files are supported")
    except Exception as e:
        raise HTTPException(400, f"Could not parse file: {e}")

    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]

    if "email" not in df.columns:
        raise HTTPException(400, f"Missing required column: email. Found: {list(df.columns)}")

    created = 0
    skipped = 0

    for _, row in df.iterrows():
        email = str(row.get("email", "")).strip().lower()
        if not email or "@" not in email:
            skipped += 1
            continue

        existing = db.query(Contact).filter(Contact.email == email).first()
        if existing:
            # Allow upload to add existing contact to selected campaign
            if campaign_id and existing.campaign_id != campaign_id:
                existing.campaign_id = campaign_id
                existing.updated_at = datetime.utcnow()
            skipped += 1
            continue

        contact = Contact(
            email=email,
            name=str(row.get("name", "")).strip() or email.split("@")[0],
            linkedin_url=str(row.get("linkedin_url", row.get("linkedin", ""))).strip() or None,
            company=str(row.get("company", "")).strip() or None,
            job_title=str(row.get("job_title", row.get("title", row.get("designation", "")))).strip() or None,
            campaign_id=campaign_id,
            status=ContactStatus.pending,
        )
        db.add(contact)
        created += 1

    db.commit()
    return {"created": created, "skipped": skipped}


@router.get("/", response_model=List[ContactOut])
def list_contacts(
    status: Optional[str] = None,
    campaign_id: Optional[int] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    q = db.query(Contact)
    if status:
        q = q.filter(Contact.status == status)
    if campaign_id:
        q = q.filter(Contact.campaign_id == campaign_id)
    if search:
        q = q.filter(
            (Contact.name.ilike(f"%{search}%"))
            | (Contact.email.ilike(f"%{search}%"))
            | (Contact.company.ilike(f"%{search}%"))
        )
    return q.order_by(Contact.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/stats")
def contact_stats(db: Session = Depends(get_db)):
    total = db.query(Contact).count()
    stats = {}
    for status in ContactStatus:
        stats[status.value] = db.query(Contact).filter(Contact.status == status).count()
    return {"total": total, **stats}


@router.get("/export.csv")
def export_contacts_csv(status: Optional[str] = None, campaign_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Download contacts with enrichment and generated drafts as CSV."""
    q = db.query(Contact)
    if status:
        q = q.filter(Contact.status == ContactStatus(status))
    if campaign_id:
        q = q.filter(Contact.campaign_id == campaign_id)
    contacts = q.order_by(Contact.created_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "name", "email", "company", "job_title", "linkedin_url", "campaign_id", "status",
        "linkedin_headline", "linkedin_summary", "linkedin_experience", "linkedin_skills",
        "email_subject", "email_body", "followup_count", "email_sent_at", "next_followup_at", "error_message"
    ])
    for c in contacts:
        writer.writerow([
            c.id, c.name, c.email, c.company or "", c.job_title or "", c.linkedin_url or "", c.campaign_id or "", c.status.value if hasattr(c.status, "value") else c.status,
            c.linkedin_headline or "", c.linkedin_summary or "", c.linkedin_experience or "", c.linkedin_skills or "",
            c.email_subject or "", c.email_body or "", c.followup_count or 0, c.email_sent_at or "", c.next_followup_at or "", c.error_message or ""
        ])
    output.seek(0)
    filename = f"salesflow_contacts_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/{contact_id}", response_model=ContactOut)
def get_contact(contact_id: int, db: Session = Depends(get_db)):
    c = db.query(Contact).filter(Contact.id == contact_id).first()
    if not c:
        raise HTTPException(404, "Contact not found")
    return c


@router.get("/{contact_id}/activities", response_model=List[ActivityOut])
def get_activities(contact_id: int, db: Session = Depends(get_db)):
    return (
        db.query(Activity)
        .filter(Activity.contact_id == contact_id)
        .order_by(Activity.created_at.desc())
        .all()
    )


@router.get("/{contact_id}/progress")
def contact_progress(contact_id: int):
    return get_progress(contact_id)


@router.patch("/{contact_id}", response_model=ContactOut)
def update_contact(contact_id: int, payload: ContactUpdate, db: Session = Depends(get_db)):
    c = db.query(Contact).filter(Contact.id == contact_id).first()
    if not c:
        raise HTTPException(404, "Contact not found")

    data = payload.dict(exclude_unset=True)
    for field, value in data.items():
        if field == "status" and value:
            setattr(c, field, ContactStatus(value))
        else:
            setattr(c, field, value)

    c.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(c)
    return c


@router.post("/{contact_id}/enrich")
async def enrich_contact(contact_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    c = db.query(Contact).filter(Contact.id == contact_id).first()
    if not c:
        raise HTTPException(404, "Contact not found")

    # Enrich + auto-generate first draft in the background.
    background_tasks.add_task(run_enrichment_for_contact, contact_id, True)
    return {"message": "Enrichment started", "contact_id": contact_id, "progress_url": f"/api/contacts/{contact_id}/progress"}


@router.post("/{contact_id}/draft")
async def draft_email(contact_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    c = db.query(Contact).filter(Contact.id == contact_id).first()
    if not c:
        raise HTTPException(404, "Contact not found")

    background_tasks.add_task(run_email_draft_for_contact, contact_id)
    return {"message": "Draft generation started", "contact_id": contact_id, "progress_url": f"/api/contacts/{contact_id}/progress"}


@router.post("/{contact_id}/approve-and-send")
async def approve_and_send(contact_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    c = db.query(Contact).filter(Contact.id == contact_id).first()
    if not c:
        raise HTTPException(404, "Contact not found")
    if not c.email_subject or not c.email_body:
        raise HTTPException(400, "No email draft — run Draft first")

    c.status = ContactStatus.approved
    c.updated_at = datetime.utcnow()
    db.commit()
    background_tasks.add_task(run_send_email_for_contact, contact_id)
    return {"message": "Email queued for sending"}


@router.post("/bulk/pipeline")
async def bulk_pipeline(
    background_tasks: BackgroundTasks,
    campaign_id: Optional[int] = None,
    auto_send: bool = False,
    db: Session = Depends(get_db),
):
    q = db.query(Contact).filter(Contact.status.in_([ContactStatus.pending, ContactStatus.enriched, ContactStatus.error]))
    if campaign_id:
        q = q.filter(Contact.campaign_id == campaign_id)
    contacts = q.all()

    for contact in contacts:
        background_tasks.add_task(run_full_pipeline_for_contact, contact.id, auto_send)

    return {"message": f"Pipeline started for {len(contacts)} contacts"}


@router.post("/{contact_id}/mark-replied")
def mark_replied(contact_id: int, db: Session = Depends(get_db)):
    c = db.query(Contact).filter(Contact.id == contact_id).first()
    if not c:
        raise HTTPException(404, "Contact not found")
    c.status = ContactStatus.replied
    c.next_followup_at = None
    c.updated_at = datetime.utcnow()
    db.commit()
    return {"message": "Marked as replied"}




@router.post("/bulk/campaign")
def bulk_campaign(payload: BulkContactAction, db: Session = Depends(get_db)):
    if not payload.ids:
        raise HTTPException(400, "Select at least one contact")
    updated = db.query(Contact).filter(Contact.id.in_(payload.ids)).update(
        {Contact.campaign_id: payload.campaign_id, Contact.updated_at: datetime.utcnow()},
        synchronize_session=False,
    )
    db.commit()
    return {"message": f"Updated campaign for {updated} contacts", "updated": updated}


@router.post("/bulk/delete")
def bulk_delete(payload: BulkContactAction, db: Session = Depends(get_db)):
    if not payload.ids:
        raise HTTPException(400, "Select at least one contact")
    deleted = db.query(Contact).filter(Contact.id.in_(payload.ids)).delete(synchronize_session=False)
    db.commit()
    return {"message": f"Deleted {deleted} contacts", "deleted": deleted}


@router.post("/{contact_id}/reset-drafting")
def reset_stuck_drafting(contact_id: int, db: Session = Depends(get_db)):
    c = db.query(Contact).filter(Contact.id == contact_id).first()
    if not c:
        raise HTTPException(404, "Contact not found")
    if c.status == ContactStatus.drafting:
        c.status = ContactStatus.enriched if (c.linkedin_headline or c.linkedin_summary or c.linkedin_experience) else ContactStatus.pending
    c.error_message = None
    c.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(c)
    return {"message": "Contact reset. You can regenerate the draft now.", "status": c.status.value}


@router.delete("/{contact_id}")
def delete_contact(contact_id: int, db: Session = Depends(get_db)):
    c = db.query(Contact).filter(Contact.id == contact_id).first()
    if not c:
        raise HTTPException(404, "Contact not found")
    db.delete(c)
    db.commit()
    return {"message": "Deleted"}


@router.post("/{contact_id}/campaign/{campaign_id}")
def add_contact_to_campaign(contact_id: int, campaign_id: int, db: Session = Depends(get_db)):
    c = db.query(Contact).filter(Contact.id == contact_id).first()
    if not c:
        raise HTTPException(404, "Contact not found")
    c.campaign_id = campaign_id
    c.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(c)
    return {"message": "Contact added to campaign", "campaign_id": campaign_id}


@router.delete("/{contact_id}/campaign")
def remove_contact_from_campaign(contact_id: int, db: Session = Depends(get_db)):
    c = db.query(Contact).filter(Contact.id == contact_id).first()
    if not c:
        raise HTTPException(404, "Contact not found")
    c.campaign_id = None
    c.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(c)
    return {"message": "Contact removed from campaign"}
