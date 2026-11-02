# Bank Statement Processing & Classification System

A web-based prototype that extracts transaction details from bank statement PDFs, classifies transactions using rule-based and traditional machine learning methods, and exports the results to Excel or CSV.

The project uses a **Next.js frontend** and a **FastAPI backend** with Python-based PDF processing.

## Features

- Upload bank statements in PDF format.
- Detect whether a PDF is text-based or scanned.
- Extract text and tables from text-based PDFs.
- Use OCR to read scanned PDF pages.
- Extract account details and transaction information.
- Classify transactions without using an LLM.
- View processed transactions in a table.
- Export classified transactions to Excel or CSV.

## Technology Stack

| Component | Technology |
|---|---|
| Frontend | Next.js, React, TypeScript, Tailwind CSS |
| Backend | Python, FastAPI |
| PDF text and table extraction | pdfplumber |
| OCR | Tesseract OCR, Poppler, pdf2image |
| Data processing | Pandas |
| Classification | Rule-based keywords, TF-IDF, LinearSVC |
| Export | Excel, CSV |

## How It Works

1. The user uploads a bank statement PDF.
2. The backend checks whether the PDF contains extractable text.
3. For text-based PDFs, `pdfplumber` extracts text and tables.
4. For scanned PDFs, Poppler converts pages into images and Tesseract OCR extracts the text.
5. The parser identifies account details and transaction fields such as date, description, debit, credit, and balance.
6. The classification engine assigns a category based on the transaction description.
7. The frontend displays the extracted and classified transactions.
8. The user can download the results as Excel or CSV.

### Classification Approach

The system supports three classification modes:

- **Rule-based:** Uses keyword rules to assign transaction categories.
- **Machine learning:** Uses TF-IDF to convert transaction descriptions into features and LinearSVC to predict categories.
- **Hybrid:** Applies matching keyword rules first and uses the machine learning model as a fallback.

The system does **not** use an LLM for transaction classification.

## Project Structure

```text
Bank_Statement_Analyzer/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   └── main.py
│   ├── classifier.py
│   ├── extractor.py
│   ├── parser.py
│   ├── exporter.py
│   ├── sample_data/
│   │   └── training_data.csv
│   └── requirements.txt
│
├── frontend/
│   ├── app/
│   ├── components/
│   ├── services/
│   ├── package.json
│   └── ...
│
└── README.md
```

## Requirements

- Python 3.10 or later
- Node.js 18 or later
- Tesseract OCR
- Poppler

Tesseract and Poppler are required for processing scanned PDF statements.

## Setup and Run

### 1. Backend Setup

Open PowerShell in the project directory:

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

If PowerShell blocks virtual environment activation, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### 2. Install OCR Dependencies

Install Tesseract OCR:

```powershell
winget install UB-Mannheim.TesseractOCR
```

Install Poppler separately and add its `Library\bin` directory to the Windows PATH.

Verify the installations:

```powershell
tesseract --version
pdftoppm -v
```

If Tesseract is installed but not available on PATH, configure its executable path in `extractor.py`.

### 3. Frontend Setup

Open a **new terminal** in the project directory:

```powershell
cd frontend
npm install
Copy-Item .env.local.example .env.local
npm run dev
```

Open the application:

[http://localhost:3000](http://localhost:3000)

The backend API documentation is available at:

[http://localhost:8000/docs](http://localhost:8000/docs)

## API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | Check backend health |
| POST | `/api/statements/upload` | Upload and process a statement |
| GET | `/api/statements/{id}/status` | Check processing status |
| GET | `/api/statements/{id}/results` | Retrieve extracted transactions |
| GET | `/api/statements/{id}/export/csv` | Download CSV |
| GET | `/api/statements/{id}/export/excel` | Download Excel |

## Testing

1. Start the backend and frontend.
2. Open the frontend in your browser.
3. Upload a text-based bank statement PDF.
4. Review the extracted account details and transactions.
5. Download the results as Excel or CSV.
6. Test a scanned PDF after confirming Tesseract and Poppler are installed.

## Limitations

- Bank statement layouts differ, so extraction accuracy may vary by bank and PDF format.
- OCR output can contain recognition errors, especially for low-quality scans.
- Multi-line transaction descriptions may not always be reconstructed correctly.
- Extracted transaction data should be checked against the original statement before being used for financial decisions.
- The current prototype stores processing records temporarily in memory. Restarting the backend clears the registry and temporary results.

## Assessment Alignment

This prototype demonstrates PDF input handling, text and scanned PDF processing, account and transaction extraction, non-LLM transaction classification, and Excel/CSV export.
