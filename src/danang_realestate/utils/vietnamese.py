import re
import unicodedata

DISTRICTS = {
    "hải châu": "Hải Châu",
    "thanh khê": "Thanh Khê",
    "sơn trà": "Sơn Trà",
    "ngũ hành sơn": "Ngũ Hành Sơn",
    "liên chiểu": "Liên Chiểu",
    "cẩm lệ": "Cẩm Lệ",
    "hòa vang": "Hòa Vang",
}

def remove_diacritics(text: str) -> str:
    """Remove diacritics from Vietnamese string."""
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    # Remove combined diacritical marks
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    # Replace Vietnamese custom characters
    text = text.replace("đ", "d").replace("Đ", "D")
    return text

def normalize_text(text: str | None) -> str:
    """Normalize whitespace and lowercase a string."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text.strip())
    text = re.sub(r"\s+", " ", text)
    return text

def clean_district(district_raw: str | None) -> str | None:
    """Extract and normalize district name from raw text for Da Nang."""
    if not district_raw:
        return None
    
    val = normalize_text(district_raw).lower()
    
    # Strip common prefixes
    val = re.sub(r"^(quận|huyện|q\.|h\.)\s*", "", val)
    val = val.strip()
    
    # Check direct match
    if val in DISTRICTS:
        return DISTRICTS[val]
    
    # Check no-diacritics match
    val_nodiacritics = remove_diacritics(val)
    for k, v in DISTRICTS.items():
        k_nodiacritics = remove_diacritics(k)
        if val_nodiacritics == k_nodiacritics:
            return v
            
    # Substring check
    for k, v in DISTRICTS.items():
        if k in val or remove_diacritics(k) in val_nodiacritics:
            return v
            
    return None
