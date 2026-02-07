import os
import logging
import asyncio
import time
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from typing import Dict, Any

# --- MODULE IMPORTS ---
# Bu dosyaların aynı klasörde olduğundan emin ol
from token_analyzer import calculate_supply_score
from whale_engine import calculate_whale_pressure
from dominance_tracker import calculate_dominance_shift
from behavior_verdict import generate_behavior_verdict

# --- CONFIGURATION ---
API_KEY = os.getenv("HELIUS_API_KEY")
RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={API_KEY}"
MAX_RPC_RETRIES = 3

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("SolanaAPI")

# --- LIFESPAN MANAGER ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 API & HTTP Client Starting...")
    app.state.client = httpx.AsyncClient(
        timeout=30.0,
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=100)
    )
    yield
    logger.info("🛑 API Shutting Down...")
    await app.state.client.aclose()

app = FastAPI(title="TheRugScopeBot API", version="2.6", lifespan=lifespan)

# --- HELPER FUNCTIONS ---

async def fetch_price_data(client: httpx.AsyncClient, mint: str) -> Dict:
    """
    DexScreener API üzerinden anlık fiyat verisi çeker.
    """
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        resp = await client.get(url)
        
        if resp.status_code == 200:
            data = resp.json()
            pairs = data.get("pairs", [])
            if pairs:
                # En likit çifti al (Genellikle ilk sıradadır)
                best_pair = pairs[0]
                return {
                    "found": True,
                    "price_usd": float(best_pair.get("priceUsd", 0)),
                    "price_change_1h": float(best_pair.get("priceChange", {}).get("h1", 0)),
                    "volume_1h": float(best_pair.get("volume", {}).get("h1", 0)),
                    "market_cap": float(best_pair.get("fdv", 0)),
                    "liquidity_usd": float(best_pair.get("liquidity", {}).get("usd", 0))
                }
    except Exception as e:
        logger.error(f"DexScreener Error: {e}")
    
    # Hata durumunda veya veri yoksa boş dön
    return {"found": False, "price_usd": 0.0, "price_change_1h": 0.0}

async def fetch_rpc(client: httpx.AsyncClient, method: str, params: list) -> Dict:
    """Güvenli RPC İstek Fonksiyonu"""
    for attempt in range(MAX_RPC_RETRIES):
        try:
            payload = {
                "jsonrpc": "2.0", 
                "id": int(time.time()*1000), 
                "method": method, 
                "params": params
            }
            response = await client.post(RPC_URL, json=payload)
            response.raise_for_status()
            data = response.json()
            
            if "error" in data:
                error_msg = data["error"].get("message", "Unknown RPC Error")
                if attempt < MAX_RPC_RETRIES - 1:
                    await asyncio.sleep(1)
                    continue
                raise ValueError(error_msg)
            
            return data

        except Exception as e:
            wait_time = 0.5 * (2 ** attempt)
            if attempt == MAX_RPC_RETRIES - 1:
                logger.error(f"RPC Failed: {e}")
                return {} 
            await asyncio.sleep(wait_time)
    return {}

def parse_token_amount(info: dict, decimals: int) -> float:
    """
    Hem uiAmount hem de raw amount desteği (%0 hatasını çözen kısım).
    """
    if not info: return 0.0
    if "uiAmount" in info and info["uiAmount"] is not None:
        return float(info["uiAmount"])
    if "amount" in info:
        return float(info["amount"]) / (10 ** decimals)
    return 0.0

# --- MAIN ENDPOINT ---

@app.get("/analyze/{mint}")
async def analyze_token(mint: str):
    if len(mint) < 32 or len(mint) > 44:
        raise HTTPException(status_code=400, detail="Invalid Mint Address")

    client: httpx.AsyncClient = app.state.client
    start_time = time.time()

    try:
        logger.info(f"🔍 Analyzing: {mint}")

        # --- ADIM 1: PARALEL VERİ TOPLAMA ---
        # Fiyat, Güvenlik, Arz ve Holderlar aynı anda çekilir
        price_task = fetch_price_data(client, mint)
        security_task = fetch_rpc(client, "getAccountInfo", [mint, {"encoding": "jsonParsed"}])
        supply_task = fetch_rpc(client, "getTokenSupply", [mint])
        holders_task = fetch_rpc(client, "getTokenLargestAccounts", [mint])

        price_data, security_resp, supply_resp, holders_resp = await asyncio.gather(
            price_task, security_task, supply_task, holders_task
        )

        # --- ADIM 2: VERİ İŞLEME ---
        
        # A. Güvenlik ve Decimals
        sec_val = security_resp.get("result", {}).get("value", {}) or {}
        parsed_info = sec_val.get("data", {}).get("parsed", {}).get("info", {})
        
        decimals = int(parsed_info.get("decimals", 9))
        mint_authority = parsed_info.get("mintAuthority")
        freeze_authority = parsed_info.get("freezeAuthority")

        # B. Toplam Arz (Yedekli Kontrol)
        supply_val = supply_resp.get("result", {}).get("value", {})
        total_supply = parse_token_amount(supply_val, decimals)
        if total_supply == 0: total_supply = 1 # DivisionByZero önlemi

        # C. Holder Yüzdeleri
        accounts = holders_resp.get("result", {}).get("value", [])
        if not accounts:
            raise HTTPException(status_code=404, detail="No Holder Data Found")

        top1_amount = parse_token_amount(accounts[0], decimals)
        top10_amount = sum(parse_token_amount(a, decimals) for a in accounts[:10])
        
        top1_percent = min(100.0, (top1_amount / total_supply) * 100)
        top10_percent = min(100.0, (top10_amount / total_supply) * 100)

        # --- ADIM 3: ANALİZ MOTORLARI ---
        
        # A. Yapısal Skor
        structural_data = calculate_supply_score(
            largest_wallet_percent=top1_percent,
            top10_percent=top10_percent,
            total_holders=len(accounts)
        )
        
        # B. Balina & Bundle Motoru
        whale_data = await calculate_whale_pressure(mint)
        
        # C. Dominance
        dominance_data = calculate_dominance_shift(mint, top1_percent)
        
        # D. Karar Motoru (Fiyat Verisi Dahil)
        verdict = generate_behavior_verdict(
            distribution_status=structural_data["status"],
            whale_data=whale_data,
            dominance_data=dominance_data,
            price_data=price_data # Yeni eklenen parametre
        )

        elapsed = round(time.time() - start_time, 2)
        
        # --- JSON YANIT ---
        return {
            "mint": mint,
            "timestamp": int(time.time()),
            "price_data": price_data,
            "security": {
                "mint_authority": mint_authority,
                "freeze_authority": freeze_authority
            },
            "structural": structural_data,
            "whale_metrics": whale_data,
            "dominance_metrics": dominance_data,
            "verdict": verdict,
            "meta": {
                "execution_time_sec": elapsed
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Analysis Failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)