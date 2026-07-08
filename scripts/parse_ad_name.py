"""Quasi Meta ad-name parser.

Quasi's naming convention is mid-rework; real spend today runs through five
observed families (share of spend in a $47K/day sample):

1. product_underscore (49%):
   GLOW_727_IT_H3_Graduation_DVW_AIPixarSongAnimation_Kian_Mark+Penny_ProblemAware
   PRODUCT_BATCH_[H#|audience in either order]_CONCEPT_FORMAT_STYLE_CREATOR_STRATEGIST_AWARENESS
2. product_dash (24%):
   COL-785-H1-NN-DVW-AI Animation-Martin_Daniyal
   Same fields dash-delimited; creator pair underscore-joined at the end.
   Includes the MHI sub-family (8-char code token + LP token like PDP):
   MHI-H1-OBGCNV8Y-Marc-Sally-Video-Net_New-AI_VO_+_B_Roll-PDP-Product_Aware
3. leading_number (10%):
   754_Glow_IT_H4_..._ProblemAware  — batch first, product second
4. pb_legacy (10%):
   "P4 B254 …" / "P4_B312_rev_2" / "B664 …" — P-phase + B-batch, rest best-effort
5. freeform (7%): convention "unknown", fields null

Each family has an isolated `_parse_<family>` entry point so the NEW
convention (in progress) can drop in as family #6 without touching the
existing ones — mirroring IM8's old/new split.

The output dict is schema-compatible with the IM8 dashboard: every key the
frontend pivots on is always present (None when not applicable).

Quasi field mapping onto the IM8 schema:
  format        <- DVW/Video/UGC -> VID, IVW/Static/Image -> IMG (original in ad_type)
  icp           <- audience: IT | NN (Net_New normalized to NN)
  problem       <- awareness stage (ProblemAware/SolutionAware/ProductAware/Unaware)
  concept       <- angle token (Graduation, ExHusbandRevenge, ...)
  creative_no   <- batch number (727)
  batch_name    <- product prefix (GLOW / PDRN / COL / MHI / SLP ...)
  creator_name  <- first person name
  agency        <- second person name (the strategist — tab is relabeled "Strategists")
  hook          <- H# / Hook # normalized to H1..Hn
  creator_type  <- style token (AIPixarSongAnimation, AI Animation, UGC, ...)
  landing_page  <- PDP etc. when present (MHI family)
  date          <- None here; the fetch script fills it from first-seen date
  winner_*      <- None (no winner tags in Quasi names yet; kept for the Win Rate tab)
"""
import re
from typing import Optional

# Product prefixes seen in real data. COLLAGEN folds into COL.
PRODUCTS = {
    "GLOW": "GLOW",
    "PDRN": "PDRN",
    "COL": "COL",
    "COLLAGEN": "COL",
    "MHI": "MHI",
    "SLP": "SLP",
}

# Audience tokens -> canonical ICP.
AUDIENCES = {
    "IT": "IT",
    "NN": "NN",
    "NET_NEW": "NN",
    "NET NEW": "NN",
    "NETNEW": "NN",
}

# Format tokens -> canonical dashboard format. Original token kept in ad_type.
FORMATS = {
    "DVW": "VID",
    "VIDEO": "VID",
    "VID": "VID",
    "UGC": "VID",
    "IVW": "IMG",
    "STATIC": "IMG",
    "IMAGE": "IMG",
    "IMG": "IMG",
    "GRAPHIC": "IMG",
}

# Landing-page tokens (MHI family mostly).
LANDING_PAGES = {"PDP", "LP", "HP", "ADV", "ADVERTORIAL", "LISTICLE"}

