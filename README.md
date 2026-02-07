# 🛡️ TheRugScopeBot - Institutional Risk Analysis for Solana

**TheRugScopeBot** is an advanced forensic analysis tool designed to detect **Insider Bundles**, **Whale Manipulation**, and **Rug Pull risks** on the Solana blockchain before they happen.

Unlike standard checkers, this bot performs deep on-chain analysis to identify connected wallets and funding sources.

## 🚀 Key Features

- **🕵️ Insider Bundle Detection:** Identifies if Top 10 wallets are funded by the same source (Dev/Bundler).
- **💰 Price Causality Engine:** Determines if price action is organic or whale-driven manipulation.
- **🔒 Security Audit:** Checks Mint Authority, Freeze Authority, and LP status.
- **🐋 Whale Pressure:** Real-time analysis of large holder accumulation vs. distribution.
- **📊 Structural Analysis:** Calculates Gini Coefficient & HHI for supply concentration.

## 🛠️ Tech Stack

- **Python 3.10+**
- **Aiogram / Python-Telegram-Bot** (Interface)
- **FastAPI** (Backend Analysis Engine)
- **Solana.py & Solders** (Blockchain Interaction)
- **Helius RPC & DexScreener API** (Data Sources)

## ⚙️ Installation

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/YOUR_USERNAME/TheRugScopeBot.git](https://github.com/YOUR_USERNAME/TheRugScopeBot.git)
   cd TheRugScopeBot