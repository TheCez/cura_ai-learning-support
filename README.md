# careLearn

Ein Python-Web-App-Prototyp für deutsche Pflegeschulen mit Rollen für Lehrende und Lernende.

## Architektur

- Flask als Web-Framework
- SQLAlchemy + SQLite als Datenbank
- Rollen: `student` und `teacher`
- Funktionen:
  - Registrierung / Login
  - Spracheingangs-Test & Pflegewissen-Test
  - Lehrende laden Kurse und Module hoch
  - Studierende melden sich an und sehen Lernpfade, Quizze und KI-Lehrer
  - Fortschrittsanzeige für Lehrende

## Setup

1. `python -m venv venv`
2. Virtuelle Umgebung aktivieren (macOS/Linux): `source venv/bin/activate`
3. Virtuelle Umgebung aktivieren (Windows PowerShell): `venv\Scripts\Activate.ps1`
4. Virtuelle Umgebung aktivieren (Windows CMD): `venv\Scripts\activate.bat`
5. `pip install -r requirements.txt`
6. `python app.py`

Die App läuft dann standardmäßig unter `https://127.0.0.1:5001`.

### HTTPS/HTTP Modus

1. Standard (HTTPS): `python app.py` und dann `https://127.0.0.1:5001`
2. Optional unsicheres HTTP: `DEV_HTTPS=0 python app.py` und dann `http://127.0.0.1:5001`
