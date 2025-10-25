from flask import Blueprint, render_template

bp = Blueprint("screens", __name__)

@bp.get("/")
def index():
    return render_template("screen_1.html")
