#!/bin/sh

#   build_notarized_package.sh

set -e

source ./scripts/00-build_settings.txt
source ./scripts/00-private_build_settings.txt

cd ./installer
cd ./variant

cd ./signed-package
if [ ! -e "./installer_pkg_name.txt" ] ; then
    echo "ERROR: "${PWD}/installer_pkg_name.txt" does not yet exist"
    exit 1
    fi
INSTALL_PKG=$( cat ./installer_pkg_name.txt)
cd ..

if [ -e "./notarized-package" ] ; then
    echo "ERROR: "${PWD}/notarized-package" already exists"
    exit 1
    fi

mkdir ./notarized-package
cd ./notarized-package

cp -p ../signed-package/${INSTALL_PKG} .

echo " -- submitting package for notarization"
xcrun notarytool submit ./${INSTALL_PKG} \
                   --keychain-profile "${BP_NOTARYTOOL_KEYCHAIN_PROFILE}" \
                   --wait

echo " -- stapling package"
xcrun stapler staple ./${INSTALL_PKG}

echo " -- signing package with pgp key"
gpg --armor --detach-sign -u "${BP_GPG_USER}" "./${INSTALL_PKG}" || \
    echo " -- Warning: gpg signing failed"

set -- "${INSTALL_PKG}" "${INSTALL_PKG}.asc"
for f ; do
    printf '%s %9s %s\n' \
        $(openssl sha256 ./$f) \
        "$f" \
      >> ./email.txt
done

echo "${INSTALL_PKG}" > ./installer_pkg_name.txt

echo " -- stapled package at ${PWD}/${INSTALL_PKG}"
echo " -- notarization complete"

exit 0
