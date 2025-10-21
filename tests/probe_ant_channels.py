"""
Prueba estable de capacidad SIMULTÁNEA:
- Abre 1 wildcard.
- Va abriendo canales dedicados uno a uno hasta que falle.
- Mantiene todo abierto 'hold' segundos y cierra ordenadamente.

Evita los ciclos de abrir/cerrar que en Windows causan 'Access denied'.
"""

import sys, time, argparse

def _close_unassign(ch):
    try: ch.on_broadcast_data = lambda *_: None
    except Exception: pass
    try: ch.close()
    except Exception as e:
        if "CHANNEL_IN_WRONG_STATE" not in str(e):
            raise
    try: ch.unassign()
    except Exception: pass

def _open_wildcard(node, Channel, rf=57, period=8070, devtype=120):
    ch = node.new_channel(Channel.Type.BIDIRECTIONAL_RECEIVE)
    ch.set_rf_freq(rf); ch.set_period(period); ch.set_id(0, devtype, 0)
    try: ch.enable_extended_messages(True)
    except Exception: pass
    try: node.enable_extended_messages(True)  # type: ignore[attr-defined]
    except Exception: pass
    try: ch.set_search_timeout(255); ch.set_low_priority_search_timeout(255)
    except Exception: pass
    ch.on_broadcast_data = lambda *_: None
    ch.open()
    return ch

def _open_dedicated(node, Channel, dev_id, rf=57, period=8070, devtype=120):
    ch = node.new_channel(Channel.Type.BIDIRECTIONAL_RECEIVE)
    ch.set_rf_freq(rf); ch.set_period(period); ch.set_id(dev_id, devtype, 0)
    try: ch.enable_extended_messages(True)
    except Exception: pass
    ch.on_broadcast_data = lambda *_: None
    ch.open()
    return ch

def main():
    ap = argparse.ArgumentParser(description="ANT+ open-once capacity probe")
    ap.add_argument("--network", type=int, default=0x00)
    ap.add_argument("--key", type=str, default="B9A521FBBD72C345", help="16 hex chars")
    ap.add_argument("--maxk", type=int, default=16, help="máximo dedicados a intentar")
    ap.add_argument("--hold", type=float, default=5.0, help="segundos de mantenimiento")
    args = ap.parse_args()

    try:
        from openant.easy.node import Node
        from openant.easy.channel import Channel
    except ImportError:
        print("❌ Falta 'openant'. pip install openant")
        sys.exit(1)

    hexkey = args.key.strip()
    if len(hexkey) != 16:
        print("⚠️ Clave inválida, usando pública.")
        hexkey = "B9A521FBBD72C345"
    KEY = [int(hexkey[i:i+2], 16) for i in range(0, 16, 2)]

    node = Node()
    try:
        node.set_network_key(args.network, KEY)
    except Exception as e:
        print(f"⚠️ set_network_key: {e}")

    chans = []
    max_opened = 0
    try:
        # 1) Wildcard
        wc = _open_wildcard(node, Channel)
        chans.append(wc)
        print("✔️ Wildcard abierto")

        # 2) Añadir dedicados uno a uno
        base = 60000
        for k in range(1, args.maxk + 1):
            dev_id = base + k
            try:
                ch = _open_dedicated(node, Channel, dev_id)
                chans.append(ch)
                max_opened = k
                print(f"✔️ Dedicado {k} abierto (dev {dev_id})")
                time.sleep(0.05)  # pequeño respiro
            except Exception as e:
                print(f"🧩 Stop al intentar dedicado {k}: {e}")
                break

        print(f"⏳ Manteniendo {1 + max_opened} canales abiertos ({max_opened} dedicados) durante {args.hold:.1f}s…")
        time.sleep(args.hold)

    finally:
        # Cierre ordenado
        for ch in chans:
            try: _close_unassign(ch)
            except Exception: pass
        try: node.stop()
        except Exception: pass
        try: node.driver.close()
        except Exception: pass
        time.sleep(0.3)

    print("\n===== RESULTADO =====")
    print(f"Dedicados abiertos a la vez: {max_opened}")
    if max_opened > 0:
        print(f"➡️ MAX_DEDICATED_CHANNELS recomendado = {max_opened} (1 wildcard + {max_opened} dedicados)")
    else:
        print("⚠️ No se pudo abrir ningún dedicado. ¿otro proceso usando el stick?")
    print("=====================")

if __name__ == "__main__":
    main()
