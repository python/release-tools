#!/bin/sh

#   build_signed_package.sh

set -e

source ./scripts/00-build_settings.txt
source ./scripts/00-private_build_settings.txt

cd ./installer
cd ./variant

if [ -e "./signed-package" ] ; then
    echo "ERROR: "${PWD}/signed-package" already exists"
    exit 1
fi
mkdir ./signed-package
cd ./signed-package

PY_VER="${BP_PY_VER}"
TAG_VER="${BP_PY_VER}"

PY_BUILD_DIR="$PWD/../binaries/build"
[ ! -e "${PY_BUILD_DIR}" ] && echo " ERROR: ${PY_BUILD_DIR} does not exist" && exit 1

# Note, previously, MNT was the mount point of the disk image of /tmp/_py
#   from the build_installer.py VM.
MNT="${PY_BUILD_DIR}"

# Import the desired .pkg file name as determined in build-installer.py
#   The file contains "export INSTALL_PKG=python-3.x.y-macos11.pkg"
source "${MNT}/diskimage/export_installer_pkg_name"

if ( echo ${INSTALL_PKG} | grep -q macosx10 ) ; then
    export HOSTARCHITECTURES="x86_64"
else
    export INSTALL_PKG="$(echo ${INSTALL_PKG} | sed -e s/macosx/macos/g )"
    export HOSTARCHITECTURES="arm64,x86_64"
fi

[ -e "./${INSTALL_PKG}" ] && error " ERROR: $PWD/${INSTALL_PKG} already exists"

mkdir ./build_pkg
cd ./build_pkg

mkdir ./scripts

cat >./frameworks_modified.plist <<EOFA
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<array>
	<dict>
		<key>BundleHasStrictIdentifier</key>
		<true/>
		<key>BundleIsRelocatable</key>
		<false/>
		<key>BundleIsVersionChecked</key>
		<true/>
		<key>BundleOverwriteAction</key>
		<string>upgrade</string>
		<key>RootRelativeBundlePath</key>
		<string>Versions/${PY_VER}/Resources/Python.app</string>
	</dict>
</array>
</plist>
EOFA

cat >./applications3x_modified.plist <<EOFB
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<array>
	<dict>
		<key>BundleHasStrictIdentifier</key>
		<true/>
		<key>BundleIsRelocatable</key>
		<false/>
		<key>BundleIsVersionChecked</key>
		<true/>
		<key>BundleOverwriteAction</key>
		<string>upgrade</string>
		<key>RootRelativeBundlePath</key>
		<string>Python ${PY_VER}/IDLE.app</string>
	</dict>
	<dict>
		<key>BundleHasStrictIdentifier</key>
		<true/>
		<key>BundleIsRelocatable</key>
		<false/>
		<key>BundleIsVersionChecked</key>
		<true/>
		<key>BundleOverwriteAction</key>
		<string>upgrade</string>
		<key>RootRelativeBundlePath</key>
		<string>Python ${PY_VER}/Python Launcher.app</string>
	</dict>
</array>
</plist>
EOFB

cat >./clt_modified.plist <<EOFC
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<array>
</array>
</plist>
EOFC

cat >./doc_modified.plist <<EOFD
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<array>
</array>
</plist>
EOFD

cat >./entitlements.plist <<EOFE
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
<key>com.apple.security.cs.allow-dyld-environment-variables</key>
<true/>
<key>com.apple.security.cs.disable-library-validation</key>
<true/>
<key>com.apple.security.cs.disable-executable-page-protection</key>
<true/>
<key>com.apple.security.automation.apple-events</key>
<true/>
</dict>
</plist>
EOFE

if [ -e "${MNT}/libraries-saved" ] ; then
    echo " -- saving third-party library binaries"
    cp -a "${MNT}/libraries-saved" ..
else
    echo "WARNING: ${MNT}/libraries-saved does not exist, cache not copied"
fi

echo " -- codesigning Python Frameworks items"

CWD="$PWD"
pushd "${MNT}/_root/Library/Frameworks/Python.framework/Versions/${PY_VER}"

[ -e bin/python${PY_VER}-intel64 ] && \
    codesign --sign "${BP_CERTIFICATE_DEVELOPER_ID_APPLICATION}" --keychain ${BP_KEYCHAIN} \
        --entitlements ${CWD}/entitlements.plist \
        --continue --force --deep --timestamp --options=runtime --verbose=9 \
    bin/python${PY_VER}-intel64

