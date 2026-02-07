import logging
from typing import Dict, Any

logger = logging.getLogger("BehaviorVerdict")

def generate_behavior_verdict(
    distribution_status: str,
    whale_data: Dict[str, Any],
    dominance_data: Dict[str, Any],
    price_data: Dict[str, Any] = {} 
) -> Dict[str, Any]:
    """
    Risk Skorunu Hesaplar. 
    Fiyat hareketi ile Balina hareketini karşılaştırır.
    """
    
    risk_score = 0.0
    verdict_desc = []
    
    # --- 1. YAPISAL RİSK (Max 40 Puan) ---
    if distribution_status == "Critical Centralization":
        risk_score += 40
        verdict_desc.append("Extreme centralization.")
    elif distribution_status == "High Concentration":
        risk_score += 30
        verdict_desc.append("High holder concentration.")
    elif distribution_status == "Moderate Concentration":
        risk_score += 15
    
    # --- 2. BALİNA & BUNDLE RİSKİ (Max 50 Puan) ---
    pressure = whale_data.get("pressure", "Neutral")
    bundle_detected = whale_data.get("bundle_detected", False)
    
    # Bundle varsa direkt yüksek risk
    if bundle_detected:
        risk_score += 50
        verdict_desc.append("🚨 INSIDER BUNDLE DETECTED.")
    
    # Satış baskısı
    if pressure == "Strong Distribution":
        risk_score += 25
        verdict_desc.append("Whales dumping.")
    elif pressure == "Distribution":
        risk_score += 15

    # --- 3. DOMINANCE RİSKİ (Max 20 Puan) ---
    slope = dominance_data.get("slope", 0.0)
    if slope > 0.5:
        risk_score += 20
        verdict_desc.append("Top holder accumulating fast.")

    # --- 4. FİYAT KORELASYONU (NEDENSELLİK) ---
    # Fiyatı kim hareket ettiriyor?
    
    price_change = price_data.get("price_change_1h", 0.0)
    whale_flow = whale_data.get("net_flow_percent_supply", 0.0)
    
    correlation_verdict = "Neutral / Low Volatility"

    if price_change > 2.0: # Fiyat Yükseliyor
        if whale_flow > 0.1:
            correlation_verdict = "🐳 Whale Driven Pump"
            # Balina destekli yükseliş (Normal risk)
        elif whale_flow < -0.1:
            correlation_verdict = "⚠️ Divergence: Whales Selling into Pump"
            # Fiyat artarken balina satıyor -> ÇOK RİSKLİ (Tuzak)
            risk_score += 25
            verdict_desc.append("Exit liquidity trap detected.")
        else:
            correlation_verdict = "👥 Organic/Retail Rally"
            
    elif price_change < -2.0: # Fiyat Düşüyor
        if whale_flow < -0.1:
            correlation_verdict = "📉 Whale Driven Dump"
            # Balina satıyor, fiyat düşüyor
            risk_score += 10
        elif whale_flow > 0.1:
            correlation_verdict = "🧠 Whales Absorbing the Dip"
            # Fiyat düşerken balina topluyor -> İYİ SİNYAL
            risk_score -= 15 # Riski düşür
            verdict_desc.append("Smart money buying the dip.")
        else:
            correlation_verdict = "😨 Retail Panic Sell"

    # --- SKORLAMA VE ETİKET ---
    risk_score = min(100.0, max(0.0, risk_score))
    
    if risk_score >= 80:
        label = "⛔ HIGH RUG RISK"
        intensity = "Critical"
    elif risk_score >= 50:
        label = "🟠 CAUTION ADVISED"
        intensity = "High"
    elif risk_score >= 25:
        label = "🟡 MODERATE RISK"
        intensity = "Medium"
    else:
        label = "🟢 STABLE / HEALTHY"
        intensity = "Low"

    full_desc = " ".join(verdict_desc) if verdict_desc else "No major anomalies detected."

    return {
        "risk_score": round(risk_score, 2),
        "risk_intensity": intensity,
        "verdict_label": label,
        "verdict_description": full_desc,
        "correlation_verdict": correlation_verdict
    }