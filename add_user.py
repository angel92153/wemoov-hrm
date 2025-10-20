# add_user.py
import db

db.init_db()

uid = db.create_user(
    nombre="Angel",
    apellido="Diaz",
    apodo="Angel",
    edad=33,
    peso=65,
    device_id=10002,
    sexo="M"   # 🔹 "M" para masculino, "F" para femenino
)

print(f"✅ Usuario creado con ID {uid}")
