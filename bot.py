import asyncio
from datetime import datetime, timezone
from functools import wraps
from typing import Callable, Any, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

import config
from config import logger
import feed
from screenshots import send_alert_chart
from alerts import alert_manager

def authorized_only(func: Callable) -> Callable:
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any) -> Any:
        user = update.effective_user
        if not user or user.id != config.ALLOWED_USER_ID:
            logger.warning(f"Unauthorized access attempt from user_id={user.id if user else 'Unknown'}")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

def get_keyboard() -> InlineKeyboardMarkup:
    two_green = "ON" if alert_manager.is_setting_enabled("two_green_candles") else "OFF"
    two_red = "ON" if alert_manager.is_setting_enabled("two_red_candles") else "OFF"
    active_alerts_cnt = len(alert_manager.get_active_price_alerts())

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Live & Closed Bar", callback_data="btn_latest")],
        [
            InlineKeyboardButton(f"🟢🟢 2-Green: {two_green}", callback_data="btn_two_green"),
            InlineKeyboardButton(f"🔴🔴 2-Red: {two_red}", callback_data="btn_two_red"),
        ],
        [
            InlineKeyboardButton(f"🎯 Price Alerts ({active_alerts_cnt})", callback_data="btn_alerts_list"),
            InlineKeyboardButton("ℹ️ Status", callback_data="btn_status"),
        ],
    ])

def calculate_next_wait_seconds(timeframe: str) -> int:
    now = datetime.now(timezone.utc)
    interval_sec = config.get_interval_seconds(timeframe)
    total_seconds_today = now.hour * 3600 + now.minute * 60 + now.second
    next_boundary = ((total_seconds_today // interval_sec) + 1) * interval_sec + 10
    wait_sec = next_boundary - total_seconds_today
    if wait_sec <= 0:
        wait_sec += interval_sec
    return wait_sec

@authorized_only
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🟡 *XAU/USD Multi-Alert Monitor*\n"
        "Active monitoring engine with price level rejection & candle momentum alerts.\n\n"
        "💡 *Available Commands:*\n"
        "• `/alert <price>` — Set level rejection & false breakout alert (e.g. `/alert 2740`)\n"
        "• `/alert 2green` — Toggle 2 consecutive green candles alert (On/Off)\n"
        "• `/alert 2red` — Toggle 2 consecutive red candles alert (On/Off)\n"
        "• `/alerts` — List and manage pending price alerts\n"
        "• `/clearalerts` — Remove all pending price alerts\n"
        "• `/candle` — View live forming bar and last closed bar\n"
        "• `/status` — View current bot and alert settings"
    )
    if update.message:
        await update.message.reply_text(
            welcome_text,
            reply_markup=get_keyboard(),
            parse_mode="Markdown"
        )

@authorized_only
async def alert_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "❌ *Usage:*\n"
            "• `/alert 2740.00` (Set Level Rejection & False Breakout alert)\n"
            "• `/alert 2710.00 support` (Explicit support level)\n"
            "• `/alert 2750.00 resistance` (Explicit resistance level)\n"
            "• `/alert 2green` (Toggle 2-green momentum alert)\n"
            "• `/alert 2red` (Toggle 2-red momentum alert)",
            parse_mode="Markdown"
        )
        return

    first_arg = args[0].lower()
    if first_arg in ("2green", "green", "2g"):
        is_enabled = alert_manager.toggle_setting("two_green_candles")
        status_str = "ON 🟢" if is_enabled else "OFF ⚪"
        await update.message.reply_text(
            f"🟢🟢 *2 Consecutive Green Candles Alert:* `{status_str}`\n"
            f"• *Timeframe*: `{config.state.current_timeframe}`\n"
            f"• *Mode*: One-shot (switches to OFF after 1 notification)\n"
            f"• *Streak Guard*: Only alerts once on a fresh 2-green sequence.\n"
            f"{'Alert will trigger when two green candles close consecutively, then turn OFF.' if is_enabled else 'Alert is now muted.'}",
            reply_markup=get_keyboard(),
            parse_mode="Markdown"
        )
        return
    elif first_arg in ("2red", "red", "2r"):
        is_enabled = alert_manager.toggle_setting("two_red_candles")
        status_str = "ON 🔴" if is_enabled else "OFF ⚪"
        await update.message.reply_text(
            f"🔴🔴 *2 Consecutive Red Candles Alert:* `{status_str}`\n"
            f"• *Timeframe*: `{config.state.current_timeframe}`\n"
            f"• *Mode*: One-shot (switches to OFF after 1 notification)\n"
            f"• *Streak Guard*: Only alerts once on a fresh 2-red sequence.\n"
            f"{'Alert will trigger when two red candles close consecutively, then turn OFF.' if is_enabled else 'Alert is now muted.'}",
            reply_markup=get_keyboard(),
            parse_mode="Markdown"
        )
        return

    price_str = args[0]
    explicit_side = None
    if len(args) >= 2:
        side_candidate = args[1].lower()
        if side_candidate in ("resistance", "upper", "res"):
            explicit_side = "resistance"
        elif side_candidate in ("support", "lower", "sup"):
            explicit_side = "support"

    try:
        price_val = float(price_str.replace("$", "").replace(",", ""))
    except ValueError:
        await update.message.reply_text(f"❌ Invalid price format: `{price_str}`", parse_mode="Markdown")
        return

    # Fetch latest price for immediate context and automatic side resolution
    latest = await feed.get_latest_candle(config.state.current_timeframe)
    current_price_val = latest["close"] if latest else None
    current_price_str = f"${current_price_val:,.2f}" if current_price_val else "N/A"

    alert = alert_manager.add_price_alert(
        target_price=price_val,
        current_price=current_price_val,
        side=explicit_side
    )

    side_display = alert["side"].upper()
    side_icon = "🔺" if alert["side"] == "resistance" else "🔻"
    offset_note = f"\n• *OANDA Alignment*: `{config.OANDA_PRICE_OFFSET:+.2f}` offset applied" if config.OANDA_PRICE_OFFSET != 0.0 else ""

    msg = (
        f"🎯 *Level Rejection Alert #{alert['id']} Created* {side_icon}\n"
        f"• *Level*: `${alert['target_price']:,.2f}` (`{side_display}`)\n"
        f"• *Current Price*: `{current_price_str}`{offset_note}\n"
        f"• *Evaluation*: Every `{config.state.current_timeframe}` candle close\n\n"
        f"⚡ *Wick Touch*: Fires if a candle's wick touches `${alert['target_price']:,.2f}` and closes back inside.\n"
        f"🔄 *False Breakout*: If broken by body, fires ONLY if the immediate next candle closes in the opposite direction outside the zone.\n"
        f"❌ *Zone Breach*: If broken by body and next candle does not reject, zone is breached and alert is discarded silently."
    )
    await update.message.reply_text(msg, reply_markup=get_keyboard(), parse_mode="Markdown")

