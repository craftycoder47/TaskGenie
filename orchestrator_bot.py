"""
Orchestrator Telegram Bot powered by Claude.
One bot, multiple money-making modules, each triggered by a command.

Current modules:
  /content    -> Social media caption / post generator
  /resume     -> Resume / cover letter generator
  /watch      -> Price watch / deal alert tracker
  /newsletter -> Turns notes/topics into a ready newsletter draft
  (just chat normally for general Q&A / code help)

Payments: free modules are limited to FREE_LIMIT uses per person, then the
bot shows a payment link. The bot owner unlocks a customer after payment
using /unlock <chat_id> (see PAYMENTS.md for the full flow).
"""

import os
import re
import json
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters
import anthropic

# ---- CONFIG ----
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "PUT_YOUR_TELEGRAM_TOKEN_HERE")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "PUT_YOUR_ANTHROPIC_API_KEY_HERE")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")  # your own Telegram chat ID, set on Railway
PAYMENT_LINK = os.environ.get("PAYMENT_LINK", "https://PUT_YOUR_STRIPE_OR_PAYPAL_LINK_HERE")
MODEL = "claude-sonnet-4-6"
WATCH_FILE = "watched_items.json"
USAGE_FILE = "usage.json"
CHECK_INTERVAL_SECONDS = 3600  # check prices every hour
FREE_LIMIT = 3  # free uses per person for paid modules (content/resume/newsletter)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Tracks which module each chat is currently using ("general", "content", "resume", "watch", "newsletter")
chat_mode = {}
chat_histories = {}


# ---------------- USAGE / PAYMENT STORAGE ----------------

def load_usage():
    if not os.path.exists(USAGE_FILE):
        return {}
    with open(USAGE_FILE, "r") as f:
        return json.load(f)


def save_usage(data):
    with open(USAGE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_user_record(chat_id):
    usage = load_usage()
    key = str(chat_id)
    if key not in usage:
        usage[key] = {"uses": 0, "premium": False}
        save_usage(usage)
    return usage[key]


def register_use(chat_id):
    usage = load_usage()
    key = str(chat_id)
    usage.setdefault(key, {"uses": 0, "premium": False})
    usage[key]["uses"] += 1
    save_usage(usage)


def is_allowed(chat_id):
    """True if this user can still use a paid module (premium or under free limit)."""
    record = get_user_record(chat_id)
    return record["premium"] or record["uses"] < FREE_LIMIT


PAYWALL_TEXT = (
    "🔒 You've used your {limit} free generations.\n\n"
    "To keep using this feature, pay here:\n{link}\n\n"
    "After paying, send /activate and I'll let the bot owner know to unlock you."
)


# ---------------- PRICE WATCH STORAGE ----------------

def load_watches():
    if not os.path.exists(WATCH_FILE):
        return {}
    with open(WATCH_FILE, "r") as f:
        return json.load(f)


def save_watches(data):
    with open(WATCH_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_price(url):
    """
    Try to find a price on a product page. Works on many stores that use
    standard meta tags; large sites like Amazon actively block simple
    scraping and may need a dedicated method later.
    Returns a float price or None if not found.
    """
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")

        # Common pattern 1: Open Graph price meta tag
        tag = soup.find("meta", property="product:price:amount") or \
              soup.find("meta", property="og:price:amount")
        if tag and tag.get("content"):
            return float(tag["content"])

        # Common pattern 2: itemprop price
        tag = soup.find(attrs={"itemprop": "price"})
        if tag:
            value = tag.get("content") or tag.get_text()
            match = re.search(r"[\d,.]+", value)
            if match:
                return float(match.group().replace(",", ""))

        # Fallback: look for a dollar-sign price anywhere prominent
        match = re.search(r"\$\s?([\d,]+\.\d{2})", resp.text)
        if match:
            return float(match.group(1).replace(",", ""))

    except Exception:
        return None
    return None


def ask_claude(system_prompt, user_text):
    """Send a single prompt to Claude and return the text reply."""
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_text}],
        )
        return "".join(block.text for block in response.content if block.type == "text")
    except Exception as e:
        return f"⚠️ Error talking to Claude: {e}"


