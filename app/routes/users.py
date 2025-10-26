# app/routes/users.py
from __future__ import annotations
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify
from app.db.repos import UsersRepo
from datetime import date, timedelta
import random
from typing import Optional

bp = Blueprint("users", __name__)

def _repo():
    """Obtiene el repositorio de usuarios con la ruta de BD configurada."""
    db_path = current_app.config["USERS_DB_PATH"]
    return UsersRepo(db_path)

# -----------------------------
# Pools de nombres realistas
# -----------------------------
_MALE_NAMES = [
    "Alejandro","Bruno","Carlos","Diego","Eduardo","Fernando","Gonzalo","Hugo","Iván","Javier",
    "Luis","Manuel","Nicolás","Óscar","Pablo","Quique","Raúl","Sergio","Tomás","Víctor"
]
_FEMALE_NAMES = [
    "Ana","Beatriz","Carla","Diana","Elena","Fátima","Gloria","Helena","Irene","Julia",
    "Laura","Marta","Noelia","Olivia","Patricia","Raquel","Sara","Teresa","Uxía","Valeria"
]
_SURNAMES = [
    "García","Rodríguez","González","Fernández","López","Martínez","Sánchez","Pérez","Gómez","Martín",
    "Jiménez","Ruiz","Hernández","Díaz","Moreno","Muñoz","Álvarez","Romero","Alonso","Gutiérrez"
]

def _rand_dob(min_age=18, max_age=55) -> str:
    """Fecha de nacimiento razonable ISO yyyy-mm-dd."""
    years = random.randint(min_age, max_age)
    extra_days = random.randint(0, 364)
    d = date.today() - timedelta(days=years*365 + extra_days)
    return d.isoformat()

def _age_from_dob(dob_iso: str) -> int:
    try:
        y, m, d = map(int, dob_iso.split("-"))
        born = date(y, m, d)
        today = date.today()
        return today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    except Exception:
        return random.randint(20, 50)

def _rand_weight(sexo: str) -> float:
    """Peso aproximado por sexo."""
    if (sexo or "").upper() == "F":
        # 52–76 kg aprox
        return round(random.uniform(52, 76), 1)
    # 64–96 kg aprox
    return round(random.uniform(64, 96), 1)

def _nickname_from(nombre: str) -> str:
    """Apodo corto a partir del nombre."""
    base = nombre.strip()
    # Si es muy corto, lo dejamos; si no, primeras 3–5 letras
    if len(base) <= 5:
        return base
    return base[:random.choice([3,4,5])]

# -----------------------------
# FCR (Frecuencia Cardíaca en Reposo)
# -----------------------------
def _rand_hr_rest(sexo: str, edad: int) -> int:
    """
    Genera una FCR realista en función de sexo/edad (ligero ajuste).
    Rangos típicos saludables en reposo:
      - Hombres: ~52–70 lpm
      - Mujeres: ~55–74 lpm
    Subimos 1–3 lpm para edades más altas dentro del rango.
    """
    sexo = (sexo or "M").upper()
    if sexo == "F":
        base = random.randint(55, 74)
    else:
        base = random.randint(52, 70)
    # Ajuste suave por edad (más edad → ligeramente más alta)
    if edad >= 45:
        base += random.choice([1, 2, 3])
    elif edad <= 25:
        base -= random.choice([0, 1])  # quizá algo más baja si muy joven
    return max(45, min(85, base))

def _gen_sim_user(n: int) -> dict:
    """
    Genera un usuario simulado coherente.
    30% de probabilidad de tener FCR (hr_rest).
    """
    sexo = "M" if (n % 2 == 0) else "F"
    nombre = random.choice(_MALE_NAMES if sexo == "M" else _FEMALE_NAMES)
    apellido = random.choice(_SURNAMES)
    if random.random() < 0.35:
        apellido = f"{apellido} {random.choice(_SURNAMES)}"

    apodo = _nickname_from(nombre)
    dob = _rand_dob()
    peso = _rand_weight(sexo)

    # 30% de probabilidad de tener FCR
    if random.random() < 0.3:
        # rangos realistas
        hr_rest = random.randint(55, 74) if sexo == "F" else random.randint(52, 70)
    else:
        hr_rest = None  # sin FCR

    return {
        "nombre": nombre,
        "apellido": apellido,
        "apodo": apodo,
        "sexo": sexo,
        "dob": dob,
        "peso": peso,
        "device_id": None,
        "hr_rest": hr_rest,   # puede ser None
        "hr_max": None,
        "hr_max_auto": 1,
        "is_sim": 1,
        "edad": None,
    }



