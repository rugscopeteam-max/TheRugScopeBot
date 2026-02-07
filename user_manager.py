import json
import os
import time
import logging
from typing import Dict, Any, List

# --- CONFIGURATION ---
USAGE_FILE = "usage_data.json"
PREMIUM_FILE = "premium_users.json"
DAILY_LIMIT = 5
RESET_PERIOD = 86400  # 24 Saat

# --- LOGGING ---
logger = logging.getLogger("UserManager")

class UserManager:
    def __init__(self):
        self._ensure_files()
        # In-Memory Cache (Performans için)
        self.usage_cache = self._load_json(USAGE_FILE)
        self.premium_cache = self._load_json(PREMIUM_FILE)

    def _ensure_files(self):
        """Dosyalar yoksa oluşturur."""
        if not os.path.exists(USAGE_FILE):
            self._atomic_write(USAGE_FILE, {})
        if not os.path.exists(PREMIUM_FILE):
            self._atomic_write(PREMIUM_FILE, {})

    def _atomic_write(self, filename: str, data: Any):
        """Veri kaybını önleyen güvenli yazma işlemi."""
        temp_file = f"{filename}.tmp"
        try:
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=4)
            os.replace(temp_file, filename)
        except OSError as e:
            logger.error(f"Failed to save {filename}: {e}")

    def _load_json(self, filename: str) -> Dict:
        """JSON dosyasını güvenli okur."""
        try:
            with open(filename, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _sync_usage(self):
        """Usage verilerini diske yazar."""
        self._atomic_write(USAGE_FILE, self.usage_cache)

    def _sync_premium(self):
        """Premium verilerini diske yazar."""
        self._atomic_write(PREMIUM_FILE, self.premium_cache)

    def check_status(self, user_id: int, admin_ids: List[int]) -> Dict[str, Any]:
        """
        Kullanıcının yetkisini ve limitlerini kontrol eder.
        Öncelik: Admin > Premium > Free
        """
        uid = str(user_id)
        
        # 1. ADMIN CHECK
        if user_id in admin_ids:
            return {
                "allowed": True, 
                "type": "Admin", 
                "usage": 0, 
                "limit": "Unlimited"
            }

        # 2. PREMIUM CHECK (Cache üzerinden)
        if uid in self.premium_cache:
            user_prem = self.premium_cache[uid]
            if user_prem.get("active", False):
                expires_at = user_prem.get("expires_at", 0)
                if expires_at > time.time():
                    return {
                        "allowed": True, 
                        "type": "Premium", 
                        "usage": 0, 
                        "limit": "Unlimited"
                    }
                else:
                    # Süresi dolmuş, pasife çek ve diske yaz
                    user_prem["active"] = False
                    self._sync_premium()

        # 3. FREE CHECK (Cache üzerinden)
        if uid not in self.usage_cache:
            self.usage_cache[uid] = {"count": 0, "last_reset": int(time.time())}
            # Yeni kullanıcı olduğu için diske yazmaya gerek yok, increment'te yazılır.
        
        user_usage = self.usage_cache[uid]
        current_time = int(time.time())
        
        # Günlük Limit Sıfırlama Kontrolü
        if current_time - user_usage["last_reset"] >= RESET_PERIOD:
            user_usage["count"] = 0
            user_usage["last_reset"] = current_time
            self._sync_usage() # Resetlendiği için diske yaz

        count = user_usage["count"]
        
        if count < DAILY_LIMIT:
            return {
                "allowed": True, 
                "type": "Free", 
                "usage": count, 
                "limit": DAILY_LIMIT,
                "remaining": DAILY_LIMIT - count
            }
        
        return {
            "allowed": False, 
            "type": "Free", 
            "usage": count, 
            "limit": DAILY_LIMIT,
            "remaining": 0
        }

    def increment_usage(self, user_id: int, admin_ids: List[int]):
        """
        Kullanım sayacını artırır. Sadece Free kullanıcılar için çalışır.
        """
        if user_id in admin_ids:
            return

        uid = str(user_id)
        
        # Premium kullanıcı ise artırma
        if uid in self.premium_cache:
            p = self.premium_cache[uid]
            if p.get("active") and p.get("expires_at") > time.time():
                return

        # Free kullanıcı sayacını artır
        if uid not in self.usage_cache:
            self.usage_cache[uid] = {"count": 0, "last_reset": int(time.time())}
        
        self.usage_cache[uid]["count"] += 1
        
        # Kritik veriyi diske yaz (Crash durumunda kaybolmasın)
        self._sync_usage()

# Singleton Instance
user_manager = UserManager()