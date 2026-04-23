# Diagram Processor — FastAPI Backend

## Project Structure

```
diagram_processor/
├── app/
│   ├── main.py                  # FastAPI app, routes
│   ├── config.py                # Settings, env vars
│   ├── parsers/
│   │   ├── base_parser.py       # Abstract base parser
│   │   ├── pdf_parser.py        # PDF → pages/images/text
│   │   ├── pptx_parser.py       # PPTX/PPT → slides/images/text
│   │   ├── docx_parser.py       # DOCX/DOC → paragraphs/images
│   │   └── xlsx_parser.py       # XLSX/XLS → sheets/text
│   ├── extractors/
│   │   ├── diagram_detector.py  # Heuristics + OCR to flag diagram regions
│   │   └── ocr_engine.py        # Tesseract OCR wrapper
│   ├── processors/
│   │   ├── groq_client.py       # Groq API wrapper (async)
│   │   └── diagram_explainer.py # Orchestrates LLM explanation
│   ├── generators/
│   │   └── docx_generator.py    # Build per-file and master DOCX
│   └── utils/
│       ├── file_utils.py        # File validation, temp dirs, ZIP
│       └── logger.py            # Structured logging setup
├── logs/
├── outputs/
└── requirements.txt
```

## Setup

```bash
pip install -r requirements.txt
export GROQ_API_KEY=your_key_here
uvicorn app.main:app --reload --port 8000
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/process` | Start batch processing (JSON body: `{"folder_path": "..."}`) |
| GET  | `/api/v1/status/{job_id}` | Poll job status |
| GET  | `/api/v1/download/{job_id}` | Download ZIP result |
| GET  | `/api/v1/health` | Health check |