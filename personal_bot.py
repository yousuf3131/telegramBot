from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.helpers import escape_markdown
import json
import os
from datetime import datetime
import requests
from dotenv import load_dotenv
import math
from PIL import Image, ImageDraw, ImageFont
import pytesseract
from PyPDF2 import PdfMerger, PdfReader, PdfWriter
import io
from pathlib import Path
import qrcode
import whois
import socket
import dns.resolver
import re

# Load environment variables
load_dotenv()

# --- Airtable Setup ---
AIRTABLE_TOKEN = os.getenv("AIRTABLE_TOKEN")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE_NAME = "Expenses"
AIRTABLE_API_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_TOKEN}",
    "Content-Type": "application/json"
}

# --- Local Storage ---
if not os.path.exists("data"):
    os.mkdir("data")
if not os.path.exists("temp"):
    os.mkdir("temp")

EXPENSE_FILE = "data/expenses.json"
NOTES_FILE = "data/notes.json"
LOCATION_FILE = "data/location.json"
TEMP_DIR = "temp"

# --- Config ---
DEFAULT_LOCATION = {
    "city": "Dubai",
    "country": "United Arab Emirates",
    "latitude": 25.2048,
    "longitude": 55.2708,
    "method": 3  # Default method
}

CALCULATION_METHODS = {
    1: "Egyptian General Authority of Survey",
    2: "University of Islamic Sciences, Karachi",
    3: "Islamic Society of North America",
    4: "Muslim World League",
    5: "Umm Al-Qura University, Makkah",
    7: "Institute of Geophysics, University of Tehran",
    8: "Gulf Region",
    9: "Kuwait",
    10: "Qatar",
    11: "Majlis Ugama Islam Singapura, Singapore",
    12: "Union Organization islamic de France",
    13: "Diyanet İşleri Başkanlığı, Turkey",
    14: "Spiritual Administration of Muslims of Russia",
    15: "Moonsighting Committee Worldwide"
}

# --- API Keys ---
NUMVERIFY_API_KEY = os.getenv("NUMVERIFY_API_KEY")

def load_data(filename):
    if os.path.exists(filename):
        with open(filename, "r") as f:
            return json.load(f)
    return []

def save_data(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

def load_location():
    if os.path.exists(LOCATION_FILE):
        with open(LOCATION_FILE, "r") as f:
            return json.load(f)
    return DEFAULT_LOCATION

def save_location(location_data):
    with open(LOCATION_FILE, "w") as f:
        json.dump(location_data, f, indent=2)

# --- Telegram Commands ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        escape_markdown("📱 Personal Bot Commands:\n\n", version=2) +
        escape_markdown("💰 Expense Tracking:\n", version=2) +
        escape_markdown("/addexpense [amount] [description] – Add a new expense\n", version=2) +
        escape_markdown("/expenses – Show total + recent expenses\n\n", version=2) +
        escape_markdown("📝 Notes:\n", version=2) +
        escape_markdown("/addnote [note text] – Save a quick note\n", version=2) +
        escape_markdown("/notes – Show all saved notes\n", version=2) +
        escape_markdown("/clear_notes – Delete all notes\n\n", version=2) +
        escape_markdown("🕌 Prayer Times:\n", version=2) +
        escape_markdown("/prayer – Show today's prayer times\n", version=2) +
        escape_markdown("/next – Show next prayer time\n", version=2) +
        escape_markdown("/setlocation [city] [country] – Set your location\n", version=2) +
        escape_markdown("/setmethod [number] – Set prayer calculation method\n", version=2) +
        escape_markdown("/qibla – Get Qibla direction for your location\n\n", version=2) +
        escape_markdown("🛠️ File Utilities:\n", version=2) +
        escape_markdown("/compress - Compress image or PDF (send file with command)\n", version=2) +
        escape_markdown("/convert [to] - Convert image format (send image with command)\n", version=2) +
        escape_markdown("/ocr - Extract text from image (send image with command)\n", version=2) +
        escape_markdown("/merge_pdf - Combine PDFs (send PDFs after command)\n", version=2) +
        escape_markdown("/watermark [text] - Add watermark (send image with command)\n", version=2) +
        escape_markdown("/resize [width] [height] - Resize image (send image with command)", version=2)
    )
    await update.message.reply_markdown_v2(text)