@authorized_only
async def alerts_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    text, markup = build_alerts_list_view()
    await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

@authorized_only
async def clear_alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    count = alert_manager.clear_all_price_alerts()
    await update.message.reply_text(
        f"🗑 Cleared all *{count}* active price alerts.",
        reply_markup=get_keyboard(),
        parse_mode="Markdown"
    )

def build_alerts_list_view():
    alerts = alert_manager.get_active_price_alerts()
    two_green_status = "🟢 ON (One-Shot)" if alert_manager.is_setting_enabled("two_green_candles") else "⚪ OFF"
    two_red_status = "🔴 ON (One-Shot)" if alert_manager.is_setting_enabled("two_red_candles") else "⚪ OFF"

    header = (
        f"⚙️ *Momentum Alert Settings:*\n"
        f"• 2-Green Alert: `{two_green_status}`\n"
        f"• 2-Red Alert: `{two_red_status}`\n"
        f"• Timeframe: `{config.state.current_timeframe}`\n\n"
    )

    if not alerts:
        text = header + "🎯 *Active Price Alerts: None*\nUse `/alert <price>` to set a level rejection alert."
        markup = get_keyboard()
        return text, markup

    text = header + f"🎯 *Active Price Alerts ({len(alerts)}):*\n"
    buttons = []
    for a in alerts:
        side_tag = a.get("side", "resistance").upper()
        state_tag = "ACTIVE" if a.get("state") == "ACTIVE" else "⏳ AWAITING 2ND BAR"
        text += f"• *#{a['id']}*: `{side_tag}` `${a['target_price']:,.2f}` — _{state_tag}_ (Set at {a['created_at']})\n"
        buttons.append([
            InlineKeyboardButton(f"❌ Cancel #{a['id']} (${a['target_price']:,.2f})", callback_data=f"cancel_alert_{a['id']}")
        ])

    buttons.append([InlineKeyboardButton("🗑 Clear All Alerts", callback_data="clear_all_alerts")])
    buttons.append([InlineKeyboardButton("🔙 Back to Main Menu", callback_data="btn_main_menu")])
    return text, InlineKeyboardMarkup(buttons)

