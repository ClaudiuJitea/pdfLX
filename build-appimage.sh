#!/bin/bash
set -e

ARCH="$(uname -m)"
echo "=== Building pdfLX for architecture: $ARCH ==="

case "$ARCH" in
    x86_64|amd64)
        ARCH_NAME="x86_64"
        DEB_ARCH="amd64"
        PYTHON_ARCH="x86_64"
        LINUXDEPLOY_ARCH="x86_64"
        LIB_DIR="/usr/lib/x86_64-linux-gnu"
        ;;
    aarch64|arm64)
        ARCH_NAME="aarch64"
        DEB_ARCH="arm64"
        PYTHON_ARCH="aarch64"
        LINUXDEPLOY_ARCH="aarch64"
        LIB_DIR="/usr/lib/aarch64-linux-gnu"
        ;;
    *)
        echo "Unsupported architecture: $ARCH"
        exit 1
        ;;
esac

rm -rf AppDir build_tmp
mkdir -p AppDir/usr build_tmp

echo "--- Downloading standalone Python 3.10 for $PYTHON_ARCH ---"
curl -L -s "https://github.com/indygreg/python-build-standalone/releases/download/20240107/cpython-3.10.13+20240107-${PYTHON_ARCH}-unknown-linux-gnu-install_only.tar.gz" -o build_tmp/python.tar.gz
tar -xzf build_tmp/python.tar.gz -C AppDir/usr --strip-components=1

echo "--- Installing Python dependencies ---"
AppDir/usr/bin/python3 -m pip install --upgrade pip
AppDir/usr/bin/python3 -m pip install pygobject==3.50.0
AppDir/usr/bin/python3 -m pip install PyMuPDF numpy pdf2docx pyHanko
AppDir/usr/bin/python3 -m pip install .

echo "--- Configuring application entry point ---"
cat << 'EOF' > AppDir/usr/bin/pdflx.py
import sys
from pdflx.main import main
sys.exit(main())
EOF

cat << 'EOF' > AppDir/usr/bin/pdflx
#!/bin/bash
unset GTK_THEME
SELF_DIR="$(dirname "$(readlink -f "$0")")"
export GDK_PIXBUF_MODULEDIR="$SELF_DIR/../lib/gdk-pixbuf-2.0/2.10.0/loaders"
export GDK_PIXBUF_MODULE_FILE="$SELF_DIR/../lib/gdk-pixbuf-2.0/2.10.0/loaders.cache"
export LD_LIBRARY_PATH="$SELF_DIR/../lib:$SELF_DIR/../lib/gdk-pixbuf-2.0/2.10.0/loaders:$LD_LIBRARY_PATH"
exec "$SELF_DIR/python3" "$SELF_DIR/pdflx.py" "$@"
EOF
chmod +x AppDir/usr/bin/pdflx

mkdir -p AppDir/usr/share/applications
cp pdflx.desktop AppDir/usr/share/applications/

mkdir -p AppDir/usr/share/icons/hicolor/scalable/apps
cp pdflx/img/pdflx.svg AppDir/usr/share/icons/hicolor/scalable/apps/

mkdir -p AppDir/usr/share/icons/hicolor/256x256/apps
cp pdflx/img/pdflx.png AppDir/usr/share/icons/hicolor/256x256/apps/

if [ -d "/usr/share/icons/Adwaita" ]; then
    cp -r /usr/share/icons/Adwaita AppDir/usr/share/icons/
fi

echo "--- Downloading linuxdeploy ($LINUXDEPLOY_ARCH) ---"
curl -L -s "https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-${LINUXDEPLOY_ARCH}.AppImage" -o build_tmp/linuxdeploy
curl -L -s https://raw.githubusercontent.com/linuxdeploy/linuxdeploy-plugin-gtk/master/linuxdeploy-plugin-gtk.sh -o build_tmp/linuxdeploy-plugin-gtk.sh
curl -L -s "https://github.com/linuxdeploy/linuxdeploy-plugin-appimage/releases/download/continuous/linuxdeploy-plugin-appimage-${LINUXDEPLOY_ARCH}.AppImage" -o build_tmp/linuxdeploy-plugin-appimage
chmod +x build_tmp/linuxdeploy build_tmp/linuxdeploy-plugin-gtk.sh build_tmp/linuxdeploy-plugin-appimage

export PATH="$(pwd)/build_tmp:$PATH"
export DEPLOY_GTK_VERSION=4
export APPIMAGE_EXTRACT_AND_RUN=1

# Detect libadwaita-1 location dynamically
ADWAITA_SO="$(find /usr/lib -name "libadwaita-1.so.0" 2>/dev/null | head -n 1)"
if [ -z "$ADWAITA_SO" ]; then
    ADWAITA_SO="${LIB_DIR}/libadwaita-1.so.0"
fi

echo "--- Running linuxdeploy ---"
build_tmp/linuxdeploy --appdir AppDir --plugin gtk --desktop-file=pdflx.desktop --icon-file=pdflx/img/pdflx.png -l "$ADWAITA_SO"

cp AppDir/usr/lib/gdk-pixbuf-2.0/2.10.0/loaders/*.so AppDir/usr/lib/gdk-pixbuf-2.0/2.10.0/ 2>/dev/null || true
cp AppDir/usr/lib/librsvg-2.so* AppDir/usr/lib/gdk-pixbuf-2.0/2.10.0/loaders/ 2>/dev/null || true

echo "--- Generating AppImage ---"
build_tmp/linuxdeploy --appdir AppDir --output appimage --desktop-file=pdflx.desktop --icon-file=pdflx/img/pdflx.png

# One AppImage per architecture; the .deb is built separately by build-deb.sh.
GENERATED_APPIMAGE="$(ls pdfLX*${LINUXDEPLOY_ARCH}.AppImage 2>/dev/null || ls *.AppImage 2>/dev/null | head -n 1)"
if [ -f "$GENERATED_APPIMAGE" ] && [ "$GENERATED_APPIMAGE" != "pdflx-${ARCH_NAME}.AppImage" ]; then
    mv "$GENERATED_APPIMAGE" "pdflx-${ARCH_NAME}.AppImage"
fi

rm -rf build_tmp
echo "=== Built pdflx-${ARCH_NAME}.AppImage ==="
