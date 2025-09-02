# -- coding: utf-8 --
"""
بوت مواقيت الصلاة واتجاه القبلة - نسخة مطورة مع تنبيهات
"""

import os
import math
import requests
import telebot
import pytz
import difflib
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from timezonefinder import TimezoneFinder
from datetime import datetime, timedelta

#--- ضع توكن البوت هنا ---
TOKEN = "8083563853:AAEcoB-6PMFAL3kkMaa2ICusyrkmlnM4f5k"

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

#📌 قاعدة بيانات محلية مختصرة (قابلة للتوسيع)
OFFLINE_DB = {
    "Algeria": {
        "Algiers": {"lat": 36.75, "lon": 3.06},
        "Oran": {"lat": 35.691, "lon": -0.641},
        "Constantine": {"lat": 36.365, "lon": 6.614},
    },
    "Egypt": {
        "Cairo": {"lat": 30.0444, "lon": 31.2357},
        "Alexandria": {"lat": 31.2001, "lon": 29.9187},
    },
    "Saudi Arabia": {
        "Makkah": {"lat": 21.389, "lon": 39.857},
        "Medina": {"lat": 24.5247, "lon": 39.5692},
        "Riyadh": {"lat": 24.7136, "lon": 46.6753},
    }
}

#🔔 مستخدمين مفعلين للتذكير
reminder_users = set()

#--- لوحة البداية ---
def get_start_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("📍 إرسال موقعي", request_location=True))
    kb.add(KeyboardButton("🔎 البحث باسم المدينة"))
    kb.add(KeyboardButton("🔔 تفعيل التنبيهات"), KeyboardButton("🔕 إيقاف التنبيهات"))
    return kb

#--- البحث في القاعدة المحلية ---
def lookup_offline(query):
    """
    يدور فـ OFFLINE_DB على المدينة ويعطي إحداثياتها إذا لقاها.
    """
    if not query: 
        return None
    q = query.strip().lower()
    for country, cities in OFFLINE_DB.items():
        # هنا cities هو dict: city_name -> {"lat":..., "lon":...}
        for city, coords in cities.items():
            if q == city.lower():
                return {"lat": coords["lat"], "lon": coords["lon"], "display_name": f"{city}, {country}"}
    return None

#--- geocoding من Nominatim ---
def geocode_place(place_name):
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {"q": place_name, "format": "json", "limit": 3}
        r = requests.get(url, params=params, headers={"User-Agent": "PrayerBot/2.0"}, timeout=10)
        data = r.json()
        if not data:
            return None
        return [{"lat": float(p["lat"]), "lon": float(p["lon"]), "display_name": p.get("display_name", "")} for p in data]
    except Exception:
        return None

#--- مواقيت الصلاة ---
def get_prayer_timings(lat, lon, method=5):
    try:
        url = "http://api.aladhan.com/v1/timings"
        r = requests.get(url, params={"latitude": lat, "longitude": lon, "method": method}, timeout=10)
        return r.json().get("data")
    except Exception:
        return None

#--- اتجاه القبلة ---
def get_qibla_direction(lat, lon):
    try:
        url = f"http://api.aladhan.com/v1/qibla/{lat}/{lon}"
        r = requests.get(url, timeout=10).json()
        return float(r["data"]["direction"])
    except Exception:
        return None

#--- المنطقة الزمنية ---
def get_local_time(lat, lon):
    tf = TimezoneFinder()
    # timezone_at expects lng, lat
    tzname = tf.timezone_at(lng=lon, lat=lat)
    if not tzname:
        tzname = "UTC"
    tz = pytz.timezone(tzname)
    return datetime.now(tz), tzname

#--- تحديد الصلاة القادمة ---
def get_next_prayer(timings, now):
    order = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]
    fmt = "%H:%M"
    try:
        for prayer in order:
            t = datetime.strptime(timings[prayer], fmt).time()
            if now.time() < t:
                remain = (datetime.combine(now.date(), t) - now).seconds // 60
                return prayer, timings[prayer], remain
        # لو خلصت كلها → فجر بكرة
        t = datetime.strptime(timings["Fajr"], fmt).time()
        remain = ((datetime.combine(now.date() + timedelta(days=1), t) - now).seconds) // 60
        return "Fajr", timings["Fajr"], remain
    except Exception:
        return None, None, None