# ============================================
# LISTADO DE USUARIOS
# ============================================
@bp.get("/")
def index():
    repo = _repo()
    users = repo.list(limit=1000)
    # (opcional) cuenta de simulados para precargar un control en la UI
    sim_count = sum(1 for u in users if int(u.get("is_sim") or 0) == 1)
    return render_template("users/list.html", users=users, sim_count=sim_count)

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
            is_sim=1 if data.get("is_sim") else 0,
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
            is_sim=1 if data.get("is_sim") else int(user.get("is_sim") or 0),
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

# ============================================
# AJUSTAR NÚMERO DE USUARIOS SIMULADOS
# ============================================
@bp.post("/simulated")
def set_simulated_users():
    """
    Crea o elimina usuarios simulados hasta alcanzar el número indicado.
    Acepta count vía JSON, form-data o querystring.
    """
    # 1) Leer 'count' desde varios sitios
    payload = request.get_json(silent=True) or {}
    count_candidates = [
        payload.get("count"),
        request.form.get("count"),
        request.args.get("count"),
    ]
    count_val = None
    for c in count_candidates:
        if c is None or c == "":
            continue
        try:
            count_val = int(c)
            break
        except Exception:
            pass
    if count_val is None:
        return jsonify({"ok": False, "error": "Falta 'count' (int) en JSON, form o querystring."}), 400
    if count_val < 0:
        return jsonify({"ok": False, "error": "'count' debe ser >= 0"}), 400

    target = count_val

    repo = _repo()
    try:
        users = repo.list(limit=10000)
    except Exception as e:
        return jsonify({"ok": False, "error": f"DB list() fallo: {e}"}), 500

    sim_users = [u for u in users if int(u.get("is_sim") or 0) == 1]
    current = len(sim_users)

    # 2) Nada que hacer
    if current == target:
        return jsonify({"ok": True, "count": target, "created": 0, "deleted": 0})

    created = 0
    deleted = 0
    errors: list[str] = []

    # 3) Crear faltantes
    if current < target:
        to_add = target - current
        for i in range(to_add):
            u = _gen_sim_user(n=current + i)
            try:
                repo.create(
                    nombre=u["nombre"],
                    apellido=u["apellido"],
                    apodo=u["apodo"],
                    sexo=u["sexo"],
                    dob=u["dob"],
                    peso=u["peso"],
                    device_id=None,
                    hr_rest=(int(u["hr_rest"]) if u["hr_rest"] is not None else None),
                    hr_max=None,
                    hr_max_auto=1,
                    is_sim=1,
                )
                created += 1
            except Exception as e:
                errors.append(f"create[{i}]: {e}")

    # 4) Borrar sobrantes
    else:
        to_del = current - target
        sim_users_sorted = sorted(sim_users, key=lambda u: int(u["id"]), reverse=True)
        for u in sim_users_sorted[:to_del]:
            try:
                repo.delete(int(u["id"]))
                deleted += 1
            except Exception as e:
                errors.append(f"delete[id={u['id']}]: {e}")

    status_code = 200 if not errors else 207  # 207 Multi-Status si hubo mixto
    return jsonify({
        "ok": (len(errors) == 0),
        "count": target,
        "created": created,
        "deleted": deleted,
        "errors": errors or None
    }), status_code


# (Opcional) consultar el número actual sin recargar toda la lista
@bp.get("/simulated")
def get_simulated_count():
    repo = _repo()
    users = repo.list(limit=10000)
    sim_count = sum(1 for u in users if int(u.get("is_sim") or 0) == 1)
    return jsonify({"ok": True, "count": sim_count})
