import pyautogui
import time
from datetime import datetime

print("=" * 60)
print("マウス座標確認ツール")
print("=" * 60)
print()
print("5秒後に開始します")
print("Ctrl+C で終了")
print()
print("確認したい場所へマウスを移動してください")
print()
print("例:")
print("  1. Clineの「+ New Chat」")
print("  2. Cline入力欄")
print()
print("=" * 60)

time.sleep(5)

while True:
    try:
        x, y = pyautogui.position()

        print(
            f"\r[{datetime.now():%H:%M:%S}] "
            f"X={x:4d}  Y={y:4d}",
            end="",
            flush=True,
        )

        time.sleep(0.1)

    except KeyboardInterrupt:
        print()
        print()
        print("終了しました")
        break