#--- الترحيب ---
@bot.message_handler(commands=["start"])
def send_welcome(msg):
    bot.send_message(
        msg.chat.id,
        "🌙 <b>أهلاً بك في بوت مواقيت الصلاة</b> 🕌\n"
        "📍 أرسل موقعك أو اكتب اسم مدينتك.\n"
        "🔔 يمكنك تفعيل أو إيقاف التنبيهات.",
        reply_markup=get_start_keyboard()
    )

#--- استقبال الموقع ---
@bot.message_handler(content_types=["location"])
def handle_location(message):
    lat, lon = message.location.latitude, message.location.longitude
    show_prayer_info(message.chat.id, lat, lon, "موقعك الحالي")

#--- استقبال النصوص ---
@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(message):
    txt = message.text.strip()

    if txt == "🔔 تفعيل التنبيهات":
        reminder_users.add(message.chat.id)
        bot.send_message(message.chat.id, "✅ تم تفعيل التنبيهات.")
        return

    if txt == "🔕 إيقاف التنبيهات":
        reminder_users.discard(message.chat.id)
        bot.send_message(message.chat.id, "❎ تم إيقاف التنبيهات.")
        return

    offline = lookup_offline(txt)
    if offline:
        show_prayer_info(message.chat.id, offline["lat"], offline["lon"], offline["display_name"])
        return

    results = geocode_place(txt)
    if not results:
        bot.send_message(message.chat.id, "⚠️ لم أجد هذه المدينة.")
        return

    if len(results) == 1:
        res = results[0]
        show_prayer_info(message.chat.id, res["lat"], res["lon"], res["display_name"])
    else:
        # اقترح أقرب 3
        names = "\n".join([f"▫️ {r['display_name']}" for r in results])
        bot.send_message(message.chat.id, f"🔍 هل تقصد أحد هذه الأماكن؟\n{names}")

#--- عرض النتائج ---
def show_prayer_info(chat_id, lat, lon, place_name):
    timings_data = get_prayer_timings(lat, lon)
    if not timings_data:
        bot.send_message(chat_id, "❌ خطأ في جلب مواقيت الصلاة. حاول لاحقًا.")
        return

    q_angle = get_qibla_direction(lat, lon)
    now_local, tzname = get_local_time(lat, lon)

    timings = timings_data["timings"]
    date_hijri = timings_data["date"]["hijri"]["date"]
    date_greg = timings_data["date"]["gregorian"]["date"]

    next_p, next_t, remain = get_next_prayer(timings, now_local)

    text = (
        f"🕌 <b>مواقيت الصلاة</b>\n"
        f"📍 <i>{place_name}</i>\n"
        f"📅 هجري: {date_hijri} | ميلادي: {date_greg}\n"
        f"🕰 الوقت المحلي: {now_local.strftime('%H:%M:%S')} ({tzname})\n\n"
        f"🌅 الفجر: {timings.get('Fajr')}\n"
        f"☀️ الشروق: {timings.get('Sunrise')}\n"
        f"🏙️ الظهر: {timings.get('Dhuhr')}\n"
        f"🌇 العصر: {timings.get('Asr')}\n"
        f"🌆 المغرب: {timings.get('Maghrib')}\n"
        f"🌙 العشاء: {timings.get('Isha')}\n\n"
        f"⏭ الصلاة القادمة: <b>{next_p}</b> عند {next_t}\n"
        f"⏳ متبقي: {remain} دقيقة\n\n"
        f"🧭 اتجاه القبلة: {q_angle:.2f}°" if q_angle is not None else "\n🧭 اتجاه القبلة: غير متوفر"
    )
    bot.send_message(chat_id, text)

#--- تشغيل البوت ---
if __name__ == "__main__":
    try:
        bot.remove_webhook()
    except Exception:
        pass
    print("✅ البوت يعمل الآن...")
    bot.polling(non_stop=True)