import os
import asyncio
import logging
import httpx
import time
from collections import Counter
from typing import Dict, List, Optional

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("WhaleEngine")

# --- CONFIG ---
API_KEY = os.getenv("HELIUS_API_KEY")
RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={API_KEY}"
MAX_RETRIES = 3
CONCURRENCY_LIMIT = 5  # Paralel işlem limiti

class WhaleEngine:
    def __init__(self):
        self.semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

    async def _rpc_call(self, client: httpx.AsyncClient, method: str, params: list) -> Dict:
        """Güvenli RPC Çağrısı"""
        for attempt in range(MAX_RETRIES):
            try:
                payload = {
                    "jsonrpc": "2.0", "id": int(time.time()*1000), 
                    "method": method, "params": params
                }
                resp = await client.post(RPC_URL, json=payload, timeout=10.0)
                resp.raise_for_status()
                data = resp.json()
                if "error" in data:
                    # Rate limit yönetimi
                    if "429" in str(data):
                        await asyncio.sleep(1 + attempt)
                        continue
                    return {}
                return data
            except Exception:
                await asyncio.sleep(0.5 * (2 ** attempt))
        return {}

    async def _analyze_wallet_flow(self, client: httpx.AsyncClient, wallet: str, mint: str) -> float:
        """Cüzdanın token üzerindeki net alım/satım hareketini ölçer."""
        async with self.semaphore:
            # Son 15 işlemi çek
            sig_resp = await self._rpc_call(client, "getSignaturesForAddress", [
                wallet, {"limit": 15, "commitment": "finalized"}
            ])
            signatures = [s["signature"] for s in sig_resp.get("result", [])]
            if not signatures: return 0.0

            net_change = 0.0
            # Sadece son 5 işlemi detaylı incele (Hız için)
            for sig in signatures[:5]:
                tx_resp = await self._rpc_call(client, "getTransaction", [
                    sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
                ])
                result = tx_resp.get("result")
                if not result or result.get("meta", {}).get("err"): continue
                
                meta = result["meta"]
                pre = next((float(b["uiTokenAmount"]["uiAmount"] or 0) for b in meta.get("preTokenBalances", []) if b["owner"] == wallet and b["mint"] == mint), 0.0)
                post = next((float(b["uiTokenAmount"]["uiAmount"] or 0) for b in meta.get("postTokenBalances", []) if b["owner"] == wallet and b["mint"] == mint), 0.0)
                
                net_change += (post - pre)
            
            return net_change

    async def _find_funding_source(self, client: httpx.AsyncClient, wallet: str) -> Optional[str]:
        """
        Cüzdanın ilk fonlayıcısını (Baba Cüzdan) bulur.
        Bu özellik BUNDLE tespiti için kritiktir.
        """
        async with self.semaphore:
            # 1. En eski işlemi bulmak için sondan başla (Limitli)
            # Eğer cüzdanın 1000'den fazla işlemi varsa "Eski/Güvenli" kabul et.
            # Biz sadece "Fresh" cüzdanların (Sniper/Bundle) peşindeyiz.
            sig_resp = await self._rpc_call(client, "getSignaturesForAddress", [
                wallet, {"limit": 100} 
            ])
            signatures = sig_resp.get("result", [])
            
            if not signatures: return None
            
            # Eğer 100 işlemden azsa, en sondaki işlem "Yaratılış" işlemidir.
            # Eğer 100 ise, muhtemelen eski bir cüzdandır, "Exchange" veya "User" der geçeriz.
            if len(signatures) == 100:
                return "Established_User"

            creation_tx_sig = signatures[-1]["signature"]
            
            # 2. İşlemi detaylandır
            tx_resp = await self._rpc_call(client, "getTransaction", [
                creation_tx_sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
            ])
            
            result = tx_resp.get("result")
            if not result: return None
            
            # 3. Parayı kim gönderdi? (Signer 0 genellikle ödeyendir)
            try:
                accounts = result["transaction"]["message"]["accountKeys"]
                # Parsed formatta accountKeys bir liste veya dict olabilir, yapıya göre:
                signer = next((acc["pubkey"] for acc in accounts if acc["signer"]), None)
                
                # Eğer signer cüzdanın kendisiyse (nadir), başka kaynaktan gelmiştir.
                # Genellikle creation tx'de fee ödeyen "Funder"dır.
                return signer if signer != wallet else "Self_Funded"
            except:
                return None

    async def calculate_whale_pressure(self, mint: str) -> Dict:
        """
        ANA FONKSİYON: Hem Balina Baskısını hem de BUNDLE (Küme) analizini yapar.
        """
        start_time = time.time()
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Toplam Arz ve Holderlar
            supply_task = self._rpc_call(client, "getTokenSupply", [mint])
            holders_task = self._rpc_call(client, "getTokenLargestAccounts", [mint])
            
            supply_resp, holders_resp = await asyncio.gather(supply_task, holders_task)
            
            supply_val = supply_resp.get("result", {}).get("value", {})
            total_supply = float(supply_val.get("uiAmount") or 1)
            
            accounts = holders_resp.get("result", {}).get("value", [])
            if not accounts: return {"pressure": "Neutral", "bundle_detected": False}

            # Top 7 Cüzdanı Analiz Et (Hız/Performans dengesi için 7 iyi)
            top_wallets = [acc["address"] for acc in accounts[:7]]
            
            # --- PARALEL GÖREVLER ---
            # 1. Flow Analizi (Alıyor mu Satıyor mu?)
            flow_tasks = [self._analyze_wallet_flow(client, w, mint) for w in top_wallets]
            
            # 2. Funding Analizi (Bundle Kontrolü) - KILLER FEATURE
            fund_tasks = [self._find_funding_source(client, w) for w in top_wallets]
            
            results = await asyncio.gather(*flow_tasks, *fund_tasks)
            
            # Sonuçları Ayır
            flows = results[:len(top_wallets)]
            funders = results[len(top_wallets):]
            
            # --- VERİ ANALİZİ ---
            
            # A. Balina Baskısı
            net_flow = sum(f for f in flows if isinstance(f, float))
            flow_percent = (net_flow / total_supply) * 100
            
            pressure = "Neutral"
            if flow_percent > 0.5: pressure = "Strong Accumulation"
            elif flow_percent < -0.5: pressure = "Strong Distribution"

            # B. BUNDLE (Küme) Tespiti
            # Funders listesindeki tekrarları say (None ve Established hariç)
            suspect_funders = [f for f in funders if f and f not in ["Established_User", "Self_Funded"]]
            funder_counts = Counter(suspect_funders)
            
            # En çok tekrar eden funder (Baba Cüzdan)
            bundle_alert = False
            bundle_size = 0
            main_funder = "None"
            
            if funder_counts:
                most_common = funder_counts.most_common(1)[0] # (Adres, Sayı)
                main_funder = most_common[0]
                bundle_size = most_common[1]
                
                # Eğer 2 veya daha fazla cüzdan aynı kaynaktan geldiyse BUNDLE var!
                if bundle_size >= 2:
                    bundle_alert = True

            return {
                "pressure": pressure,
                "net_flow_percent_supply": round(flow_percent, 4),
                
                # YENİ ÖZELLİKLER
                "bundle_detected": bundle_alert,
                "bundle_size": bundle_size,
                "main_funder": main_funder, # Baba Cüzdan Adresi
                "scanned_wallets": len(top_wallets),
                
                "execution_time": round(time.time() - start_time, 2)
            }

# Singleton
calculate_whale_pressure = WhaleEngine().calculate_whale_pressure