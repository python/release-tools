#!/bin/sh

#   cache_third_party_libraries.sh

set -e

source ./scripts/00-build_settings.txt

THIRD_PARTY_LIBRARY_CACHE="$PWD/source/${BUILDOTHER_SOURCES}/libraries-saved/"

cd ./installer
cd ./variant
cd ./signed-package

if [ -e ./"libraries-saved" ] ; then
    if [ -e "${THIRD_PARTY_LIBRARY_CACHE}" ] ; then
        echo " -- replacing third-party library binaries in ${THIRD_PARTY_LIBRARY_CACHE}"
        rm -rf "${THIRD_PARTY_LIBRARY_CACHE}"
    else
        echo " -- caching third-party library binaries in ${THIRD_PARTY_LIBRARY_CACHE}"
    fi
    cp -a "${PWD}/libraries-saved" "${THIRD_PARTY_LIBRARY_CACHE}"
else
    echo "WARNING: ${PWD}/libraries-saved does not exist, cache not copied"
fi

exit 0




