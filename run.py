from app import create_app
import os
import threading
import time
import subprocess
from pathlib import Path

app = create_app()

def find_edge():
    # Rutas típicas de Edge en Windows
    candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return "msedge"  # fallback al PATH

def open_fullscreen():
    time.sleep(1)
    url = "http://127.0.0.1:5000"
    # Abre el navegador predeterminado (sin pantalla completa)
    import webbrowser
    webbrowser.open_new(url)

if __name__ == "__main__":
    # Solo en el proceso del reloader (evita doble apertura)
    should_open = os.environ.get("WERKZEUG_RUN_MAIN") == "true"

    if should_open:
        threading.Thread(target=open_fullscreen, daemon=True).start()

    app.run(host="0.0.0.0", port=5000, debug=True)