async def add_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        raw_input = " ".join(context.args)
        parts = [p.strip() for p in raw_input.split("|")]

        if len(parts) < 1:
            raise ValueError("Not enough fields. Usage below.")

        amount_desc = parts[0].split()
        if not amount_desc:
            raise ValueError("Amount and description required.")
            
        amount = float(amount_desc[0])
        description = " ".join(amount_desc[1:])
        date = datetime.now().strftime("%Y-%m-%d")

        # Make these fields optional
        payer = parts[1].strip() if len(parts) > 1 else ""
        participants_raw = parts[2] if len(parts) > 2 else ""
        participant_names = [p.strip() for p in participants_raw.split(",") if p.strip()]
        split_type = parts[3].strip() if len(parts) > 3 else "Even"

        airtable_data = {
            "fields": {
                "Date": date,
                "Description": description,
                "Amount": amount,
                "Split Type": split_type
            }
        }

        # Try to fetch participants if we have a payer or participants
        if payer or participant_names:
            try:
                # 🔍 Get list of all participant records (name → record ID)
                lookup_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/Participants"
                response = requests.get(lookup_url, headers=HEADERS)
                
                if response.status_code == 200:
                    name_to_id = {}
                    for record in response.json()["records"]:
                        name_to_id[record["fields"].get("Name", "")] = record["id"]
                    
                    # Handle payer
                    if payer:
                        if payer in name_to_id:
                            airtable_data["fields"]["Payer"] = name_to_id[payer]  # Single ID
                        else:
                            await update.message.reply_text(f"⚠️ Warning: Payer '{payer}' not found in participants")
                    
                    # Handle participants
                    if participant_names:
                        participant_ids = []
                        missing_names = []
                        for name in participant_names:
                            if name in name_to_id:
                                participant_ids.append({"id": name_to_id[name]})  # Keep as object with id
                            else:
                                missing_names.append(name)
                        
                        if participant_ids:
                            # Convert to array of strings format
                            airtable_data["fields"]["Participants"] = [p["id"] for p in participant_ids]
                        if missing_names:
                            await update.message.reply_text(f"⚠️ Warning: These participants weren't found: {', '.join(missing_names)}")
            except Exception as e:
                await update.message.reply_text(f"⚠️ Warning: Couldn't fetch participants. Adding expense without participant info. Error: {str(e)}")

        # Debug info
        await update.message.reply_text(f"Debug - Sending to Airtable: {json.dumps(airtable_data, indent=2)}")

        post_response = requests.post(AIRTABLE_API_URL, headers=HEADERS, json=airtable_data)
        if post_response.status_code == 200:
            success_msg = f"✅ Expense added:\n💵 ${amount:.2f} – {description}"
            if payer:
                success_msg += f"\n👤 Payer: {payer}"
            if participant_names:
                success_msg += f"\n👥 Participants: {', '.join(participant_names)}"
            success_msg += f"\n🔗 Split: {split_type}"
            await update.message.reply_text(success_msg)
        else:
            error_msg = f"⚠️ Airtable error: {post_response.status_code}"
            if post_response.text:
                error_msg += f"\nDetails: {post_response.text}"
            await update.message.reply_text(error_msg)

    except Exception as e:
        await update.message.reply_text(
            f"⚠️ Error: {str(e)}\n\nUsage:\n/addexpense 12.50 lunch | Nibras | Yousuf,Furqan | Even\nor simple usage:\n/addexpense 12.50 lunch"
        )