HOOK_RE = re.compile(r"^H(?:OOK)?[\s_-]?(\d{1,2})$", re.IGNORECASE)
AWARENESS_RE = re.compile(
    r"^(?:(problem|solution|product|prodcut|porduct)[\s_-]*aware(?:ness)?|un[\s_-]*aware)$",
    re.IGNORECASE,
)
# Style/creator-type tokens: anything that reads like a production style.
STYLE_RE = re.compile(
    r"(anim|ugc|vsl|voice\s*over|voiceover|b[\s_+]*rolls?|illustration|"
    r"talking[\s_]*head|mashup|whiteboard|testimonial|podcast|skit|"
    r"^ai[\s_+-]|[\s_+-]ai$|^ai$)",
    re.IGNORECASE,
)
# MHI-style opaque code: 6-10 uppercase alphanumerics with at least one digit.
CODE_RE = re.compile(r"^(?=.*\d)[A-Z0-9]{6,10}$")
# Common misspellings worth canonicalizing so the Data Clean Up tab stays sane.
STYLE_CANON = {
    "AIPIXARSONGANIMATUON": "AIPixarSongAnimation",
    "AIPIXARSONGANIMATION": "AIPixarSongAnimation",
    "AI PIXAR SONG ANIMATION": "AIPixarSongAnimation",
    "AIANIMATION": "AIAnimation",
    "AI ANIMATION": "AIAnimation",
    "AI-ANIMATION": "AIAnimation",
}
AWARENESS_CANON = {
    "PROBLEM": "ProblemAware",
    "SOLUTION": "SolutionAware",
    "PRODUCT": "ProductAware",
    "PRODCUT": "ProductAware",  # real-world typo
    "PORDUCT": "ProductAware",
}


def _empty_result(name: str) -> dict:
    return {
        "ad_name": name,
        "convention": "unknown",
        "format": None,
        "ad_type": None,
        "icp": None,
        "problem": None,
        "concept": None,
        "creative_no": None,
        "agency": None,
        "batch_name": None,
        "creator_type": None,
        "creator_name": None,
        "hook": None,
        "wtad": None,
        "landing_page": None,
        "date": None,          # filled by fetch script from first-seen date
        "paid_seed": None,
        "winner": None,
        "winner_year": None,
        "winner_month": None,
        "winner_week": None,
        "dedup_key": None,
    }


def _clean_suffixes(name: str) -> str:
    """Strip copy/dupe suffixes so edited duplicates share a dedup_key."""
    w = name.strip()
    # Iteratively strip trailing junk; ordering matters little, repetition does.
    prev = None
    while w != prev:
        prev = w
        w = re.sub(r"\s*[-–]\s*copy(\s*\d+)?$", "", w, flags=re.IGNORECASE)
        w = re.sub(r"\s*\(\d+\)$", "", w)
        w = re.sub(r"\s*@2x$", "", w, flags=re.IGNORECASE)
        w = re.sub(r"[\s_-]+adnova$", "", w, flags=re.IGNORECASE)
        w = re.sub(r"-{2,}$", "", w)
        w = re.sub(r"\s+$", "", w)
    return w


def _strip_version_suffix(w: str) -> str:
    """Drop a trailing _1/_2 version marker on long structured names."""
    m = re.search(r"_(\d)$", w)
    if m and len(re.split(r"[_-]", w)) >= 6:
        return w[: m.start()]
    return w


def _canon_hook(tok: str) -> Optional[str]:
    m = HOOK_RE.match(tok.strip())
    return f"H{int(m.group(1))}" if m else None


def _canon_awareness(tok: str) -> Optional[str]:
    t = tok.strip()
    m = AWARENESS_RE.match(t)
    if not m:
        return None
    if m.group(1) is None:
        return "Unaware"
    return AWARENESS_CANON[m.group(1).upper()]


def _canon_style(tok: str) -> Optional[str]:
    t = tok.strip()
    if not STYLE_RE.search(t):
        return None
    return STYLE_CANON.get(t.upper(), t)


def _is_name_like(tok: str) -> bool:
    """Person-name heuristic used only in the trailing name slots."""
    t = tok.strip()
    if not t or t.upper() in {"NA", "N/A"}:
        return False
    # Mark+Penny, Mark & Penny, single names; allow accents.
    return bool(re.match(r"^[A-Za-zÀ-ÿ]+([+&' ][A-Za-zÀ-ÿ]+)*$", t)) and len(t) <= 30


