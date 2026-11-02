"""PDF extraction utilities for text-based and scanned bank statements."""

from __future__ import annotations

import glob
import logging
import os
import re
import shutil
from typing import Dict, List, Optional

import pdfplumber
import pytesseract

pytesseract.pytesseract.tesseract_cmd = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)
from pdf2image import convert_from_path
from pdfminer.pdfdocument import PDFPasswordIncorrect
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}

# How many leading pages to probe for a real text layer, and how many extracted words on
# a single page count as "meaningful" (not scanned). Checked per-page, not summed across
# pages -- see detect_pdf_type() docstring for why that distinction matters in practice.
_DETECTION_PAGES_TO_CHECK = 3
_DETECTION_MIN_WORDS = 20


def _is_password_protected_error(exc: BaseException) -> bool:
    """pdfplumber wraps pdfminer's PDFPasswordIncorrect in its own PdfminerException,
    and critically that wrapper's str() is empty -- so string-matching on the message
    (e.g. `"password" in str(exc)`) silently never fires. The real exception is only
    reachable via .args[0] or the implicit exception-chaining context, so check both.
    """
    if isinstance(exc, PDFPasswordIncorrect):
        return True
    for candidate in (*getattr(exc, "args", ()), exc.__context__, exc.__cause__):
        if isinstance(candidate, PDFPasswordIncorrect):
            return True
    return False