async def show_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # First fetch all participants to get a mapping of IDs to names
        lookup_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/Participants"
        participant_response = requests.get(lookup_url, headers=HEADERS)
        id_to_name = {}
        
        if participant_response.status_code == 200:
            for record in participant_response.json()["records"]:
                id_to_name[record["id"]] = record["fields"].get("Name", "")

        # Then fetch expenses
        response = requests.get(AIRTABLE_API_URL, headers=HEADERS)
        if response.status_code != 200:
            await update.message.reply_text("⚠️ Failed to fetch expenses from Airtable.")
            return

        records = response.json()["records"]
        if not records:
            await update.message.reply_text("💸 No expenses recorded yet.")
            return

        total = sum(float(record["fields"]["Amount"]) for record in records)
        msg = f"*📊 Total:* ${total:.2f}\n\n*🧾 Recent Expenses:*\n"
        
        # Sort records by date in descending order and take last 5
        sorted_records = sorted(records, key=lambda x: x["fields"]["Date"], reverse=True)[:5]
        for record in sorted_records:
            fields = record["fields"]
            amount = escape_markdown(f"${float(fields['Amount']):.2f}", version=2)
            desc = escape_markdown(fields["Description"], version=2)
            date = escape_markdown(fields["Date"], version=2)
            
            # Get payer name from ID
            payer_id = fields.get("Payer", "")
            payer_name = id_to_name.get(payer_id, payer_id)
            payer = escape_markdown(payer_name, version=2)
            
            # Get participant names
            participant_ids = fields.get("Participants", [])
            participant_names = [id_to_name.get(pid, pid) for pid in participant_ids]
            participants = escape_markdown(", ".join(participant_names), version=2)

            msg += f"• {amount} – {desc} ({date})"
            if payer:
                msg += f" by {payer}"
            if participants:
                msg += f" with {participants}"
            msg += "\n"

        await update.message.reply_markdown_v2(msg)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error fetching expenses: {str(e)}")

async def add_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    note = " ".join(context.args)
    if not note:
        await update.message.reply_text("⚠️ Usage: /addnote remember this thing")
        return

    notes = load_data(NOTES_FILE)
    notes.append({"note": note, "date": datetime.now().strftime("%Y-%m-%d %H:%M")})
    save_data(NOTES_FILE, notes)

    await update.message.reply_text("📝 Note saved.")

async def show_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    notes = load_data(NOTES_FILE)
    if not notes:
        await update.message.reply_text("📭 No notes saved.")
        return

    lines = [f'• {n["note"]} ({n["date"]})' for n in notes[-10:]]
    await update.message.reply_text("🗒️ Your Notes:\n" + "\n".join(lines))

async def clear_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_data(NOTES_FILE, [])
    await update.message.reply_text("🗑️ All notes cleared.")

async def set_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                "⚠️ Usage: /setlocation [city] [country]\n"
                "Example: /setlocation 'New York' 'United States'"
            )
            return

        city = context.args[0]
        country = " ".join(context.args[1:])

        # Use OpenStreetMap Nominatim API to get coordinates
        search_query = f"{city}, {country}"
        nominatim_url = f"https://nominatim.openstreetmap.org/search"
        params = {
            "q": search_query,
            "format": "json",
            "limit": 1
        }
        headers = {
            "User-Agent": "TelegramBot/1.0"  # Required by Nominatim
        }

        response = requests.get(nominatim_url, params=params, headers=headers)
        if response.status_code == 200 and response.json():
            location_data = response.json()[0]
            new_location = {
                "city": city,
                "country": country,
                "latitude": float(location_data["lat"]),
                "longitude": float(location_data["lon"]),
                "method": 3  # Default method
            }
            save_location(new_location)
            
            msg = (
                f"✅ Location set to:\n"
                f"🌆 City: {city}\n"
                f"🌍 Country: {country}\n"
                f"📍 Coordinates: {new_location['latitude']:.4f}, {new_location['longitude']:.4f}\n\n"
                f"Use /prayer to see prayer times for your new location!"
            )
            await update.message.reply_text(msg)
        else:
            await update.message.reply_text("⚠️ Could not find the specified location. Please check the city and country names.")

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error setting location: {str(e)}")

