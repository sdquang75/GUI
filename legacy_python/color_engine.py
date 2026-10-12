"""
Color Engine for Windows Gamma Ramp & NVIDIA Digital Vibrance
Provides pure-software calibration:
- NVIDIA NVAPI Digital Vibrance (1:1 with vibranceGUI via NvAPI_SetDVCLevelEx)
- Custom Gamma (e.g. 1.8 vs 2.2)
- Black Boost (Shadow Equalizer)
"""

import ctypes
from ctypes import wintypes
import atexit

class RAMP(ctypes.Structure):
    _fields_ = [
        ('Red', wintypes.WORD * 256),
        ('Green', wintypes.WORD * 256),
        ('Blue', wintypes.WORD * 256)
    ]


class GammaEngine:
    def __init__(self):
        self.original_ramp = None
        self.last_applied_ramp = None
        self.is_custom_active = False
        self._get_dc = ctypes.windll.user32.GetDC
        self._release_dc = ctypes.windll.user32.ReleaseDC
        self._get_ramp = ctypes.windll.gdi32.GetDeviceGammaRamp
        self._set_ramp = ctypes.windll.gdi32.SetDeviceGammaRamp
        self.backup_original_ramp()
        atexit.register(self.restore)

    def backup_original_ramp(self):
        hdc = self._get_dc(None)
        if hdc:
            orig = RAMP()
            if self._get_ramp(hdc, ctypes.byref(orig)):
                self.original_ramp = orig
            self._release_dc(None, hdc)

    def apply_curve(self, gamma=1.8, black_boost=0.0):
        """
        Calculates and applies the custom LUT:
        - gamma: Target gamma (Windows standard baseline ~ 2.2).
        - black_boost: 0.0 to 1.0 non-linear shadow lift.
        """
        gamma = max(1.2, min(3.0, gamma))
        base_p = gamma / 2.2

        new_ramp = RAMP()

        for i in range(256):
            x = i / 255.0
            y_gamma = x ** base_p if x > 0 else 0.0
            lift = (1.0 - x) ** 2 * (x ** 0.6) * 1.5 * black_boost if x > 0 and black_boost > 0 else 0.0
            y = max(0.0, min(1.0, y_gamma + lift))

            val = int(y * 65535.0 + 0.5)
            val = max(0, min(65535, val))

            new_ramp.Red[i] = val
            new_ramp.Green[i] = val
            new_ramp.Blue[i] = val

        self.last_applied_ramp = new_ramp
        self.is_custom_active = True

        hdc = self._get_dc(None)
        success = False
        if hdc:
            success = bool(self._set_ramp(hdc, ctypes.byref(new_ramp)))
            self._release_dc(None, hdc)
        return success

    def enforce_if_needed(self):
        """If game reset gamma in Exclusive Fullscreen, force it back."""
        if not self.is_custom_active or not self.last_applied_ramp:
            return False
        hdc = self._get_dc(None)
        if not hdc:
            return False
        curr = RAMP()
        success = False
        if self._get_ramp(hdc, ctypes.byref(curr)):
            target_mid = self.last_applied_ramp.Red[128]
            if abs(curr.Red[128] - target_mid) > 800:
                self._set_ramp(hdc, ctypes.byref(self.last_applied_ramp))
                success = True
        self._release_dc(None, hdc)
        return success

    def reset_to_identity(self):
        """Write clean linear identity table (0..65535) directly to hardware."""
        identity = RAMP()
        for i in range(256):
            val = int((i / 255.0) * 65535.0 + 0.5)
            identity.Red[i] = val
            identity.Green[i] = val
            identity.Blue[i] = val
        hdc = self._get_dc(None)
        if hdc:
            self._set_ramp(hdc, ctypes.byref(identity))
            self._release_dc(None, hdc)

    def restore(self):
        """Restore default Windows gamma ramp."""
        self.is_custom_active = False
        self.last_applied_ramp = None
        if self.original_ramp:
            hdc = self._get_dc(None)
            if hdc:
                self._set_ramp(hdc, ctypes.byref(self.original_ramp))
                self._release_dc(None, hdc)
        else:
            self.reset_to_identity()


