"""Sector classification and bounded sector-relative rating adjustment."""

import re


# Names match the NSE index names in Angel One's instrument master.
_INDEXES = {
    "bank": "Nifty Bank",
    "financial services": "Nifty Fin Service",
    "information technology": "Nifty IT",
    "technology": "Nifty IT",
    "healthcare": "Nifty Pharma",
    "pharma": "Nifty Pharma",
    "consumer defensive": "Nifty FMCG",
    "energy": "Nifty Energy",
    "real estate": "Nifty Realty",
    "auto": "Nifty Auto",
    "consumer cyclical": "Nifty Consumption",
    "metal": "Nifty Metal",
    "media": "Nifty Media",
    "telecom": "Nifty Serv Sector",
    "infrastructure": "Nifty Infra",
}


def sector_index_for(company_sector, company_industry):
    """Choose the closest available NSE sector index; unknowns stay unmapped."""
    sector = str(company_sector or "").strip().lower()
    industry = str(company_industry or "").strip().lower()
    text = f"{sector} {industry}"

    # Prefer a specific industry match over Yahoo's broader sector label.
    if any(word in text for word in ("bank", "credit services")):
        return "Nifty Bank" if "bank" in industry else "Nifty Fin Service"
    if any(word in text for word in ("insurance", "capital markets", "financial data", "credit")):
        return "Nifty Fin Service"
    if any(word in text for word in ("software", "it services", "information technology")):
        return "Nifty IT"
    if any(word in text for word in ("pharmaceutical", "drug manufacturer", "biotechnology")):
        return "Nifty Pharma"
    if any(word in text for word in ("automobile", "auto manufacturer", "auto parts", "auto & truck")):
        return "Nifty Auto"
    if any(word in text for word in ("metal", "steel", "aluminum", "copper", "mining")):
        return "Nifty Metal"
    if any(word in text for word in ("real estate", "reit")):
        return "Nifty Realty"
    if any(word in text for word in ("telecom", "wireless", "communication services")):
        return "Nifty Serv Sector"
    if any(word in text for word in ("media", "entertainment", "broadcasting")):
        return "Nifty Media"
    if any(word in text for word in ("fmcg", "household products", "packaged foods", "beverages")):
        return "Nifty FMCG"
    if any(word in text for word in ("construction", "engineering", "infrastructure")):
        return "Nifty Infra"
    if any(word in text for word in ("oil & gas", "oil and gas", "energy", "utilities")):
        return "Nifty Energy"

    for label, index_name in _INDEXES.items():
        if sector == label:
            return index_name
    return None


def normalize_index_symbol(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def sector_rating_adjustment(
    sector_day_change_pct=None,
    sector_5d_change_pct=None,
    nifty_day_change_pct=None,
    nifty_5d_change_pct=None,
):
    """Return (bounded rating adjustment, sector-vs-Nifty five-session move)."""
    adjustment = 0
    relative_5d = None

    try:
        if sector_5d_change_pct is not None and nifty_5d_change_pct is not None:
            relative_5d = float(sector_5d_change_pct) - float(nifty_5d_change_pct)
            if relative_5d >= 1.5:
                adjustment += 4
            elif relative_5d >= 0.5:
                adjustment += 2
            elif relative_5d <= -1.5:
                adjustment -= 4
            elif relative_5d <= -0.5:
                adjustment -= 2
    except (TypeError, ValueError):
        relative_5d = None

    try:
        if sector_day_change_pct is not None and nifty_day_change_pct is not None:
            relative_day = float(sector_day_change_pct) - float(nifty_day_change_pct)
            if relative_day >= 0.5:
                adjustment += 1
            elif relative_day <= -0.5:
                adjustment -= 1
    except (TypeError, ValueError):
        pass

    return max(-5, min(5, adjustment)), relative_5d
