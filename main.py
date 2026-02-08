import os
import logging
import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
)

# --- MODULE IMPORTS ---
from user_manager import user_manager
from payment_session_manager import session_manager

# --- CONFIGURATION ---
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_URL = "http://127.0.0.1:8000"
MASTER_WALLET = "GkbSGWwSuiYDddMpM72NVQWFgLny3W1Yh3WxwoA3kY8D"
SUPPORT_EMAIL = "rugscope.team@gmail.com"

try:
    admin_env = os.getenv("ADMIN_IDS", "")
    ADMIN_IDS = [int(id_str) for id_str in admin_env.split(",") if id_str.strip()]
except ValueError:
    ADMIN_IDS = []

if not BOT_TOKEN:
    raise ValueError("❌ CRITICAL: BOT_TOKEN is missing.")

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("TheRugScopeBot")

# --- STATES ---
WAITING_FOR_WALLET = 1

# --- REPORT FORMATTING ---

def format_premium_report(data: dict, mint: str) -> str:
    """Premium Rapor Formatı"""
    struct = data.get("structural", {})
    sec = data.get("security", {})
    whale = data.get("whale_metrics", {})
    price = data.get("price_data", {})
    verdict = data.get("verdict", {})
    metrics = struct.get("metrics", {})

    risk_level = verdict.get("risk_intensity", "Medium")
    badge = "🟡 MEDIUM RISK"
    if risk_level == "Low": badge = "🟢 LOW RISK"
    elif risk_level == "High": badge = "🟠 HIGH RISK"
    elif risk_level == "Critical": badge = "⛔ CRITICAL RISK"

    if sec.get("mint_authority"): badge = "⛔ CRITICAL (MINTABLE)"
    if whale.get("bundle_detected"): badge = "⛔ CRITICAL (BUNDLE)"

    mint_auth = "✅ Safe" if not sec.get("mint_authority") else "⚠️ **RISK: Mintable**"
    
    bundle_txt = "✅ Clean"
    if whale.get("bundle_detected"):
        bundle_txt = f"🚨 **WARNING: {whale.get('bundle_size')} Wallets Linked!**"

    p_usd = price.get("price_usd", 0)
    p_emoji = "📈" if price.get("price_change_1h", 0) >= 0 else "📉"
    price_line = f"${p_usd:.6f} ({p_emoji} {price.get('price_change_1h', 0):.2f}%)"
    if not price.get("found"): price_line = "N/A"

    trend_cause = verdict.get('correlation_verdict', 'Neutral')

    return (
        f"🛡️ **INSTITUTIONAL RISK REPORT**\n"
        f"**Ref:** `{mint}`\n\n"
        f"**RISK LEVEL:** {badge}\n"
        f"**SUPPLY SCORE:** {struct.get('score')}/100\n\n"
        f"💰 **MARKET ACTION (1H)**\n"
        f"• Price: `{price_line}`\n"
        f"• MC: `${price.get('market_cap', 0):,.0f}`\n"
        f"• Trend Cause: `{trend_cause}`\n\n"
        f"🕵️ **FORENSIC ANALYSIS**\n"
        f"• Bundles: {bundle_txt}\n"
        f"• Mint Auth: {mint_auth}\n\n"
        f"📊 **DISTRIBUTION**\n"
        f"• Top 10 Hold: `{metrics.get('top10_percent', 0):.2f}%`\n"
        f"• HHI Score: `{metrics.get('hhi_estimate', 'N/A')}`\n\n"
        f"🐋 **WHALE ACTIVITY**\n"
        f"• Pressure: `{whale.get('pressure', 'Neutral')}`\n"
        f"• Flow: `{whale.get('net_flow_percent_supply', 0):.2f}%`\n\n"
        f"📝 **VERDICT**\n"
        f"{verdict.get('verdict_label')}\n"
        f"_{verdict.get('verdict_description')}_"
    )

