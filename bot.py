import os
from flask import Flask

TOKEN = os.environ.get('TELEGRAM_TOKEN')
print(f"Токен получен: {'ДА' if TOKEN else 'НЕТ'}")

app = Flask(__name__)

@app.route('/')
def home():
    return "Бот работает"

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    print("✅ Запускаю Flask...")
    app.run(host='0.0.0.0', port=port)
