#!/bin/bash
# API'yi arka planda başlat (Portu Render otomatik atar ama biz 8000 deneyelim)
uvicorn solana_api:app --host 0.0.0.0 --port 8000 &

# Payment Engine'i arka planda başlat
python payment_engine.py &

# Botu başlat (Bu ana işlem olarak kalacak)
python main.py