using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;

// ----------------------------------------------------------------------
// Converts the app logo into the installer and shortcut icon
// ----------------------------------------------------------------------
// [MakeIcon]::Run("<logo.png>", "<output folder>")  ->  app.ico (16-256 px) and app.png (256 px)
// build.bat loads this file with PowerShell's Add-Type, so no separate tool executable is written to disk
// (antivirus heuristics tend to quarantine freshly compiled, unsigned programs in %TEMP%).
public static class MakeIcon
{
    private static readonly int[] Sizes = { 16, 20, 24, 32, 40, 48, 64, 128, 256 };

    public static int Run(string logoPath, string output)
    {
        List<byte[]> images = new List<byte[]>();
        using (Bitmap logo = new Bitmap(Path.GetFullPath(logoPath)))
        {
            foreach (int size in Sizes)
            {
                using (Bitmap frame = Resize(logo, size))
                {
                    // Classic bitmaps for small sizes, PNG for 256 px (the Windows icon convention).
                    images.Add(size >= 256 ? EncodePng(frame) : EncodeDib(frame));
                    if (size == 256)
                    {
                        frame.Save(Path.GetFullPath(Path.Combine(output, "app.png")), ImageFormat.Png);
                    }
                }
            }
        }
        WriteIco(Path.GetFullPath(Path.Combine(output, "app.ico")), images);
        Console.WriteLine("Wrote app.ico and app.png to " + Path.GetFullPath(output));
        return 0;
    }

    // ----------------------------------------------------------------------
    // Drawing
    // ----------------------------------------------------------------------
    // Scales the logo into a transparent square, centred and keeping its aspect ratio.
    private static Bitmap Resize(Bitmap logo, int size)
    {
        Bitmap frame = new Bitmap(size, size, PixelFormat.Format32bppArgb);
        float scale = Math.Min((float)size / logo.Width, (float)size / logo.Height);
        float width = logo.Width * scale;
        float height = logo.Height * scale;
        using (Graphics g = Graphics.FromImage(frame))
        using (ImageAttributes edges = new ImageAttributes())
        {
            g.Clear(Color.Transparent);
            g.InterpolationMode = InterpolationMode.HighQualityBicubic;
            g.PixelOffsetMode = PixelOffsetMode.HighQuality;
            g.SmoothingMode = SmoothingMode.HighQuality;
            g.CompositingQuality = CompositingQuality.HighQuality;
            // Clamp sampling at the borders so the edge pixels do not fade.
            edges.SetWrapMode(WrapMode.TileFlipXY);
            RectangleF target = new RectangleF((size - width) / 2, (size - height) / 2, width, height);
            g.DrawImage(logo, Rectangle.Round(target), 0, 0, logo.Width, logo.Height, GraphicsUnit.Pixel, edges);
        }
        return frame;
    }

    // ----------------------------------------------------------------------
    // ICO encoding
    // ----------------------------------------------------------------------
    private static byte[] EncodePng(Bitmap bitmap)
    {
        using (MemoryStream stream = new MemoryStream())
        {
            bitmap.Save(stream, ImageFormat.Png);
            return stream.ToArray();
        }
    }

    // BITMAPINFOHEADER + bottom-up BGRA pixels + 1-bit AND mask (set where fully transparent).
    private static byte[] EncodeDib(Bitmap bitmap)
    {
        int w = bitmap.Width;
        int h = bitmap.Height;
        byte[] pixels = new byte[w * h * 4];
        BitmapData data = bitmap.LockBits(new Rectangle(0, 0, w, h), ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
        try
        {
            for (int y = 0; y < h; y++)
            {
                Marshal.Copy(data.Scan0 + (h - 1 - y) * data.Stride, pixels, y * w * 4, w * 4);
            }
        }
        finally
        {
            bitmap.UnlockBits(data);
        }

        int maskStride = (w + 31) / 32 * 4;
        byte[] mask = new byte[maskStride * h];
        for (int y = 0; y < h; y++)
        {
            for (int x = 0; x < w; x++)
            {
                if (pixels[(y * w + x) * 4 + 3] == 0)
                {
                    mask[y * maskStride + x / 8] |= (byte)(0x80 >> (x % 8));
                }
            }
        }

        using (MemoryStream stream = new MemoryStream())
        using (BinaryWriter writer = new BinaryWriter(stream))
        {
            writer.Write(40);
            writer.Write(w);
            writer.Write(h * 2);
            writer.Write((short)1);
            writer.Write((short)32);
            writer.Write(0);
            writer.Write(pixels.Length + mask.Length);
            writer.Write(0);
            writer.Write(0);
            writer.Write(0);
            writer.Write(0);
            writer.Write(pixels);
            writer.Write(mask);
            writer.Flush();
            return stream.ToArray();
        }
    }

    private static void WriteIco(string path, List<byte[]> images)
    {
        using (FileStream file = File.Create(path))
        using (BinaryWriter writer = new BinaryWriter(file))
        {
            writer.Write((short)0);
            writer.Write((short)1);
            writer.Write((short)images.Count);
            int offset = 6 + 16 * images.Count;
            for (int i = 0; i < images.Count; i++)
            {
                int size = Sizes[i];
                writer.Write((byte)(size >= 256 ? 0 : size));
                writer.Write((byte)(size >= 256 ? 0 : size));
                writer.Write((byte)0);
                writer.Write((byte)0);
                writer.Write((short)1);
                writer.Write((short)32);
                writer.Write(images[i].Length);
                writer.Write(offset);
                offset += images[i].Length;
            }
            foreach (byte[] image in images)
            {
                writer.Write(image);
            }
        }
    }
}
