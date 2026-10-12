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

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool DestroyIcon(IntPtr hIcon);

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

            // 1. Check disk cache
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
                    return bmp;
                }
                catch { }
            }

            // 2. Try extract from specified exe path
            if (!string.IsNullOrEmpty(exePath) && File.Exists(exePath))
            {
                var extracted = ExtractFromExe(exePath, size);
                if (extracted != null)
                {
                    try { extracted.Save(cachePath, System.Drawing.Imaging.ImageFormat.Png); } catch { }
                    return extracted;
                }
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

        private static Image? ExtractFromExe(string path, int size)
        {
            try
            {
                IntPtr[] largeIcons = new IntPtr[1];
                IntPtr[] smallIcons = new IntPtr[1];
                uint count = ExtractIconEx(path, 0, largeIcons, smallIcons, 1);

                IntPtr hIcon = IntPtr.Zero;
                if (count > 0 && largeIcons[0] != IntPtr.Zero)
                {
                    hIcon = largeIcons[0];
                }
                else if (count > 0 && smallIcons[0] != IntPtr.Zero)
                {
                    hIcon = smallIcons[0];
                }

                if (hIcon != IntPtr.Zero)
                {
                    using var icon = Icon.FromHandle(hIcon);
                    var bmp = new Bitmap(size, size);
                    using (var g = Graphics.FromImage(bmp))
                    {
                        g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                        g.DrawIcon(icon, new Rectangle(0, 0, size, size));
                    }
                    DestroyIcon(hIcon);
                    if (smallIcons[0] != IntPtr.Zero) DestroyIcon(smallIcons[0]);
                    return bmp;
                }

                // Fallback to ExtractAssociatedIcon
                using var assocIcon = Icon.ExtractAssociatedIcon(path);
                if (assocIcon != null)
                {
                    var bmp = new Bitmap(size, size);
                    using (var g = Graphics.FromImage(bmp))
                    {
                        g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                        g.DrawIcon(assocIcon, new Rectangle(0, 0, size, size));
                    }
                    return bmp;
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
