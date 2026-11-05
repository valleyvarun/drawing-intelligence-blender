# Engine and assets

This application runs inside Blender and uses its native Python API, Workbench renderer, geometry, GPU drawing API, and window system. Blender is licensed under GPL-2.0-or-later; its bundled libraries have their own notices. A packaged engine must retain Blender's license files and satisfy its source-distribution requirements. The optional native build reads the separate Blender checkout without modifying it.

The small drawing-tool icons are copies of assets in this repository's existing drawing application. No Omniverse or Kit binaries are bundled or required by this application.

The ruler glyph atlas uses Blender's bundled DejaVu Sans Mono. Its Bitstream/DejaVu license is included beside the atlas in `app/drawing_app/viewport/assets`. Interface text uses Blender's bundled Inter font.
