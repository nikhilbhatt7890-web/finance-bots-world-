import os
import json
import re
import logging
import tempfile
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, ContextTypes, filters
)

# --- CONFIG ---
# IMPORTANT: set TOKEN in your host's environment variables (e.g., Render/Railway variables)
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise ValueError("❌ No TOKEN found! Set the TOKEN environment variable before running the bot.")

DATA_FILE = "user_strategies.json"
STRATEGY_EDIT = range(1)  # Single state

# --- LOGGING ---
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- DATA STORAGE ---
def load_user_data():
    """Load user data from JSON file."""
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # convert keys to int (saved as strings)
        return {int(k): v for k, v in data.items()}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_user_data():
    """Save user data to JSON file atomically to avoid corruption."""
    try:
        # Convert keys back to strings for JSON
        serializable = {str(k): v for k, v in user_data.items()}
        dirpath = os.path.dirname(os.path.abspath(DATA_FILE)) or "."
        with tempfile.NamedTemporaryFile("w", dir=dirpath, delete=False, encoding="utf-8") as tf:
            json.dump(serializable, tf, indent=4, ensure_ascii=False)
            tempname = tf.name
        os.replace(tempname, DATA_FILE)
    except Exception as e:
        logger.exception("Failed to save user data: %s", e)

user_data = load_user_data()

# --- HELPERS ---
def get_summary(chat_id):
    """Return formatted strategy summary."""
    if chat_id not in user_data or not user_data[chat_id]["categories"]:
        return "📭 You have no categories yet."

    lines = [f"🔹 {cat}: {pct}%" for cat, pct in user_data[chat_id]["categories"]]
    total = user_data[chat_id]["total"]
    remaining = 100 - total

    return (
        f"📊 *Your Current Strategy:*\n" +
        "\n".join(lines) +
        f"\n\n*Total:* {total}% | *Remaining:* {remaining}%"
    )

async def send_typing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show typing indicator."""
    # some update types may not have effective_chat; guard defensively
    chat = update.effective_chat
    if chat:
        await context.bot.send_chat_action(chat.id, ChatAction.TYPING)

def get_action_keyboard():
    """Inline keyboard for next actions."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Another Category", callback_data="add")],
        [InlineKeyboardButton("⏪ Undo Last Entry", callback_data="undo")],
        [InlineKeyboardButton("❌ Cancel & Reset", callback_data="cancel")]
    ])

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    await send_typing(update, context)
    kb = [[InlineKeyboardButton("🚀 Let's Set Up My Strategy", callback_data="start_strategy")]]
    # update.message is expected for /start, but guard just in case
    if update.message:
        await update.message.reply_text(
            "👋 *Welcome to your Personal Finance Assistant!* 💰\n\n"
            "We’ll design a percentage-based budget plan together — dividing your money into categories like Needs, Savings, and Fun.\n"
            "Click below to start building your personal strategy!",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(kb)
        )