def _classify_tokens(tokens: list, result: dict) -> list:
    """First-match-wins classification of shared fields; returns leftovers
    in original order tagged with their token index."""
    leftovers = []
    for i, raw in enumerate(tokens):
        tok = raw.strip().strip("[]")
        if not tok or tok.upper() in {"NA", "N/A"}:
            continue
        up = tok.upper()
        hook = _canon_hook(tok)
        aware = _canon_awareness(tok)
        if hook and not result["hook"]:
            result["hook"] = hook
        elif aware and not result["problem"]:
            result["problem"] = aware
        elif up in AUDIENCES and not result["icp"]:
            result["icp"] = AUDIENCES[up]
        elif up in FORMATS and not result["format"]:
            result["format"] = FORMATS[up]
            result["ad_type"] = tok
        elif up in LANDING_PAGES and not result["landing_page"]:
            result["landing_page"] = up if up != "ADVERTORIAL" else "ADV"
        elif up in PRODUCTS and not result["batch_name"]:
            result["batch_name"] = PRODUCTS[up]
        elif tok.isdigit() and 2 <= len(tok) <= 4 and not result["creative_no"]:
            result["creative_no"] = tok
        elif _canon_style(tok) and not result["creator_type"]:
            result["creator_type"] = _canon_style(tok)
        else:
            leftovers.append((i, tok))
    return leftovers


def _assign_concept_and_names(leftovers: list, tokens: list, result: dict) -> None:
    """Split unclassified tokens into concept (before the format/style token)
    and creator/strategist names (after it)."""
    # Find the pivot: index of the style token if present, else the format token.
    pivot = None
    for i, raw in enumerate(tokens):
        tok = raw.strip().strip("[]")
        if result["creator_type"] and _canon_style(tok) == result["creator_type"]:
            pivot = i
        elif pivot is None and result["ad_type"] and tok == result["ad_type"]:
            pivot = i
    concept_toks, name_toks = [], []
    saw_code = False
    for i, tok in leftovers:
        # Opaque MHI-style codes read as concept regardless of position; in
        # that family the creator/strategist names follow the code directly.
        if CODE_RE.match(tok):
            concept_toks.append(tok)
            saw_code = True
            continue
        if saw_code and _is_name_like(tok) and len(name_toks) < 2:
            name_toks.append(tok)
        elif pivot is not None and i > pivot and _is_name_like(tok):
            name_toks.append(tok)
        elif pivot is None or i < pivot:
            concept_toks.append(tok)
        elif _is_name_like(tok):
            name_toks.append(tok)
    if concept_toks and not result["concept"]:
        result["concept"] = " ".join(concept_toks)
    if name_toks:
        result["creator_name"] = name_toks[0]
        if len(name_toks) > 1:
            result["agency"] = name_toks[1]


def _matches_canonical(tok: str) -> bool:
    """True when a token already reads as a known field value and must not be
    split further (Net_New, Product_Aware, AI_VO_+_B_Roll, …)."""
    up = tok.upper()
    return bool(
        up in AUDIENCES
        or up in FORMATS
        or up in LANDING_PAGES
        or up in PRODUCTS
        or _canon_hook(tok)
        or _canon_awareness(tok)
        or _canon_style(tok)
    )


def _split_dash_tokens(working: str) -> list:
    """Dash-family tokenizer. The trailing creator pair is underscore-joined
    (…-Martin_Daniyal), so expand that into two tokens."""
    tokens = [t for t in working.split("-") if t.strip()]
    out = []
    for t in tokens:
        t = t.strip()
        # Underscore-joined trailing name pair: Martin_Daniyal — but never
        # split tokens that already carry field meaning (Net_New, Product_Aware).
        parts = t.split("_")
        if (
            2 <= len(parts) <= 3
            and not _matches_canonical(t)
            and all(_is_name_like(p) for p in parts if p)
        ):
            out.extend(parts)
        else:
            out.append(t)
    return out


def _parse_product_underscore(working: str, result: dict) -> None:
    result["convention"] = "product_underscore"
    tokens = [t for t in working.split("_") if t.strip()]
    leftovers = _classify_tokens(tokens, result)
    _assign_concept_and_names(leftovers, tokens, result)


def _parse_product_dash(working: str, result: dict) -> None:
    result["convention"] = "product_dash"
    tokens = _split_dash_tokens(working)
    leftovers = _classify_tokens(tokens, result)
    _assign_concept_and_names(leftovers, tokens, result)


def _parse_leading_number(working: str, result: dict) -> None:
    result["convention"] = "leading_number"
    tokens = [t for t in re.split(r"[_]", working) if t.strip()]
    # Batch number leads; product token follows in any casing (Glow, glow).
    if tokens and tokens[0].isdigit():
        result["creative_no"] = tokens[0]
        tokens = tokens[1:]
    if tokens and tokens[0].upper() in PRODUCTS:
        result["batch_name"] = PRODUCTS[tokens[0].upper()]
        tokens = tokens[1:]
    leftovers = _classify_tokens(tokens, result)
    _assign_concept_and_names(leftovers, tokens, result)


