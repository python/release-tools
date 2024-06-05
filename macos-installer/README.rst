PSF macOS Installer Builds 
==========================

2024-06-04

**WARNING** This is a snapshot of the evolving build process for Python
Software Foundation cpython macOS installer builds as published on the
python.org website. The process is currently in transition from a specific
private multiple Intel Mac virtual machine environment to a more general
environment that can be used by Python release managers on all Macs running
current versions of macOS and eventually automatically in cloud environments.
The build process has evolved slowly over many years. This snapshot is a step
in the journey. It is a work in progress.

Current known limitations of this snapshot:

- building only tested on current macOS 14 Sonoma systems (14.5) using Apple
  Command Line Tools 14.4 (a full Xcode installation is not needed)

- built installer can be deployed on macOS 10.13 and newer systems, unlike
  current PSF installers which can be deployed on macOS 10.9 and newer systems

- the build process may not provide complete isolation from the user's build
  environment. Use of a separate user account for building should be considered.

- currently only one build may be in progress at a time on a particular system
  (should no longer be true but not yet tested)

- the builder should have an Apple Developer Connection account

- the builder will need access to Developer ID Application and Developer ID
  Installer certificates issued by Apple in order to codesign build binary
  artifacts and installer packages.

- to meet Apple Gatekeeper security requirements, the built installer package
  must complete Apple's notarization process for macOS downloads.

Build steps outline:

- Clone / checkout this repo under a macOS user. 

- Currently the build process is implemented as a set of scripts in the
  scripts directory. They must be run manually in sequence from a terminal
  shell.

The steps:

git clone or unpack source bundle

cd build_macos_installer

cd scripts
cp 00-build_settings-pre.txt 00-build_settings.txt
$EDITOR 00-build_settings.txt # customize build settings here
cp 00-private_build_settings-pre.txt 00-private_build_settings.txt
$EDITOR 00-private_build_settings.txt # customize security-related settings here

cd ..

rm -rf ./source ./cached-artifacts # manually clean cpython source snapshot

./scripts/01-build_installer_source.sh

rm -rf ./installer # manually clean build tree

./scripts/02-build_installer_binaries.sh
# As of 3.13.0b2, this step will build an installer package that includes an optional and experimental free-threaded PythonT.framework along side the normal Python.framework. When installing using the system Installer.app, click on the Customize option to access the options.

./scripts/03-build_signed_package.sh

# optionally, save the just-built third-party libraries (OpenSSL, Tcl/Tk, etc)
#   if you expect to rerun the 02-build_installer_binaries.sh 
#   or 03-build_sign_package.sh steps
03-cache_third_party_libraries

./scripts/04-build_notorized_package.sh  # will synchronously submit installer package to Apple Notarization Service and wait for completion (typically no more than 5 minutes)

./scripts/05-upload_installer.sh  # uploads notarized installer to a test location on the PSF download server and to your home directory there.

./scripts/06-test_installer.sh <remote-ssh-id> # DESTRUCTIVE!
# Will install into the system /Library/Frameworks, /Applications/Python 3.x, and /usr/local/bin of the ssh-connected host. It currently requires one or more ssh-connected hosts to run on and currently requires console (Desktop) access (for example, via VM console or remote desktop screen sharing).
# Currently, only downloads test scripts to the remote Desktop folder. Manual intervention is needed to run and save results locally.

./scripts/06-retrieve_test_results.sh <remote-ssh-id> # retrieve test results that have been manually saved from the remote Terminal session

./scripts/07-handoff_to_release_manager.sh  # not yet implemented

./scripts/08-cleanup_testing.sh # removes installer package from test location on download server and purges CDN cache

----------
