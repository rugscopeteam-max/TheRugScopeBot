import json
import os
import time
import logging
from contextlib import contextmanager
from typing import Dict, Any, Optional

# --- CONFIGURATION ---
SESSIONS_FILE = "payment_sessions.json"
USED_WALLETS_FILE = "used_wallets.json"
SESSION_TIMEOUT = 1800  # 30 Dakika (Saniye cinsinden)
LOCK_FILE = "session.lock"

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("SessionManager")

class PaymentSessionManager:
    def __init__(self):
        """Başlangıçta gerekli dosyaların varlığını kontrol eder."""
        if not os.path.exists(SESSIONS_FILE):
            self._save(SESSIONS_FILE, {})
        if not os.path.exists(USED_WALLETS_FILE):
            self._save(USED_WALLETS_FILE, [])

    @contextmanager
    def _lock(self):
        """
        Basit dosya tabanlı Spinlock.
        Aynı anda birden fazla işlemin JSON dosyasına yazmasını engeller.
        """
        timeout = 5.0
        start_time = time.time()
        
        while os.path.exists(LOCK_FILE):
            # Deadlock koruması: Kilit dosyası çok eskiyse (örn. crash durumunda) sil
            if time.time() - os.path.getmtime(LOCK_FILE) > 5:
                try:
                    os.remove(LOCK_FILE)
                    logger.warning("Stale lock file removed.")
                except OSError:
                    pass
            
            if time.time() - start_time > timeout:
                logger.error("Lock timeout reached!")
                break
                
            time.sleep(0.1)

        # Kilidi al
        try:
            with open(LOCK_FILE, 'w') as f:
                f.write("1")
            yield
        finally:
            # Kilidi bırak
            if os.path.exists(LOCK_FILE):
                try:
                    os.remove(LOCK_FILE)
                except OSError:
                    pass

    def _load(self, fname: str) -> Any:
        """Güvenli JSON okuma."""
        try:
            with open(fname, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {} if fname == SESSIONS_FILE else []

    def _save(self, fname: str, data: Any):
        """Atomik yazma işlemi (Temp file -> Rename)."""
        tmp = f"{fname}.tmp"
        try:
            with open(tmp, 'w') as f:
                json.dump(data, f, indent=4)
            os.replace(tmp, fname)
        except OSError as e:
            logger.error(f"Failed to save {fname}: {e}")

    def is_wallet_used(self, wallet: str) -> bool:
        """Bu cüzdan daha önce premium almış mı?"""
        # Okuma işlemi için kilide gerek yok (performans için)
        # Ancak çok yüksek trafikli sistemlerde okuma kilidi de düşünülebilir.
        used_wallets = self._load(USED_WALLETS_FILE)
        return wallet in used_wallets

    def create_session(self, user_id: int, wallet: str) -> Dict:
        """Yeni bir ödeme oturumu başlatır."""
        with self._lock():
            sessions = self._load(SESSIONS_FILE)
            
            # Eski oturumları temizle (Aynı kullanıcı veya aynı cüzdan)
            clean_sessions = {
                k: v for k, v in sessions.items() 
                if v["user_id"] != user_id and k != wallet
            }
            
            now = int(time.time())
            session = {
                "user_id": user_id,
                "payer_wallet": wallet,
                "created_at": now,
                "expires_at": now + SESSION_TIMEOUT,
                "status": "pending"
            }
            
            # Cüzdan adresi anahtar olarak kullanılır (Hızlı erişim için)
            clean_sessions[wallet] = session
            self._save(SESSIONS_FILE, clean_sessions)
            
            return session

    def get_valid_session(self, wallet: str) -> Optional[Dict]:
        """
        Cüzdan adresiyle eşleşen geçerli (süresi dolmamış) bir oturum döner.
        Süresi dolmuşsa siler.
        """
        with self._lock():
            sessions = self._load(SESSIONS_FILE)
            session = sessions.get(wallet)
            
            if not session:
                return None
            
            # Süre kontrolü
            if int(time.time()) > session["expires_at"]:
                del sessions[wallet]
                self._save(SESSIONS_FILE, sessions)
                return None
            
            return session

    def complete_session(self, wallet: str):
        """
        Ödeme başarıyla tamamlandığında çağrılır.
        Oturumu siler ve cüzdanı 'kullanılmış' listesine ekler.
        """
        with self._lock():
            # 1. Oturumu Sil
            sessions = self._load(SESSIONS_FILE)
            if wallet in sessions:
                del sessions[wallet]
                self._save(SESSIONS_FILE, sessions)
            
            # 2. Cüzdanı Kara Listeye Al (Reuse Prevention)
            used = self._load(USED_WALLETS_FILE)
            if wallet not in used:
                used.append(wallet)
                self._save(USED_WALLETS_FILE, used)

# Singleton Instance
session_manager = PaymentSessionManager()