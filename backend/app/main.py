from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .classifier import CLASSIFIER_MODES, classify_transactions
from .exporter import export_transactions
from .extractor import extract_statement
from .parser import extract_account_details, parse_statement_text, parse_table_rows

app = FastAPI(title="Bank Statement Processing & Classification API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("FRONTEND_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(15 * 1024 * 1024)))
TEMP_ROOT = Path(tempfile.gettempdir()) / "bank_statement_analyzer"
TEMP_ROOT.mkdir(parents=True, exist_ok=True)
# In-memory registry: processing records are temporary and disappear when the API restarts.
STATEMENTS: dict[str, dict[str, Any]] = {}


class StatusResponse(BaseModel):
    id: str
    status: str
    message: str


def _clean_account_details(details: dict) -> dict:
    # Keep only assessment-required fields; don't log or expose additional extracted text.
    return {
        "account_holder_name": details.get("account_holder_name"),
        "account_number": details.get("account_number"),
        "ifsc_code": details.get("ifsc_code"),
    }


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/statements/upload", response_model=StatusResponse)
async def upload_statement(file: UploadFile = File(...), classifier_mode: str = "hybrid"):
    if classifier_mode not in CLASSIFIER_MODES:
        raise HTTPException(status_code=400, detail=f"classifier_mode must be one of {CLASSIFIER_MODES}")
    filename = Path(file.filename or "").name
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="Only PDF files are supported.")
    if file.content_type not in ("application/pdf", "application/octet-stream", None):
        raise HTTPException(status_code=415, detail="Uploaded file must be a PDF.")
    statement_id = uuid.uuid4().hex
    work_dir = TEMP_ROOT / statement_id
    work_dir.mkdir(parents=True, exist_ok=False)
    pdf_path = work_dir / "statement.pdf"
    size = 0
    try:
        with pdf_path.open("wb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    raise HTTPException(status_code=413, detail=f"PDF exceeds {MAX_FILE_SIZE // (1024 * 1024)} MB limit.")
                target.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="The uploaded PDF is empty.")
        if pdf_path.read_bytes()[:5] != b"%PDF-":
            raise HTTPException(status_code=400, detail="The file does not have a valid PDF signature.")

        STATEMENTS[statement_id] = {
            "status": "processing", "message": "Extracting and classifying transactions.",
            "work_dir": str(work_dir), "filename": filename,
        }
        extracted = extract_statement(str(pdf_path))
        account_details = _clean_account_details(extract_account_details(extracted["text"]))
        parsed_df = None
        if extracted["pdf_type"] == "text" and extracted.get("tables"):
            table_df = parse_table_rows(extracted["tables"])
            if not table_df.empty:
                parsed_df = table_df
        if parsed_df is None:
            parsed_df = parse_statement_text(extracted["text"])
        if parsed_df is None:
            parsed_df = pd.DataFrame(columns=["date", "description", "debit_amount", "credit_amount", "balance"])
        parsed_df = parsed_df.fillna("")
        classified_df = classify_transactions(parsed_df, mode=classifier_mode)
        # Exporter expects its existing output schema; keep processing in the original module.
        export_paths = export_transactions(
            classified_df,
            str(work_dir / "statement.xlsx"),
            str(work_dir / "statement.csv"),
            account_details=account_details,
        )
        records = classified_df.where(pd.notna(classified_df), None).to_dict(orient="records")
        STATEMENTS[statement_id].update({
            "status": "completed",
            "message": "Processing completed." if records else "Processing completed; no transactions were detected.",
            "pdf_type": extracted["pdf_type"],
            "account_details": account_details,
            "transactions": records,
            "exports": export_paths,
            "classifier_mode": classifier_mode,
        })
        return StatusResponse(id=statement_id, status="completed", message=STATEMENTS[statement_id]["message"])
    except HTTPException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    except Exception as exc:
        STATEMENTS[statement_id] = {
            "status": "failed",
            "message": str(exc) if isinstance(exc, RuntimeError) else "Could not process this PDF. Check that it is valid and readable.",
            "work_dir": str(work_dir),
        }
        raise HTTPException(status_code=422, detail=STATEMENTS[statement_id]["message"]) from exc
    finally:
        await file.close()


@app.get("/api/statements/{statement_id}/status")
def get_status(statement_id: str):
    record = STATEMENTS.get(statement_id)
    if not record:
        raise HTTPException(status_code=404, detail="Statement not found or expired.")
    return {"id": statement_id, "status": record["status"], "message": record["message"]}


@app.get("/api/statements/{statement_id}/results")
def get_results(statement_id: str):
    record = STATEMENTS.get(statement_id)
    if not record:
        raise HTTPException(status_code=404, detail="Statement not found or expired.")
    if record["status"] != "completed":
        raise HTTPException(status_code=409, detail=record["message"])
    return {
        "id": statement_id,
        "filename": record["filename"],
        "pdf_type": record["pdf_type"],
        "classifier_mode": record["classifier_mode"],
        "account_details": record["account_details"],
        "transactions": record["transactions"],
    }


@app.get("/api/statements/{statement_id}/export/csv")
def export_csv(statement_id: str):
    record = STATEMENTS.get(statement_id)
    if not record or record.get("status") != "completed":
        raise HTTPException(status_code=404, detail="Processed statement not found.")
    path = record["exports"]["csv"]
    return FileResponse(path, media_type="text/csv", filename="classified_transactions.csv")


@app.get("/api/statements/{statement_id}/export/excel")
def export_excel(statement_id: str):
    record = STATEMENTS.get(statement_id)
    if not record or record.get("status") != "completed":
        raise HTTPException(status_code=404, detail="Processed statement not found.")
    path = record["exports"]["excel"]
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="classified_statement.xlsx",
    )