class NV_DISPLAY_DVC_INFO_EX(ctypes.Structure):
    _fields_ = [
        ('version', ctypes.c_uint32),
        ('currentLevel', ctypes.c_int32),
        ('minLevel', ctypes.c_int32),
        ('maxLevel', ctypes.c_int32),
        ('defaultLevel', ctypes.c_int32),
    ]


class NvidiaVibranceEngine:
    """
    Direct wrapper around NVIDIA NVAPI for Digital Vibrance Control (DVC).
    Uses NvAPI_SetDVCLevelEx which is 100% supported across all NVIDIA architectures.
    """
    def __init__(self):
        self.available = False
        self.desktop_level = 50  # NVIDIA standard default
        self.displays = []
        self._fn_set_dvc_ex = None
        self._fn_get_dvc = None

        try:
            self.nvapi = ctypes.WinDLL('nvapi64.dll')
            self.qi = self.nvapi.nvapi_QueryInterface
            self.qi.restype = ctypes.c_void_p
            self.qi.argtypes = [ctypes.c_uint32]

            fn_init = ctypes.WINFUNCTYPE(ctypes.c_int)(self.qi(0x0150E828))
            if fn_init() == 0:
                fn_enum_disp = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(self.qi(0x9ABDD40D))
                
                # Enumerate all active displays
                idx = 0
                while True:
                    h = ctypes.c_void_p()
                    if fn_enum_disp(idx, ctypes.byref(h)) != 0 or not h.value:
                        break
                    self.displays.append(h)
                    idx += 1

                if self.displays:
                    # 0x4A82C2B1: NvAPI_SetDVCLevelEx (Primary hardware setter)
                    addr_ex = self.qi(0x4A82C2B1)
                    if addr_ex:
                        self._fn_set_dvc_ex = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(NV_DISPLAY_DVC_INFO_EX))(addr_ex)

                    self._fn_get_dvc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(NV_DISPLAY_DVC_INFO_EX))(self.qi(0x0E45002D))
                    
                    if self._fn_set_dvc_ex and self._fn_get_dvc:
                        self.available = True
                        atexit.register(self.restore)
        except Exception:
            self.available = False

    def _get_info(self, disp_handle=None):
        target = disp_handle or (self.displays[0] if self.displays else None)
        if not target or not self._fn_get_dvc:
            return None
        info = NV_DISPLAY_DVC_INFO_EX()
        info.version = ctypes.sizeof(NV_DISPLAY_DVC_INFO_EX) | (1 << 16)
        if self._fn_get_dvc(target, 0, ctypes.byref(info)) == 0:
            return info
        return None

    def get_level(self):
        info = self._get_info()
        return info.currentLevel if info else self.desktop_level

    def set_level(self, level):
        """Set Digital Vibrance in percentage (0 to 100, standard is 50). Works in Fullscreen!"""
        if not self.available or not self._fn_set_dvc_ex:
            return False
        level = max(0, min(100, int(level)))
        success = True
        
        info = NV_DISPLAY_DVC_INFO_EX()
        info.version = ctypes.sizeof(NV_DISPLAY_DVC_INFO_EX) | (1 << 16)
        info.currentLevel = level

        for disp in self.displays:
            res = self._fn_set_dvc_ex(disp, 0, ctypes.byref(info))
            if res != 0:
                success = False
        return success

    def restore(self):
        """Always restore to desktop default level (50%)."""
        if self.available:
            self.set_level(self.desktop_level)


class UnifiedVibranceEngine:
    """
    Selects NVIDIA NVAPI when available.
    """
    def __init__(self):
        self.nv = NvidiaVibranceEngine()
        if self.nv.available:
            self.engine_type = "NVIDIA Hardware DVC (vibranceGUI 1:1)"
            self.active_engine = self.nv
        else:
            self.engine_type = "None"
            self.active_engine = None

    @property
    def available(self):
        return self.active_engine is not None and self.active_engine.available

    def set_level(self, level):
        if self.active_engine:
            return self.active_engine.set_level(level)
        return False

    def restore(self):
        if self.active_engine:
            self.active_engine.restore()


if __name__ == '__main__':
    gamma = GammaEngine()
    vib = UnifiedVibranceEngine()
    print("Gamma engine ready, Active Engine:", vib.engine_type, "Available:", vib.available)
    if vib.available:
        print("Current level:", vib.active_engine.get_level())
