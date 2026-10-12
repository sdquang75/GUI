# ⚡ Vibe Color & Gamer Profile (vibranceGUI Core + Gamma 1.8)

Ứng dụng tinh chỉnh màu sắc hiển thị cho game thủ trên Windows:
- **Cơ chế VibranceGUI 1:1**: Tự động chuyển Digital Vibrance lên mức game (ví dụ: 75%) khi vào game và **tự động hoàn trả 50% mặc định ngay lập tức khi Alt-Tab ra Desktop/Chrome/Discord**.
- **NVIDIA Hardware DVC (`NvAPI_SetDVCLevelEx`)**: Can thiệp trực tiếp phần cứng chip xuất hình GPU, tương thích 100% với game **Exclusive Fullscreen** (DirectX 11/12, Vulkan) và không gây hiện tượng đổi tông màu (Hue shift).
- **Gamma 1.8 chuẩn thi đấu FPS**: Tương đương lệnh `r_fullscreen_gamma 1.8` trong CS2.
- **Black Boost (Shadow Equalizer - tùy chọn)**: Nâng sáng vùng tối sâu, giữ nguyên 100% chi tiết vùng sáng cực đại (white point).
- **Modular Controls**: Có công tắc độc lập cho từng tính năng (Vibrance riêng, Gamma riêng, Black Boost riêng).

---

## 🚀 Cách chạy ứng dụng

1. **Khởi động ứng dụng**:
   - Click đúp vào file [`run.bat`](file:///C:/Users/NGUYEN%20QUANG/.gemini/antigravity/scratch/vibe-gamer-ramp/run.bat)
   - Hoặc gõ lệnh:
     ```bash
     python main.py
     ```

2. **Cơ chế tự động**:
   - Khi bật tùy chọn *"Tự động kích hoạt khi vào game & trả về mặc định khi Alt-Tab"*:
     - Vào game (`cs2.exe`, `valorant.exe`...) $\to$ màu sắc tự động chuyển sang cấu hình game.
     - Alt-Tab ra màn hình desktop $\to$ tự động trả về 50% mặc định của Windows.
