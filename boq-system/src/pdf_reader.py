"""
pdf_reader.py — Converts PDF pages to images, sends to GPT-4o for schedule extraction.

Each PDF page is converted to PNG using pdf2image, then sent to GPT-4o
with a structured prompt asking for construction schedule data.

Returns: pdf_data dict with extracted schedules and notes.
"""

import os
import base64
import json
from io import BytesIO

from PIL import Image

try:
    from pdf2image import convert_from_path
except ImportError:
    convert_from_path = None

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

EXTRACTION_PROMPT = """You are a quantity surveyor reading a construction drawing page.
Extract all visible information including:
- Room names and dimensions
- Door schedule (type, size, quantity)
- Window schedule (type, size, quantity)
- Finish schedule (room, floor finish, wall finish, ceiling finish)
- Structural notes
- Any dimensions or measurements visible
- Material specifications
- General notes and annotations

Return your response as valid JSON only, with this structure:
{
  "page_type": "floor_plan|elevation|section|schedule|detail|notes",
  "rooms": [{"name": "...", "area_m2": null, "dimensions": "..."}],
  "door_schedule": [{"type": "...", "size": "...", "qty": 0, "notes": "..."}],
  "window_schedule": [{"type": "...", "size": "...", "qty": 0, "notes": "..."}],
  "finish_schedule": [{"room": "...", "floor": "...", "wall": "...", "ceiling": "..."}],
  "structural_notes": ["..."],
  "dimensions": {"key": "value"},
  "materials": ["..."],
  "general_notes": ["..."]
}

Only include fields where you found actual data. Return empty arrays for sections with no data.
Return ONLY the JSON, no other text."""


def read_pdfs(pdf_dir: str, max_pages: int = 50) -> dict:
    """Read all PDF files in a directory and extract construction data.

    Args:
        pdf_dir: Directory containing PDF files.
        max_pages: Maximum total pages to process across all PDFs.

    Returns:
        dict with keys:
          - pages: list of per-page extraction results
          - door_schedule: merged door schedule
          - window_schedule: merged window schedule
          - finish_schedule: merged finish schedule
          - notes: all collected notes
          - errors: any processing errors
    """
    if convert_from_path is None:
        return {
            "pages": [],
            "door_schedule": [],
            "window_schedule": [],
            "finish_schedule": [],
            "notes": [],
            "errors": ["pdf2image not installed. Install with: pip install pdf2image"],
        }

    if not os.path.isdir(pdf_dir):
        return {
            "pages": [],
            "door_schedule": [],
            "window_schedule": [],
            "finish_schedule": [],
            "notes": [],
            "errors": [f"PDF directory not found: {pdf_dir}"],
        }

    pdf_files = sorted([
        f for f in os.listdir(pdf_dir)
        if f.lower().endswith(".pdf")
    ])

    if not pdf_files:
        return {
            "pages": [],
            "door_schedule": [],
            "window_schedule": [],
            "finish_schedule": [],
            "notes": [],
            "errors": ["No PDF files found in directory"],
        }

    client = _get_openai_client()
    all_pages = []
    errors = []
    pages_processed = 0

    for pdf_file in pdf_files:
        if pages_processed >= max_pages:
            break

        pdf_path = os.path.join(pdf_dir, pdf_file)
        try:
            images = convert_from_path(pdf_path, dpi=200)
        except Exception as e:
            errors.append(f"Failed to convert {pdf_file}: {e}")
            continue

        for i, image in enumerate(images):
            if pages_processed >= max_pages:
                break

            try:
                result = _process_page(client, image, pdf_file, i + 1)
                all_pages.append(result)
            except Exception as e:
                errors.append(f"Failed to process {pdf_file} page {i + 1}: {e}")

            pages_processed += 1

    # Merge all extracted data
    merged = _merge_page_data(all_pages)
    merged["errors"] = errors

    return merged


def _get_openai_client() -> OpenAI:
    """Create OpenAI client from environment."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY not set. Add it to your .env file."
        )
    return OpenAI(api_key=api_key)


def _process_page(client: OpenAI, image: Image.Image, filename: str, page_num: int) -> dict:
    """Send a single page image to GPT-4o for extraction."""
    # Convert PIL image to base64
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    img_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": EXTRACTION_PROMPT,
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{img_base64}",
                            "detail": "high",
                        },
                    },
                ],
            }
        ],
        max_tokens=4096,
        temperature=0.1,
    )

    content = response.choices[0].message.content.strip()

    # Parse JSON response
    try:
        # Handle markdown code blocks
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        data = json.loads(content)
    except json.JSONDecodeError:
        data = {"raw_text": content, "parse_error": True}

    data["_source_file"] = filename
    data["_page_number"] = page_num

    return data


def _merge_page_data(pages: list[dict]) -> dict:
    """Merge extracted data from all pages into unified schedules."""
    door_schedule = []
    window_schedule = []
    finish_schedule = []
    notes = []
    rooms = []

    for page in pages:
        if "door_schedule" in page:
            for item in page["door_schedule"]:
                item["_source"] = f"{page.get('_source_file', '?')} p{page.get('_page_number', '?')}"
                door_schedule.append(item)

        if "window_schedule" in page:
            for item in page["window_schedule"]:
                item["_source"] = f"{page.get('_source_file', '?')} p{page.get('_page_number', '?')}"
                window_schedule.append(item)

        if "finish_schedule" in page:
            for item in page["finish_schedule"]:
                item["_source"] = f"{page.get('_source_file', '?')} p{page.get('_page_number', '?')}"
                finish_schedule.append(item)

        if "rooms" in page:
            rooms.extend(page["rooms"])

        if "general_notes" in page:
            notes.extend(page["general_notes"])
        if "structural_notes" in page:
            notes.extend(page["structural_notes"])

    return {
        "pages": pages,
        "door_schedule": door_schedule,
        "window_schedule": window_schedule,
        "finish_schedule": finish_schedule,
        "rooms": rooms,
        "notes": notes,
    }
