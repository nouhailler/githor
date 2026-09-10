#!/bin/sh
# Construit un paquet .deb autonome pour Githor.
#
# Le paquet embarque son propre environnement virtuel Python sous
# /opt/githor/venv — les dépendances de Githor (pydantic, textual, etc.) ne
# sont pas toutes disponibles comme paquets Debian, ou pas dans la bonne
# version. Un venv privé évite d'exiger leur installation système et reste
# cohérent avec le reste du projet : rien n'est modifié hors de ce que
# Githor possède.
#
# /usr/bin/githor est un simple lanceur shell vers ce venv : les scripts
# générés par pip dans venv/bin/ embarquent le chemin de construction dans
# leur shebang et ne survivraient pas au déplacement vers /opt/githor. Le
# lanceur invoque directement venv/bin/python3 (un symlien vers l'interpréteur
# système, insensible à son propre emplacement) avec `-m githor.cli`.
#
# Conséquence assumée : le paquet produit est lié à l'architecture et à la
# version de Python de la machine qui le construit (amd64, Python 3.13 sur
# Debian 13 ici) — ce n'est pas un paquet redistribuable pour d'autres
# architectures ou versions de Python.
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('$ROOT_DIR/pyproject.toml', 'rb'))['project']['version'])")
ARCH=$(dpkg --print-architecture)
BUILD_DIR=$(mktemp -d)
OUTPUT="$ROOT_DIR/githor_${VERSION}_${ARCH}.deb"

cleanup() {
    rm -rf "$BUILD_DIR"
}
trap cleanup EXIT

echo "Construction de Githor ${VERSION} (${ARCH})…"

mkdir -p "$BUILD_DIR/DEBIAN" "$BUILD_DIR/opt/githor" "$BUILD_DIR/usr/bin"

python3 -m venv "$BUILD_DIR/opt/githor/venv"
"$BUILD_DIR/opt/githor/venv/bin/pip" install --quiet --upgrade pip
"$BUILD_DIR/opt/githor/venv/bin/pip" install --quiet "$ROOT_DIR"

cat > "$BUILD_DIR/usr/bin/githor" <<'LAUNCHER'
#!/bin/sh
exec /opt/githor/venv/bin/python3 -m githor.cli "$@"
LAUNCHER
chmod 755 "$BUILD_DIR/usr/bin/githor"

INSTALLED_SIZE=$(du -sk "$BUILD_DIR/opt/githor" | cut -f1)

cat > "$BUILD_DIR/DEBIAN/control" <<CONTROL
Package: githor
Version: ${VERSION}
Section: devel
Priority: optional
Architecture: ${ARCH}
Installed-Size: ${INSTALLED_SIZE}
Depends: python3 (>= 3.13), git
Maintainer: Patrick Nouhailler <patrick.nouhailler@gmail.com>
Homepage: https://github.com/nouhailler/githor
Description: Inventaire, métriques et audit local de repositories GitHub
 Githor est un outil en ligne de commande, local et en lecture seule, qui
 inventorie des repositories GitHub, en suit l'évolution dans le temps
 (snapshots), en analyse le code source, en tire des constats déterministes
 et des recommandations via un modèle Ollama local.
 .
 N'écrit jamais sur GitHub : aucune issue, branche ou fichier créé, modifié
 ni supprimé. Aucune donnée n'est envoyée à un service tiers.
CONTROL

dpkg-deb --root-owner-group --build "$BUILD_DIR" "$OUTPUT"

echo "Paquet construit : $OUTPUT"
