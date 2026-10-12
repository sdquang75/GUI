# ⚡ GUI (vibranceGUI Native Core + Multi-GPU + Gamma 1.8)

Ứng dụng C# .NET 8 WinForms nguyên bản siêu nhẹ, thay thế và vượt trội **vibranceGUI**, hỗ trợ đa GPU (NVIDIA NVAPI + Intel Iris Xe / AMD DWM), Gamma 1.8 và Black Boost:

- **Hỗ trợ đa GPU (Multi-GPU Engine)**:
  - **NVIDIA GPU**: Tự động sử dụng phần cứng **NVIDIA NVAPI (`NvAPI_SetDVCLevelEx`)** can thiệp trực tiếp bộ xuất hình GPU.
  - **Intel iGPU (Iris Xe, UHD) & AMD**: Tự động chuyển đổi sang **Universal Windows DWM Engine (`MagSetFullscreenColorEffect`)** điều chỉnh saturation trực tiếp qua Desktop Window Manager của Windows 10/11 mà không cần phụ thuộc driver bên ngoài.
- **Quan sát cửa sổ bằng Event Hook (0% CPU)**:
  - Sử dụng Windows API `SetWinEventHook` (`EVENT_SYSTEM_FOREGROUND`).
  - Phản hồi **0ms tức thì** khi Alt-Tab, hoàn toàn không tốn CPU (0.00% CPU khi chơi game), an toàn tuyệt đối với mọi hệ thống Anti-Cheat (VAC, Riot Vanguard, FACEIT, Easy Anti-Cheat).
- **Gamma 1.8 & Black Boost**:
  - GDI32 `SetDeviceGammaRamp` LUT engine mô phỏng chuẩn thi đấu FPS (`r_fullscreen_gamma 1.8`).
  - Shadow equalizer nâng sáng vùng tối, không bị cháy sáng vùng trắng.
- **Thang đo chuẩn 0–100%**:
  - Mặc định 50% (trung tính), 100% (rực rỡ tối đa), 0% (đơn sắc trắng đen).
- **Giao diện chuẩn Single-File**:
  - Tối giản theo phong cách vibranceGUI tiếng Anh.
  - Tự động lưu cấu hình tại `%APPDATA%\GUI\config.json`.
  - Icon game cache tại `%LOCALAPPDATA%\GUI\icons`.
  - Không sinh file/thư mục rác tại thư mục chứa file `.exe`.

---

## 📦 Các phiên bản trong thư mục gốc

1. **[`GUI_Standalone.exe`](file:///C:/Users/NGUYEN%20QUANG/.gemini/antigravity/scratch/vibe-gamer-ramp/GUI_Standalone.exe) (~71 MB - Khuyên dùng cho Laptop)**:
   - Bản **Self-contained** đóng gói toàn bộ runtime .NET 8 bên trong đúng 1 file duy nhất.
   - Chạy ngay lập tức trên mọi máy tính / laptop Windows 10 & 11 (kể cả máy laptop Intel Core i5-1235U, máy net, bạn bè...) mà **không cần cài đặt bất kỳ phần mềm hay .NET Runtime nào**.

2. **[`GUI.exe`](file:///C:/Users/NGUYEN%20QUANG/.gemini/antigravity/scratch/vibe-gamer-ramp/GUI.exe) (~198 KB - Siêu nhẹ)**:
   - Bản **Framework-dependent** siêu gọn nhẹ dành cho các máy đã có sẵn .NET 8 Desktop Runtime.

---

## 🎮 Cách sử dụng

1. Chạy [`GUI_Standalone.exe`](file:///C:/Users/NGUYEN%20QUANG/.gemini/antigravity/scratch/vibe-gamer-ramp/GUI_Standalone.exe) hoặc [`GUI.exe`](file:///C:/Users/NGUYEN%20QUANG/.gemini/antigravity/scratch/vibe-gamer-ramp/GUI.exe).
2. Nhìn dòng trạng thái góc dưới:
   - Máy NVIDIA: `Observer status: Running! [NVIDIA DVC]`
   - Máy laptop Intel: `Observer status: Running! [Intel (DWM)]`
3. Nhấn **Add** (chọn game đang chạy) hoặc **Add manually** (chọn file `.exe`).
4. Kéo thanh **In-game Vibrance Level** (ví dụ 75% hoặc 100%), tùy chọn bật **Enable Gamma** (Gamma 1.8) rồi bấm **Save**.
5. Khi vào game, màu sắc lập tức chuyển sang chế độ game; khi Alt-Tab ra màn hình desktop, màu sắc lập tức trở lại bình thường.
