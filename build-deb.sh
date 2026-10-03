#!/usr/bin/env bash
# Build a Debian binary package without requiring debhelper or root.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

# --offline bundles dependency wheels for this machine's Python and CPU, so the
# package installs without internet access on matching systems (postinst falls
# back to PyPI when the wheels do not fit). The package then becomes
# architecture-specific.
OFFLINE=0
if [ "${1:-}" = "--offline" ]; then
    OFFLINE=1
fi

VERSION=$(python3 -c 'import ast; from pathlib import Path; tree = ast.parse(Path("pdflx/constants.py").read_text()); print(next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in node.targets)))')
OUTPUT_DIR="$PWD/dist"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$OUTPUT_DIR" "$STAGE/DEBIAN" "$STAGE/usr/share/doc/pdflx"

make -f debian/rules override_dh_install PKGDDIR="$STAGE"
ARCH=all
if [ "$OFFLINE" -eq 1 ]; then
    mkdir -p "$STAGE/opt/pdflx/wheels"
    python3 -m pip download --quiet --only-binary=:all: --dest "$STAGE/opt/pdflx/wheels" \
        'PyMuPDF>=1.26.0' numpy Pillow 'pdf2docx>=0.5.13' 'pyHanko>=0.25'
    ARCH=$(dpkg --print-architecture)
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
dpkg-deb --root-owner-group --build "$STAGE" "$OUTPUT_DIR/pdflx_${VERSION}_${ARCH}.deb"
install -m 644 pdflx/img/pdflx.svg "$OUTPUT_DIR/pdflx.svg"
install -m 644 pdflx/img/pdflx.png "$OUTPUT_DIR/pdflx.png"
echo "Built $OUTPUT_DIR/pdflx_${VERSION}_${ARCH}.deb and pdfLX icons."
