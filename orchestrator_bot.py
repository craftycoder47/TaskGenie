"""TaskGenie orchestrator.

TaskGenie is evolving from a collection of AI utilities into a practical
small-business work assistant. Telegram is kept as the current adapter so
we can validate the workflow before moving the product UI to the web.
"""

import json
import os
import re
from typing import Optional

import anthropic
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

# ---------------- CONFIG ----------------

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")
PAYMENT_LINK = os.environ.get("PAYMENT_LINK", "")
MODEL = os.environ.get("TASKGENIE_MODEL", "claude-sonnet-4-6")
DATA_DIR = os.environ.get("DATA_DIR", ".")
FREE_LIMIT = 3
CHECK_INTERVAL_SECONDS = 3600

os.makedirs(DATA_DIR, exist_ok=True)
WATCH_FILE = os.path.join(DATA_DIR, "watched_items.json")
USAGE_FILE = os.path.join(DATA_DIR, "usage.json")

if ANTHROPIC_API_KEY:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
else:
    client = None

# Per-chat workflow state. This is intentionally lightweight for the current
# Telegram prototype; production web UI will move state into a database.
chat_mode = {}
chat_histories = {}
business_profiles = {}


# ---------------- SAFE JSON STORAGE ----------------

def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: str, data):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ---------------- USAGE / PAYMENT ----------------

def load_usage():
    return load_json(USAGE_FILE, {})


def save_usage(data):
    save_json(USAGE_FILE, data)


def get_user_record(chat_id):
    usage = load_usage()
    key = str(chat_id)
    record = usage.setdefault(key, {"uses": 0, "premium": False, "feature_uses": {}})
    # Migrate older records without deleting their existing counters.
    record.setdefault("uses", 0)
    record.setdefault("premium", False)
    record.setdefault("feature_uses", {})
    save_usage(usage)
    return record


def is_allowed(chat_id, feature: str = "general") -> bool:
    record = get_user_record(chat_id)
    return bool(record.get("premium")) or int(record.get("feature_uses", {}).get(feature, 0)) < FREE_LIMIT


def register_use(chat_id, feature: str):
    usage = load_usage()
    key = str(chat_id)
    record = usage.setdefault(key, {"uses": 0, "premium": False, "feature_uses": {}})
    record.setdefault("feature_uses", {})
    record["feature_uses"][feature] = int(record["feature_uses"].get(feature, 0)) + 1
    # Keep legacy total counter for compatibility with existing usage files.
    record["uses"] = int(record.get("uses", 0)) + 1
    save_usage(usage)


def paywall_text(feature: str) -> str:
    if PAYMENT_LINK:
        return (
            f"🔒 You've used your {FREE_LIMIT} free {feature} generations.\n\n"
            f"Continue with TaskGenie here:\n{PAYMENT_LINK}\n\n"
            "After payment, use /activate so the owner can confirm and unlock you."
        )
    return (
        f"🔒 You've used your {FREE_LIMIT} free {feature} generations.\n\n"
        "Paid checkout is being prepared for the public launch."
    )


# ---------------- CLAUDE ----------------

def ask_claude(system_prompt: str, user_text: str, max_tokens: int = 2200) -> str:
    if client is None:
        return "⚠️ TaskGenie AI is not configured yet. Please set ANTHROPIC_API_KEY."
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_text}],
        )
        text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
        return text.strip() or "I couldn't generate an answer this time."
    except Exception:
        # Never expose provider/API exception details to customers.
        return "⚠️ TaskGenie couldn't complete that request right now. Please try again."


# ---------------- BUSINESS GROWTH PACK ----------------

BUSINESS_STEPS = [
    ("business_type", "1/5 — What type of business do you run?\n\nExample: cleaner, plumber, barber, takeaway, electrician"),
    ("location", "2/5 — Where do you serve customers?\n\nExample: Luton and nearby areas"),
    ("services", "3/5 — What are your main services or products?\n\nGive me 3–8 important ones."),
    ("customer", "4/5 — Who is your ideal customer?\n\nExample: busy families, landlords, local homeowners, small offices"),
    ("goal", "5/5 — What is your main goal for the next 7 days?\n\nExample: get more enquiries, fill empty appointments, promote a new service"),
]


