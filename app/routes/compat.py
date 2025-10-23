from flask import Blueprint, jsonify
from app.services.session_manager import SESSION

bp = Blueprint("compat", __name__)

@bp.get("/session/status")
def session_status_alias():
    resp = jsonify(SESSION.status())
    resp.cache_control.no_store = True
    return resp
