#!/bin/sh

#   test_installer.sh host variant

set -e

source ./scripts/00-build_settings.txt
source ./scripts/00-private_build_settings.txt

HOST="${1:?"parameter 1 must HOST"}"
VARIANT="${2:-""}"

[ ! x${VARIANT} = x ] && VARIANT="-${VARIANT}"

cd ./installer
cd ./variant

cd ./notarized-package

INSTALL_PKG="$( cat installer_pkg_name.txt )"
INSTALL_PKG_PATH="${PWD}/${INSTALL_PKG}"
TEST_DIR=installer_test/$(basename logs_${INSTALL_PKG} .pkg)

cd ..

if [ ! -e "./test-results" ] ; then
    mkdir ./test-results
fi

cd ./test-results

if [ -e "./${HOST}" ] ; then
    echo "ERROR: "${PWD}/${HOST}" already exists"
    exit 1
fi

mkdir "./${HOST}"
cd "./${HOST}"

PYTHON="/usr/local/bin/python3${VARIANT}"

cat >./download_devtest.command <<EOF_DOWNLOAD
#!/bin/sh
set -x

open ~/Downloads
open "${BP_DOWNLOAD_SERVER_DEVTEST_URL}/${INSTALL_PKG}"
EOF_DOWNLOAD

chmod 755 ./download_devtest.command

cat >./test.command <<EOFY
#!/bin/sh
set -x

mkdir -p ${TEST_DIR}
cd ${TEST_DIR}
open . || true
defaults write com.apple.CrashReporter DialogType none
ulimit -n 1000
date
${PYTHON} -m test -j3 -w -uall,-largefile,-gui,-curses -x test_signal --timeout=120
${PYTHON} -m test.pythoninfo
${PYTHON} -m test -v -uall,-largefile --timeout=120 test_signal
${PYTHON} -m test -w -uall,-largefile --timeout=60 test_idle
${PYTHON} -m test -w -uall,-largefile --timeout=60 test_tkinter
${PYTHON} -m test -w -uall,-largefile --timeout=60 test_ttk
${PYTHON} -m test -w -uall,-largefile --timeout=60 test_curses

${PYTHON} -m pip install --force-reinstall --no-binary ":all:" --user psutil
${PYTHON} -c "import psutil; from pprint import pprint; pprint(list(psutil.process_iter()))"
${PYTHON} -m pip uninstall --yes psutil
${PYTHON}  \$(find -L /Library/Frameworks/Python.framework/Versions/Current -name "var_access_benchmark.py")
${PYTHON} -c "import urllib.request;print(urllib.request.urlopen('https://www.python.org').read(40))" && echo ' -- passed'
date
# [ x"$( arch )" = x"arm64" ] && [ -e "${PYTHON}-intel64" ] && ${PYTHON}-intel64 -m test -w -uall,-largefile || true
# [ -e "${PYTHON}-32" ] && ${PYTHON}-32 -m test -w -uall,-largefile || true
defaults write com.apple.CrashReporter DialogType creashreport
EOFY

chmod 755 ./test.command

# TODO: fix this
PYTHON="/Library/Frameworks/PythonT.framework/Versions/3.13/bin/python3.13t"

cat >./test_t.command <<EOFZ
#!/bin/sh
set -x

mkdir -p ${TEST_DIR}
cd ${TEST_DIR}
open . || true
defaults write com.apple.CrashReporter DialogType none
ulimit -n 1000
${PYTHON} -E -s -m ensurepip --upgrade
${PYTHON} -m test -j3 -w -uall,-largefile,-gui,-curses -x test_signal --timeout=120
${PYTHON} -m test.pythoninfo
${PYTHON} -m test -v -uall,-largefile --timeout=120 test_signal
${PYTHON} -m test -w -uall,-largefile --timeout=60 test_idle
${PYTHON} -m test -w -uall,-largefile --timeout=60 test_tkinter
${PYTHON} -m test -w -uall,-largefile --timeout=60 test_ttk
${PYTHON} -m test -w -uall,-largefile --timeout=60 test_curses

${PYTHON} -m pip install --force-reinstall --no-binary ":all:" --user psutil
${PYTHON} -c "import psutil; from pprint import pprint; pprint(list(psutil.process_iter()))"
${PYTHON} -m pip uninstall --yes psutil
${PYTHON}  \$(find -L /Library/Frameworks/Python.framework/Versions/Current -name "var_access_benchmark.py")
${PYTHON} -c "import urllib.request;print(urllib.request.urlopen('https://www.python.org').read(40))" && echo ' -- passed'

# [ x"$( arch )" = x"arm64" ] && [ -e "${PYTHON}-intel64" ] && ${PYTHON}-intel64 -m test -w -uall,-largefile || true
# [ -e "${PYTHON}-32" ] && ${PYTHON}-32 -m test -w -uall,-largefile || true
defaults write com.apple.CrashReporter DialogType creashreport
EOFZ

chmod 755 ./test_t.command

scp -p \
        ./download_devtest.command \
        ./test.command \
        ./test_t.command \
    ${HOST}:Desktop/ || echo "--ERROR: scp to test host failed - files here $PWD"

exit 0