def business_pack_prompt(profile: dict) -> str:
    return f"""
Create a practical UK small-business customer growth pack using this profile:

Business type: {profile['business_type']}
Location/service area: {profile['location']}
Services/products: {profile['services']}
Ideal customer: {profile['customer']}
7-day goal: {profile['goal']}

Produce ONE coherent, ready-to-use "7-DAY CUSTOMER GROWTH PACK". Use UK English,
GBP (£) where money is mentioned, and realistic local-business language. Do not
invent awards, qualifications, reviews, guarantees, prices, opening hours,
statistics, customer results, or other facts that the owner did not provide.
Use [ADD ...] placeholders where an important fact is missing.

Include exactly these sections:
1. POSITIONING — one clear value proposition and a short business description.
2. 5 SOCIAL POSTS — ready to publish, with a suggested visual idea for each.
3. 2 GOOGLE BUSINESS UPDATES — useful, local, non-spammy posts.
4. 1 PROMOTION — a simple offer concept that avoids inventing a price.
5. 3 REVIEW REQUEST MESSAGES — short messages suitable for WhatsApp/SMS/email.
6. HOMEPAGE COPY — headline, subheadline, 3 benefits, CTA.
7. FAQ — 5 customer questions with concise answers; use placeholders for unknown facts.
8. 7-DAY ACTION CHECKLIST — one realistic action per day.
9. QUICK WIN — the single action most likely to help this business get an enquiry soon.

Keep it specific to the business. Avoid generic AI-sounding phrases. Make the owner
able to copy, paste, and use the material immediately.
"""


async def business_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_allowed(chat_id, "business"):
        await update.message.reply_text(paywall_text("business"))
        return
    business_profiles[chat_id] = {}
    chat_mode[chat_id] = "business:business_type"
    await update.message.reply_text(
        "🚀 TaskGenie Business Growth Pack\n\n"
        "Let's turn your business details into a practical 7-day customer growth pack.\n\n"
        + BUSINESS_STEPS[0][1]
    )


async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat_id = update.effective_chat.id
    mode = chat_mode.get(chat_id, "")
    if not mode.startswith("business:"):
        return False

    if not is_allowed(chat_id, "business"):
        chat_mode[chat_id] = "general"
        await update.message.reply_text(paywall_text("business"))
        return True

    step = mode.split(":", 1)[1]
    profile = business_profiles.setdefault(chat_id, {})
    profile[step] = update.message.text.strip()
    current_index = next((i for i, item in enumerate(BUSINESS_STEPS) if item[0] == step), None)

    if current_index is None:
        chat_mode[chat_id] = "general"
        await update.message.reply_text("Something went wrong with the business setup. Please use /business again.")
        return True

    if current_index < len(BUSINESS_STEPS) - 1:
        next_key, prompt = BUSINESS_STEPS[current_index + 1]
        chat_mode[chat_id] = f"business:{next_key}"
        await update.message.reply_text(prompt)
        return True

    await update.message.reply_text("🧠 Building your personalised 7-day growth pack…")
    result = ask_claude(
        "You are TaskGenie, a practical UK small-business growth assistant. Produce useful, truthful, ready-to-use marketing material.",
        business_pack_prompt(profile),
        max_tokens=3200,
    )
    register_use(chat_id, "business")
    business_profiles.pop(chat_id, None)
    chat_mode[chat_id] = "general"
    await send_long(update, result)
    return True


# ---------------- LEGACY MODULES ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to TaskGenie.\n\n"
        "🎯 /business — Build a 7-day customer growth pack for your small business\n"
        "📱 /content — Social media posts\n"
        "📄 /resume — Resume / cover letter\n"
        "🔎 /watch — Track a product price\n"
        "📰 /newsletter — Turn notes into a newsletter\n\n"
        "Use /business first if you're a small-business owner and want more customers.\n"
        "Use /menu to show this again or /stop to leave a mode."
    )


