# Engine and assets

This application distributes a private Blender engine and uses its native Python API, Workbench renderer, Grease Pencil tools, geometry, GPU drawing API, and window system. Blender is licensed under GPL-2.0-or-later; its bundled libraries have their own notices. A packaged engine retains Blender's license files. The launcher changes application identity resources and startup branding; it does not replace the viewport or brush algorithms.

The engine version is Blender 5.2.2 LTS, source commit `d13f752e3b9c4f8c261cda552b1021f8bcc0382c`. Its source is available from [Blender's repository](https://projects.blender.org/blender/blender/src/tag/v5.2.2) and locally under `vendor/blender/` after running `scripts/setup-source.ps1`. The source pin, build profile, launcher/branding code, and application code are supplied in this repository. Native source-build dependencies are obtained separately into the local source tree.

The small drawing-tool icons are copies of assets in this repository's existing drawing application. No Omniverse or Kit binaries are bundled or required by this application.

The transform and Grease Pencil toolbar icons are unchanged copies from `C:\blender\release\datafiles\icons`. They are stored in `app/drawing_app/assets/blender` and rendered from their original triangle coordinates and colors. `SOURCE.txt` lists each asset, including the Draw, Erase, Fill, Trim, primitive, and Eyedropper icons. Blender's GPL license text is included in that directory.

The ruler glyph atlas uses Blender's bundled DejaVu Sans Mono. Its Bitstream/DejaVu license is included beside the atlas in `app/drawing_app/viewport/assets`. Interface text uses Blender's bundled Inter font.
