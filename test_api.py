"""
test_api.py — Quick smoke-test for the Diagram Processor API.

Run AFTER starting the server:
    uvicorn app.main:app --reload --port 8000

Then in another terminal:
    python test_api.py
"""

import json
import time
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"

# ── Replace this with a real folder on your machine ──────────────────────────
# The folder must contain at least one .pdf / .pptx / .docx / .xlsx file.
TEST_FOLDER = "input\Mine Operation Management.docx"   # ← CHANGE THIS


def request(method: str, path: str, body: dict = None):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def main():
    print("=" * 60)
    print("Diagram Processor — API smoke test")
    print("=" * 60)

    # 1. Health check
    status, body = request("GET", "/api/v1/health")
    print(f"\n[1] GET /api/v1/health  →  {status}")
    print(f"    groq_configured : {body.get('groq_configured')}")
    print(f"    groq_model      : {body.get('groq_model')}")
    if not body.get("groq_configured"):
        print("\n  ⚠  GROQ_API_KEY is not set in your .env file!")
        print("     Edit .env → set GROQ_API_KEY=your_actual_key")
        print("     Then restart: uvicorn app.main:app --reload --port 8000")

    # 2. Submit job
    print(f"\n[2] POST /api/v1/process  (folder: {TEST_FOLDER})")
    status, body = request("POST", "/api/v1/process", {"folder_path": TEST_FOLDER})
    print(f"    HTTP status : {status}")

    if status == 422:
        print("    ✘ Validation error:")
        print("     ", json.dumps(body, indent=4))
        sys.exit(1)

    if status == 400:
        print("    ✘ Bad request:", body.get("detail"))
        print(f"\n  ⚠  Make sure the folder exists and contains supported files:")
        print(f"      {TEST_FOLDER}")
        sys.exit(1)

    if status != 200:
        print("    ✘ Unexpected error:", body)
        sys.exit(1)

    job_id = body["job_id"]
    print(f"    ✔ Job accepted — job_id: {job_id}")
    print(f"    Files queued  : {body['total_files_found']}")

    # 3. Poll status
    print(f"\n[3] Polling GET /api/v1/status/{job_id} ...")
    for i in range(30):
        time.sleep(3)
        status, body = request("GET", f"/api/v1/status/{job_id}")
        pct = body.get("progress_percent", 0)
        s = body.get("status")
        print(f"    [{i*3:>3}s]  status={s}  progress={pct}%")
        if s in ("completed", "failed"):
            break

    if body.get("status") == "failed":
        print(f"\n  ✘ Job failed: {body.get('error')}")
        sys.exit(1)

    if body.get("status") != "completed":
        print("\n  ⚠  Job still running after 90s — check logs/diagram_processor.log")
        sys.exit(0)

    print(f"\n    ✔ Job completed!")
    for fr in body.get("file_results", []):
        print(f"      {fr['file_name']}: {fr['sections_extracted']} sections, {fr['diagrams_detected']} diagrams")

    # 4. Download ZIP
    print(f"\n[4] GET /api/v1/download/{job_id}")
    print(f"    Open in browser or run:")
    print(f"    curl -OJ http://127.0.0.1:8000/api/v1/download/{job_id}")

    print("\n" + "=" * 60)
    print("All checks passed!")


if __name__ == "__main__":
    main()