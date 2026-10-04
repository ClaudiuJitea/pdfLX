#!/usr/bin/env bash
# Build a Debian binary package without requiring debhelper or root.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

# --offline bundles the Python dependency wheels, so the package installs without
# internet access. Wheels are fetched for every supported Python version (Ubuntu
# 24.04 has 3.12, Debian 13 3.13, newer releases 3.14) and for the target CPU, which
# may differ from this machine: --arch amd64 or --arch arm64 (default: this machine).
# postinst still falls back to PyPI if no bundled wheel fits. The package then
# becomes architecture-specific and is named pdflx_<version>_offline_<arch>.deb.
OFFLINE=0
TARGET_ARCH=""
PYTHON_VERSIONS="3.12 3.13 3.14"
while [ $# -gt 0 ]; do
    case "$1" in
        --offline) OFFLINE=1 ;;
        --arch) TARGET_ARCH="$2"; shift ;;
        *) echo "Usage: $0 [--offline [--arch amd64|arm64]]" >&2; exit 1 ;;
    esac
    shift
done

VERSION=$(python3 -c 'import ast; from pathlib import Path; tree = ast.parse(Path("pdflx/constants.py").read_text()); print(next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in node.targets)))')
OUTPUT_DIR="$PWD/dist"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$OUTPUT_DIR" "$STAGE/DEBIAN" "$STAGE/usr/share/doc/pdflx"

make -f debian/rules override_dh_install PKGDDIR="$STAGE"
ARCH=all
SUFFIX=""
if [ "$OFFLINE" -eq 1 ]; then
    ARCH=${TARGET_ARCH:-$(dpkg --print-architecture)}
    case "$ARCH" in
        amd64) MACHINE=x86_64 ;;
        arm64) MACHINE=aarch64 ;;
        *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
    esac
    PLATFORMS=""
    for tag in manylinux2014 manylinux_2_17 manylinux_2_27 manylinux_2_28 manylinux_2_31 manylinux_2_34; do
        PLATFORMS="$PLATFORMS --platform ${tag}_${MACHINE}"
    done
    mkdir -p "$STAGE/opt/pdflx/wheels"
    for py in $PYTHON_VERSIONS; do
        echo "Bundling wheels for Python $py ($ARCH)..."
        # shellcheck disable=SC2086
        python3 -m pip download --quiet --only-binary=:all: --implementation cp --python-version "$py" \
            $PLATFORMS --dest "$STAGE/opt/pdflx/wheels" \
            'PyMuPDF>=1.26.0' numpy Pillow 'pdf2docx>=0.5.13' 'pyHanko>=0.25'
    done
    SUFFIX="_offline"
fi
install -m 755 debian/postinst "$STAGE/DEBIAN/postinst"
install -m 755 debian/prerm "$STAGE/DEBIAN/prerm"
install -m 644 debian/copyright LICENSE "$STAGE/usr/share/doc/pdflx/"
gzip -n -9 -c debian/changelog > "$STAGE/usr/share/doc/pdflx/changelog.Debian.gz"

python3 - "$STAGE" "$VERSION" "$ARCH" <<'PY'
import re
import sys
from pathlib import Path

stage = Path(sys.argv[1])
package = Path('debian/control').read_text().split('\n\n', 1)[1]
package = package.replace('${misc:Depends},\n         ', '')
assert '${' not in package, 'Unresolved Debian dependency substitution'
size = sum(path.stat().st_size for path in stage.rglob('*') if path.is_file())
package = package.replace('Architecture: all', 'Architecture: ' + sys.argv[3])
package = re.sub(r'^(Package: .+)$', r'\1\nVersion: ' + sys.argv[2] + '\nMaintainer: pdfLX contributors <pdflx@localhost>\nSection: graphics\nPriority: optional\nInstalled-Size: ' + str((size + 1023) // 1024), package, count=1, flags=re.M)
(stage / 'DEBIAN/control').write_text(package)
PY

find "$STAGE" -type d -exec chmod 755 {} +
DEB="$OUTPUT_DIR/pdflx_${VERSION}${SUFFIX}_${ARCH}.deb"
dpkg-deb --root-owner-group -Zxz --build "$STAGE" "$DEB"
install -m 644 pdflx/img/pdflx.svg "$OUTPUT_DIR/pdflx.svg"
install -m 644 pdflx/img/pdflx.png "$OUTPUT_DIR/pdflx.png"
echo "Built $DEB and pdfLX icons."
