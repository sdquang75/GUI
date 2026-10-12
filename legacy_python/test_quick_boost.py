"""
Quick 5-second test of Gamma 1.8 + Black Boost + NVIDIA Vibrance.
Automatically restores normal settings after 5 seconds.
"""

import time
from color_engine import GammaEngine, UnifiedVibranceEngine

def main():
    print("=" * 60)
    print("  TESTING GAMER MODE (Gamma 1.8 + 40% Black Boost + 75% Vibrance)")
    print("  Màn hình sẽ sáng rõ vùng tối trong 5 giây, sau đó tự hồi phục.")
    print("=" * 60)

    gamma = GammaEngine()
    nv = UnifiedVibranceEngine()

    print("\n[+] Đang kích hoạt profile Gamer...")
    gamma.apply_curve(gamma=1.80, black_boost=0.40)
    if nv.available:
        nv.set_level(75)

    for i in range(5, 0, -1):
        print(f"    Tự động reset về bình thường sau: {i}s...")
        time.sleep(1)

    print("\n[+] Khôi phục lại trạng thái mặc định của Windows...")
    gamma.restore()
    if nv.available:
        nv.restore()
    print("[✔] Đã khôi phục hoàn toàn. Test thành công!\n")

if __name__ == '__main__':
    main()
