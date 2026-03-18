"""
pricer.py — Matches items to rate library, GPT-4o fallback.

For each BOQ item:
  1. Search rate_library by keyword match on description
  2. If match found → use library rate (rate_source: "library")
  3. If no match → ask GPT-4o for PNG market rate estimate
     (rate_source: "AI-estimate", flagged for review)
  4. If no GPT-4o response → leave rate blank (rate_source: "manual-required")

RULE: Never invent rates. Never guess silently.
"""

import os
import json
import re
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()


def apply_rates(
    project_boq: list[dict],
    rate_library: list[dict],
    use_ai_fallback: bool = True,
) -> list[dict]:
    """Apply rates from the rate library to all BOQ items.

    Args:
        project_boq: Project BOQ items from quantity_calculator.
        rate_library: Rate library entries from loader.
        use_ai_fallback: Whether to use GPT-4o for unmatched items.

    Returns:
        Updated BOQ with rates applied and rate_source tags.
    """
    # Build search index from rate library
    rate_index = _build_rate_index(rate_library)

    # Items that need AI fallback
    unmatched_items = []

    for i, item in enumerate(project_boq):
        # Skip items that already have a rate from the standard BOQ
        if item.get("rate") and item.get("rate_source") == "library":
            continue

        # Try to match from rate library
        match = _find_rate_match(item, rate_index, rate_library)

        if match:
            project_boq[i]["rate"] = match["rate"]
            project_boq[i]["rate_source"] = "library"
            project_boq[i]["rate_match"] = match.get("description", "")
        elif item.get("rate"):
            # Has a rate from the standard BOQ — keep it
            project_boq[i]["rate_source"] = "standard"
        else:
            # No rate found
            project_boq[i]["rate_source"] = "unmatched"
            if use_ai_fallback:
                unmatched_items.append((i, item))

    # Batch AI fallback for unmatched items
    if unmatched_items and use_ai_fallback:
        ai_rates = _get_ai_rates([item for _, item in unmatched_items])
        for j, (idx, _) in enumerate(unmatched_items):
            if j < len(ai_rates) and ai_rates[j] is not None:
                project_boq[idx]["rate"] = ai_rates[j]
                project_boq[idx]["rate_source"] = "AI-estimate"
                project_boq[idx]["notes"] = (
                    (project_boq[idx].get("notes") or "") +
                    " | Rate is AI-estimated — verify before use"
                ).strip(" |")
            else:
                project_boq[idx]["rate"] = None
                project_boq[idx]["rate_source"] = "manual-required"
                project_boq[idx]["notes"] = (
                    (project_boq[idx].get("notes") or "") +
                    " | No rate found — manual entry required"
                ).strip(" |")

    return project_boq


def _build_rate_index(rate_library: list[dict]) -> dict:
    """Build keyword index from rate library for fast matching."""
    index = {}

    for entry in rate_library:
        code = str(entry.get("stock_code", "")).strip().upper()
        desc = str(entry.get("description", "")).strip().lower()

        if code:
            index[code] = entry

        # Index by significant words in description
        words = re.findall(r'\b[a-z]{3,}\b', desc)
        for word in words:
            if word not in index:
                index[word] = []
            if isinstance(index[word], list):
                index[word].append(entry)

    return index


def _find_rate_match(
    item: dict,
    rate_index: dict,
    rate_library: list[dict],
) -> dict | None:
    """Find the best matching rate for a BOQ item.

    Priority:
      1. Exact stock code match
      2. Best keyword overlap in description
    """
    # 1. Try exact stock code match
    item_code = str(item.get("stock_code", "")).strip().upper()
    if item_code and item_code in rate_index:
        match = rate_index[item_code]
        if isinstance(match, dict) and match.get("rate"):
            return match

    # 2. Keyword matching on description
    item_desc = str(item.get("description", "")).lower()
    item_words = set(re.findall(r'\b[a-z]{3,}\b', item_desc))

    if not item_words:
        return None

    best_match = None
    best_score = 0

    for entry in rate_library:
        entry_desc = str(entry.get("description", "")).lower()
        entry_words = set(re.findall(r'\b[a-z]{3,}\b', entry_desc))

        if not entry_words:
            continue

        # Calculate overlap score
        common = item_words & entry_words
        if not common:
            continue

        # Jaccard-like score weighted by word significance
        score = len(common) / len(item_words | entry_words)

        # Bonus for matching specific construction terms
        construction_terms = {
            "roof", "wall", "floor", "door", "window", "ceiling",
            "batten", "sheet", "board", "frame", "post", "beam",
            "pipe", "fitting", "screw", "bolt", "bracket",
        }
        term_bonus = len(common & construction_terms) * 0.1
        score += term_bonus

        if score > best_score and entry.get("rate"):
            best_score = score
            best_match = entry

    # Require minimum match quality
    if best_score >= 0.3:
        return best_match

    return None


def _get_ai_rates(items: list[dict]) -> list[float | None]:
    """Get rate estimates from GPT-4o for unmatched items.

    Batches items to minimise API calls.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return [None] * len(items)

    client = OpenAI(api_key=api_key)

    # Batch items (max 20 per request)
    batch_size = 20
    all_rates = []

    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        rates = _request_ai_rates(client, batch)
        all_rates.extend(rates)

    return all_rates


def _request_ai_rates(client: OpenAI, items: list[dict]) -> list[float | None]:
    """Send a batch of items to GPT-4o for rate estimation."""
    items_text = "\n".join([
        f"{i + 1}. {item.get('description', 'Unknown')} ({item.get('unit', 'no')})"
        for i, item in enumerate(items)
    ])

    prompt = f"""You are a construction cost estimator for Papua New Guinea.
For each item below, provide the typical SUPPLY-ONLY rate in PGK (Papua New Guinea Kina).
These are for standard residential construction as of 2025.

Items:
{items_text}

Return a JSON array of numbers (rates in PGK), one per item, in the same order.
If you cannot estimate a rate for an item, use null.
Return ONLY the JSON array, no other text.
Example: [45.50, 12.00, null, 89.00]"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
            temperature=0.2,
        )

        content = response.choices[0].message.content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        rates_raw = json.loads(content)

        rates = []
        for r in rates_raw:
            if r is None:
                rates.append(None)
            else:
                try:
                    rates.append(round(float(r), 2))
                except (ValueError, TypeError):
                    rates.append(None)

        # Pad if response is shorter than input
        while len(rates) < len(items):
            rates.append(None)

        return rates[:len(items)]

    except Exception:
        return [None] * len(items)
