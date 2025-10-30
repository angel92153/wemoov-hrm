from flask import Blueprint, render_template

bp = Blueprint("screens", __name__)

@bp.get("/")
def index():
    # Redirige la home a la vista LIVE nueva:
    return render_template("screens/live.html")

@bp.get("/screen/live")
def screen_live():
    return render_template("screens/live.html")

@bp.get("/screen/summary")
def screen_summary():
    return render_template("screens/summary.html")
