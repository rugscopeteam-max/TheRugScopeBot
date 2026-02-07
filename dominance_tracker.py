import json
import os
import time
import math
import logging
import errno
from contextlib import contextmanager
from typing import Dict, List, Any

# --- CONFIGURATION ---
SNAPSHOT_FILE = "dominance_snapshot.json"
LOCK_FILE = "dominance.lock"
HISTORY_LIMIT = 10  # Her token için son 10 analizi sakla

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("DominanceTracker")

# --- ATOMIC FILE LOCKING ---
@contextmanager
def atomic_lock():
    """
    OS-Level Atomic Spinlock.
    Uses O_CREAT | O_EXCL to ensure true mutual exclusion.
    """
    timeout = 5.0
    start_time = time.time()
    lock_fd = None
    
    while True:
        try:
            # os.O_EXCL: Dosya varsa hata fırlatır (Atomik İşlem)
            lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            break
        except OSError as e:
            if e.errno == errno.EEXIST:
                # Dosya kilitli, bekle
                if time.time() - start_time > timeout:
                    # Deadlock koruması: Kilit çok eskiyse kır
                    try:
                        if os.path.exists(LOCK_FILE) and (time.time() - os.path.getmtime(LOCK_FILE) > timeout):
                            logger.warning("Breaking stale lock.")
                            os.remove(LOCK_FILE)
                    except OSError:
                        pass
                    # Tekrar dene
                    continue
                time.sleep(0.1)
            else:
                # Başka bir hata varsa
                raise
    
    try:
        yield
    finally:
        # Kilidi temizle
        if lock_fd is not None:
            os.close(lock_fd)
        try:
            os.remove(LOCK_FILE)
        except OSError:
            pass

# --- DATABASE OPERATIONS ---
def _load_db() -> Dict:
    if not os.path.exists(SNAPSHOT_FILE):
        return {}
    try:
        with open(SNAPSHOT_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_db(data: Dict):
    # Atomik yazma: Önce .tmp'ye yaz, sonra rename et
    tmp = f"{SNAPSHOT_FILE}.tmp"
    try:
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=4)
        os.replace(tmp, SNAPSHOT_FILE)
    except Exception as e:
        logger.error(f"Database Save Failed: {e}")

# --- ANALYTICS ENGINE ---
def calculate_slope(history: List[Dict]) -> float:
    """Doğrusal regresyon ile trend eğimini hesaplar."""
    if len(history) < 2: return 0.0
    
    # x = zaman (saat cinsinden normalize), y = yüzde
    start_time = history[0]['ts']
    x = [(h['ts'] - start_time) / 3600.0 for h in history]
    y = [h['val'] for h in history]
    
    n = len(x)
    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(xi*yi for xi, yi in zip(x, y))
    sum_xx = sum(xi**2 for xi in x)
    
    denominator = (n * sum_xx - sum_x**2)
    if denominator == 0: return 0.0
    
    slope = (n * sum_xy - sum_x * sum_y) / denominator
    return slope

def calculate_volatility(history: List[Dict]) -> float:
    """Top 1 cüzdanın standart sapmasını (oynaklığını) hesaplar."""
    if len(history) < 2: return 0.0
    vals = [h['val'] for h in history]
    mean = sum(vals) / len(vals)
    variance = sum((x - mean) ** 2 for x in vals) / len(vals)
    return math.sqrt(variance)

# --- MAIN LOGIC ---
def calculate_dominance_shift(mint: str, current_top1: float) -> Dict[str, Any]:
    """
    En büyük cüzdanın zaman içindeki değişimini takip eder ve yorumlar.
    """
    with atomic_lock():
        db = _load_db()
        now = int(time.time())
        
        if mint not in db:
            db[mint] = []
        
        history = db[mint]
        
        # Yeni veriyi ekle
        history.append({"ts": now, "val": current_top1})
        
        # Geçmişi sınırla (Disk tasarrufu)
        if len(history) > HISTORY_LIMIT:
            history = history[-HISTORY_LIMIT:]
        
        db[mint] = history
        _save_db(db)
        
        # Yetersiz veri durumu
        if len(history) < 2:
            return {
                "previous_top1": 0.0,
                "current_top1": current_top1,
                "shift": 0.0,
                "slope": 0.0,
                "volatility": 0.0,
                "regime": "Initial",
                "status": "First Record"
            }
        
        # Metrik Hesaplamaları
        prev_val = history[-2]['val']
        shift = current_top1 - prev_val
        slope = calculate_slope(history)
        volatility = calculate_volatility(history)
        
        # Rejim Tespiti (Piyasa Durumu)
        if slope > 0.5: regime = "Aggressive Consolidation"
        elif slope < -0.5: regime = "Rapid Dilution"
        elif volatility > 2.0: regime = "Volatile Reallocation"
        else: regime = "Stable"
        
        # Statü Tespiti
        status = "Stable"
        if shift > 2.0: status = "Accumulation"
        elif shift < -2.0: status = "Distribution"
        
        return {
            "previous_top1": prev_val,
            "current_top1": current_top1,
            "shift": round(shift, 2),
            "slope": round(slope, 4),
            "volatility": round(volatility, 2),
            "regime": regime,
            "status": status
        }