"""
run.py
Alternative to `python app.py`. Because this file imports app.py as a
module named 'app' (rather than app.py running itself as '__main__'),
app.py only ever gets loaded once -- sidestepping the double-import
issue that `python app.py` needs a small workaround for (see the
comment at the bottom of app.py).

Usage:
    python run.py
"""
from app import create_app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5002)
