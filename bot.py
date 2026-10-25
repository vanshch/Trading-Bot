import asyncio
import time
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, NetworkError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import config
import feed
from alerts import alert_manager
from config import logger
from hourly_volume import SETTING_LABEL, check_hourly_volume
from manual_charts import request_chart
from screenshots import send_alert_chart


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
    volume = "ON" if alert_manager.is_hourly_volume_enabled() else "OFF"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Live & Closed Bar", callback_data="btn_latest")],
        [InlineKeyboardButton("📸 Screenshot Now", callback_data="btn_screenshot"),
         InlineKeyboardButton("🕐 1H Chart + Volume", callback_data="btn_hourly_chart")],
        [InlineKeyboardButton(f"🔊 {SETTING_LABEL}: {volume}", callback_data="btn_hourly_volume")],
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
        "🟡 *XAU/USD Alert Monitor*\n"
        "Real-time gold level rejection & momentum alerts.\n\n"
        "💡 *Quick Commands:*\n"
        "• `/alert <price>` — Set level alert (e.g. `/alert 2740`)\n"
        "• `/alert 2green` | `/alert 2red` — Toggle 2-candle momentum\n"
        "• `/alerts` — View & manage active alerts\n"
        "• `/clearalerts` — Cancel all price alerts\n"
        "• `/candle` — Live forming & last closed bar\n"
        "• `/chart1h` — 20-bar 1H chart with tick volume\n"
        "• `/screenshot` — Live TradingView snapshot\n"
        "• `/volume1h` — Toggle 1H volume spike alarm\n"
        "• `/status` — Bot status & active settings"
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
            "• `/alert 2740` (Level Rejection alert)\n"
            "• `/alert 2710 support` (Explicit support)\n"
            "• `/alert 2750 resistance` (Explicit resistance)\n"
            "• `/alert 2green` (Toggle 2-green momentum)\n"
            "• `/alert 2red` (Toggle 2-red momentum)",
            parse_mode="Markdown"
        )
        return

    first_arg = args[0].lower()
    if first_arg in ("2green", "green", "2g"):
        is_enabled = alert_manager.toggle_setting("two_green_candles")
        status_str = "ON 🟢" if is_enabled else "OFF ⚪"
        await update.message.reply_text(
            f"🟢🟢 *2-Green Alert:* `{status_str}`\n"
            f"• *Timeframe*: `{config.state.current_timeframe}` (one-shot)\n"
            f"{'Will trigger when two green candles close consecutively.' if is_enabled else 'Alert muted.'}",
            reply_markup=get_keyboard(),
            parse_mode="Markdown"
        )
        return
    elif first_arg in ("2red", "red", "2r"):
        is_enabled = alert_manager.toggle_setting("two_red_candles")
        status_str = "ON 🔴" if is_enabled else "OFF ⚪"
        await update.message.reply_text(
            f"🔴🔴 *2-Red Alert:* `{status_str}`\n"
            f"• *Timeframe*: `{config.state.current_timeframe}` (one-shot)\n"
            f"{'Will trigger when two red candles close consecutively.' if is_enabled else 'Alert muted.'}",
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
    offset_note = f"\n• *Twelve Data Offset*: `{config.OANDA_PRICE_OFFSET:+.2f}`" if latest and latest.get("source", "").startswith("Twelve Data") and config.OANDA_PRICE_OFFSET != 0.0 else ""

    msg = (
        f"🎯 *Level Alert #{alert['id']} Set* {side_icon}\n"
        f"• *Level*: `${alert['target_price']:,.2f}` (`{side_display}`)\n"
        f"• *Current*: `{current_price_str}`{offset_note}\n"
        f"• *Timeframe*: `{config.state.current_timeframe}` close\n"
        f"_Triggers on wick touch or sweep rejection._"
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
        f"⚙️ *Alert Settings [{config.state.current_timeframe}]:*\n"
        f"• 2-Green: `{two_green_status}` · 2-Red: `{two_red_status}`\n"
        f"• 1H Vol Spike: `{'ON (One-Shot)' if alert_manager.is_hourly_volume_enabled() else 'OFF'}`\n\n"
    )

    if not alerts:
        text = header + "🎯 *Active Price Alerts: None*\nUse `/alert <price>` to set an alert."
        markup = get_keyboard()
        return text, markup

    text = header + f"🎯 *Active Price Alerts ({len(alerts)}):*\n"
    buttons = []
    for a in alerts:
        side_tag = a.get("side", "resistance").upper()
        state_tag = "ACTIVE" if a.get("state") == "ACTIVE" else "⏳ AWAITING BAR 2"
        text += f"• *#{a['id']}*: `{side_tag}` `${a['target_price']:,.2f}` — _{state_tag}_\n"
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
        parts.append(feed.format_candle_message(closed, tf))

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
    two_green = "🟢 ON" if alert_manager.is_setting_enabled("two_green_candles") else "⚪ OFF"
    two_red = "🔴 ON" if alert_manager.is_setting_enabled("two_red_candles") else "⚪ OFF"
    vol_status = "🔊 ON" if alert_manager.is_hourly_volume_enabled() else "⚪ OFF"
    active_alerts_cnt = len(alert_manager.get_active_price_alerts())
    offset_str = f" ({config.OANDA_PRICE_OFFSET:+.2f})" if config.OANDA_PRICE_OFFSET != 0.0 else ""

    return (
        f"📊 *Bot Status*\n"
        f"• *Timeframe*: `{config.state.current_timeframe}` · *Next Bar*: in `{m}m {s}s`\n"
        f"• *Feed*: {config.state.last_feed_source}{offset_str}\n"
        f"• *Uptime*: {config.state.get_uptime_str()}\n\n"
        f"🎯 *Active Triggers:*\n"
        f"• Price Targets: `{active_alerts_cnt} active`\n"
        f"• 2-Green: `{two_green}` · 2-Red: `{two_red}`\n"
        f"• 1H Volume ≥120% MA20: `{vol_status}`"
    )

@authorized_only
async def screenshot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await request_chart(context, "screenshot", config.state.current_timeframe)


@authorized_only
async def hourly_chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await request_chart(context, "hourly", "1h")


def toggle_hourly_volume_text() -> str:
    enabled = alert_manager.toggle_hourly_volume()
    status = "ON" if enabled else "OFF"
    return (
        f"🔊 *Hourly High-Volume Alarm: {status}*\n"
        f"{'Armed: will trigger once when a 1H candle closes with volume ≥120% of 20h average.' if enabled else 'Hourly volume alarm is now muted.'}"
    )


@authorized_only
async def hourly_volume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(
            toggle_hourly_volume_text(), reply_markup=get_keyboard(), parse_mode="Markdown"
        )


@authorized_only
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    try:
        await query.answer()
    except BadRequest as exc:
        # The click is still actionable even if its acknowledgment expired.
        if not any(marker in str(exc).lower() for marker in (
            "query is too old", "query id is invalid",
        )):
            raise
        logger.warning("Callback acknowledgment expired; processing authorized action")
    except NetworkError as exc:
        logger.warning("Callback acknowledgment unavailable (%s); processing authorized action",
                       type(exc).__name__)
    data = query.data

    if data == "btn_screenshot":
        await request_chart(context, "screenshot", config.state.current_timeframe)

    elif data == "btn_hourly_chart":
        await request_chart(context, "hourly", "1h")

    elif data == "btn_hourly_volume":
        await query.edit_message_text(
            text=toggle_hourly_volume_text(), reply_markup=get_keyboard(), parse_mode="Markdown"
        )

    elif data == "btn_latest":
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
            "🟡 *XAU/USD Alert Monitor*\n"
            "Real-time gold level rejection & momentum alerts."
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

            last_id = config.state.last_alerted_candle_time
            previous_ts = int(last_id.split("_", 1)[1]) if last_id.startswith(tf + "_") else -1
            if int(latest_candle['timestamp']) <= previous_ts:
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
                        f"⚡ *WICK REJECTION · XAU/USD [{tf.upper()}]*\n"
                        f"• *Level*: `${target_p:,.2f}` ({side_str})\n"
                        f"• *Closed*: `${latest_candle['close']:,.2f}` ({latest_candle['sign']}{latest_candle['pips']:.1f} pips {latest_candle['emoji']})\n"
                        f"• *Candle Wick*: `${latest_candle['low']:,.2f}` ➔ `${latest_candle['high']:,.2f}`\n"
                        f"• *Time*: `{latest_candle['time_str']}`"
                    )
                else:  # FALSE_BREAKOUT
                    b_candle = alert.get("breakout_candle") or {}
                    b_color = "Green 🟢" if b_candle.get("is_green") else "Red 🔴"
                    r_color = "Green 🟢" if latest_candle.get("is_green") else "Red 🔴"
                    alert_msg = (
                        f"🔄 *SWEEP REJECTION · XAU/USD [{tf.upper()}]*\n"
                        f"• *Level*: `${target_p:,.2f}` ({side_str})\n"
                        f"• *Breakout*: {b_color} `${b_candle.get('close', 0):,.2f}` (`{b_candle.get('time_str', 'N/A')}`)\n"
                        f"• *Rejection*: {r_color} `${latest_candle['close']:,.2f}` ({latest_candle['sign']}{latest_candle['pips']:.1f} pips {latest_candle['emoji']}) (`{latest_candle['time_str']}`)\n"
                        f"_False breakout confirmed back inside zone._"
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
                            f"🟢🟢 *BULLISH MOMENTUM · 2 GREEN [{tf.upper()}]*\n"
                            f"1️⃣ `${c1['open']:,.2f}` ➔ `${c1['close']:,.2f}` ({c1['sign']}{c1['pips']:.1f} pips 🟢) · `{c1['time_str']}`\n"
                            f"2️⃣ `${c2['open']:,.2f}` ➔ `${c2['close']:,.2f}` ({c2['sign']}{c2['pips']:.1f} pips 🟢) · `{c2['time_str']}`\n\n"
                            f"ℹ️ _Alert triggered & disarmed (one-shot)._"
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
                            f"🔴🔴 *BEARISH MOMENTUM · 2 RED [{tf.upper()}]*\n"
                            f"1️⃣ `${c1['open']:,.2f}` ➔ `${c1['close']:,.2f}` ({c1['sign']}{c1['pips']:.1f} pips 🔴) · `{c1['time_str']}`\n"
                            f"2️⃣ `${c2['open']:,.2f}` ➔ `${c2['close']:,.2f}` ({c2['sign']}{c2['pips']:.1f} pips 🔴) · `{c2['time_str']}`\n\n"
                            f"ℹ️ _Alert triggered & disarmed (one-shot)._"
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

async def hourly_volume_scheduler(app: Application, *, started_at: float):
    while True:
        try:
            await asyncio.sleep(calculate_next_wait_seconds("1h"))
            await check_hourly_volume(app.bot, alert_manager, started_at=started_at)
        except asyncio.CancelledError:
            break
        except Exception as exc:  # noqa: BLE001 - isolate this optional scheduler; redact credentials
            # Telegram/provider errors may embed credentials; never print payloads.
            logger.warning("Hourly volume alarm check failed (%s)", type(exc).__name__)


async def post_init(app: Application):
    started_at = time.time()
    tf = config.state.current_timeframe
    latest = await feed.get_latest_candle(tf)
    if latest:
        # Existing history is startup context, not a fresh post-start alert.
        config.state.last_alerted_candle_time = f"{tf}_{latest['timestamp']}"
    asyncio.create_task(candle_scheduler(app))
    asyncio.create_task(hourly_volume_scheduler(app, started_at=started_at))
    try:
        await app.bot.send_message(
            chat_id=config.ALLOWED_USER_ID,
            text="🟢 *XAU/USD Alert Engine Online*\nReady for commands and alerts. Tap buttons below or use `/alert`.",
            reply_markup=get_keyboard(),
            parse_mode="Markdown"
        )
        logger.info(f"Sent startup greeting to user {config.ALLOWED_USER_ID}")
    except Exception as e:
        logger.warning(f"Failed to send startup message: {e}")

async def telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    # PTB already retries receive polling. Logging must not dump credential-bearing
    # request URLs, update bodies or exception payloads, nor claim a send succeeded.
    error = context.error
    operation = "polling" if update is None else "update"
    if isinstance(error, NetworkError) and not isinstance(error, BadRequest):
        logger.warning("Telegram %s network failure (%s)", operation, type(error).__name__)
    else:
        logger.error("Telegram %s handler failed (%s)", operation, type(error).__name__)


def main():
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is missing!")
        return

    logger.info("Starting XAU/USD Telegram Bot daemon with multi-alert engine...")
    app = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_error_handler(telegram_error_handler)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("alert", alert_command))
    app.add_handler(CommandHandler("alerts", alerts_list_command))
    app.add_handler(CommandHandler("clearalerts", clear_alerts_command))
    app.add_handler(CommandHandler("candle", candle_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("screenshot", screenshot_command))
    app.add_handler(CommandHandler("chart1h", hourly_chart_command))
    app.add_handler(CommandHandler("volume1h", hourly_volume_command))
    app.add_handler(CallbackQueryHandler(button_callback))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
