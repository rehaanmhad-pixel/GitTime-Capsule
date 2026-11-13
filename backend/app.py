from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from git_analyzer import RepoError, analyze

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = Flask(__name__, static_folder=None)


@app.get("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.post("/api/analyze")
def api_analyze():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(analyze(data.get("path", "")))
    except RepoError as err:
        return jsonify({"error": str(err)}), 400


if __name__ == "__main__":
    # 127.0.0.1 = only this machine can reach it (not the whole network).
    app.run(host="127.0.0.1", port=5000, debug=False)