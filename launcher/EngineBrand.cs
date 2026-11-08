using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace DrawingIntelligence
{
    // Turns an official Blender runtime into the application's private engine.
    // The executable is renamed and receives the application's icon and version text;
    // Blender's drawing code, data files and license notices are retained.
    public static class EngineBrand
    {
        public const string MarkerName = "drawing-intelligence-engine.txt";
        // Increase when the branding steps change, so existing engines are branded again.
        private const string BrandRevision = "Branding revision 4";
        private const int RtIcon = 3;
        private const int RtGroupIcon = 14;
        private const int RtVersion = 16;
        private const string AppIconName = "APPICON";
        private const uint LoadAsDatafile = 0x00000002;
        private const uint LoadAsImageResource = 0x00000020;
        private const ushort DefaultLanguage = 0x0409;
        private const ushort UnicodeCodePage = 0x04B0;

        // Returns the branded executable. Safe to repeat on an engine that is already branded.
        public static string Apply(string engineDir, string iconPath)
        {
            string branded = Path.Combine(engineDir, AppIdentity.Executable);
            string original = Path.Combine(engineDir, "blender.exe");
            if (File.Exists(original))
            {
                if (File.Exists(branded)) File.Delete(branded);
                File.Move(original, branded);
            }
            if (!File.Exists(branded)) throw new FileNotFoundException("The engine executable is missing.", branded);

            // The vendor signature cannot stay valid after the resources change.
            StripSignature(branded);
            // A windows-subsystem program has no console window.
            UseWindowsSubsystem(branded);
            // The window exists before the application's code runs: brand its first title and taskbar group.
            ReplaceText(branded);
            Patch(branded, File.ReadAllBytes(iconPath));

            // Blender's console launcher and helper scripts only start blender.exe.
            foreach (string name in new[] { "blender-launcher.exe" })
            {
                string path = Path.Combine(engineDir, name);
                if (File.Exists(path)) File.Delete(path);
            }
            foreach (string script in Directory.GetFiles(engineDir, "blender_*.cmd"))
            {
                File.Delete(script);
            }
            File.WriteAllText(Path.Combine(engineDir, MarkerName),
                AppIdentity.Name + " engine" + Environment.NewLine +
                "Built from Blender " + AppIdentity.EngineVersion + " LTS (GPL-2.0-or-later)." + Environment.NewLine +
                BrandRevision + Environment.NewLine);
            return branded;
        }

        public static bool IsBranded(string engineDir)
        {
            string branded = Path.Combine(engineDir, AppIdentity.Executable);
            string marker = Path.Combine(engineDir, MarkerName);
            return File.Exists(branded) && File.Exists(marker) && File.ReadAllText(marker).Contains(BrandRevision) &&
                !File.Exists(Path.Combine(engineDir, "blender.exe")) && IsWindowsSubsystem(branded);
        }

        // ------------------------------------------------------------------
        // Resources
        // ------------------------------------------------------------------
        private struct ResourceRef
        {
            public ushort Id;
            public ushort Language;
        }

        private sealed class IconImage
        {
            public byte Width;
            public byte Height;
            public byte Colors;
            public ushort Planes;
            public ushort BitCount;
            public byte[] Data;
        }

        private static void Patch(string exe, byte[] ico)
        {
            List<IconImage> images = ReadIcon(ico);
            List<ushort> groupLanguages = new List<ushort>();
            List<ResourceRef> oldIcons = new List<ResourceRef>();
            List<ushort> versionLanguages = new List<ushort>();
            int nextIcon = Inspect(exe, groupLanguages, oldIcons, versionLanguages) + 1;
            if (nextIcon + images.Count > ushort.MaxValue) throw new InvalidDataException("The engine has too many icon resources.");
            ushort language = groupLanguages.Count > 0 ? groupLanguages[0] : DefaultLanguage;

            IntPtr update = BeginUpdateResource(exe, false);
            if (update == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "The engine could not be opened for branding");
            bool finished = false;
            try
            {
                foreach (ResourceRef icon in oldIcons) Check(UpdateResource(update, (IntPtr)RtIcon, (IntPtr)icon.Id, icon.Language, null, 0));
                foreach (ushort old in groupLanguages) Check(UpdateResource(update, (IntPtr)RtGroupIcon, AppIconName, old, null, 0));
                foreach (ushort old in versionLanguages) Check(UpdateResource(update, (IntPtr)RtVersion, (IntPtr)1, old, null, 0));
                for (int index = 0; index < images.Count; index++)
                {
                    byte[] data = images[index].Data;
                    Check(UpdateResource(update, (IntPtr)RtIcon, (IntPtr)(nextIcon + index), language, data, (uint)data.Length));
                }
                byte[] group = GroupIcon(images, nextIcon);
                Check(UpdateResource(update, (IntPtr)RtGroupIcon, AppIconName, language, group, (uint)group.Length));
                byte[] version = VersionInfo(AppIdentity.EngineVersion, language);
                Check(UpdateResource(update, (IntPtr)RtVersion, (IntPtr)1, language, version, (uint)version.Length));
                finished = true;
                if (!EndUpdateResource(update, false)) throw new Win32Exception(Marshal.GetLastWin32Error(), "The engine branding could not be saved");
            }
            finally
            {
                if (!finished) EndUpdateResource(update, true);
            }
        }

        // Returns the highest numeric icon ID; fills the resources that branding replaces.
        private static int Inspect(string exe, List<ushort> groupLanguages, List<ResourceRef> oldIcons, List<ushort> versionLanguages)
        {
            IntPtr module = LoadLibraryEx(exe, IntPtr.Zero, LoadAsDatafile | LoadAsImageResource);
            if (module == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "The engine resources could not be read");
            try
            {
                int highest = 0;
                EnumResNameProc names = delegate(IntPtr handle, IntPtr type, IntPtr name, IntPtr param)
                {
                    long value = name.ToInt64();
                    if (value > 0 && value <= ushort.MaxValue) highest = Math.Max(highest, (int)value);
                    return true;
                };
                EnumResourceNames(module, (IntPtr)RtIcon, names, IntPtr.Zero);
                EnumResLangProc groups = delegate(IntPtr handle, IntPtr type, IntPtr name, ushort language, IntPtr param)
                {
                    groupLanguages.Add(language);
                    return true;
                };
                EnumResourceLanguages(module, (IntPtr)RtGroupIcon, AppIconName, groups, IntPtr.Zero);
                foreach (ushort language in groupLanguages)
                {
                    byte[] group = ReadResource(module, AppIconName, language);
                    int count = BitConverter.ToUInt16(group, 4);
                    for (int index = 0; index < count; index++)
                    {
                        ResourceRef icon = new ResourceRef();
                        icon.Id = BitConverter.ToUInt16(group, 6 + index * 14 + 12);
                        icon.Language = language;
                        oldIcons.Add(icon);
                    }
                }
                EnumResLangProc versions = delegate(IntPtr handle, IntPtr type, IntPtr name, ushort language, IntPtr param)
                {
                    versionLanguages.Add(language);
                    return true;
                };
                EnumResourceLanguages(module, (IntPtr)RtVersion, (IntPtr)1, versions, IntPtr.Zero);
                GC.KeepAlive(names);
                GC.KeepAlive(groups);
                GC.KeepAlive(versions);
                return highest;
            }
            finally
            {
                FreeLibrary(module);
            }
        }

        private static byte[] ReadResource(IntPtr module, string name, ushort language)
        {
            IntPtr info = FindResourceEx(module, (IntPtr)RtGroupIcon, name, language);
            if (info == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "The engine icon could not be read");
            uint size = SizeofResource(module, info);
            IntPtr data = LockResource(LoadResource(module, info));
            if (data == IntPtr.Zero || size < 6) throw new InvalidDataException("The engine icon resource is invalid.");
            byte[] result = new byte[size];
            Marshal.Copy(data, result, 0, (int)size);
            return result;
        }

        private static List<IconImage> ReadIcon(byte[] ico)
        {
            List<IconImage> images = new List<IconImage>();
            using (BinaryReader reader = new BinaryReader(new MemoryStream(ico)))
            {
                if (ico.Length < 6 || reader.ReadUInt16() != 0 || reader.ReadUInt16() != 1)
                {
                    throw new InvalidDataException("The application icon is not an .ico file.");
                }
                int count = reader.ReadUInt16();
                for (int index = 0; index < count; index++)
                {
                    IconImage image = new IconImage();
                    image.Width = reader.ReadByte();
                    image.Height = reader.ReadByte();
                    image.Colors = reader.ReadByte();
                    reader.ReadByte();
                    image.Planes = reader.ReadUInt16();
                    image.BitCount = reader.ReadUInt16();
                    int size = reader.ReadInt32();
                    int offset = reader.ReadInt32();
                    if (size <= 0 || offset < 0 || (long)offset + size > ico.Length)
                    {
                        throw new InvalidDataException("The application icon is damaged.");
                    }
                    image.Data = new byte[size];
                    Buffer.BlockCopy(ico, offset, image.Data, 0, size);
                    images.Add(image);
                }
            }
            if (images.Count == 0) throw new InvalidDataException("The application icon has no images.");
            return images;
        }

        private static byte[] GroupIcon(List<IconImage> images, int firstId)
        {
            using (MemoryStream stream = new MemoryStream())
            using (BinaryWriter writer = new BinaryWriter(stream))
            {
                writer.Write((ushort)0);
                writer.Write((ushort)1);
                writer.Write((ushort)images.Count);
                for (int index = 0; index < images.Count; index++)
                {
                    IconImage image = images[index];
                    writer.Write(image.Width);
                    writer.Write(image.Height);
                    writer.Write(image.Colors);
                    writer.Write((byte)0);
                    writer.Write(image.Planes);
                    writer.Write(image.BitCount);
                    writer.Write((uint)image.Data.Length);
                    writer.Write((ushort)(firstId + index));
                }
                writer.Flush();
                return stream.ToArray();
            }
        }

        // VS_VERSIONINFO with the application's names; the version stays the engine's.
        private static byte[] VersionInfo(string version, ushort language)
        {
            Version parsed = new Version(version);
            uint high = (uint)((parsed.Major << 16) | parsed.Minor);
            uint low = (uint)((Math.Max(parsed.Build, 0) << 16) | Math.Max(parsed.Revision, 0));
            byte[] fixedInfo;
            using (MemoryStream stream = new MemoryStream())
            using (BinaryWriter writer = new BinaryWriter(stream))
            {
                uint[] values = { 0xFEEF04BD, 0x00010000, high, low, high, low, 0x3F, 0, 0x00040004, 1, 0, 0, 0 };
                foreach (uint value in values) writer.Write(value);
                writer.Flush();
                fixedInfo = stream.ToArray();
            }
            string[,] strings = {
                { "CompanyName", AppIdentity.Publisher },
                { "FileDescription", AppIdentity.Name },
                { "FileVersion", version },
                { "InternalName", AppIdentity.Name },
                { "LegalCopyright", "Blender Foundation and contributors, GPL-2.0-or-later" },
                { "OriginalFilename", AppIdentity.Executable },
                { "ProductName", AppIdentity.Name },
                { "ProductVersion", version },
                { "Comments", "Drawing engine built from Blender " + version + " LTS" },
            };
            byte[][] entries = new byte[strings.GetLength(0)][];
            for (int index = 0; index < entries.Length; index++)
            {
                string text = strings[index, 1];
                entries[index] = Block(strings[index, 0], Encoding.Unicode.GetBytes(text + "\0"), (ushort)(text.Length + 1), 1);
            }
            string tableKey = language.ToString("X4", CultureInfo.InvariantCulture) + UnicodeCodePage.ToString("X4", CultureInfo.InvariantCulture);
            byte[] table = Block(tableKey, null, 0, 1, entries);
            byte[] stringInfo = Block("StringFileInfo", null, 0, 1, table);
            byte[] translation = new byte[4];
            BitConverter.GetBytes(language).CopyTo(translation, 0);
            BitConverter.GetBytes(UnicodeCodePage).CopyTo(translation, 2);
            byte[] variable = Block("Translation", translation, 4, 0);
            byte[] variableInfo = Block("VarFileInfo", null, 0, 1, variable);
            return Block("VS_VERSION_INFO", fixedInfo, (ushort)fixedInfo.Length, 0, stringInfo, variableInfo);
        }

        private static byte[] Block(string key, byte[] value, ushort valueLength, ushort type, params byte[][] children)
        {
            using (MemoryStream stream = new MemoryStream())
            using (BinaryWriter writer = new BinaryWriter(stream))
            {
                writer.Write((ushort)0);
                writer.Write(valueLength);
                writer.Write(type);
                writer.Write(Encoding.Unicode.GetBytes(key + "\0"));
                Align(writer);
                if (value != null) writer.Write(value);
                foreach (byte[] child in children)
                {
                    Align(writer);
                    writer.Write(child);
                }
                writer.Flush();
                byte[] result = stream.ToArray();
                if (result.Length > ushort.MaxValue) throw new InvalidDataException("The version resource is too large.");
                BitConverter.GetBytes((ushort)result.Length).CopyTo(result, 0);
                return result;
            }
        }

        private static void Align(BinaryWriter writer)
        {
            while (writer.BaseStream.Position % 4 != 0) writer.Write((byte)0);
        }

        // Removes the Authenticode certificate table so the image is plainly unsigned.
        private static void StripSignature(string exe)
        {
            using (FileStream stream = new FileStream(exe, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
            using (BinaryReader reader = new BinaryReader(stream, Encoding.ASCII, true))
            using (BinaryWriter writer = new BinaryWriter(stream, Encoding.ASCII, true))
            {
                stream.Position = 0x3C;
                int header = reader.ReadInt32();
                stream.Position = header;
                if (reader.ReadUInt32() != 0x00004550) throw new InvalidDataException("The engine is not a Windows executable.");
                long optional = header + 24;
                stream.Position = optional;
                ushort magic = reader.ReadUInt16();
                long countOffset = optional + (magic == 0x20B ? 108 : 92);
                stream.Position = countOffset;
                if (reader.ReadUInt32() < 5) return;
                long security = countOffset + 4 + 4 * 8;
                stream.Position = security;
                uint offset = reader.ReadUInt32();
                uint size = reader.ReadUInt32();
                if (size == 0) return;
                stream.Position = security;
                writer.Write(0u);
                writer.Write(0u);
                writer.Flush();
                if ((long)offset + size == stream.Length) stream.SetLength(offset);
            }
        }

        // Engine text shown before the application starts. Each replacement keeps the byte length.
        private static readonly string[,] TextPatches = {
            // Window title suffix " - Blender <version>": a leading NUL ends the title before it.
            { " - Blender {}\0", "\0- Blender {}\0" },
            { "(Unsaved)\0", "Untitled\0\0" },
            // Process taskbar identity (BLENDER_WIN_APPID).
            { "blender.5.2\0", AppIdentity.UserModelId + "\0" },
            { "Blender - Unsupported Graphics Card Configuration\0", "Drawing Intelligence - Unsupported Graphics Card\0\0" },
        };

        private static void ReplaceText(string exe)
        {
            byte[] data = File.ReadAllBytes(exe);
            bool changed = false;
            for (int index = 0; index < TextPatches.GetLength(0); index++)
            {
                byte[] original = Encoding.ASCII.GetBytes(TextPatches[index, 0]);
                byte[] replacement = Encoding.ASCII.GetBytes(TextPatches[index, 1]);
                if (original.Length != replacement.Length) throw new InvalidOperationException("Text patches must keep their length.");
                int found = Find(data, original);
                if (found >= 0)
                {
                    if (Find(data, original, found + 1) >= 0) throw new InvalidDataException("The engine text appears more than once.");
                    Buffer.BlockCopy(replacement, 0, data, found, replacement.Length);
                    changed = true;
                }
                else if (Find(data, replacement) < 0)
                {
                    throw new InvalidDataException("This engine build is not the expected Blender " + AppIdentity.EngineVersion + ".");
                }
            }
            if (changed) File.WriteAllBytes(exe, data);
        }

        private static int Find(byte[] data, byte[] pattern, int start = 0)
        {
            byte first = pattern[0];
            for (int index = Array.IndexOf(data, first, start); index >= 0 && index <= data.Length - pattern.Length;
                index = Array.IndexOf(data, first, index + 1))
            {
                int offset = 1;
                while (offset < pattern.Length && data[index + offset] == pattern[offset]) offset++;
                if (offset == pattern.Length) return index;
            }
            return -1;
        }

        private static void UseWindowsSubsystem(string exe)
        {
            const ushort WindowsGui = 2;
            using (FileStream stream = new FileStream(exe, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
            using (BinaryReader reader = new BinaryReader(stream, Encoding.ASCII, true))
            using (BinaryWriter writer = new BinaryWriter(stream, Encoding.ASCII, true))
            {
                stream.Position = 0x3C;
                int header = reader.ReadInt32();
                stream.Position = header;
                if (reader.ReadUInt32() != 0x00004550) throw new InvalidDataException("The engine is not a Windows executable.");
                // IMAGE_OPTIONAL_HEADER.Subsystem has the same offset in PE32 and PE32+.
                stream.Position = header + 24 + 68;
                if (reader.ReadUInt16() == WindowsGui) return;
                stream.Position = header + 24 + 68;
                writer.Write(WindowsGui);
                writer.Flush();
            }
        }

        public static bool IsWindowsSubsystem(string exe)
        {
            using (FileStream stream = new FileStream(exe, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            using (BinaryReader reader = new BinaryReader(stream))
            {
                stream.Position = 0x3C;
                int header = reader.ReadInt32();
                stream.Position = header + 24 + 68;
                return reader.ReadUInt16() == 2;
            }
        }

        private static void Check(bool result)
        {
            if (!result) throw new Win32Exception(Marshal.GetLastWin32Error(), "The engine branding failed");
        }

        // ------------------------------------------------------------------
        // Win32 resource API (winbase.h)
        // ------------------------------------------------------------------
        private delegate bool EnumResLangProc(IntPtr module, IntPtr type, IntPtr name, ushort language, IntPtr param);
        private delegate bool EnumResNameProc(IntPtr module, IntPtr type, IntPtr name, IntPtr param);

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "LoadLibraryExW")]
        private static extern IntPtr LoadLibraryEx(string path, IntPtr file, uint flags);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool FreeLibrary(IntPtr module);

        [DllImport("kernel32.dll", SetLastError = true, EntryPoint = "EnumResourceNamesW")]
        private static extern bool EnumResourceNames(IntPtr module, IntPtr type, EnumResNameProc callback, IntPtr param);

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "EnumResourceLanguagesW")]
        private static extern bool EnumResourceLanguages(IntPtr module, IntPtr type, string name, EnumResLangProc callback, IntPtr param);

        [DllImport("kernel32.dll", SetLastError = true, EntryPoint = "EnumResourceLanguagesW")]
        private static extern bool EnumResourceLanguages(IntPtr module, IntPtr type, IntPtr name, EnumResLangProc callback, IntPtr param);

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "FindResourceExW")]
        private static extern IntPtr FindResourceEx(IntPtr module, IntPtr type, string name, ushort language);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr LoadResource(IntPtr module, IntPtr info);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr LockResource(IntPtr data);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint SizeofResource(IntPtr module, IntPtr info);

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "BeginUpdateResourceW")]
        private static extern IntPtr BeginUpdateResource(string file, bool deleteExisting);

        [DllImport("kernel32.dll", SetLastError = true, EntryPoint = "UpdateResourceW")]
        private static extern bool UpdateResource(IntPtr update, IntPtr type, IntPtr name, ushort language, byte[] data, uint size);

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "UpdateResourceW")]
        private static extern bool UpdateResource(IntPtr update, IntPtr type, string name, ushort language, byte[] data, uint size);

        [DllImport("kernel32.dll", SetLastError = true, EntryPoint = "EndUpdateResourceW")]
        private static extern bool EndUpdateResource(IntPtr update, bool discard);
    }
}
