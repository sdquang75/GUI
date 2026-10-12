using System;

namespace GUI.Engine
{
    public interface IVibranceEngine : IDisposable
    {
        bool IsAvailable { get; }
        string EngineName { get; }
        int DesktopLevel { get; set; }
        int GetLevel();
        bool SetLevel(int level);
        void Restore();
    }
}
