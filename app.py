import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from dotenv import load_dotenv
from flask import (
    Flask,
    render_template,
    redirect,
    url_for,
    request,
    flash,
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
    UserMixin,
)
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import func

# ------------------------------------------------------------------------------
# Инициализация приложения
# ------------------------------------------------------------------------------

load_dotenv()

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-prod")

app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:123456@localhost:5432/minicash",
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"

# ------------------------------------------------------------------------------
# Модели
# ------------------------------------------------------------------------------


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    initial_balance = db.Column(db.Numeric(14, 2), nullable=True)

    categories = db.relationship("Category", backref="user", lazy=True)
    transactions = db.relationship("Transaction", backref="user", lazy=True)

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.now(timezone.utc),  # ВАЖНО: без скобок!
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Category(db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    type = db.Column(db.String(10), nullable=False)  # "income" | "expense"
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    transactions = db.relationship("Transaction", backref="category", lazy=True)


class Transaction(db.Model):
    __tablename__ = "transactions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False)

    # Numeric(14,2) — до 999 999 999 999.99
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    type = db.Column(db.String(10), nullable=False)  # "income" | "expense"

    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


# ------------------------------------------------------------------------------
# Login manager
# ------------------------------------------------------------------------------


@login_manager.user_loader
def load_user(user_id: str):
    return User.query.get(int(user_id))


# ------------------------------------------------------------------------------
# Вспомогательные функции
# ------------------------------------------------------------------------------


def create_default_categories(user: User) -> None:
    """Создаём дефолтные категории для нового пользователя."""
    defaults = [
        ("Еда", "expense"),
        ("Транспорт", "expense"),
        ("Развлечения", "expense"),
        ("Аренда", "expense"),
        ("Подписки", "expense"),
        ("Другое", "expense"),
        ("Зарплата", "income"),
        ("Фриланс", "income"),
        ("Подарки", "income"),
    ]
    for name, type_ in defaults:
        db.session.add(Category(name=name, type=type_, user=user))


def parse_decimal(raw: str) -> Decimal:
    """Безопасно парсим строку в Decimal, поддерживаем запятую."""
    if raw is None:
        raise InvalidOperation("empty")
    cleaned = raw.replace(" ", "").replace(",", ".")
    return Decimal(cleaned)


# Фильтр для красивого вывода денег в шаблоне
@app.template_filter("currency")
def currency_filter(value):
    if value is None:
        return "0,00 ₽"
    try:
        if not isinstance(value, Decimal):
            value = Decimal(str(value))
        value = value.quantize(Decimal("0.01"))
        # 1234.56 -> '1,234.56' -> '1 234,56 ₽'
        s = f"{value:,.2f}"
        s = s.replace(",", " ").replace(".", ",")
        return f"{s} ₽"
    except Exception:
        return str(value)


# ------------------------------------------------------------------------------
# Маршруты аутентификации
# ------------------------------------------------------------------------------


@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if not name or not email or not password:
            flash("Заполните все обязательные поля.", "error")
            return redirect(url_for("register"))

        if password != confirm:
            print("PWD:", repr(password), "CONFIRM:", repr(confirm))
            flash("Паролы не совпадают.", "error")
            return redirect(url_for("register"))

        existing = User.query.filter_by(email=email).first()
        if existing:
            flash("Пользователь с таким e-mail уже существует.", "error")
            return redirect(url_for("register"))

        user = User(name=name, email=email)
        user.set_password(password)
        db.session.add(user)
        create_default_categories(user)
        db.session.commit()

        login_user(user)
        flash("Аккаунт создан.", "success")
        return redirect(url_for("dashboard"))


    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            flash("Добро пожаловать!", "success")
            return redirect(url_for("setup_balance"))
        else:
            flash("Неверный логин или пароль.", "error")
            return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Вы вышли из аккаунта.", "info")
    return redirect(url_for("login"))


# ------------------------------------------------------------------------------
# Дашборд
# ------------------------------------------------------------------------------

@app.route("/setup-balance", methods=["GET", "POST"])
@login_required
def setup_balance():
    # если баланс уже задан – сюда больше не пускаем
    if current_user.initial_balance is not None:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        amount_raw = request.form.get("initial_balance")

        try:
            amount = parse_decimal(amount_raw)
        except (InvalidOperation, TypeError):
            flash("Некорректный баланс.", "error")
            return redirect(url_for("setup_balance"))

        current_user.initial_balance = amount
        db.session.commit()
        flash("Начальный баланс сохранён.", "success")
        return redirect(url_for("dashboard"))

    return render_template("setup_balance.html")

@app.route("/dashboard")
@login_required
def dashboard():
    # если пользователь ещё не указал начальный баланс – отправляем на страницу настройки
    if current_user.initial_balance is None:
        return redirect(url_for("setup_balance"))

    user_id = current_user.id

    # период: week | month | year
    period = request.args.get("period", "month")
    today = datetime.utcnow().date()

    from datetime import date

    period = request.args.get("period", "month")
    today = date.today()

    if period == "week":
        start_date = today - timedelta(days=7)
    elif period == "month":
        start_date = today.replace(day=1)
    elif period == "year":
        start_date = today.replace(month=1, day=1)
    elif period == "all":
        # минимальное изменение: очень ранняя дата, чтобы захватить все транзакции
        start_date = date(1970, 1, 1)
    else:
        start_date = today.replace(day=1)

    start_dt = datetime.combine(start_date, datetime.min.time())

    # базовый запрос по транзакциям пользователя и периоду
    base_query = Transaction.query.filter(
        Transaction.user_id == user_id,
        Transaction.created_at >= start_dt,
    )

    # последние транзакции
    transactions = base_query.order_by(Transaction.created_at.desc()).limit(50).all()

    # сумма доходов и расходов
    total_income = (
        db.session.query(func.coalesce(func.sum(Transaction.amount), 0))
        .filter(
            Transaction.user_id == user_id,
            Transaction.type == "income",
            Transaction.created_at >= start_dt,
        )
        .scalar()
    )

    total_expense = (
        db.session.query(func.coalesce(func.sum(Transaction.amount), 0))
        .filter(
            Transaction.user_id == user_id,
            Transaction.type == "expense",
            Transaction.created_at >= start_dt,
        )
        .scalar()
    )

    initial = current_user.initial_balance or Decimal("0")
    balance = initial + (total_income or 0) - (total_expense or 0)

    # расходы по категориям
    expense_rows = (
        db.session.query(Category.name, func.sum(Transaction.amount))
        .join(Transaction, Transaction.category_id == Category.id)
        .filter(
            Transaction.user_id == user_id,
            Transaction.type == "expense",
            Transaction.created_at >= start_dt,
        )
        .group_by(Category.name)
        .all()
    )
    expense_labels = [row[0] for row in expense_rows]
    expense_values = [float(row[1]) for row in expense_rows]

    # доходы vs расходы
    income_vs_expense_labels = ["Доходы", "Расходы"]
    income_vs_expense_values = [float(total_income or 0), float(total_expense or 0)]

    # Топ-5 трат
    top_expenses = (
        base_query.filter(Transaction.type == "expense")
        .order_by(Transaction.amount.desc())
        .limit(5)
        .all()
    )

    # категории для формы
    expense_categories = Category.query.filter_by(
        user_id=user_id, type="expense"
    ).all()
    income_categories = Category.query.filter_by(
        user_id=user_id, type="income"
    ).all()

    return render_template(
        "dashboard.html",
        balance=balance,
        total_income=total_income,
        total_expense=total_expense,
        transactions=transactions,
        top_expenses=top_expenses,
        expense_labels=expense_labels,
        expense_values=expense_values,
        income_vs_expense_labels=income_vs_expense_labels,
        income_vs_expense_values=income_vs_expense_values,
        expense_categories=expense_categories,
        income_categories=income_categories,
        current_period=period,
        today=today,
    )


# ------------------------------------------------------------------------------
# Добавление / редактирование / удаление транзакций
# ------------------------------------------------------------------------------


@app.route("/transactions/add", methods=["POST"])
@login_required
def add_transaction():
    type_ = request.form.get("type", "expense")
    amount_raw = request.form.get("amount")
    category_id = request.form.get("category_id")
    description = request.form.get("description", "").strip()
    date_str = request.form.get("date")

    try:
        amount = parse_decimal(amount_raw)
        if amount <= 0:
            raise InvalidOperation("non-positive")
    except (InvalidOperation, TypeError):
        flash("Некорректная сумма.", "error")
        return redirect(url_for("dashboard"))

    # дата
    if date_str:
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            date_obj = datetime.utcnow()
    else:
        date_obj = datetime.utcnow()

    # проверяем категорию
    try:
        category_id_int = int(category_id)
    except (TypeError, ValueError):
        flash("Категория не выбрана.", "error")
        return redirect(url_for("dashboard"))

    category = Category.query.filter_by(
        id=category_id_int, user_id=current_user.id, type=type_
    ).first()

    if not category:
        flash("Категория не найдена.", "error")
        return redirect(url_for("dashboard"))

    tx = Transaction(
        user_id=current_user.id,
        category_id=category.id,
        amount=amount,
        type=type_,
        description=description or None,
        created_at=date_obj,
    )
    db.session.add(tx)
    db.session.commit()

    flash("Операция добавлена.", "success")
    return redirect(url_for("dashboard"))


@app.route("/transactions/<int:tx_id>/edit", methods=["GET", "POST"])
@login_required
def edit_transaction(tx_id):
    tx = Transaction.query.filter_by(id=tx_id, user_id=current_user.id).first_or_404()

    if request.method == "POST":
        type_ = request.form.get("type", tx.type)
        amount_raw = request.form.get("amount")
        category_id = request.form.get("category_id")
        description = request.form.get("description", "").strip()
        date_str = request.form.get("date")

        try:
            amount = parse_decimal(amount_raw)
            if amount <= 0:
                raise InvalidOperation("non-positive")
        except (InvalidOperation, TypeError):
            flash("Некорректная сумма.", "error")
            return redirect(url_for("edit_transaction", tx_id=tx.id))

        # дата
        if date_str:
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                date_obj = tx.created_at
        else:
            date_obj = tx.created_at

        try:
            category_id_int = int(category_id)
        except (TypeError, ValueError):
            flash("Категория не выбрана.", "error")
            return redirect(url_for("edit_transaction", tx_id=tx.id))

        category = Category.query.filter_by(
            id=category_id_int, user_id=current_user.id, type=type_
        ).first()
        if not category:
            flash("Категория не найдена.", "error")
            return redirect(url_for("edit_transaction", tx_id=tx.id))

        tx.type = type_
        tx.amount = amount
        tx.category_id = category.id
        tx.description = description or None
        tx.created_at = date_obj

        db.session.commit()
        flash("Операция обновлена.", "success")
        return redirect(url_for("dashboard"))

    # для формы редактирования нужны категории
    # Категории соответствуют ТИПУ КАТЕГОРИИ, а не типу транзакции
    if tx.category.type == "expense":
        categories = Category.query.filter_by(user_id=current_user.id, type="expense").all()
    else:
        categories = Category.query.filter_by(user_id=current_user.id, type="income").all()

    tx_date = tx.created_at.date()

    return render_template(
        "edit_transaction.html",
        tx=tx,
        categories=categories,
        tx_date=tx_date,
    )

@app.route("/transactions/<int:tx_id>/delete", methods=["POST"])
@login_required
def delete_transaction(tx_id):
    tx = Transaction.query.filter_by(id=tx_id, user_id=current_user.id).first_or_404()
    db.session.delete(tx)
    db.session.commit()
    flash("Операция удалена.", "info")
    return redirect(url_for("dashboard"))


# ------------------------------------------------------------------------------
# Точка входа
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    # Для разработки удобно автоматически создавать таблицы
    with app.app_context():
        db.create_all()
    app.run(debug=True)