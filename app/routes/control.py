from flask import Blueprint, render_template

bp = Blueprint("control", __name__)

@bp.get("/control")
def control():
    return render_template("control.html")