async def prayer_times(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Get current location
        location = load_location()
        
        # Get today's date in the required format
        date = datetime.now().strftime("%d-%m-%Y")
        
        # Call Aladhan API
        url = "http://api.aladhan.com/v1/timings"
        params = {
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "method": location["method"],
            "date": date
        }
        
        response = requests.get(url, params=params)
        if response.status_code == 200:
            data = response.json()
            timings = data["data"]["timings"]
            
            # Format the prayer times
            prayers = {
                "Fajr": timings["Fajr"],
                "Sunrise": timings["Sunrise"],
                "Dhuhr": timings["Dhuhr"],
                "Asr": timings["Asr"],
                "Maghrib": timings["Maghrib"],
                "Isha": timings["Isha"]
            }
            
            # Create message
            msg = f"*🕌 Prayer Times for {location['city']}*\n\n"
            for prayer, time in prayers.items():
                # Convert 24h to 12h format
                time_obj = datetime.strptime(time, "%H:%M")
                time_12h = time_obj.strftime("%I:%M %p")
                msg += f"*{prayer}:* {escape_markdown(time_12h, version=2)}\n"
            
            await update.message.reply_markdown_v2(msg)
        else:
            await update.message.reply_text("⚠️ Failed to fetch prayer times. Please try again later.")
    
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {str(e)}")

async def next_prayer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Get current location
        location = load_location()
        
        # Get current time and prayer times
        now = datetime.now()
        date = now.strftime("%d-%m-%Y")
        
        url = "http://api.aladhan.com/v1/timings"
        params = {
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "method": location["method"],
            "date": date
        }
        
        response = requests.get(url, params=params)
        if response.status_code == 200:
            data = response.json()
            timings = data["data"]["timings"]
            
            # Get relevant prayer times
            prayers = {
                "Fajr": timings["Fajr"],
                "Dhuhr": timings["Dhuhr"],
                "Asr": timings["Asr"],
                "Maghrib": timings["Maghrib"],
                "Isha": timings["Isha"]
            }
            
            # Find next prayer
            current_time = now.strftime("%H:%M")
            next_prayer_name = None
            next_prayer_time = None
            
            for prayer, time in prayers.items():
                if time > current_time:
                    next_prayer_name = prayer
                    next_prayer_time = time
                    break
            
            if not next_prayer_name:
                next_prayer_name = "Fajr"
                next_prayer_time = prayers["Fajr"]
            
            # Convert to 12h format
            time_obj = datetime.strptime(next_prayer_time, "%H:%M")
            time_12h = time_obj.strftime("%I:%M %p")
            
            msg = f"*⏰ Next Prayer:*\n*{next_prayer_name}* at {escape_markdown(time_12h, version=2)}"
            await update.message.reply_markdown_v2(msg)
        else:
            await update.message.reply_text("⚠️ Failed to fetch prayer times. Please try again later.")
    
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {str(e)}")

async def set_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args:
            # Show available methods
            msg = "*🕌 Available Prayer Calculation Methods:*\n\n"
            for method_id, method_name in CALCULATION_METHODS.items():
                msg += f"*{method_id}*: {escape_markdown(method_name, version=2)}\n"
            msg += "\nUsage: `/setmethod [number]`"
            await update.message.reply_markdown_v2(msg)
            return

        method = int(context.args[0])
        if method not in CALCULATION_METHODS:
            await update.message.reply_text("⚠️ Invalid method number. Use /setmethod to see available methods.")
            return

        # Update location data with new method
        location = load_location()
        location["method"] = method
        save_location(location)

        await update.message.reply_markdown_v2(
            f"✅ Prayer calculation method set to:\n*{escape_markdown(CALCULATION_METHODS[method], version=2)}*"
        )

    except ValueError:
        await update.message.reply_text("⚠️ Please provide a valid method number. Use /setmethod to see available methods.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {str(e)}")

async def qibla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        location = load_location()
        
        # Coordinates of the Kaaba
        KAABA_LAT = 21.4225
        KAABA_LON = 39.8262
        
        # Convert to radians
        lat1 = math.radians(location["latitude"])
        lon1 = math.radians(location["longitude"])
        lat2 = math.radians(KAABA_LAT)
        lon2 = math.radians(KAABA_LON)
        
        # Calculate Qibla direction
        y = math.sin(lon2 - lon1)
        x = math.cos(lat1) * math.tan(lat2) - math.sin(lat1) * math.cos(lon2 - lon1)
        qibla = math.degrees(math.atan2(y, x))
        
        # Convert to 0-360 range
        qibla = (qibla + 360) % 360
        
        # Get cardinal direction
        directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        index = round(qibla / 45) % 8
        cardinal = directions[index]
        
        msg = (
            f"*🕋 Qibla Direction for {escape_markdown(location['city'], version=2)}*\n\n"
            f"*Degrees:* {escape_markdown(f'{qibla:.1f}°', version=2)}\n"
            f"*Direction:* {escape_markdown(cardinal, version=2)}\n\n"
            f"_Face {escape_markdown(f'{qibla:.1f}°', version=2)} from True North_"
        )
        
        await update.message.reply_markdown_v2(msg)
        
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error calculating Qibla direction: {str(e)}")

# --- File Utility Functions ---
async def compress_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Check if a file is attached
        if not update.message.document and not update.message.photo:
            await update.message.reply_text("⚠️ Please send a file (image or PDF) with the /compress command")
            return

        # Get file info
        if update.message.document:
            file = await context.bot.get_file(update.message.document.file_id)
            file_name = update.message.document.file_name
            file_path = os.path.join(TEMP_DIR, file_name)
        else:
            file = await context.bot.get_file(update.message.photo[-1].file_id)
            file_name = f"image_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            file_path = os.path.join(TEMP_DIR, file_name)

        # Download file
        await file.download_to_drive(file_path)

        # Process based on file type
        if file_path.lower().endswith(('.jpg', '.jpeg', '.png')):
            # Compress image
            with Image.open(file_path) as img:
                # Convert to RGB if necessary
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                # Save with compression
                output_path = os.path.join(TEMP_DIR, f"compressed_{file_name}")
                img.save(output_path, quality=60, optimize=True)
        elif file_path.lower().endswith('.pdf'):
            # Compress PDF
            reader = PdfReader(file_path)
            writer = PdfWriter()
            
            for page in reader.pages:
                writer.add_page(page)
            
            output_path = os.path.join(TEMP_DIR, f"compressed_{file_name}")
            with open(output_path, "wb") as f:
                writer.write(f)
        else:
            await update.message.reply_text("⚠️ Unsupported file format. Please send an image or PDF.")
            return

        # Send compressed file
        with open(output_path, "rb") as f:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=f,
                filename=f"compressed_{file_name}",
                caption="✅ Here's your compressed file!"
            )

        # Cleanup
        os.remove(file_path)
        os.remove(output_path)

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error compressing file: {str(e)}")

