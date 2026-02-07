import math
import logging
from typing import Dict, Any

logger = logging.getLogger("TokenAnalyzer")

def calculate_supply_score(
    largest_wallet_percent: float, 
    top10_percent: float,
    total_holders: int = 0
) -> Dict[str, Any]:
    """
    Gelişmiş Arz Puanlama Motoru.
    Kural: Top 10 cüzdan %20'den fazlaysa puan düşer (Orta Risk).
    """
    
    # Başlangıç Puanı
    score = 100.0

    # 1. TOP 1 CÜZDAN CEZALARI (Tekel Riski)
    if largest_wallet_percent > 40: score -= 60
    elif largest_wallet_percent > 20: score -= 40
    elif largest_wallet_percent > 10: score -= 20
    elif largest_wallet_percent > 5: score -= 10

    # 2. TOP 10 CÜZDAN CEZALARI (Yoğunlaşma Riski)
    if top10_percent > 60: score -= 50       # Kritik
    elif top10_percent > 50: score -= 40     # Yüksek
    elif top10_percent > 35: score -= 30     # Ciddi
    elif top10_percent >= 20: score -= 20    # Orta Risk (İsteğin üzerine eklendi)

    # 3. HOLDER SAYISI CEZASI
    if total_holders > 0:
        if total_holders < 100: score -= 20
        elif total_holders < 500: score -= 10
        elif total_holders > 2000: score += 5

    # 4. HHI Proxy (Matematiksel Yoğunlaşma)
    remaining = max(0, top10_percent - largest_wallet_percent)
    avg_rem = remaining / 9.0 if remaining > 0 else 0
    hhi = (largest_wallet_percent ** 2) + (9 * (avg_rem ** 2))

    # 5. Normalizasyon ve Statü
    score = max(0.0, min(100.0, score))

    if score >= 85: status = "Healthy Distribution"
    elif score >= 65: status = "Moderate Concentration" # Top 10 > %20 buraya düşer
    elif score >= 40: status = "High Concentration"
    else: status = "Critical Centralization"

    return {
        "score": round(score, 2),
        "status": status,
        "metrics": {
            "top1_percent": round(largest_wallet_percent, 2),
            "top10_percent": round(top10_percent, 2),
            "gini_proxy": round(largest_wallet_percent / 100.0, 4), 
            "hhi_estimate": round(hhi, 2)
        }
    }