async def content_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_mode[update.effective_chat.id] = "content"
    await update.message.reply_text(
        "📱 Content mode on. Send the platform, topic/product and tone.\n"
        "Example: Instagram post about a new coffee shop, friendly tone."
    )


async def resume_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_mode[update.effective_chat.id] = "resume"
    await update.message.reply_text(
        "📄 Resume mode on. Send the target role, your experience/skills, and whether you want a resume section or cover letter."
    )


async def newsletter_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_mode[update.effective_chat.id] = "newsletter"
    await update.message.reply_text(
        "📰 Newsletter mode on. Paste your rough notes, updates, or topic and I'll turn them into a ready-to-send draft."
    )


# ---------------- PRICE WATCH ----------------

def load_watches():
    return load_json(WATCH_FILE, {})


def save_watches(data):
    save_json(WATCH_FILE, data)


def get_price(url: str) -> Optional[float]:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TaskGenie/1.0)"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        tag = soup.find("meta", property="product:price:amount") or soup.find("meta", property="og:price:amount")
        if tag and tag.get("content"):
            return float(tag["content"].replace(",", ""))

        tag = soup.find(attrs={"itemprop": "price"})
        if tag:
            value = tag.get("content") or tag.get_text()
            match = re.search(r"\d[\d,.]*", value)
            if match:
                return float(match.group().replace(",", ""))

        # UK-first fallback: prefer GBP symbols but accept common dollar pages too.
        for pattern in (r"£\s?([\d,]+(?:\.\d{2})?)", r"\$\s?([\d,]+(?:\.\d{2})?)"):
            match = re.search(pattern, response.text)
            if match:
                return float(match.group(1).replace(",", ""))
    except (requests.RequestException, ValueError, TypeError):
        return None
    return None


async def watch_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_mode[update.effective_chat.id] = "watch"
    await update.message.reply_text(
        "🔎 Price watch mode on. Send a product URL and target price.\n\n"
        "Example: https://example.com/item 49.99\n"
        "I'll check hourly and alert you if the target is reached."
    )


async def my_watches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    watches = load_watches().get(chat_id, [])
    if not watches:
        await update.message.reply_text("You're not tracking any products yet.")
        return
    lines = []
    for i, item in enumerate(watches, 1):
        last = item.get("last_price")
        last_text = f"£{last:.2f}" if last is not None else "unknown"
        lines.append(f"{i}. {item['url']} — target £{item['target_price']:.2f} (last seen: {last_text})")
    await send_long(update, "Your tracked products:\n\n" + "\n".join(lines))


# ---------------- COMMANDS / HANDLER ----------------

async def activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text("Thanks — your payment claim has been flagged for confirmation.")
    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(
                chat_id=int(ADMIN_CHAT_ID),
                text=f"💰 Payment claim from chat_id {chat_id}. If confirmed, run /unlock {chat_id}",
            )
        except Exception:
            pass


async def unlock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    requester = str(update.effective_chat.id)
    if not ADMIN_CHAT_ID or requester != str(ADMIN_CHAT_ID):
        await update.message.reply_text("This command is only available to the bot owner.")
        return
    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text("Usage: /unlock <chat_id>")
        return
    target = context.args[0]
    usage = load_usage()
    record = usage.setdefault(target, {"uses": 0, "premium": False, "feature_uses": {}})
    record["premium"] = True
    record.setdefault("feature_uses", {})
    save_usage(usage)
    await update.message.reply_text(f"✅ {target} is now unlocked (premium).")
    try:
        await context.bot.send_message(chat_id=int(target), text="🎉 You're unlocked! Premium access enabled.")
    except Exception:
        pass


async def terms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "TaskGenie terms (prototype):\n\n"
        f"- Free plan: {FREE_LIMIT} generations per feature.\n"
        "- AI output can contain mistakes; review it before publishing or relying on it.\n"
        "- Do not provide passwords, payment credentials, or other sensitive secrets.\n"
        "- Payments are currently handled externally while checkout is being prepared."
    )


async def stop_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_mode[update.effective_chat.id] = "general"
    business_profiles.pop(update.effective_chat.id, None)
    await update.message.reply_text("Back to general mode. 👍")