# ---------------- COMMANDS ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! I'm your all-in-one bot. Available modules:\n\n"
        "/content - Generate social media captions/posts\n"
        "/resume - Generate a resume or cover letter\n"
        "/watch - Track a product's price and get alerted on drops\n"
        "/mywatches - See what you're tracking\n"
        "/newsletter - Turn notes into a ready newsletter draft\n"
        "/terms - View terms of service\n"
        "/menu - Show this menu again\n"
        "/stop - Return to general chat mode\n\n"
        "Or just type a normal message and I'll chat / help with code."
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def content_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_mode[update.effective_chat.id] = "content"
    await update.message.reply_text(
        "📱 Content mode on. Tell me:\n"
        "- the platform (Instagram, TikTok, X, etc.)\n"
        "- the topic or product\n"
        "- the tone (funny, professional, casual...)\n\n"
        "Example: 'Instagram post about a new coffee shop, friendly tone'"
    )


async def resume_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_mode[update.effective_chat.id] = "resume"
    await update.message.reply_text(
        "📄 Resume mode on. Send me:\n"
        "- your job title / target role\n"
        "- your experience (jobs, years, skills)\n"
        "- resume OR cover letter\n\n"
        "Example: 'Cover letter for a barista job, 1 year experience, friendly and hardworking'"
    )


async def watch_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_mode[update.effective_chat.id] = "watch"
    await update.message.reply_text(
        "🔎 Price watch mode on. Send me a product link and your target price, like:\n\n"
        "https://example.com/product 49.99\n\n"
        "I'll check every hour and message you when the price drops to that amount or below.\n"
        "Note: this works best on smaller/independent stores — big sites like Amazon often "
        "block simple price checks.\n\n"
        "Send /mywatches to see what you're tracking, or /stop to leave this mode."
    )


async def my_watches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    watches = load_watches().get(chat_id, [])
    if not watches:
        await update.message.reply_text("You're not tracking any products yet.")
        return
    lines = [f"{i+1}. {w['url']} — target ${w['target_price']:.2f} (last seen: "
             f"{'$' + format(w['last_price'], '.2f') if w['last_price'] else 'unknown'})"
             for i, w in enumerate(watches)]
    await update.message.reply_text("Your tracked products:\n\n" + "\n".join(lines))


async def newsletter_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_mode[update.effective_chat.id] = "newsletter"
    await update.message.reply_text(
        "📰 Newsletter mode on. Paste your rough notes, updates, or topic, and I'll turn "
        "them into a clean, ready-to-send newsletter draft.\n\n"
        "Example: 'This month: launched new flavor, hit 500 followers, weekend sale starts Friday'"
    )


async def activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "Thanks! I've flagged this for the bot owner to confirm your payment and unlock "
        "your account — this usually happens within a day."
    )
    if ADMIN_CHAT_ID:
        await context.bot.send_message(
            chat_id=int(ADMIN_CHAT_ID),
            text=f"💰 Payment claim from chat_id {chat_id}. If confirmed, run:\n/unlock {chat_id}"
        )


