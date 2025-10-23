# app/routes/users.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from app.db.repos import UsersRepo

bp = Blueprint("users", __name__)

def _repo():
    """Obtiene el repositorio de usuarios con la ruta de BD configurada."""
    db_path = current_app.config["USERS_DB_PATH"]
    return UsersRepo(db_path)

# ============================================
# LISTADO DE USUARIOS
# ============================================
@bp.get("/")
def index():
    repo = _repo()
    users = repo.list(limit=1000)
    return render_template("users/list.html", users=users)

# ============================================
# CREAR USUARIO
# ============================================
@bp.route("/add", methods=["GET", "POST"])
def add():
    repo = _repo()
    if request.method == "POST":
        data = request.form.to_dict()
        repo.create(
            nombre=data.get("nombre", ""),
            apellido=data.get("apellido", ""),
            apodo=data.get("apodo", ""),
            sexo=data.get("sexo", "M"),
            dob=data.get("dob"),
            peso=float(data["peso"]) if data.get("peso") else None,
            device_id=int(data["device_id"]) if data.get("device_id") else None,
            hr_rest=int(data["hr_rest"]) if data.get("hr_rest") else None,
            hr_max=int(data["hr_max"]) if data.get("hr_max") else None,
            hr_max_auto=1 if data.get("hr_max_auto") else 0,
        )
        flash("Usuario creado correctamente", "success")
        return redirect(url_for("users.index"))
    return render_template("users/form.html", user=None)

# ============================================
# EDITAR USUARIO
# ============================================
@bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
def edit(user_id: int):
    repo = _repo()
    user = repo.get(user_id)
    if not user:
        flash("Usuario no encontrado", "error")
        return redirect(url_for("users.index"))

    if request.method == "POST":
        data = request.form.to_dict()
        repo.update(
            user_id,
            nombre=data.get("nombre", ""),
            apellido=data.get("apellido", ""),
            apodo=data.get("apodo", ""),
            sexo=data.get("sexo", "M"),
            dob=data.get("dob"),
            peso=float(data["peso"]) if data.get("peso") else None,
            device_id=int(data["device_id"]) if data.get("device_id") else None,
            hr_rest=int(data["hr_rest"]) if data.get("hr_rest") else None,
            hr_max=int(data["hr_max"]) if data.get("hr_max") else None,
            hr_max_auto=1 if data.get("hr_max_auto") else 0,
        )
        flash("Cambios guardados", "success")
        return redirect(url_for("users.index"))

    return render_template("users/form.html", user=user)

# ============================================
# BORRAR USUARIO
# ============================================
@bp.get("/<int:user_id>/delete")
def delete(user_id: int):
    repo = _repo()
    repo.delete(user_id)
    flash("Usuario eliminado", "success")
    return redirect(url_for("users.index"))