async def send_long(update: Update, text: str):
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i + 4000])


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await handle_business_message(update, context):
        return

    chat_id = update.effective_chat.id
    user_text = (update.message.text or "").strip()
    mode = chat_mode.get(chat_id, "general")

    if mode in {"content", "resume", "newsletter"}:
        if not is_allowed(chat_id, mode):
            reply_text = paywall_text(mode)
        else:
            prompts = {
                "content": "You are a social media content writer. Write 3 concise ready-to-post captions with useful hashtags.",
                "resume": "You are a professional UK resume and cover-letter writer. Produce polished, ATS-friendly material without inventing experience.",
                "newsletter": "You are a newsletter editor. Turn rough notes into a concise, friendly, ready-to-send newsletter with subject line and sections.",
            }
            reply_text = ask_claude(prompts[mode], user_text)
            register_use(chat_id, mode)
        await send_long(update, reply_text)
        return

    if mode == "watch":
        parts = user_text.rsplit(" ", 1)
        if len(parts) != 2 or not parts[0].startswith(("http://", "https://")):
            await update.message.reply_text("Please send: <product URL> <target price>\nExample: https://example.com/item 49.99")
            return
        url, price_str = parts
        try:
            target_price = float(price_str)
            if target_price <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a valid positive target price, e.g. 49.99")
            return
        current_price = get_price(url)
        watches = load_watches()
        key = str(chat_id)
        watches.setdefault(key, []).append({"url": url, "target_price": target_price, "last_price": current_price})
        save_watches(watches)
        if current_price is None:
            await update.message.reply_text("✅ Tracking added. I couldn't read the price right now, but I'll keep checking hourly.")
        else:
            await update.message.reply_text(f"✅ Tracking added.\nCurrent price: £{current_price:.2f}\nTarget: £{target_price:.2f}")
        return

    # General chat is intentionally kept available for prototype testing.
    history = chat_histories.get(chat_id, [])
    history.append({"role": "user", "content": user_text})
    history = history[-10:]
    if client is None:
        reply_text = "⚠️ TaskGenie AI is not configured yet."
    else:
        try:
            response = client.messages.create(model=MODEL, max_tokens=1500, messages=history)
            reply_text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text").strip()
        except Exception:
            reply_text = "⚠️ TaskGenie couldn't complete that request right now. Please try again."
    history.append({"role": "assistant", "content": reply_text})
    chat_histories[chat_id] = history[-10:]
    await send_long(update, reply_text)


async def check_prices_job(context: ContextTypes.DEFAULT_TYPE):
    watches = load_watches()
    changed = False
    for chat_id, items in list(watches.items()):
        still_tracking = []
        for item in items:
            price = get_price(item["url"])
            if price is not None:
                if price <= item["target_price"]:
                    try:
                        await context.bot.send_message(
                            chat_id=int(chat_id),
                            text=(f"🎉 Price target reached!\n{item['url']}\n"
                                  f"Now £{price:.2f} (target £{item['target_price']:.2f})"),
                        )
                    except Exception:
                        pass
                    changed = True
                    continue
                if price != item.get("last_price"):
                    item["last_price"] = price
                    changed = True
            still_tracking.append(item)
        watches[chat_id] = still_tracking
    if changed:
        save_watches(watches)


def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN is not configured")

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", start))
    application.add_handler(CommandHandler("business", business_mode))
    application.add_handler(CommandHandler("content", content_mode))
    application.add_handler(CommandHandler("resume", resume_mode))
    application.add_handler(CommandHandler("watch", watch_mode))
    application.add_handler(CommandHandler("mywatches", my_watches))
    application.add_handler(CommandHandler("newsletter", newsletter_mode))
    application.add_handler(CommandHandler("activate", activate))
    application.add_handler(CommandHandler("unlock", unlock))
    application.add_handler(CommandHandler("terms", terms))
    application.add_handler(CommandHandler("stop", stop_mode))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if application.job_queue is not None:
        application.job_queue.run_repeating(check_prices_job, interval=CHECK_INTERVAL_SECONDS, first=60)

    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
