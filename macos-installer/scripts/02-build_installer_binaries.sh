#!/bin/sh

#   build_installer_binaries.sh

set -e

source ./scripts/00-build_settings.txt

if [ -e "./installer" ] ; then
    echo "ERROR: "${PWD}/installer" already exists"
    exit 1
fi
mkdir ./installer
cd ./installer


if [ -e "./variant" ] ; then
    echo "ERROR: "${PWD}/variant" already exists"
    exit 1
fi
mkdir ./variant
cd ./variant

if [ -e "./binaries" ] ; then
    echo "ERROR: "${PWD}/binaries" already exists"
    exit 1
fi
mkdir ./binaries
cd ./binaries

PY_SOURCE_DIR="${PWD}/build_source"
PY_BUILD_DIR="${PWD}/build"

echo BUILDNAME=${BUILDNAME}
echo BUILD=${BUILD}
echo BP_DEPLOYMENT_TARGET=${BP_DEPLOYMENT_TARGET}

sw_vers || true
xcodebuild -version || true
xcode-select -p || true

# clean up environment

set -- "${PY_BUILD_DIR}" \
    "${HOME}/.pydistutils.cfg" \
    "/Library/Frameworks/Python.framework" \
    "/Applications/Python "*

FOUND=no

for f ; do
    [ \( -e "${f}" \) -o \( -L "${f}" \) ] && echo "found ${f}" && export FOUND=yes
done
if [ ${FOUND} = "yes" ] ; then
    echo "ERROR: remove or rename above files and retry"
    echo " -- WARNING: proceeding anyway"
    # exit 1
fi

# stop if any signs of Homebrew, MacPorts, Fink, or local libs

set -- "/opt/local" "/opt/homebrew" "/sw" "/usr/local/lib"
for f ; do
    [ -e ${f} ] && echo "ERROR: ${f} exists, check environment" && exit 1
done

# unpack exported tarball of vcs checkout

mkdir ${PY_SOURCE_DIR}
cd ${PY_SOURCE_DIR}
tar -x -f "${BP_ROOT}/source/${BUILDTARBALL}"

# source exported environment variables with vcs info

. "${BP_ROOT}/source/${BUILDEXPORTS}"

# use local copy of BuildScript/build-installer.py et al

cd ./Mac/
rm -rf BuildScript
cp -pr ${BP_ROOT}/BuildScript .

# build installer

cd ./BuildScript/

date

echo export SDKROOT="${BP_MACOS_SDK_ROOT}"
export SDKROOT="${BP_MACOS_SDK_ROOT}"

echo ${BP_PYTHON} build-installer.py \
   --universal-archs="${BP_UNIVERSAL_ARCHS}" \
   --dep-target="${BP_DEPLOYMENT_TARGET}" \
   --third-party="${BP_ROOT}/source/${BUILDOTHER_SOURCES}" \
   --build-dir="${PY_BUILD_DIR}"
${BP_PYTHON} build-installer.py \
   --universal-archs="${BP_UNIVERSAL_ARCHS}" \
   --dep-target="${BP_DEPLOYMENT_TARGET}" \
   --third-party="${BP_ROOT}/source/${BUILDOTHER_SOURCES}" \
   --build-dir="${PY_BUILD_DIR}"
cd ../..

echo " -- Created ${PY_BUILD_DIR}"
echo " "

date

exit 0
