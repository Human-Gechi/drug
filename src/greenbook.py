"Everything specific to the NAFDAC Greenbook (greenbook.nafdac.gov.ng)"

import re
from typing import List, Optional

from apify import Actor
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

GREENBOOK_HOST = "greenbook.nafdac.gov.ng"
GREENBOOK_URL = "https://greenbook.nafdac.gov.ng/"

# Brand → active ingredient. Extend freely; unmapped terms fall through unchanged.
# Keys must be lowercase and match how the brand appears in a user's question.
BRAND_TO_ACTIVE = {
    # Analgesics / antipyretics
    "panadol": "Paracetamol",
    "panadol extra": "Paracetamol",
    "tylenol": "Paracetamol",
    "acetaminophen": "Paracetamol",
    "paracetamol": "Paracetamol",
    "nurofen": "Ibuprofen",
    "brufen": "Ibuprofen",
    "advil": "Ibuprofen",
    "ibuprofen": "Ibuprofen",
    "voltaren": "Diclofenac",
    "cataflam": "Diclofenac",
    "diclofenac": "Diclofenac",
    "aspirin": "Aspirin",
    "novalgin": "Metamizole",
    "metamizole": "Metamizole",
    # Antibiotics
    "amoxil": "Amoxicillin",
    "amoxicillin": "Amoxicillin",
    "augmentin": "Amoxicillin",
    "flagyl": "Metronidazole",
    "metronidazole": "Metronidazole",
    "cipro": "Ciprofloxacin",
    "ciprofloxacin": "Ciprofloxacin",
    "zithromax": "Azithromycin",
    "azithromycin": "Azithromycin",
    "septrin": "Cotrimoxazole",
    "cotrimoxazole": "Cotrimoxazole",
    "co-trimoxazole": "Cotrimoxazole",
    "ampiclox": "Ampicillin",
    "ampicillin": "Ampicillin",
    "tetracycline": "Tetracycline",
    "erythromycin": "Erythromycin",
    "gentamicin": "Gentamicin",
    "ceftriaxone": "Ceftriaxone",
    # Antimalarials
    "coartem": "Artemether",
    "lonart": "Artemether",
    "artemether": "Artemether",
    "lumefantrine": "Lumefantrine",
    "artesunate": "Artesunate",
    "chloroquine": "Chloroquine",
    "primaquine": "Primaquine",
    "quinine": "Quinine",
    # Antiparasitics
    "vermox": "Mebendazole",
    "mebendazole": "Mebendazole",
    "zentel": "Albendazole",
    "albendazole": "Albendazole",
    "ivermectin": "Ivermectin",
    "praziquantel": "Praziquantel",
    # Antihistamines / allergy
    "piriton": "Chlorpheniramine",
    "chlorpheniramine": "Chlorpheniramine",
    "clarityne": "Loratadine",
    "loratadine": "Loratadine",
    "cetirizine": "Cetirizine",
    "zyrtec": "Cetirizine",
    "diphenhydramine": "Diphenhydramine",
    # Respiratory
    "ventolin": "Salbutamol",
    "salbutamol": "Salbutamol",
    "albuterol": "Salbutamol",
    "seretide": "Salmeterol",
    "salmeterol": "Salmeterol",
    "beclomethasone": "Beclomethasone",
    # GI
    "losec": "Omeprazole",
    "omeprazole": "Omeprazole",
    "zantac": "Ranitidine",
    "ranitidine": "Ranitidine",
    "esomeprazole": "Esomeprazole",
    "nexium": "Esomeprazole",
    "ors": "Oral Rehydration Salts",
    # Cardiovascular / metabolic
    "glucophage": "Metformin",
    "metformin": "Metformin",
    "norvasc": "Amlodipine",
    "amlodipine": "Amlodipine",
    "lipitor": "Atorvastatin",
    "atorvastatin": "Atorvastatin",
    "losartan": "Losartan",
    "lisinopril": "Lisinopril",
    "hydrochlorothiazide": "Hydrochlorothiazide",
    "nifedipine": "Nifedipine",
    "propranolol": "Propranolol",
    "atenolol": "Atenolol",
    # Others
    "insulin": "Insulin",
    "prednisolone": "Prednisolone",
    "dexamethasone": "Dexamethasone",
    "hydrocortisone": "Hydrocortisone",
    "folic acid": "Folic Acid",
    "ferrous sulphate": "Ferrous Sulphate",
    "ferrous sulfate": "Ferrous Sulphate",
    "vitamin c": "Ascorbic Acid",
    "ascorbic acid": "Ascorbic Acid",
}


def map_to_active(term: str) -> Optional[str]:
    """Return the active-ingredient name for a known brand, else None."""
    return BRAND_TO_ACTIVE.get(term.lower().strip())


def expand_terms(terms: List[str]) -> List[str]:
    """Given user-facing terms, return the terms to actually search on the Greenbook.
    A mapped brand is replaced by its active ingredient; an already-generic term
    is kept as-is. Order is preserved and duplicates are removed."""
    out: List[str] = []
    seen = set()
    for t in terms:
        mapped = map_to_active(t) or t
        key = mapped.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(mapped)
    return out


# Selectors that indicate the results table has actually rendered. Tried in order.
_TABLE_READY_SELECTORS = (
    "table tbody tr",
    "table tr",
    "table",
)


async def search_greenbook(page: Page, term: str, timeout_ms: int) -> bool:
    """Submit `term` in the Greenbook product-name box and wait for the results
    table to actually render. Returns True if the table exists, False otherwise.

    Critically this waits on the DOM (a real <tr> appearing), not on networkidle
    -- the AJAX response can complete while React/Vue is still painting rows."""
    try:
        inputs = await page.query_selector_all('input[type="text"], input:not([type])')
        target = None
        for inp in inputs:
            try:
                if not await inp.is_visible():
                    continue
                meta = " ".join(
                    (await inp.get_attribute(a)) or ""
                    for a in ("placeholder", "name", "id", "aria-label")
                ).lower()
            except Exception:
                meta = ""
            if "product" in meta:
                target = inp
                break
        if target is None:
            for inp in inputs:
                try:
                    if await inp.is_visible():
                        target = inp
                        break
                except Exception:
                    continue
        if not target:
            Actor.log.warning("Greenbook: no visible search input found.")
            return False

        await target.click(timeout=2000)
        await target.fill("", timeout=2000)
        await target.fill(term, timeout=2000)
        await page.keyboard.press("Enter")

        for selector in _TABLE_READY_SELECTORS:
            try:
                await page.wait_for_selector(selector, timeout=timeout_ms, state="attached")
                break
            except PlaywrightTimeoutError:
                continue

        await page.wait_for_timeout(500)
        return True
    except Exception as exc:
        Actor.log.warning("Greenbook search failed for %r: %r", term, exc)
        return False


def has_rows(html: str) -> bool:
    """Cheap check: does the HTML contain any <tr> with at least one <td>?"""
    return bool(re.search(r"<tr[^>]*>\s*<td", html, re.I))