@authorized_only
async def candle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    text = await build_candle_view()
    await update.message.reply_text(text, reply_markup=get_keyboard(), parse_mode="Markdown")

async def build_candle_view() -> str:
    tf = config.state.current_timeframe
    closed = await feed.get_latest_candle(tf)
    forming = await feed.get_forming_candle(tf)

    parts = []
    if forming:
        parts.append(feed.format_forming_candle_message(forming, tf))

    if closed:
        parts.append(f"🏁 *Last Closed Bar:*\n{feed.format_candle_message(closed, tf)}")

    if not parts:
        return "❌ Could not fetch candle data at this moment."

    return "\n\n".join(parts)

@authorized_only
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    text = build_status_text()
    await update.message.reply_text(text, reply_markup=get_keyboard(), parse_mode="Markdown")

def build_status_text() -> str:
    wait_sec = calculate_next_wait_seconds(config.state.current_timeframe)
    m, s = divmod(wait_sec, 60)
    two_green = "ON (One-Shot)" if alert_manager.is_setting_enabled("two_green_candles") else "OFF"
    two_red = "ON (One-Shot)" if alert_manager.is_setting_enabled("two_red_candles") else "OFF"
    active_alerts_cnt = len(alert_manager.get_active_price_alerts())
    offset_str = f"{config.OANDA_PRICE_OFFSET:+.2f} (TradingView OANDA aligned)" if config.OANDA_PRICE_OFFSET != 0.0 else "None (Raw Feed)"

    return (
        f"📊 *Bot Status*\n"
        f"• *Uptime*: {config.state.get_uptime_str()}\n"
        f"• *Timeframe*: `{config.state.current_timeframe}`\n"
        f"• *OANDA Price Offset*: `{offset_str}`\n"
        f"• *2-Green Momentum Alert*: `{two_green}`\n"
        f"• *2-Red Momentum Alert*: `{two_red}`\n"
        f"• *Active Price Targets*: `{active_alerts_cnt}`\n"
        f"• *Feed Source*: {config.state.last_feed_source}\n"
        f"• *Next Candle Check*: in {m}m {s}s"
    )

