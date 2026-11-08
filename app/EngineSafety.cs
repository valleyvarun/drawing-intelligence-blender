using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace DrawingIntelligence
{
    public static class EngineSafety
    {
        // Blender's thumbnail code uses this Shell API without checking its return value.
        public static void EnsureThumbnailDirectory()
        {
            StringBuilder path = new StringBuilder(260);
            bool found = SHGetSpecialFolderPathW(IntPtr.Zero, path, 0x28, false);
            string profile = path.ToString();
            if (!found || string.IsNullOrWhiteSpace(profile) || !Path.IsPathRooted(profile) ||
                Path.GetPathRoot(profile).Length < 2 || !Directory.Exists(profile))
            {
                throw new IOException("Windows could not provide a valid user profile folder for the engine's " +
                    "thumbnail cache. The engine was not started. Run Drawing Intelligence in your normal " +
                    "Windows session; restricted test sessions may not provide this folder.");
            }
        }

        [DllImport("shell32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool SHGetSpecialFolderPathW(IntPtr window, StringBuilder path, int folder,
            [MarshalAs(UnmanagedType.Bool)] bool create);
    }
}
