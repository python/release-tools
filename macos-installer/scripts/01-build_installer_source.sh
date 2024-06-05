#!/bin/sh

#   build_installer_source.sh

set -e

source ./scripts/00-build_settings.txt

# create source tarball

if [ -e "./source" ] ; then
    echo "ERROR: "${PWD}/source" already exists"
    exit 1
fi

if [ -e "./cached-artifacts" ] ; then
    echo " -- WARNING: ./cached-artifacts exists"
    ls -l "./cached-artifacts"
fi

mkdir ./source
cd ./source

TMP_REPO=tmp_repo

git clone --reference-if-able ${BP_CPYTHON_LOCAL_CACHE_REPO:-"/dev/null"} \
    -b ${BP_GITTAG} ${BP_CPYTHON_BUILD_REPO} ${TMP_REPO}

cd ${TMP_REPO}
git checkout ${BP_GITTAG}
git archive --format=tar --prefix="./" -o ../${BUILDTARBALL} ${BP_GITTAG}
cd ..

TMP_BUILD=tmp_build
mkdir ${TMP_BUILD}

cd ${TMP_BUILD}
echo " -- building temporary python executable"
../${TMP_REPO}/configure -q && make -j3 > ${TMP_BUILD}.log

if [ -e "./python.exe" ] ; then
    TMP_PYTHON="./python.exe"
else
    TMP_PYTHON="./python"
fi

_GITVERSION=$($(${TMP_PYTHON} -c 'import sysconfig;print(sysconfig.get_config_var("GITVERSION"))'))
_PYVERSION=$(${TMP_PYTHON} -c 'import platform;print(platform.python_version())')
_PYMAJMIN=$(${TMP_PYTHON} -c 'import platform;print("{}.{}".format(*platform.python_version_tuple()))')
cd ..

sed -e 's/tags\///g' >./${BUILDEXPORTS} <<EOFBBME
export BUILDINSTALLER_BUILDPYTHON_MAKE_EXTRAS=" \
    GITVERSION='echo ${_GITVERSION}' \
    GITTAG='echo ${BP_GITTAG}' \
    GITBRANCH='echo ${_PYMAJMIN}'"
EOFBBME

cat >./${BUILDVERSION} <<EOFPYV
export PYVERSION='${_PYVERSION}'
export PYMAJMIN='${_PYMAJMIN}'
EOFPYV

cp -pr ${BP_THIRD_PARTY_SOURCES_CACHE} ${BUILDOTHER_SOURCES}

if [ -e "../cached-artifacts/libraries-saved" ] ; then
    echo " -- Using cached libraries-saved "
    echo " "
    cp -pr "../cached-artifacts/libraries-saved" ${BUILDOTHER_SOURCES}
else
    rm -rf ${BUILDOTHER_SOURCES}/libraries-saved
fi

echo " -- Created ${PWD}/${BUILDEXPORTS}"
echo " "
cat ./${BUILDEXPORTS}

rm -rf ./${TMP_REPO}
rm -rf ./${TMP_BUILD}

exit 0
