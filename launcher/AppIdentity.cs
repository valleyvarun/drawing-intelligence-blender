namespace DrawingIntelligence
{
    // Names shared by the launcher, the engine branding step and the setup wizard.
    // The Python application keeps matching values in app/drawing_app/core/identity.py.
    public static class AppIdentity
    {
        public const string Name = "Drawing Intelligence";
        public const string Publisher = "valleyvarun";
        // Taskbar identity. It replaces the engine's own ID in place, so it has at most 11 characters.
        public const string UserModelId = "DrawIntel.1";
        public const string Executable = "Drawing Intelligence.exe";
        public const string EngineFolder = "engine";
        public const string Template = "drawing_intelligence";
        public const string EngineVersion = "5.2.2";
    }
}