async def begin_strategy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Begin a new strategy."""
    chat = update.effective_chat
    if not chat:
        return ConversationHandler.END

    chat_id = chat.id
    user_data[chat_id] = {"categories": [], "total": 0}
    save_user_data()

    text = (
        "💡 Let's start building your custom financial strategy.\n\n"
        "Assign a percentage to each category until you reach 100%.\n"
        "For example: `50% Needs` means half your income goes to essentials."
    )

    if update.callback_query:
        q = update.callback_query
        await q.answer()
        await q.edit_message_text(text, parse_mode='Markdown')
    else:
        if update.message:
            await update.message.reply_text(text, parse_mode='Markdown')

    return STRATEGY_EDIT

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle percentage and category input."""
    chat = update.effective_chat
    if not chat or not update.message:
        return STRATEGY_EDIT

    chat_id = chat.id
    text = update.message.text.strip()

    match = re.search(r"(\d+)\s*%?\s*(?:for|on|to)?\s*(.+)", text, re.I)
    if not match:
        await update.message.reply_text(
            "⚠️ I couldn’t understand that.\n\n"
            "Please use this format: `30% Savings`\n"
            "This means you want that % of your income to go to a category.",
            parse_mode='Markdown'
        )
        return STRATEGY_EDIT

    pct, category = int(match.group(1)), match.group(2).strip().capitalize()

    if not (0 < pct <= 100):
        await update.message.reply_text(
            "❌ Percentage must be between 1 and 100.",
            parse_mode='Markdown'
        )
        return STRATEGY_EDIT

    # ensure the user's data exists
    if chat_id not in user_data:
        user_data[chat_id] = {"categories": [], "total": 0}

    total = user_data[chat_id]["total"]
    if total + pct > 100:
        await update.message.reply_text(
            f"❌ Only {100 - total}% left to assign.",
            parse_mode='Markdown'
        )
        return STRATEGY_EDIT

    user_data[chat_id]["categories"].append((category, pct))
    user_data[chat_id]["total"] += pct
    save_user_data()

    await update.message.reply_text(
        f"✅ *Added:* {category} – {pct}%\n\n{get_summary(chat_id)}",
        parse_mode='Markdown'
    )

    if user_data[chat_id]["total"] == 100:
        await update.message.reply_text(
            "🎯 *Strategy complete!*\n\nType /status anytime to view your plan.",
            parse_mode='Markdown'
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "What would you like to do next?",
        parse_mode='Markdown',
        reply_markup=get_action_keyboard()
    )
    return STRATEGY_EDIT

async def add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask for another category."""
    q = update.callback_query
    if not q:
        return STRATEGY_EDIT
    await q.answer()
    await q.edit_message_text("Enter the next category as `20% Fun`.", parse_mode='Markdown')
    return STRATEGY_EDIT

async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Undo last entry (works globally)."""
    chat = update.effective_chat
    if not chat:
        return STRATEGY_EDIT
    chat_id = chat.id

    if user_data.get(chat_id) and user_data[chat_id]["categories"]:
        cat, pct = user_data[chat_id]["categories"].pop()
        user_data[chat_id]["total"] -= pct
        save_user_data()
        msg = f"⏪ Removed: {cat} ({pct}%)\n\n{get_summary(chat_id)}"
    else:
        msg = "⚠️ Nothing to undo."

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            msg, parse_mode='Markdown', reply_markup=get_action_keyboard()
        )
    else:
        if update.message:
            await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=get_action_keyboard())

    return STRATEGY_EDIT

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel and reset (works globally)."""
    chat = update.effective_chat
    if not chat:
        return ConversationHandler.END
    chat_id = chat.id
    user_data.pop(chat_id, None)
    save_user_data()

    text = (
        "🔄 *Your budget strategy has been reset.*\n\n"
        "All saved categories deleted.\n"
        "Type /strategy to start over."
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode='Markdown')
    else:
        if update.message:
            await update.message.reply_text(text, parse_mode='Markdown')

    return ConversationHandler.END

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current strategy."""
    chat = update.effective_chat
    if not chat:
        return
    await update.message.reply_text(
        get_summary(chat.id),
        parse_mode='Markdown'
    )

# --- MAIN ---
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # Conversation for strategy building
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("strategy", begin_strategy),
            CallbackQueryHandler(begin_strategy, pattern="^start_strategy$")
        ],
        states={
            STRATEGY_EDIT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
                CallbackQueryHandler(add_category, pattern="^add$")
            ]
        },
        fallbacks=[
            CallbackQueryHandler(cancel, pattern="^cancel$"),
            CommandHandler("cancel", cancel)
        ]
    )

    # Core commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(conv_handler)

    # Global undo/cancel outside conversations
    app.add_handler(CommandHandler("undo", undo))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(undo, pattern="^undo$"))
    app.add_handler(CallbackQueryHandler(cancel, pattern="^cancel$"))

    logger.info("Bot is starting...")
    app.run_polling(poll_interval=0.5)

if __name__ == "__main__":
    main()