# ***TODO: resolve issue with notarization failure of zipimport_data files

rm -rf lib/python${PY_VER}/test/zipimport_data

# remove Tk Wish.app if present

rm -rf Frameworks/Tk.framework/Resources/Wish.app

codesign --sign "${BP_CERTIFICATE_DEVELOPER_ID_APPLICATION}" --keychain ${BP_KEYCHAIN} \
    --entitlements ${CWD}/entitlements.plist \
    --continue --force --deep --timestamp --options=runtime --verbose=9 \
lib/python${PY_VER}/lib-dynload/*.so \
lib/python${PY_VER}/config-${PY_VER}-darwin/python.o \
lib/libcrypto.3.dylib \
lib/libformw.5.dylib \
lib/libmenuw.5.dylib \
lib/libncursesw.5.dylib \
lib/libpanelw.5.dylib \
lib/libssl.3.dylib \
Frameworks/Tcl.framework/Tcl \
Frameworks/Tk.framework/Tk \
bin/python${PY_VER} \
Resources/Python.app \
Python \
#

popd

echo " -- codesigning PythonT Frameworks items"

pushd "${MNT}/_root/Library/Frameworks/PythonT.framework/Versions/${PY_VER}"

[ -e bin/python${PY_VER}t-intel64 ] && \
    codesign --sign "${BP_CERTIFICATE_DEVELOPER_ID_APPLICATION}" --keychain ${BP_KEYCHAIN} \
        --entitlements ${CWD}/entitlements.plist \
        --continue --force --deep --timestamp --options=runtime --verbose=9 \
    bin/python${PY_VER}t-intel64

# ***TODO: resolve issue with notarization failure of zipimport_data files

rm -rf lib/python${PY_VER}/test/zipimport_data

codesign --sign "${BP_CERTIFICATE_DEVELOPER_ID_APPLICATION}" --keychain ${BP_KEYCHAIN} \
    --entitlements ${CWD}/entitlements.plist \
    --continue --force --deep --timestamp --options=runtime --verbose=9 \
lib/python${PY_VER}/lib-dynload/*.so \
lib/python${PY_VER}/config-${PY_VER}t-darwin/python.o \
bin/python${PY_VER} \
bin/python${PY_VER}t \
Resources/Python.app \
PythonT \
#

popd

echo " -- deleting Applications from PythonT frameworks"
rm -rf "${MNT}/_root/Applications/PythonT ${PY_VER}/"

echo " -- codesigning Applications packages"

codesign --sign "${BP_CERTIFICATE_DEVELOPER_ID_APPLICATION}" --keychain ${BP_KEYCHAIN} \
    --entitlements ./entitlements.plist \
    --continue --force --deep --timestamp --options=runtime --verbose=9 \
    "${MNT}/_root/Applications/Python ${PY_VER}/"*.app

echo " -- building component packages"

mkdir ./scripts/framework
cp -p \
    "${MNT}/installer/Python.mpkg/Contents/Packages/PythonFramework-${PY_VER}.pkg/Contents/Resources/postflight" \
    ./scripts/framework/postinstall
pkgbuild  \
    --identifier org.python.Python.PythonFramework-${TAG_VER} \
    --install-location "/Library/Frameworks/Python.framework" \
    --scripts ./scripts/framework \
    --root "${MNT}/_root/Library/Frameworks/Python.framework" \
    --component-plist ./frameworks_modified.plist \
    Python_Framework.pkg

pkgbuild  \
    --identifier org.python.Python.PythonApplications-${TAG_VER} \
    --install-location "/Applications" \
    --root "${MNT}/_root/Applications" \
    --component-plist ./applications3x_modified.plist \
    Python_Applications.pkg

pkgbuild  \
    --identifier org.python.Python.PythonUnixTools-${TAG_VER} \
    --install-location "/usr/local/bin" \
    --root "${MNT}/_root/usr/local/bin" \
    --component-plist ./clt_modified.plist \
    Python_Command_Line_Tools.pkg

mkdir ./scripts/documentation
cp -p \
    "${MNT}/installer/Python.mpkg/Contents/Packages/PythonDocumentation-${PY_VER}.pkg/Contents/Resources/postflight" \
    ./scripts/documentation/postinstall
pkgbuild  \
    --identifier org.python.Python.PythonDocumentation-${TAG_VER} \
    --install-location "/Library/Frameworks/Python.framework/Versions/${PY_VER}/Resources/English.lproj/Documentation" \
    --scripts ./scripts/documentation \
    --root "${MNT}/_root/pydocs" \
    --component-plist ./doc_modified.plist \
    Python_Documentation.pkg

mkdir ./scripts/profilechanges
cp -p \
    "${MNT}/installer/Python.mpkg/Contents/Packages/PythonProfileChanges-${PY_VER}.pkg/Contents/Resources/postflight" \
    ./scripts/profilechanges/postinstall
pkgbuild  \
    --identifier org.python.Python.PythonProfileChanges-${TAG_VER} \
    --scripts ./scripts/profilechanges \
    --nopayload \
    Python_Shell_Profile_Updater.pkg

mkdir ./scripts/installpip
cp -p \
    "${MNT}/installer/Python.mpkg/Contents/Packages/PythonInstallPip-${PY_VER}.pkg/Contents/Resources/postflight" \
    ./scripts/installpip/postinstall
pkgbuild  \
    --identifier org.python.Python.PythonInstallPip-${TAG_VER} \
    --scripts ./scripts/installpip \
    --nopayload \
    Python_Install_Pip.pkg

mkdir ./scripts/frameworkt
cp -p \
    "${MNT}/installer/Python.mpkg/Contents/Packages/PythonTFramework-${PY_VER}.pkg/Contents/Resources/postflight" \
    ./scripts/frameworkt/postinstall
pkgbuild  \
    --identifier org.python.Python.PythonTFramework-${TAG_VER} \
    --install-location "/Library/Frameworks/PythonT.framework" \
    --scripts ./scripts/frameworkt \
    --root "${MNT}/_root/Library/Frameworks/PythonT.framework" \
    --component-plist ./frameworks_modified.plist \
    PythonT_Framework.pkg


mkdir -p ./InstallerResources
cp -p ${MNT}/installer/Python.mpkg/Contents/Resources/background.jpg ./InstallerResources/
cp -p ${MNT}/installer/Python.mpkg/Contents/Resources/Welcome.rtf ./InstallerResources/
cp -p ${MNT}/installer/Python.mpkg/Contents/Resources/ReadMe.rtf ./InstallerResources/
cp -p ${MNT}/installer/Python.mpkg/Contents/Resources/License.rtf ./InstallerResources/
cp -p ${MNT}/installer/Python.mpkg/Contents/Resources/Conclusion.rtf ./InstallerResources/

cat >./pre-install-requirements.xml <<EOFPIR
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>os</key>
  <array>
    <string>${BP_MINIMUM_MACOS_VERSION}</string>
  </array>
</dict>
</plist>
EOFPIR

cat >./distribution_file.xml <<EOF
<?xml version="1.0" encoding="utf-8" standalone="no"?>
<installer-gui-script minSpecVersion="1">

    <title>Python</title>
    <background alignment="left" file="background.jpg" mime-type="image/png" />
    <welcome file="Welcome.rtf" mime-type="text/richtext" />
    <readme file="ReadMe.rtf" mime-type="text/richtext" />
    <license file="License.rtf" mime-type="text/richtext" />
    <conclusion file="Conclusion.rtf" mime-type="text/richtext" />

    <options customize="allow" require-scripts="false" rootVolumeOnly="true" hostArchitectures="${HOSTARCHITECTURES}"/>

    <choices-outline>
            <line choice="org.python.Python.PythonFramework-${TAG_VER}"/>
            <line choice="org.python.Python.PythonApplications-${TAG_VER}"/>
            <line choice="org.python.Python.PythonUnixTools-${TAG_VER}"/>
            <line choice="org.python.Python.PythonDocumentation-${TAG_VER}"/>
            <line choice="org.python.Python.PythonProfileChanges-${TAG_VER}"/>
            <line choice="org.python.Python.PythonInstallPip-${TAG_VER}"/>
            <line choice="org.python.Python.PythonTFramework-${TAG_VER}"/>
    </choices-outline>

    <choice id="default"/>

    <choice id="org.python.Python.PythonFramework-${TAG_VER}"
        visible="true"
        selected="true"
        enabled="false"
        title="Python Framework"
        description="This package installs Python.framework, that is the python
interpreter and the standard library.">
        <pkg-ref id="org.python.Python.PythonFramework-${TAG_VER}"/>
    </choice>

    <choice id="org.python.Python.PythonApplications-${TAG_VER}"
        visible="true"
        title="GUI Applications"
        description="This package installs IDLE (an interactive Python IDE),
and Python Launcher.  It also installs a number of examples and demos.">
        <pkg-ref id="org.python.Python.PythonApplications-${TAG_VER}"/>
    </choice>

    <choice id="org.python.Python.PythonUnixTools-${TAG_VER}"
        visible="true"
        title="UNIX command-line tools"
        description="This package installs the unix tools in /usr/local/bin for
compatibility with older releases of Python. This package
is not necessary to use Python.">
        <pkg-ref id="org.python.Python.PythonUnixTools-${TAG_VER}"/>
    </choice>

    <choice id="org.python.Python.PythonDocumentation-${TAG_VER}"
        visible="true"
        title="Python Documentation"
        description="This package installs the python documentation at a location
that is useable for pydoc and IDLE.">
        <pkg-ref id="org.python.Python.PythonDocumentation-${TAG_VER}"/>
    </choice>

    <choice id="org.python.Python.PythonProfileChanges-${TAG_VER}"
        visible="true"
        start_selected="true"
        title="Shell profile updater"
        description="This packages updates your shell profile to make sure that
the Python tools are found by your shell in preference of
the system provided Python tools.

If you don't install this package you'll have to add
'/Library/Frameworks/Python.framework/Versions/${PY_VER}/bin'
to your PATH by hand.">
        <pkg-ref id="org.python.Python.PythonProfileChanges-${TAG_VER}"/>
    </choice>

    <choice id="org.python.Python.PythonInstallPip-${TAG_VER}"
        visible="true" start_selected="true"
        title="Install or upgrade pip"
        description="This package installs (or upgrades from an earlier version)
pip, a tool for installing and managing Python packages.">
        <pkg-ref id="org.python.Python.PythonInstallPip-${TAG_VER}"/>
    </choice>

    <choice id="org.python.Python.PythonTFramework-${TAG_VER}"
        visible="true"
        start_selected="false"
        title="Free-threaded Python [experimental]"
        description="This optional package installs a free-threaded Python
framework, including a separate Python interpeter capable of running without
the global interpreter lock. It is installed independently of
the normal interpeter and includes its own standard library and site-packages.
See the installer ReadMe and the release notice for more information.
">
        <pkg-ref id="org.python.Python.PythonTFramework-${TAG_VER}"/>
    </choice>

    <pkg-ref id="org.python.Python.PythonFramework-${TAG_VER}" version="0" auth="Root" onConclusion="none">Python_Framework.pkg</pkg-ref>
    <pkg-ref id="org.python.Python.PythonApplications-${TAG_VER}" version="0" auth="Root" onConclusion="none">Python_Applications.pkg</pkg-ref>
    <pkg-ref id="org.python.Python.PythonUnixTools-${TAG_VER}" version="0" auth="Root" onConclusion="none">Python_Command_Line_Tools.pkg</pkg-ref>
    <pkg-ref id="org.python.Python.PythonDocumentation-${TAG_VER}" version="0" auth="Root" onConclusion="none">Python_Documentation.pkg</pkg-ref>
    <pkg-ref id="org.python.Python.PythonProfileChanges-${TAG_VER}" version="0" auth="Root" onConclusion="none">Python_Shell_Profile_Updater.pkg</pkg-ref>
    <pkg-ref id="org.python.Python.PythonInstallPip-${TAG_VER}" version="0" auth="Root" onConclusion="none">Python_Install_Pip.pkg</pkg-ref>
    <pkg-ref id="org.python.Python.PythonTFramework-${TAG_VER}" version="0" auth="Root" onConclusion="none">PythonT_Framework.pkg</pkg-ref>

</installer-gui-script>
EOF

echo " -- calling productbuild"
productbuild \
    --distribution ./distribution_file.xml \
    --resources ./InstallerResources \
    --product ./pre-install-requirements.xml \
    Python.pkg

echo " -- signing package with Apple Developer ID"

productsign \
    --sign "${BP_CERTIFICATE_DEVELOPER_ID_INSTALLER}" \
    --keychain ${BP_KEYCHAIN} \
    --timestamp \
    ./Python.pkg \
    "../${INSTALL_PKG}"
cd ..

echo "${INSTALL_PKG}" > ./installer_pkg_name.txt

echo " -- unnotarized package ${INSTALL_PKG} in $PWD"

cd .

exit 0