def format_free_report(data: dict, mint: str, usage: int) -> str:
    """Free Rapor Formatı"""
    struct = data.get("structural", {})
    price = data.get("price_data", {})
    p_emoji = "📈" if price.get("price_change_1h", 0) >= 0 else "📉"
    
    return (
        f"🛡️ **BASIC RISK REPORT**\n"
        f"**Ref:** `{mint}`\n\n"
        f"💰 **PRICE:** ${price.get('price_usd', 0):.6f} ({p_emoji} {price.get('price_change_1h', 0):.2f}%)\n"
        f"**SUPPLY SCORE:** {struct.get('score')}/100\n\n"
        f"🔒 **PREMIUM INSIGHTS LOCKED:**\n"
        f"• 🧠 Trend Causality (Whale vs Community)\n"
        f"• 🕵️ Insider Bundle Detection\n"
        f"• 🔒 Full Security Audit\n\n"
        f"💡 _Upgrade to unlock forensic tools._\n"
        f"📉 **Daily Usage:** {usage}/5\n"
        f"👉 `/upgrade`"
    )

# --- ADMIN COMMANDS (YENİ EKLENDİ) ---

async def admin_add_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Sadece Adminlerin kullanabileceği manuel yetki verme komutu.
    Kullanım: /addpremium <USER_ID> <GÜN>
    Örnek: /addpremium 12345678 30
    """
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return # Admin değilse sessizce yoksay

    try:
        if len(context.args) < 2:
            raise ValueError
            
        target_id = int(context.args[0])
        days = int(context.args[1])
        
        # User Manager üzerinden yetki ver
        expiry_date = user_manager.set_premium(target_id, days)
        
        # Admin'e bilgi ver
        await update.message.reply_text(
            f"✅ **ADMIN ACTION SUCCESS**\n"
            f"User: `{target_id}`\n"
            f"Plan: Premium ({days} days)\n"
            f"Expires: `{expiry_date}`",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Kullanıcıya Bildirim Gönder (Opsiyonel - Hata verirse admin işlemini bozmaz)
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=f"🎉 **Congratulations!**\n\nYour account has been upgraded to **PREMIUM** for {days} days by the admin team.\n\nEnjoy unrestricted access! 🕵️‍♂️",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            await update.message.reply_text("⚠️ User updated but notification failed (User might have blocked bot).")

    except (IndexError, ValueError):
        await update.message.reply_text("ℹ️ **Usage:** `/addpremium <User_ID> <Days>`", parse_mode=ParseMode.MARKDOWN)

# --- COMMAND HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    perm = user_manager.check_status(user_id, ADMIN_IDS)
    status_icon = "💎 Premium" if perm["type"] in ["Premium", "Admin"] else "👤 Free Plan"
    
    msg = (
        f"🤖 **TheRugScopeBot v2.9.3**\n"
        f"**Account Status:** `{status_icon}`\n\n"
        "Welcome to the institutional-grade risk analysis tool for Solana.\n"
        "We detect what DexScreener hides.\n\n"
        "🚀 **COMMANDS:**\n"
        "🔹 `/check <Mint>` - Analyze a token\n"
        "🔹 `/upgrade` - Unlock Forensic Features\n"
        "🔹 `/help` - Documentation & Support\n\n"
        "_Select a command to begin._"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        f"📚 **USER GUIDE & SUPPORT**\n\n"
        "**1. HOW TO ANALYZE?**\n"
        "Send the token address (Mint ID) or use:\n"
        "`/check <Mint_Address>`\n\n"
        "**2. RISK LEVELS EXPLAINED**\n"
        "🟢 **Low Risk:** Healthy distribution, no bundles.\n"
        "🟡 **Medium Risk:** Moderate concentration.\n"
        "🟠 **High Risk:** Whale dominance or suspicious flow.\n"
        "⛔ **Critical:** Insider Bundles, Mint Authority enabled, or Scam detected.\n\n"
        "**3. PREMIUM FEATURES**\n"
        "• **Bundle Detection:** Finds linked wallets (Insiders).\n"
        "• **Causality:** Did a whale pump the price?\n"
        "• **Security:** Mint/Freeze authority checks.\n\n"
        "**4. CONTACT & SUPPORT**\n"
        "For billing issues or bug reports:\n"
        f"📧 **Email:** `{SUPPORT_EMAIL}`\n\n"
        "_TheRugScopeBot is an analysis tool, not financial advice._"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    perm = user_manager.check_status(user_id, ADMIN_IDS)
    
    if not perm["allowed"]:
        await update.message.reply_text("🚫 **Daily Limit Reached**\nUpgrade for unlimited access.\n👉 `/upgrade`", parse_mode=ParseMode.MARKDOWN)
        return

    if not context.args:
        await update.message.reply_text("ℹ️ **Usage:** `/check <Mint_Address>`", parse_mode=ParseMode.MARKDOWN)
        return

    msg = await update.message.reply_text("⏳ **Initializing Forensic Scan...**")

    try:
        user_manager.increment_usage(user_id, ADMIN_IDS)
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(f"{API_URL}/analyze/{context.args[0]}")
            resp.raise_for_status()
            data = resp.json()

        txt = format_premium_report(data, context.args[0]) if perm["type"] in ["Premium", "Admin"] else format_free_report(data, context.args[0], perm.get("usage", 0)+1)
        await msg.edit_text(txt, parse_mode=ParseMode.MARKDOWN)

    except Exception:
        await msg.edit_text("⚠️ **Scan Failed:** Please check the token address.")

# --- UPGRADE FLOW ---

async def upgrade_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    perm = user_manager.check_status(user_id, ADMIN_IDS)
    
    if perm["type"] in ["Premium", "Admin"]:
        await update.message.reply_text("✅ **You are already a Premium Member.**", parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

    msg = (
        "💎 **PREMIUM SUBSCRIPTION**\n\n"
        "Unlock the forensic power used by smart money:\n\n"
        "✅ **Insider Bundle Detection** (Anti-Rug)\n"
        "✅ **Price Causality** (Whale vs Retail)\n"
        "✅ **Full Security Audit** (Mint/Freeze)\n"
        "✅ **Unlimited Daily Scans**\n\n"
        "──────────────\n"
        "💵 **Price:** $4.99 / Month\n"
        "💳 **Method:** USDT / USDC (Solana)\n"
        "──────────────\n\n"
        "👇 **To generate an invoice, please reply with your SOLANA WALLET ADDRESS:**"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    return WAITING_FOR_WALLET

async def receive_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wallet = update.message.text.strip()
    user_id = update.effective_user.id

    if len(wallet) < 32:
        await update.message.reply_text("❌ **Invalid Address.** Please try again.")
        return WAITING_FOR_WALLET

    if session_manager.is_wallet_used(wallet):
        await update.message.reply_text("⚠️ **Wallet already active.** Contact support if this is an error.")
        return ConversationHandler.END

    session_manager.create_session(user_id, wallet)

    msg = (
        "🧾 **PAYMENT INVOICE GENERATED**\n\n"
        "Please send exactly **4.99 USDT** or **4.99 USDC** to the address below:\n\n"
        f"`{MASTER_WALLET}`\n"
        "_(Tap address to copy)_\n\n"
        "⚠️ **IMPORTANT INSTRUCTIONS:**\n"
        "1. Network: **Solana (SPL)** ONLY.\n"
        "2. Sender: Must be the wallet you just provided.\n"
        "3. Activation: Automatic (1-2 mins after tx)."
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Transaction cancelled.")
    return ConversationHandler.END

# --- MAIN ---

if __name__ == '__main__':
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # 1. Admin Handler Ekleme
    application.add_handler(CommandHandler('addpremium', admin_add_premium))

    # 2. Upgrade Handler Tanımı (Reentry Fix için)
    upgrade_handler = CommandHandler('upgrade', upgrade_start)

    conv_handler = ConversationHandler(
        entry_points=[upgrade_handler],
        states={
            WAITING_FOR_WALLET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_wallet),
                upgrade_handler # Beklemede olsa bile upgrade komutunu kabul et
            ]
        },
        fallbacks=[
            CommandHandler('cancel', cancel),
            upgrade_handler # Fallback olarak da upgrade kabul et
        ],
        allow_reentry=True
    )

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler('check', check))
    application.add_handler(conv_handler)
    
    logger.info("🚀 TheRugScopeBot v2.9.3 Interface Online.")
    application.run_polling()