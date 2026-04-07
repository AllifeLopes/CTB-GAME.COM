import os
import json
import random
import sqlite3
import datetime
from functools import wraps

from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import jwt

app = Flask(__name__)
CORS(app)

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "chave_local_teste")

DB_NAME = "database.db"
QUESTIONS_FILE = "questions.json"

active_players = set()


def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        points INTEGER DEFAULT 0,
        rank TEXT DEFAULT 'Recruta',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id INTEGER NOT NULL,
        score INTEGER DEFAULT 0,
        correct_answers INTEGER DEFAULT 0,
        wrong_answers INTEGER DEFAULT 0,
        started_at TEXT DEFAULT CURRENT_TIMESTAMP,
        finished_at TEXT
    )
    """)

    conn.commit()
    conn.close()


def load_questions():
    try:
        with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def get_rank(points):
    if points >= 1000:
        return "Lenda da Fiscalização"
    elif points >= 700:
        return "Capitão do Trânsito"
    elif points >= 500:
        return "Sargento da Lei"
    elif points >= 300:
        return "Cabo da Rodovia"
    elif points >= 100:
        return "Soldado CTB"
    return "Recruta"


def create_token(player_id, username):
    payload = {
        "id": player_id,
        "username": username,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(days=7)
    }
    return jwt.encode(payload, app.config["SECRET_KEY"], algorithm="HS256")


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")

        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Token ausente"}), 401

        token = auth_header.split(" ")[1]

        try:
            decoded = jwt.decode(token, app.config["SECRET_KEY"], algorithms=["HS256"])
            request.user = decoded
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expirado"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Token inválido"}), 401

        return f(*args, **kwargs)

    return decorated


@app.route("/")
def home():
    return jsonify({"message": "API CTB online"})


@app.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "Username e password são obrigatórios"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO players (username, password) VALUES (?, ?)",
            (username, generate_password_hash(password))
        )
        conn.commit()
        player_id = cursor.lastrowid
        conn.close()

        return jsonify({
            "success": True,
            "message": "Jogador cadastrado com sucesso",
            "playerId": player_id
        }), 201

    except sqlite3.IntegrityError:
        return jsonify({"error": "Usuário já existe"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM players WHERE username = ?", (username,))
    player = cursor.fetchone()
    conn.close()

    if not player:
        return jsonify({"error": "Usuário não encontrado"}), 404

    if not check_password_hash(player["password"], password):
        return jsonify({"error": "Senha incorreta"}), 401

    token = create_token(player["id"], player["username"])
    active_players.add(player["id"])

    return jsonify({
        "success": True,
        "token": token,
        "player": {
            "id": player["id"],
            "username": player["username"],
            "points": player["points"],
            "rank": player["rank"]
        }
    })


@app.route("/ranking", methods=["GET"])
def ranking():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT username, points, rank
        FROM players
        ORDER BY points DESC, username ASC
        LIMIT 20
    """)
    rows = cursor.fetchall()
    conn.close()

    return jsonify([dict(row) for row in rows])


@app.route("/active-players", methods=["GET"])
def active():
    return jsonify({"activePlayers": len(active_players)})


@app.route("/question", methods=["GET"])
@token_required
def question():
    questions = load_questions()

    if not questions:
        return jsonify({"error": "Nenhuma questão cadastrada"}), 500

    q = random.choice(questions)
    return jsonify({
        "id": q["id"],
        "pergunta": q["pergunta"],
        "alternativas": q["alternativas"]
    })


@app.route("/answer", methods=["POST"])
@token_required
def answer():
    data = request.get_json(silent=True) or {}
    question_id = data.get("questionId")
    answer_value = (data.get("answer") or "").strip().upper()

    questions = load_questions()
    question = next((q for q in questions if q["id"] == question_id), None)

    if not question:
        return jsonify({"error": "Questão não encontrada"}), 404

    correct_answer = question["correta"].upper()
    is_correct = answer_value == correct_answer
    earned_points = 10 if is_correct else 0

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM players WHERE id = ?", (request.user["id"],))
    player = cursor.fetchone()

    new_points = player["points"] + earned_points
    new_rank = get_rank(new_points)

    cursor.execute(
        "UPDATE players SET points = ?, rank = ? WHERE id = ?",
        (new_points, new_rank, request.user["id"])
    )

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "correta": is_correct,
        "resposta_correta": correct_answer,
        "pontos_ganhos": earned_points,
        "pontos_totais": new_points,
        "patente": new_rank,
        "explicacao": question.get("explicacao", "")
    })


init_db()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)