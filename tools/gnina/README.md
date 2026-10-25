# gnina

iDock is configured to call `tools/gnina/gnina`. The wrapper keeps this path
stable across platforms and reports a clear error on macOS.

The upstream project currently publishes a Linux/WSL CUDA static executable;
it does not publish a macOS executable. On Linux, place the official release
asset named `gnina.cuda12.8.static` in this directory and make it executable.
The wrapper will then invoke it automatically.

- Source: https://github.com/gnina/gnina
- Releases: https://github.com/gnina/gnina/releases/latest
- GNU GPL license: https://github.com/gnina/gnina/blob/master/LICENSE.GNU
- Apache license: https://github.com/gnina/gnina/blob/master/LICENSE.APACHE

The pre-built executable contains GPL-covered components (including
OpenBabel), so the GNU GPL terms apply to redistribution of that binary.