def detect_pdf_type(pdf_path: str) -> str:
    """Return the detected PDF type: 'text' or 'image'.

    Opens the PDF and checks the first few pages ONE AT A TIME (not their combined
    total) for a real, extractable text layer via pdfplumber/pdfminer. As soon as any
    single page clears `_DETECTION_MIN_WORDS`, the PDF is treated as text-based;
    otherwise it's assumed to be scanned/image-based and routed to OCR.

    Checking page-by-page (rather than summing words across pages) matters for real
    statements: many have a near-blank cover page or a logo-only first page, and a
    combined-total check can let that thin page dilute an otherwise clearly text-based
    statement -- or, in the opposite direction, let scattered boilerplate across
    several thin pages add up to clear a low combined bar even though no single page
    actually has a usable transaction table on it.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    try:
        with pdfplumber.open(pdf_path) as pdf:
            if len(pdf.pages) == 0:
                return "image"
            for page in pdf.pages[:_DETECTION_PAGES_TO_CHECK]:
                page_text = page.extract_text() or ""
                word_count = len(re.findall(r"\b\w+\b", page_text))
                if word_count >= _DETECTION_MIN_WORDS:
                    return "text"
        return "image"
    except Exception as exc:
        if _is_password_protected_error(exc):
            raise RuntimeError(
                f"'{pdf_path}' is password-protected/encrypted. This prototype can't "
                "open encrypted PDFs -- remove the password first (most PDF readers "
                "can do this via File > Print > 'Save as PDF' with the password "
                "entered once, or a dedicated PDF-unlock tool) and try again."
            ) from exc
        logger.warning("Text detection failed for %s: %s", pdf_path, exc)
        return "image"


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract raw visible text from a text-based statement PDF."""
    extracted_pages: List[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            extracted_pages.append(text)
    return "\n".join(extracted_pages)


def extract_tables_from_pdf(pdf_path: str) -> List[List[List[Optional[str]]]]:
    """Extract structured tables (per-page) from a text-based PDF using pdfplumber.

    Returns a list of tables, one per page that contains a detected table (a page may
    contribute zero or more tables). Each table is a list of rows, each row a list of
    cell strings (or None for empty cells). Naturally handles multi-page statements
    since pages are walked in order and results concatenated.
    """
    all_tables: List[List[List[Optional[str]]]] = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                try:
                    page_tables = page.extract_tables()
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("Table extraction failed on a page of %s: %s", pdf_path, exc)
                    continue
                for table in page_tables or []:
                    if table:
                        all_tables.append(table)
    except Exception as exc:
        logger.warning("Unable to open %s for table extraction: %s", pdf_path, exc)
    return all_tables


def _resolve_poppler_path() -> Optional[str]:
    """Best-effort locate the poppler `bin` directory containing pdftoppm.

    pdf2image needs poppler's binaries on PATH. On Windows this is a very common
    source of "it works on my machine" failures because installers rarely add it to
    PATH automatically (and PATH changes don't take effect in an already-open shell).
    This checks, in order: an explicit POPPLER_PATH env var, whether pdftoppm is
    already resolvable on PATH, then a few common Windows install locations
    (winget package cache, Program Files) so OCR keeps working without requiring a
    shell restart.
    """
    env_path = os.environ.get("POPPLER_PATH")
    if env_path and os.path.isdir(env_path):
        return env_path

    if shutil.which("pdftoppm"):
        return None  # already on PATH; let pdf2image resolve it normally

    search_globs = [
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\*Poppler*\**\Library\bin"),
        r"C:\Program Files\poppler*\Library\bin",
        r"C:\Program Files\poppler*\bin",
        r"C:\poppler*\Library\bin",
        r"C:\poppler*\bin",
    ]
    for pattern in search_globs:
        for candidate in glob.glob(pattern, recursive=True):
            if os.path.isfile(os.path.join(candidate, "pdftoppm.exe")):
                return candidate
    return None


def verify_tesseract_available() -> None:
    """Raise a clear, actionable RuntimeError if the Tesseract OCR engine isn't reachable.

    This is checked once, up front, rather than left to surface inside the per-page OCR
    loop -- a missing binary there would previously just log a per-page warning and
    silently return empty text for every page, which looks identical to "OCR ran fine
    but the scan was blank" from the caller's point of view. Calling this eagerly turns
    a silent, confusing failure into one clear message naming the actual cause.
    """
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:
        raise RuntimeError(
            "Tesseract OCR engine not found (needed for scanned PDFs and image "
            "uploads). Install it and ensure it's on PATH:\n"
            "  Windows: winget install UB-Mannheim.TesseractOCR (or "
            "https://github.com/UB-Mannheim/tesseract/wiki), then restart your shell\n"
            "  macOS: brew install tesseract\n"
            "  Linux: sudo apt install tesseract-ocr\n"
            f"Original error: {exc}"
        ) from exc


def verify_poppler_available() -> None:
    """Raise a clear, actionable RuntimeError if Poppler isn't reachable.

    Only needed for scanned PDFs (pdf2image rasterizes pages via Poppler's pdftoppm);
    direct image uploads never call this since there's no PDF page to rasterize.
    """
    if _resolve_poppler_path() is not None or shutil.which("pdftoppm"):
        return
    raise RuntimeError(
        "Poppler not found (needed to rasterize scanned PDF pages before OCR). Install it:\n"
        "  Windows: winget install oschwartz10612.Poppler (then restart your shell, or "
        "set the POPPLER_PATH environment variable to its `Library\\bin` directory)\n"
        "  macOS: brew install poppler\n"
        "  Linux: sudo apt install poppler-utils"
    )


def _ocr_image(image: Image.Image) -> str:
    """OCR a single in-memory image (Tesseract availability must already be verified
    by the caller), with light preprocessing to help accuracy.

    Real phone photos carry an EXIF orientation tag rather than physically rotating
    pixel data -- PIL does NOT apply this automatically, and neither does Tesseract, so
    a photo taken in portrait mode can be processed sideways/upside-down, producing
    near-total OCR garbage with no error raised (verified: this reproduces exactly with
    a rotated+EXIF-tagged test image, and every synthetic test image used elsewhere in
    this project has no such metadata at all, so this bug was invisible to those tests).
    exif_transpose() is a no-op when no orientation tag is present.
    """
    image = ImageOps.exif_transpose(image) or image
    # Grayscale reduces noise from paper/scan texture; upscaling helps small statement
    # fonts that would otherwise fall below Tesseract's reliable recognition size.
    image = image.convert("L")
    if image.width < 1800:
        scale = 1800 / image.width
        image = image.resize((int(image.width * scale), int(image.height * scale)))
    return pytesseract.image_to_string(image, config="--psm 6")


def perform_ocr(pdf_path: str) -> str:
    """Convert scanned PDF pages to images and OCR them into text."""
    verify_tesseract_available()
    verify_poppler_available()
    poppler_path = _resolve_poppler_path()
    images = convert_from_path(pdf_path, poppler_path=poppler_path)

    ocr_pages: List[str] = []
    for page_number, image in enumerate(images, start=1):
        try:
            ocr_pages.append(_ocr_image(image))
        except Exception as exc:
            # A genuine per-page failure at this point is a real anomaly (corrupt
            # rendered image, etc.) now that the binaries themselves are confirmed
            # present -- surfacing it as a visible warning is correct here, since it's
            # no longer masking a systemic "OCR isn't installed" problem.
            logger.warning("OCR failed for page %d of %s: %s", page_number, pdf_path, exc)
            ocr_pages.append("")
    return "\n".join(ocr_pages)


def perform_ocr_on_image_file(image_path: str) -> str:
    """OCR a directly-uploaded image file (e.g. a phone photo of a scanned statement page)."""
    verify_tesseract_available()
    try:
        with Image.open(image_path) as image:
            return _ocr_image(image.copy())
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Unable to read image '{image_path}': {exc}") from exc


def extract_pdf_text(pdf_path: str) -> Dict[str, object]:
    """Detect PDF type and return the raw extracted text (+ tables, for text PDFs) with metadata."""
    pdf_type = detect_pdf_type(pdf_path)

    tables: List[List[List[Optional[str]]]] = []
    try:
        if pdf_type == "text":
            raw_text = extract_text_from_pdf(pdf_path)
            tables = extract_tables_from_pdf(pdf_path)
        else:
            raw_text = perform_ocr(pdf_path)
    except Exception as exc:
        raise RuntimeError(f"Failed to process PDF '{pdf_path}': {exc}") from exc

    return {
        "pdf_path": pdf_path,
        "pdf_type": pdf_type,
        "text": raw_text.strip(),
        "tables": tables,
    }


def extract_statement(file_path: str) -> Dict[str, object]:
    """Entry point that dispatches on file type: PDF (text or scanned) or a raw image upload.

    Raw images (PNG/JPG/etc., e.g. a phone photo of a printed statement) are OCR'd directly
    with no PDF conversion step; everything else goes through `extract_pdf_text`.
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        raw_text = perform_ocr_on_image_file(file_path)
        return {
            "pdf_path": file_path,
            "pdf_type": "image",
            "text": raw_text.strip(),
            "tables": [],
        }
    return extract_pdf_text(file_path)
