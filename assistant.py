import os
import sqlite3
import logging
import threading  # <-- AGGIUNTO
from http.server import HTTPServer, BaseHTTPRequestHandler  # <-- AGGIUNTO
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai
from google.genai import types

# Configurazione del Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# -------------------------------------------------------------------
# CONFIGURAZIONE CHIAVI E INIZIALIZZAZIONE
# -------------------------------------------------------------------
# Inserisci le tue chiavi qui sotto sostituendo i segnaposto
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# Inizializzazione del client Gemini
ai_client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_ID = "gemini-3.6-flash"

# System Prompt per definire la personalità del bot
SYSTEM_PROMPT = (
    "Sei un assistente personale interattivo, efficiente, cortese e conciso. "
    "Il tuo compito è aiutare l'utente a organizzare idee, rispondere a quesiti "
    "e fornire supporto pratico nelle attività quotidiane."
)

# -------------------------------------------------------------------
# GESTIONE DATABASE SQLITE (MEMORIA LOCALE)
# -------------------------------------------------------------------
DB_NAME = "assistant_memory.db"

def init_db():
    """Inizializza il database SQLite per salvare la memoria."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def save_message(user_id: int, role: str, content: str):
    """Salva un singolo messaggio nel DB."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO chat_history (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, role, content)
    )
    conn.commit()
    conn.close()

def get_recent_history(user_id: int, limit: int = 10) -> list:
    """Recupera gli ultimi 'limit' messaggi dal DB."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    
    # Inverte l'ordine per mantenere la sequenza temporale corretta
    history = []
    for role, content in reversed(rows):
        history.append({"role": role, "content": content})
    return history

def clear_user_history(user_id: int):
    """Cancella la cronologia messaggi dell'utente."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# -------------------------------------------------------------------
# HANDLER COMANDI TELEGRAM
# -------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestisce il comando /start."""
    user = update.effective_user
    welcome_msg = (
        f"Ciao {user.first_name}! 👋\n\n"
        "Sono il tuo Assistente Personale.\n"
        "Puoi scrivermi qualsiasi cosa e terrò traccia della nostra conversazione.\n\n"
        "📌 Comandi utili:\n"
        "/reset - Cancella la memoria e ricomincia la chat"
    )
    await update.message.reply_text(welcome_msg)

async def reset_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestisce il comando /reset."""
    user_id = update.effective_user.id
    clear_user_history(user_id)
    await update.message.reply_text("🧹 Memoria resettata con successo! Di cosa vogliamo parlare?")

# -------------------------------------------------------------------
# HANDLER MESSAGGI DI TESTO
# -------------------------------------------------------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Riceve i messaggi, ricostruisce il contesto e risponde tramite Gemini."""
    user_id = update.effective_user.id
    user_text = update.message.text

    # 1. Salva il messaggio dell'utente nel DB
    save_message(user_id, "user", user_text)

    # 2. Mostra lo stato "sta scrivendo..." su Telegram
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # 3. Recupera la cronologia recente
    raw_history = get_recent_history(user_id, limit=12)

    # 4. Prepara i contenuti nel formato dell'SDK google-genai
    contents = []
    for msg in raw_history:
        gemini_role = "user" if msg["role"] == "user" else "model"
        contents.append(
            types.Content(
                role=gemini_role,
                parts=[types.Part.from_text(text=msg["content"])]
            )
        )

    try:
        # 5. Invia la richiesta a Gemini
        response = ai_client.models.generate_content(
            model=MODEL_ID,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.7
            )
        )
        bot_response = response.text

    except Exception as e:
        logging.error(f"Errore Gemini API: {e}")
        bot_response = "⚠️ Si è verificato un errore durante l'elaborazione della risposta. Riprova tra poco."

    # 6. Salva la risposta dell'AI e inviala all'utente
    save_message(user_id, "model", bot_response)
    await update.message.reply_text(bot_response)

# -------------------------------------------------------------------
# AVVIO BOT
# -------------------------------------------------------------------
# -------------------------------------------------------------------
# MINI SERVER HTTP (per i controlli di Render)
# -------------------------------------------------------------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_health_check():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

# -------------------------------------------------------------------
# AVVIO BOT
# -------------------------------------------------------------------
if __name__ == '__main__':
    init_db()

    # 1. Avvia PRIMA il server HTTP in un thread separato
    threading.Thread(target=run_health_check, daemon=True).start()

    # 2. Inizializza l'app Telegram
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset_memory))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    print("🤖 Assistente avviato correttamente!")
    
    # 3. Avvia il polling per ultimo (funzione bloccante)
    app.run_polling()