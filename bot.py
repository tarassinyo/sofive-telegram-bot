import logging
import datetime
import pytz
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, PollHandler, CallbackContext, PicklePersistence

# --- CONFIGURATION ---
TARGET_CHAT_ID_STR = os.getenv("TARGET_CHAT_ID")
TARGET_CHAT_ID = int(TARGET_CHAT_ID_STR) if TARGET_CHAT_ID_STR else 0
FORWARD_CHAT_ID_STR = os.getenv("FORWARD_CHAT_ID")
FORWARD_CHAT_ID = int(FORWARD_CHAT_ID_STR) if FORWARD_CHAT_ID_STR else 0
FORWARD_TOPIC_ID_STR = os.getenv("FORWARD_TOPIC_ID")
FORWARD_TOPIC_ID = int(FORWARD_TOPIC_ID_STR) if FORWARD_TOPIC_ID_STR else 0
TIMEZONE = "America/New_York"
# ---------------------

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)


# --- BOT FUNCTIONS ---

async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text('Привіт! Я бот для щотижневих опитувань.')

async def chatid(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    text = f"The Chat ID for this group is: `{chat_id}`"
    if update.message.is_topic_message and update.message.message_thread_id:
        topic_id = update.message.message_thread_id
        text += f"\n\nThe Message Thread ID for this topic is: `{topic_id}`"
    await update.message.reply_text(text, parse_mode='Markdown')

async def check_and_forward_poll(context: CallbackContext) -> None:
    job = context.job
    poll_id = job.data['poll_id']
    if poll_id in context.bot_data:
        if FORWARD_CHAT_ID == 0:
            return
        current_votes = context.bot_data[poll_id].get("plus_votes", 0)
        if current_votes < 15:
            await context.bot.forward_message(
                chat_id=FORWARD_CHAT_ID,
                from_chat_id=job.data['chat_id'],
                message_id=job.data['message_id'],
                message_thread_id=FORWARD_TOPIC_ID if FORWARD_TOPIC_ID != 0 else None
            )

async def create_and_send_poll(context: CallbackContext, chat_id: int):
    today = datetime.date.today()
    days_until_sunday = (6 - today.weekday() + 7) % 7 or 7
    next_sunday_date = today + datetime.timedelta(days=days_until_sunday)
    days_until_saturday = (5 - today.weekday() + 7) % 7 or 7
    next_saturday_date = today + datetime.timedelta(days=days_until_saturday)

    date_str = next_sunday_date.strftime("%m/%d")
    poll_title = f"Футбол. Неділя {date_str} 09:00am. Sofive - Поле #6"
    
    message = await context.bot.send_poll(
        chat_id=chat_id, question=poll_title, options=["+", "-"],
        is_anonymous=False, allows_multiple_answers=False
    )
    
    poll_id = message.poll.id
    context.bot_data[poll_id] = {"chat_id": chat_id, "message_id": message.message_id, "plus_votes": 0}

    local_tz = pytz.timezone(TIMEZONE)
    close_time_aware = local_tz.localize(datetime.datetime.combine(next_sunday_date, datetime.time(10, 0)))
    context.job_queue.run_once(auto_close_poll, when=close_time_aware, data={'poll_id': poll_id}, name=f"close_{poll_id}")

    forward_time_aware = local_tz.localize(datetime.datetime.combine(next_saturday_date, datetime.time(10, 0)))
    context.job_queue.run_once(check_and_forward_poll, when=forward_time_aware, data={'chat_id': chat_id, 'message_id': message.message_id, 'poll_id': poll_id}, name=f"forward_{poll_id}")

async def poll_command(update: Update, context: CallbackContext) -> None:
    await create_and_send_poll(context, update.effective_chat.id)

async def send_weekly_poll(context: CallbackContext) -> None:
    if datetime.date.today().weekday() != 3: return
    if TARGET_CHAT_ID == 0: return
    await create_and_send_poll(context, TARGET_CHAT_ID)

async def receive_poll_update(update: Update, context: CallbackContext) -> None:
    poll_id = update.poll.id
    if poll_id not in context.bot_data: return

    plus_votes = update.poll.options[0].voter_count
    context.bot_data[poll_id]["plus_votes"] = plus_votes

    if plus_votes >= 15:
        jobs_to_cancel = context.job_queue.get_jobs_by_name(f"close_{poll_id}") + context.job_queue.get_jobs_by_name(f"forward_{poll_id}")
        for job in jobs_to_cancel:
            job.schedule_removal()
        
        await context.bot.stop_poll(context.bot_data[poll_id]['chat_id'], context.bot_data[poll_id]['message_id'])
        del context.bot_data[poll_id]

async def auto_close_poll(context: CallbackContext) -> None:
    poll_id = context.job.data['poll_id']
    if poll_id in context.bot_data:
        poll_info = context.bot_data.pop(poll_id)
        await context.bot.stop_poll(poll_info['chat_id'], poll_info['message_id'])

def main() -> None:
    TOKEN = os.getenv("TELEGRAM_TOKEN")
    if not TOKEN:
        raise ValueError("No TELEGRAM_TOKEN found in environment variables")

    # Use PicklePersistence, an older but compatible method
    persistence = PicklePersistence(filepath="bot_data.pickle")

    application = Application.builder().token(TOKEN).persistence(persistence).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("poll", poll_command))
    application.add_handler(CommandHandler("chatid", chatid))
    application.add_handler(PollHandler(receive_poll_update))

    job_queue = application.job_queue
    job_queue.run_daily(
        callback=send_weekly_poll,
        time=datetime.time(hour=10, minute=0, second=0, tzinfo=pytz.timezone(TIMEZONE)),
        name="daily_poll_check"
    )

    application.run_polling()

if __name__ == '__main__':
    main()
