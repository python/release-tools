#!/bin/sh

#   retrieve_test_results.sh host

set -e

source ./scripts/00-build_settings.txt

HOST="${1:?"parameter 1 must HOST"}"

cd ./installer
cd ./variant

cd ./notarized-package

INSTALL_PKG="$( cat installer_pkg_name.txt )"
TEST_DIR=installer_test/$(basename logs_${INSTALL_PKG} .pkg)

cd ..
cd ./test-results

if [ ! -e "./${HOST}" ] ; then
    echo "ERROR: "${PWD}/${HOST}" does not exist"
    exit 1
fi

cd "./${HOST}"

scp -pr ${HOST}:${TEST_DIR} .

open . || true

exit 0