async def unlock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: /unlock <chat_id> marks a user as premium (unlimited use)."""
    requester = str(update.effective_chat.id)
    if not ADMIN_CHAT_ID or requester != str(ADMIN_CHAT_ID):
        await update.message.reply_text("This command is only available to the bot owner.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /unlock <chat_id>")
        return
    target = context.args[0]
    usage = load_usage()
    usage.setdefault(target, {"uses": 0, "premium": False})
    usage[target]["premium"] = True
    save_usage(usage)
    await update.message.reply_text(f"✅ {target} is now unlocked (premium).")
    await context.bot.send_message(chat_id=int(target), text="🎉 You're unlocked! Unlimited access enabled.")


async def terms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Terms of service (short version):\n\n"
        f"- Free plan: {FREE_LIMIT} generations per feature, then payment is required.\n"
        "- Payments are handled externally; contact the bot owner for refund requests.\n"
        "- This bot generates AI content/assistance; review outputs before relying on them "
        "for real decisions (job applications, purchases, etc.)."
    )


async def stop_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_mode[update.effective_chat.id] = "general"
    await update.message.reply_text("Back to general chat mode.")


# ---------------- MESSAGE HANDLER ----------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text
    mode = chat_mode.get(chat_id, "general")

    if mode == "content":
        if not is_allowed(chat_id):
            reply_text = PAYWALL_TEXT.format(limit=FREE_LIMIT, link=PAYMENT_LINK)
        else:
            system_prompt = (
                "You are a social media content writer. Given a platform, topic, and tone, "
                "write 3 short ready-to-post captions with relevant hashtags. Be punchy and specific."
            )
            reply_text = ask_claude(system_prompt, user_text)
            register_use(chat_id)

    elif mode == "resume":
        if not is_allowed(chat_id):
            reply_text = PAYWALL_TEXT.format(limit=FREE_LIMIT, link=PAYMENT_LINK)
        else:
            system_prompt = (
                "You are a professional resume and cover letter writer. Given a role and "
                "experience details, write a polished, ATS-friendly resume section or a "
                "concise cover letter (whichever was requested). Keep formatting clean."
            )
            reply_text = ask_claude(system_prompt, user_text)
            register_use(chat_id)

    elif mode == "newsletter":
        if not is_allowed(chat_id):
            reply_text = PAYWALL_TEXT.format(limit=FREE_LIMIT, link=PAYMENT_LINK)
        else:
            system_prompt = (
                "You are a newsletter editor. Given rough notes or updates, write a clean, "
                "friendly newsletter draft with a short subject line, a greeting, organized "
                "sections, and a warm sign-off. Keep it concise and ready to send."
            )
            reply_text = ask_claude(system_prompt, user_text)
            register_use(chat_id)

    elif mode == "watch":
        parts = user_text.rsplit(" ", 1)
        if len(parts) != 2:
            reply_text = "Please send it as: <link> <target price>\nExample: https://example.com/item 49.99"
        else:
            url, price_str = parts
            try:
                target_price = float(price_str)
            except ValueError:
                reply_text = "That doesn't look like a valid price. Example: https://example.com/item 49.99"
                for i in range(0, len(reply_text), 4000):
                    await update.message.reply_text(reply_text[i:i + 4000])
                return

            current_price = get_price(url)
            watches = load_watches()
            chat_key = str(chat_id)
            watches.setdefault(chat_key, [])
            watches[chat_key].append({
                "url": url,
                "target_price": target_price,
                "last_price": current_price,
            })
            save_watches(watches)

            if current_price is not None:
                reply_text = (
                    f"✅ Tracking added.\nCurrent price: ${current_price:.2f}\n"
                    f"Target: ${target_price:.2f}\nI'll alert you when it drops."
                )
            else:
                reply_text = (
                    "✅ Added to tracking, but I couldn't read a price from that page right now. "
                    "I'll keep checking hourly — some sites are harder to read than others."
                )

    else:
        # General chat / coding help, with short memory of the conversation
        history = chat_histories.get(chat_id, [])
        history.append({"role": "user", "content": user_text})
        history = history[-10:]
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=1500,
                messages=history,
            )
            reply_text = "".join(block.text for block in response.content if block.type == "text")
        except Exception as e:
            reply_text = f"⚠️ Error talking to Claude: {e}"
        history.append({"role": "assistant", "content": reply_text})
        chat_histories[chat_id] = history

    for i in range(0, len(reply_text), 4000):
        await update.message.reply_text(reply_text[i:i + 4000])


async def check_prices_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every CHECK_INTERVAL_SECONDS. Checks all tracked products and alerts on drops."""
    watches = load_watches()
    changed = False

    for chat_id, items in watches.items():
        still_tracking = []
        for item in items:
            price = get_price(item["url"])
            if price is not None:
                if price <= item["target_price"]:
                    await context.bot.send_message(
                        chat_id=int(chat_id),
                        text=f"🎉 Price drop! {item['url']}\nNow ${price:.2f} "
                             f"(your target was ${item['target_price']:.2f})"
                    )
                    changed = True
                    continue  # remove from list once target is hit
                if price != item["last_price"]:
                    item["last_price"] = price
                    changed = True
            still_tracking.append(item)
        watches[chat_id] = still_tracking

    if changed:
        save_watches(watches)


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("content", content_mode))
    app.add_handler(CommandHandler("resume", resume_mode))
    app.add_handler(CommandHandler("watch", watch_mode))
    app.add_handler(CommandHandler("mywatches", my_watches))
    app.add_handler(CommandHandler("newsletter", newsletter_mode))
    app.add_handler(CommandHandler("activate", activate))
    app.add_handler(CommandHandler("unlock", unlock))
    app.add_handler(CommandHandler("terms", terms))
    app.add_handler(CommandHandler("stop", stop_mode))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.job_queue.run_repeating(check_prices_job, interval=CHECK_INTERVAL_SECONDS, first=60)
    print("Orchestrator bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
