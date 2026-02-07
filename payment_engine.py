import os
import json
import time
import asyncio
import logging
import httpx
from datetime import datetime
from typing import Dict, Any, Optional, List, Set

# --- MODULE IMPORTS ---
# Session manager ile ödeyen kişiyi eşleştirmek için gerekli
from payment_session_manager import session_manager

# --- CONFIGURATION ---
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Cüzdan ve Token Sabitleri
MASTER_WALLET = "GkbSGWwSuiYDddMpM72NVQWFgLny3W1Yh3WxwoA3kY8D"
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

# Kabul Edilen Tokenlar (Whitelist)
ACCEPTED_MINTS = {
    "USDT": "Es9vMFrzaCERmJfrGv2kRkGq5BPdZiZsaAJ2bX7wY8L",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
}
ACCEPTED_MINT_VALUES = list(ACCEPTED_MINTS.values())

# Fiyatlandırma ve Tolerans
TARGET_PRICE = 4.99
MIN_ACCEPT_AMOUNT = 4.90  # Fee/Slippage toleransı
PREMIUM_DURATION_SECONDS = 30 * 24 * 60 * 60  # 30 Gün

# Persistence (Kalıcılık) Dosyaları
TX_FILE = "processed_transactions.json"
PREMIUM_FILE = "premium_users.json"

# --- LOGGING SETUP ---
logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("PaymentEngine")

