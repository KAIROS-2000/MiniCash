from app import db, app

# простой скрипт для начального создания таблиц
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        print("База данных и таблицы созданы.")