@authorized_only
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()
    data = query.data

    if data == "btn_latest":
        text = await build_candle_view()
        await query.edit_message_text(text=text, reply_markup=get_keyboard(), parse_mode="Markdown")

    elif data == "btn_tf":
        # Timeframe selector removed from active UI; refresh to new keyboard
        await query.edit_message_reply_markup(reply_markup=get_keyboard())

    elif data == "btn_two_green":
        alert_manager.toggle_setting("two_green_candles")
        await query.edit_message_reply_markup(reply_markup=get_keyboard())

    elif data == "btn_two_red":
        alert_manager.toggle_setting("two_red_candles")
        await query.edit_message_reply_markup(reply_markup=get_keyboard())

    elif data == "btn_alerts_list":
        text, markup = build_alerts_list_view()
        await query.edit_message_text(text=text, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("cancel_alert_"):
        alert_id_str = data.replace("cancel_alert_", "")
        try:
            alert_id = int(alert_id_str)
            alert_manager.remove_price_alert(alert_id)
        except ValueError:
            pass
        text, markup = build_alerts_list_view()
        await query.edit_message_text(text=text, reply_markup=markup, parse_mode="Markdown")

    elif data == "clear_all_alerts":
        alert_manager.clear_all_price_alerts()
        text, markup = build_alerts_list_view()
        await query.edit_message_text(text=text, reply_markup=markup, parse_mode="Markdown")

    elif data == "btn_main_menu":
        welcome_text = (
            "🟡 *XAU/USD Multi-Alert Monitor*\n"
            "Active monitoring engine with price & candle momentum alerts."
        )
        await query.edit_message_text(text=welcome_text, reply_markup=get_keyboard(), parse_mode="Markdown")

    elif data == "btn_status":
        text = build_status_text()
        await query.edit_message_text(text=text, reply_markup=get_keyboard(), parse_mode="Markdown")

async def candle_scheduler(app: Application):
    logger.info("Candle scheduler background loop started.")
    last_green_alert_candle_id = ""
    last_red_alert_candle_id = ""

    while True:
        try:
            wait_sec = calculate_next_wait_seconds(config.state.current_timeframe)
            logger.info(f"Scheduler waiting {wait_sec}s for next {config.state.current_timeframe} candle close buffer...")

            try:
                await asyncio.wait_for(config.state.timeframe_changed_event.wait(), timeout=wait_sec)
                config.state.timeframe_changed_event.clear()
                logger.info(f"Timeframe switched to {config.state.current_timeframe}. Recalculating next boundary.")
                continue
            except asyncio.TimeoutError:
                pass

            tf = config.state.current_timeframe
            closed_candles = await feed.get_latest_closed_candles(tf, count=3)
            if not closed_candles:
                logger.warning("No closed candles returned from feed.")
                continue

            latest_candle = closed_candles[-1]
            sent_alert = False
            candle_id = f"{tf}_{latest_candle['timestamp']}"

            if candle_id == config.state.last_alerted_candle_time:
                logger.debug(f"Candle {candle_id} already processed, skipping duplicate.")
                continue

            config.state.last_alerted_candle_time = candle_id

            # 1. Check Concurrent Level Rejection & False Breakout Alerts
            interval_sec = config.get_interval_seconds(tf)
            triggered_alerts = alert_manager.check_price_alerts(latest_candle, interval_seconds=interval_sec)
            for alert in triggered_alerts:
                t_type = alert.get("trigger_type", "WICK_TOUCH")
                side_str = alert.get("side", "resistance").capitalize()
                target_p = alert["target_price"]

                if t_type == "WICK_TOUCH":
                    alert_msg = (
                        f"⚡ *XAU/USD [{tf.upper()}] Level Rejection: Wick Touch!*\n"
                        f"• *Level*: `${target_p:,.2f}` ({side_str})\n"
                        f"• *Candle Range*: `${latest_candle['low']:,.2f}` ➔ `${latest_candle['high']:,.2f}`\n"
                        f"• *Close*: `${latest_candle['close']:,.2f}` ({latest_candle['sign']}{latest_candle['pips']:.1f} pips {latest_candle['emoji']}) | `{latest_candle['time_str']}`\n"
                        f"• *Action*: Wick tested level and body closed strictly inside."
                    )
                else:  # FALSE_BREAKOUT
                    b_candle = alert.get("breakout_candle") or {}
                    b_color = "Green 🟢" if b_candle.get("is_green") else "Red 🔴"
                    r_color = "Green 🟢" if latest_candle.get("is_green") else "Red 🔴"
                    alert_msg = (
                        f"🔄 *XAU/USD [{tf.upper()}] False Breakout (Sweep) Rejection!*\n"
                        f"• *Level*: `${target_p:,.2f}` ({side_str})\n"
                        f"• *Bar 1 (Breakout)*: {b_color} close at `${b_candle.get('close', 0):,.2f}` | `{b_candle.get('time_str', 'N/A')}`\n"
                        f"• *Bar 2 (Rejection)*: Immediate {r_color} close at `${latest_candle['close']:,.2f}` ({latest_candle['sign']}{latest_candle['pips']:.1f} pips {latest_candle['emoji']}) | `{latest_candle['time_str']}`\n"
                        f"• *Action*: Level was broken with body, but immediate opposite candle rejected back outside zone!"
                    )

                logger.info(f"Dispatching rejection alert: {alert_msg}")
                await app.bot.send_message(
                    chat_id=config.ALLOWED_USER_ID,
                    text=alert_msg,
                    parse_mode="Markdown"
                )
                sent_alert = True

            # 2. Check Two Consecutive Green Candles Alert (Streak Deduplication & One-Shot Auto-Reset)
            if alert_manager.is_setting_enabled("two_green_candles") and len(closed_candles) >= 2:
                c1 = closed_candles[-2]
                c2 = closed_candles[-1]
                if c1.get("is_green") and c2.get("is_green"):
                    is_fresh_streak = True
                    if len(closed_candles) >= 3:
                        c0 = closed_candles[-3]
                        if c0.get("is_green"):
                            is_fresh_streak = False

                    if is_fresh_streak and candle_id != last_green_alert_candle_id:
                        last_green_alert_candle_id = candle_id
                        alert_manager.set_setting("two_green_candles", False)
                        green_msg = (
                            f"🟢🟢 *XAU/USD [{tf.upper()}] Bullish Momentum: 2 Consecutive Green Closes!*\n"
                            f"• *Bar 1*: `${c1['open']:,.2f}` ➔ `${c1['close']:,.2f}` ({c1['sign']}{c1['pips']:.1f} pips 🟢) | `{c1['time_str']}`\n"
                            f"• *Bar 2*: `${c2['open']:,.2f}` ➔ `${c2['close']:,.2f}` ({c2['sign']}{c2['pips']:.1f} pips 🟢) | `{c2['time_str']}`\n\n"
                            f"ℹ️ _One-shot complete: 2-Green alert is now turned OFF._"
                        )
                        logger.info(f"Dispatching 2-green alert: {green_msg}")
                        await app.bot.send_message(
                            chat_id=config.ALLOWED_USER_ID,
                            text=green_msg,
                            parse_mode="Markdown"
                        )
                        sent_alert = True

            # 3. Check Two Consecutive Red Candles Alert (Streak Deduplication & One-Shot Auto-Reset)
            if alert_manager.is_setting_enabled("two_red_candles") and len(closed_candles) >= 2:
                c1 = closed_candles[-2]
                c2 = closed_candles[-1]
                is_c1_red = not c1.get("is_green") and c1.get("pips", 0) != 0
                is_c2_red = not c2.get("is_green") and c2.get("pips", 0) != 0
                if is_c1_red and is_c2_red:
                    is_fresh_streak = True
                    if len(closed_candles) >= 3:
                        c0 = closed_candles[-3]
                        if not c0.get("is_green") and c0.get("pips", 0) != 0:
                            is_fresh_streak = False

                    if is_fresh_streak and candle_id != last_red_alert_candle_id:
                        last_red_alert_candle_id = candle_id
                        alert_manager.set_setting("two_red_candles", False)
                        red_msg = (
                            f"🔴🔴 *XAU/USD [{tf.upper()}] Bearish Momentum: 2 Consecutive Red Closes!*\n"
                            f"• *Bar 1*: `${c1['open']:,.2f}` ➔ `${c1['close']:,.2f}` ({c1['sign']}{c1['pips']:.1f} pips 🔴) | `{c1['time_str']}`\n"
                            f"• *Bar 2*: `${c2['open']:,.2f}` ➔ `${c2['close']:,.2f}` ({c2['sign']}{c2['pips']:.1f} pips 🔴) | `{c2['time_str']}`\n\n"
                            f"ℹ️ _One-shot complete: 2-Red alert is now turned OFF._"
                        )
                        logger.info(f"Dispatching 2-red alert: {red_msg}")
                        await app.bot.send_message(
                            chat_id=config.ALLOWED_USER_ID,
                            text=red_msg,
                            parse_mode="Markdown"
                        )
                        sent_alert = True

            # One shared visual for all alerts on this candle; text always goes first.
            if sent_alert:
                await send_alert_chart(app.bot, tf, latest_candle['time_str'], config.state.last_feed_source)

        except asyncio.CancelledError:
            logger.info("Candle scheduler cancelled.")
            break
        except Exception as e:
            logger.error(f"Error in candle scheduler: {e}", exc_info=True)
            await asyncio.sleep(5)

async def post_init(app: Application):
    await feed.get_latest_candle(config.state.current_timeframe)
    asyncio.create_task(candle_scheduler(app))
    try:
        await app.bot.send_message(
            chat_id=config.ALLOWED_USER_ID,
            text="🟢 *XAU/USD Alert Engine is Online*\nAll candle spam is disabled. Use `/alert 2green` or the buttons to place alerts.",
            reply_markup=get_keyboard(),
            parse_mode="Markdown"
        )
        logger.info(f"Sent startup greeting to user {config.ALLOWED_USER_ID}")
    except Exception as e:
        logger.warning(f"Failed to send startup message: {e}")

def main():
    logger.info("Starting XAU/USD Telegram Bot daemon with multi-alert engine...")
    app = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("alert", alert_command))
    app.add_handler(CommandHandler("alerts", alerts_list_command))
    app.add_handler(CommandHandler("clearalerts", clear_alerts_command))
    app.add_handler(CommandHandler("candle", candle_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CallbackQueryHandler(button_callback))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
