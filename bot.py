import os
import sys
import requests
import json

print("=" * 50)
print("🔬 ДИАГНОСТИКА БОТА")
print("=" * 50)

# ========== 1. ПРОВЕРКА ОКРУЖЕНИЯ ==========
print("\n📌 1. ПРОВЕРКА ОКРУЖЕНИЯ")
print(f"   Python version: {sys.version}")
print(f"   Python executable: {sys.executable}")

# ========== 2. ПРОВЕРКА ПЕРЕМЕННЫХ ==========
print("\n📌 2. ПРОВЕРКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ")
TOKEN = os.environ.get('TELEGRAM_TOKEN')
GOOGLE_CREDS = os.environ.get('GOOGLE_CREDENTIALS')

if TOKEN:
    print(f"   ✅ TELEGRAM_TOKEN: {TOKEN[:20]}... (найден)")
else:
    print("   ❌ TELEGRAM_TOKEN: НЕ НАЙДЕН")

if GOOGLE_CREDS:
    print(f"   ✅ GOOGLE_CREDENTIALS: {len(GOOGLE_CREDS)} символов (найден)")
    # Проверяем, валидный ли JSON
    try:
        json.loads(GOOGLE_CREDS)
        print("   ✅ GOOGLE_CREDENTIALS: валидный JSON")
    except:
        print("   ❌ GOOGLE_CREDENTIALS: НЕ валидный JSON!")
else:
    print("   ❌ GOOGLE_CREDENTIALS: НЕ НАЙДЕН")

# ========== 3. ПРОВЕРКА ДОСТУПА К TELEGRAM ==========
print("\n📌 3. ПРОВЕРКА ДОСТУПА К TELEGRAM API")
if TOKEN:
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getMe"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                print(f"   ✅ Telegram API доступен")
                print(f"   ✅ Бот: @{data['result']['username']}")
            else:
                print(f"   ❌ Ошибка Telegram: {data}")
        else:
            print(f"   ❌ HTTP ошибка: {response.status_code}")
    except requests.exceptions.Timeout:
        print("   ❌ Таймаут! Telegram API недоступен (проверь интернет/прокси)")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
else:
    print("   ⏭️ Пропущено (нет токена)")

# ========== 4. ПРОВЕРКА УСТАНОВЛЕННЫХ БИБЛИОТЕК ==========
print("\n📌 4. ПРОВЕРКА БИБЛИОТЕК")
libraries = ['telegram', 'gspread', 'google.auth', 'flask']
for lib in libraries:
    try:
        __import__(lib)
        print(f"   ✅ {lib}")
    except ImportError:
        print(f"   ❌ {lib} - НЕ УСТАНОВЛЕНА")

# ========== 5. ПРОВЕРКА ТЕЛЕГРАМ БОТА (короткий тест) ==========
print("\n📌 5. ПРОВЕРКА РАБОТЫ БОТА (отправка тестового сообщения)")
if TOKEN:
    try:
        # Отправляем сообщение самому себе (если знаем chat_id)
        # Этот тест только если у нас есть chat_id
        print("   ⏭️ Для отправки сообщения нужен chat_id")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
else:
    print("   ⏭️ Пропущено (нет токена)")

# ========== 6. ПРОВЕРКА МАРШРУТИЗАЦИИ (Railway) ==========
print("\n📌 6. ПРОВЕРКА СЕТИ (Railway)")
print(f"   PORT: {os.environ.get('PORT', 'не указан')}")
print(f"   RAILWAY_PUBLIC_DOMAIN: {os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'не указан')}")

# ========== ИТОГ ==========
print("\n" + "=" * 50)
print("📋 ДИАГНОСТИКА ЗАВЕРШЕНА")
print("=" * 50)

# Запускаем простой Flask сервер для проверки вебхука (если нужно)
if 'flask' in sys.modules:
    print("\n🔗 Запуск Flask сервера для проверки вебхука...")
    from flask import Flask, request
    test_app = Flask(__name__)
    
    @test_app.route('/webhook', methods=['POST'])
    def webhook():
        print(f"📩 Получен запрос: {request.get_data()}")
        return "OK", 200
    
    @test_app.route('/')
    def home():
        return "Диагностический сервер работает", 200
    
    port = int(os.environ.get("PORT", 8080))
    print(f"✅ Flask запущен на порту {port}")
    test_app.run(host='0.0.0.0', port=port)
else:
    print("\n⚠️ Flask не установлен, вебхук не запущен")
    print("   Установите flask: pip install flask")
