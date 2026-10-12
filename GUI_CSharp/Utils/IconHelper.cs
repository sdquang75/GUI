using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Runtime.InteropServices;

namespace GUI.Utils
{
    public static class IconHelper
    {
        [DllImport("shell32.dll", CharSet = CharSet.Auto)]
        private static extern uint ExtractIconEx(string szFileName, int nIconIndex, IntPtr[] phiconLarge, IntPtr[] phiconSmall, uint nIcons);

        [DllImport("user32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
        private static extern uint PrivateExtractIconsW(string szFileName, int nIconIndex, int cxIcon, int cyIcon, IntPtr[] phicon, uint[] piconid, uint nIcons, uint flags);

        [DllImport("shell32.dll", CharSet = CharSet.Auto)]
        private static extern IntPtr SHGetFileInfo(string pszPath, uint dwFileAttributes, ref SHFILEINFO psfi, uint cbFileInfo, uint uFlags);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool DestroyIcon(IntPtr hIcon);

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Auto)]
        private struct SHFILEINFO
        {
            public IntPtr hIcon;
            public int iIcon;
            public uint dwAttributes;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)]
            public string szDisplayName;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 80)]
            public string szTypeName;
        }

        private const uint SHGFI_ICON = 0x000000100;
        private const uint SHGFI_LARGEICON = 0x000000000;

        private static string IconsDirectory
        {
            get
            {
                string localDir = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "icons");
                if (Directory.Exists(localDir)) return localDir;

                string appDataDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "GUI", "icons");
                if (!Directory.Exists(appDataDir)) Directory.CreateDirectory(appDataDir);
                return appDataDir;
            }
        }

        public static Image GetGameIcon(string exeName, string? exePath = null, int size = 48)
        {
            string cachePath = Path.Combine(IconsDirectory, $"{exeName.ToLowerInvariant()}.png");

            // 1. Try extract directly from specified exe path first so valid paths always show real icon & heal bad cache
            if (!string.IsNullOrEmpty(exePath) && File.Exists(exePath))
            {
                var extracted = ExtractFromExe(exePath, size);
                if (extracted != null)
                {
                    try { extracted.Save(cachePath, System.Drawing.Imaging.ImageFormat.Png); } catch { }
                    return extracted;
                }
            }

            // 2. Check disk cache (validate alpha channel to reject/heal corrupted 0-alpha PNGs)
            if (File.Exists(cachePath))
            {
                try
                {
                    byte[] bytes = File.ReadAllBytes(cachePath);
                    using var ms = new MemoryStream(bytes);
                    using var orig = Image.FromStream(ms);
                    var bmp = new Bitmap(size, size, System.Drawing.Imaging.PixelFormat.Format32bppArgb);
                    using (var g = Graphics.FromImage(bmp))
                    {
                        g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                        g.DrawImage(orig, 0, 0, size, size);
                    }
                    if (EnsureVisibleBitmap(bmp))
                    {
                        return bmp;
                    }
                    bmp.Dispose();
                }
                catch { }
            }

            // 3. Search running processes if path not provided or failed
            string simpleName = Path.GetFileNameWithoutExtension(exeName).ToLowerInvariant();
            foreach (var proc in Process.GetProcesses())
            {
                try
                {
                    if (proc.ProcessName.ToLowerInvariant() == simpleName)
                    {
                        string procPath = ProcessHelper.GetProcessPath(proc.Id);
                        if (!string.IsNullOrEmpty(procPath) && File.Exists(procPath))
                        {
                            var extracted = ExtractFromExe(procPath, size);
                            if (extracted != null)
                            {
                                try { extracted.Save(cachePath, System.Drawing.Imaging.ImageFormat.Png); } catch { }
                                return extracted;
                            }
                        }
                    }
                }
                catch { }
            }

            // 4. Fallback badge
            return CreateBadge(exeName, size);
        }

        private static Bitmap? RenderHIconToBitmap(IntPtr hIcon, int size)
        {
            if (hIcon == IntPtr.Zero) return null;
            try
            {
                using var icon = Icon.FromHandle(hIcon);
                var bmp = new Bitmap(size, size, System.Drawing.Imaging.PixelFormat.Format32bppArgb);
                using (var g = Graphics.FromImage(bmp))
                {
                    g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                    g.SmoothingMode = SmoothingMode.HighQuality;
                    g.PixelOffsetMode = PixelOffsetMode.HighQuality;
                    try
                    {
                        using var iconBmp = icon.ToBitmap();
                        g.DrawImage(iconBmp, new Rectangle(0, 0, size, size));
                    }
                    catch
                    {
                        g.DrawIcon(icon, new Rectangle(0, 0, size, size));
                    }
                }

                if (EnsureVisibleBitmap(bmp))
                {
                    return bmp;
                }

                // Retry with DrawIcon if ToBitmap produced an empty image
                using (var g = Graphics.FromImage(bmp))
                {
                    g.Clear(Color.Transparent);
                    g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                    g.DrawIcon(icon, new Rectangle(0, 0, size, size));
                }

                if (EnsureVisibleBitmap(bmp))
                {
                    return bmp;
                }

                bmp.Dispose();
            }
            catch { }
            return null;
        }

        /// <summary>
        /// Checks whether the bitmap has any visible pixels (A > 10).
        /// If all pixels have A == 0 but RGB data exists (24-bit icon alpha bug), restores A = 255.
        /// </summary>
        private static bool EnsureVisibleBitmap(Bitmap bmp)
        {
            bool hasVisibleAlpha = false;
            bool hasNonZeroRgb = false;

            for (int y = 0; y < bmp.Height; y++)
            {
                for (int x = 0; x < bmp.Width; x++)
                {
                    Color c = bmp.GetPixel(x, y);
                    if (c.A > 10)
                    {
                        hasVisibleAlpha = true;
                        break;
                    }
                    if (c.R > 0 || c.G > 0 || c.B > 0)
                    {
                        hasNonZeroRgb = true;
                    }
                }
                if (hasVisibleAlpha) break;
            }

            if (hasVisibleAlpha) return true;

            if (hasNonZeroRgb)
            {
                for (int y = 0; y < bmp.Height; y++)
                {
                    for (int x = 0; x < bmp.Width; x++)
                    {
                        Color c = bmp.GetPixel(x, y);
                        if (c.R > 0 || c.G > 0 || c.B > 0)
                        {
                            bmp.SetPixel(x, y, Color.FromArgb(255, c.R, c.G, c.B));
                        }
                    }
                }
                return true;
            }

            return false;
        }

        private static Image? ExtractFromExe(string path, int size)
        {
            // Method 1: PrivateExtractIconsW (extracts native 48x48 / high-res icon directly)
            try
            {
                IntPtr[] hIcons = new IntPtr[1];
                uint[] iconIds = new uint[1];
                uint extracted = PrivateExtractIconsW(path, 0, size, size, hIcons, iconIds, 1, 0);
                if (extracted > 0 && hIcons[0] != IntPtr.Zero)
                {
                    try
                    {
                        var bmp = RenderHIconToBitmap(hIcons[0], size);
                        if (bmp != null) return bmp;
                    }
                    finally
                    {
                        DestroyIcon(hIcons[0]);
                    }
                }
            }
            catch { }

            // Method 2: ExtractIconEx
            try
            {
                IntPtr[] largeIcons = new IntPtr[1];
                IntPtr[] smallIcons = new IntPtr[1];
                uint count = ExtractIconEx(path, 0, largeIcons, smallIcons, 1);
                try
                {
                    IntPtr hIcon = IntPtr.Zero;
                    if (count > 0 && largeIcons[0] != IntPtr.Zero)
                        hIcon = largeIcons[0];
                    else if (count > 0 && smallIcons[0] != IntPtr.Zero)
                        hIcon = smallIcons[0];

                    var bmp = RenderHIconToBitmap(hIcon, size);
                    if (bmp != null) return bmp;
                }
                finally
                {
                    if (largeIcons[0] != IntPtr.Zero) DestroyIcon(largeIcons[0]);
                    if (smallIcons[0] != IntPtr.Zero) DestroyIcon(smallIcons[0]);
                }
            }
            catch { }

            // Method 3: Icon.ExtractAssociatedIcon
            try
            {
                using var assocIcon = Icon.ExtractAssociatedIcon(path);
                if (assocIcon != null)
                {
                    var bmp = RenderHIconToBitmap(assocIcon.Handle, size);
                    if (bmp != null) return bmp;
                }
            }
            catch { }

            // Method 4: SHGetFileInfo Shell Icon fallback
            try
            {
                var shinfo = new SHFILEINFO();
                IntPtr res = SHGetFileInfo(path, 0, ref shinfo, (uint)Marshal.SizeOf(shinfo), SHGFI_ICON | SHGFI_LARGEICON);
                if (res != IntPtr.Zero && shinfo.hIcon != IntPtr.Zero)
                {
                    try
                    {
                        var bmp = RenderHIconToBitmap(shinfo.hIcon, size);
                        if (bmp != null) return bmp;
                    }
                    finally
                    {
                        DestroyIcon(shinfo.hIcon);
                    }
                }
            }
            catch { }

            return null;
        }

        private static Icon? _cachedAppIcon;

        public static Icon GetAppIcon()
        {
            if (_cachedAppIcon != null) return _cachedAppIcon;
            try
            {
                // 1. Try embedded app.ico from assembly
                var asm = System.Reflection.Assembly.GetExecutingAssembly();
                using var stream = asm.GetManifestResourceStream("GUI.app.ico");
                if (stream != null)
                {
                    _cachedAppIcon = new Icon(stream);
                    return _cachedAppIcon;
                }

                // 2. Try local app.ico
                string localIco = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "app.ico");
                if (File.Exists(localIco))
                {
                    _cachedAppIcon = new Icon(localIco);
                    return _cachedAppIcon;
                }

                // 3. Try extract from exe
                string exePath = Process.GetCurrentProcess().MainModule?.FileName ?? "";
                if (File.Exists(exePath))
                {
                    var ico = Icon.ExtractAssociatedIcon(exePath);
                    if (ico != null)
                    {
                        _cachedAppIcon = ico;
                        return _cachedAppIcon;
                    }
                }
            }
            catch { }

            return SystemIcons.Application;
        }

        public static Image CreateBadge(string exeName, int size)
        {
            var bmp = new Bitmap(size, size);
            using var g = Graphics.FromImage(bmp);
            g.SmoothingMode = SmoothingMode.AntiAlias;

            // Rounded rectangle background
            using var brush = new SolidBrush(Color.FromArgb(59, 130, 246));
            using var path = GetRoundedRect(new Rectangle(2, 2, size - 4, size - 4), 8);
            g.FillPath(brush, path);

            // Text
            string shortName = Path.GetFileNameWithoutExtension(exeName);
            if (shortName.Length > 3) shortName = shortName.Substring(0, 3);
            shortName = shortName.ToUpperInvariant();

            using var font = new Font("Segoe UI", 10, FontStyle.Bold, GraphicsUnit.Point);
            using var textBrush = new SolidBrush(Color.White);
            var format = new StringFormat
            {
                Alignment = StringAlignment.Center,
                LineAlignment = StringAlignment.Center
            };
            g.DrawString(shortName, font, textBrush, new RectangleF(0, 0, size, size), format);

            return bmp;
        }

        private static GraphicsPath GetRoundedRect(Rectangle bounds, int radius)
        {
            int diameter = radius * 2;
            var path = new GraphicsPath();
            var arc = new Rectangle(bounds.Location, new Size(diameter, diameter));

            path.AddArc(arc, 180, 90);
            arc.X = bounds.Right - diameter;
            path.AddArc(arc, 270, 90);
            arc.Y = bounds.Bottom - diameter;
            path.AddArc(arc, 0, 90);
            arc.X = bounds.Left;
            path.AddArc(arc, 90, 90);
            path.CloseFigure();
            return path;
        }
    }
}