class PaymentEngine:
    def __init__(self):
        self._ensure_files()
        self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
        # RPC çağrıları için tek bir client, timeout süresi uzun tutuldu
        self.http_client = httpx.AsyncClient(timeout=30.0)

    def _ensure_files(self):
        """Gerekli JSON dosyalarının varlığını garanti eder."""
        if not os.path.exists(TX_FILE):
            self._atomic_write(TX_FILE, [])
        if not os.path.exists(PREMIUM_FILE):
            self._atomic_write(PREMIUM_FILE, {})

    def _load_json(self, filename: str) -> Any:
        """Güvenli JSON okuma."""
        try:
            with open(filename, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return [] if filename == TX_FILE else {}

    def _atomic_write(self, filename: str, data: Any):
        """Windows uyumlu atomik yazma işlemi (Veri kaybını önler)."""
        temp_file = f"{filename}.tmp"
        try:
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=4)
            os.replace(temp_file, filename)
        except OSError as e:
            logger.error(f"Critical IO Error ({filename}): {e}")

    def _is_processed(self, signature: str) -> bool:
        """İşlem daha önce işlendi mi?"""
        txs = self._load_json(TX_FILE)
        return signature in txs

    def _mark_processed(self, signature: str):
        """İşlemi işlendi olarak işaretle ve listeyi temiz tut."""
        txs = self._load_json(TX_FILE)
        if signature not in txs:
            txs.append(signature)
            # Dosya şişmesin diye son 2000 işlemi tutuyoruz
            if len(txs) > 2000:
                txs = txs[-2000:]
            self._atomic_write(TX_FILE, txs)

    async def _rpc_call(self, method: str, params: list) -> Dict:
        """Helius RPC Çağrısı (Retry Mekanizmalı)."""
        payload = {
            "jsonrpc": "2.0", 
            "id": int(time.time()), 
            "method": method, 
            "params": params
        }
        
        for attempt in range(3):
            try:
                response = await self.http_client.post(self.rpc_url, json=payload)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                if attempt == 2:
                    logger.error(f"RPC Fail [{method}]: {e}")
                await asyncio.sleep(1 * (attempt + 1))
        return {}

    async def _notify_user(self, user_id: int, expiry_ts: int, currency: str):
        """Kullanıcıya Telegram üzerinden başarı mesajı gönderir."""
        if not BOT_TOKEN: return
        
        date_str = datetime.fromtimestamp(expiry_ts).strftime('%Y-%m-%d')
        text = (
            f"✅ **Payment Confirmed!**\n\n"
            f"Received: **{TARGET_PRICE} {currency}**\n"
            f"Status: **Premium Active**\n"
            f"Valid Until: `{date_str}`\n\n"
            f"Thank you for choosing TheRugScopeBot."
        )
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        try:
            await self.http_client.post(url, json={
                "chat_id": user_id, 
                "text": text, 
                "parse_mode": "Markdown"
            })
            logger.info(f"Notification sent to User {user_id}")
        except Exception as e:
            logger.error(f"Failed to notify user {user_id}: {e}")

    def _activate_premium_direct(self, user_id: int) -> int:
        """
        Premium veritabanını günceller.
        Bağımsız çalışabilmesi için user_manager import etmez, direkt dosyaya yazar.
        """
        data = self._load_json(PREMIUM_FILE)
        uid = str(user_id)
        now = int(time.time())
        
        user_data = data.get(uid, {"active": False, "expires_at": 0})
        current_expiry = user_data.get("expires_at", 0)
        
        # Eğer zaten aktifse süreyi uzat, değilse şimdiden başlat
        base_time = max(now, current_expiry) if user_data.get("active") else now
        new_expiry = base_time + PREMIUM_DURATION_SECONDS

        data[uid] = {"active": True, "expires_at": new_expiry}
        self._atomic_write(PREMIUM_FILE, data)
        return new_expiry

    async def _get_monitoring_addresses(self) -> List[str]:
        """
        Master Wallet'ı ve ona ait USDT/USDC ATA adreslerini bulur.
        Bu sayede token transferlerini kaçırmayız.
        """
        addresses = [MASTER_WALLET]
        
        resp = await self._rpc_call("getTokenAccountsByOwner", [
            MASTER_WALLET,
            {"programId": TOKEN_PROGRAM_ID},
            {"encoding": "jsonParsed"}
        ])
        
        if "result" in resp and "value" in resp["result"]:
            for account in resp["result"]["value"]:
                info = account.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
                mint = info.get("mint")
                
                if mint in ACCEPTED_MINT_VALUES:
                    pubkey = account.get("pubkey")
                    if pubkey:
                        addresses.append(pubkey)
        
        # Tekrar edenleri temizle
        return list(set(addresses))

    async def verify_transaction(self, signature: str) -> Optional[int]:
        """
        Bir işlemi analiz eder ve geçerli bir ödeme olup olmadığına karar verir.
        Logic:
        1. İşlem başarılı mı? (err is None)
        2. Master Wallet (veya ATA'sı) para aldı mı?
        3. Kim para gönderdi? (Balance Delta Check)
        4. Gönderen kişinin açık bir oturumu var mı?
        """
        resp = await self._rpc_call("getTransaction", [
            signature, 
            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "finalized"}
        ])

        if not resp.get("result"): return None
        tx_data = resp["result"]
        meta = tx_data.get("meta")
        
        if not meta or meta.get("err"): return None # Hatalı işlemi atla

        # --- ADIM 1: Master Wallet'a Gelen Parayı Tespit Et ---
        transaction = tx_data.get("transaction", {})
        message = transaction.get("message", {})
        account_keys = message.get("accountKeys", [])
        
        # Master Wallet'a ait olan hesapları (ATA) bul
        master_atas_in_tx = {} # {ATA_Address: Mint}
        
        for bal in meta.get("postTokenBalances", []):
            if bal["owner"] == MASTER_WALLET and bal["mint"] in ACCEPTED_MINT_VALUES:
                idx = bal["accountIndex"]
                # Adresi çöz
                if isinstance(account_keys[0], dict):
                    addr = account_keys[idx]["pubkey"]
                else:
                    addr = account_keys[idx]
                master_atas_in_tx[addr] = bal["mint"]

        if not master_atas_in_tx: return None

        # --- ADIM 2: Transfer Instruction Kontrolü ---
        # Bu adım, bakiyenin gerçekten bir transferden geldiğini doğrular.
        valid_amount = 0.0
        detected_mint = None
        
        # Tüm instructionları düzleştir (Inner dahil)
        all_ixs = message.get("instructions", []) + [
            ix for inner in meta.get("innerInstructions", []) 
            for ix in inner.get("instructions", [])
        ]

        for ix in all_ixs:
            if ix.get("program") not in ["spl-token", "spl-token-2022"]: continue
            parsed = ix.get("parsed")
            if not isinstance(parsed, dict): continue
            
            type_ = parsed.get("type")
            if type_ not in ["transfer", "transferChecked"]: continue
            
            info = parsed.get("info", {})
            dest = info.get("destination")
            
            # Hedef bizim cüzdanlardan biri mi?
            if dest not in master_atas_in_tx: continue
            
            current_mint = master_atas_in_tx[dest]
            
            # Miktar hesapla
            amt = 0.0
            if type_ == "transfer":
                amt = int(info.get("amount", "0")) / 1_000_000.0
            else:
                amt = float(info.get("tokenAmount", {}).get("uiAmount", 0.0))
            
            valid_amount += amt
            detected_mint = current_mint

        if valid_amount < MIN_ACCEPT_AMOUNT: return None

        # --- ADIM 3: Ödeyen Kişiyi Bul (Balance Delta Analysis) ---
        # "Kimin cebinden para çıktı?" sorusunun cevabı. En güvenli yöntem.
        payer_address = None
        
        # Pre-Balances'da olup bakiyesi azalan kişiyi bul
        for pre in meta.get("preTokenBalances", []):
            if pre["mint"] != detected_mint: continue
            if pre["owner"] == MASTER_WALLET: continue # Kendimiz olamayız
            
            # Post balance'ı bul
            post_amt = 0.0
            for post in meta.get("postTokenBalances", []):
                if post["accountIndex"] == pre["accountIndex"]:
                    post_amt = float(post["uiTokenAmount"]["uiAmount"] or 0)
                    break
            
            pre_amt = float(pre["uiTokenAmount"]["uiAmount"] or 0)
            delta = pre_amt - post_amt
            
            # Eğer eksilen miktar, ödenen miktara yakınsa (tolerans dahil) ödeyen budur.
            if delta >= MIN_ACCEPT_AMOUNT:
                payer_address = pre["owner"]
                break

        if not payer_address:
            logger.warning(f"Valid amount received but Payer not identified in {signature}")
            return None

        # --- ADIM 4: Oturum Eşleştirme ve Aktivasyon ---
        currency = "USDT" if "Es9v" in detected_mint else "USDC"
        logger.info(f"💰 Detected {valid_amount} {currency} from {payer_address}")

        session = session_manager.get_valid_session(payer_address)
        
        if not session:
            logger.info(f"No active session for {payer_address}. Ignoring.")
            return None

        user_id = session["user_id"]
        logger.info(f"✅ MATCH! Activating Premium for User {user_id}")
        
        expiry = self._activate_premium_direct(user_id)
        session_manager.complete_session(payer_address)
        await self._notify_user(user_id, expiry, currency)
        
        return user_id

    async def run_forever(self):
        """Sürekli çalışan ana döngü."""
        logger.info("🚀 Payment Engine Started. Monitoring Blockchain...")
        
        while True:
            try:
                # 1. Dinlenecek adresleri güncelle (ATA'lar değişebilir)
                targets = await self._get_monitoring_addresses()
                
                unique_signatures = set()

                # 2. Her adres için son işlemleri çek
                for address in targets:
                    resp = await self._rpc_call("getSignaturesForAddress", [
                        address, 
                        {"limit": 10, "commitment": "finalized"}
                    ])
                    
                    if "result" in resp:
                        for item in resp["result"]:
                            unique_signatures.add(item["signature"])
                    
                    # Rate limit koruması
                    await asyncio.sleep(0.5)

                # 3. İşlemleri Analiz Et
                for sig in unique_signatures:
                    if self._is_processed(sig): continue
                    
                    await self.verify_transaction(sig)
                    self._mark_processed(sig)
                    
                    await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"Polling Loop Error: {e}")
            
            # 30 saniye bekle
            await asyncio.sleep(30)

if __name__ == "__main__":
    if not HELIUS_API_KEY:
        print("❌ CRITICAL: HELIUS_API_KEY missing.")
        exit(1)
    
    engine = PaymentEngine()
    try:
        asyncio.run(engine.run_forever())
    except KeyboardInterrupt:
        print("🛑 Payment Engine Stopped.")