def _parse_pb_legacy(working: str, result: dict) -> None:
    result["convention"] = "pb_legacy"
    m = re.match(r"^(P\d+)[\s_-]+B(\d+)", working, re.IGNORECASE)
    if m:
        result["batch_name"] = m.group(1).upper()
        result["creative_no"] = m.group(2)
        rest = working[m.end():]
    else:
        m2 = re.match(r"^B(\d+)", working, re.IGNORECASE)
        if m2:
            result["creative_no"] = m2.group(1)
            rest = working[m2.end():]
        else:
            rest = working
    # Legacy names write hooks as "Hook 1"/"Hook1" anywhere in the string.
    hm = re.search(r"\bHook[\s_-]?(\d{1,2})\b", rest, re.IGNORECASE)
    if hm:
        result["hook"] = f"H{int(hm.group(1))}"
    # Best-effort on whatever remains, whichever delimiter dominates
    # (space-delimited when neither _ nor - appears).
    if rest.count("_") == 0 and rest.count("-") == 0:
        delim = " "
    else:
        delim = "_" if rest.count("_") >= rest.count("-") else "-"
    tokens = [t for t in rest.split(delim) if t.strip()]
    leftovers = _classify_tokens(tokens, result)
    _assign_concept_and_names(leftovers, tokens, result)


def _detect_family(working: str) -> str:
    if re.match(r"^P\d+[\s_-]+B\d+", working, re.IGNORECASE) or re.match(
        r"^B\d{2,4}\b", working
    ):
        return "pb_legacy"
    if re.match(r"^\d{2,4}[_-]", working):
        return "leading_number"
    first_us = re.split(r"_", working, 1)[0]
    first_dash = re.split(r"-", working, 1)[0]
    if first_us.upper() in PRODUCTS and working.count("_") >= working.count("-"):
        return "product_underscore"
    if first_dash.upper() in PRODUCTS:
        return "product_dash"
    if first_us.upper() in PRODUCTS:
        return "product_underscore"
    return "freeform"


_FAMILY_PARSERS = {
    "product_underscore": _parse_product_underscore,
    "product_dash": _parse_product_dash,
    "leading_number": _parse_leading_number,
    "pb_legacy": _parse_pb_legacy,
    # Family #6 (the reworked convention) slots in here when it ships.
}


def parse_ad_name(name: str) -> dict:
    """Parse a Quasi ad name into the stable dashboard schema.

    Every key is always present (None if not applicable) so downstream
    pivoting code can assume a stable schema.
    """
    result = _empty_result(name if isinstance(name, str) else "")
    if not isinstance(name, str) or not name.strip():
        return result

    working = _clean_suffixes(name)
    working = _strip_version_suffix(working)
    result["dedup_key"] = working

    family = _detect_family(working)
    parser = _FAMILY_PARSERS.get(family)
    if parser:
        parser(working, result)
    else:
        result["convention"] = "unknown"

    # A production style like AIAnimation/VSL/UGC/talking-head implies video
    # when the name carries no explicit format token.
    if result["format"] is None and result["creator_type"]:
        result["format"] = "VID"
    return result


if __name__ == "__main__":
    import json
    import sys

    samples = sys.argv[1:] or [
        "GLOW_727_IT_H3_Graduation_DVW_AIPixarSongAnimation_Kian_Mark+Penny_ProblemAware",
        "COL-785-H1-NN-DVW-AI Animation-Martin_Daniyal",
        "MHI-H1-OBGCNV8Y-Marc-Sally-Video-Net_New-AI_VO_+_B_Roll-PDP-Product_Aware",
        "754_Glow_IT_H4_ExHusbandAndTherapist_DVW_AIPixarSongAnimation_Kian_Mark_ProblemAware",
        "P4_B254_New_Video_ProductAware_MatureGlowSeeker_185S_Martin_Abdul_V2",
        "GLOW_801_H1_IT_Exhusband-Church_DVW_AIPixarSongAnimation_Kian_Mark+Penny_ProblemAware (1)",
    ]
    for s in samples:
        print(f"\n--- {s}")
        print(json.dumps(parse_ad_name(s), indent=2))