async def convert_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args:
            await update.message.reply_text("⚠️ Please specify the target format: /convert [format]\nSupported formats: jpg, png, webp")
            return

        target_format = context.args[0].lower()
        if target_format not in ['jpg', 'png', 'webp']:
            await update.message.reply_text("⚠️ Unsupported format. Please use: jpg, png, or webp")
            return

        if not update.message.photo:
            await update.message.reply_text("⚠️ Please send an image with the /convert command")
            return

        # Get the image
        file = await context.bot.get_file(update.message.photo[-1].file_id)
        input_path = os.path.join(TEMP_DIR, f"input_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
        await file.download_to_drive(input_path)

        # Convert image
        with Image.open(input_path) as img:
            if img.mode in ('RGBA', 'P') and target_format == 'jpg':
                img = img.convert('RGB')
            output_path = os.path.join(TEMP_DIR, f"converted.{target_format}")
            img.save(output_path)

        # Send converted image
        with open(output_path, "rb") as f:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=f,
                filename=f"converted.{target_format}",
                caption="✅ Here's your converted image!"
            )

        # Cleanup
        os.remove(input_path)
        os.remove(output_path)

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error converting image: {str(e)}")

async def ocr_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message.photo:
            await update.message.reply_text("⚠️ Please send an image with the /ocr command")
            return

        # Get the image
        file = await context.bot.get_file(update.message.photo[-1].file_id)
        file_path = os.path.join(TEMP_DIR, f"ocr_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
        await file.download_to_drive(file_path)

        # Extract text
        with Image.open(file_path) as img:
            text = pytesseract.image_to_string(img)

        if text.strip():
            await update.message.reply_text(f"📝 Extracted Text:\n\n{text.strip()}")
        else:
            await update.message.reply_text("⚠️ No text found in the image")

        # Cleanup
        os.remove(file_path)

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error extracting text: {str(e)}")

async def merge_pdfs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not hasattr(context.user_data, 'pdf_files'):
            context.user_data['pdf_files'] = []

        if not context.args and not update.message.document:
            await update.message.reply_text(
                "📄 PDF Merger Mode:\n"
                "1. Send PDFs you want to merge\n"
                "2. Use /merge_pdf done when finished\n"
                "3. Use /merge_pdf cancel to cancel"
            )
            return

        if context.args and context.args[0].lower() == 'done':
            if not context.user_data['pdf_files']:
                await update.message.reply_text("⚠️ No PDFs to merge. Send some PDFs first!")
                return

            # Merge PDFs
            merger = PdfMerger()
            for pdf_path in context.user_data['pdf_files']:
                merger.append(pdf_path)

            output_path = os.path.join(TEMP_DIR, f"merged_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
            with open(output_path, "wb") as f:
                merger.write(f)

            # Send merged file
            with open(output_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename="merged.pdf",
                    caption="✅ Here's your merged PDF!"
                )

            # Cleanup
            for pdf_path in context.user_data['pdf_files']:
                os.remove(pdf_path)
            os.remove(output_path)
            context.user_data['pdf_files'] = []

        elif context.args and context.args[0].lower() == 'cancel':
            # Cleanup
            for pdf_path in context.user_data['pdf_files']:
                os.remove(pdf_path)
            context.user_data['pdf_files'] = []
            await update.message.reply_text("❌ PDF merge cancelled")

        elif update.message.document and update.message.document.mime_type == 'application/pdf':
            file = await context.bot.get_file(update.message.document.file_id)
            file_path = os.path.join(TEMP_DIR, f"pdf_{len(context.user_data['pdf_files'])}_{update.message.document.file_name}")
            await file.download_to_drive(file_path)
            context.user_data['pdf_files'].append(file_path)
            await update.message.reply_text(
                f"✅ PDF added ({len(context.user_data['pdf_files'])} total)\n"
                "Send more PDFs or use /merge_pdf done to finish"
            )

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error merging PDFs: {str(e)}")

async def add_watermark(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args:
            await update.message.reply_text("⚠️ Please provide watermark text: /watermark [text]")
            return

        if not update.message.photo:
            await update.message.reply_text("⚠️ Please send an image with the /watermark command")
            return

        watermark_text = " ".join(context.args)

        # Get the image
        file = await context.bot.get_file(update.message.photo[-1].file_id)
        input_path = os.path.join(TEMP_DIR, f"watermark_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
        await file.download_to_drive(input_path)

        # Add watermark
        with Image.open(input_path) as img:
            # Create text layer
            txt = Image.new('RGBA', img.size, (255, 255, 255, 0))
            # Get a drawing context
            d = ImageDraw.Draw(txt)
            
            # Calculate font size based on image size
            font_size = int(min(img.size) / 20)
            font = ImageFont.truetype("arial.ttf", font_size)
            
            # Calculate text size
            text_size = d.textsize(watermark_text, font=font)
            
            # Calculate position (center)
            x = (img.size[0] - text_size[0]) / 2
            y = (img.size[1] - text_size[1]) / 2
            
            # Add text
            d.text((x, y), watermark_text, fill=(255, 255, 255, 128), font=font)
            
            # Combine images
            watermarked = Image.alpha_composite(img.convert('RGBA'), txt)
            
            # Save
            output_path = os.path.join(TEMP_DIR, "watermarked.png")
            watermarked.save(output_path)

        # Send watermarked image
        with open(output_path, "rb") as f:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=f,
                filename="watermarked.png",
                caption="✅ Here's your watermarked image!"
            )

        # Cleanup
        os.remove(input_path)
        os.remove(output_path)

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error adding watermark: {str(e)}")

async def resize_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if len(context.args) != 2:
            await update.message.reply_text("⚠️ Please specify width and height: /resize [width] [height]")
            return

        if not update.message.photo:
            await update.message.reply_text("⚠️ Please send an image with the /resize command")
            return

        try:
            width = int(context.args[0])
            height = int(context.args[1])
        except ValueError:
            await update.message.reply_text("⚠️ Width and height must be numbers")
            return

        # Get the image
        file = await context.bot.get_file(update.message.photo[-1].file_id)
        input_path = os.path.join(TEMP_DIR, f"resize_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
        await file.download_to_drive(input_path)

        # Resize image
        with Image.open(input_path) as img:
            resized = img.resize((width, height), Image.LANCZOS)
            output_path = os.path.join(TEMP_DIR, "resized.png")
            resized.save(output_path)

        # Send resized image
        with open(output_path, "rb") as f:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=f,
                filename="resized.png",
                caption=f"✅ Here's your resized image ({width}x{height})!"
            )

        # Cleanup
        os.remove(input_path)
        os.remove(output_path)

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error resizing image: {str(e)}")

# --- Network Utility Functions ---
async def shorten_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args:
            await update.message.reply_text("⚠️ Usage: /shorten [url]\nExample: /shorten https://example.com")
            return

        url = context.args[0]
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        # Use TinyURL API
        response = requests.get(f'http://tinyurl.com/api-create.php?url={url}')
        if response.status_code == 200:
            shortened_url = response.text
            await update.message.reply_text(f"🔗 Shortened URL:\n{shortened_url}")
        else:
            await update.message.reply_text("⚠️ Failed to shorten URL. Please try again.")

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {str(e)}")

async def generate_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args:
            await update.message.reply_text("⚠️ Usage: /qr [text]\nExample: /qr https://example.com")
            return

        text = " ".join(context.args)
        
        # Generate QR code
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(text)
        qr.make(fit=True)
        
        # Create image
        qr_image = qr.make_image(fill_color="black", back_color="white")
        
        # Save image
        output_path = os.path.join(TEMP_DIR, f"qr_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
        qr_image.save(output_path)
        
        # Send image
        with open(output_path, "rb") as f:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=f,
                caption=f"📱 QR Code for: {text}"
            )
        
        # Cleanup
        os.remove(output_path)

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {str(e)}")

async def domain_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args:
            await update.message.reply_text("⚠️ Usage: /whois [domain]\nExample: /whois example.com")
            return

        domain = context.args[0]
        
        # Get WHOIS info
        w = whois.whois(domain)
        
        # Format expiration date
        expiration = w.expiration_date
        if isinstance(expiration, list):
            expiration = expiration[0]
        
        # Get DNS info
        dns_info = []
        try:
            a_records = dns.resolver.resolve(domain, 'A')
            dns_info.append(f"📍 IP: {', '.join(str(r) for r in a_records)}")
        except:
            pass
        
        try:
            mx_records = dns.resolver.resolve(domain, 'MX')
            dns_info.append(f"📧 Mail Servers: {', '.join(str(r.exchange) for r in mx_records)}")
        except:
            pass
        
        # Create message
        msg = (
            f"*🌐 Domain Information for {escape_markdown(domain, version=2)}*\n\n"
            f"*Registrar:* {escape_markdown(str(w.registrar), version=2)}\n"
            f"*Creation Date:* {escape_markdown(str(w.creation_date), version=2)}\n"
            f"*Expiration Date:* {escape_markdown(str(expiration), version=2)}\n"
            f"*Status:* {escape_markdown(str(w.status), version=2)}\n\n"
            f"*DNS Information:*\n{escape_markdown(chr(10).join(dns_info), version=2)}"
        )
        
        await update.message.reply_markdown_v2(msg)

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {str(e)}")

async def check_site(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args:
            await update.message.reply_text("⚠️ Usage: /ping [site]\nExample: /ping example.com")
            return

        site = context.args[0]
        if not site.startswith(('http://', 'https://')):
            site = 'https://' + site

        # Check DNS
        domain = site.split('//')[-1].split('/')[0]
        try:
            ip = socket.gethostbyname(domain)
            dns_ok = True
        except:
            dns_ok = False
            ip = "Not found"

        # Check HTTP response
        try:
            response = requests.get(site, timeout=5)
            status_code = response.status_code
            response_time = response.elapsed.total_seconds() * 1000  # Convert to ms
        except requests.exceptions.RequestException as e:
            status_code = "Error"
            response_time = 0

        # Create message
        msg = (
            f"*🌐 Site Status for {escape_markdown(site, version=2)}*\n\n"
            f"*DNS Resolution:* {'✅' if dns_ok else '❌'}\n"
            f"*IP Address:* {escape_markdown(ip, version=2)}\n"
            f"*Status Code:* {escape_markdown(str(status_code), version=2)}\n"
            f"*Response Time:* {escape_markdown(f'{response_time:.0f}ms', version=2)}"
        )
        
        await update.message.reply_markdown_v2(msg)

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {str(e)}")

async def lookup_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args:
            await update.message.reply_text("⚠️ Usage: /number [phone_number]\nExample: /number +1234567890")
            return

        if not NUMVERIFY_API_KEY:
            await update.message.reply_text("⚠️ NumVerify API key not configured. Please set NUMVERIFY_API_KEY in environment variables.")
            return

        number = context.args[0]
        
        # Clean the number
        number = re.sub(r'[^\d+]', '', number)
        
        # Call NumVerify API
        url = f"http://apilayer.net/api/validate"
        params = {
            "access_key": NUMVERIFY_API_KEY,
            "number": number,
            "format": 1
        }
        
        response = requests.get(url, params=params)
        if response.status_code == 200:
            data = response.json()
            if data.get("valid"):
                msg = (
                    f"*📱 Phone Number Information*\n\n"
                    f"*Number:* {escape_markdown(data['international_format'], version=2)}\n"
                    f"*Country:* {escape_markdown(data['country_name'], version=2)}\n"
                    f"*Location:* {escape_markdown(data['location'], version=2)}\n"
                    f"*Carrier:* {escape_markdown(data['carrier'], version=2)}\n"
                    f"*Line Type:* {escape_markdown(data['line_type'], version=2)}"
                )
            else:
                msg = "⚠️ Invalid phone number"
            
            await update.message.reply_markdown_v2(msg)
        else:
            await update.message.reply_text("⚠️ Failed to lookup number. Please try again.")

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {str(e)}")

# --- Bot Setup ---
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("addexpense", add_expense))
app.add_handler(CommandHandler("expenses", show_expenses))
app.add_handler(CommandHandler("addnote", add_note))
app.add_handler(CommandHandler("notes", show_notes))
app.add_handler(CommandHandler("clear_notes", clear_notes))
app.add_handler(CommandHandler("prayer", prayer_times))
app.add_handler(CommandHandler("next", next_prayer))
app.add_handler(CommandHandler("setlocation", set_location))
app.add_handler(CommandHandler("setmethod", set_method))
app.add_handler(CommandHandler("qibla", qibla))
app.add_handler(CommandHandler("compress", compress_file))
app.add_handler(CommandHandler("convert", convert_format))
app.add_handler(CommandHandler("ocr", ocr_image))
app.add_handler(CommandHandler("merge_pdf", merge_pdfs))
app.add_handler(CommandHandler("watermark", add_watermark))
app.add_handler(CommandHandler("resize", resize_image))
app.add_handler(CommandHandler("shorten", shorten_url))
app.add_handler(CommandHandler("qr", generate_qr))
app.add_handler(CommandHandler("whois", domain_info))
app.add_handler(CommandHandler("ping", check_site))
app.add_handler(CommandHandler("number", lookup_number))

app.bot.set_my_commands([
    BotCommand("start", "Show available commands"),
    BotCommand("addexpense", "Add new expense"),
    BotCommand("expenses", "Show total + recent expenses"),
    BotCommand("addnote", "Save a quick note"),
    BotCommand("notes", "Show your saved notes"),
    BotCommand("clear_notes", "Delete all notes"),
    BotCommand("prayer", "Show today's prayer times"),
    BotCommand("next", "Show next prayer time"),
    BotCommand("setlocation", "Set your location for prayer times"),
    BotCommand("setmethod", "Set prayer calculation method"),
    BotCommand("qibla", "Get Qibla direction for your location"),
    BotCommand("compress", "Compress image or PDF"),
    BotCommand("convert", "Convert image format"),
    BotCommand("ocr", "Extract text from image"),
    BotCommand("merge_pdf", "Combine multiple PDFs"),
    BotCommand("watermark", "Add watermark to image"),
    BotCommand("resize", "Resize image"),
    BotCommand("shorten", "Shorten a URL"),
    BotCommand("qr", "Generate QR code"),
    BotCommand("whois", "Get domain information"),
    BotCommand("ping", "Check website status"),
    BotCommand("number", "Lookup phone number info"),
])

app.run_polling()
